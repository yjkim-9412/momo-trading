"""코인 에이전트 활동 로그"""
from datetime import date
from uuid import uuid4

from sqlalchemy import Date, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TimestampMixin


class CoinActivityLog(Base, TimestampMixin):
    __tablename__ = "coin_activity_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    cycle_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    trading_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)

    activity_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    phase: Mapped[str] = mapped_column(String(20), nullable=False)

    coin_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(20), nullable=True)

    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    llm_provider: Mapped[str | None] = mapped_column(String(30), nullable=True)
    llm_tier: Mapped[str | None] = mapped_column(String(10), nullable=True)
    execution_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
