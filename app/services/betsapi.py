import httpx
import os
import logging
from typing import Optional
from app.models import MatchContext, TeamInfo, OddsSnapshot, ImpliedProbabilities
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.b365api.com"
TOKEN = os.getenv("BETSAPI_TOKEN")

# ATENÇÃO: 94 = Premier League na BetsAPI
#          535 = Premier League no FBref — IDs diferentes, não confundir
PREMIER_LEAGUE_ID = int(os.getenv("PREMIER_LEAGUE_ID", "94"))

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


def _build_match(event: dict, status: str) -> MatchContext:
    """Monta um MatchContext a partir de um evento bruto da BetsAPI."""
    score_home, score_away = _parse_score(event.get("ss", "0-0"))
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
    )


async def get_live_matches() -> list[MatchContext]:
    """Retorna partidas da Premier League ao vivo, com odds e probabilidades."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{BASE_URL}/v1/events/inplay",
            params={"token": TOKEN, "sport_id": 1, "league_id": PREMIER_LEAGUE_ID},
        )
        r.raise_for_status()
        data = r.json()

    results = []
    for event in data.get("results", []):
        match = _build_match(event, status="live")
        odds = await _fetch_odds(match.event_id)
        if odds:
            match.odds = odds
            match.probabilities = _remove_bookmaker_margin(
                odds.home_win, odds.draw, odds.away_win
            )
        results.append(match)
    return results


async def get_upcoming_matches() -> list[MatchContext]:
    """Retorna próximos jogos da Premier League com odds pré-jogo."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{BASE_URL}/v1/events/upcoming",
            params={"token": TOKEN, "sport_id": 1, "league_id": PREMIER_LEAGUE_ID},
        )
        r.raise_for_status()
        data = r.json()

    results = []
    for event in data.get("results", []):
        match = _build_match(event, status="upcoming")
        odds = await _fetch_odds(match.event_id)
        if odds:
            match.odds = odds
            match.probabilities = _remove_bookmaker_margin(
                odds.home_win, odds.draw, odds.away_win
            )
        results.append(match)
    return results


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
