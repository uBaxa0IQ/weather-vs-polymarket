from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, Float, ForeignKey, Integer, PrimaryKeyConstraint, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class City(Base):
    __tablename__ = "cities"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    city_slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    station_code: Mapped[str | None] = mapped_column(Text)
    lat: Mapped[float] = mapped_column(Float(precision=53), nullable=False)
    lon: Mapped[float] = mapped_column(Float(precision=53), nullable=False)
    timezone_name: Mapped[str] = mapped_column(Text, nullable=False)
    temp_unit: Mapped[str] = mapped_column(Text, nullable=False, server_default="C")
    created_at_utc: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), server_default="NOW()")

    markets: Mapped[list[Market]] = relationship("Market", back_populates="city")


class Market(Base):
    __tablename__ = "markets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    city_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cities.id"), nullable=False)
    event_slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    target_date_local: Mapped[date] = mapped_column(Date, nullable=False)
    timezone_name: Mapped[str] = mapped_column(Text, nullable=False)
    nominal_resolve_at_utc: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at_utc: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), server_default="NOW()")
    # Real Polymarket (UMA) resolution — complementary to nominal_resolve / status rotation.
    pm_resolved_at_utc: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    pm_winning_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    pm_winning_bucket_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pm_resolution_checked_at_utc: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    pm_resolution_meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    city: Mapped[City] = relationship("City", back_populates="markets")
    snapshots: Mapped[list[MarketSnapshot]] = relationship("MarketSnapshot", back_populates="market")


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True)
    market_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("markets.id"), nullable=False)
    captured_at_utc: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    tomorrow_max: Mapped[float | None] = mapped_column(Float(precision=53))
    ecmwf_max: Mapped[float | None] = mapped_column(Float(precision=53))
    poly_implied: Mapped[float | None] = mapped_column(Float(precision=53))
    top_bucket: Mapped[str | None] = mapped_column(Text)
    top_bucket_prob: Mapped[float | None] = mapped_column(Float(precision=53))
    top_bucket_index: Mapped[int | None] = mapped_column(Integer)
    bucket_labels_json: Mapped[list | None] = mapped_column(JSONB)
    bucket_prices_json: Mapped[list | None] = mapped_column(JSONB)
    buckets_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    time_to_resolve_hours: Mapped[float | None] = mapped_column(Float(precision=53))

    __table_args__ = (PrimaryKeyConstraint("id", "captured_at_utc"),)

    market: Mapped[Market] = relationship("Market", back_populates="snapshots")


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    started_at_utc: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    finished_at_utc: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    triggered_by: Mapped[str | None] = mapped_column(Text)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at_utc: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default="NOW()")


class Bet(Base):
    __tablename__ = "bets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    condition_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="''")
    token_id: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[str] = mapped_column(Text, nullable=False)
    bucket_label: Mapped[str] = mapped_column(Text, nullable=False)
    market_title: Mapped[str] = mapped_column(Text, nullable=False)
    event_slug: Mapped[str | None] = mapped_column(Text)
    amount_usd: Mapped[float] = mapped_column(Float(precision=53), nullable=False)
    theoretical_price: Mapped[float] = mapped_column(Float(precision=53), nullable=False)
    actual_price: Mapped[float | None] = mapped_column(Float(precision=53))
    clob_order_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="'pending'")
    pnl: Mapped[float | None] = mapped_column(Float(precision=53))
    snapshot_json: Mapped[dict | None] = mapped_column(JSONB)
    placed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default="NOW()")
    resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
