"""
LLM Tool Definitions + Dispatcher
===================================
TOOLS        — list[dict] in OpenAI function-calling schema.
execute_tool — async dispatcher; always returns str for the LLM's `tool` role message.

Available tools:
  web_search              → Google Custom Search (live web: referees, injuries, news)
  get_team_form           → recent match results from internal DB (4,585 matches)
  get_team_stats          → aggregate win/goal/BTTS stats from internal DB
  get_h2h_stats           → head-to-head history from internal DB
  get_upcoming_odds       → upcoming PL fixtures + odds from BetsAPI
"""

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Tool schema (OpenAI function-calling format) ──────────────────────────────

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for current information not in the database. "
                "Use for: referee statistics/background, player injuries, team news, "
                "match previews, manager quotes, stadium weather, or any live topic."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Specific search query in English or Portuguese. "
                            "Examples: 'Michael Oliver referee stats Premier League 2026', "
                            "'Arsenal injury news March 2026', 'Liverpool vs Chelsea preview'"
                        ),
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "Number of results to retrieve (default 5, max 10)",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_team_form",
            "description": (
                "Get a Premier League team's recent match results from the internal historical "
                "database (4,585 matches, 2014–2026). Returns form string (e.g. WWDLL), "
                "wins/draws/losses, average goals scored and conceded, and last N match details."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "team_name": {
                        "type": "string",
                        "description": "Team name as in the Premier League (e.g. Arsenal, Chelsea, Liverpool)",
                    },
                    "n": {
                        "type": "integer",
                        "description": "Number of recent matches (default 10, max 20)",
                        "default": 10,
                    },
                },
                "required": ["team_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_team_stats",
            "description": (
                "Get aggregate historical statistics for a Premier League team: "
                "win rate, draw rate, average goals scored/conceded, clean sheet rate, BTTS rate. "
                "Based on the last 50 matches in the internal database."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "team_name": {
                        "type": "string",
                        "description": "Team name as in the Premier League",
                    },
                },
                "required": ["team_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_h2h_stats",
            "description": (
                "Get head-to-head historical match results between two Premier League teams "
                "from the internal database. Returns total meetings, wins per side, "
                "average goals, and the last N individual match scores."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "home_team": {
                        "type": "string",
                        "description": "Name of the first / home team",
                    },
                    "away_team": {
                        "type": "string",
                        "description": "Name of the second / away team",
                    },
                    "n": {
                        "type": "integer",
                        "description": "Number of recent encounters to return (default 10)",
                        "default": 10,
                    },
                },
                "required": ["home_team", "away_team"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_team_profile",
            "description": "Retorna perfil avançado de um time: eficiência de chutes, xG, distribuição de gols por tempo (1T/2T), taxa de vitória em casa vs fora.",
            "parameters": {
                "type": "object",
                "properties": {
                    "team_name": {
                        "type": "string",
                        "description": "Nome do time (ex: Arsenal, Liverpool)"
                    }
                },
                "required": ["team_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_referee_stats",
            "description": "Retorna estatísticas históricas de um árbitro da Premier League: média de cartões amarelos, vermelhos e faltas por jogo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "referee_name": {
                        "type": "string",
                        "description": "Nome do árbitro (ex: Michael Oliver, Anthony Taylor)"
                    }
                },
                "required": ["referee_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_upcoming_odds",
            "description": (
                "Fetch upcoming Premier League fixtures with current betting odds and kick-off times "
                "from BetsAPI. Use when the user asks about future matches, the next game for a team, "
                "or odds for upcoming fixtures."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "team_filter": {
                        "type": "string",
                        "description": (
                            "Optional team name to filter results. "
                            "If provided, returns only matches involving that team."
                        ),
                    },
                },
                "required": [],
            },
        },
    },
]


# ── Dispatcher ────────────────────────────────────────────────────────────────

async def execute_tool(name: str, args: dict[str, Any]) -> str:
    """Dispatch a tool call and return its result as a string for the LLM."""
    try:
        if name == "web_search":
            from app.services.search import web_search
            return await web_search(
                args["query"],
                num_results=int(args.get("num_results", 5)),
            )

        elif name == "get_team_form":
            return await asyncio.to_thread(
                _team_form_sync,
                args["team_name"],
                int(args.get("n", 10)),
            )

        elif name == "get_team_stats":
            return await asyncio.to_thread(_team_stats_sync, args["team_name"])

        elif name == "get_h2h_stats":
            return await asyncio.to_thread(
                _h2h_sync,
                args["home_team"],
                args["away_team"],
                int(args.get("n", 10)),
            )

        elif name == "get_upcoming_odds":
            return await _upcoming_odds_async(args.get("team_filter"))

        elif name == "get_team_profile":
            return await asyncio.to_thread(_team_profile_sync, args["team_name"])

        elif name == "get_referee_stats":
            return await asyncio.to_thread(_referee_stats_sync, args["referee_name"])

        else:
            return json.dumps({"error": f"unknown tool: {name}"})

    except Exception as exc:
        logger.warning("execute_tool(%s) failed: %s", name, exc)
        return json.dumps({"error": str(exc)})


# ── Sync tool implementations (wrapped with asyncio.to_thread) ────────────────

def _team_form_sync(team_name: str, n: int) -> str:
    from app.services.analytics import get_team_form

    form = get_team_form(team_name, min(n, 20))
    if not form:
        return f"Nenhum dado encontrado para o time: {team_name}"

    lines = [
        f"Forma recente — {form.team_name} (últimos {form.last_n_matches} jogos):",
        f"  Forma: {form.form_string}",
        f"  V/E/D: {form.wins}/{form.draws}/{form.losses}",
        f"  Gols marcados/jogo: {form.avg_goals_scored:.2f}",
        f"  Gols sofridos/jogo: {form.avg_goals_conceded:.2f}",
    ]
    if form.matches:
        lines.append("  Últimas partidas:")
        for m in form.matches[:5]:
            lines.append(
                f"    {m.date or '?'} vs {m.opponent} ({m.home_or_away}) "
                f"{m.goals_scored}–{m.goals_conceded} → {m.result}"
            )
    return "\n".join(lines)


def _team_stats_sync(team_name: str) -> str:
    from app.services.analytics import get_team_historical_stats

    stats = get_team_historical_stats(team_name)
    if not stats:
        return f"Nenhuma estatística encontrada para o time: {team_name}"

    return (
        f"Estatísticas históricas — {stats['team_name']} (amostra: {stats['sample_size']} jogos):\n"
        f"  Taxa de vitória: {stats['win_rate']:.1%}\n"
        f"  Taxa de empate:  {stats['draw_rate']:.1%}\n"
        f"  Gols marcados/jogo: {stats['avg_goals_scored']:.2f}\n"
        f"  Gols sofridos/jogo: {stats['avg_goals_conceded']:.2f}\n"
        f"  Clean sheets: {stats['clean_sheet_rate']:.1%}\n"
        f"  Ambos marcam (BTTS): {stats['btts_rate']:.1%}"
    )


def _h2h_sync(home_team: str, away_team: str, n: int) -> str:
    from app.services.analytics import get_h2h_history

    h2h = get_h2h_history(home_team, away_team, min(n, 20))
    if not h2h or h2h.total_matches == 0:
        return f"Nenhum H2H encontrado entre {home_team} e {away_team}"

    lines = [
        f"H2H histórico — {h2h.home_team} vs {h2h.away_team} ({h2h.total_matches} confrontos):",
        f"  Vitórias {h2h.home_team}: {h2h.home_wins}",
        f"  Empates: {h2h.draws}",
        f"  Vitórias {h2h.away_team}: {h2h.away_wins}",
        f"  Média de gols: {h2h.home_team} {h2h.home_goals_avg:.2f} x "
        f"{h2h.away_goals_avg:.2f} {h2h.away_team}",
    ]
    if h2h.last_matches:
        lines.append("  Últimos confrontos:")
        for m in h2h.last_matches[:5]:
            lines.append(
                f"    {m.date or '?'} — {m.home_team} {m.score_home}–{m.score_away} {m.away_team}"
            )
    return "\n".join(lines)


def _team_profile_sync(team_name: str) -> str:
    from app.services.analytics import get_team_profile
    data = get_team_profile(team_name)
    if not data:
        return f"Perfil não encontrado para o time: {team_name}"
    half = data["goals_by_half"]
    return (
        f"Perfil avançado — {data['team_name']} (amostra: {data['sample_size']} jogos):\n"
        f"  Chutes no alvo/jogo: {data['avg_shots_on_target']}\n"
        f"  Gols/jogo: {data['avg_goals_scored']}\n"
        f"  Shot efficiency: {data['shot_efficiency']:.1%}\n"
        f"  xG médio: {data['avg_xg']}\n"
        f"  Gols 1T/2T: {half['first_half_avg']}/{half['second_half_avg']} avg ({half['first_half_pct']:.0%} no 1T)\n"
        f"  Vitórias em casa: {data['home_win_rate']:.0%}  |  Fora: {data['away_win_rate']:.0%}\n"
        f"  Média gols em casa: {data['home_goals_avg']}  |  Fora: {data['away_goals_avg']}"
    )


def _referee_stats_sync(referee_name: str) -> str:
    from app.services.analytics import get_referee_stats
    data = get_referee_stats(referee_name)
    if not data:
        return f"Árbitro não encontrado: {referee_name}"
    return (
        f"Estatísticas — {data['referee_name']} ({data['matches']} jogos):\n"
        f"  Cartões amarelos/jogo: {data['avg_yellow_cards']}\n"
        f"  Cartões vermelhos/jogo: {data['avg_red_cards']}\n"
        f"  Faltas/jogo: {data['avg_fouls']}\n"
        f"  Taxa vitória mandante: {data['home_win_rate']:.0%}"
    )


# ── Async tool implementations ────────────────────────────────────────────────

async def _upcoming_odds_async(team_filter: str | None) -> str:
    from app.services.betsapi import get_upcoming_matches

    matches = await get_upcoming_matches()
    if not matches:
        return "Nenhuma partida da Premier League encontrada em breve."

    if team_filter:
        from app.repositories.historical import _normalize
        tf = _normalize(team_filter)
        matches = [
            m for m in matches
            if (tf in m.home.name.lower() or m.home.name.lower() in tf or
                tf in m.away.name.lower() or m.away.name.lower() in tf)
        ]
        if not matches:
            return f"Nenhuma partida encontrada para o time: {team_filter}"

    lines = ["Próximas partidas da Premier League:"]
    for m in matches[:10]:
        odds_str = ""
        if m.odds:
            odds_str = (
                f" | {m.home.name} {m.odds.home_win} / "
                f"Empate {m.odds.draw} / "
                f"{m.away.name} {m.odds.away_win}"
            )
        kick = m.kick_off_time or "?"
        lines.append(f"  {m.home.name} vs {m.away.name} — {kick}{odds_str}")

    return "\n".join(lines)
