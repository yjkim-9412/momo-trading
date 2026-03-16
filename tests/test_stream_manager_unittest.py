import asyncio
import unittest
from unittest.mock import patch

import realtime.stream_manager as stream_module
from realtime.stream_manager import StreamManager


class DummyWebSocket:
    def __init__(self):
        self.subscribed: set[tuple[str, str]] = set()
        self.is_connected = True
        self.connect_calls = 0

    @property
    def subscription_count(self) -> int:
        return len(self.subscribed)

    async def subscribe(self, symbol: str, market: str = "KRX") -> bool:
        self.is_connected = True
        self.subscribed.add((market, symbol.upper()))
        return True

    async def unsubscribe(self, symbol: str, market: str = "KRX") -> None:
        self.subscribed.discard((market, symbol.upper()))

    async def connect(self) -> None:
        self.connect_calls += 1
        return None

    async def disconnect(self) -> None:
        self.is_connected = False
        self.subscribed.clear()

    async def reset_runtime_state(self) -> None:
        self.is_connected = False
        self.subscribed.clear()

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
            dummy_ws.subscribed,
            {
                ("KRX", "005930"),
                ("KRX", "000660"),
                ("NASDAQ", "AAPL"),
            },
        )

        await manager.replace_market_subscriptions("US", [("MSFT", "NASDAQ")])

        self.assertEqual(
            dummy_ws.subscribed,
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
            dummy_ws.subscribed,
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
        self.assertEqual(status["desired_count"], 1)
        self.assertEqual(status["active_count"], 1)
        self.assertIn(("NASDAQ", "AAPL"), dummy_ws.subscribed)

        await manager.stop()


if __name__ == "__main__":
    unittest.main()
