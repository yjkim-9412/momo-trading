import unittest
from datetime import date, datetime, time
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch
from zoneinfo import ZoneInfo

from core.config import settings
from scheduler.market_calendar import market_calendar
from scheduler.scheduler import TradingScheduler
from trading.enums import ActivityPhase
from trading.mcp_client import mcp_client


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

    async def test_on_startup_schedules_us_premarket_catchup(self):
        settings.ENABLED_MARKETS = "US"
        self.scheduler._market_open_scan = AsyncMock()

        with patch("asyncio.sleep", AsyncMock()), \
                patch("asyncio.create_task", MagicMock()) as create_task, \
                patch.object(type(mcp_client), "is_connected", new_callable=PropertyMock, return_value=True), \
                patch.object(self.scheduler, "_startup_trading_action", return_value="startup_catchup"), \
                patch.object(market_calendar, "is_trading_hours", return_value=True):
            await self.scheduler._on_startup()

        self.scheduler._market_open_scan.assert_called_once_with("NASDAQ", trigger_reason="startup_catchup")
        self.assertEqual(create_task.call_count, 1)
        create_task.call_args.args[0].close()


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


if __name__ == "__main__":
    unittest.main()
