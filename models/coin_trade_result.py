from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TimestampMixin


class CoinTradeResult(Base, TimestampMixin):
    """코인 매매 결과 (AI 피드백 루프용)"""

    __tablename__ = "coin_trade_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    order_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    coin_name: Mapped[str] = mapped_column(String(100), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)  # BUY / SELL
    strategy_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)

    entry_price: Mapped[float] = mapped_column(Float, nullable=False)  # KRW
    exit_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)  # KRW
    return_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    is_win: Mapped[bool] = mapped_column(default=False)

    hold_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 코인은 시간 단위
    exit_reason: Mapped[str] = mapped_column(String(30), nullable=False, default="")

    # AI 분석 시점 데이터
    ai_recommendation: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    ai_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    ai_target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_stop_loss_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 진입 시점 기술 지표
    entry_rsi: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_macd_hist: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_bb_position: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # 코인 전용 컨텍스트
    market_regime: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    btc_dominance: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_24h_volume: Mapped[float | None] = mapped_column(Float, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    entry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    exit_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # ── TradeResult 호환 프로퍼티 (PerformanceTracker 공유용) ──

    @property
    def stock_symbol(self) -> str:
        return self.symbol

    @property
    def stock_name(self) -> str:
        return self.coin_name

    @property
    def hold_days(self) -> int:
        return self.hold_hours

    @property
    def entry_pattern(self) -> str | None:
        return None
