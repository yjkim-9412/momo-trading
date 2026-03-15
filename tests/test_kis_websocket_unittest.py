from unittest.mock import AsyncMock

import pytest

from trading.kis_websocket import KISWebSocket


def _build_raw_message(tr_id: str, records: list[list[str]]) -> str:
    flattened = "^".join(value for record in records for value in record)
    return f"0|{tr_id}|{len(records):03d}|{flattened}"


@pytest.mark.asyncio
async def test_handle_message_parses_overseas_single_record_26_fields():
    ws = KISWebSocket()
    received: list[dict] = []
    ws.set_on_price(AsyncMock(side_effect=lambda payload: received.append(payload)))

    record = [
        "DNASNVDA",
        "NVDA",
        "4",
        "20260313",
        "20260313",
        "185434",
        "20260313",
        "185434",
        "180.5000",
        "182.7400",
        "179.3300",
        "323.2100",
        "2",
        "1.8500",
        "0.5800",
        "323.2000",
        "323.2200",
        "6592",
        "21260",
        "321",
        "456789",
        "147200000",
        "5000",
        "4500",
        "112.30",
        "1",
    ]

    await ws._handle_message(
        _build_raw_message("HDFSCNT0", [record]),
        "overseas",
        AsyncMock(),
    )

    assert received == [{
        "market": "NASDAQ",
        "symbol": "NVDA",
        "session": "US_DELAYED",
        "currency": "USD",
        "time": "185434",
        "price": 323.21,
        "change": 1.85,
        "change_rate": 0.58,
        "volume": 321,
        "cumulative_volume": 456789,
    }]


@pytest.mark.asyncio
async def test_handle_message_splits_overseas_multi_record_payload():
    ws = KISWebSocket()
    received: list[dict] = []
    ws.set_on_price(AsyncMock(side_effect=lambda payload: received.append(payload)))

    record_1 = [
        "DNASNVDA", "NVDA", "4", "20260313", "20260313", "185434", "20260313", "185434",
        "180.5000", "182.7400", "179.3300", "323.2100", "2", "1.8500", "0.5800",
        "323.2000", "323.2200", "6592", "21260", "321", "456789", "147200000",
        "5000", "4500", "112.30", "1",
    ]
    record_2 = [
        "DNASTSLA", "TSLA", "4", "20260313", "20260313", "185504", "20260313", "185504",
        "183.0000", "395.3300", "182.0000", "184.5800", "5", "-1.5900", "-0.8500",
        "184.5600", "184.6000", "3630", "6645", "145", "99012", "88000000",
        "2200", "1800", "98.70", "1",
    ]
    record_3 = [
        "RBAQPLTR", "PLTR", "4", "20260313", "20260313", "105723", "20260313", "225723",
        "52.0000", "54.4800", "52.9900", "53.6100", "2", "0.3800", "0.7100",
        "53.6000", "53.6200", "3994", "2176", "87", "30810", "15000000",
        "1200", "1300", "101.20", "1",
    ]

    await ws._handle_message(
        _build_raw_message("HDFSCNT0", [record_1, record_2, record_3]),
        "overseas",
        AsyncMock(),
    )

    assert [item["symbol"] for item in received] == ["NVDA", "TSLA", "PLTR"]
    assert [item["price"] for item in received] == [323.21, 184.58, 53.61]
    assert [item["volume"] for item in received] == [321, 145, 87]


@pytest.mark.asyncio
async def test_handle_message_supports_overseas_25_field_fallback():
    ws = KISWebSocket()
    received: list[dict] = []
    ws.set_on_price(AsyncMock(side_effect=lambda payload: received.append(payload)))

    record = [
        "RBAQNVDA",
        "4",
        "20260313",
        "20260313",
        "105959",
        "20260313",
        "225959",
        "180.5000",
        "182.7400",
        "179.3300",
        "323.2100",
        "2",
        "1.8500",
        "0.5800",
        "323.2000",
        "323.2200",
        "6592",
        "21260",
        "321",
        "456789",
        "147200000",
        "5000",
        "4500",
        "112.30",
        "1",
    ]

    await ws._handle_message(
        _build_raw_message("HDFSCNT0", [record]),
        "overseas",
        AsyncMock(),
    )

    assert received == [{
        "market": "NASDAQ",
        "symbol": "NVDA",
        "session": "US_DAYTIME",
        "currency": "USD",
        "time": "225959",
        "price": 323.21,
        "change": 1.85,
        "change_rate": 0.58,
        "volume": 321,
        "cumulative_volume": 456789,
    }]


@pytest.mark.asyncio
async def test_handle_message_ignores_malformed_record_count():
    ws = KISWebSocket()
    callback = AsyncMock()
    ws.set_on_price(callback)

    record = [
        "DNASNVDA", "NVDA", "4", "20260313", "20260313", "185434", "20260313", "185434",
        "180.5000", "182.7400", "179.3300", "323.2100", "2", "1.8500", "0.5800",
        "323.2000", "323.2200", "6592", "21260", "321", "456789", "147200000",
        "5000", "4500", "112.30", "1",
    ]
    malformed = f"0|HDFSCNT0|002|{'^'.join(record)}"

    await ws._handle_message(malformed, "overseas", AsyncMock())

    callback.assert_not_called()


@pytest.mark.asyncio
async def test_handle_message_keeps_domestic_mapping_with_official_record_size():
    ws = KISWebSocket()
    received: list[dict] = []
    ws.set_on_price(AsyncMock(side_effect=lambda payload: received.append(payload)))

    record = [
        "005930", "123929", "73100", "5", "100", "0.14", "73050", "73000", "73200", "72900",
        "73100", "73000", "15", "123456", "987654321", "10", "20", "10", "105.3", "10000",
        "9000", "1", "55.0", "12.4", "090000", "1", "100", "123000", "1", "200", "122500",
        "1", "50", "20260313", "1", "N", "800", "700", "5000", "4500", "3.2", "100000",
        "12.0", "1", "0", "72000",
    ]

    await ws._handle_message(
        _build_raw_message("H0STCNT0", [record]),
        "domestic",
        AsyncMock(),
    )

    assert received == [{
        "market": "KRX",
        "symbol": "005930",
        "time": "123929",
        "price": 73100.0,
        "change": 100.0,
        "change_rate": 0.14,
        "volume": 15,
        "cumulative_volume": 123456,
    }]
