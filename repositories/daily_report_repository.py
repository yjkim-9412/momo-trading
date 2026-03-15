"""일일 리포트 리포지토리"""
from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.daily_report import DailyReport
from repositories.async_base_repository import AsyncBaseRepository
from trading.market_profile import normalize_market_scope


class DailyReportRepository(AsyncBaseRepository[DailyReport]):
    def __init__(self, db: AsyncSession):
        super().__init__(DailyReport, db)

    async def get_by_date(
        self,
        report_date: date,
        market_scope: str = "KRX",
    ) -> Optional[DailyReport]:
        return await self.filter_by_one(
            report_date=report_date,
            market_scope=normalize_market_scope(market_scope),
        )

    async def get_latest(self, market_scope: str | None = None) -> Optional[DailyReport]:
        stmt = select(DailyReport).order_by(DailyReport.report_date.desc()).limit(1)
        if market_scope:
            stmt = stmt.where(DailyReport.market_scope == normalize_market_scope(market_scope))
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def get_reports(self, limit: int = 30, market_scope: str | None = None) -> list[DailyReport]:
        stmt = select(DailyReport).order_by(DailyReport.report_date.desc()).limit(limit)
        if market_scope:
            stmt = stmt.where(DailyReport.market_scope == normalize_market_scope(market_scope))
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
