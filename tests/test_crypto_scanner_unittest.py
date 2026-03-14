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
                '{"selected": [], "market_regime": "CONSOLIDATION", "market_analysis": "관망"}',
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


@pytest.mark.asyncio
async def test_scan_enriches_selected_candidates_with_discovery_source():
    scanner = CryptoScanner()
    items = []
    for idx in range(16):
        items.append(
            {
                "symbol": f"N{idx:02d}",
                "name": f"하락코인{idx:02d}",
                "market": "BITHUMB",
                "price": 1_000 + idx,
                "change_rate": float(-20 + idx),
                "volume": float(10_000 - idx),
                "trade_value": float(100_000 - idx),
            }
        )
    for idx in range(15):
        items.append(
            {
                "symbol": f"P{idx:02d}",
                "name": f"상승코인{idx:02d}",
                "market": "BITHUMB",
                "price": 2_000 + idx,
                "change_rate": float(20 - idx),
                "volume": float(20_000 - idx),
                "trade_value": float(200_000 - idx),
            }
        )
    items.append(
        {
            "symbol": "DOGE",
            "name": "도지코인",
            "market": "BITHUMB",
            "price": 500.0,
            "change_rate": 0.1,
            "volume": 1.0,
            "trade_value": 1.0,
        }
    )
    overview = MCPResponse(success=True, data={"items": items})
    volume_rank = MCPResponse(success=True, data={"items": items[:15]})
    surge_data = MCPResponse(success=True, data={"items": items[:15]})
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
                '{"selected":[{"symbol":"P00","name":"상승코인00","strategy_type":"AGGRESSIVE_SHORT","reason":"거래량 우위"},{"symbol":"DOGE","name":"도지코인","strategy_type":"STABLE_SHORT","reason":"고정 감시"}],"market_regime":"CONSOLIDATION","market_analysis":"혼조"}',
                "TEST",
            )
        ),
    ), patch("agent.crypto_scanner.activity_logger.log", AsyncMock()), patch(
        "agent.crypto_scanner.settings.CRYPTO_WATCHLIST_SYMBOLS",
        "DOGE",
    ), patch(
        "agent.crypto_scanner.settings.CRYPTO_DYNAMIC_DISCOVERY_ENABLED",
        True,
    ):
        result = await scanner.scan(
            market="BITHUMB",
            cycle_id="cycle-1",
            account_snapshot=(_balance(), []),
        )

    selected = {item["symbol"]: item for item in result["selected"]}
    assert selected["P00"]["scan_source"] == "DISCOVERY"
    assert selected["DOGE"]["scan_source"] == "WATCHLIST"
    assert result["selected_count_by_source"] == {"DISCOVERY": 1, "WATCHLIST": 1}


@pytest.mark.asyncio
async def test_scan_uses_watchlist_only_when_discovery_disabled():
    scanner = CryptoScanner()
    overview_items = [
        {
            "symbol": "SOL",
            "name": "솔라나",
            "market": "BITHUMB",
            "price": 220000,
            "change_rate": 9.0,
            "volume": 9000.0,
            "trade_value": 9000.0,
        },
        {
            "symbol": "BTC",
            "name": "비트코인",
            "market": "BITHUMB",
            "price": 150000000,
            "change_rate": 1.0,
            "volume": 1000.0,
            "trade_value": 1000.0,
        },
    ]
    overview = MCPResponse(success=True, data={"items": overview_items})
    client = SimpleNamespace(
        get_market_overview=AsyncMock(return_value=overview),
        get_volume_rank=AsyncMock(return_value=MCPResponse(success=True, data={"items": overview_items})),
        get_surge_data=AsyncMock(return_value=MCPResponse(success=True, data={"items": overview_items})),
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
                '{"selected":[{"symbol":"BTC","name":"비트코인","strategy_type":"STABLE_SHORT","reason":"고정 감시"}],"market_regime":"SIDEWAYS","market_analysis":"watchlist only"}',
                "TEST",
            )
        ),
    ) as generate_tier1, patch("agent.crypto_scanner.activity_logger.log", AsyncMock()), patch(
        "agent.crypto_scanner.settings.CRYPTO_WATCHLIST_SYMBOLS",
        "BTC",
    ), patch(
        "agent.crypto_scanner.settings.CRYPTO_DYNAMIC_DISCOVERY_ENABLED",
        False,
    ):
        result = await scanner.scan(
            market="BITHUMB",
            cycle_id="cycle-1",
            account_snapshot=(_balance(), []),
        )

    prompt = generate_tier1.await_args.args[0]
    assert "비트코인(BTC)" in prompt
    assert "솔라나(SOL)" not in prompt
    assert "보유 코인 없음" in prompt
    assert "P(P)" not in prompt
    assert result["market_regime"] == "CONSOLIDATION"
    assert result["selected"][0]["scan_source"] == "WATCHLIST"


@pytest.mark.asyncio
async def test_build_watchlist_fallback_coins_tags_holding_and_watchlist_sources():
    scanner = CryptoScanner()
    client = SimpleNamespace(
        get_current_price=AsyncMock(
            side_effect=[
                MCPResponse(success=True, data={"name": "리플", "price": 3000, "change_rate": -1.5, "trade_value": 5000}),
                MCPResponse(success=True, data={"name": "비트코인", "price": 150000000, "change_rate": 2.0, "trade_value": 9000}),
            ]
        )
    )

    coins = await scanner._build_watchlist_fallback_coins(
        client,
        watchlist=["BTC"],
        holdings=[SimpleNamespace(symbol="XRP")],
    )

    by_symbol = {item["symbol"]: item for item in coins}
    assert by_symbol["XRP"]["scan_source"] == "HOLDING_FALLBACK"
    assert by_symbol["BTC"]["scan_source"] == "WATCHLIST_FALLBACK"
