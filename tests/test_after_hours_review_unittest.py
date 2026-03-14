import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

from agent.trading_agent import TradingAgent
from core.events import EventBus, EventType
from scheduler.market_calendar import market_calendar
from scheduler.scheduler import TradingScheduler
from trading.mcp_client import mcp_client


class MarketCalendarReviewWindowTest(unittest.TestCase):
    def test_krx_review_window_opens_at_1540_kst(self):
        before_open = datetime(2026, 3, 13, 15, 35, tzinfo=ZoneInfo("Asia/Seoul"))
        after_open = datetime(2026, 3, 13, 16, 25, tzinfo=ZoneInfo("Asia/Seoul"))

        self.assertFalse(market_calendar.is_post_market_review_time("KRX", before_open))
        self.assertTrue(market_calendar.is_post_market_review_time("KRX", after_open))

    def test_us_review_window_opens_at_1610_et(self):
        before_open = datetime(2026, 3, 13, 16, 5, tzinfo=ZoneInfo("America/New_York"))
        after_open = datetime(2026, 3, 13, 16, 10, tzinfo=ZoneInfo("America/New_York"))

        self.assertFalse(market_calendar.is_post_market_review_time("NASDAQ", before_open))
        self.assertTrue(market_calendar.is_post_market_review_time("NASDAQ", after_open))


class TradingAgentAfterHoursGuardTest(unittest.IsolatedAsyncioTestCase):
    async def test_preview_cycle_skips_when_review_already_exists(self):
        agent = TradingAgent()
        trading_date = date(2026, 3, 13)

        with patch("agent.trading_agent.market_calendar.is_trading_hours", return_value=False), \
                patch("agent.trading_agent.market_calendar.is_post_market_review_time", return_value=True), \
                patch("agent.trading_agent.market_calendar.market_date", return_value=trading_date), \
                patch.object(TradingAgent, "_daily_report_exists", AsyncMock(return_value=True)):
            preview = await agent.preview_cycle(market="NASDAQ")

        self.assertTrue(preview["skipped"])
        self.assertEqual(preview["reason"], "already_reviewed")
        self.assertEqual(preview["market_scope"], "US")
        self.assertEqual(agent.get_runtime("US").last_completed_review_date, trading_date)

    async def test_run_cycle_skips_after_hours_when_review_already_exists(self):
        agent = TradingAgent()
        trading_date = date(2026, 3, 13)

        with patch("agent.trading_agent.market_calendar.is_trading_hours", return_value=False), \
                patch("agent.trading_agent.market_calendar.is_post_market_review_time", return_value=True), \
                patch("agent.trading_agent.market_calendar.market_date", return_value=trading_date), \
                patch.object(TradingAgent, "_daily_report_exists", AsyncMock(return_value=True)), \
                patch.object(TradingAgent, "_run_after_hours_cycle", AsyncMock()) as run_after_hours:
            result = await agent.run_cycle(market="NASDAQ")

        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "already_reviewed")
        run_after_hours.assert_not_awaited()

    async def test_preview_cycle_skips_when_after_hours_cycle_is_running(self):
        agent = TradingAgent()
        runtime = agent.get_runtime("US")
        trading_date = date(2026, 3, 13)
        await runtime.after_hours_lock.acquire()

        try:
            with patch("agent.trading_agent.market_calendar.is_trading_hours", return_value=False), \
                    patch("agent.trading_agent.market_calendar.market_date", return_value=trading_date):
                preview = await agent.preview_cycle(market="NASDAQ")
        finally:
            runtime.after_hours_lock.release()

        self.assertTrue(preview["skipped"])
        self.assertEqual(preview["reason"], "after_hours_already_running")

    async def test_preview_cycle_skips_trading_when_mcp_unavailable(self):
        agent = TradingAgent()
        trading_date = date(2026, 3, 13)

        with patch("agent.trading_agent.market_calendar.is_trading_hours", return_value=True), \
                patch("agent.trading_agent.market_calendar.market_date", return_value=trading_date), \
                patch.object(type(mcp_client), "is_connected", new_callable=PropertyMock, return_value=False):
            preview = await agent.preview_cycle(market="KRX")

        self.assertTrue(preview["skipped"])
        self.assertEqual(preview["reason"], "mcp_unavailable")

    async def test_preview_cycle_allows_crypto_without_mcp(self):
        agent = TradingAgent()
        trading_date = date(2026, 3, 13)

        with patch("agent.trading_agent.market_calendar.is_trading_hours", return_value=True), \
                patch("agent.trading_agent.market_calendar.market_date", return_value=trading_date), \
                patch.object(type(mcp_client), "is_connected", new_callable=PropertyMock, return_value=False):
            preview = await agent.preview_cycle(market="BITHUMB")

        self.assertFalse(preview.get("skipped", False))
        self.assertTrue(preview["allowed"])
        self.assertEqual(preview["mode"], "TRADING")
        self.assertEqual(preview["market_scope"], "CRYPTO")
        self.assertEqual(preview["trading_date"], trading_date.isoformat())


class StartupIdempotencyTest(unittest.IsolatedAsyncioTestCase):
    async def test_trading_agent_start_is_idempotent(self):
        agent = TradingAgent()

        with patch("agent.trading_agent.event_bus.subscribe", MagicMock()) as subscribe:
            await agent.start()
            await agent.start()

        self.assertEqual(subscribe.call_count, 5)

    async def test_scheduler_start_is_idempotent(self):
        scheduler = TradingScheduler()

        with patch("scheduler.scheduler.settings.SCHEDULER_ENABLED", True), \
                patch.object(scheduler, "_setup_jobs", MagicMock()) as setup_jobs, \
                patch.object(scheduler.scheduler, "start", MagicMock()) as scheduler_start, \
                patch.object(scheduler, "_on_startup", AsyncMock()) as on_startup:
            await scheduler.start()
            await scheduler.start()

        self.assertEqual(setup_jobs.call_count, 1)
        self.assertEqual(scheduler_start.call_count, 1)
        on_startup.assert_awaited_once()


class EventBusSubscriptionTest(unittest.TestCase):
    def test_subscribe_skips_duplicate_handler(self):
        bus = EventBus()

        async def handler(_event):
            return None

        bus.subscribe(EventType.PRICE_SURGE, handler)
        bus.subscribe(EventType.PRICE_SURGE, handler)

        self.assertEqual(len(bus._handlers[EventType.PRICE_SURGE]), 1)


if __name__ == "__main__":
    unittest.main()
