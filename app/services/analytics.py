"""
Analytics Service
=================
Business logic for historical Premier League statistics.

Responsibility:  Transform raw repository data into domain objects.
Depends on:      app.repositories.historical (data access)
Does NOT:        Make HTTP calls or touch the database directly.
"""

import logging
from functools import lru_cache
from typing import Optional

import pandas as pd

from app.repositories.historical import (
    get_all_teams, get_team_events, get_h2h_events,
    get_timeline_goals, get_timeline_cards, count_ended_matches,
)
from app.schemas.analytics import (
    TeamForm, MatchResult, H2HRecord, H2HMatch,
    GoalPatterns, GoalMinuteBucket, CardPatterns, CardPattern,
)

logger = logging.getLogger(__name__)

_MINUTE_BUCKETS = [
    ("0-15",   0,   15),
    ("16-30",  16,  30),
    ("31-45+", 31,  50),
    ("46-60",  46,  60),
    ("61-75",  61,  75),
    ("76-90+", 76, 120),
]


def _extract_minute(text: str) -> Optional[int]:
    try:
        return int(text.split("'")[0].strip())
    except (ValueError, IndexError):
        return None


# ── Public API ─────────────────────────────────────────────────────────────────

def get_teams() -> list[dict]:
    return get_all_teams()


def get_team_form(team_name: str, n: int = 10) -> Optional[TeamForm]:
    df = get_team_events(team_name)
    if df.empty:
        return None

    df = df.sort_values("time_unix", ascending=False).head(n)

    # Resolve actual name from data
    sample = df.iloc[0]
    if str(sample.get("home_team_name", "")).lower() == team_name.lower():
        actual_name = sample["home_team_name"]
    elif str(sample.get("away_team_name", "")).lower() == team_name.lower():
        actual_name = sample["away_team_name"]
    else:
        actual_name = team_name

    results: list[MatchResult] = []
    wins = draws = losses = goals_scored = goals_conceded = 0

    for _, row in df.iterrows():
        is_home = str(row.get("home_team_name", "")).lower() == team_name.lower()
        hs = int(row.get("home_score", 0) or 0)
        as_ = int(row.get("away_score", 0) or 0)
        scored, conceded = (hs, as_) if is_home else (as_, hs)
        opponent = str(row.get("away_team_name" if is_home else "home_team_name", "Unknown"))

        result = "W" if scored > conceded else ("D" if scored == conceded else "L")
        wins += result == "W"
        draws += result == "D"
        losses += result == "L"
        goals_scored += scored
        goals_conceded += conceded

        date = str(row["time_utc"])[:10] if pd.notna(row.get("time_utc")) else ""
        results.append(MatchResult(
            event_id=str(row["event_id"]),
            date=date,
            opponent=opponent,
            home_or_away="home" if is_home else "away",
            goals_scored=scored,
            goals_conceded=conceded,
            result=result,
        ))

    total = len(results)
    return TeamForm(
        team_name=actual_name,
        last_n_matches=total,
        matches=results,
        wins=wins, draws=draws, losses=losses,
        goals_scored=goals_scored,
        goals_conceded=goals_conceded,
        form_string="".join(r.result for r in results),
        avg_goals_scored=round(goals_scored / total, 2) if total else 0.0,
        avg_goals_conceded=round(goals_conceded / total, 2) if total else 0.0,
    )


def get_h2h_history(home_team: str, away_team: str, n: int = 10) -> Optional[H2HRecord]:
    df = get_h2h_events(home_team, away_team).head(n)
    if df.empty:
        return None

    parsed: list[H2HMatch] = []
    home_wins = away_wins = draws = total_hg = total_ag = 0
    h_lower = home_team.lower()

    for _, row in df.iterrows():
        ht = str(row.get("home_team_name", ""))
        hs = int(row.get("home_score", 0) or 0)
        as_ = int(row.get("away_score", 0) or 0)
        our_home = as_ if ht.lower() != h_lower else hs
        our_away = hs if ht.lower() != h_lower else as_

        winner = "home" if our_home > our_away else ("away" if our_home < our_away else "draw")
        home_wins += winner == "home"
        away_wins += winner == "away"
        draws += winner == "draw"
        total_hg += our_home
        total_ag += our_away

        date = str(row["time_utc"])[:10] if pd.notna(row.get("time_utc")) else ""
        parsed.append(H2HMatch(
            event_id=str(row["event_id"]), date=date,
            home_team=ht, away_team=str(row.get("away_team_name", "")),
            score_home=hs, score_away=as_, winner=winner,
        ))

    total = len(parsed)
    return H2HRecord(
        home_team=home_team, away_team=away_team,
        total_matches=total, home_wins=home_wins, away_wins=away_wins, draws=draws,
        home_goals_avg=round(total_hg / total, 2) if total else 0.0,
        away_goals_avg=round(total_ag / total, 2) if total else 0.0,
        last_matches=parsed,
    )


@lru_cache(maxsize=1)
def get_goal_patterns() -> GoalPatterns:
    goals = get_timeline_goals()
    goals["minute"] = goals["text"].apply(_extract_minute)
    goals = goals.dropna(subset=["minute"])
    goals["minute"] = goals["minute"].astype(int)

    total = len(goals)
    total_matches = count_ended_matches()
    avg = round(total / total_matches, 2) if total_matches else 0.0

    buckets: list[GoalMinuteBucket] = []
    peak_range, peak_count = "", 0
    for label, lo, hi in _MINUTE_BUCKETS:
        count = int(((goals["minute"] >= lo) & (goals["minute"] <= hi)).sum())
        pct = round(count / total, 4) if total else 0.0
        buckets.append(GoalMinuteBucket(minute_range=label, goals=count, pct_of_total=pct))
        if count > peak_count:
            peak_count = count
            peak_range = label

    return GoalPatterns(total_goals=total, buckets=buckets,
                        peak_minute_range=peak_range, avg_goals_per_match=avg)


@lru_cache(maxsize=1)
def get_card_patterns() -> CardPatterns:
    yellows, reds = get_timeline_cards()
    for df in (yellows, reds):
        df["minute"] = df["text"].apply(_extract_minute)

    yellows = yellows.dropna(subset=["minute"])
    reds = reds.dropna(subset=["minute"])
    yellows["minute"] = yellows["minute"].astype(int)
    reds["minute"] = reds["minute"].astype(int)

    ty, tr = len(yellows), len(reds)
    total = ty + tr

    buckets: list[CardPattern] = []
    peak_range, peak_count = "", 0
    for label, lo, hi in _MINUTE_BUCKETS:
        y = int(((yellows["minute"] >= lo) & (yellows["minute"] <= hi)).sum())
        r = int(((reds["minute"] >= lo) & (reds["minute"] <= hi)).sum())
        pct = round((y + r) / total, 4) if total else 0.0
        buckets.append(CardPattern(minute_range=label, yellow_cards=y, red_cards=r, pct_of_total=pct))
        if (y + r) > peak_count:
            peak_count = y + r
            peak_range = label

    return CardPatterns(total_yellows=ty, total_reds=tr, buckets=buckets, peak_minute_range=peak_range)


def calculate_goal_risk_score(minute: Optional[int], score_diff: int,
                               stats: Optional[dict] = None) -> float:
    if minute is None:
        return 5.0
    patterns = get_goal_patterns()
    bucket_pct = next(
        (b.pct_of_total for b in patterns.buckets
         for label, lo, hi in _MINUTE_BUCKETS
         if b.minute_range == label and lo <= minute <= hi),
        0.0,
    )
    base = bucket_pct * 30
    pressure = min(abs(score_diff) * 0.5, 1.5)
    urgency = 2.0 if minute >= 75 else (1.0 if minute >= 60 else 0.0)
    stats_bonus = 0.0
    if stats:
        da = (stats.get("home_dangerous_attacks") or 0) + (stats.get("away_dangerous_attacks") or 0)
        stats_bonus = 1.5 if da > 20 else (0.75 if da > 10 else 0.0)
    return round(min(max(base + pressure + urgency + stats_bonus, 0.0), 10.0), 2)


def calculate_card_risk_score(minute: Optional[int],
                               timeline_events: list[str] = None) -> float:
    if minute is None:
        return 3.0
    patterns = get_card_patterns()
    bucket_pct = next(
        (b.pct_of_total for b in patterns.buckets
         for label, lo, hi in _MINUTE_BUCKETS
         if b.minute_range == label and lo <= minute <= hi),
        0.0,
    )
    base = bucket_pct * 30
    yellow_risk = min(sum(1 for e in (timeline_events or []) if "yellow" in e.lower()) * 0.3, 2.0)
    urgency = 2.0 if minute >= 80 else (1.0 if minute >= 70 else 0.0)
    return round(min(max(base + yellow_risk + urgency, 0.0), 10.0), 2)


def get_team_historical_stats(team_name: str) -> dict:
    form = get_team_form(team_name, n=50)
    if not form:
        return {}
    total = form.last_n_matches
    clean_sheets = sum(1 for m in form.matches if m.goals_conceded == 0)
    btts = sum(1 for m in form.matches if m.goals_scored > 0 and m.goals_conceded > 0)
    return {
        "team_name": form.team_name,
        "sample_size": total,
        "wins": form.wins, "draws": form.draws, "losses": form.losses,
        "win_rate": round(form.wins / total, 3) if total else 0.0,
        "draw_rate": round(form.draws / total, 3) if total else 0.0,
        "goals_scored": form.goals_scored,
        "goals_conceded": form.goals_conceded,
        "avg_goals_scored": form.avg_goals_scored,
        "avg_goals_conceded": form.avg_goals_conceded,
        "clean_sheets": clean_sheets,
        "clean_sheet_rate": round(clean_sheets / total, 3) if total else 0.0,
        "btts_matches": btts,
        "btts_rate": round(btts / total, 3) if total else 0.0,
    }
