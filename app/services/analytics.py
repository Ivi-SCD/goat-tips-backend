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

import numpy as np
import pandas as pd

from app.repositories.historical import (
    get_all_teams, get_team_events, get_h2h_events,
    get_timeline_goals, get_timeline_cards, count_ended_matches,
    load_events,
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


def get_team_profile(team_name: str) -> Optional[dict]:
    """
    Extended team profile: shot efficiency, goals by half, home/away splits.
    Uses stats CSV for shots/xg, timeline for half-time goal distribution.
    """
    from app.repositories.historical import get_team_stat_values, get_team_events, get_timeline_goals

    events = get_team_events(team_name)
    if events.empty:
        return None

    lower = team_name.lower()
    # Resolve actual name
    actual_name = team_name
    if not events.empty:
        r = events.iloc[0]
        if str(r.get("home_team_name", "")).lower() == lower:
            actual_name = r["home_team_name"]
        elif str(r.get("away_team_name", "")).lower() == lower:
            actual_name = r["away_team_name"]

    n = len(events)

    # ── Shot efficiency from stats CSV ──────────────────────────────
    stat_df = get_team_stat_values(team_name, ["on_target", "goals", "xg"])

    shots = stat_df[stat_df["metric"] == "on_target"]["team_value"].apply(pd.to_numeric, errors="coerce").dropna()
    goals_stats = stat_df[stat_df["metric"] == "goals"]["team_value"].apply(pd.to_numeric, errors="coerce").dropna()
    xg_vals = stat_df[stat_df["metric"] == "xg"]["team_value"].apply(pd.to_numeric, errors="coerce").dropna()

    avg_shots = round(float(shots.mean()), 2) if not shots.empty else 0.0
    avg_goals_stat = round(float(goals_stats.mean()), 2) if not goals_stats.empty else 0.0
    avg_xg = round(float(xg_vals.mean()), 2) if not xg_vals.empty else 0.0
    shot_eff = round(avg_goals_stat / avg_shots, 3) if avg_shots > 0 else 0.0

    # ── Goals by half from timeline ──────────────────────────────────
    goals_tl = get_timeline_goals()
    team_event_ids = set(events["event_id"].astype(str).tolist())
    team_goals = goals_tl[goals_tl["event_id"].astype(str).isin(team_event_ids)].copy()
    team_goals["minute"] = team_goals["text"].apply(_extract_minute)
    team_goals = team_goals.dropna(subset=["minute"])
    team_goals["minute"] = team_goals["minute"].astype(int)

    # Filter to only goals scored by this team (text contains team name)
    # Fallback: use all goals in their matches divided by 2
    first_half = int((team_goals["minute"] <= 45).sum())
    second_half = int((team_goals["minute"] > 45).sum())
    total_goals_half = first_half + second_half
    goals_per_match = total_goals_half / n if n else 0
    fh_avg = round(first_half / n, 2) if n else 0.0
    sh_avg = round(second_half / n, 2) if n else 0.0
    fh_pct = round(first_half / total_goals_half, 3) if total_goals_half else 0.5

    # ── Home vs Away splits ──────────────────────────────────────────
    home_events = events[events["home_team_name"].str.lower() == lower].copy()
    away_events = events[events["away_team_name"].str.lower() == lower].copy()

    def _win_rate(df_side, scored_col, conceded_col):
        if df_side.empty:
            return 0.0
        wins = ((df_side[scored_col].fillna(0).astype(int)) > (df_side[conceded_col].fillna(0).astype(int))).sum()
        return round(int(wins) / len(df_side), 3)

    home_win_rate = _win_rate(home_events, "home_score", "away_score")
    away_win_rate = _win_rate(away_events, "away_score", "home_score")

    home_goals_avg = round(home_events["home_score"].fillna(0).astype(int).mean(), 2) if not home_events.empty else 0.0
    away_goals_avg = round(away_events["away_score"].fillna(0).astype(int).mean(), 2) if not away_events.empty else 0.0

    return {
        "team_name": actual_name,
        "sample_size": n,
        "avg_shots_on_target": avg_shots,
        "avg_goals_scored": avg_goals_stat,
        "shot_efficiency": shot_eff,
        "avg_xg": avg_xg,
        "goals_by_half": {
            "first_half_avg": fh_avg,
            "second_half_avg": sh_avg,
            "first_half_pct": fh_pct,
        },
        "home_win_rate": home_win_rate,
        "away_win_rate": away_win_rate,
        "home_goals_avg": float(home_goals_avg),
        "away_goals_avg": float(away_goals_avg),
    }


def get_referee_stats(referee_name: str) -> Optional[dict]:
    """Returns per-game averages for a referee: cards, fouls, home win rate."""
    from app.repositories.historical import get_referee_events, load_stats

    events = get_referee_events(referee_name)
    if events.empty:
        return None

    actual_name = str(events.iloc[0].get("referee_name", referee_name))
    n = len(events)
    event_ids = events["event_id"].tolist()

    stats = load_stats()
    ref_stats = stats[stats["event_id"].isin(event_ids)]

    def _avg_metric(metric: str, col: str) -> float:
        vals = ref_stats[ref_stats["metric"] == metric][col].apply(
            pd.to_numeric, errors="coerce").dropna()
        if vals.empty:
            return 0.0
        return round(float(vals.sum()) / n, 2)

    avg_yellow = round(
        (_avg_metric("yellowcards", "home_value") * n +
         _avg_metric("yellowcards", "away_value") * n) / n, 2
    )
    avg_red = round(
        (_avg_metric("redcards", "home_value") * n +
         _avg_metric("redcards", "away_value") * n) / n, 2
    )
    avg_fouls = round(
        (_avg_metric("fouls", "home_value") * n +
         _avg_metric("fouls", "away_value") * n) / n, 2
    )

    # Simpler approach: sum both sides
    yc = ref_stats[ref_stats["metric"] == "yellowcards"]
    if not yc.empty:
        yc_home = pd.to_numeric(yc["home_value"], errors="coerce").fillna(0).sum()
        yc_away = pd.to_numeric(yc["away_value"], errors="coerce").fillna(0).sum()
        avg_yellow = round(float(yc_home + yc_away) / n, 2)

    rc = ref_stats[ref_stats["metric"] == "redcards"]
    if not rc.empty:
        rc_home = pd.to_numeric(rc["home_value"], errors="coerce").fillna(0).sum()
        rc_away = pd.to_numeric(rc["away_value"], errors="coerce").fillna(0).sum()
        avg_red = round(float(rc_home + rc_away) / n, 2)

    fouls_df = ref_stats[ref_stats["metric"] == "fouls"]
    if not fouls_df.empty:
        f_home = pd.to_numeric(fouls_df["home_value"], errors="coerce").fillna(0).sum()
        f_away = pd.to_numeric(fouls_df["away_value"], errors="coerce").fillna(0).sum()
        avg_fouls = round(float(f_home + f_away) / n, 2)

    hs = pd.to_numeric(events["home_score"], errors="coerce")
    as_ = pd.to_numeric(events["away_score"], errors="coerce")
    home_wins = int((hs > as_).sum())
    home_win_rate = round(home_wins / n, 3)

    return {
        "referee_name": actual_name,
        "matches": n,
        "avg_yellow_cards": avg_yellow,
        "avg_red_cards": avg_red,
        "avg_fouls": avg_fouls,
        "home_win_rate": home_win_rate,
    }


def get_all_referees() -> list[str]:
    from app.repositories.historical import get_all_referees as _repo
    return _repo()


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


@lru_cache(maxsize=1)
def get_model_calibration(n_matches: int = 500) -> dict:
    """Run backtesting on the last N matches and return calibration metrics."""
    from app.services.predictor import predict_match, get_model
    get_model()  # ensure loaded

    events = load_events()
    ended = events[events["time_status"] == 3].copy()
    ended["home_score"] = pd.to_numeric(ended["home_score"], errors="coerce")
    ended["away_score"] = pd.to_numeric(ended["away_score"], errors="coerce")
    ended = ended.dropna(subset=["home_score", "away_score"])
    ended = ended.sort_values("time_unix", ascending=False).head(n_matches)

    bins = np.arange(0, 1.05, 0.1)
    markets = {
        "home_win": [], "draw": [], "away_win": [],
        "over_2_5": [], "btts": [],
    }

    for _, row in ended.iterrows():
        pred = predict_match(str(row["home_team_name"]), str(row["away_team_name"]))
        hs, aw = int(row["home_score"]), int(row["away_score"])
        markets["home_win"].append((pred.home_win_prob, 1 if hs > aw else 0))
        markets["draw"].append((pred.draw_prob, 1 if hs == aw else 0))
        markets["away_win"].append((pred.away_win_prob, 1 if hs < aw else 0))
        markets["over_2_5"].append((pred.over_2_5_prob, 1 if (hs + aw) > 2 else 0))
        markets["btts"].append((pred.btts_prob, 1 if (hs > 0 and aw > 0) else 0))

    result = {"sample_size": len(ended), "markets": {}}

    for market, preds_actuals in markets.items():
        preds = np.array([p for p, a in preds_actuals])
        actuals = np.array([a for p, a in preds_actuals])
        brier = float(np.mean((preds - actuals) ** 2))

        cal_bins = []
        bin_idxs = np.digitize(preds, bins) - 1
        for i in range(len(bins) - 1):
            mask = bin_idxs == i
            if mask.sum() >= 5:
                cal_bins.append({
                    "range": f"{bins[i]:.1f}-{bins[i+1]:.1f}",
                    "count": int(mask.sum()),
                    "avg_predicted": round(float(preds[mask].mean()), 3),
                    "avg_actual": round(float(actuals[mask].mean()), 3),
                })

        result["markets"][market] = {
            "brier_score": round(brier, 4),
            "avg_predicted": round(float(preds.mean()), 3),
            "avg_actual": round(float(actuals.mean()), 3),
            "calibration_bins": cal_bins,
        }

    return result
