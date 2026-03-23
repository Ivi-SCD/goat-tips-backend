"""
Narrative Service
=================
Generates Portuguese match analysis narratives via Groq.
LLM client configuration lives in app.services.llm_client.

The /ask endpoint uses an agentic tool-calling loop (answer_question):
  - Up to MAX_TOOL_ROUNDS rounds where the LLM may call any tool in tools.TOOLS
  - Tools: web_search, get_team_form, get_team_stats, get_h2h_stats, get_upcoming_odds
  - After tool results are injected the LLM produces a final NarrativeResponse JSON
"""

import asyncio
import json
from typing import Optional

from app.schemas.analytics import H2HRecord, TeamForm
from app.schemas.match import MatchContext, NarrativeResponse, StatsTrend
from app.schemas.prediction import ScorePredictionResponse
from app.services.llm_client import MODEL, SYSTEM_PROMPT, GENERAL_SYSTEM_PROMPT, client


# ── Context builders ──────────────────────────────────────────────────────────

def _build_context_prompt(match: MatchContext, user_question: str = "") -> str:
    probs = match.probabilities
    odds  = match.odds

    lines = [
        f"PARTIDA: {match.home.name} vs {match.away.name}",
        f"STATUS: {match.status.upper()}",
    ]
    if match.minute:
        lines.append(f"MINUTO: {match.minute}'")
    lines.append(f"PLACAR: {match.home.name} {match.score_home} x {match.score_away} {match.away.name}")

    if probs:
        lines += [
            "",
            "PROBABILIDADES REAIS (sem margem da casa):",
            f"  Vitória {match.home.name}: {probs.home_win:.1%}",
            f"  Empate: {probs.draw:.1%}",
            f"  Vitória {match.away.name}: {probs.away_win:.1%}",
            f"  Margem da casa: {probs.market_margin:.1%}",
        ]

    if odds:
        lines.append("")
        lines.append("MERCADOS SECUNDÁRIOS:")
        if odds.over_2_5:
            lines.append(f"  Mais de 2.5 gols: {1 / odds.over_2_5:.1%} de chance")
        if odds.btts:
            lines.append(f"  Ambos marcam: {1 / odds.btts:.1%} de chance")

    if match.odds_shift_pct is not None:
        direction = "caiu" if match.odds_shift_pct < 0 else "subiu"
        lines.append(
            f"\nSINAL DE MERCADO: Odd do mandante {direction} "
            f"{abs(match.odds_shift_pct):.1f}% desde o início"
        )

    if user_question:
        lines.append(f"\nPERGUNTA DO USUÁRIO: {user_question}")

    return "\n".join(lines)


def _build_enriched_context(
    match: MatchContext,
    h2h: Optional[H2HRecord] = None,
    stats_trend: Optional[StatsTrend] = None,
    home_form: Optional[TeamForm] = None,
    away_form: Optional[TeamForm] = None,
    goal_risk: Optional[float] = None,
    card_risk: Optional[float] = None,
    user_question: str = "",
) -> str:
    lines = [_build_context_prompt(match, user_question)]

    if home_form and away_form:
        lines += [
            "",
            "FORMA RECENTE (últimos jogos):",
            f"  {match.home.name}: {home_form.form_string} — "
            f"{home_form.avg_goals_scored:.1f} gols/jogo marcados, "
            f"{home_form.avg_goals_conceded:.1f} sofridos",
            f"  {match.away.name}: {away_form.form_string} — "
            f"{away_form.avg_goals_scored:.1f} gols/jogo marcados, "
            f"{away_form.avg_goals_conceded:.1f} sofridos",
        ]
    elif home_form:
        lines.append(f"\nFORMA {match.home.name}: {home_form.form_string}")
    elif away_form:
        lines.append(f"\nFORMA {match.away.name}: {away_form.form_string}")

    if h2h and h2h.total_matches > 0:
        lines += [
            "",
            f"HISTÓRICO H2H ({h2h.total_matches} confrontos anteriores):",
            f"  Vitórias {h2h.home_team}: {h2h.home_wins} | "
            f"Empates: {h2h.draws} | Vitórias {h2h.away_team}: {h2h.away_wins}",
            f"  Média de gols: {h2h.home_team} {h2h.home_goals_avg} x "
            f"{h2h.away_goals_avg} {h2h.away_team}",
        ]
        if h2h.last_matches:
            last = h2h.last_matches[0]
            lines.append(
                f"  Último confronto: {last.home_team} {last.score_home}-"
                f"{last.score_away} {last.away_team}"
                + (f" ({last.date})" if last.date else "")
            )

    if stats_trend and stats_trend.periods:
        last_p = stats_trend.periods[-1]
        lines += [
            "",
            f"ESTATÍSTICAS DO JOGO (momentum: {stats_trend.momentum_label}):",
        ]
        if last_p.home_shots is not None:
            lines.append(
                f"  Finalizações no alvo: {match.home.name} {last_p.home_shots} x "
                f"{last_p.away_shots} {match.away.name}"
            )
        if last_p.home_corners is not None:
            lines.append(
                f"  Escanteios: {match.home.name} {last_p.home_corners} x "
                f"{last_p.away_corners} {match.away.name}"
            )
        if last_p.home_dangerous_attacks is not None:
            lines.append(
                f"  Ataques perigosos: {match.home.name} {last_p.home_dangerous_attacks} x "
                f"{last_p.away_dangerous_attacks} {match.away.name}"
            )

    if goal_risk is not None:
        label = "ALTO" if goal_risk >= 7 else "MÉDIO" if goal_risk >= 4 else "BAIXO"
        lines.append(f"\nRISCO DE GOL (próximos 15 min): {label} ({goal_risk:.1f}/10)")

    if card_risk is not None:
        label = "ALTO" if card_risk >= 7 else "MÉDIO" if card_risk >= 4 else "BAIXO"
        lines.append(f"RISCO DE CARTÃO: {label} ({card_risk:.1f}/10)")

    return "\n".join(lines)


def _append_prediction_context(
    lines: list[str], pred: ScorePredictionResponse, match: MatchContext
) -> None:
    lines += [
        "",
        f"PREVISÃO ESTATÍSTICA (Modelo Poisson — {pred.model_note.split('.')[0]}):",
        f"  Gols esperados: {match.home.name} {pred.lambda_home:.2f} x "
        f"{pred.lambda_away:.2f} {match.away.name}",
        f"  Probabilidades: Vitória {match.home.name} {pred.home_win_prob:.1%} | "
        f"Empate {pred.draw_prob:.1%} | Vitória {match.away.name} {pred.away_win_prob:.1%}",
        f"  Placar mais provável: {pred.most_likely_score} ({pred.most_likely_score_prob:.1%})",
        f"  Mais de 2.5 gols: {pred.over_2_5_prob:.1%} | Ambos marcam: {pred.btts_prob:.1%}",
        f"  Confiança do modelo: {pred.confidence}",
    ]


# ── LLM response parser ───────────────────────────────────────────────────────

def _parse_llm_raw(match_id: str, raw: str) -> NarrativeResponse:
    """Parse a raw LLM text response (possibly fenced JSON) into NarrativeResponse."""
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return NarrativeResponse(
            match_id=match_id,
            headline="Resposta fora do formato esperado",
            analysis=raw,
            prediction="Não foi possível estruturar a previsão automaticamente.",
            momentum_signal=None,
            confidence_label="Baixa",
        )
    return NarrativeResponse(
        match_id=match_id,
        headline=parsed["headline"],
        analysis=parsed["analysis"],
        prediction=parsed["prediction"],
        momentum_signal=parsed.get("momentum_signal"),
        confidence_label=parsed["confidence_label"],
    )


_EMPTY_RAW = json.dumps({
    "headline": "Sem resposta do modelo", "analysis": "", "prediction": "",
    "momentum_signal": None, "confidence_label": "Baixa",
})


# ── LLM call helper ───────────────────────────────────────────────────────────

async def _call_llm(
    match_id: str,
    context: str,
    history: list[dict] | None = None,
) -> NarrativeResponse:
    """Call Groq. `history` is a flat list of prior {role, content} pairs
    (user/assistant alternating) inserted between the system prompt and the
    current user message to maintain conversational context."""
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": context})

    response = await client.chat.completions.create(model=MODEL, messages=messages)
    raw = (response.choices[0].message.content or "").strip() if response.choices else ""
    return _parse_llm_raw(match_id, raw or _EMPTY_RAW)


# ── Public API ────────────────────────────────────────────────────────────────

async def generate_narrative(match: MatchContext, user_question: str = "") -> NarrativeResponse:
    """Generates a basic narrative from match context only."""
    return await _call_llm(match.event_id, _build_context_prompt(match, user_question))


MAX_TOOL_ROUNDS = 5


async def answer_question(
    match: MatchContext,
    question: str,
    history: list[dict] | None = None,
) -> NarrativeResponse:
    """Answer a free-form question using an agentic tool-calling loop.

    The LLM may call any tool in tools.TOOLS (web search, DB queries, BetsAPI)
    up to MAX_TOOL_ROUNDS times before producing a final NarrativeResponse.
    History (prior Q&A pairs) is injected for session context.
    """
    from app.services.tools import TOOLS, execute_tool

    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append({
        "role": "user",
        "content": _build_context_prompt(match, user_question=question),
    })

    last_msg = None
    for _ in range(MAX_TOOL_ROUNDS):
        response = await client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )
        last_msg = response.choices[0].message

        # No tool calls → LLM produced the final answer
        if not last_msg.tool_calls:
            break

        # Append assistant message (with tool_calls) then tool results
        messages.append(last_msg.model_dump(exclude_unset=True))

        tool_results = await asyncio.gather(*[
            execute_tool(tc.function.name, json.loads(tc.function.arguments))
            for tc in last_msg.tool_calls
        ])
        for tc, result in zip(last_msg.tool_calls, tool_results):
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    # If every round ended with tool calls, force a final tool-free completion
    if last_msg is None or last_msg.tool_calls:
        response = await client.chat.completions.create(model=MODEL, messages=messages)
        last_msg = response.choices[0].message

    raw = (last_msg.content or "").strip()
    return _parse_llm_raw(match.event_id, raw or _EMPTY_RAW)


async def answer_general_question(
    question: str,
    history: list[dict] | None = None,
) -> NarrativeResponse:
    """Answer a general Premier League question without match context.

    Uses the same agentic tool-calling loop as answer_question but with
    GENERAL_SYSTEM_PROMPT and no match context — suitable for the open /ask endpoint.
    """
    from app.services.tools import TOOLS, execute_tool

    messages: list[dict] = [{"role": "system", "content": GENERAL_SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": question})

    last_msg = None
    for _ in range(MAX_TOOL_ROUNDS):
        response = await client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )
        last_msg = response.choices[0].message

        if not last_msg.tool_calls:
            break

        messages.append(last_msg.model_dump(exclude_unset=True))

        tool_results = await asyncio.gather(*[
            execute_tool(tc.function.name, json.loads(tc.function.arguments))
            for tc in last_msg.tool_calls
        ])
        for tc, result in zip(last_msg.tool_calls, tool_results):
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    if last_msg is None or last_msg.tool_calls:
        response = await client.chat.completions.create(model=MODEL, messages=messages)
        last_msg = response.choices[0].message

    raw = (last_msg.content or "").strip()
    return _parse_llm_raw("general", raw or _EMPTY_RAW)


async def generate_narrative_enriched(
    match: MatchContext,
    h2h: Optional[H2HRecord] = None,
    stats_trend: Optional[StatsTrend] = None,
    home_form: Optional[TeamForm] = None,
    away_form: Optional[TeamForm] = None,
    goal_risk: Optional[float] = None,
    card_risk: Optional[float] = None,
    prediction: Optional[ScorePredictionResponse] = None,
    user_question: str = "",
) -> NarrativeResponse:
    """Generates a full narrative with all agent-enriched context."""
    context = _build_enriched_context(
        match, h2h, stats_trend, home_form, away_form,
        goal_risk, card_risk, user_question,
    )
    if prediction:
        extra: list[str] = []
        _append_prediction_context(extra, prediction, match)
        context += "\n" + "\n".join(extra)

    return await _call_llm(match.event_id, context)
