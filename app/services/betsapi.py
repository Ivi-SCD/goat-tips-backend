import asyncio
import httpx
import os
import logging
from datetime import datetime, timezone
from typing import Optional
from app.schemas.match import (
    MatchContext, TeamInfo, OddsSnapshot, ImpliedProbabilities,
    StatsTrend, PeriodStats, LineupInfo, LineupTeam, PlayerInfo,
    LeagueToplist, TopPlayer,
)
from app.schemas.analytics import H2HRecord, H2HMatch

from app.core.settings import get_settings

settings = get_settings()

BASE_URL = "https://api.b365api.com"
TOKEN = settings.BETSAPI_TOKEN

# ATENÇÃO: 94 = Premier League na BetsAPI
#          535 = Premier League no FBref — IDs diferentes, não confundir
PREMIER_LEAGUE_ID = int(settings.PREMIER_LEAGUE_ID)

LOGO_URL = "https://assets.b365api.com/images/team/m/{image_id}.png"
logger = logging.getLogger(__name__)


def _remove_bookmaker_margin(home: float, draw: float, away: float) -> ImpliedProbabilities:
    """
    Converte odds brutas em probabilidades reais removendo a margem da casa.
    Ex: odds 2.10 / 3.40 / 3.60 → margem ~4.8% → probs reais somam 100%.
    """
    raw = [1 / home, 1 / draw, 1 / away]
    total = sum(raw)
    margin = total - 1.0
    return ImpliedProbabilities(
        home_win=round(raw[0] / total, 4),
        draw=round(raw[1] / total, 4),
        away_win=round(raw[2] / total, 4),
        market_margin=round(margin, 4),
    )


def _parse_team(data: dict) -> TeamInfo:
    image_id = data.get("image_id", "")
    return TeamInfo(
        id=str(data.get("id", "")),
        name=data.get("name", "Unknown"),
        image_url=LOGO_URL.format(image_id=image_id) if image_id else None,
    )


def _parse_score(ss: str) -> tuple[int, int]:
    """Extrai placar do campo 'ss' (ex: '2-1' → (2, 1))."""
    try:
        parts = ss.split("-")
        return int(parts[0]), int(parts[1])
    except Exception:
        return 0, 0


def _parse_odds(odds_data: dict) -> Optional[OddsSnapshot]:
    """
    Extrai odds 1X2 e mercados secundários.
    Estrutura esperada: { "1_1": {"home_od": ..., "draw_od": ..., "away_od": ...} }
    """
    try:
        market = odds_data.get("1_1", {})
        home = float(market.get("home_od", 0))
        draw = float(market.get("draw_od", 0))
        away = float(market.get("away_od", 0))
        if not all([home, draw, away]):
            return None

        ou_market = odds_data.get("1_2", {})
        over = float(ou_market.get("over_od", 0)) or None

        btts_market = odds_data.get("1_3", {})
        btts = float(btts_market.get("yes_od", 0)) or None

        return OddsSnapshot(home_win=home, draw=draw, away_win=away, over_2_5=over, btts=btts)
    except Exception:
        return None


async def _fetch_odds(event_id: str) -> Optional[OddsSnapshot]:
    """
    Odds não vêm no payload do inplay — precisam ser buscadas separadamente.
    """
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
            r = await client.get(
                f"{BASE_URL}/v2/event/odds/summary",
                params={"token": TOKEN, "event_id": event_id},
            )
            if r.status_code != 200:
                return None
            data = r.json()
    except httpx.TimeoutException:
        logger.warning("Timeout fetching odds for event_id=%s", event_id)
        return None
    except httpx.HTTPError as exc:
        logger.warning("HTTP error fetching odds for event_id=%s: %s", event_id, exc)
        return None

    odds_raw = data.get("results", {}).get("odds", {})
    return _parse_odds(odds_raw)


def _parse_kick_off(time_unix_str) -> Optional[str]:
    """Convert Unix timestamp string to ISO 8601 UTC string."""
    try:
        ts = int(time_unix_str)
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None


def _build_match(event: dict, status: str) -> MatchContext:
    """Monta um MatchContext a partir de um evento bruto da BetsAPI."""
    score_home, score_away = _parse_score(event.get("ss") or "0-0")
    extra = event.get("extra", {}) or {}

    referee_info = extra.get("referee", {})
    referee = referee_info.get("name") if isinstance(referee_info, dict) else None

    stadium_info = extra.get("stadium_data", {})
    stadium = None
    if isinstance(stadium_info, dict) and stadium_info.get("name"):
        city = stadium_info.get("city", "")
        stadium = f"{stadium_info['name']}, {city}" if city else stadium_info["name"]

    return MatchContext(
        event_id=str(event["id"]),
        home=_parse_team(event.get("home", {})),
        away=_parse_team(event.get("away", {})),
        minute=event.get("timer", {}).get("tm") if status == "live" else None,
        score_home=score_home,
        score_away=score_away,
        status=status,
        odds=None,
        probabilities=None,
        kick_off_time=_parse_kick_off(event.get("time")),
        round=str(extra.get("round", "")) or None,
        referee=referee,
        stadium=stadium,
        bet365_id=event.get("bet365_id"),
    )


async def get_live_matches() -> list[MatchContext]:
    """Retorna partidas da Premier League ao vivo, com odds e probabilidades."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            r = await client.get(
                f"{BASE_URL}/v1/events/inplay",
                params={"token": TOKEN, "sport_id": 1, "league_id": PREMIER_LEAGUE_ID},
            )
            r.raise_for_status()
            data = r.json()
    except (httpx.TimeoutException, httpx.HTTPError) as exc:
        logger.warning("get_live_matches failed: %s", exc)
        return []

    matches = [_build_match(e, status="live") for e in data.get("results", [])]

    # Parallel odds fetch (same pattern as upcoming)
    odds_results = await asyncio.gather(
        *[_fetch_odds(m.event_id) for m in matches],
        return_exceptions=True,
    )
    for match, odds in zip(matches, odds_results):
        if isinstance(odds, OddsSnapshot):
            match.odds = odds
            match.probabilities = _remove_bookmaker_margin(
                odds.home_win, odds.draw, odds.away_win
            )
    return matches


async def get_upcoming_matches() -> list[MatchContext]:
    """Retorna próximos jogos da Premier League com odds pré-jogo."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            r = await client.get(
                f"{BASE_URL}/v1/events/upcoming",
                params={"token": TOKEN, "sport_id": 1, "league_id": PREMIER_LEAGUE_ID},
            )
            r.raise_for_status()
            data = r.json()
    except (httpx.TimeoutException, httpx.HTTPError) as exc:
        logger.warning("get_upcoming_matches failed: %s", exc)
        return []

    events = data.get("results", [])
    matches = [_build_match(e, status="upcoming") for e in events]

    # Fetch odds for all upcoming matches in parallel
    odds_results = await asyncio.gather(
        *[_fetch_odds(m.event_id) for m in matches],
        return_exceptions=True,
    )

    for match, odds in zip(matches, odds_results):
        if isinstance(odds, OddsSnapshot):
            match.odds = odds
            match.probabilities = _remove_bookmaker_margin(
                odds.home_win, odds.draw, odds.away_win
            )

    return matches


async def get_match_by_id(event_id: str) -> Optional[MatchContext]:
    """Busca contexto completo de uma partida específica pelo ID."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{BASE_URL}/v1/event/view",
            params={"token": TOKEN, "event_id": event_id},
        )
        r.raise_for_status()
        data = r.json()

    results = data.get("results")
    if not results:
        return None

    # /v1/event/view retorna lista, não dict — pega o primeiro elemento
    event = results[0] if isinstance(results, list) else results

    status_map = {"1": "live", "3": "ended", "0": "upcoming"}
    status = status_map.get(str(event.get("time_status", "")), "unknown")

    match = _build_match(event, status=status)
    odds = await _fetch_odds(event_id)
    if odds:
        match.odds = odds
        match.probabilities = _remove_bookmaker_margin(
            odds.home_win, odds.draw, odds.away_win
        )
    return match


# ── New endpoints ──────────────────────────────────────────────────────────────

def _parse_stat_value(raw) -> Optional[int]:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _build_period_stats(period_label: str, stats: dict) -> PeriodStats:
    """Converte raw stats dict em PeriodStats."""
    # BetsAPI stats keys: attacks, dangerous_attacks, on_target, off_target, corners, possession_rt
    home = stats.get("home", {})
    away = stats.get("away", {})

    home_shots = _parse_stat_value(home.get("on_target", home.get("shots_on_target")))
    away_shots = _parse_stat_value(away.get("on_target", away.get("shots_on_target")))

    home_poss_raw = home.get("possession_rt")
    away_poss_raw = away.get("possession_rt")
    try:
        home_poss = float(home_poss_raw) if home_poss_raw is not None else None
        away_poss = float(away_poss_raw) if away_poss_raw is not None else None
    except (TypeError, ValueError):
        home_poss = away_poss = None

    return PeriodStats(
        period=period_label,
        home_shots=home_shots,
        away_shots=away_shots,
        home_shots_on_target=home_shots,
        away_shots_on_target=away_shots,
        home_corners=_parse_stat_value(home.get("corners")),
        away_corners=_parse_stat_value(away.get("corners")),
        home_possession=home_poss,
        away_possession=away_poss,
        home_dangerous_attacks=_parse_stat_value(home.get("dangerous_attacks")),
        away_dangerous_attacks=_parse_stat_value(away.get("dangerous_attacks")),
        home_attacks=_parse_stat_value(home.get("attacks")),
        away_attacks=_parse_stat_value(away.get("attacks")),
    )


def _calculate_momentum(periods: list[PeriodStats]) -> tuple[float, str]:
    """
    Calcula momentum score de -1 (away domina) a +1 (home domina).
    Usa o período mais recente com dados suficientes.
    """
    if not periods:
        return 0.0, "Equilibrado"

    # Usa último período com dados
    recent = periods[-1]
    scores = []

    if recent.home_shots is not None and recent.away_shots is not None:
        total = (recent.home_shots or 0) + (recent.away_shots or 0)
        if total > 0:
            scores.append(((recent.home_shots or 0) - (recent.away_shots or 0)) / total)

    if recent.home_dangerous_attacks is not None and recent.away_dangerous_attacks is not None:
        total = (recent.home_dangerous_attacks or 0) + (recent.away_dangerous_attacks or 0)
        if total > 0:
            scores.append(
                ((recent.home_dangerous_attacks or 0) - (recent.away_dangerous_attacks or 0)) / total
            )

    if recent.home_corners is not None and recent.away_corners is not None:
        total = (recent.home_corners or 0) + (recent.away_corners or 0)
        if total > 0:
            scores.append(((recent.home_corners or 0) - (recent.away_corners or 0)) / total)

    if not scores:
        return 0.0, "Sem dados suficientes"

    score = round(sum(scores) / len(scores), 3)

    if score > 0.25:
        label = "Domínio do Mandante"
    elif score > 0.1:
        label = "Leve vantagem do Mandante"
    elif score < -0.25:
        label = "Domínio do Visitante"
    elif score < -0.1:
        label = "Leve vantagem do Visitante"
    else:
        label = "Equilibrado"

    return score, label


async def get_stats_trend(event_id: str) -> Optional[StatsTrend]:
    """
    Busca estatísticas por período de uma partida via /v1/event/stats_trend.
    Retorna dados de pressão, chutes, escanteios e posse por período.
    """
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
            r = await client.get(
                f"{BASE_URL}/v1/event/stats_trend",
                params={"token": TOKEN, "event_id": event_id},
            )
            if r.status_code != 200:
                return None
            data = r.json()
    except (httpx.TimeoutException, httpx.HTTPError) as exc:
        logger.warning("Error fetching stats_trend for event_id=%s: %s", event_id, exc)
        return None

    results = data.get("results", {})
    if not results:
        return None

    periods: list[PeriodStats] = []

    # BetsAPI returns stats keyed by period number or label
    # Try common structures: list of period objects or dict with period keys
    if isinstance(results, list):
        for i, period_data in enumerate(results):
            label = period_data.get("name", f"period_{i+1}")
            periods.append(_build_period_stats(label, period_data))
    elif isinstance(results, dict):
        for key, period_data in results.items():
            if isinstance(period_data, dict):
                periods.append(_build_period_stats(str(key), period_data))

    if not periods:
        # Fallback: treat results as single period
        periods.append(_build_period_stats("full", results))

    momentum_score, momentum_label = _calculate_momentum(periods)

    return StatsTrend(
        event_id=event_id,
        periods=periods,
        momentum_score=momentum_score,
        momentum_label=momentum_label,
    )


async def get_h2h(event_id: str) -> Optional[H2HRecord]:
    """
    Busca histórico H2H via /v1/event/history.
    Retorna partidas anteriores entre os dois times.
    """
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
            r = await client.get(
                f"{BASE_URL}/v1/event/history",
                params={"token": TOKEN, "event_id": event_id},
            )
            if r.status_code != 200:
                return None
            data = r.json()
    except (httpx.TimeoutException, httpx.HTTPError) as exc:
        logger.warning("Error fetching H2H for event_id=%s: %s", event_id, exc)
        return None

    results = data.get("results", {})
    if not results:
        return None

    h2h_matches_raw = results.get("H2H", []) or results.get("h2h", []) or []
    home_team_name = results.get("home_team_name", "Home")
    away_team_name = results.get("away_team_name", "Away")

    parsed_matches: list[H2HMatch] = []
    home_wins = away_wins = draws = 0
    total_home_goals = total_away_goals = 0

    for m in h2h_matches_raw:
        ss = m.get("ss", "0-0")
        s_home, s_away = _parse_score(ss)
        # Determine which team is "home" in this historical match
        event_home = m.get("home", {}).get("name", "")
        is_home_our_home = event_home == home_team_name

        if is_home_our_home:
            our_home_goals, our_away_goals = s_home, s_away
        else:
            our_home_goals, our_away_goals = s_away, s_home

        if our_home_goals > our_away_goals:
            winner = "home"
            home_wins += 1
        elif our_home_goals < our_away_goals:
            winner = "away"
            away_wins += 1
        else:
            winner = "draw"
            draws += 1

        total_home_goals += our_home_goals
        total_away_goals += our_away_goals

        time_str = m.get("time_utc") or m.get("time", "")
        parsed_matches.append(H2HMatch(
            event_id=str(m.get("id", "")),
            date=time_str[:10] if time_str else None,
            home_team=m.get("home", {}).get("name", home_team_name),
            away_team=m.get("away", {}).get("name", away_team_name),
            score_home=s_home,
            score_away=s_away,
            winner=winner,
        ))

    total = len(parsed_matches)
    return H2HRecord(
        home_team=home_team_name,
        away_team=away_team_name,
        total_matches=total,
        home_wins=home_wins,
        away_wins=away_wins,
        draws=draws,
        home_goals_avg=round(total_home_goals / total, 2) if total > 0 else 0.0,
        away_goals_avg=round(total_away_goals / total, 2) if total > 0 else 0.0,
        last_matches=parsed_matches[:10],
    )


async def get_lineup(event_id: str) -> Optional[LineupInfo]:
    """
    Busca escalações via /v1/event/lineup.
    """
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
            r = await client.get(
                f"{BASE_URL}/v1/event/lineup",
                params={"token": TOKEN, "event_id": event_id},
            )
            if r.status_code != 200:
                return None
            data = r.json()
    except (httpx.TimeoutException, httpx.HTTPError) as exc:
        logger.warning("Error fetching lineup for event_id=%s: %s", event_id, exc)
        return None

    results = data.get("results", {})
    if not results:
        return None

    def _parse_lineup_team(side: str) -> Optional[LineupTeam]:
        team_data = results.get(side, {})
        if not team_data:
            return None
        team_info = _parse_team(team_data)
        formation = team_data.get("formation")

        starting_xi: list[PlayerInfo] = []
        substitutes: list[PlayerInfo] = []

        for p in team_data.get("lineup", []):
            player = PlayerInfo(
                id=str(p.get("id", "")),
                name=p.get("name", ""),
                number=p.get("shirt_number") or p.get("number"),
                position=p.get("pos") or p.get("position"),
            )
            if p.get("type") in ("sub", "substitute") or p.get("substitute"):
                substitutes.append(player)
            else:
                starting_xi.append(player)

        return LineupTeam(
            team=team_info,
            formation=formation,
            starting_xi=starting_xi,
            substitutes=substitutes,
        )

    return LineupInfo(
        event_id=event_id,
        home=_parse_lineup_team("home"),
        away=_parse_lineup_team("away"),
    )


async def get_league_toplist(league_id: int = PREMIER_LEAGUE_ID) -> Optional[LeagueToplist]:
    """
    Busca artilheiros e garçons da liga via /v1/league/toplist.
    """
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
            r = await client.get(
                f"{BASE_URL}/v1/league/toplist",
                params={"token": TOKEN, "league_id": league_id},
            )
            if r.status_code != 200:
                return None
            data = r.json()
    except (httpx.TimeoutException, httpx.HTTPError) as exc:
        logger.warning("Error fetching toplist for league_id=%s: %s", league_id, exc)
        return None

    results = data.get("results", {})
    if not results:
        return None

    def _parse_players(raw_list: list, stat_key: str) -> list[TopPlayer]:
        players = []
        for i, p in enumerate(raw_list[:10]):
            players.append(TopPlayer(
                rank=i + 1,
                player_name=p.get("player", {}).get("name", p.get("name", "")),
                team_name=p.get("team", {}).get("name", p.get("team_name", "")),
                goals=p.get("goals") if stat_key == "goals" else None,
                assists=p.get("assists") if stat_key == "assists" else None,
            ))
        return players

    scorers_raw = results.get("top_scores", results.get("scorers", []))
    assists_raw = results.get("top_assists", results.get("assists", []))

    return LeagueToplist(
        league_id=league_id,
        top_scorers=_parse_players(scorers_raw, "goals"),
        top_assists=_parse_players(assists_raw, "assists"),
    )
