import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from realtime.coin_monitor import CoinRealtimeMonitor


class CoinRealtimeMonitorTest(unittest.IsolatedAsyncioTestCase):
    async def test_start_restores_holding_watchlist_before_stream_start(self):
        monitor = CoinRealtimeMonitor()
        call_order = []

        async def restore_side_effect(market):
            call_order.append(("restore", market))
            return [("BTC", "BITHUMB")]

        async def stream_start_side_effect():
            call_order.append(("stream_start", None))

        with patch("services.watchlist_sync.reconcile_market_watchlist", side_effect=restore_side_effect), \
                patch("realtime.coin_monitor.coin_stream_manager.start", side_effect=stream_start_side_effect):
            await monitor.start()

        self.assertEqual(call_order, [("restore", "BITHUMB"), ("stream_start", None)])
        self.assertEqual(monitor.last_holding_watch_symbol_count, 1)
        self.assertIsNone(monitor.last_holding_watch_restore_error)

    async def test_account_changed_events_are_coalesced(self):
        monitor = CoinRealtimeMonitor()
        monitor._ACCOUNT_CHANGE_DEBOUNCE_SECONDS = 0.01

        with patch(
            "services.watchlist_sync.reconcile_market_watchlist",
            AsyncMock(return_value=[("BTC", "BITHUMB")]),
        ) as reconcile_watchlist, \
                patch("api.routes.admin_coin.coin_sse_manager.broadcast", AsyncMock()) as broadcast_mock:
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

        reconcile_watchlist.assert_awaited_once_with("BITHUMB")
        broadcast_mock.assert_awaited_once()
        payload = broadcast_mock.await_args.args[0]
        self.assertEqual(payload["type"], "account_changed")
        self.assertEqual(payload["order_id"], "order-1")
        self.assertEqual(payload["status"], "PARTIAL")
        self.assertEqual(payload["symbols"], ["BTC"])
        self.assertEqual(payload["changed_currencies"], ["BTC", "KRW"])
        self.assertEqual(payload["reason"], "asset_update,order_update")
        self.assertEqual(monitor.last_holding_watch_symbol_count, 1)
        self.assertIsNone(monitor.last_holding_watch_restore_error)


if __name__ == "__main__":
    unittest.main()
