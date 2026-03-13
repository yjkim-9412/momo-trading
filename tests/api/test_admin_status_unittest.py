import unittest
from datetime import date, datetime, timezone
from unittest.mock import PropertyMock, patch

from admin.sse_manager import sse_manager
from agent.trading_agent import trading_agent
from api.routes.admin import get_schedule_timeline, get_system_status
from realtime.monitor import realtime_monitor
from realtime.stream_manager import stream_manager
from scheduler.market_calendar import market_calendar
from scheduler.scheduler import AdaptiveRescanState, trading_scheduler
from trading.mcp_client import mcp_client


class AdminStatusRouteTest(unittest.IsolatedAsyncioTestCase):
    async def test_get_system_status_exposes_cycle_runtime_fields(self):
        last_cycle_time = datetime(2026, 3, 14, 1, 10, tzinfo=timezone.utc)
        next_open = datetime(2026, 3, 16, 13, 30, tzinfo=timezone.utc)

        with patch.object(type(mcp_client), "is_connected", new_callable=PropertyMock, return_value=True), \
                patch.object(type(trading_scheduler), "is_running", new_callable=PropertyMock, return_value=True), \
                patch.object(type(realtime_monitor), "is_running", new_callable=PropertyMock, return_value=True), \
                patch.object(type(sse_manager), "client_count", new_callable=PropertyMock, return_value=2), \
                patch.object(trading_agent, "_running", True), \
                patch.object(trading_agent, "_last_cycle_time", last_cycle_time), \
                patch.object(
                    trading_agent,
                    "get_cycle_runtime_snapshot",
                    return_value={
                        "last_cycle_attempt_at": "2026-03-14T01:08:33+00:00",
                        "last_cycle_status": "ERROR",
                        "last_cycle_error": "market_scan: scan exploded",
                    },
                ), \
                patch.object(
                    market_calendar,
                    "get_session_schedule",
                    return_value={
                        "current_session": "US_REGULAR",
                        "sessions": ["US_PRE", "US_REGULAR"],
                        "tz_label": "ET",
                        "dst_active": True,
                    },
                ), \
                patch.object(market_calendar, "is_trading_hours", return_value=True), \
                patch.object(market_calendar, "get_holiday_name", return_value=None), \
                patch.object(market_calendar, "next_market_open", return_value=next_open), \
                patch.object(stream_manager, "stream_status", return_value={"running": True, "connected": True}):
            response = await get_system_status("NASDAQ")

        data = response.data
        self.assertEqual(data["last_cycle_time"], last_cycle_time.isoformat())
        self.assertEqual(data["last_cycle_attempt_at"], "2026-03-14T01:08:33+00:00")
        self.assertEqual(data["last_cycle_status"], "ERROR")
        self.assertEqual(data["last_cycle_error"], "market_scan: scan exploded")

    async def test_get_schedule_timeline_exposes_adaptive_last_run_and_error(self):
        now_local = datetime(2026, 3, 14, 10, 0, tzinfo=timezone.utc)
        next_run_at = datetime(2026, 3, 14, 10, 30, tzinfo=timezone.utc)
        last_run_at = datetime(2026, 3, 14, 9, 45, tzinfo=timezone.utc)
        adaptive_state = AdaptiveRescanState(
            trading_date=date(2026, 3, 14),
            scheduled_cycle_count_today=1,
            next_adaptive_run_at=next_run_at,
            last_schedule_hint={"action": "SCHEDULE_NEXT", "reason": "추세 재확인"},
            last_run_at=last_run_at,
            last_error="cycle exploded",
        )

        with patch.object(trading_scheduler, "_adaptive_state", return_value=adaptive_state), \
                patch.object(trading_scheduler, "_remaining_scheduled_budget", return_value=2), \
                patch.object(trading_scheduler, "_market_now", return_value=now_local):
            response = await get_schedule_timeline("KRX")

        adaptive = response.data["adaptive"]
        self.assertEqual(adaptive["next_run_at"], next_run_at.isoformat())
        self.assertEqual(adaptive["next_run_in_minutes"], 30)
        self.assertEqual(adaptive["last_run_at"], last_run_at.isoformat())
        self.assertEqual(adaptive["last_error"], "cycle exploded")


if __name__ == "__main__":
    unittest.main()
