import asyncio
import unittest
from datetime import timedelta
from unittest.mock import patch

import realtime.stream_manager as stream_module
from realtime.stream_manager import StreamManager


class DummyWebSocket:
    def __init__(self):
        self.requested: set[tuple[str, str]] = set()
        self.confirmed: set[tuple[str, str]] = set()
        self.is_connected = True
        self.connect_calls = 0
        self.last_business_error = None
        self.last_system_message_at = None
        self.fatal_error = None

    @property
    def subscription_count(self) -> int:
        return len(self.confirmed)

    @property
    def requested_subscription_keys(self) -> set[tuple[str, str]]:
        return set(self.requested)

    @property
    def confirmed_subscription_keys(self) -> set[tuple[str, str]]:
        return set(self.confirmed)

    async def subscribe(self, symbol: str, market: str = "KRX") -> bool:
        self.is_connected = True
        key = (market, symbol.upper())
        self.requested.add(key)
        self.confirmed.add(key)
        return True

    async def unsubscribe(self, symbol: str, market: str = "KRX") -> None:
        key = (market, symbol.upper())
        self.requested.discard(key)
        self.confirmed.discard(key)

    async def connect(self) -> None:
        self.connect_calls += 1
        self.is_connected = True
        return None

    async def disconnect(self) -> None:
        self.is_connected = False
        self.requested.clear()
        self.confirmed.clear()

    async def reset_runtime_state(self) -> None:
        self.is_connected = False
        self.requested.clear()
        self.confirmed.clear()
        self.last_business_error = None
        self.last_system_message_at = None
        self.fatal_error = None

    async def listen(self) -> None:
        await asyncio.sleep(3600)


class FlakyDummyWebSocket(DummyWebSocket):
    def __init__(self, connect_failures: int):
        super().__init__()
        self.connect_failures = connect_failures
        self.is_connected = False

    async def connect(self) -> None:
        self.connect_calls += 1
        if self.connect_calls <= self.connect_failures:
            raise ConnectionError("approval key failed")


@patch("scheduler.market_calendar.market_calendar.is_trading_hours", return_value=True)
class StreamManagerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._original_ws = stream_module.kis_websocket

    def tearDown(self):
        stream_module.kis_websocket = self._original_ws

    async def test_replace_market_subscriptions_keeps_other_scope(self, _mock_trading):
        dummy_ws = DummyWebSocket()
        stream_module.kis_websocket = dummy_ws
        manager = StreamManager()

        await manager.replace_market_subscriptions("KRX", [("005930", "KRX"), ("000660", "KRX")])
        await manager.replace_market_subscriptions("US", [("AAPL", "NASDAQ")])

        self.assertEqual(
            dummy_ws.confirmed,
            {
                ("KRX", "005930"),
                ("KRX", "000660"),
                ("NASDAQ", "AAPL"),
            },
        )

        await manager.replace_market_subscriptions("US", [("MSFT", "NASDAQ")])

        self.assertEqual(
            dummy_ws.confirmed,
            {
                ("KRX", "005930"),
                ("KRX", "000660"),
                ("NASDAQ", "MSFT"),
            },
        )

    async def test_ensure_symbol_adds_to_scope_without_reset(self, _mock_trading):
        dummy_ws = DummyWebSocket()
        stream_module.kis_websocket = dummy_ws
        manager = StreamManager()

        await manager.replace_market_subscriptions("KRX", [("005930", "KRX")])
        await manager.ensure_symbol("KRX", "000660", "KRX")

        self.assertEqual(
            dummy_ws.confirmed,
            {
                ("KRX", "005930"),
                ("KRX", "000660"),
            },
        )

    async def test_start_keeps_supervisor_alive_after_initial_connect_failure(self, _mock_trading):
        dummy_ws = FlakyDummyWebSocket(connect_failures=1)
        stream_module.kis_websocket = dummy_ws
        manager = StreamManager()

        await manager.start()

        status = manager.stream_status("US")
        self.assertTrue(status["running"])
        self.assertFalse(status["connected"])
        self.assertEqual(status["last_connect_error"], "approval key failed")

        await manager.replace_market_subscriptions("US", [("AAPL", "NASDAQ")])

        status = manager.stream_status("US")
        self.assertGreaterEqual(dummy_ws.connect_calls, 2)
        self.assertTrue(status["connected"])
        self.assertEqual(status["health"], "CONNECTED")
        self.assertEqual(status["desired_count"], 1)
        self.assertEqual(status["active_count"], 1)
        self.assertIn(("NASDAQ", "AAPL"), dummy_ws.confirmed)

        await manager.stop()

    async def test_stream_status_degrades_when_messages_missing_after_grace(self, _mock_trading):
        dummy_ws = DummyWebSocket()
        stream_module.kis_websocket = dummy_ws
        manager = StreamManager()

        manager._running = True
        await manager.replace_market_subscriptions("KRX", [("005930", "KRX")])
        manager._last_connect_at = manager._last_connect_at - timedelta(seconds=25)
        manager._last_message_at = None

        status = manager.stream_status("KRX")

        self.assertFalse(status["connected"])
        self.assertEqual(status["health"], "DEGRADED")
        self.assertEqual(status["status_reason"], "체결 수신 없음")

    async def test_stream_status_reports_error_on_business_error_without_confirmed_subscription(self, _mock_trading):
        dummy_ws = DummyWebSocket()
        stream_module.kis_websocket = dummy_ws
        manager = StreamManager()

        manager._running = True
        manager._desired_by_scope["KRX"] = {("KRX", "005930")}
        manager._requested_symbols = {("KRX", "005930"): stream_module.now_kst()}
        dummy_ws.requested.add(("KRX", "005930"))
        dummy_ws.confirmed.clear()
        dummy_ws.last_business_error = "OPSP8996: ALREADY IN USE appkey"

        status = manager.stream_status("KRX")

        self.assertFalse(status["connected"])
        self.assertEqual(status["health"], "ERROR")
        self.assertEqual(status["last_business_error"], "OPSP8996: ALREADY IN USE appkey")


if __name__ == "__main__":
    unittest.main()
