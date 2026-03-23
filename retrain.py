#!/usr/bin/env python3
"""
Goat Tips — Daily Poisson Model Retraining (with Kaggle Enrichment)
=====================================================================
Pulls finished match data from Supabase, trains the Poisson model,
enriches team-strength features with FBref player stats (Kaggle),
and uploads the serialized artifact to IBM Cloud Object Storage.

Designed to run as an IBM Code Engine Job on a daily cron schedule.

Environment variables required:
    SUPABASE_DB_URL               — Supabase PostgreSQL connection string
    IBM_COS_ACCESS_KEY_ID         — IBM COS HMAC access key
    IBM_COS_SECRET_ACCESS_KEY     — IBM COS HMAC secret key
    IBM_COS_ENDPOINT              — COS endpoint (default: us-south regional)
    IBM_COS_BUCKET                — COS bucket name (default: goat-tips-bucket)
    MODEL_BLOB_NAME               — Object key (default: poisson_model.pkl)
    KAGGLE_PLAYERS_CSV            — Path to FBref players CSV (default: data/kaggle/players_data_2025_2026.csv)
"""

import io
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import joblib
import pandas as pd
import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BUCKET    = os.getenv("IBM_COS_BUCKET", "goat-tips-bucket")
BLOB_NAME = os.getenv("MODEL_BLOB_NAME", "poisson_model.pkl")
CARD_BLOB = os.getenv("MODEL_CARD_BLOB_NAME", "model_card.json")

_REPO_ROOT = Path(__file__).resolve().parent
KAGGLE_PLAYERS_CSV = Path(
    os.getenv("KAGGLE_PLAYERS_CSV", str(_REPO_ROOT / "data" / "kaggle" / "players_data_2025_2026.csv"))
)

# Canonical alias map: FBref "Squad" → Supabase team name variants
_TEAM_ALIASES: dict[str, str] = {
    "manchester city":      "Man City",
    "manchester utd":       "Man Utd",
    "manchester united":    "Man Utd",
    "tottenham hotspur":    "Tottenham",
    "spurs":                "Tottenham",
    "newcastle utd":        "Newcastle",
    "newcastle united":     "Newcastle",
    "sheffield utd":        "Sheffield Utd",
    "sheffield united":     "Sheffield Utd",
    "brighton and hove albion": "Brighton",
    "brighton":             "Brighton",
    "wolverhampton wanderers": "Wolves",
    "wolves":               "Wolves",
    "west ham utd":         "West Ham",
    "west ham united":      "West Ham",
    "nottingham forest":    "Nott'm Forest",
    "nottm forest":         "Nott'm Forest",
    "nott'm forest":        "Nott'm Forest",
    "leicester city":       "Leicester",
    "norwich city":         "Norwich",
    "swansea city":         "Swansea",
    "cardiff city":         "Cardiff",
    "stoke city":           "Stoke",
    "hull city":            "Hull",
    "luton town":           "Luton",
    "ipswich town":         "Ipswich",
}


def normalize_team_name(raw: str) -> str:
    """Map FBref squad names to their Supabase canonical form."""
    key = raw.strip().lower()
    return _TEAM_ALIASES.get(key, raw.strip())


# ── Kaggle enrichment ─────────────────────────────────────────────────────────

def load_kaggle_player_features() -> dict[str, dict]:
    """
    Load FBref player stats and compute per-team offensive/creative/defensive
    strength indices weighted by 90s played.

    Returns a dict keyed by canonical (Supabase) team name:
        {
            "attack_index":     float,  # weighted goals+xG per 90
            "creation_index":   float,  # weighted Ast+xAG+KP+PrgP per 90
            "defensive_index":  float,  # weighted Tkl+Int+Blocks per 90
            "squad_depth":      int,    # number of players with >= 5 90s
        }
    """
    if not KAGGLE_PLAYERS_CSV.exists():
        logger.warning("Kaggle players CSV not found at %s — skipping enrichment", KAGGLE_PLAYERS_CSV)
        return {}

    df = pd.read_csv(KAGGLE_PLAYERS_CSV, low_memory=False)
    logger.info("Loaded Kaggle FBref players: %d rows", len(df))

    # Keep Premier League only
    df = df[df["Comp"].astype(str).str.contains("Premier League", na=False)].copy()
    logger.info("Premier League players: %d rows", len(df))

    if df.empty:
        return {}

    # Columns present in the FBref combined CSV (varies by export)
    numeric_cols = [
        "90s", "Gls", "Ast", "xG", "xAG",
        "KP", "PrgP",
        "TklW", "Int",              # always present
        "Tkl+Int", "Blocks", "Clr", # present in full export only
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        else:
            df[col] = 0.0

    # Drop players with negligible minutes
    df = df[df["90s"] >= 1.0].copy()

    team_features: dict[str, dict] = {}
    for raw_squad, grp in df.groupby("Squad"):
        canonical = normalize_team_name(str(raw_squad))
        total_90s = grp["90s"].sum()
        if total_90s < 1.0:
            continue

        w = grp["90s"] / total_90s  # weight by playing time

        attack_index   = float((w * (grp["Gls"] + grp["xG"])).sum())
        creation_index = float((w * (grp["Ast"] + grp["xAG"] + grp["KP"] + grp["PrgP"])).sum())

        # Use richest available defensive signal
        if grp["Tkl+Int"].sum() > 0:
            defensive_index = float((w * (grp["Tkl+Int"] + grp["Blocks"] + grp["Clr"])).sum())
        else:
            defensive_index = float((w * (grp["TklW"] + grp["Int"])).sum())

        squad_depth = int((grp["90s"] >= 5).sum())

        team_features[canonical] = {
            "attack_index":    round(attack_index, 4),
            "creation_index":  round(creation_index, 4),
            "defensive_index": round(defensive_index, 4),
            "squad_depth":     squad_depth,
        }

    logger.info("Kaggle player features computed for %d teams", len(team_features))
    return team_features


def enrich_team_strengths(
    team_strengths: dict[str, dict],
    kaggle_features: dict[str, dict],
) -> None:
    """Merge Kaggle-derived indices into team_strengths in-place."""
    if not kaggle_features:
        return

    matched = 0
    for team, strengths in team_strengths.items():
        feats = kaggle_features.get(team) or kaggle_features.get(normalize_team_name(team))
        if feats:
            strengths.update(feats)
            matched += 1

    logger.info("Kaggle enrichment: %d / %d teams matched", matched, len(team_strengths))


# ── Data loading ──────────────────────────────────────────────────────────────

def load_training_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pull finished Premier League matches and xG data from Supabase."""
    db_url = os.environ["SUPABASE_DB_URL"]
    logger.info("Connecting to Supabase …")
    conn = psycopg2.connect(db_url)

    query = """
        SELECT
            e.id          AS event_id,
            ht.name       AS home_team_name,
            at.name       AS away_team_name,
            e.home_score,
            e.away_score,
            e.time_utc
        FROM events e
        JOIN teams ht ON ht.id = e.home_team_id
        JOIN teams at ON at.id = e.away_team_id
        WHERE e.time_status = 3          -- ended only
          AND e.home_score IS NOT NULL
          AND e.away_score IS NOT NULL
        ORDER BY e.time_utc ASC
    """
    df = pd.read_sql(query, conn)
    logger.info("Loaded %d finished matches from Supabase", len(df))

    xg_query = """
        SELECT ms.event_id,
               ms.home_value AS xg_home,
               ms.away_value AS xg_away,
               ht.name AS home_team_name,
               at.name AS away_team_name
        FROM match_stats ms
        JOIN events e ON e.id = ms.event_id
        JOIN teams ht ON ht.id = e.home_team_id
        JOIN teams at ON at.id = e.away_team_id
        WHERE ms.metric = 'xg'
          AND e.time_status = 3
    """
    try:
        xg_df = pd.read_sql(xg_query, conn)
        logger.info("Loaded %d xG records from Supabase", len(xg_df))
    except Exception as exc:
        logger.warning("Could not load xG data: %s", exc)
        xg_df = pd.DataFrame()

    conn.close()
    return df, xg_df


# ── Training ──────────────────────────────────────────────────────────────────

def train(
    df: pd.DataFrame,
    xg_df: pd.DataFrame = None,
    kaggle_features: dict[str, dict] | None = None,
) -> tuple[dict, dict]:
    """
    Fit the Poisson model and return (model_data, model_card).
    Enriches team_strengths with Kaggle FBref player indices when available.
    """
    df = df.copy()
    df["home_score"] = pd.to_numeric(df["home_score"], errors="coerce")
    df["away_score"] = pd.to_numeric(df["away_score"], errors="coerce")
    df = df.dropna(subset=["home_score", "away_score"])
    n = len(df)

    league_avg_home = df["home_score"].mean()
    league_avg_away = df["away_score"].mean()
    league_avg_total = (league_avg_home + league_avg_away) / 2

    logger.info(
        "Training on %d matches | avg goals home=%.3f away=%.3f",
        n, league_avg_home, league_avg_away,
    )

    home_stats = df.groupby("home_team_name").agg(
        home_goals_scored=("home_score", "sum"),
        home_goals_conceded=("away_score", "sum"),
        home_matches=("home_score", "count"),
    )
    away_stats = df.groupby("away_team_name").agg(
        away_goals_scored=("away_score", "sum"),
        away_goals_conceded=("home_score", "sum"),
        away_matches=("away_score", "count"),
    )

    all_teams = sorted(set(home_stats.index) | set(away_stats.index))
    team_strengths: dict[str, dict] = {}

    for team in all_teams:
        h = home_stats.loc[team] if team in home_stats.index else None
        a = away_stats.loc[team] if team in away_stats.index else None

        scored = conceded = matches = 0.0

        # Home-specific strengths
        if h is not None:
            h_scored = float(h["home_goals_scored"])
            h_conceded = float(h["home_goals_conceded"])
            h_matches = int(h["home_matches"])
            scored += h_scored
            conceded += h_conceded
            matches += h_matches
            attack_home = max((h_scored / h_matches) / league_avg_home, 0.1) if h_matches else 1.0
            defense_home = max((h_conceded / h_matches) / league_avg_away, 0.1) if h_matches else 1.0
        else:
            attack_home = 1.0
            defense_home = 1.0

        # Away-specific strengths
        if a is not None:
            a_scored = float(a["away_goals_scored"])
            a_conceded = float(a["away_goals_conceded"])
            a_matches = int(a["away_matches"])
            scored += a_scored
            conceded += a_conceded
            matches += a_matches
            attack_away = max((a_scored / a_matches) / league_avg_away, 0.1) if a_matches else 1.0
            defense_away = max((a_conceded / a_matches) / league_avg_home, 0.1) if a_matches else 1.0
        else:
            attack_away = 1.0
            defense_away = 1.0

        # Combined (backward-compatible)
        if matches == 0:
            attack, defense = 1.0, 1.0
        else:
            attack  = max((scored  / matches) / league_avg_total, 0.1)
            defense = max((conceded / matches) / league_avg_total, 0.1)

        team_strengths[team] = {
            "attack":  round(attack, 4),
            "defense": round(defense, 4),
            "attack_home": round(attack_home, 4),
            "attack_away": round(attack_away, 4),
            "defense_home": round(defense_home, 4),
            "defense_away": round(defense_away, 4),
        }

    # ── xG-based adjustment ──────────────────────────────────────────────
    if xg_df is not None and not xg_df.empty:
        xg_df = xg_df.copy()
        xg_df["xg_home"] = pd.to_numeric(xg_df["xg_home"], errors="coerce")
        xg_df["xg_away"] = pd.to_numeric(xg_df["xg_away"], errors="coerce")
        xg_df = xg_df.dropna(subset=["xg_home", "xg_away"])

        if not xg_df.empty:
            xg_avg_home = xg_df["xg_home"].mean()
            xg_avg_away = xg_df["xg_away"].mean()
            logger.info("xG data: %d matches, avg home=%.3f away=%.3f",
                        len(xg_df), xg_avg_home, xg_avg_away)

            for team in all_teams:
                home_xg = xg_df[xg_df["home_team_name"] == team]
                away_xg = xg_df[xg_df["away_team_name"] == team]
                n_xg = len(home_xg) + len(away_xg)

                if n_xg >= 10:
                    xg_atk_home = max(home_xg["xg_home"].mean() / xg_avg_home, 0.1) if not home_xg.empty else 1.0
                    xg_def_home = max(home_xg["xg_away"].mean() / xg_avg_away, 0.1) if not home_xg.empty else 1.0
                    xg_atk_away = max(away_xg["xg_away"].mean() / xg_avg_away, 0.1) if not away_xg.empty else 1.0
                    xg_def_away = max(away_xg["xg_home"].mean() / xg_avg_home, 0.1) if not away_xg.empty else 1.0

                    team_strengths[team].update({
                        "xg_attack_home": round(xg_atk_home, 4),
                        "xg_defense_home": round(xg_def_home, 4),
                        "xg_attack_away": round(xg_atk_away, 4),
                        "xg_defense_away": round(xg_def_away, 4),
                        "xg_matches": n_xg,
                    })

    # ── Kaggle enrichment ────────────────────────────────────────────────
    kaggle_enriched_count = 0
    if kaggle_features:
        enrich_team_strengths(team_strengths, kaggle_features)
        kaggle_enriched_count = sum(
            1 for s in team_strengths.values() if "attack_index" in s
        )

    model_data = {
        "team_strengths":         team_strengths,
        "league_avg_home_goals":  round(league_avg_home, 6),
        "league_avg_away_goals":  round(league_avg_away, 6),
        "n_matches":              n,
        "fitted":                 True,
        "trained_at":             datetime.now(timezone.utc).isoformat(),
        "kaggle_enriched":        kaggle_enriched_count > 0,
        "kaggle_enriched_teams":  kaggle_enriched_count,
    }

    date_min = str(df["time_utc"].min())[:10] if "time_utc" in df.columns else "unknown"
    date_max = str(df["time_utc"].max())[:10] if "time_utc" in df.columns else "unknown"

    model_card = {
        "model_name":     "Poisson Match Predictor",
        "version":        "2.1.0",
        "algorithm":      "Independent Poisson Goals (Dixon-Coles inspired) + FBref player strength enrichment",
        "training_source": "Supabase (live) + Kaggle FBref (offline enrichment)",
        "training_matches": n,
        "date_range":     {"from": date_min, "to": date_max},
        "league":         "England Premier League (BetsAPI ID: 94)",
        "league_averages": {
            "home_goals_per_match":  round(league_avg_home, 4),
            "away_goals_per_match":  round(league_avg_away, 4),
            "total_goals_per_match": round(league_avg_home + league_avg_away, 4),
        },
        "teams_trained": len(all_teams),
        "kaggle_enriched_teams": kaggle_enriched_count,
        "kaggle_features": ["attack_index", "creation_index", "defensive_index", "squad_depth"],
        "trained_at":    model_data["trained_at"],
    }

    logger.info("Model trained — %d teams", len(all_teams))
    return model_data, model_card


# ── Supabase snapshot materialisation ────────────────────────────────────────

STATSBOMB_MATCHES_CSV = Path(
    os.getenv(
        "STATSBOMB_MATCHES_CSV",
        str(_REPO_ROOT / "data" / "kaggle" / "statsbomb_premier_league_matches.csv"),
    )
)

SEASON_LABEL = os.getenv("SNAPSHOT_SEASON", "2025/2026")


def _ensure_snapshot_tables(conn) -> None:
    """Create the three snapshot tables if they don't exist yet."""
    ddl_path = _REPO_ROOT / "sql" / "feature_snapshots.sql"
    if ddl_path.exists():
        cur = conn.cursor()
        cur.execute(ddl_path.read_text())
        conn.commit()
        cur.close()
    else:
        logger.warning("feature_snapshots.sql not found at %s — tables may be missing", ddl_path)


def _build_statsbomb_style(csv_path: Path) -> dict[str, dict]:
    """Parse StatsBomb matches CSV, compute per-team style metrics."""
    if not csv_path.exists():
        logger.warning("StatsBomb CSV not found at %s — skipping style snapshot", csv_path)
        return {}

    import ast

    df = pd.read_csv(csv_path, low_memory=False)

    def _extract_home_name(cell) -> str:
        try:
            d = ast.literal_eval(str(cell))
            return normalize_team_name(d.get("home_team_name", ""))
        except Exception:
            return normalize_team_name(str(cell))

    def _extract_away_name(cell) -> str:
        try:
            d = ast.literal_eval(str(cell))
            return normalize_team_name(d.get("away_team_name", ""))
        except Exception:
            return normalize_team_name(str(cell))

    df["home_name"] = df["home_team"].apply(_extract_home_name)
    df["away_name"] = df["away_team"].apply(_extract_away_name)
    df["home_score"] = pd.to_numeric(df["home_score"], errors="coerce")
    df["away_score"] = pd.to_numeric(df["away_score"], errors="coerce")
    df = df.dropna(subset=["home_score", "away_score", "home_name", "away_name"])
    df = df[(df["home_name"] != "") & (df["away_name"] != "")]

    team_stats: dict[str, dict] = {}
    all_teams = sorted(set(df["home_name"]) | set(df["away_name"]))

    for team in all_teams:
        home_rows = df[df["home_name"] == team]
        away_rows = df[df["away_name"] == team]

        goals_scored   = list(home_rows["home_score"]) + list(away_rows["away_score"])
        goals_conceded = list(home_rows["away_score"]) + list(away_rows["home_score"])
        total = len(goals_scored)
        if total == 0:
            continue

        avg_scored    = sum(goals_scored)   / total
        avg_conceded  = sum(goals_conceded) / total
        cs_rate       = sum(1 for g in goals_conceded if g == 0) / total
        btts_rate_val = sum(1 for s, c in zip(goals_scored, goals_conceded) if s > 0 and c > 0) / total

        team_stats[team] = {
            "matches_count":      total,
            "avg_goals_scored":   round(avg_scored,    4),
            "avg_goals_conceded": round(avg_conceded,  4),
            "clean_sheet_rate":   round(cs_rate,       4),
            "btts_rate":          round(btts_rate_val, 4),
            "avg_goal_diff":      round(avg_scored - avg_conceded, 4),
        }

    logger.info("StatsBomb style snapshot: %d teams", len(team_stats))
    return team_stats


def _build_player_absence_impact(csv_path: Path) -> list[dict]:
    """Build top-10 key players per PL team with an impact score (0–10)."""
    if not csv_path.exists():
        return []

    df = pd.read_csv(csv_path, low_memory=False)
    df = df[df["Comp"].astype(str).str.contains("Premier League", na=False)].copy()

    for col in ["90s", "Gls", "Ast"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        else:
            df[col] = 0.0

    df = df[df["90s"] >= 3.0].copy()
    df["canonical_squad"] = df["Squad"].apply(normalize_team_name)

    rows: list[dict] = []
    today = datetime.now(timezone.utc).date().isoformat()

    for team, grp in df.groupby("canonical_squad"):
        grp = grp.copy()
        grp["raw_impact"] = grp["Gls"] + grp["Ast"] + (grp["90s"] * 0.1)
        max_impact = grp["raw_impact"].max()
        if max_impact <= 0:
            continue
        grp["impact_score"] = (grp["raw_impact"] / max_impact * 10).round(2)

        for _, row in grp.nlargest(10, "impact_score").iterrows():
            rows.append({
                "team_name":     team,
                "player_name":   str(row.get("Player", "")),
                "position":      str(row.get("Pos", "")),
                "minutes_90s":   round(float(row["90s"]), 2),
                "goals":         int(row["Gls"]),
                "assists":       int(row["Ast"]),
                "impact_score":  float(row["impact_score"]),
                "snapshot_date": today,
            })

    logger.info("Player absence impact rows: %d", len(rows))
    return rows


def materialize_snapshots(
    conn,
    kaggle_features: dict[str, dict],
    season: str = SEASON_LABEL,
) -> None:
    """
    Upsert the three feature snapshot tables into Supabase after retraining.
    Creates tables if needed (via feature_snapshots.sql DDL).
    """
    _ensure_snapshot_tables(conn)
    today = datetime.now(timezone.utc).date().isoformat()
    cur = conn.cursor()

    # ── 1. team_player_strength_snapshot ─────────────────────────────────
    if kaggle_features:
        upsert_sql = """
            INSERT INTO team_player_strength_snapshot
                (team_name, season, attack_index, creation_index,
                 defensive_index, squad_depth, snapshot_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (team_name, season) DO UPDATE SET
                attack_index    = EXCLUDED.attack_index,
                creation_index  = EXCLUDED.creation_index,
                defensive_index = EXCLUDED.defensive_index,
                squad_depth     = EXCLUDED.squad_depth,
                snapshot_date   = EXCLUDED.snapshot_date,
                created_at      = NOW()
        """
        for team, feats in kaggle_features.items():
            cur.execute(upsert_sql, (
                team, season,
                feats["attack_index"],
                feats["creation_index"],
                feats["defensive_index"],
                feats["squad_depth"],
                today,
            ))
        logger.info("Upserted %d rows into team_player_strength_snapshot", len(kaggle_features))

    # ── 2. team_style_snapshot_statsbomb ─────────────────────────────────
    style_data = _build_statsbomb_style(STATSBOMB_MATCHES_CSV)
    if style_data:
        upsert_style = """
            INSERT INTO team_style_snapshot_statsbomb
                (team_name, season, matches_count,
                 avg_goals_scored, avg_goals_conceded,
                 clean_sheet_rate, btts_rate, avg_goal_diff, snapshot_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (team_name, season) DO UPDATE SET
                matches_count       = EXCLUDED.matches_count,
                avg_goals_scored    = EXCLUDED.avg_goals_scored,
                avg_goals_conceded  = EXCLUDED.avg_goals_conceded,
                clean_sheet_rate    = EXCLUDED.clean_sheet_rate,
                btts_rate           = EXCLUDED.btts_rate,
                avg_goal_diff       = EXCLUDED.avg_goal_diff,
                snapshot_date       = EXCLUDED.snapshot_date,
                created_at          = NOW()
        """
        for team, s in style_data.items():
            cur.execute(upsert_style, (
                team, season,
                s["matches_count"],
                s["avg_goals_scored"],
                s["avg_goals_conceded"],
                s["clean_sheet_rate"],
                s["btts_rate"],
                s["avg_goal_diff"],
                today,
            ))
        logger.info("Upserted %d rows into team_style_snapshot_statsbomb", len(style_data))

    # ── 3. player_absence_impact ─────────────────────────────────────────
    absence_rows = _build_player_absence_impact(KAGGLE_PLAYERS_CSV)
    if absence_rows:
        upsert_absence = """
            INSERT INTO player_absence_impact
                (team_name, player_name, position, minutes_90s,
                 goals, assists, impact_score, snapshot_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (team_name, player_name, snapshot_date) DO UPDATE SET
                position     = EXCLUDED.position,
                minutes_90s  = EXCLUDED.minutes_90s,
                goals        = EXCLUDED.goals,
                assists      = EXCLUDED.assists,
                impact_score = EXCLUDED.impact_score,
                created_at   = NOW()
        """
        psycopg2.extras.execute_batch(cur, upsert_absence, [
            (r["team_name"], r["player_name"], r["position"],
             r["minutes_90s"], r["goals"], r["assists"],
             r["impact_score"], r["snapshot_date"])
            for r in absence_rows
        ])
        logger.info("Upserted %d player absence rows", len(absence_rows))

    conn.commit()
    cur.close()
    logger.info("Snapshot materialisation complete")


# ── IBM COS upload ────────────────────────────────────────────────────────────

def upload_to_cos(model_data: dict, model_card: dict) -> None:
    import ibm_boto3

    access_key = os.environ["IBM_COS_ACCESS_KEY_ID"]
    secret_key = os.environ["IBM_COS_SECRET_ACCESS_KEY"]
    endpoint   = os.getenv("IBM_COS_ENDPOINT", "https://s3.us-south.cloud-object-storage.appdomain.cloud")

    cos = ibm_boto3.client(
        "s3",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        endpoint_url=endpoint,
    )

    # Upload model.pkl
    buffer = io.BytesIO()
    joblib.dump(model_data, buffer, compress=3)
    cos.put_object(Bucket=BUCKET, Key=BLOB_NAME, Body=buffer.getvalue())
    logger.info("Uploaded %s to IBM COS bucket '%s'", BLOB_NAME, BUCKET)

    # Upload model_card.json
    card_bytes = json.dumps(model_card, indent=2, ensure_ascii=False).encode()
    cos.put_object(Bucket=BUCKET, Key=CARD_BLOB, Body=card_bytes)
    logger.info("Uploaded %s", CARD_BLOB)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    t0 = time.monotonic()
    logger.info("=== Goat Tips Retraining Job started ===")

    df, xg_df = load_training_data()
    if len(df) < 100:
        logger.error("Too few matches (%d) to train reliably. Aborting.", len(df))
        sys.exit(1)

    kaggle_features = load_kaggle_player_features()
    model_data, model_card = train(df, xg_df, kaggle_features)
    upload_to_cos(model_data, model_card)

    # Materialise feature snapshots to Supabase so the /ask agent can query them
    try:
        db_url = os.environ["SUPABASE_DB_URL"]
        conn = psycopg2.connect(db_url)
        materialize_snapshots(conn, kaggle_features)
        conn.close()
    except Exception as exc:
        logger.warning("Snapshot materialisation failed (non-fatal): %s", exc)

    elapsed = time.monotonic() - t0
    logger.info("=== Retraining complete in %.1fs ===", elapsed)


if __name__ == "__main__":
    main()
