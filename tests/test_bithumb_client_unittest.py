import httpx
import pytest
from unittest.mock import AsyncMock, patch

from trading.bithumb_client import BithumbClient
from trading.models import MCPResponse


def test_normalize_ticker_exposes_numeric_change_and_preserves_direction():
    normalized = BithumbClient._normalize_ticker(
        {
            "market": "KRW-BTC",
            "trade_price": "145000000",
            "change": "FALL",
            "change_price": "1250000",
            "signed_change_price": "-1250000",
            "signed_change_rate": "-0.0085",
        }
    )

    assert normalized["symbol"] == "BTC"
    assert normalized["change"] == -1250000.0
    assert normalized["change_direction"] == "FALL"
    assert normalized["change_price"] == 1250000.0
    assert normalized["signed_change_price"] == -1250000.0


@pytest.mark.asyncio
async def test_public_get_reports_http_status_and_body_for_non_json_response():
    client = BithumbClient()
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            502,
            text="<html>bad gateway</html>",
            headers={"content-type": "text/html"},
            request=request,
        )
    )
    mock_client = httpx.AsyncClient(transport=transport, base_url="https://api.bithumb.com")

    with patch.object(client, "_ensure_client", AsyncMock(return_value=mock_client)):
        response = await client._public_get("/v1/ticker", params={"markets": "KRW-BTC"})

    await mock_client.aclose()

    assert response.success is False
    assert "HTTP 502" in (response.error or "")
    assert "text/html" in (response.error or "")
    assert "bad gateway" in (response.error or "")


@pytest.mark.asyncio
async def test_get_volume_rank_reuses_overview_data_without_refetch():
    client = BithumbClient()
    overview = MCPResponse(
        success=True,
        data={
            "items": [
                {"symbol": "BTC", "trade_value": 100.0},
                {"symbol": "ETH", "trade_value": 300.0},
                {"symbol": "XRP", "trade_value": 200.0},
            ]
        },
    )

    with patch.object(client, "get_market_overview", AsyncMock()) as overview_mock:
        response = await client.get_volume_rank(overview_data=overview, limit=2)

    overview_mock.assert_not_awaited()
    assert response.success is True
    assert [item["symbol"] for item in response.data["items"]] == ["ETH", "XRP"]


@pytest.mark.asyncio
async def test_get_surge_data_reuses_overview_data_without_refetch():
    client = BithumbClient()
    overview = MCPResponse(
        success=True,
        data={
            "items": [
                {"symbol": "BTC", "change_rate": -2.0},
                {"symbol": "ETH", "change_rate": 7.5},
                {"symbol": "XRP", "change_rate": -9.0},
            ]
        },
    )

    with patch.object(client, "get_market_overview", AsyncMock()) as overview_mock:
        response = await client.get_surge_data(overview_data=overview, limit=2)

    overview_mock.assert_not_awaited()
    assert response.success is True
    assert [item["symbol"] for item in response.data["items"]] == ["XRP", "ETH"]


@pytest.mark.asyncio
async def test_get_account_balance_excludes_non_tradeable_assets_from_summary():
    client = BithumbClient()
    accounts = MCPResponse(
        success=True,
        data={
            "items": [
                {"currency": "KRW", "balance": "1000", "locked": "0"},
                {"currency": "BTC", "balance": "0.1", "locked": "0", "avg_buy_price": "120000000"},
                {"currency": "P", "balance": "75", "locked": "0", "avg_buy_price": "0"},
            ]
        },
    )

    with patch.object(client, "_private_request", AsyncMock(return_value=accounts)), patch.object(
        client,
        "_get_tradable_krw_symbols",
        AsyncMock(return_value=({"BTC"}, "live")),
    ), patch.object(
        client,
        "_fetch_coin_prices",
        AsyncMock(return_value={"BTC": 150000000.0}),
    ), patch("trading.bithumb_client.logger.warning") as warning_mock:
        response = await client.get_account_balance()

    assert response.success is True
    assert response.data["holdings_count"] == 1
    assert response.data["holdings_summary"] == [
        {
            "currency": "BTC",
            "balance": 0.1,
            "locked": 0.0,
            "avg_buy_price": 120000000.0,
            "current_price": 150000000.0,
            "coin_value_krw": 15000000.0,
        }
    ]
    assert response.data["stock_value"] == 15000000.0
    assert response.data["total_asset"] == 15001000.0
    assert warning_mock.call_count >= 1
    assert any(
        "빗썸 비거래성 자산 제외" in str(call.args[0])
        for call in warning_mock.call_args_list
    )


@pytest.mark.asyncio
async def test_get_holdings_degraded_filter_drops_zero_cost_zero_price_assets():
    client = BithumbClient()
    accounts = MCPResponse(
        success=True,
        data={
            "items": [
                {"currency": "KRW", "balance": "1000", "locked": "0"},
                {"currency": "P", "balance": "75", "locked": "0", "avg_buy_price": "0"},
            ]
        },
    )

    with patch.object(client, "_private_request", AsyncMock(return_value=accounts)), patch.object(
        client,
        "_get_tradable_krw_symbols",
        AsyncMock(return_value=(None, "degraded")),
    ), patch.object(
        client,
        "_fetch_coin_prices",
        AsyncMock(return_value={}),
    ), patch("trading.bithumb_client.logger.warning") as warning_mock:
        response = await client.get_holdings()

    assert response.success is True
    assert response.data == {"holdings": [], "count": 0}
    assert warning_mock.call_count >= 1
    assert any(
        "빗썸 비거래성 자산 제외" in str(call.args[0])
        for call in warning_mock.call_args_list
    )


@pytest.mark.asyncio
async def test_get_tradable_krw_symbols_uses_stale_cache_when_market_catalog_fails():
    client = BithumbClient()
    client._tradable_krw_symbols_cache = {"BTC"}
    client._tradable_krw_symbols_cached_at = 0.0

    with patch.object(
        client,
        "_public_get",
        AsyncMock(return_value=MCPResponse(success=False, error="dns failure")),
    ), patch("trading.bithumb_client.logger.warning") as warning_mock:
        symbols, source = await client._get_tradable_krw_symbols()

    assert symbols == {"BTC"}
    assert source == "stale_cache"
    assert warning_mock.call_count == 1
    assert "캐시 사용" in str(warning_mock.call_args.args[0])
