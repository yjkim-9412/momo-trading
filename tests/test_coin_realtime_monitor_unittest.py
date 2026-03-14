import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from realtime.coin_monitor import CoinRealtimeMonitor


class CoinRealtimeMonitorTest(unittest.IsolatedAsyncioTestCase):
    async def test_account_changed_events_are_coalesced(self):
        monitor = CoinRealtimeMonitor()
        monitor._ACCOUNT_CHANGE_DEBOUNCE_SECONDS = 0.01

        with patch("api.routes.admin_coin.coin_sse_manager.broadcast", AsyncMock()) as broadcast_mock:
            await monitor._schedule_account_change(
                reason="order_update",
                symbol="BTC",
                order_id="order-1",
                status="PARTIAL",
            )
            await monitor._schedule_account_change(
                reason="asset_update",
                changed_currencies=["KRW", "BTC"],
                asset_timestamp="1741921501000",
            )
            await asyncio.sleep(0.03)

        broadcast_mock.assert_awaited_once()
        payload = broadcast_mock.await_args.args[0]
        self.assertEqual(payload["type"], "account_changed")
        self.assertEqual(payload["order_id"], "order-1")
        self.assertEqual(payload["status"], "PARTIAL")
        self.assertEqual(payload["symbols"], ["BTC"])
        self.assertEqual(payload["changed_currencies"], ["BTC", "KRW"])
        self.assertEqual(payload["reason"], "asset_update,order_update")


if __name__ == "__main__":
    unittest.main()
