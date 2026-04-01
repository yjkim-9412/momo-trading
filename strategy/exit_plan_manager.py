"""ExitPlan CRUD + 이력 관리 서비스"""
from __future__ import annotations

import json
import logging
import math
from typing import Any

from sqlalchemy import select

from core.database import AsyncSessionLocal
from models.exit_plan import ExitPlan, ExitPlanHistory
from util.time_util import now_kst

logger = logging.getLogger(__name__)


# ── 레벨 타입 상수 ──
TP = "TAKE_PROFIT"
SL = "STOP_LOSS"

# ── 변경 사유 상수 ──
REASON_INITIAL = "INITIAL"
REASON_ADD_ON_PYRAMID = "ADD_ON_PYRAMID"
REASON_ADD_ON_AVG_DOWN = "ADD_ON_AVERAGE_DOWN"
REASON_AI_REVIEW = "AI_REVIEW"
REASON_TRAILING_UPDATE = "TRAILING_UPDATE"


class ExitPlanManager:
    """종목별 다단계 손익절 계획 관리."""

    # ── 생성 ──

    async def create_plan(
        self,
        *,
        symbol: str,
        market: str,
        avg_entry_price: float,
        total_quantity: int,
        levels: list[dict[str, Any]],
        trailing_stop_pct: float = 0.0,
        reason: str = REASON_INITIAL,
        ai_reasoning: str | None = None,
    ) -> ExitPlan:
        """새 ExitPlan 생성 + History 기록. 기존 활성 plan이 있으면 비활성화."""
        ts = now_kst()
        levels_json = json.dumps(levels, ensure_ascii=False)

        async with AsyncSessionLocal() as session:
            async with session.begin():
                # 기존 활성 plan 비활성화
                existing = await self._get_active_plan(session, symbol, market)
                if existing:
                    existing.is_active = False
                    existing.updated_at = ts

                plan = ExitPlan(
                    symbol=symbol,
                    market=market,
                    avg_entry_price=avg_entry_price,
                    total_quantity=total_quantity,
                    levels=levels_json,
                    trailing_stop_pct=trailing_stop_pct,
                    highest_price=avg_entry_price,
                    revision=1,
                    is_active=True,
                    created_at=ts,
                    updated_at=ts,
                )
                session.add(plan)
                await session.flush()  # id 확정

                history = ExitPlanHistory(
                    exit_plan_id=plan.id,
                    revision=1,
                    previous_levels=None,
                    new_levels=levels_json,
                    reason=reason,
                    avg_entry_price=avg_entry_price,
                    total_quantity=total_quantity,
                    ai_reasoning=ai_reasoning,
                    created_at=ts,
                )
                session.add(history)

        logger.info(
            "ExitPlan created: %s/%s avg=%.2f qty=%d levels=%d",
            symbol, market, avg_entry_price, total_quantity, len(levels),
        )
        return plan

    # ── 업데이트 ──

    async def update_plan(
        self,
        *,
        symbol: str,
        market: str,
        new_levels: list[dict[str, Any]],
        new_avg_price: float,
        new_qty: int,
        reason: str,
        ai_reasoning: str | None = None,
    ) -> ExitPlan | None:
        """기존 활성 plan 업데이트 + revision 증가 + History 기록."""
        ts = now_kst()
        new_levels_json = json.dumps(new_levels, ensure_ascii=False)

        async with AsyncSessionLocal() as session:
            async with session.begin():
                plan = await self._get_active_plan(session, symbol, market)
                if not plan:
                    logger.warning("update_plan: no active plan for %s/%s", symbol, market)
                    return None

                previous_levels = plan.levels
                plan.levels = new_levels_json
                plan.avg_entry_price = new_avg_price
                plan.total_quantity = new_qty
                plan.revision += 1
                plan.updated_at = ts

                # 추가매수 시 highest_price도 갱신
                if new_avg_price > plan.highest_price:
                    plan.highest_price = new_avg_price

                history = ExitPlanHistory(
                    exit_plan_id=plan.id,
                    revision=plan.revision,
                    previous_levels=previous_levels,
                    new_levels=new_levels_json,
                    reason=reason,
                    avg_entry_price=new_avg_price,
                    total_quantity=new_qty,
                    ai_reasoning=ai_reasoning,
                    created_at=ts,
                )
                session.add(history)

        logger.info(
            "ExitPlan updated: %s/%s rev=%d reason=%s",
            symbol, market, plan.revision, reason,
        )
        return plan

    # ── 레벨 트리거 ──

    async def mark_level_triggered(
        self,
        plan_id: str,
        level_index: int,
    ) -> ExitPlan | None:
        """부분 익절 후 해당 레벨을 triggered=true로 마킹."""
        ts = now_kst()

        async with AsyncSessionLocal() as session:
            async with session.begin():
                plan = await session.get(ExitPlan, plan_id)
                if not plan or not plan.is_active:
                    return None

                levels = json.loads(plan.levels)
                if level_index < 0 or level_index >= len(levels):
                    logger.error("mark_level_triggered: invalid index %d for plan %s", level_index, plan_id)
                    return None

                levels[level_index]["triggered"] = True
                levels[level_index]["triggered_at"] = ts.isoformat()
                plan.levels = json.dumps(levels, ensure_ascii=False)
                plan.updated_at = ts

                # 모든 TP 레벨 소진 시 비활성화
                tp_levels = [l for l in levels if l["type"] == TP]
                if all(l.get("triggered") for l in tp_levels):
                    plan.is_active = False
                    logger.info("ExitPlan %s: all TP levels triggered, deactivated", plan_id)

        return plan

    # ── 비활성화 ──

    async def deactivate_plan(self, plan_id: str) -> None:
        """전량 청산 후 비활성화."""
        ts = now_kst()
        async with AsyncSessionLocal() as session:
            async with session.begin():
                plan = await session.get(ExitPlan, plan_id)
                if plan and plan.is_active:
                    plan.is_active = False
                    plan.updated_at = ts
                    logger.info("ExitPlan deactivated: %s (%s/%s)", plan_id, plan.symbol, plan.market)

    async def deactivate_by_symbol(self, symbol: str, market: str) -> None:
        """종목/마켓 기준 활성 plan 비활성화."""
        ts = now_kst()
        async with AsyncSessionLocal() as session:
            async with session.begin():
                plan = await self._get_active_plan(session, symbol, market)
                if plan:
                    plan.is_active = False
                    plan.updated_at = ts
                    logger.info("ExitPlan deactivated by symbol: %s/%s", symbol, market)

    # ── 조회 ──

    async def get_active_plan(self, symbol: str, market: str) -> ExitPlan | None:
        """종목/마켓별 활성 plan 조회."""
        async with AsyncSessionLocal() as session:
            return await self._get_active_plan(session, symbol, market)

    async def get_all_active_plans(self) -> list[ExitPlan]:
        """모든 활성 plan 조회 (재시작 시 복원용)."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ExitPlan).where(ExitPlan.is_active == True)  # noqa: E712
            )
            return list(result.scalars().all())

    # ── 수량 계산 ──

    @staticmethod
    def calculate_sell_quantity(total_quantity: int, sell_pct: int) -> int:
        """레벨의 pct 기준 매도 수량 계산. 최소 1주, 최대 전량."""
        if sell_pct >= 100:
            return total_quantity
        qty = math.floor(total_quantity * sell_pct / 100)
        return max(1, min(qty, total_quantity))

    # ── 유틸 ──

    @staticmethod
    def parse_levels(levels_json: str) -> list[dict[str, Any]]:
        """levels JSON 파싱."""
        return json.loads(levels_json)

    @staticmethod
    def get_active_tp_levels(levels: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """미트리거 TP 레벨만 가격 오름차순 반환."""
        tp = [l for l in levels if l["type"] == TP and not l.get("triggered")]
        return sorted(tp, key=lambda x: x["price"])

    @staticmethod
    def get_stop_loss_price(levels: list[dict[str, Any]]) -> float:
        """SL 가격 추출. 없으면 0.0."""
        for l in levels:
            if l["type"] == SL:
                return float(l["price"])
        return 0.0

    # ── 내부 ──

    @staticmethod
    async def _get_active_plan(session, symbol: str, market: str) -> ExitPlan | None:
        result = await session.execute(
            select(ExitPlan).where(
                ExitPlan.symbol == symbol,
                ExitPlan.market == market,
                ExitPlan.is_active == True,  # noqa: E712
            )
        )
        return result.scalar_one_or_none()


# 싱글톤 인스턴스
exit_plan_manager = ExitPlanManager()
