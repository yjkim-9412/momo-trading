from uuid import uuid4

from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TimestampMixin


class CoinHolding(Base, TimestampMixin):
    """코인 보유 현황"""

    __tablename__ = "coin_holdings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    coin_asset_id: Mapped[str] = mapped_column(String(36), ForeignKey("coin_assets.id"), nullable=False, index=True)
    quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_buy_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)  # KRW
    current_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    unrealized_pnl_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Relationships
    coin_asset: Mapped["CoinAsset"] = relationship(back_populates="holdings")  # noqa: F821
