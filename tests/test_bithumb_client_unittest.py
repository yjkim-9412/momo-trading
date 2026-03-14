import base64
import json
import time
import httpx
import pytest
from unittest.mock import AsyncMock, patch

from trading.bithumb_client import BithumbClient
from trading.models import MCPResponse


def _decode_segment(segment: str) -> dict:
    padded = segment + "=" * (-len(segment) % 4)
    raw = base64.urlsafe_b64decode(padded.encode("utf-8"))
    return json.loads(raw.decode("utf-8"))


def test_build_jwt_encodes_hs256_header_and_payload():
    client = BithumbClient()
    client._api_key = "test-access"
    client._api_secret = "test-secret"

    token = client._build_jwt()

    header_segment, payload_segment, signature_segment = token.split(".")
    header = _decode_segment(header_segment)
    payload = _decode_segment(payload_segment)

    assert header == {"alg": "HS256", "typ": "JWT"}
    assert payload["access_key"] == "test-access"
    assert payload["nonce"]
    assert isinstance(payload["timestamp"], int)
    assert signature_segment


def test_build_jwt_includes_query_hash_when_query_string_exists():
    client = BithumbClient()
    client._api_key = "test-access"
    client._api_secret = "test-secret"

    token = client._build_jwt("market=KRW-BTC&limit=1")

    _header_segment, payload_segment, _signature_segment = token.split(".")
    payload = _decode_segment(payload_segment)

    assert payload["query_hash_alg"] == "SHA512"
    assert payload["query_hash"]


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


@pytest.mark.asyncio
async def test_get_ticker_snapshots_normalizes_multiple_symbols():
    client = BithumbClient()
    raw_items = [
        {
            "market": "KRW-BTC",
            "trade_price": "145000000",
            "signed_change_rate": "0.0125",
            "acc_trade_volume_24h": "100",
            "acc_trade_price_24h": "1000",
        },
        {
            "market": "KRW-ETH",
            "trade_price": "5000000",
            "signed_change_rate": "-0.034",
            "acc_trade_volume_24h": "200",
            "acc_trade_price_24h": "2000",
        },
    ]

    with patch.object(
        client,
        "_public_get",
        AsyncMock(return_value=MCPResponse(success=True, data={"items": raw_items})),
    ):
        response = await client.get_ticker_snapshots(["BTC", "ETH", "BTC"])

    assert response.success is True
    assert response.data["count"] == 2
    assert response.data["symbols"] == ["BTC", "ETH"]
    assert [item["symbol"] for item in response.data["items"]] == ["BTC", "ETH"]


@pytest.mark.asyncio
async def test_get_discovery_universe_returns_fresh_cache_without_live_refresh():
    client = BithumbClient()
    client._discovery_universe_cache = [{"symbol": "BTC", "trade_value": 1000.0}]
    client._discovery_universe_cached_at = time.monotonic()
    client._discovery_universe_cached_at_epoch = time.time()

    with patch.object(client, "get_market_overview", AsyncMock()) as overview_mock:
        response = await client.get_discovery_universe()

    overview_mock.assert_not_awaited()
    assert response.success is True
    assert response.data["cache_source"] == "cache"
    assert response.data["items"][0]["symbol"] == "BTC"


@pytest.mark.asyncio
async def test_get_discovery_universe_refreshes_live_and_updates_cache():
    client = BithumbClient()
    overview = MCPResponse(
        success=True,
        data={
            "items": [
                {"symbol": "ETH", "trade_value": 200.0},
                {"symbol": "BTC", "trade_value": 300.0},
            ]
        },
    )

    with patch.object(client, "get_market_overview", AsyncMock(return_value=overview)):
        response = await client.get_discovery_universe(force_refresh=True)

    assert response.success is True
    assert response.data["cache_source"] == "live"
    assert [item["symbol"] for item in response.data["items"]] == ["BTC", "ETH"]
    assert [item["symbol"] for item in client._discovery_universe_cache] == ["BTC", "ETH"]


@pytest.mark.asyncio
async def test_get_discovery_universe_uses_stale_cache_when_live_refresh_fails():
    client = BithumbClient()
    client._discovery_universe_cache = [{"symbol": "BTC", "trade_value": 1000.0}]
    client._discovery_universe_cached_at = 0.0
    client._discovery_universe_cached_at_epoch = time.time() - 10_000

    with patch.object(
        client,
        "get_market_overview",
        AsyncMock(return_value=MCPResponse(success=False, error="dns failure")),
    ), patch("trading.bithumb_client.logger.warning") as warning_mock:
        response = await client.get_discovery_universe()

    assert response.success is True
    assert response.data["cache_source"] == "stale_cache"
    assert response.data["last_error"] == "dns failure"
    assert warning_mock.call_count == 1
