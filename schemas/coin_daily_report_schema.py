"""코인 체크포인트 리포트 응답 스키마"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class CoinDailyReportResponse(BaseModel):
    """코인 체크포인트 리포트 응답 모델"""

    id: str
    market_scope: str
    report_date: date
    report_source: str
    trigger_reason: str | None = None
    applied_cycle_id: str | None = None
    period_started_at: datetime | None = None
    period_ended_at: datetime | None = None
    total_cycles: int
    total_analyses: int
    total_recommendations: int
    total_orders: int
    buy_count: int = 0
    sell_count: int = 0
    win_count: int
    loss_count: int
    total_pnl: float
    unrealized_pnl: float = 0.0
    open_position_count: int = 0
    total_24h_volume: float = 0.0
    btc_dominance: float | None = None
    market_regime: str = ""
    market_summary: str | None = None
    performance_review: str | None = None
    lessons_learned: str | None = None
    next_day_plan: str | None = None
    top_picks: str | None = None
    strategy_stats: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
