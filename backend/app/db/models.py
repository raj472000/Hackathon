"""SQLAlchemy models (PostgreSQL in deployment, SQLite for local runs).

Prices and money use NUMERIC. Normalised statement facts and computed metric bundles are stored as
JSON documents next to typed key columns; raw provider lineage is kept in `data_source_records`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class UTCDateTime(TypeDecorator):  # type: ignore[type-arg]
    """Timezone-aware UTC timestamps on every backend (SQLite drops the offset on its own)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC) if value is not None else None

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


PRICE = Numeric(18, 4)
TS = UTCDateTime()


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    role: Mapped[str] = mapped_column(String(20), default="analyst")  # analyst | admin
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Instrument(Base):
    __tablename__ = "instruments"
    __table_args__ = (UniqueConstraint("exchange", "symbol"), Index("ix_instruments_sector", "sector"))
    id: Mapped[int] = mapped_column(primary_key=True)
    exchange: Mapped[str] = mapped_column(String(10), default="NSE")
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str | None] = mapped_column(String(200))
    isin: Mapped[str | None] = mapped_column(String(12), index=True)
    instrument_type: Mapped[str] = mapped_column(String(12), default="EQ")  # EQ ETF INDEX REIT INVIT COMMODITY
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    sector: Mapped[str | None] = mapped_column(String(80))
    industry: Mapped[str | None] = mapped_column(String(120))
    sector_kind: Mapped[str] = mapped_column(String(12), default="industrial")  # industrial | financial | index
    sector_index_symbol: Mapped[str | None] = mapped_column(String(40))
    lot_size: Mapped[int | None] = mapped_column(Integer)
    tick_size: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    trading_status: Mapped[str] = mapped_column(String(20), default="active")
    governance_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    # commodity / derivative contract metadata (expiry, contract size, roll calendar ...) lives here
    contract_meta: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    classification_source: Mapped[str | None] = mapped_column(String(80))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)
    identifiers: Mapped[list[InstrumentIdentifier]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class InstrumentIdentifier(Base):
    __tablename__ = "instrument_identifiers"
    __table_args__ = (UniqueConstraint("instrument_id", "provider", "kind"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(30))
    kind: Mapped[str] = mapped_column(String(30))
    value: Mapped[str] = mapped_column(String(80))


class PriceBar(Base):
    """Daily bar after source reconciliation and split/bonus back-adjustment."""

    __tablename__ = "price_bars"
    __table_args__ = (UniqueConstraint("instrument_id", "date"), Index("ix_price_bars_date", "date"))
    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date)
    open: Mapped[Decimal | None] = mapped_column(PRICE)
    high: Mapped[Decimal | None] = mapped_column(PRICE)
    low: Mapped[Decimal | None] = mapped_column(PRICE)
    close: Mapped[Decimal] = mapped_column(PRICE)
    volume: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    traded_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))
    provider: Mapped[str] = mapped_column(String(30))
    adjustment: Mapped[str] = mapped_column(String(20), default="split_adjusted")
    retrieved_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class CorporateActionRow(Base):
    __tablename__ = "corporate_actions"
    __table_args__ = (UniqueConstraint("instrument_id", "ex_date", "kind", "description"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), index=True)
    ex_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(20))
    ratio: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    description: Mapped[str] = mapped_column(String(300), default="")
    provider: Mapped[str] = mapped_column(String(30))
    retrieved_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class MarketQuote(Base):
    __tablename__ = "market_quotes"
    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), unique=True)
    last_price: Mapped[Decimal | None] = mapped_column(PRICE)
    fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # field -> value + provenance + conflict
    retrieved_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class SourceConflict(Base):
    __tablename__ = "source_conflicts"
    __table_args__ = (UniqueConstraint("instrument_id", "field", "obs_date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(30))
    obs_date: Mapped[date] = mapped_column(Date)
    values: Mapped[dict[str, Any]] = mapped_column(JSON)
    diff_pct: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    tolerance_pct: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    chosen: Mapped[str] = mapped_column(String(30))
    detected_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class DataSourceRecord(Base):
    """Lineage of each ingestion call: which provider and tool produced what, and when."""

    __tablename__ = "data_source_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(30), index=True)
    source_id: Mapped[str | None] = mapped_column(String(80))
    dataset: Mapped[str] = mapped_column(String(30))  # history | quote | corporate_actions | fundamentals
    status: Mapped[str] = mapped_column(String(20))  # ok | unavailable | not_supported | login_required | not_configured
    reason: Mapped[str | None] = mapped_column(String(400))
    rows: Mapped[int | None] = mapped_column(Integer)
    as_of: Mapped[date | None] = mapped_column(Date)
    adjustment: Mapped[str | None] = mapped_column(String(20))
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    retrieved_at: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)


class FinancialPeriod(Base):
    __tablename__ = "financial_statement_periods"
    __table_args__ = (UniqueConstraint("instrument_id", "period_end", "period_type", "source"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), index=True)
    period_end: Mapped[date] = mapped_column(Date)
    period_type: Mapped[str] = mapped_column(String(10))
    fiscal_year: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    unit: Mapped[str] = mapped_column(String(12), default="crore")
    reported_unit: Mapped[str | None] = mapped_column(String(12))
    source: Mapped[str] = mapped_column(String(200))
    source_url: Mapped[str | None] = mapped_column(String(500))
    published_at: Mapped[date | None] = mapped_column(Date)
    ingested_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    facts: Mapped[dict[str, Any]] = mapped_column(JSON)  # normalised values (INR crore)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # as supplied, before unit normalisation
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class MetricSnapshot(Base):
    """All computed metrics and scores for one instrument as of one date."""

    __tablename__ = "metric_snapshots"
    __table_args__ = (UniqueConstraint("instrument_id", "as_of"), Index("ix_metric_snapshots_asof", "as_of"))
    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), index=True)
    as_of: Mapped[date] = mapped_column(Date)
    flat: Mapped[dict[str, Any]] = mapped_column(JSON)  # metric name -> value (None when unavailable)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON)  # metric name -> status, reason, formula, inputs, unit
    score: Mapped[dict[str, Any]] = mapped_column(JSON)  # pillars, overall, completeness, labels, entry setup
    overall_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), index=True)
    versions: Mapped[dict[str, Any]] = mapped_column(JSON)
    is_latest: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    computed_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Theme(Base):
    __tablename__ = "themes"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(60), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    methodology: Mapped[str] = mapped_column(Text, default="")
    owner: Mapped[str] = mapped_column(String(80), default="system")
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    requires: Mapped[list[Any]] = mapped_column(JSON, default=list)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    spec: Mapped[dict[str, Any]] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)
    scoring_version: Mapped[str] = mapped_column(String(30), default="")
    refresh_cron: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class ThemeVersion(Base):
    __tablename__ = "theme_versions"
    __table_args__ = (UniqueConstraint("theme_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    theme_id: Mapped[int] = mapped_column(ForeignKey("themes.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict[str, Any]] = mapped_column(JSON)
    changed_by: Mapped[str] = mapped_column(String(80), default="system")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class ThemeSnapshot(Base):
    """Immutable result of one theme refresh. A new refresh in the same month adds a revision."""

    __tablename__ = "theme_snapshots"
    __table_args__ = (UniqueConstraint("theme_id", "period", "revision"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    theme_id: Mapped[int] = mapped_column(ForeignKey("themes.id", ondelete="CASCADE"), index=True)
    period: Mapped[str] = mapped_column(String(7), index=True)  # YYYY-MM
    revision: Mapped[int] = mapped_column(Integer, default=1)
    theme_version: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict[str, Any]] = mapped_column(JSON)
    metrics_as_of: Mapped[date | None] = mapped_column(Date)
    universe_count: Mapped[int] = mapped_column(Integer, default=0)
    eligible_count: Mapped[int] = mapped_column(Integer, default=0)
    exclusions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    versions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    trigger: Mapped[str] = mapped_column(String(20), default="scheduled")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    constituents: Mapped[list[ThemeSnapshotConstituent]] = relationship(cascade="all, delete-orphan", order_by="ThemeSnapshotConstituent.rank")


class ThemeSnapshotConstituent(Base):
    __tablename__ = "theme_snapshot_constituents"
    __table_args__ = (UniqueConstraint("snapshot_id", "instrument_id"), Index("ix_tsc_rank", "snapshot_id", "rank"))
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("theme_snapshots.id", ondelete="CASCADE"), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"))
    symbol: Mapped[str] = mapped_column(String(40))
    rank: Mapped[int] = mapped_column(Integer)
    score: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    price_at_snapshot: Mapped[Decimal | None] = mapped_column(PRICE)


class SectorRotationSnapshot(Base):
    __tablename__ = "sector_rotation_snapshots"
    __table_args__ = (UniqueConstraint("benchmark", "slot", "params_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    benchmark: Mapped[str] = mapped_column(String(40))
    slot: Mapped[str] = mapped_column(String(20), index=True)  # 12-hour bucket, e.g. 2026-10-12T00
    params_key: Mapped[str] = mapped_column(String(40), default="default")
    data_as_of: Mapped[date | None] = mapped_column(Date)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    coverage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    version: Mapped[str] = mapped_column(String(20))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    computed_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class ResearchReport(Base):
    __tablename__ = "research_reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), index=True)
    mode: Mapped[str] = mapped_column(String(20))  # llm | deterministic
    status: Mapped[str] = mapped_column(String(20), default="ok")
    report: Mapped[dict[str, Any]] = mapped_column(JSON)
    model_id: Mapped[str | None] = mapped_column(String(120))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)
    sources: Mapped[list[ResearchSource]] = relationship(cascade="all, delete-orphan")


class ResearchSource(Base):
    __tablename__ = "research_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("research_reports.id", ondelete="CASCADE"), index=True)
    ref: Mapped[str] = mapped_column(String(12))
    title: Mapped[str] = mapped_column(String(300))
    url: Mapped[str] = mapped_column(String(1000))
    source_type: Mapped[str] = mapped_column(String(12))
    published_at: Mapped[str | None] = mapped_column(String(40))
    retrieved_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    snippet: Mapped[str] = mapped_column(Text, default="")


class ConcallTranscript(Base):
    __tablename__ = "concall_transcripts"
    __table_args__ = (UniqueConstraint("instrument_id", "fiscal_year", "quarter"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), index=True)
    fiscal_year: Mapped[int] = mapped_column(Integer)
    quarter: Mapped[int] = mapped_column(Integer)
    call_date: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(200))
    source_url: Mapped[str | None] = mapped_column(String(1000))
    text: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    chars: Mapped[int] = mapped_column(Integer)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    uploaded_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class ConcallAnalysis(Base):
    __tablename__ = "concall_analyses"
    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), index=True)
    previous_transcript_id: Mapped[int] = mapped_column(ForeignKey("concall_transcripts.id", ondelete="CASCADE"))
    current_transcript_id: Mapped[int] = mapped_column(ForeignKey("concall_transcripts.id", ondelete="CASCADE"))
    mode: Mapped[str] = mapped_column(String(20))  # llm | heuristic
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    label: Mapped[str] = mapped_column(String(40))
    confidence: Mapped[str] = mapped_column(String(10))
    result: Mapped[dict[str, Any]] = mapped_column(JSON)
    version: Mapped[str] = mapped_column(String(20))
    model_id: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Watchlist(Base):
    __tablename__ = "watchlists"
    __table_args__ = (UniqueConstraint("owner", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    owner: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    items: Mapped[list[WatchlistItem]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("watchlist_id", "instrument_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    watchlist_id: Mapped[int] = mapped_column(ForeignKey("watchlists.id", ondelete="CASCADE"), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"))
    note: Mapped[str | None] = mapped_column(String(300))
    added_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (UniqueConstraint("owner", "idempotency_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    owner: Mapped[str] = mapped_column(String(80), index=True)
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(30))
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    channel: Mapped[str] = mapped_column(String(20), default="in_app")
    status: Mapped[str] = mapped_column(String(10), default="active")  # active | paused
    idempotency_key: Mapped[str] = mapped_column(String(80))
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # last observed value, for cross detection
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    last_triggered_at: Mapped[datetime | None] = mapped_column(TS)
    events: Mapped[list[AlertEvent]] = relationship(cascade="all, delete-orphan")


class AlertEvent(Base):
    __tablename__ = "alert_events"
    __table_args__ = (UniqueConstraint("alert_id", "dedupe_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(120))
    message: Mapped[str] = mapped_column(String(500))
    delivery_status: Mapped[str] = mapped_column(String(20), default="recorded")  # in-app history; no external send is claimed
    triggered_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class JobRun(Base):
    __tablename__ = "job_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    job_key: Mapped[str] = mapped_column(String(120), index=True)
    trigger: Mapped[str] = mapped_column(String(20), default="scheduled")
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | succeeded | failed | skipped
    started_at: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(TS)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(1000))
    requested_by: Mapped[str | None] = mapped_column(String(80))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(60))
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    request_id: Mapped[str | None] = mapped_column(String(40))
