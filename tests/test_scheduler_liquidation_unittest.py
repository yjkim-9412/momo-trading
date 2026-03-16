import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from scheduler.scheduler import TradingScheduler
from trading.models import HoldingInfo


class SchedulerLiquidationCleanupTest(unittest.IsolatedAsyncioTestCase):
    async def test_force_liquidation_cleans_watchlist_when_no_holdings(self):
        scheduler = TradingScheduler()

        with patch("scheduler.scheduler.settings.AI_DYNAMIC_RESCAN_ENABLED", False), patch(
            "scheduler.scheduler.settings.TRADING_ENABLED",
            True,
        ), patch(
            "scheduler.market_calendar.market_calendar.is_holiday",
            return_value=False,
        ), patch(
            "trading.account_manager.account_manager.get_holdings",
            AsyncMock(return_value=[]),
        ), patch(
            "services.watchlist_sync.cleanup_post_market_stock_watchlist",
            AsyncMock(),
        ) as cleanup_watchlist, patch.object(
            scheduler,
            "_log_schedule",
            AsyncMock(),
        ):
            await scheduler._force_liquidation("KRX")

        cleanup_watchlist.assert_awaited_once_with("KRX", retained_symbols=[])

    async def test_force_liquidation_retains_hold_and_final_failures_only(self):
        scheduler = TradingScheduler()
        aapl = HoldingInfo(
            symbol="AAPL",
            name="Apple",
            market="NASDAQ",
            quantity=1,
            avg_buy_price=100,
            current_price=110,
            pnl=10,
            pnl_rate=10,
        )
        tsla = HoldingInfo(
            symbol="TSLA",
            name="Tesla",
            market="NASDAQ",
            quantity=2,
            avg_buy_price=200,
            current_price=180,
            pnl=-40,
            pnl_rate=-10,
        )
        msft = HoldingInfo(
            symbol="MSFT",
            name="Microsoft",
            market="NASDAQ",
            quantity=3,
            avg_buy_price=300,
            current_price=330,
            pnl=90,
            pnl_rate=10,
        )

        with patch("scheduler.scheduler.settings.AI_DYNAMIC_RESCAN_ENABLED", False), patch(
            "scheduler.scheduler.settings.TRADING_ENABLED",
            True,
        ), patch(
            "scheduler.scheduler.settings.DAY_TRADING_ONLY",
            False,
        ), patch(
            "scheduler.market_calendar.market_calendar.is_holiday",
            return_value=False,
        ), patch(
            "trading.account_manager.account_manager.get_holdings",
            AsyncMock(return_value=[aapl, tsla, msft]),
        ), patch.object(
            scheduler,
            "_smart_liquidation",
            AsyncMock(return_value=([aapl, tsla], [msft])),
        ), patch(
            "trading.mcp_client.mcp_client.place_order",
            AsyncMock(
                side_effect=[
                    SimpleNamespace(success=True, error=None, data={}),
                    SimpleNamespace(success=False, error="initial failure", data={}),
                    SimpleNamespace(success=False, error="retry failure", data={}),
                ]
            ),
        ), patch(
            "services.activity_logger.activity_logger.log",
            AsyncMock(),
        ), patch(
            "services.watchlist_sync.cleanup_post_market_stock_watchlist",
            AsyncMock(),
        ) as cleanup_watchlist, patch(
            "scheduler.scheduler.asyncio.sleep",
            AsyncMock(),
        ), patch.object(
            scheduler,
            "_log_schedule",
            AsyncMock(),
        ):
            await scheduler._force_liquidation("NASDAQ")

        cleanup_watchlist.assert_awaited_once()
        self.assertEqual(cleanup_watchlist.await_args.args[0], "NASDAQ")
        self.assertEqual(
            cleanup_watchlist.await_args.kwargs["retained_symbols"],
            [("MSFT", "NASDAQ"), ("TSLA", "NASDAQ")],
        )


if __name__ == "__main__":
    unittest.main()
