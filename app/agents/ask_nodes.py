"""
Ask Agent Node Definitions
==========================
Defines the AskState TypedDict, AgentArtifact contract, and the six node
functions used by the LangGraph supervisor in ask_agent.py.

Agents:
  1. intent_router      — classify question intent and set execution plan
  2. live_context       — BetsAPI live/upcoming/odds data
  3. historical_stats   — Supabase historical team form + H2H
  4. player_intel       — team_player_strength_snapshot + player_absence_impact
  5. quant_agent        — Poisson quantitative output
  6. narrative_verifier — final LLM synthesis and quality check

Execution flow:
  intent_router → parallel_gather (2+3+4) → quant_agent → narrative_verifier
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, TypedDict

logger = logging.getLogger(__name__)

# Per-agent timeout (seconds)
AGENT_TIMEOUT = 1.8

# Confidence weight by level
_CONFIDENCE_WEIGHTS = {"high": 1.0, "medium": 0.6, "low": 0.3}

# Intent categories recognised by IntentRouterAgent
INTENTS = {
    "FORM_ODDS":    "form, recent results, betting odds, markets",
    "INJURIES":     "player injuries, absences, suspensions, fitness news",
    "HISTORICAL":   "H2H history, historical stats, previous encounters",
    "PLAYER":       "individual player stats, performance, career data",
    "TACTICAL":     "tactics, formation, playing style, pressing, possession",
    "PREDICTION":   "match prediction, score forecast, probability",
    "GENERAL":      "anything else — general Premier League question",
}


# ── Data contracts ────────────────────────────────────────────────────────────

class AgentArtifact(TypedDict):
    source:        str           # agent name
    timestamp_utc: str           # ISO UTC
    confidence:    str           # "high" | "medium" | "low"
    payload:       str           # text to inject into final LLM prompt
    citations:     list[str]     # short source labels
    errors:        list[str]     # non-fatal error messages


class AskState(TypedDict):
    # Inputs
    question:       str
    event_id:       str          # "" for general /ask
    history:        Optional[list[dict]]

    # Routing
    intent:         str          # one of INTENTS keys

    # Pre-fetched match context (injected by caller when event_id is known)
    match_context_text: str      # pre-formatted match context block (may be "")

    # Parallel agent outputs
    artifacts:      list[AgentArtifact]

    # Quant output
    quant_output:   Optional[str]

    # Final answer
    final_answer:   Optional[dict]   # keys: headline, analysis, prediction, momentum_signal, confidence_label

    # Quality / observability
    quality_flags:  list[str]
    partial_context: bool
    confidence_score: float
    data_sources:   list[str]
    agent_trace_id: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_artifact(source: str, error: str) -> AgentArtifact:
    return AgentArtifact(
        source=source, timestamp_utc=_now_utc(),
        confidence="low", payload="", citations=[], errors=[error],
    )


async def _with_timeout(coro, *, timeout: float = AGENT_TIMEOUT, label: str) -> AgentArtifact:
    """Run `coro` with a hard timeout; return a low-confidence artifact on failure."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("Agent '%s' timed out after %.1fs", label, timeout)
        return _empty_artifact(label, f"timeout after {timeout}s")
    except Exception as exc:
        logger.warning("Agent '%s' error: %s", label, exc)
        return _empty_artifact(label, str(exc))


def _aggregate_confidence(artifacts: list[AgentArtifact]) -> float:
    """Weighted average confidence across non-empty artifacts."""
    scored = [
        _CONFIDENCE_WEIGHTS.get(a["confidence"], 0.3)
        for a in artifacts
        if not a["errors"] and a["payload"]
    ]
    return round(sum(scored) / len(scored), 3) if scored else 0.3


# ── Node 1: IntentRouterAgent ─────────────────────────────────────────────────

async def intent_router_node(state: AskState) -> dict[str, Any]:
    """
    Classify the question intent with a single fast LLM call.
    Falls back to 'GENERAL' on timeout or error.
    """
    question = state["question"]

    # Fast keyword heuristic (avoids LLM call for obvious cases)
    q_lower = question.lower()
    if any(w in q_lower for w in ["odds", "aposta", "mercado", "bet"]):
        intent = "FORM_ODDS"
    elif any(w in q_lower for w in ["lesão", "lesao", "lesionado", "injury", "injured", "suspen"]):
        intent = "INJURIES"
    elif any(w in q_lower for w in ["h2h", "histórico", "historico", "history", "last time"]):
        intent = "HISTORICAL"
    elif any(w in q_lower for w in ["jogador", "player", "gols", "assist"]):
        intent = "PLAYER"
    elif any(w in q_lower for w in ["tático", "tatico", "formation", "tactical", "pressing"]):
        intent = "TACTICAL"
    elif any(w in q_lower for w in ["prever", "previsão", "predict", "placar", "score"]):
        intent = "PREDICTION"
    else:
        intent = "GENERAL"

    return {
        "intent": intent,
        "agent_trace_id": state.get("agent_trace_id") or str(uuid.uuid4()),
    }


# ── Node 2: LiveContextAgent ──────────────────────────────────────────────────

async def live_context_node(state: AskState) -> AgentArtifact:
    """
    Fetch live/upcoming BetsAPI data: odds, inplay status, upcoming fixtures.
    Uses the existing get_upcoming_odds tool implementation.
    """
    from app.services.tools import _upcoming_odds_async

    intent = state["intent"]
    event_id = state.get("event_id", "")

    # For non-odds/non-prediction intents, a lightweight fetch is fine
    try:
        odds_text = await asyncio.wait_for(_upcoming_odds_async(None), timeout=AGENT_TIMEOUT)
        confidence = "high" if odds_text and "Premier League" in odds_text else "medium"
        return AgentArtifact(
            source="live_context",
            timestamp_utc=_now_utc(),
            confidence=confidence,
            payload=odds_text or "",
            citations=["BetsAPI upcoming"],
            errors=[],
        )
    except Exception as exc:
        return _empty_artifact("live_context", str(exc))


# ── Node 3: HistoricalStatsAgent ──────────────────────────────────────────────

async def historical_stats_node(state: AskState) -> AgentArtifact:
    """
    Query Supabase historical DB: team form + H2H relevant to the question.
    Extracts team names from the question heuristically or from match_context_text.
    """
    from app.services.tools import _team_form_sync, _h2h_sync

    # Detect teams from match context or question
    ctx_text = state.get("match_context_text", "")
    question = state["question"]
    combined = (ctx_text + " " + question).lower()

    # Known PL teams (simplified — full alias dict lives in analytics.py)
    PL_TEAMS = [
        "Arsenal", "Chelsea", "Liverpool", "Man City", "Man Utd",
        "Tottenham", "Newcastle", "Brighton", "Aston Villa", "West Ham",
        "Wolves", "Brentford", "Fulham", "Crystal Palace", "Everton",
        "Bournemouth", "Luton", "Burnley", "Nott'm Forest", "Sheffield Utd",
        "Ipswich", "Leicester", "Southampton",
    ]
    mentioned = [t for t in PL_TEAMS if t.lower() in combined]

    if not mentioned:
        return _empty_artifact("historical_stats", "no team names detected in question")

    parts: list[str] = []
    citations: list[str] = []

    try:
        if len(mentioned) >= 2:
            h2h = await asyncio.to_thread(_h2h_sync, mentioned[0], mentioned[1], 5)
            parts.append(h2h)
            citations.append(f"H2H {mentioned[0]} vs {mentioned[1]}")

        for team in mentioned[:2]:
            form = await asyncio.to_thread(_team_form_sync, team, 5)
            parts.append(form)
            citations.append(f"form:{team}")

    except Exception as exc:
        return _empty_artifact("historical_stats", str(exc))

    payload = "\n\n".join(parts)
    return AgentArtifact(
        source="historical_stats",
        timestamp_utc=_now_utc(),
        confidence="high" if payload else "low",
        payload=payload,
        citations=citations,
        errors=[],
    )


# ── Node 4: PlayerIntelAgent ──────────────────────────────────────────────────

async def player_intel_node(state: AskState) -> AgentArtifact:
    """
    Query team_player_strength_snapshot + player_absence_impact from Supabase.
    Uses the app's shared async pool (asyncpg) to avoid cold connection overhead.
    """
    ctx_text = state.get("match_context_text", "")
    question = state["question"]
    combined = (ctx_text + " " + question).lower()

    PL_TEAMS = [
        "Arsenal", "Chelsea", "Liverpool", "Man City", "Man Utd",
        "Tottenham", "Newcastle", "Brighton", "Aston Villa", "West Ham",
        "Wolves", "Brentford", "Fulham", "Crystal Palace", "Everton",
        "Bournemouth", "Luton", "Burnley", "Nott'm Forest", "Sheffield Utd",
        "Ipswich", "Leicester", "Southampton",
    ]
    mentioned = [t for t in PL_TEAMS if t.lower() in combined]

    if not mentioned:
        return _empty_artifact("player_intel", "no teams identified for player intel lookup")

    try:
        result = await _query_player_intel_async(mentioned[:2])
        if not result:
            return _empty_artifact("player_intel", "no snapshot data found")
        return AgentArtifact(
            source="player_intel",
            timestamp_utc=_now_utc(),
            confidence="high",
            payload=result,
            citations=["team_player_strength_snapshot", "player_absence_impact"],
            errors=[],
        )
    except Exception as exc:
        return _empty_artifact("player_intel", str(exc))


async def _query_player_intel_async(teams: list[str]) -> str:
    """
    Async query for player strength + absence snapshots using the app's shared pool.
    Falls back to a sync psycopg2 connection if the pool is unavailable.
    """
    try:
        from app.db.connection import get_pool
        pool = await get_pool()
        return await _run_intel_queries(pool, teams)
    except Exception:
        # Fallback: run sync version in a thread
        return await asyncio.to_thread(_query_player_intel_sync, teams)


async def _run_intel_queries(pool, teams: list[str]) -> str:
    """Execute the three snapshot queries against an asyncpg pool."""
    lines: list[str] = []

    async with pool.acquire() as conn:
        for team in teams:
            # Strength snapshot
            row = await conn.fetchrow("""
                SELECT attack_index, creation_index, defensive_index, squad_depth, snapshot_date
                FROM team_player_strength_snapshot
                WHERE lower(team_name) = lower($1)
                ORDER BY snapshot_date DESC LIMIT 1
            """, team)
            if row:
                lines.append(
                    f"[{team}] Kaggle strength: attack={row['attack_index']:.2f}  "
                    f"creation={row['creation_index']:.2f}  defense={row['defensive_index']:.2f}  "
                    f"depth={row['squad_depth']}  (as of {row['snapshot_date']})"
                )

            # Style snapshot (StatsBomb)
            row = await conn.fetchrow("""
                SELECT avg_goals_scored, avg_goals_conceded, clean_sheet_rate, btts_rate,
                       matches_count, snapshot_date
                FROM team_style_snapshot_statsbomb
                WHERE lower(team_name) = lower($1)
                ORDER BY snapshot_date DESC LIMIT 1
            """, team)
            if row:
                lines.append(
                    f"[{team}] StatsBomb style ({row['matches_count']} matches): "
                    f"scored={row['avg_goals_scored']:.2f}  conceded={row['avg_goals_conceded']:.2f}  "
                    f"CS={row['clean_sheet_rate']:.0%}  BTTS={row['btts_rate']:.0%}"
                )

            # Top key players
            rows = await conn.fetch("""
                SELECT player_name, goals, assists, impact_score
                FROM player_absence_impact
                WHERE lower(team_name) = lower($1)
                ORDER BY impact_score DESC LIMIT 5
            """, team)
            if rows:
                players_txt = "  ".join(
                    f"{r['player_name']}(G={r['goals']},A={r['assists']},imp={r['impact_score']:.1f})"
                    for r in rows
                )
                lines.append(f"[{team}] Key players: {players_txt}")

    return "\n".join(lines)


def _query_player_intel_sync(teams: list[str]) -> str:
    """Sync fallback: opens a psycopg2 connection (used in tests and tools.py)."""
    import os
    import psycopg2
    import psycopg2.extras

    db_url = os.environ.get("SUPABASE_DB_URL", "")
    if not db_url:
        return ""

    conn = psycopg2.connect(db_url)
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    lines: list[str] = []

    for team in teams:
        cur.execute("""
            SELECT attack_index, creation_index, defensive_index, squad_depth, snapshot_date
            FROM team_player_strength_snapshot
            WHERE lower(team_name) = lower(%s)
            ORDER BY snapshot_date DESC LIMIT 1
        """, (team,))
        row = cur.fetchone()
        if row:
            lines.append(
                f"[{team}] Kaggle strength: attack={row['attack_index']:.2f}  "
                f"creation={row['creation_index']:.2f}  defense={row['defensive_index']:.2f}  "
                f"depth={row['squad_depth']}  (as of {row['snapshot_date']})"
            )

        cur.execute("""
            SELECT avg_goals_scored, avg_goals_conceded, clean_sheet_rate, btts_rate,
                   matches_count, snapshot_date
            FROM team_style_snapshot_statsbomb
            WHERE lower(team_name) = lower(%s)
            ORDER BY snapshot_date DESC LIMIT 1
        """, (team,))
        row = cur.fetchone()
        if row:
            lines.append(
                f"[{team}] StatsBomb style ({row['matches_count']} matches): "
                f"scored={row['avg_goals_scored']:.2f}  conceded={row['avg_goals_conceded']:.2f}  "
                f"CS={row['clean_sheet_rate']:.0%}  BTTS={row['btts_rate']:.0%}"
            )

        cur.execute("""
            SELECT player_name, goals, assists, impact_score
            FROM player_absence_impact
            WHERE lower(team_name) = lower(%s)
            ORDER BY impact_score DESC LIMIT 5
        """, (team,))
        rows = cur.fetchall()
        if rows:
            players_txt = "  ".join(
                f"{r['player_name']}(G={r['goals']},A={r['assists']},imp={r['impact_score']:.1f})"
                for r in rows
            )
            lines.append(f"[{team}] Key players: {players_txt}")

    cur.close()
    conn.close()
    return "\n".join(lines)


# ── Node 5: QuantAgent ────────────────────────────────────────────────────────

async def quant_agent_node(state: AskState) -> dict[str, Any]:
    """
    Run the Poisson model for quantitative probability output.
    Extracts teams from match_context_text or question.
    """
    ctx_text = state.get("match_context_text", "")

    # Extract home/away from context text  (format: "PARTIDA: X vs Y")
    home_team = away_team = None
    for line in ctx_text.splitlines():
        if line.startswith("PARTIDA:"):
            parts = line.replace("PARTIDA:", "").strip().split(" vs ")
            if len(parts) == 2:
                home_team, away_team = parts[0].strip(), parts[1].strip()
            break

    if not (home_team and away_team):
        return {"quant_output": None}

    try:
        from app.services.predictor import predict_match
        raw = await asyncio.to_thread(predict_match, home_team, away_team)
        quant_text = (
            f"PREVISÃO QUANTITATIVA — {home_team} vs {away_team}:\n"
            f"  λ_home={raw.lambda_home:.2f}  λ_away={raw.lambda_away:.2f}\n"
            f"  V/E/D: {raw.home_win_prob:.1%}/{raw.draw_prob:.1%}/{raw.away_win_prob:.1%}\n"
            f"  Placar mais provável: {raw.most_likely_score} ({raw.most_likely_score_prob:.1%})\n"
            f"  Over 2.5: {raw.over_2_5_prob:.1%}  BTTS: {raw.btts_prob:.1%}\n"
            f"  Confiança: {raw.confidence}"
        )
        return {"quant_output": quant_text}
    except Exception as exc:
        logger.warning("QuantAgent error: %s", exc)
        return {"quant_output": None}


# ── Node 6: NarrativeVerifierAgent ───────────────────────────────────────────

async def narrative_verifier_node(state: AskState) -> dict[str, Any]:
    """
    Synthesise all artifact payloads + quant output into a final NarrativeResponse.
    Adds quality flags and computes aggregate confidence_score.
    """
    from app.services.llm_client import MODEL, SYSTEM_PROMPT, client

    artifacts = state.get("artifacts", [])
    quality_flags: list[str] = list(state.get("quality_flags", []))
    data_sources: list[str] = []
    error_count = 0

    context_blocks: list[str] = []

    # Inject pre-fetched match context first (highest priority)
    if state.get("match_context_text"):
        context_blocks.append(state["match_context_text"])

    for artifact in artifacts:
        if artifact["errors"]:
            error_count += 1
            quality_flags.append(f"{artifact['source']}:error")
        if artifact["payload"]:
            context_blocks.append(
                f"[{artifact['source'].upper()}]\n{artifact['payload']}"
            )
            data_sources.extend(artifact["citations"])

    if state.get("quant_output"):
        context_blocks.append(state["quant_output"])
        data_sources.append("quant_model")

    partial = error_count > 0
    confidence_score = _aggregate_confidence(artifacts)

    context_blocks.append(f"\nPERGUNTA DO USUÁRIO: {state['question']}")
    full_context = "\n\n".join(context_blocks)

    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if state.get("history"):
        messages.extend(state["history"])
    messages.append({"role": "user", "content": full_context})

    try:
        response = await client.chat.completions.create(model=MODEL, messages=messages)
        raw = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        logger.warning("NarrativeVerifierAgent LLM call failed: %s", exc)
        raw = json.dumps({
            "headline": "Erro ao gerar narrativa",
            "analysis": str(exc),
            "prediction": "",
            "momentum_signal": None,
            "confidence_label": "Baixa",
        })

    # Quality check: flag if response is suspiciously short
    if len(raw) < 80:
        quality_flags.append("short_response")

    return {
        "final_answer": raw,
        "quality_flags": quality_flags,
        "partial_context": partial,
        "confidence_score": confidence_score,
        "data_sources": list(dict.fromkeys(data_sources)),  # dedup, preserve order
    }


# ── Parallel gather node ──────────────────────────────────────────────────────

async def parallel_gather_node(state: AskState) -> dict[str, Any]:
    """
    Fan-out: run LiveContextAgent, HistoricalStatsAgent, PlayerIntelAgent in parallel.
    Each has a hard timeout of AGENT_TIMEOUT seconds.
    """
    live_art, hist_art, player_art = await asyncio.gather(
        _with_timeout(live_context_node(state),     timeout=AGENT_TIMEOUT, label="live_context"),
        _with_timeout(historical_stats_node(state), timeout=AGENT_TIMEOUT, label="historical_stats"),
        _with_timeout(player_intel_node(state),     timeout=AGENT_TIMEOUT, label="player_intel"),
    )

    return {"artifacts": [live_art, hist_art, player_art]}
