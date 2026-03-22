-- Conversation History — run once on Supabase SQL Editor
-- Stores per-user, per-match Q&A history for the /predictions/{event_id}/ask endpoint.

CREATE TABLE IF NOT EXISTS conversation_sessions (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  TEXT        NOT NULL,
    event_id    TEXT        NOT NULL,
    messages    JSONB       NOT NULL DEFAULT '[]'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_session_event UNIQUE (session_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_conv_session_id
    ON conversation_sessions (session_id);

-- Optional: auto-expire sessions older than 7 days via pg_cron
-- SELECT cron.schedule('purge-old-sessions', '0 3 * * *',
--   $$DELETE FROM conversation_sessions WHERE updated_at < NOW() - INTERVAL '7 days'$$);
