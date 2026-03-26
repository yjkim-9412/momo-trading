import unittest
from unittest.mock import AsyncMock, patch

from agent.trading_agent import trading_agent
from agent.trading_agent._types import MarketState
from realtime.event_detector import event_detector
from services.watchlist_sync import cleanup_post_market_stock_watchlist, reconcile_market_watchlist
from trading.models import AccountBalance, HoldingInfo


class WatchlistSyncTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        event_detector.clear_all()

    def tearDown(self):
        event_detector.clear_all()

    @staticmethod
    def _balance(effective_cash: float = 500_000) -> AccountBalance:
        return AccountBalance(
            total_asset=1_000_000,
            cash=effective_cash,
            stock_value=500_000,
            total_pnl=0.0,
            total_pnl_rate=0.0,
            market="NASDAQ",
            currency="USD",
            exchange_rate_to_krw=1450.0,
            raw_cash=effective_cash,
            effective_cash=effective_cash,
            cash_source="BROKER",
            is_valid=True,
        )

    async def test_reconcile_market_watchlist_merges_selected_and_holdings(self):
        runtime = MarketState(scope="US")

        with patch.object(trading_agent, "_market_states", {"US": runtime}), \
                patch(
                    "trading.account_manager.account_manager.get_account_snapshot",
                    AsyncMock(return_value=(
                        self._balance(),
                        [
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
                        ],
                    )),
                ), patch(
                    "realtime.stream_manager.stream_manager.replace_market_subscriptions",
                    AsyncMock(),
                ) as replace_subscriptions:
            desired = await reconcile_market_watchlist(
                "NASDAQ",
                selected_watchlist=[
                    {
                        "symbol": "NVDA",
                        "market": "NASDAQ",
                        "name": "NVIDIA",
                        "scan_source": "DISCOVERY",
                        "price_krw": 42_000,
                    },
                    {"symbol": "pltr", "market": "NASDAQ", "name": "Palantir"},
                ],
            )

        self.assertEqual(
            runtime.last_selected_watchlist,
            [
                {
                    "symbol": "NVDA",
                    "market": "NASDAQ",
                    "name": "NVIDIA",
                    "scan_source": "DISCOVERY",
                    "price_krw": 42_000,
                },
                {"symbol": "PLTR", "market": "NASDAQ", "name": "Palantir"},
            ],
        )
        self.assertEqual(desired, [("NVDA", "NASDAQ"), ("PLTR", "NASDAQ")])
        replace_subscriptions.assert_awaited_once_with(
            "NASDAQ",
            [("NVDA", "NASDAQ"), ("PLTR", "NASDAQ")],
        )

    async def test_reconcile_market_watchlist_prunes_unaffordable_selected_but_keeps_holdings(self):
        runtime = MarketState(scope="US")
        event_detector.set_thresholds("NVDA", market="NASDAQ", stop_loss=100)
        event_detector.set_thresholds("PLTR", market="NASDAQ", stop_loss=10)
        event_detector.set_thresholds("SOFI", market="NASDAQ", stop_loss=5)

        with patch.object(trading_agent, "_market_states", {"US": runtime}), \
                patch(
                    "trading.account_manager.account_manager.get_account_snapshot",
                    AsyncMock(return_value=(
                        self._balance(effective_cash=100_000),
                        [
                            HoldingInfo(
                                symbol="PLTR",
                                name="Palantir",
                                market="NASDAQ",
                                quantity=2,
                                avg_buy_price=10,
                                current_price=11,
                                pnl=2,
                                pnl_rate=10,
                            )
                        ],
                    )),
                ), patch(
                    "realtime.stream_manager.stream_manager.replace_market_subscriptions",
                    AsyncMock(),
                ) as replace_subscriptions:
            desired = await reconcile_market_watchlist(
                "NASDAQ",
                selected_watchlist=[
                    {"symbol": "NVDA", "market": "NASDAQ", "name": "NVIDIA", "price_krw": 150_000},
                    {"symbol": "PLTR", "market": "NASDAQ", "name": "Palantir", "price_krw": 200_000},
                    {"symbol": "SOFI", "market": "NASDAQ", "name": "SoFi", "price_krw": 50_000},
                ],
            )

        self.assertEqual(
            runtime.last_selected_watchlist,
            [
                {"symbol": "PLTR", "market": "NASDAQ", "name": "Palantir", "price_krw": 200_000},
                {"symbol": "SOFI", "market": "NASDAQ", "name": "SoFi", "price_krw": 50_000},
            ],
        )
        self.assertEqual(desired, [("PLTR", "NASDAQ"), ("SOFI", "NASDAQ")])
        replace_subscriptions.assert_awaited_once_with(
            "NASDAQ",
            [("PLTR", "NASDAQ"), ("SOFI", "NASDAQ")],
        )
        self.assertNotIn("NASDAQ:NVDA", event_detector.monitored_symbols)
        self.assertIn("NASDAQ:PLTR", event_detector.monitored_symbols)
        self.assertIn("NASDAQ:SOFI", event_detector.monitored_symbols)

    async def test_reconcile_market_watchlist_fail_open_when_account_snapshot_fails(self):
        runtime = MarketState(scope="US")
        event_detector.set_thresholds("NVDA", market="NASDAQ", stop_loss=100)

        with patch.object(trading_agent, "_market_states", {"US": runtime}), \
                patch(
                    "trading.account_manager.account_manager.get_account_snapshot",
                    AsyncMock(side_effect=RuntimeError("snapshot unavailable")),
                ), patch(
                    "trading.account_manager.account_manager.get_holdings",
                    AsyncMock(return_value=[]),
                ), patch(
                    "realtime.stream_manager.stream_manager.replace_market_subscriptions",
                    AsyncMock(),
                ) as replace_subscriptions:
            desired = await reconcile_market_watchlist(
                "NASDAQ",
                selected_watchlist=[
                    {"symbol": "NVDA", "market": "NASDAQ", "name": "NVIDIA", "price_krw": 900_000},
                ],
            )

        self.assertEqual(
            runtime.last_selected_watchlist,
            [{"symbol": "NVDA", "market": "NASDAQ", "name": "NVIDIA", "price_krw": 900_000}],
        )
        self.assertEqual(desired, [("NVDA", "NASDAQ")])
        replace_subscriptions.assert_awaited_once_with("NASDAQ", [("NVDA", "NASDAQ")])
        self.assertIn("NASDAQ:NVDA", event_detector.monitored_symbols)

    async def test_reconcile_market_watchlist_gc_removes_threshold_only_symbols(self):
        runtime = MarketState(scope="KRX")
        event_detector.set_thresholds("042660", market="KRX", stop_loss=126100)
        event_detector.set_thresholds("138040", market="KRX", stop_loss=110500)
        event_detector.set_thresholds("006800", market="KRX", stop_loss=64137)

        with patch.object(trading_agent, "_market_states", {"KRX": runtime}), \
                patch(
                    "trading.account_manager.account_manager.get_account_snapshot",
                    AsyncMock(return_value=(
                        self._balance(effective_cash=300_000),
                        [
                            HoldingInfo(
                                symbol="006800",
                                name="미래에셋",
                                market="KRX",
                                quantity=1,
                                avg_buy_price=67700,
                                current_price=69000,
                                pnl=1300,
                                pnl_rate=1.92,
                            )
                        ],
                    )),
                ), patch(
                    "realtime.stream_manager.stream_manager.replace_market_subscriptions",
                    AsyncMock(),
                ) as replace_subscriptions:
            desired = await reconcile_market_watchlist(
                "KRX",
                selected_watchlist=[
                    {"symbol": "138040", "market": "KRX", "name": "메리츠금융", "price_krw": 114_800},
                ],
            )

        self.assertEqual(desired, [("138040", "KRX"), ("006800", "KRX")])
        replace_subscriptions.assert_awaited_once_with(
            "KRX",
            [("138040", "KRX"), ("006800", "KRX")],
        )
        self.assertNotIn("KRX:042660", event_detector.monitored_symbols)
        self.assertIn("KRX:138040", event_detector.monitored_symbols)
        self.assertIn("KRX:006800", event_detector.monitored_symbols)

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
