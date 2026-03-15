"""코인 분석 결과"""
from uuid import uuid4

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TimestampMixin


class CoinAnalysisResult(Base, TimestampMixin):
    __tablename__ = "coin_analysis_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    coin_asset_id: Mapped[str] = mapped_column(String(36), ForeignKey("coin_assets.id"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(30), nullable=False)  # TECHNICAL
    recommendation: Mapped[str] = mapped_column(String(10), nullable=False)  # BUY / SELL / HOLD
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_provider: Mapped[str] = mapped_column(String(20), nullable=False)
    llm_tier: Mapped[str] = mapped_column(String(10), nullable=False)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Relationships
    recommendations: Mapped[list["CoinRecommendation"]] = relationship(back_populates="analysis")  # noqa: F821
