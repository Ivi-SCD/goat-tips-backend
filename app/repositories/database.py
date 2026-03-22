"""
Database Repository
===================
Async read/write operations against Supabase (PostgreSQL via asyncpg).

Responsibility:  Execute queries, map rows to domain dicts.
Does NOT:        Contain business logic (that's in services/).
Depends on:      app.db.connection (pool management)

Design notes
------------
- All functions are async, returning plain dicts or lists of dicts.
- Use upsert (INSERT ... ON CONFLICT DO UPDATE) everywhere so re-runs
  of the daily sync are idempotent.
- The historical.py repository (CSV) stays as a fast local fallback;
  database.py is the persistent, queryable source of truth.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from app.db.connection import acquire

logger = logging.getLogger(__name__)


# ── Teams ─────────────────────────────────────────────────────────────────────

async def upsert_team(team_id: int, name: str, image_id: str = None, cc: str = None) -> None:
    async with acquire() as conn:
        await conn.execute("""
            INSERT INTO teams (id, name, image_id, cc, created_at, updated_at)
            VALUES ($1, $2, $3, $4, NOW(), NOW())
            ON CONFLICT (id) DO UPDATE
                SET name       = EXCLUDED.name,
                    image_id   = EXCLUDED.image_id,
                    cc         = EXCLUDED.cc,
                    updated_at = NOW()
        """, team_id, name, image_id, cc)


async def upsert_teams_bulk(teams: list[dict]) -> int:
    """Bulk upsert teams. Each dict: {id, name, image_id?, cc?}"""
    async with acquire() as conn:
        await conn.executemany("""
            INSERT INTO teams (id, name, image_id, cc, created_at, updated_at)
            VALUES ($1, $2, $3, $4, NOW(), NOW())
            ON CONFLICT (id) DO UPDATE
                SET name = EXCLUDED.name, updated_at = NOW()
        """, [(t["id"], t["name"], t.get("image_id"), t.get("cc")) for t in teams])
    return len(teams)


async def get_all_teams() -> list[dict]:
    async with acquire() as conn:
        rows = await conn.fetch("SELECT id, name, image_id FROM teams ORDER BY name")
        return [dict(r) for r in rows]


# ── Events ────────────────────────────────────────────────────────────────────

async def upsert_event(event: dict) -> None:
    """
    Upsert a single event row. Keys must match the events table columns.
    """
    async with acquire() as conn:
        await conn.execute("""
            INSERT INTO events (
                id, time_unix, time_utc, time_status, league_id, league_name,
                home_team_id, away_team_id, home_score, away_score, score_string,
                round, home_position, away_position, stadium_name, stadium_city,
                referee_id, referee_name, bet365_id, created_at, updated_at
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,NOW(),NOW()
            )
            ON CONFLICT (id) DO UPDATE SET
                time_status   = EXCLUDED.time_status,
                home_score    = EXCLUDED.home_score,
                away_score    = EXCLUDED.away_score,
                score_string  = EXCLUDED.score_string,
                home_position = EXCLUDED.home_position,
                away_position = EXCLUDED.away_position,
                updated_at    = NOW()
        """,
        event["id"], event.get("time_unix"), event.get("time_utc"),
        event.get("time_status"), event.get("league_id", 94), event.get("league_name"),
        event.get("home_team_id"), event.get("away_team_id"),
        event.get("home_score"), event.get("away_score"), event.get("score_string"),
        event.get("round"), event.get("home_position"), event.get("away_position"),
        event.get("stadium_name"), event.get("stadium_city"),
        event.get("referee_id"), event.get("referee_name"), event.get("bet365_id"),
        )


async def upsert_events_bulk(events: list[dict]) -> int:
    async with acquire() as conn:
        async with conn.transaction():
            for event in events:
                await conn.execute("""
                    INSERT INTO events (
                        id, time_unix, time_utc, time_status, league_id, league_name,
                        home_team_id, away_team_id, home_score, away_score, score_string,
                        round, home_position, away_position, stadium_name, stadium_city,
                        referee_id, referee_name, bet365_id, created_at, updated_at
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,NOW(),NOW())
                    ON CONFLICT (id) DO UPDATE SET
                        time_status   = EXCLUDED.time_status,
                        home_score    = EXCLUDED.home_score,
                        away_score    = EXCLUDED.away_score,
                        score_string  = EXCLUDED.score_string,
                        updated_at    = NOW()
                """,
                event["id"], event.get("time_unix"), event.get("time_utc"),
                event.get("time_status"), event.get("league_id", 94), event.get("league_name"),
                event.get("home_team_id"), event.get("away_team_id"),
                event.get("home_score"), event.get("away_score"), event.get("score_string"),
                event.get("round"), event.get("home_position"), event.get("away_position"),
                event.get("stadium_name"), event.get("stadium_city"),
                event.get("referee_id"), event.get("referee_name"), event.get("bet365_id"),
                )
    return len(events)


async def get_event_ids() -> set[int]:
    """Returns the set of event IDs already in the database."""
    async with acquire() as conn:
        rows = await conn.fetch("SELECT id FROM events")
        return {r["id"] for r in rows}


async def get_events_for_team(team_name: str, n: int = 10) -> list[dict]:
    async with acquire() as conn:
        rows = await conn.fetch("""
            SELECT e.id, e.time_utc, e.home_score, e.away_score,
                   ht.name AS home_team, at.name AS away_team,
                   e.score_string, e.time_status
            FROM events e
            JOIN teams ht ON ht.id = e.home_team_id
            JOIN teams at ON at.id = e.away_team_id
            WHERE (lower(ht.name) = lower($1) OR lower(at.name) = lower($1))
              AND e.time_status = 3
            ORDER BY e.time_unix DESC
            LIMIT $2
        """, team_name, n)
        return [dict(r) for r in rows]


async def get_h2h_events(home_team: str, away_team: str, n: int = 10) -> list[dict]:
    async with acquire() as conn:
        rows = await conn.fetch("""
            SELECT e.id, e.time_utc, e.home_score, e.away_score,
                   ht.name AS home_team, at.name AS away_team
            FROM events e
            JOIN teams ht ON ht.id = e.home_team_id
            JOIN teams at ON at.id = e.away_team_id
            WHERE (lower(ht.name) = lower($1) AND lower(at.name) = lower($2))
               OR (lower(ht.name) = lower($2) AND lower(at.name) = lower($1))
              AND e.time_status = 3
            ORDER BY e.time_unix DESC
            LIMIT $3
        """, home_team, away_team, n)
        return [dict(r) for r in rows]


# ── Match stats ───────────────────────────────────────────────────────────────

async def upsert_stats_bulk(stats: list[dict]) -> int:
    """
    Each dict: {event_id, metric, home_value, away_value, period?}
    """
    async with acquire() as conn:
        async with conn.transaction():
            await conn.executemany("""
                INSERT INTO match_stats (event_id, metric, home_value, away_value, period, created_at)
                VALUES ($1, $2, $3, $4, $5, NOW())
                ON CONFLICT (event_id, metric, period) DO UPDATE SET
                    home_value = EXCLUDED.home_value,
                    away_value = EXCLUDED.away_value
            """, [
                (s["event_id"], s["metric"],
                 s.get("home_value"), s.get("away_value"),
                 s.get("period", "full"))
                for s in stats
            ])
    return len(stats)


# ── Timeline ──────────────────────────────────────────────────────────────────

async def upsert_timeline_bulk(rows: list[dict]) -> int:
    """
    Each dict: {event_id, timeline_id, text}
    """
    async with acquire() as conn:
        async with conn.transaction():
            await conn.executemany("""
                INSERT INTO match_timeline (event_id, timeline_id, text, created_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (event_id, timeline_id) DO NOTHING
            """, [(r["event_id"], r["timeline_id"], r["text"]) for r in rows])
    return len(rows)


# ── Odds ──────────────────────────────────────────────────────────────────────

async def upsert_odds(event_id: int, market_key: str, odds: dict) -> None:
    async with acquire() as conn:
        await conn.execute("""
            INSERT INTO odds_snapshots (
                event_id, market_key, home_od, draw_od, away_od,
                over_od, under_od, yes_od, no_od, recorded_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9, NOW())
            ON CONFLICT (event_id, market_key) DO UPDATE SET
                home_od     = EXCLUDED.home_od,
                draw_od     = EXCLUDED.draw_od,
                away_od     = EXCLUDED.away_od,
                over_od     = EXCLUDED.over_od,
                recorded_at = NOW()
        """,
        event_id, market_key,
        odds.get("home_od"), odds.get("draw_od"), odds.get("away_od"),
        odds.get("over_od"), odds.get("under_od"),
        odds.get("yes_od"), odds.get("no_od"),
        )


async def get_odds_for_event(event_id: int) -> list[dict]:
    async with acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM odds_snapshots WHERE event_id = $1", event_id
        )
        return [dict(r) for r in rows]


# ── Sync log ──────────────────────────────────────────────────────────────────

async def log_sync(
    trigger: str,
    events_fetched: int = 0,
    events_upserted: int = 0,
    errors: int = 0,
    duration_ms: int = 0,
    notes: str = None,
) -> None:
    async with acquire() as conn:
        await conn.execute("""
            INSERT INTO sync_log (run_at, trigger, events_fetched, events_upserted,
                                  errors, duration_ms, notes)
            VALUES (NOW(), $1, $2, $3, $4, $5, $6)
        """, trigger, events_fetched, events_upserted, errors, duration_ms, notes)


async def get_last_sync() -> Optional[dict]:
    async with acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM sync_log ORDER BY run_at DESC LIMIT 1"
        )
        return dict(row) if row else None
