import json
from datetime import date, datetime
from unittest.mock import AsyncMock, patch

import pytest

from models.trade_result import TradeResult
from services.daily_report_service import daily_report_service
from tests.conftest import TestAsyncSessionLocal
from trading.models import AccountBalance, HoldingInfo


@pytest.mark.asyncio
async def test_regenerate_daily_report_uses_usd_metrics_for_us_scope():
    report_date = date(2026, 4, 1)
    captured = {}

    async with TestAsyncSessionLocal() as session:
        async with session.begin():
            session.add(
                TradeResult(
                    id="us-report-trade-1",
                    order_id="order-us-report-1",
                    stock_symbol="NVDA",
                    stock_name="NVIDIA",
                    currency="USD",
                    exchange_rate_to_krw=1506.2,
                    side="BUY",
                    strategy_type="STABLE_SHORT",
                    entry_price=100.0,
                    entry_price_krw=150620.0,
                    exit_price=110.0,
                    exit_price_krw=165682.0,
                    quantity=1,
                    raw_pnl=10.0,
                    pnl=15062.0,
                    return_pct=10.0,
                    is_win=True,
                    hold_days=0,
                    exit_reason="SIGNAL",
                    market="NASDAQ",
                    market_regime="BULLISH",
                    entry_at=datetime(2026, 4, 1, 9, 45),
                    exit_at=datetime(2026, 4, 1, 15, 55),
                )
            )

    balance = AccountBalance(
        total_asset=301240.0,
        total_asset_foreign=200.0,
        cash=150620.0,
        cash_foreign=100.0,
        stock_value=150620.0,
        stock_value_foreign=100.0,
        operating_cash=150620.0,
        operating_cash_foreign=100.0,
        total_pnl=15062.0,
        total_pnl_rate=10.0,
        raw_total_pnl=10.0,
        raw_total_pnl_rate=10.0,
        market="NASDAQ",
        currency="KRW",
        exchange_rate_to_krw=1506.2,
        raw_cash=150620.0,
        raw_cash_foreign=100.0,
        effective_cash=150620.0,
        effective_cash_foreign=100.0,
        cash_source="BROKER",
    )
    holdings = [
        HoldingInfo(
            symbol="NVDA",
            name="NVIDIA",
            market="NASDAQ",
            currency="USD",
            quantity=1,
            avg_buy_price=95.0,
            current_price=100.0,
            pnl=5.0,
            pnl_rate=5.26,
            exchange_rate_to_krw=1506.2,
        )
    ]

    async def fake_generate_tier1(prompt, **kwargs):
        captured["prompt"] = prompt
        _ = kwargs
        return (
            json.dumps(
                {
                    "market_summary": "USD summary",
                    "performance_review": "USD performance",
                    "lessons_learned": "USD lessons",
                    "next_day_plan": "USD plan",
                    "top_picks": ["NVDA"],
                },
                ensure_ascii=False,
            ),
            "TEST",
        )

    with patch("services.daily_report_service.AsyncSessionLocal", TestAsyncSessionLocal), \
            patch(
                "services.daily_report_service.account_manager.get_account_snapshot",
                AsyncMock(return_value=(balance, holdings)),
            ), \
            patch(
                "services.daily_report_service.llm_factory.generate_tier1",
                AsyncMock(side_effect=fake_generate_tier1),
            ), \
            patch("services.daily_report_service.activity_logger.log", AsyncMock()):
        result = await daily_report_service.regenerate_daily_report(
            report_date=report_date,
            market_scope="NASDAQ",
        )

    refreshed = result["refreshed"]
    prompt = captured["prompt"]

    assert refreshed.market_scope == "US"
    assert refreshed.report_currency == "USD"
    assert refreshed.total_pnl == 10.0
    assert refreshed.unrealized_pnl == 5.0
    assert refreshed.open_position_count == 1
    assert "기준 통화: USD" in prompt
    assert "실현 손익: +10.00 USD" in prompt
    assert "미실현 손익: +5.00 USD" in prompt
    assert "총자산: 200.00 USD" in prompt
    assert "현금: 100.00 USD" in prompt
    assert "주식 평가: 100.00 USD" in prompt
    assert "15062원" not in prompt
