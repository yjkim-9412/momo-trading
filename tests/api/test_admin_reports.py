from datetime import date

import pytest

from models.daily_report import DailyReport
from services.daily_report_service import daily_report_service
from tests.conftest import TestAsyncSessionLocal


@pytest.mark.asyncio
async def test_admin_reports_latest_respects_market_scope(client):
    report_date = date(2026, 3, 13)

    async with TestAsyncSessionLocal() as session:
        async with session.begin():
            session.add(
                DailyReport(
                    market_scope="KRX",
                    report_date=report_date,
                    total_cycles=1,
                    total_analyses=2,
                    total_recommendations=3,
                    total_orders=4,
                    buy_count=1,
                    sell_count=1,
                    win_count=1,
                    loss_count=0,
                    total_pnl=1000.0,
                )
            )
            session.add(
                DailyReport(
                    market_scope="US",
                    report_date=report_date,
                    total_cycles=5,
                    total_analyses=6,
                    total_recommendations=7,
                    total_orders=8,
                    buy_count=2,
                    sell_count=2,
                    win_count=2,
                    loss_count=1,
                    total_pnl=2500.0,
                )
            )

    resp = await client.get("/api/v1/admin/reports/latest?market_scope=NASDAQ")
    data = resp.json()["data"]

    assert resp.status_code == 200
    assert data["market_scope"] == "US"
    assert data["total_cycles"] == 5


@pytest.mark.asyncio
async def test_admin_reports_list_filters_by_market_scope(client):
    report_date = date(2026, 3, 12)

    async with TestAsyncSessionLocal() as session:
        async with session.begin():
            session.add(
                DailyReport(
                    market_scope="KRX",
                    report_date=report_date,
                    total_cycles=9,
                    total_analyses=0,
                    total_recommendations=0,
                    total_orders=0,
                    win_count=0,
                    loss_count=0,
                    total_pnl=0.0,
                )
            )

    resp = await client.get("/api/v1/admin/reports?market_scope=KRX&limit=20")
    data = resp.json()["data"]

    assert resp.status_code == 200
    assert all(item["market_scope"] == "KRX" for item in data)


@pytest.mark.asyncio
async def test_admin_report_confirm_refresh_accepts_json_body(client, monkeypatch):
    refreshed = DailyReport(
        market_scope="KRX",
        report_date=date(2026, 3, 16),
        total_cycles=7,
        total_analyses=8,
        total_recommendations=9,
        total_orders=10,
        buy_count=4,
        sell_count=3,
        win_count=2,
        loss_count=1,
        total_pnl=12345.0,
    )

    async def fake_regenerate(report_date, market_scope=None):
        return {
            "existing": None,
            "refreshed": refreshed,
            "recommendation": "refreshed",
            "comparison": {},
        }

    async def fake_apply(report_date, market_scope, refreshed_data):
        _ = refreshed_data
        return refreshed

    monkeypatch.setattr(daily_report_service, "regenerate_daily_report", fake_regenerate)
    monkeypatch.setattr(daily_report_service, "apply_refreshed_report", fake_apply)

    resp = await client.post(
        "/api/v1/admin/reports/confirm-refresh",
        json={
            "report_date": "2026-03-16",
            "market_scope": "KRX",
            "choice": "refreshed",
        },
    )
    data = resp.json()["data"]

    assert resp.status_code == 200
    assert data["report_date"] == "2026-03-16"
    assert data["market_scope"] == "KRX"
    assert data["total_pnl"] == 12345.0
