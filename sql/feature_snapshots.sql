-- ============================================================
-- Goat Tips — Feature Snapshot Tables
-- Materialised weekly by retrain/retrain.py
-- ============================================================

-- ── team_player_strength_snapshot ────────────────────────────
-- Kaggle FBref-derived per-team player strength indices.
-- Refreshed each retrain run (weekly cron).
CREATE TABLE IF NOT EXISTS team_player_strength_snapshot (
    id              BIGSERIAL   PRIMARY KEY,
    team_name       TEXT        NOT NULL,
    season          TEXT        NOT NULL DEFAULT '2025/2026',
    attack_index    FLOAT       NOT NULL,   -- weighted Gls + xG per 90
    creation_index  FLOAT       NOT NULL,   -- weighted Ast + xAG + KP + PrgP
    defensive_index FLOAT       NOT NULL,   -- weighted TklW + Int (or Tkl+Int + Blocks + Clr)
    squad_depth     INT         NOT NULL,   -- players with ≥5 90s played
    snapshot_date   DATE        NOT NULL DEFAULT CURRENT_DATE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (team_name, season)
);

CREATE INDEX IF NOT EXISTS idx_tpss_team ON team_player_strength_snapshot (team_name);

-- ── team_style_snapshot_statsbomb ────────────────────────────
-- StatsBomb-derived per-team tactical style metrics.
-- Refreshed each retrain run (weekly cron).
CREATE TABLE IF NOT EXISTS team_style_snapshot_statsbomb (
    id                  BIGSERIAL   PRIMARY KEY,
    team_name           TEXT        NOT NULL,
    season              TEXT        NOT NULL,
    matches_count       INT         NOT NULL,
    avg_goals_scored    FLOAT,
    avg_goals_conceded  FLOAT,
    clean_sheet_rate    FLOAT,      -- fraction of matches with 0 goals conceded
    btts_rate           FLOAT,      -- fraction of matches both teams scored
    avg_goal_diff       FLOAT,
    snapshot_date       DATE        NOT NULL DEFAULT CURRENT_DATE,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (team_name, season)
);

CREATE INDEX IF NOT EXISTS idx_tsss_team ON team_style_snapshot_statsbomb (team_name);

-- ── player_absence_impact ─────────────────────────────────────
-- Top players per team with a computed impact score.
-- Refreshed each retrain run (weekly cron).
CREATE TABLE IF NOT EXISTS player_absence_impact (
    id              BIGSERIAL   PRIMARY KEY,
    team_name       TEXT        NOT NULL,
    player_name     TEXT        NOT NULL,
    position        TEXT,
    minutes_90s     FLOAT,          -- 90-minute periods played
    goals           INT,
    assists         INT,
    impact_score    FLOAT,          -- normalised 0-10 importance to team
    snapshot_date   DATE        NOT NULL DEFAULT CURRENT_DATE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (team_name, player_name, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_pai_team ON player_absence_impact (team_name);
