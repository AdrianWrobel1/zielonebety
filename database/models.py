"""
SQLAlchemy ORM Canonical Database Schema Models
"""

from typing import List, Optional
from datetime import datetime, timezone
from sqlalchemy import (
    String,
    Float,
    Integer,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    Index,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


class BaseORM(DeclarativeBase):
    """Base class for all ORM models."""
    pass


class ProviderORM(BaseORM):
    __tablename__ = "providers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    runs: Mapped[List["ProviderRunORM"]] = relationship(back_populates="provider", cascade="all, delete-orphan")


class SportORM(BaseORM):
    __tablename__ = "sports"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)

    competitions: Mapped[List["CompetitionORM"]] = relationship(back_populates="sport", cascade="all, delete-orphan")


class CompetitionORM(BaseORM):
    __tablename__ = "competitions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sport_id: Mapped[str] = mapped_column(String(64), ForeignKey("sports.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    country: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    season: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    sport: Mapped["SportORM"] = relationship(back_populates="competitions")
    events: Mapped[List["EventORM"]] = relationship(back_populates="competition", cascade="all, delete-orphan")


class TeamORM(BaseORM):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    country: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)


class EventORM(BaseORM):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    competition_id: Mapped[str] = mapped_column(String(64), ForeignKey("competitions.id"), nullable=False)
    home_team_name: Mapped[str] = mapped_column(String(256), nullable=False)
    away_team_name: Mapped[str] = mapped_column(String(256), nullable=False)
    kickoff: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="SCHEDULED", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    competition: Mapped["CompetitionORM"] = relationship(back_populates="events")
    markets: Mapped[List["MarketORM"]] = relationship(back_populates="event", cascade="all, delete-orphan")


class MarketORM(BaseORM):
    __tablename__ = "markets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), ForeignKey("events.id"), nullable=False)
    market_type: Mapped[str] = mapped_column(String(64), nullable=False)
    line: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="OPEN", nullable=False)

    event: Mapped["EventORM"] = relationship(back_populates="markets")
    outcomes: Mapped[List["OutcomeORM"]] = relationship(back_populates="market", cascade="all, delete-orphan")


class OutcomeORM(BaseORM):
    __tablename__ = "outcomes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    market_id: Mapped[str] = mapped_column(String(64), ForeignKey("markets.id"), nullable=False)
    outcome_type: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    handicap: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    market: Mapped["MarketORM"] = relationship(back_populates="outcomes")
    odds_history: Mapped[List["OddsORM"]] = relationship(back_populates="outcome", cascade="all, delete-orphan")


class OddsORM(BaseORM):
    """Append-only immutable historical odds snapshot table."""
    __tablename__ = "odds"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider_id: Mapped[str] = mapped_column(String(64), ForeignKey("providers.id"), nullable=False)
    outcome_id: Mapped[str] = mapped_column(String(64), ForeignKey("outcomes.id"), nullable=False)
    decimal_odds: Mapped[float] = mapped_column(Float, nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    outcome: Mapped["OutcomeORM"] = relationship(back_populates="odds_history")


class ProviderRunORM(BaseORM):
    __tablename__ = "provider_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider_id: Mapped[str] = mapped_column(String(64), ForeignKey("providers.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    events_count: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    provider: Mapped["ProviderORM"] = relationship(back_populates="runs")


class SnapshotORM(BaseORM):
    __tablename__ = "snapshots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider_id: Mapped[str] = mapped_column(String(64), ForeignKey("providers.id"), nullable=False)
    execution_id: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class OpportunityRecordORM(BaseORM):
    """Authoritative persistent record of a logical betting opportunity lifecycle state."""
    __tablename__ = "opportunity_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(256), unique=True, index=True, nullable=False)
    opportunity_type: Mapped[str] = mapped_column(String(32), default="SUREBET", nullable=False)
    canonical_event_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    market_key: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="NEW", nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    last_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    last_alerted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expired_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    arbitrage_margin: Mapped[float] = mapped_column(Float, nullable=False)
    implied_probability_sum: Mapped[float] = mapped_column(Float, nullable=False)
    consecutive_misses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    delivery_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    alert_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class DeliveryRecordORM(BaseORM):
    """Authoritative persistent record of an individual notification delivery stream attempt."""
    __tablename__ = "delivery_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(256), unique=True, index=True, nullable=False)
    opportunity_fingerprint: Mapped[str] = mapped_column(String(256), index=True, nullable=False)
    opportunity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    consumer_name: Mapped[str] = mapped_column(String(64), nullable=False)
    lifecycle_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="PENDING", index=True, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    first_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True, nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_error_category: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    payload_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)


