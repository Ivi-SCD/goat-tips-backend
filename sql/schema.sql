-- ============================================================
-- Goat Tips Premier League — Supabase Schema
-- Run this once to initialise your database.
-- ============================================================

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── teams ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS teams (
    id          BIGINT      PRIMARY KEY,
    name        TEXT        NOT NULL,
    image_id    TEXT,
    cc          TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_teams_name ON teams (lower(name));

-- ── events (matches) ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS events (
    id              BIGINT      PRIMARY KEY,
    time_unix       BIGINT,
    time_utc        TIMESTAMPTZ,
    time_status     SMALLINT,           -- 0=upcoming 1=live 3=ended
    league_id       INT         DEFAULT 94,
    league_name     TEXT,
    home_team_id    BIGINT      REFERENCES teams(id) ON DELETE SET NULL,
    away_team_id    BIGINT      REFERENCES teams(id) ON DELETE SET NULL,
    home_score      SMALLINT,
    away_score      SMALLINT,
    score_string    TEXT,
    round           TEXT,
    home_position   SMALLINT,
    away_position   SMALLINT,
    stadium_name    TEXT,
    stadium_city    TEXT,
    referee_id      BIGINT,
    referee_name    TEXT,
    bet365_id       TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_time_utc    ON events (time_utc DESC);
CREATE INDEX IF NOT EXISTS idx_events_time_status ON events (time_status);
CREATE INDEX IF NOT EXISTS idx_events_home_team   ON events (home_team_id);
CREATE INDEX IF NOT EXISTS idx_events_away_team   ON events (away_team_id);
CREATE INDEX IF NOT EXISTS idx_events_league      ON events (league_id);

-- ── match_stats ───────────────────────────────────────────────────────────────
-- One row per (event, metric, period).
-- Metrics: goals, shots, shots_on_target, corners, possession_rt,
--          dangerous_attacks, attacks, yellow_cards, red_cards, offsides, fouls
CREATE TABLE IF NOT EXISTS match_stats (
    id          BIGSERIAL   PRIMARY KEY,
    event_id    BIGINT      NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    metric      TEXT        NOT NULL,
    home_value  NUMERIC,
    away_value  NUMERIC,
    period      TEXT        DEFAULT 'full',  -- 'full' | '1st_half' | '2nd_half'
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_match_stats_unique
    ON match_stats (event_id, metric, period);

CREATE INDEX IF NOT EXISTS idx_match_stats_event ON match_stats (event_id);

-- ── match_timeline ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS match_timeline (
    id              BIGSERIAL   PRIMARY KEY,
    event_id        BIGINT      NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    timeline_id     BIGINT,
    text            TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_timeline_unique
    ON match_timeline (event_id, timeline_id);

CREATE INDEX IF NOT EXISTS idx_timeline_event ON match_timeline (event_id);

-- ── odds_snapshots ────────────────────────────────────────────────────────────
-- Stores the LATEST odds snapshot per event (not full timeseries — 4.5M rows
-- of timeseries stays local; only latest is synced to Supabase).
CREATE TABLE IF NOT EXISTS odds_snapshots (
    id          BIGSERIAL   PRIMARY KEY,
    event_id    BIGINT      NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    market_key  TEXT        NOT NULL,   -- "1_1" (1X2), "1_2" (O/U), "1_3" (BTTS)
    home_od     NUMERIC,
    draw_od     NUMERIC,
    away_od     NUMERIC,
    over_od     NUMERIC,
    under_od    NUMERIC,
    yes_od      NUMERIC,
    no_od       NUMERIC,
    recorded_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_odds_unique
    ON odds_snapshots (event_id, market_key);

CREATE INDEX IF NOT EXISTS idx_odds_event ON odds_snapshots (event_id);

-- ── sync_log ──────────────────────────────────────────────────────────────────
-- Tracks each Azure Function run for observability.
CREATE TABLE IF NOT EXISTS sync_log (
    id              BIGSERIAL   PRIMARY KEY,
    run_at          TIMESTAMPTZ DEFAULT NOW(),
    trigger         TEXT,                   -- 'daily_timer' | 'manual' | 'http'
    events_fetched  INT         DEFAULT 0,
    events_upserted INT         DEFAULT 0,
    errors          INT         DEFAULT 0,
    duration_ms     INT,
    notes           TEXT
);

-- ── Helper: auto-update updated_at ───────────────────────────────────────────
CREATE OR REPLACE FUNCTION trigger_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER set_updated_at_events
    BEFORE UPDATE ON events
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE OR REPLACE TRIGGER set_updated_at_teams
    BEFORE UPDATE ON teams
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

-- ── Views ─────────────────────────────────────────────────────────────────────

-- Convenience view: full match row with team names
CREATE OR REPLACE VIEW v_matches AS
SELECT
    e.id              AS event_id,
    e.time_utc,
    e.time_status,
    e.round,
    ht.name           AS home_team,
    at.name           AS away_team,
    ht.image_id       AS home_image_id,
    at.image_id       AS away_image_id,
    e.home_score,
    e.away_score,
    e.score_string,
    e.referee_name,
    e.stadium_name,
    e.stadium_city,
    e.bet365_id
FROM events e
LEFT JOIN teams ht ON ht.id = e.home_team_id
LEFT JOIN teams at ON at.id = e.away_team_id;

-- Goal timing view for quick pattern queries
-- Uses chr(39) for the apostrophe and filters to rows that actually start with a minute
-- (e.g. "45' - Goal - Player") to avoid CAST errors on summary rows like "0-0 Goals 00:00-09:59"
CREATE OR REPLACE VIEW v_goal_timeline AS
SELECT
    t.event_id,
    t.text,
    CAST(SPLIT_PART(t.text, chr(39), 1) AS INT) AS minute
FROM match_timeline t
WHERE t.text ILIKE '%goal%'
  AND t.text NOT ILIKE '%miss%'
  AND t.text NOT ILIKE '%no goal%'
  AND t.text ~ E'^[0-9]+\\'';
