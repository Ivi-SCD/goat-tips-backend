"""
Database Models
===============
SQLAlchemy ORM models mirroring the Supabase schema.

These are the "domain persistence" models — separate from Pydantic schemas
(which are the API contract). Convert between them in the repository layer.

Usage:
    from app.db.models import Event, Team, MatchStat, MatchTimeline, OddsSnapshot
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, Column, ForeignKey, Index, Integer,
    Numeric, SmallInteger, Text, TIMESTAMP, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Team(Base):
    __tablename__ = "teams"

    id:         Mapped[int]           = mapped_column(BigInteger, primary_key=True)
    name:       Mapped[str]           = mapped_column(Text, nullable=False)
    image_id:   Mapped[Optional[str]] = mapped_column(Text)
    cc:         Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime]      = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[datetime]      = mapped_column(TIMESTAMP(timezone=True))

    home_events: Mapped[list["Event"]] = relationship(
        "Event", foreign_keys="Event.home_team_id", back_populates="home_team"
    )
    away_events: Mapped[list["Event"]] = relationship(
        "Event", foreign_keys="Event.away_team_id", back_populates="away_team"
    )

    def __repr__(self) -> str:
        return f"<Team id={self.id} name={self.name!r}>"


class Event(Base):
    __tablename__ = "events"

    id:           Mapped[int]           = mapped_column(BigInteger, primary_key=True)
    time_unix:    Mapped[Optional[int]] = mapped_column(BigInteger)
    time_utc:     Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    time_status:  Mapped[Optional[int]] = mapped_column(SmallInteger)
    league_id:    Mapped[int]           = mapped_column(Integer, default=94)
    league_name:  Mapped[Optional[str]] = mapped_column(Text)
    home_team_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("teams.id"))
    away_team_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("teams.id"))
    home_score:   Mapped[Optional[int]] = mapped_column(SmallInteger)
    away_score:   Mapped[Optional[int]] = mapped_column(SmallInteger)
    score_string: Mapped[Optional[str]] = mapped_column(Text)
    round:        Mapped[Optional[str]] = mapped_column(Text)
    home_position: Mapped[Optional[int]] = mapped_column(SmallInteger)
    away_position: Mapped[Optional[int]] = mapped_column(SmallInteger)
    stadium_name: Mapped[Optional[str]] = mapped_column(Text)
    stadium_city: Mapped[Optional[str]] = mapped_column(Text)
    referee_id:   Mapped[Optional[int]] = mapped_column(BigInteger)
    referee_name: Mapped[Optional[str]] = mapped_column(Text)
    bet365_id:    Mapped[Optional[str]] = mapped_column(Text)
    created_at:   Mapped[datetime]      = mapped_column(TIMESTAMP(timezone=True))
    updated_at:   Mapped[datetime]      = mapped_column(TIMESTAMP(timezone=True))

    home_team:  Mapped[Optional[Team]] = relationship(
        "Team", foreign_keys=[home_team_id], back_populates="home_events"
    )
    away_team:  Mapped[Optional[Team]] = relationship(
        "Team", foreign_keys=[away_team_id], back_populates="away_events"
    )
    stats:      Mapped[list["MatchStat"]]     = relationship("MatchStat", back_populates="event", cascade="all, delete-orphan")
    timeline:   Mapped[list["MatchTimeline"]] = relationship("MatchTimeline", back_populates="event", cascade="all, delete-orphan")
    odds:       Mapped[list["OddsSnapshot"]]  = relationship("OddsSnapshot", back_populates="event", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Event id={self.id} {self.home_team_id} vs {self.away_team_id}>"


class MatchStat(Base):
    __tablename__ = "match_stats"
    __table_args__ = (
        UniqueConstraint("event_id", "metric", "period", name="uq_match_stats"),
    )

    id:         Mapped[int]           = mapped_column(BigInteger, primary_key=True)
    event_id:   Mapped[int]           = mapped_column(BigInteger, ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    metric:     Mapped[str]           = mapped_column(Text, nullable=False)
    home_value: Mapped[Optional[float]] = mapped_column(Numeric)
    away_value: Mapped[Optional[float]] = mapped_column(Numeric)
    period:     Mapped[str]           = mapped_column(Text, default="full")
    created_at: Mapped[datetime]      = mapped_column(TIMESTAMP(timezone=True))

    event: Mapped[Event] = relationship("Event", back_populates="stats")

    def __repr__(self) -> str:
        return f"<MatchStat event={self.event_id} metric={self.metric!r} {self.home_value}-{self.away_value}>"


class MatchTimeline(Base):
    __tablename__ = "match_timeline"
    __table_args__ = (
        UniqueConstraint("event_id", "timeline_id", name="uq_timeline"),
    )

    id:          Mapped[int]           = mapped_column(BigInteger, primary_key=True)
    event_id:    Mapped[int]           = mapped_column(BigInteger, ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    timeline_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    text:        Mapped[Optional[str]] = mapped_column(Text)
    created_at:  Mapped[datetime]      = mapped_column(TIMESTAMP(timezone=True))

    event: Mapped[Event] = relationship("Event", back_populates="timeline")


class OddsSnapshot(Base):
    __tablename__ = "odds_snapshots"
    __table_args__ = (
        UniqueConstraint("event_id", "market_key", name="uq_odds_snapshot"),
    )

    id:          Mapped[int]           = mapped_column(BigInteger, primary_key=True)
    event_id:    Mapped[int]           = mapped_column(BigInteger, ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    market_key:  Mapped[str]           = mapped_column(Text, nullable=False)
    home_od:     Mapped[Optional[float]] = mapped_column(Numeric)
    draw_od:     Mapped[Optional[float]] = mapped_column(Numeric)
    away_od:     Mapped[Optional[float]] = mapped_column(Numeric)
    over_od:     Mapped[Optional[float]] = mapped_column(Numeric)
    under_od:    Mapped[Optional[float]] = mapped_column(Numeric)
    yes_od:      Mapped[Optional[float]] = mapped_column(Numeric)
    no_od:       Mapped[Optional[float]] = mapped_column(Numeric)
    recorded_at: Mapped[datetime]      = mapped_column(TIMESTAMP(timezone=True))

    event: Mapped[Event] = relationship("Event", back_populates="odds")


class SyncLog(Base):
    __tablename__ = "sync_log"

    id:              Mapped[int]           = mapped_column(BigInteger, primary_key=True)
    run_at:          Mapped[datetime]      = mapped_column(TIMESTAMP(timezone=True))
    trigger:         Mapped[Optional[str]] = mapped_column(Text)
    events_fetched:  Mapped[int]           = mapped_column(Integer, default=0)
    events_upserted: Mapped[int]           = mapped_column(Integer, default=0)
    errors:          Mapped[int]           = mapped_column(Integer, default=0)
    duration_ms:     Mapped[Optional[int]] = mapped_column(Integer)
    notes:           Mapped[Optional[str]] = mapped_column(Text)
