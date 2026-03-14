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
