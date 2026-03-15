from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TimestampMixin


class BrokerOrder(Base, TimestampMixin):
    """브로커 주문 이력 (자동매매 런타임용)"""

    __tablename__ = "broker_orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    cycle_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    market: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    stock_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="SUBMITTED", index=True)
    strategy_type: Mapped[str] = mapped_column(String(30), nullable=False, default="")

    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    requested_price_krw: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    filled_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    filled_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    filled_price_krw: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="KRW")
    exchange_rate_to_krw: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    kis_order_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    status_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
