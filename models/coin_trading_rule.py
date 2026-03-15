"""코인 트레이딩 규칙 — AI 리뷰에서 도출된 코인 전용 하드 규칙"""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TimestampMixin


class CoinTradingRule(Base, TimestampMixin):
    __tablename__ = "coin_trading_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))

    rule_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    strategy_type: Mapped[str] = mapped_column(String(30), default="ALL")

    param_name: Mapped[str] = mapped_column(String(50), nullable=False)
    param_value: Mapped[float] = mapped_column(Float, nullable=False)

    source: Mapped[str] = mapped_column(String(30), default="DAILY_REVIEW")
    reason: Mapped[str] = mapped_column(Text, default="")
    source_report_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    priority: Mapped[str] = mapped_column(String(10), default="MEDIUM")

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    applied_count: Mapped[int] = mapped_column(Integer, default=0)
