from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete

from agent.trading_agent import trading_agent
from agent.trading_agent._types import MarketState
from models.coin_activity_log import CoinActivityLog
from models.coin_daily_report import CoinDailyReport
from services.coin_daily_report_service import CoinReportGenerationError
from tests.conftest import TestAsyncSessionLocal


@pytest.mark.asyncio
async def test_admin_coin_reports_generate_uses_checkpoint_service_and_refreshes_rules(client):
    runtime = MarketState(
        scope="CRYPTO",
        market_regime="ALTSEASON",
        market_context="최근 회고 기반 다음 사이클 우선순위",
    )
    report = SimpleNamespace(
        id="coin-report-1",
        market_scope="CRYPTO",
        report_date=date(2026, 3, 15),
        report_source="MANUAL",
        trigger_reason="manual_generate",
        applied_cycle_id=None,
        period_started_at=datetime(2026, 3, 15, 0, 0, tzinfo=timezone.utc),
        period_ended_at=datetime(2026, 3, 15, 4, 0, tzinfo=timezone.utc),
        total_cycles=1,
        total_analyses=3,
        total_recommendations=2,
        total_orders=1,
        buy_count=1,
        sell_count=0,
        win_count=0,
        loss_count=0,
        total_pnl=15000.0,
        unrealized_pnl=3000.0,
        open_position_count=1,
        total_24h_volume=1000000000.0,
        btc_dominance=54.2,
        market_regime="ALTSEASON",
        market_summary="수동 리포트 테스트",
        performance_review="성과 유지",
        lessons_learned="BTC 중심 순환 대응",
        next_day_plan="다음 사이클에서도 BTC 우선",
        top_picks="[]",
        strategy_stats="{}",
        created_at=datetime(2026, 3, 15, 4, 1, tzinfo=timezone.utc),
    )

    with patch.object(trading_agent, "get_runtime", return_value=runtime), \
            patch(
                "services.coin_daily_report_service.coin_daily_report_service.generate_checkpoint_report",
                AsyncMock(return_value=report),
            ) as generate_report, \
            patch.object(
                trading_agent,
                "refresh_runtime_trading_rules",
                AsyncMock(return_value={"rules": []}),
            ) as refresh_rules:
        resp = await client.post("/api/v1/admin-coin/reports/generate")

    assert resp.status_code == 200
    body = resp.json()
    assert "리포트 생성 완료" in body["message"]
    assert body["data"]["report_source"] == "MANUAL"
    assert body["data"]["market_scope"] == "CRYPTO"
    assert body["data"]["trigger_reason"] == "manual_generate"

    generate_report.assert_awaited_once_with(
        market="BITHUMB",
        report_source="MANUAL",
        trigger_reason="manual_generate",
        market_regime="ALTSEASON",
        market_context="최근 회고 기반 다음 사이클 우선순위",
        period_anchor_sources={"AUTO_SETTLEMENT"},
        raise_on_error=True,
    )
    refresh_rules.assert_awaited_once_with(market="CRYPTO", emit_activity=True)


@pytest.mark.asyncio
async def test_admin_coin_reports_generate_returns_failure_message_when_market_overview_is_unavailable(client):
    runtime = MarketState(scope="CRYPTO", market_regime="ALTSEASON", market_context="최근 회고")

    with patch.object(trading_agent, "get_runtime", return_value=runtime), \
            patch(
                "services.coin_daily_report_service.coin_daily_report_service.generate_checkpoint_report",
                AsyncMock(
                    side_effect=CoinReportGenerationError(
                        "시장 개요 조회 실패",
                        user_message="시장 개요 조회 실패로 코인 리포트를 생성하지 않았습니다: DNS 해석 실패",
                    )
                ),
            ) as generate_report, \
            patch.object(
                trading_agent,
                "refresh_runtime_trading_rules",
                AsyncMock(return_value={"rules": []}),
            ) as refresh_rules:
        resp = await client.post("/api/v1/admin-coin/reports/generate")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] is None
    assert "시장 개요 조회 실패" in body["message"]
    refresh_rules.assert_not_awaited()
    generate_report.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_coin_reports_latest_and_list_use_period_ended_at_order(client):
    async with TestAsyncSessionLocal() as session:
        async with session.begin():
            await session.execute(delete(CoinActivityLog))
            await session.execute(delete(CoinDailyReport))
            session.add_all(
                [
                    CoinDailyReport(
                        id="coin-report-old",
                        report_date=date(2026, 3, 15),
                        report_source="AUTO_PRE_CYCLE",
                        period_started_at=datetime(2026, 3, 15, 0, 0),
                        period_ended_at=datetime(2026, 3, 15, 4, 0),
                        total_cycles=1,
                        total_analyses=2,
                        total_recommendations=1,
                        total_orders=0,
                        win_count=0,
                        loss_count=0,
                        total_pnl=1000.0,
                    ),
                    CoinDailyReport(
                        id="coin-report-new",
                        report_date=date(2026, 3, 15),
                        report_source="MANUAL",
                        period_started_at=datetime(2026, 3, 15, 4, 0),
                        period_ended_at=datetime(2026, 3, 15, 8, 0),
                        total_cycles=2,
                        total_analyses=3,
                        total_recommendations=2,
                        total_orders=1,
                        win_count=1,
                        loss_count=0,
                        total_pnl=2500.0,
                    ),
                ]
            )

    latest_resp = await client.get("/api/v1/admin-coin/reports/latest")
    list_resp = await client.get("/api/v1/admin-coin/reports?limit=30")

    assert latest_resp.status_code == 200
    assert latest_resp.json()["data"]["id"] == "coin-report-new"

    assert list_resp.status_code == 200
    assert [item["id"] for item in list_resp.json()["data"][:2]] == [
        "coin-report-new",
        "coin-report-old",
    ]


@pytest.mark.asyncio
async def test_admin_coin_report_activities_filters_to_selected_period(client):
    async with TestAsyncSessionLocal() as session:
        async with session.begin():
            await session.execute(delete(CoinActivityLog))
            await session.execute(delete(CoinDailyReport))
            session.add(
                CoinDailyReport(
                    id="coin-report-period",
                    report_date=date(2026, 3, 15),
                    report_source="AUTO_PRE_CYCLE",
                    period_started_at=datetime(2026, 3, 15, 4, 0),
                    period_ended_at=datetime(2026, 3, 15, 8, 0),
                    total_cycles=1,
                    total_analyses=2,
                    total_recommendations=1,
                    total_orders=0,
                    win_count=0,
                    loss_count=0,
                    total_pnl=0.0,
                )
            )
            session.add_all(
                [
                    CoinActivityLog(
                        id="coin-activity-in-window",
                        trading_date=date(2026, 3, 15),
                        cycle_id="cycle-in",
                        activity_type="ANALYSIS",
                        phase="COMPLETE",
                        symbol="BTC",
                        summary="리포트 기간 내 활동",
                        detail="{}",
                        created_at=datetime(2026, 3, 15, 6, 0),
                    ),
                    CoinActivityLog(
                        id="coin-activity-before-window",
                        trading_date=date(2026, 3, 15),
                        cycle_id="cycle-before",
                        activity_type="ANALYSIS",
                        phase="COMPLETE",
                        symbol="ETH",
                        summary="리포트 시작 전 활동",
                        detail="{}",
                        created_at=datetime(2026, 3, 15, 3, 59),
                    ),
                    CoinActivityLog(
                        id="coin-activity-after-window",
                        trading_date=date(2026, 3, 15),
                        cycle_id="cycle-after",
                        activity_type="ANALYSIS",
                        phase="COMPLETE",
                        symbol="XRP",
                        summary="리포트 종료 후 활동",
                        detail="{}",
                        created_at=datetime(2026, 3, 15, 8, 1),
                    ),
                ]
            )

    resp = await client.get("/api/v1/admin-coin/reports/coin-report-period/activities?limit=500")

    assert resp.status_code == 200
    assert [item["id"] for item in resp.json()["data"]] == ["coin-activity-in-window"]
