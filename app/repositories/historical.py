"""
Historical Repository
=====================
Data-access layer for the local Premier League CSV dataset.

Responsibility:  Load, cache and query raw CSV data.
NOT responsible: Business logic, risk calculations, or formatting.

Supabase migration note
-----------------------
This layer is intentionally isolated so it can be swapped for a database
without touching services. To migrate:
  1. Replace _load_*() with async Supabase client queries.
  2. Replace @lru_cache with Redis / application-level caching.
  3. Update COLUMNS mapping if schema differs.

Why Supabase NOW is not required:
  - The dataset is static (4,585 matches, not growing in real time).
  - CSVs load in <1 s and fit comfortably in RAM (~50 MB pandas).
  - A Supabase instance adds latency per query vs. in-process numpy.
  - Recommended only if: multi-replica deploy, real-time ingestion,
    or dataset grows beyond available server RAM.
"""

import os
import logging
from functools import lru_cache
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

_BASE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "betsapi")

# Canonical aliases: maps common long/alternative names → CSV stored names
_ALIASES: dict[str, str] = {
    "manchester city": "man city",
    "manchester united": "man utd",
    "nottingham forest": "nottm forest",
    "sheffield united": "sheff utd",
    "west bromwich albion": "west brom",
    "wolverhampton wanderers": "wolverhampton",
    "wolves": "wolverhampton",
    "spurs": "tottenham",
    "newcastle united": "newcastle",
    "west ham united": "west ham",
    "leeds united": "leeds",
    "brighton & hove albion": "brighton",
}


def _normalize(name: str) -> str:
    """Normalize team name to match CSV stored values."""
    lower = name.lower().strip()
    return _ALIASES.get(lower, lower)

# ── Cache globals ─────────────────────────────────────────────────────────────
_events_df: Optional[pd.DataFrame] = None
_stats_df: Optional[pd.DataFrame] = None
_timeline_df: Optional[pd.DataFrame] = None


def load_events() -> pd.DataFrame:
    global _events_df
    if _events_df is None:
        path = os.path.join(_BASE, "premier_league_events.csv")
        _events_df = pd.read_csv(path, low_memory=False)
        _events_df["time_utc"] = pd.to_datetime(_events_df["time_utc"], errors="coerce")
        logger.info("Historical: loaded events (%d rows)", len(_events_df))
    return _events_df


def load_stats() -> pd.DataFrame:
    global _stats_df
    if _stats_df is None:
        path = os.path.join(_BASE, "premier_league_stats.csv")
        _stats_df = pd.read_csv(path, low_memory=False)
        logger.info("Historical: loaded stats (%d rows)", len(_stats_df))
    return _stats_df


def load_timeline() -> pd.DataFrame:
    global _timeline_df
    if _timeline_df is None:
        path = os.path.join(_BASE, "premier_league_timeline.csv")
        _timeline_df = pd.read_csv(path, low_memory=False)
        logger.info("Historical: loaded timeline (%d rows)", len(_timeline_df))
    return _timeline_df


def get_all_teams() -> list[dict]:
    events = load_events()
    home = events[["home_team_id", "home_team_name"]].rename(
        columns={"home_team_id": "id", "home_team_name": "name"}
    )
    away = events[["away_team_id", "away_team_name"]].rename(
        columns={"away_team_id": "id", "away_team_name": "name"}
    )
    return (
        pd.concat([home, away])
        .drop_duplicates(subset=["id"])
        .sort_values("name")
        .to_dict(orient="records")
    )


def get_team_events(team_name: str, ended_only: bool = True) -> pd.DataFrame:
    """Returns all events where `team_name` played (home or away)."""
    events = load_events()
    if ended_only:
        events = events[events["time_status"] == 3]

    lower = _normalize(team_name)
    home_mask = events["home_team_name"].str.lower() == lower
    away_mask = events["away_team_name"].str.lower() == lower
    mask = home_mask | away_mask

    if not mask.any():
        # Partial match fallback
        home_mask = events["home_team_name"].str.lower().str.contains(lower, na=False)
        away_mask = events["away_team_name"].str.lower().str.contains(lower, na=False)
        mask = home_mask | away_mask

    return events[mask].copy()


def get_h2h_events(home_team: str, away_team: str) -> pd.DataFrame:
    """Returns ended events between two specific teams (either home/away)."""
    events = load_events()
    events = events[events["time_status"] == 3]
    h = _normalize(home_team)
    a = _normalize(away_team)

    def _match(col: str, name: str) -> pd.Series:
        lower_col = events[col].str.lower()
        exact = lower_col == name
        if exact.any():
            return exact
        return lower_col.str.contains(name, na=False, regex=False)

    mask = (_match("home_team_name", h) & _match("away_team_name", a)) | \
           (_match("home_team_name", a) & _match("away_team_name", h))
    return events[mask].sort_values("time_unix", ascending=False).copy()


def get_timeline_goals() -> pd.DataFrame:
    """Returns timeline rows that are goal events."""
    tl = load_timeline()
    goal_mask = tl["text"].str.contains(r"Goal|goal", case=False, na=False, regex=True)
    not_miss = ~tl["text"].str.contains(
        r"Miss|Wide|Woodwork|no goal|disallow", case=False, na=False, regex=True
    )
    return tl[goal_mask & not_miss].copy()


def get_timeline_cards() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (yellow_df, red_df) timeline rows."""
    tl = load_timeline()
    yellow = tl[tl["text"].str.contains(r"Yellow Card", case=False, na=False, regex=True)].copy()
    red = tl[tl["text"].str.contains(r"Red Card|Straight Red", case=False, na=False, regex=True)].copy()
    return yellow, red


def count_ended_matches() -> int:
    events = load_events()
    return int((events["time_status"] == 3).sum())


def get_all_referees() -> list[str]:
    """Returns sorted list of unique referee names."""
    events = load_events()
    return sorted(events["referee_name"].dropna().unique().tolist())


def get_referee_events(referee_name: str) -> pd.DataFrame:
    """Returns all ended events officiated by this referee (partial match fallback)."""
    events = load_events()
    events = events[events["time_status"] == 3]
    lower = referee_name.lower()
    mask = events["referee_name"].str.lower() == lower
    if not mask.any():
        mask = events["referee_name"].str.lower().str.contains(lower, na=False)
    return events[mask].copy()


def get_team_stat_values(team_name: str, metrics: list) -> pd.DataFrame:
    """
    Returns a DataFrame with columns: event_id, metric, team_value, opponent_value, is_home.
    Joins events for team_name with the stats CSV, filtered to given metrics.
    """
    events = get_team_events(team_name)
    if events.empty:
        return pd.DataFrame()
    stats = load_stats()
    lower = team_name.lower()
    merged = stats[stats["event_id"].isin(events["event_id"]) & stats["metric"].isin(metrics)].merge(
        events[["event_id", "home_team_name", "away_team_name"]],
        on="event_id", how="left"
    )
    merged["is_home"] = merged["home_team_name"].str.lower() == lower
    merged["team_value"] = merged.apply(
        lambda r: r["home_value"] if r["is_home"] else r["away_value"], axis=1
    )
    merged["opponent_value"] = merged.apply(
        lambda r: r["away_value"] if r["is_home"] else r["home_value"], axis=1
    )
    return merged[["event_id", "metric", "team_value", "opponent_value", "is_home"]].copy()
