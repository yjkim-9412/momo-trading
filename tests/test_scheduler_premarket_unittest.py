import unittest
import json
from datetime import date, datetime, time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from core.config import settings
from scheduler.market_calendar import market_calendar
from scheduler.scheduler import TradingScheduler
from trading.enums import ActivityPhase
from trading.models import MCPResponse

class SchedulerPremarketProfileTest(unittest.TestCase):
    def setUp(self):
        self._original_premarket = settings.US_PREMARKET_ENABLED

    def tearDown(self):
        settings.US_PREMARKET_ENABLED = self._original_premarket

    def test_us_profile_uses_premarket_schedule_when_enabled(self):
        settings.US_PREMARKET_ENABLED = True

        profile = TradingScheduler._market_schedule_profile("NASDAQ")

        self.assertEqual(profile.prep_time, time(3, 50))
        self.assertEqual(profile.open_scan_time, time(4, 5))
        self.assertEqual(profile.resume_sessions, frozenset({"US_PRE", "US_REGULAR"}))
        self.assertEqual(profile.session_label, "프리마켓")

    def test_us_profile_keeps_regular_schedule_when_disabled(self):
        settings.US_PREMARKET_ENABLED = False

        profile = TradingScheduler._market_schedule_profile("NASDAQ")

        self.assertEqual(profile.prep_time, time(9, 20))
        self.assertEqual(profile.open_scan_time, time(9, 35))
        self.assertEqual(profile.resume_sessions, frozenset({"US_REGULAR"}))
        self.assertEqual(profile.session_label, "정규장")

    def test_holdings_check_hours_expand_into_premarket_when_enabled(self):
        settings.US_PREMARKET_ENABLED = True

        self.assertEqual(TradingScheduler._holdings_check_hours("NASDAQ"), "4-15")

    def test_holdings_check_hours_keep_regular_window_when_premarket_disabled(self):
        settings.US_PREMARKET_ENABLED = False

        self.assertEqual(TradingScheduler._holdings_check_hours("NASDAQ"), "10-15")


class SchedulerStartupActionTest(unittest.TestCase):
    def setUp(self):
        self.scheduler = TradingScheduler()
        self._original_premarket = settings.US_PREMARKET_ENABLED

    def tearDown(self):
        settings.US_PREMARKET_ENABLED = self._original_premarket

    def test_startup_waits_before_premarket_open_scan_time(self):
        settings.US_PREMARKET_ENABLED = True
        dt = datetime(2026, 3, 13, 4, 2, tzinfo=ZoneInfo("America/New_York"))

        action = self.scheduler._startup_trading_action("NASDAQ", dt)

        self.assertIsNone(action)

    def test_startup_uses_catchup_inside_open_scan_grace_window(self):
        settings.US_PREMARKET_ENABLED = True
        dt = datetime(2026, 3, 13, 4, 7, tzinfo=ZoneInfo("America/New_York"))

        action = self.scheduler._startup_trading_action("NASDAQ", dt)

        self.assertEqual(action, "startup_catchup")

    def test_startup_uses_resume_after_premarket_grace_window(self):
        settings.US_PREMARKET_ENABLED = True
        dt = datetime(2026, 3, 13, 10, 0, tzinfo=ZoneInfo("America/New_York"))

        action = self.scheduler._startup_trading_action("NASDAQ", dt)

        self.assertEqual(action, "startup_resume")

    def test_startup_skips_krx_after_market_resume(self):
        dt = datetime(2026, 3, 13, 17, 0, tzinfo=ZoneInfo("Asia/Seoul"))

        action = self.scheduler._startup_trading_action("KRX", dt)

        self.assertIsNone(action)


class SchedulerStartupExecutionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.scheduler = TradingScheduler()
        self._original_markets = settings.ENABLED_MARKETS

    async def asyncTearDown(self):
        settings.ENABLED_MARKETS = self._original_markets

    async def test_on_startup_does_not_schedule_us_trading_cycle(self):
        settings.ENABLED_MARKETS = "US"
        self.scheduler._market_open_scan = AsyncMock()
        self.scheduler._post_market_if_needed = AsyncMock()
        self.scheduler._schedule_startup_recovery = AsyncMock()
        self.scheduler._seed_startup_holdings_watchlist = AsyncMock()
        self.scheduler._restore_open_position_thresholds = AsyncMock(return_value=2)
        self.scheduler._restore_adaptive_count = AsyncMock()

        class DummyTask:
            def add_done_callback(self, _callback):
                return None

        def fake_create_task(coro):
            coro.close()
            return DummyTask()

        with patch("asyncio.sleep", AsyncMock()), \
                patch("asyncio.create_task", side_effect=fake_create_task) as create_task, \
                patch.object(market_calendar, "is_trading_hours", return_value=True), \
                patch.object(market_calendar, "get_market_session", return_value="US_PRE"), \
                patch.object(self.scheduler, "_startup_trading_action", return_value="startup_resume"), \
                patch("agent.decision_maker.decision_maker.repair_recent_broker_orders", AsyncMock(return_value=0)), \
                patch("agent.decision_maker.decision_maker.repair_stale_open_trade_results", AsyncMock(return_value=0)):
            await self.scheduler._on_startup()

        self.scheduler._market_open_scan.assert_not_called()
        self.assertGreaterEqual(self.scheduler._restore_open_position_thresholds.await_count, 1)
        self.scheduler._restore_open_position_thresholds.assert_any_await("NASDAQ")
        self.assertGreaterEqual(self.scheduler._schedule_startup_recovery.await_count, 1)
        self.scheduler._schedule_startup_recovery.assert_any_await("NASDAQ", "startup_resume")
        self.scheduler._post_market_if_needed.assert_called_once()
        self.assertGreaterEqual(create_task.call_count, 1)

    async def test_on_startup_repairs_stale_open_before_threshold_restore(self):
        settings.ENABLED_MARKETS = "KRX"
        call_order: list[str] = []

        async def _repair_recent(*args, **kwargs):
            call_order.append("repair_recent")
            return 0

        async def _repair_stale(*args, **kwargs):
            call_order.append("repair_stale")
            return 1

        async def _seed(_market):
            call_order.append("seed")

        async def _restore(_market):
            call_order.append("restore")
            return 0

        async def _adaptive(_market):
            call_order.append("adaptive")

        self.scheduler._market_open_scan = AsyncMock()
        self.scheduler._post_market_if_needed = AsyncMock()
        self.scheduler._schedule_startup_recovery = AsyncMock()
        self.scheduler._seed_startup_holdings_watchlist = AsyncMock(side_effect=_seed)
        self.scheduler._restore_open_position_thresholds = AsyncMock(side_effect=_restore)
        self.scheduler._restore_adaptive_count = AsyncMock(side_effect=_adaptive)

        class DummyTask:
            def add_done_callback(self, _callback):
                return None

        def fake_create_task(coro):
            coro.close()
            return DummyTask()

        with patch("asyncio.sleep", AsyncMock()), \
                patch("asyncio.create_task", side_effect=fake_create_task), \
                patch.object(market_calendar, "is_trading_hours", return_value=False), \
                patch(
                    "agent.decision_maker.decision_maker.repair_recent_broker_orders",
                    AsyncMock(side_effect=_repair_recent),
                ), \
                patch(
                    "agent.decision_maker.decision_maker.repair_stale_open_trade_results",
                    AsyncMock(side_effect=_repair_stale),
                ):
            await self.scheduler._on_startup()

        self.assertEqual(
            call_order[:5],
            ["repair_recent", "repair_stale", "seed", "restore", "adaptive"],
        )


class SchedulerLoggingContextTest(unittest.IsolatedAsyncioTestCase):
    async def test_log_schedule_attaches_market_scope_and_trading_date(self):
        scheduler = TradingScheduler()

        with patch.object(market_calendar, "market_date", return_value=date(2026, 3, 13)), \
                patch("services.activity_logger.activity_logger.log", AsyncMock()) as log_activity:
            await scheduler._log_schedule("NASDAQ", ActivityPhase.PROGRESS, "test")

        log_activity.assert_awaited_once()
        kwargs = log_activity.await_args.kwargs
        self.assertEqual(kwargs["market_scope"], "US")
        self.assertEqual(kwargs["trading_date"], date(2026, 3, 13))


class SchedulerThresholdHandlingTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.scheduler = TradingScheduler()
        self._original_day_trading_only = settings.DAY_TRADING_ONLY

    async def asyncTearDown(self):
        settings.DAY_TRADING_ONLY = self._original_day_trading_only

    async def test_check_overnight_positions_restores_separate_take_profit_price(self):
        trade = SimpleNamespace(
            stock_symbol="AAPL",
            stock_name="Apple",
            market="NASDAQ",
            ai_target_price=220.0,
            ai_take_profit_price=205.0,
            ai_stop_loss_price=190.0,
            notes=json.dumps({"trailing_stop_pct": 2.5}, ensure_ascii=False),
            strategy_type="STABLE_SHORT",
            created_at=datetime(2026, 3, 13, 10, 0),
            entry_at=None,
        )

        class DummySession:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        repo = MagicMock()
        repo.get_all_open = AsyncMock(return_value=[trade])
        detector = MagicMock()

        with patch("core.database.AsyncSessionLocal", return_value=DummySession()), \
                patch("repositories.trade_result_repository.TradeResultRepository", return_value=repo), \
                patch("realtime.event_detector.event_detector", detector), \
                patch("services.activity_logger.activity_logger.log", AsyncMock()):
            await self.scheduler._check_overnight_positions("NASDAQ")

        detector.set_thresholds.assert_called_once_with(
            "AAPL",
            market="NASDAQ",
            stop_loss=190.0,
            take_profit=205.0,
            trailing_stop_pct=2.5,
        )

    async def test_holdings_check_without_thresholds_skips_legacy_percent_sell(self):
        settings.DAY_TRADING_ONLY = False
        holding = SimpleNamespace(
            symbol="AAPL",
            name="Apple",
            market="NASDAQ",
            avg_buy_price=100.0,
            quantity=1,
        )

        class Thresholds:
            stop_loss = 0.0
            take_profit = 0.0

        detector = MagicMock()
        detector.get_thresholds.return_value = Thresholds()

        with patch.object(market_calendar, "is_trading_hours", return_value=True), \
                patch.object(self.scheduler, "_update_realtime_subscriptions", AsyncMock()), \
                patch(
                    "agent.decision_maker.decision_maker.repair_stale_open_trade_results",
                    AsyncMock(return_value=1),
                ) as repair_mock, \
                patch("trading.account_manager.account_manager.get_holdings", AsyncMock(return_value=[holding])), \
                patch(
                    "trading.mcp_client.mcp_client.get_current_price",
                    AsyncMock(return_value=MCPResponse(success=True, data={"price": 94.0})),
                ), \
                patch("trading.mcp_client.mcp_client.place_order", AsyncMock()) as place_order_mock, \
                patch("realtime.event_detector.event_detector", detector), \
                patch("services.activity_logger.activity_logger.log", AsyncMock()), \
                patch("scheduler.scheduler.logger.warning") as warning_mock:
            await self.scheduler._holdings_check("NASDAQ")

        place_order_mock.assert_not_awaited()
        repair_mock.assert_awaited_once_with(market_scope="NASDAQ")
        warning_mock.assert_called()

    async def test_check_overnight_positions_repairs_stale_open_before_threshold_restore(self):
        trade = SimpleNamespace(
            stock_symbol="AAPL",
            stock_name="Apple",
            market="NASDAQ",
            notes=json.dumps({"planned_hold_days": 2, "close_review_count": 0}, ensure_ascii=False),
        )

        class DummySession:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        repo = MagicMock()
        repo.get_all_open = AsyncMock(return_value=[trade])
        call_order: list[str] = []

        async def _repair(*args, **kwargs):
            call_order.append("repair")
            return 1

        async def _restore(*args, **kwargs):
            call_order.append("restore")
            return 1

        with patch(
            "agent.decision_maker.decision_maker.repair_stale_open_trade_results",
            AsyncMock(side_effect=_repair),
        ), \
                patch("core.database.AsyncSessionLocal", return_value=DummySession()), \
                patch("repositories.trade_result_repository.TradeResultRepository", return_value=repo), \
                patch.object(self.scheduler, "_restore_open_position_thresholds", AsyncMock(side_effect=_restore)), \
                patch.object(self.scheduler, "_log_schedule", AsyncMock()):
            await self.scheduler._check_overnight_positions("NASDAQ")

        self.assertEqual(call_order[:2], ["repair", "restore"])


if __name__ == "__main__":
    unittest.main()
