from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TimestampMixin


class CoinOrder(Base, TimestampMixin):
    """코인 주문 이력"""

    __tablename__ = "coin_orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    coin_asset_id: Mapped[str] = mapped_column(String(36), ForeignKey("coin_assets.id"), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(10), nullable=False)  # BUY / SELL
    order_type: Mapped[str] = mapped_column(String(10), nullable=False)  # MARKET / LIMIT
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", index=True)
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="AI")  # AI / MANUAL / RISK

    quantity: Mapped[float] = mapped_column(Float, nullable=False)  # 소수점 수량
    price: Mapped[float | None] = mapped_column(Float, nullable=True)  # 지정가 (시장가 시 None)
    filled_quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    filled_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    bithumb_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    coin_asset: Mapped["CoinAsset"] = relationship(back_populates="orders")  # noqa: F821
