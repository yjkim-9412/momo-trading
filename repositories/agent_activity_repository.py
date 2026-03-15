"""에이전트 활동 로그 리포지토리"""
from datetime import date, datetime, time

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.agent_activity import AgentActivityLog
from repositories.async_base_repository import AsyncBaseRepository
from trading.market_profile import normalize_market_scope


class AgentActivityRepository(AsyncBaseRepository[AgentActivityLog]):
    def __init__(self, db: AsyncSession):
        super().__init__(AgentActivityLog, db)

    async def get_by_date(
        self,
        target_date: date,
        limit: int = 500,
        offset: int = 0,
        market_scope: str | None = None,
    ) -> list[AgentActivityLog]:
        stmt = select(AgentActivityLog).order_by(
            AgentActivityLog.created_at.desc(),
            AgentActivityLog.id.desc(),
        )
        if market_scope:
            stmt = stmt.where(
                AgentActivityLog.market_scope == normalize_market_scope(market_scope),
                AgentActivityLog.trading_date == target_date,
            )
        else:
            start = datetime.combine(target_date, time.min)
            end = datetime.combine(target_date, time.max)
            stmt = stmt.where(AgentActivityLog.created_at.between(start, end))
        result = await self.db.execute(stmt.offset(offset).limit(limit))
        return list(result.scalars().all())

    async def get_by_cycle(self, cycle_id: str) -> list[AgentActivityLog]:
        result = await self.db.execute(
            select(AgentActivityLog)
            .where(AgentActivityLog.cycle_id == cycle_id)
            .order_by(AgentActivityLog.created_at.asc())
        )
        return list(result.scalars().all())

    async def get_by_type(
        self,
        activity_type: str,
        limit: int = 100,
        market_scope: str | None = None,
    ) -> list[AgentActivityLog]:
        stmt = (
            select(AgentActivityLog)
            .where(AgentActivityLog.activity_type == activity_type)
            .order_by(AgentActivityLog.created_at.desc(), AgentActivityLog.id.desc())
            .limit(limit)
        )
        if market_scope:
            stmt = stmt.where(AgentActivityLog.market_scope == normalize_market_scope(market_scope))
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_feed_page(
        self,
        target_date: date,
        market_scope: str,
        limit: int = 100,
        before_created_at: datetime | None = None,
        before_id: str | None = None,
    ) -> tuple[list[AgentActivityLog], bool]:
        stmt = (
            select(AgentActivityLog)
            .where(
                AgentActivityLog.market_scope == normalize_market_scope(market_scope),
                AgentActivityLog.trading_date == target_date,
            )
            .order_by(AgentActivityLog.created_at.desc(), AgentActivityLog.id.desc())
            .limit(limit + 1)
        )
        if before_created_at is not None:
            if before_id:
                stmt = stmt.where(
                    or_(
                        AgentActivityLog.created_at < before_created_at,
                        and_(
                            AgentActivityLog.created_at == before_created_at,
                            AgentActivityLog.id < before_id,
                        ),
                    )
                )
            else:
                stmt = stmt.where(AgentActivityLog.created_at < before_created_at)

        result = await self.db.execute(stmt)
        rows = list(result.scalars().all())
        has_more = len(rows) > limit
        return rows[:limit], has_more

    async def get_recent_cycles(self, limit: int = 20, market_scope: str | None = None) -> list[dict]:
        """최근 사이클 목록 (cycle_id별 그룹핑)"""
        stmt = (
            select(
                AgentActivityLog.cycle_id,
                func.min(AgentActivityLog.created_at).label("started_at"),
                func.max(AgentActivityLog.created_at).label("ended_at"),
                func.count(AgentActivityLog.id).label("activity_count"),
                func.max(AgentActivityLog.market_scope).label("market_scope"),
            )
            .where(AgentActivityLog.cycle_id.isnot(None))
            .group_by(AgentActivityLog.cycle_id)
            .order_by(func.max(AgentActivityLog.created_at).desc())
            .limit(limit)
        )
        if market_scope:
            stmt = stmt.where(AgentActivityLog.market_scope == normalize_market_scope(market_scope))
        result = await self.db.execute(stmt)
        return [
            {
                "cycle_id": row.cycle_id,
                "started_at": row.started_at,
                "ended_at": row.ended_at,
                "activity_count": row.activity_count,
                "market_scope": row.market_scope,
            }
            for row in result.all()
        ]

    async def count_by_date(self, target_date: date, market_scope: str | None = None) -> dict:
        """날짜별 활동 유형 카운트"""
        stmt = select(
            AgentActivityLog.activity_type,
            func.count(AgentActivityLog.id).label("count"),
        ).group_by(AgentActivityLog.activity_type)
        if market_scope:
            stmt = stmt.where(
                AgentActivityLog.market_scope == normalize_market_scope(market_scope),
                AgentActivityLog.trading_date == target_date,
            )
        else:
            start = datetime.combine(target_date, time.min)
            end = datetime.combine(target_date, time.max)
            stmt = stmt.where(AgentActivityLog.created_at.between(start, end))
        result = await self.db.execute(stmt)
        return {row.activity_type: row.count for row in result.all()}
