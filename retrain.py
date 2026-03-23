#!/usr/bin/env python3
"""
Goat Tips — Weekly Poisson Model Retraining
========================================
Pulls finished match data from Supabase, trains the Poisson model,
and uploads the serialized artifact to IBM Cloud Object Storage.

Designed to run as an IBM Code Engine Job on a weekly cron schedule.

Environment variables required:
    SUPABASE_DB_URL               — Supabase PostgreSQL connection string
    IBM_COS_ACCESS_KEY_ID         — IBM COS HMAC access key
    IBM_COS_SECRET_ACCESS_KEY     — IBM COS HMAC secret key
    IBM_COS_ENDPOINT              — COS endpoint (default: us-south regional)
    IBM_COS_BUCKET                — COS bucket name (default: goat-tips-bucket)
    MODEL_BLOB_NAME               — Object key (default: poisson_model.pkl)
"""

import io
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

import joblib
import pandas as pd
import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BUCKET    = os.getenv("IBM_COS_BUCKET", "goat-tips-bucket")
BLOB_NAME = os.getenv("MODEL_BLOB_NAME", "poisson_model.pkl")
CARD_BLOB = os.getenv("MODEL_CARD_BLOB_NAME", "model_card.json")


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

def train(df: pd.DataFrame, xg_df: pd.DataFrame = None) -> tuple[dict, dict]:
    """
    Fit the Poisson model and return (model_data, model_card).
    Identical algorithm to scripts/train_model.py — single source of truth.
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

    model_data = {
        "team_strengths":         team_strengths,
        "league_avg_home_goals":  round(league_avg_home, 6),
        "league_avg_away_goals":  round(league_avg_away, 6),
        "n_matches":              n,
        "fitted":                 True,
        "trained_at":             datetime.now(timezone.utc).isoformat(),
    }

    date_min = str(df["time_utc"].min())[:10] if "time_utc" in df.columns else "unknown"
    date_max = str(df["time_utc"].max())[:10] if "time_utc" in df.columns else "unknown"

    model_card = {
        "model_name":     "Poisson Match Predictor",
        "version":        "2.0.0",
        "algorithm":      "Independent Poisson Goals (Dixon-Coles inspired)",
        "training_source": "Supabase (live)",
        "training_matches": n,
        "date_range":     {"from": date_min, "to": date_max},
        "league":         "England Premier League (BetsAPI ID: 94)",
        "league_averages": {
            "home_goals_per_match":  round(league_avg_home, 4),
            "away_goals_per_match":  round(league_avg_away, 4),
            "total_goals_per_match": round(league_avg_home + league_avg_away, 4),
        },
        "teams_trained": len(all_teams),
        "trained_at":    model_data["trained_at"],
    }

    logger.info("Model trained — %d teams", len(all_teams))
    return model_data, model_card


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

    model_data, model_card = train(df, xg_df)
    upload_to_cos(model_data, model_card)

    elapsed = time.monotonic() - t0
    logger.info("=== Retraining complete in %.1fs ===", elapsed)


if __name__ == "__main__":
    main()
