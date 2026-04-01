"""종목별 다단계 손익절 계획 — DB 영속 + 이력 추적"""
from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Float, Integer, String, Text, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TimestampMixin


class ExitPlan(Base, TimestampMixin):
    """
    종목별 활성 청산 계획.

    매수 체결 후 AI가 설정한 다단계 익절/손절 레벨을 DB에 저장하여
    프로세스 재시작 시 자동 복원하고, 추가 매수 시 재평가할 수 있다.

    levels JSON 예시:
    [
      {"type": "TAKE_PROFIT", "price": 155.0, "pct": 50, "triggered": false, "triggered_at": null},
      {"type": "TAKE_PROFIT", "price": 165.0, "pct": 100, "triggered": false, "triggered_at": null},
      {"type": "STOP_LOSS", "price": 140.0, "pct": 100, "triggered": false, "triggered_at": null}
    ]
    """
    __tablename__ = "exit_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    market: Mapped[str] = mapped_column(String(10), nullable=False)  # KRX, NYSE, NASDAQ

    avg_entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    total_quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    # 다단계 청산 레벨 (JSON string)
    levels: Mapped[str] = mapped_column(Text, nullable=False)

    trailing_stop_pct: Mapped[float] = mapped_column(Float, default=0.0)
    highest_price: Mapped[float] = mapped_column(Float, default=0.0)

    revision: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class ExitPlanHistory(Base):
    """
    ExitPlan 변경 이력.

    생성, 추가매수 재평가, 트레일링 스탑 갱신 등 모든 변경을 추적한다.
    """
    __tablename__ = "exit_plan_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    exit_plan_id: Mapped[str] = mapped_column(String(36), ForeignKey("exit_plans.id"), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)

    previous_levels: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON (최초 생성 시 null)
    new_levels: Mapped[str] = mapped_column(Text, nullable=False)  # JSON

    reason: Mapped[str] = mapped_column(String(30), nullable=False)
    # INITIAL | ADD_ON_PYRAMID | ADD_ON_AVERAGE_DOWN | AI_REVIEW | TRAILING_UPDATE

    avg_entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    total_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    ai_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
