import unittest
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from scheduler.market_calendar import market_calendar
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
        ), patch.object(
            scheduler,
            "_reconcile_from_kis",
            AsyncMock(),
        ), patch.object(
            scheduler,
            "_trigger_immediate_review",
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
            "_is_after_hours_disabled",
            return_value=False,
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
        ), patch.object(
            scheduler,
            "_reconcile_from_kis",
            AsyncMock(),
        ), patch.object(
            scheduler,
            "_trigger_immediate_review",
            AsyncMock(),
        ):
            await scheduler._force_liquidation("NASDAQ")

        cleanup_watchlist.assert_awaited_once()
        self.assertEqual(cleanup_watchlist.await_args.args[0], "NASDAQ")
        self.assertEqual(
            cleanup_watchlist.await_args.kwargs["retained_symbols"],
            [("MSFT", "NASDAQ"), ("TSLA", "NASDAQ")],
        )


class SchedulerSmartLiquidationReviewTest(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _holding(symbol: str = "AAPL") -> HoldingInfo:
        return HoldingInfo(
            symbol=symbol,
            name="Apple",
            market="NASDAQ",
            quantity=2,
            avg_buy_price=100.0,
            current_price=105.0,
            pnl=10.0,
            pnl_rate=5.0,
            currency="USD",
            exchange_rate_to_krw=1450.0,
        )

    @staticmethod
    def _trade(notes: dict | None = None):
        return SimpleNamespace(
            stock_symbol="AAPL",
            stock_name="Apple",
            market="NASDAQ",
            strategy_type="STABLE_SHORT",
            ai_confidence=0.72,
            ai_target_price=112.0,
            ai_stop_loss_price=98.0,
            ai_take_profit_price=110.0,
            notes=json.dumps(notes or {}, ensure_ascii=False),
        )

    @staticmethod
    def _dummy_session():
        class DummyTransaction:
            async def __aenter__(self):
                return None

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class DummySession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def begin(self):
                return DummyTransaction()

        return DummySession()

    async def test_smart_liquidation_updates_hold_plan_and_thresholds_on_hold(self):
        scheduler = TradingScheduler()
        holding = self._holding()
        trade = self._trade(
            {
                "planned_hold_days": 2,
                "close_review_count": 0,
                "trailing_stop_pct": 1.5,
            }
        )
        repo = MagicMock()
        repo.get_open_buy = AsyncMock(side_effect=[trade, trade])
        detector = MagicMock()

        with patch("core.database.AsyncSessionLocal", side_effect=[self._dummy_session(), self._dummy_session()]), \
                patch("repositories.trade_result_repository.TradeResultRepository", return_value=repo), \
                patch(
                    "trading.mcp_client.mcp_client.get_current_price",
                    AsyncMock(return_value=SimpleNamespace(success=True, data={"price": 105.0})),
                ), \
                patch(
                    "agent.trading_agent.trading_agent.review_close_hold_position",
                    AsyncMock(return_value={
                        "action": "HOLD",
                        "reason": "추세 유지",
                        "planned_hold_days": 3,
                        "confidence": 0.81,
                        "stop_loss_price": 101.0,
                        "take_profit_price": 118.0,
                        "trailing_stop_pct": 2.0,
                    }),
                ), \
                patch("realtime.event_detector.event_detector", detector), \
                patch.object(market_calendar, "market_date", return_value=date(2026, 3, 26)):
            to_sell, to_hold = await scheduler._smart_liquidation([holding], "NASDAQ")

        self.assertEqual(to_sell, [])
        self.assertEqual(to_hold, [holding])
        notes = json.loads(trade.notes)
        self.assertEqual(notes["planned_hold_days"], 3)
        self.assertEqual(notes["close_review_count"], 1)
        self.assertEqual(notes["last_close_review_date"], "2026-03-26")
        self.assertEqual(notes["trailing_stop_pct"], 2.0)
        self.assertEqual(trade.ai_stop_loss_price, 101.0)
        self.assertEqual(trade.ai_take_profit_price, 118.0)
        self.assertEqual(trade.ai_target_price, 118.0)
        detector.set_thresholds.assert_called_once_with(
            "AAPL",
            market="NASDAQ",
            stop_loss=101.0,
            take_profit=118.0,
            trailing_stop_pct=2.0,
            highest_price=105.0,
        )

    async def test_smart_liquidation_holds_when_ai_review_fails_before_plan_is_exhausted(self):
        scheduler = TradingScheduler()
        holding = self._holding()
        trade = self._trade(
            {
                "planned_hold_days": 2,
                "close_review_count": 1,
            }
        )
        repo = MagicMock()
        repo.get_open_buy = AsyncMock(return_value=trade)

        with patch("core.database.AsyncSessionLocal", return_value=self._dummy_session()), \
                patch("repositories.trade_result_repository.TradeResultRepository", return_value=repo), \
                patch(
                    "trading.mcp_client.mcp_client.get_current_price",
                    AsyncMock(return_value=SimpleNamespace(success=True, data={"price": 105.0})),
                ), \
                patch(
                    "agent.trading_agent.trading_agent.review_close_hold_position",
                    AsyncMock(return_value=None),
                ), \
                patch.object(market_calendar, "market_date", return_value=date(2026, 3, 26)):
            to_sell, to_hold = await scheduler._smart_liquidation([holding], "NASDAQ")

        self.assertEqual(to_sell, [])
        self.assertEqual(to_hold, [holding])
        notes = json.loads(trade.notes)
        self.assertEqual(notes["close_review_count"], 2)
        self.assertEqual(notes["last_close_review_date"], "2026-03-26")

    async def test_smart_liquidation_sells_when_ai_review_fails_after_plan_is_exhausted(self):
        scheduler = TradingScheduler()
        holding = self._holding()
        trade = self._trade(
            {
                "planned_hold_days": 1,
                "close_review_count": 1,
            }
        )
        repo = MagicMock()
        repo.get_open_buy = AsyncMock(return_value=trade)

        with patch("core.database.AsyncSessionLocal", return_value=self._dummy_session()), \
                patch("repositories.trade_result_repository.TradeResultRepository", return_value=repo), \
                patch(
                    "trading.mcp_client.mcp_client.get_current_price",
                    AsyncMock(return_value=SimpleNamespace(success=True, data={"price": 105.0})),
                ), \
                patch(
                    "agent.trading_agent.trading_agent.review_close_hold_position",
                    AsyncMock(return_value=None),
                ), \
                patch.object(market_calendar, "market_date", return_value=date(2026, 3, 26)):
            to_sell, to_hold = await scheduler._smart_liquidation([holding], "NASDAQ")

        self.assertEqual(to_sell, [holding])
        self.assertEqual(to_hold, [])


if __name__ == "__main__":
    unittest.main()
