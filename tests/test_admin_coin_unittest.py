import unittest
from unittest.mock import patch

from agent.trading_agent import trading_agent
from agent.trading_agent._types import MarketState
from api.routes.admin_coin import get_coin_watchlist


class AdminCoinRouteTest(unittest.IsolatedAsyncioTestCase):
    async def test_get_coin_watchlist_exposes_scan_source_metadata(self):
        runtime = MarketState(
            scope="CRYPTO",
            last_selected_watchlist=[
                {
                    "symbol": "BTC",
                    "market": "BITHUMB",
                    "name": "비트코인",
                    "price": 150000000,
                    "change_rate": 1.25,
                    "volume": 12345.0,
                    "trade_value": 45678.0,
                    "strategy_type": "STABLE_SHORT",
                    "reason": "시장 기준점",
                    "scan_source": "DISCOVERY",
                }
            ],
        )

        with patch.object(trading_agent, "_market_states", {"CRYPTO": runtime}):
            response = await get_coin_watchlist()

        self.assertEqual(len(response.data["symbols"]), 1)
        item = response.data["symbols"][0]
        self.assertEqual(item["symbol"], "BTC")
        self.assertEqual(item["market"], "BITHUMB")
        self.assertEqual(item["scan_source"], "DISCOVERY")
        self.assertEqual(item["trade_value"], 45678.0)
        self.assertEqual(item["reason"], "시장 기준점")


if __name__ == "__main__":
    unittest.main()
