"""매매 결과 리포지토리"""
from datetime import date, datetime, time

from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from models.trade_result import TradeResult
from repositories.async_base_repository import AsyncBaseRepository
from scheduler.market_calendar import market_calendar
from trading.market_profile import markets_for_scope


class TradeResultRepository(AsyncBaseRepository[TradeResult]):

    def __init__(self, session: AsyncSession):
        super().__init__(TradeResult, session)

    async def get_by_symbol(
        self,
        symbol: str,
        limit: int = 50,
        market_scope: str | None = None,
    ) -> list[TradeResult]:
        stmt = (
            select(TradeResult)
            .where(TradeResult.stock_symbol == symbol)
            .order_by(TradeResult.created_at.desc())
            .limit(limit)
        )
        if market_scope:
            stmt = stmt.where(TradeResult.market.in_(markets_for_scope(market_scope)))
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_by_strategy(
        self,
        strategy_type: str,
        limit: int = 100,
        market_scope: str | None = None,
    ) -> list[TradeResult]:
        stmt = (
            select(TradeResult)
            .where(TradeResult.strategy_type == strategy_type)
            .order_by(TradeResult.created_at.desc())
            .limit(limit)
        )
        if market_scope:
            stmt = stmt.where(TradeResult.market.in_(markets_for_scope(market_scope)))
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_recent(self, limit: int = 50, market_scope: str | None = None) -> list[TradeResult]:
        stmt = (
            select(TradeResult)
            .order_by(TradeResult.created_at.desc())
            .limit(limit)
        )
        if market_scope:
            stmt = stmt.where(TradeResult.market.in_(markets_for_scope(market_scope)))
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_open_buy(self, symbol: str, market: str | None = None) -> TradeResult | None:
        """미청산 매수 기록 조회 (exit_at IS NULL, side=BUY)"""
        conditions = [
            TradeResult.stock_symbol == symbol,
            TradeResult.side == "BUY",
            TradeResult.exit_at.is_(None),
        ]
        if market:
            conditions.append(TradeResult.market == market)
        stmt = (
            select(TradeResult)
            .where(and_(*conditions))
            .order_by(TradeResult.created_at.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_completed_by_date(
        self,
        target_date: date,
        market_scope: str | None = None,
    ) -> list[TradeResult]:
        """특정 날짜에 청산 완료된 거래 (exit_at 기준)"""
        if market_scope:
            start, end = market_calendar.market_day_bounds(market_scope, target_date)
        else:
            start = datetime.combine(target_date, time.min)
            end = datetime.combine(target_date, time.max)
        conditions = [
            TradeResult.exit_at.isnot(None),
            TradeResult.exit_at >= start,
            TradeResult.exit_at <= end,
        ]
        if market_scope:
            conditions.append(TradeResult.market.in_(markets_for_scope(market_scope)))
        stmt = (
            select(TradeResult)
            .where(and_(*conditions))
            .order_by(TradeResult.exit_at.asc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_opened_by_date(
        self,
        target_date: date,
        market_scope: str | None = None,
    ) -> list[TradeResult]:
        """특정 날짜에 진입한 매수 기록 (entry_at 기준)"""
        if market_scope:
            start, end = market_calendar.market_day_bounds(market_scope, target_date)
        else:
            start = datetime.combine(target_date, time.min)
            end = datetime.combine(target_date, time.max)
        conditions = [
            TradeResult.side == "BUY",
            TradeResult.entry_at >= start,
            TradeResult.entry_at <= end,
        ]
        if market_scope:
            conditions.append(TradeResult.market.in_(markets_for_scope(market_scope)))
        stmt = (
            select(TradeResult)
            .where(and_(*conditions))
            .order_by(TradeResult.entry_at.asc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_all_open(self, market_scope: str | None = None) -> list[TradeResult]:
        """미청산 포지션 전체 조회 (exit_at IS NULL, side=BUY)"""
        conditions = [
            TradeResult.side == "BUY",
            TradeResult.exit_at.is_(None),
        ]
        if market_scope:
            conditions.append(TradeResult.market.in_(markets_for_scope(market_scope)))
        stmt = (
            select(TradeResult)
            .where(and_(*conditions))
            .order_by(TradeResult.created_at.desc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
