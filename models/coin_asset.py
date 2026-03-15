from uuid import uuid4

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TimestampMixin


class CoinAsset(Base, TimestampMixin):
    """코인 마스터 (주식 Stock 테이블과 분리)"""

    __tablename__ = "coin_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    symbol: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)  # BTC, ETH
    name: Mapped[str] = mapped_column(String(200), nullable=False)  # 비트코인, 이더리움
    market: Mapped[str] = mapped_column(String(20), nullable=False, default="BITHUMB")
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 대형코인, DeFi, Layer2, Meme
    market_cap_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)

    # Relationships
    orders: Mapped[list["CoinOrder"]] = relationship(back_populates="coin_asset")  # noqa: F821
    holdings: Mapped[list["CoinHolding"]] = relationship(back_populates="coin_asset")  # noqa: F821
