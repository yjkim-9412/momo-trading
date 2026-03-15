"""코인 체크포인트 리포트"""
from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import Date, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TimestampMixin


class CoinDailyReport(Base, TimestampMixin):
    __tablename__ = "coin_daily_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    report_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    report_source: Mapped[str] = mapped_column(String(30), default="AUTO_PRE_CYCLE", nullable=False, index=True)
    trigger_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    applied_cycle_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    period_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    period_ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    # 24h 성과
    total_cycles: Mapped[int] = mapped_column(Integer, default=0)
    total_analyses: Mapped[int] = mapped_column(Integer, default=0)
    total_recommendations: Mapped[int] = mapped_column(Integer, default=0)
    total_orders: Mapped[int] = mapped_column(Integer, default=0)
    buy_count: Mapped[int] = mapped_column(Integer, default=0)
    sell_count: Mapped[int] = mapped_column(Integer, default=0)
    win_count: Mapped[int] = mapped_column(Integer, default=0)
    loss_count: Mapped[int] = mapped_column(Integer, default=0)
    total_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    open_position_count: Mapped[int] = mapped_column(Integer, default=0)

    # 코인 전용 요약
    total_24h_volume: Mapped[float] = mapped_column(Float, default=0.0)  # 전체 거래대금
    btc_dominance: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_regime: Mapped[str] = mapped_column(String(20), default="")  # BULL_RUN 등

    # AI 요약 (LLM 생성)
    market_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    performance_review: Mapped[str | None] = mapped_column(Text, nullable=True)
    lessons_learned: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_day_plan: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 상세 데이터 (JSON)
    top_picks: Mapped[str | None] = mapped_column(Text, nullable=True)
    strategy_stats: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def market_scope(self) -> str:
        return "CRYPTO"
