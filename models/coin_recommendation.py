"""코인 AI 추천 (SEMI_AUTO 모드용)"""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TimestampMixin


class CoinRecommendation(Base, TimestampMixin):
    __tablename__ = "coin_recommendations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    coin_asset_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    analysis_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("coin_analysis_results.id"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(10), nullable=False)  # BUY / SELL
    suggested_price: Mapped[float] = mapped_column(Float, nullable=False)
    suggested_quantity: Mapped[float] = mapped_column(Float, nullable=False)  # 소수점 수량
    suggested_amount_krw: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING", index=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Relationships
    analysis: Mapped["CoinAnalysisResult"] = relationship(back_populates="recommendations")  # noqa: F821
