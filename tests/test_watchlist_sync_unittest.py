import unittest
from unittest.mock import AsyncMock, patch

from agent.trading_agent import trading_agent
from agent.trading_agent._types import MarketState
from services.watchlist_sync import reconcile_market_watchlist
from trading.models import HoldingInfo


class WatchlistSyncTest(unittest.IsolatedAsyncioTestCase):
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
                    {"symbol": "NVDA", "market": "NASDAQ", "name": "NVIDIA"},
                    {"symbol": "pltr", "market": "NASDAQ", "name": "Palantir"},
                ],
            )

        self.assertEqual(
            runtime.last_selected_watchlist,
            [
                {"symbol": "NVDA", "market": "NASDAQ", "name": "NVIDIA"},
                {"symbol": "PLTR", "market": "NASDAQ", "name": "Palantir"},
            ],
        )
        self.assertEqual(desired, [("NVDA", "NASDAQ"), ("PLTR", "NASDAQ")])
        replace_subscriptions.assert_awaited_once_with(
            "NASDAQ",
            [("NVDA", "NASDAQ"), ("PLTR", "NASDAQ")],
        )


if __name__ == "__main__":
    unittest.main()
