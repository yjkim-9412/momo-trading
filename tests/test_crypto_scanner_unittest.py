import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agent.crypto_scanner import CryptoScanner
from trading.models import AccountBalance, MCPResponse


def _balance() -> AccountBalance:
    return AccountBalance(
        total_asset=1_000_000,
        cash=500_000,
        raw_cash=500_000,
        effective_cash=500_000,
        cash_source="BROKER",
        stock_value=500_000,
        total_pnl=0,
        total_pnl_rate=0,
        market="BITHUMB",
        currency="KRW",
        exchange_rate_to_krw=1.0,
    )


@pytest.mark.asyncio
async def test_scan_reuses_single_overview_payload_for_ranked_views():
    scanner = CryptoScanner()
    overview = MCPResponse(
        success=True,
        data={
            "items": [
                {
                    "symbol": "BTC",
                    "name": "비트코인",
                    "market": "BITHUMB",
                    "price": 150000000,
                    "change_rate": 2.0,
                    "volume": 1000.0,
                    "trade_value": 1000000.0,
                },
                {
                    "symbol": "ETH",
                    "name": "이더리움",
                    "market": "BITHUMB",
                    "price": 5000000,
                    "change_rate": 4.5,
                    "volume": 800.0,
                    "trade_value": 900000.0,
                },
            ]
        },
    )
    volume_rank = MCPResponse(success=True, data={"items": overview.data["items"]})
    surge_data = MCPResponse(success=True, data={"items": overview.data["items"]})
    client = SimpleNamespace(
        get_market_overview=AsyncMock(return_value=overview),
        get_volume_rank=AsyncMock(return_value=volume_rank),
        get_surge_data=AsyncMock(return_value=surge_data),
        get_current_price=AsyncMock(),
    )

    with patch("agent.crypto_scanner._get_bithumb_client", return_value=client), patch.object(
        scanner,
        "_get_performance_summary",
        AsyncMock(return_value="매매 이력 없음"),
    ), patch(
        "agent.crypto_scanner.llm_factory.generate_tier1",
        AsyncMock(
            return_value=(
                '{"selected": [], "market_regime": "SIDEWAYS", "market_analysis": "관망"}',
                "TEST",
            )
        ),
    ), patch("agent.crypto_scanner.activity_logger.log", AsyncMock()):
        result = await scanner.scan(
            market="BITHUMB",
            cycle_id="cycle-1",
            account_snapshot=(_balance(), []),
        )

    client.get_market_overview.assert_awaited_once()
    assert client.get_volume_rank.await_args.kwargs["overview_data"] is overview
    assert client.get_surge_data.await_args.kwargs["overview_data"] is overview
    assert result["provider"] == "TEST"


@pytest.mark.asyncio
async def test_scan_returns_market_data_unavailable_without_llm_call():
    scanner = CryptoScanner()
    failure = MCPResponse(success=False, error="non_json_response")
    client = SimpleNamespace(
        get_market_overview=AsyncMock(return_value=failure),
        get_volume_rank=AsyncMock(return_value=failure),
        get_surge_data=AsyncMock(return_value=failure),
        get_current_price=AsyncMock(return_value=MCPResponse(success=False, error="ticker unavailable")),
    )

    with patch.object(type(scanner), "_build_watchlist_fallback_coins", AsyncMock(return_value=[])), patch(
        "agent.crypto_scanner._get_bithumb_client",
        return_value=client,
    ), patch.object(
        scanner,
        "_get_performance_summary",
        AsyncMock(return_value="매매 이력 없음"),
    ), patch(
        "agent.crypto_scanner.llm_factory.generate_tier1",
        AsyncMock(),
    ) as generate_tier1, patch(
        "agent.crypto_scanner.activity_logger.log",
        AsyncMock(),
    ), patch(
        "agent.crypto_scanner.settings.CRYPTO_WATCHLIST_SYMBOLS",
        "",
    ):
        result = await scanner.scan(
            market="BITHUMB",
            cycle_id="cycle-1",
            account_snapshot=(_balance(), []),
        )

    generate_tier1.assert_not_awaited()
    assert result["selected"] == []
    assert result["market_summary"] == "시장 데이터 unavailable"
