"""코인 체크포인트 리포트 저장소"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.coin_daily_report import CoinDailyReport
from repositories.async_base_repository import AsyncBaseRepository


class CoinDailyReportRepository(AsyncBaseRepository[CoinDailyReport]):
    """코인 체크포인트 리포트 조회/저장 헬퍼"""

    def __init__(self, db: AsyncSession):
        super().__init__(CoinDailyReport, db)

    @staticmethod
    def _normalize_report_sources(report_sources: Iterable[str] | None) -> list[str]:
        return [str(source).upper() for source in (report_sources or []) if str(source).strip()]

    async def get_latest(self, report_sources: Iterable[str] | None = None) -> CoinDailyReport | None:
        """가장 최근 체크포인트 리포트를 반환한다."""
        stmt = select(CoinDailyReport)
        normalized_sources = self._normalize_report_sources(report_sources)
        if normalized_sources:
            stmt = stmt.where(CoinDailyReport.report_source.in_(normalized_sources))

        result = await self.db.execute(
            stmt.order_by(
                CoinDailyReport.period_ended_at.desc().nullslast(),
                CoinDailyReport.created_at.desc(),
            ).limit(1)
        )
        return result.scalars().first()

    async def get_reports(self, limit: int = 30) -> list[CoinDailyReport]:
        """최근 체크포인트 리포트 목록을 반환한다."""
        result = await self.db.execute(
            select(CoinDailyReport)
            .order_by(
                CoinDailyReport.period_ended_at.desc().nullslast(),
                CoinDailyReport.created_at.desc(),
            )
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_latest_before(
        self,
        period_end_at: datetime,
        report_sources: Iterable[str] | None = None,
    ) -> CoinDailyReport | None:
        """주어진 시각 이전의 최신 리포트를 반환한다."""
        stmt = (
            select(CoinDailyReport)
            .where(CoinDailyReport.period_ended_at.is_not(None))
            .where(CoinDailyReport.period_ended_at < period_end_at)
        )
        normalized_sources = self._normalize_report_sources(report_sources)
        if normalized_sources:
            stmt = stmt.where(CoinDailyReport.report_source.in_(normalized_sources))

        result = await self.db.execute(
            stmt.order_by(CoinDailyReport.period_ended_at.desc(), CoinDailyReport.created_at.desc()).limit(1)
        )
        return result.scalars().first()

    async def get_by_applied_cycle_id(self, cycle_id: str) -> CoinDailyReport | None:
        """같은 적용 사이클로 이미 생성된 리포트를 조회한다."""
        return await self.filter_by_one(applied_cycle_id=cycle_id)
