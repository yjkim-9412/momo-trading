import unittest
from unittest.mock import AsyncMock, patch

from agent.trading_agent import trading_agent
from agent.trading_agent._types import MarketState
from realtime.event_detector import event_detector
from services.watchlist_sync import cleanup_post_market_stock_watchlist, reconcile_market_watchlist
from trading.models import HoldingInfo


class WatchlistSyncTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        event_detector.clear_all()

    def tearDown(self):
        event_detector.clear_all()

    async def test_reconcile_market_watchlist_merges_selected_and_holdings(self):
        runtime = MarketState(scope="US")

        with patch.object(trading_agent, "_market_states", {"US": runtime}), \
                patch(
                    "trading.account_manager.account_manager.get_holdings",
                    AsyncMock(return_value=[
                        HoldingInfo(
                            symbol="PLTR",
                            name="Palantir",
                            market="NASDAQ",
                            quantity=1,
                            avg_buy_price=10,
                            current_price=11,
                            pnl=1,
                            pnl_rate=10,
                        )
                    ]),
                ), patch(
                    "realtime.stream_manager.stream_manager.replace_market_subscriptions",
                    AsyncMock(),
                ) as replace_subscriptions:
            desired = await reconcile_market_watchlist(
                "NASDAQ",
                selected_watchlist=[
                    {"symbol": "NVDA", "market": "NASDAQ", "name": "NVIDIA", "scan_source": "DISCOVERY"},
                    {"symbol": "pltr", "market": "NASDAQ", "name": "Palantir"},
                ],
            )

        self.assertEqual(
            runtime.last_selected_watchlist,
            [
                {"symbol": "NVDA", "market": "NASDAQ", "name": "NVIDIA", "scan_source": "DISCOVERY"},
                {"symbol": "PLTR", "market": "NASDAQ", "name": "Palantir"},
            ],
        )
        self.assertEqual(desired, [("NVDA", "NASDAQ"), ("PLTR", "NASDAQ")])
        replace_subscriptions.assert_awaited_once_with(
            "NASDAQ",
            [("NVDA", "NASDAQ"), ("PLTR", "NASDAQ")],
        )

    async def test_cleanup_post_market_stock_watchlist_clears_selected_and_stale_thresholds(self):
        runtime = MarketState(
            scope="US",
            last_selected_watchlist=[
                {"symbol": "NVDA", "market": "NASDAQ", "name": "NVIDIA"},
                {"symbol": "PLTR", "market": "NASDAQ", "name": "Palantir"},
            ],
        )
        event_detector.set_thresholds("NVDA", market="NASDAQ", stop_loss=700)
        event_detector.set_thresholds("PLTR", market="NASDAQ", stop_loss=10)
        event_detector.set_thresholds("005930", market="KRX", stop_loss=70000)

        with patch.object(trading_agent, "_market_states", {"US": runtime}), patch(
            "realtime.stream_manager.stream_manager.replace_market_subscriptions",
            AsyncMock(),
        ) as replace_subscriptions:
            desired = await cleanup_post_market_stock_watchlist(
                "NASDAQ",
                retained_symbols=[("PLTR", "NASDAQ")],
            )

        self.assertEqual(desired, [("PLTR", "NASDAQ")])
        self.assertEqual(runtime.last_selected_watchlist, [])
        replace_subscriptions.assert_awaited_once_with("NASDAQ", [("PLTR", "NASDAQ")])
        self.assertNotIn("NASDAQ:NVDA", event_detector.monitored_symbols)
        self.assertIn("NASDAQ:PLTR", event_detector.monitored_symbols)
        self.assertIn("KRX:005930", event_detector.monitored_symbols)

    async def test_cleanup_post_market_stock_watchlist_keeps_state_when_holdings_lookup_fails(self):
        runtime = MarketState(
            scope="KRX",
            last_selected_watchlist=[{"symbol": "005930", "market": "KRX", "name": "삼성전자"}],
        )
        event_detector.set_thresholds("005930", market="KRX", stop_loss=70000)

        with patch.object(trading_agent, "_market_states", {"KRX": runtime}), patch(
            "trading.account_manager.account_manager.get_holdings",
            AsyncMock(side_effect=RuntimeError("holdings unavailable")),
        ), patch(
            "realtime.stream_manager.stream_manager.replace_market_subscriptions",
            AsyncMock(),
        ) as replace_subscriptions:
            desired = await cleanup_post_market_stock_watchlist("KRX")

        self.assertIsNone(desired)
        self.assertEqual(
            runtime.last_selected_watchlist,
            [{"symbol": "005930", "market": "KRX", "name": "삼성전자"}],
        )
        replace_subscriptions.assert_not_awaited()
        self.assertIn("KRX:005930", event_detector.monitored_symbols)


if __name__ == "__main__":
    unittest.main()
