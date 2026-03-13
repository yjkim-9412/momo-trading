import asyncio
import unittest
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, PropertyMock, patch

from admin.sse_manager import sse_manager
from agent.trading_agent import trading_agent
from agent.trading_agent._types import MarketState
from api.routes.admin import (
    get_schedule_timeline,
    get_system_status,
    get_watchlist,
    trigger_agent_cycle,
)
from realtime.event_detector import StockThresholds, event_detector
from realtime.monitor import realtime_monitor
from realtime.stream_manager import stream_manager
from scheduler.market_calendar import market_calendar
from scheduler.scheduler import AdaptiveRescanState, trading_scheduler
from services.activity_logger import activity_logger
from trading.account_manager import account_manager
from trading.mcp_client import mcp_client
from trading.models import HoldingInfo


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

    async def test_get_watchlist_exposes_selected_desired_and_threshold_flags(self):
        runtime = MarketState(
            scope="US",
            last_selected_watchlist=[
                {"symbol": "NVDA", "market": "NASDAQ", "name": "NVIDIA"},
            ],
        )

        with patch.object(trading_agent, "_market_states", {"US": runtime}), \
                patch.object(
                    account_manager,
                    "get_holdings",
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
                ), \
                patch.object(stream_manager, "desired_keys", return_value={
                    ("NASDAQ", "NVDA"),
                    ("NASDAQ", "PLTR"),
                }), \
                patch.object(stream_manager, "active_keys", return_value={("NASDAQ", "PLTR")}), \
                patch.object(stream_manager, "stream_status", return_value={"running": True}), \
                patch.object(
                    event_detector,
                    "_thresholds",
                    {
                        "NASDAQ:NVDA": StockThresholds(surge_pct=1.5),
                        "NASDAQ:SOFI": StockThresholds(volume_spike_ratio=4.0),
                    },
                ):
            response = await get_watchlist("NASDAQ")

        symbols = {
            (item["market"], item["symbol"]): item
            for item in response.data["symbols"]
        }
        self.assertTrue(symbols[("NASDAQ", "NVDA")]["selected_in_last_cycle"])
        self.assertTrue(symbols[("NASDAQ", "NVDA")]["in_desired_set"])
        self.assertTrue(symbols[("NASDAQ", "NVDA")]["has_thresholds"])
        self.assertFalse(symbols[("NASDAQ", "NVDA")]["is_subscribed"])
        self.assertTrue(symbols[("NASDAQ", "PLTR")]["is_holding"])
        self.assertTrue(symbols[("NASDAQ", "PLTR")]["in_desired_set"])
        self.assertFalse(symbols[("NASDAQ", "SOFI")]["in_desired_set"])
        self.assertFalse(symbols[("NASDAQ", "SOFI")]["selected_in_last_cycle"])
        self.assertTrue(symbols[("NASDAQ", "SOFI")]["has_thresholds"])

    async def test_trigger_agent_cycle_reconciles_watchlist_after_successful_run(self):
        created_tasks = []
        real_create_task = asyncio.create_task

        def _capture_task(coro):
            task = real_create_task(coro)
            created_tasks.append(task)
            return task

        with patch.object(
            trading_agent,
            "preview_cycle",
            AsyncMock(return_value={"market_scope": "US", "trading_date": "2026-03-14"}),
        ), patch.object(
            trading_agent,
            "run_cycle",
            AsyncMock(return_value={"analyzed": 2, "executed": 0}),
        ), patch.object(
            activity_logger,
            "log",
            AsyncMock(),
        ), patch(
            "services.watchlist_sync.reconcile_market_watchlist",
            AsyncMock(return_value=[("NVDA", "NASDAQ")]),
        ) as reconcile_watchlist, patch(
            "api.routes.admin.asyncio.create_task",
            side_effect=_capture_task,
        ):
            response = await trigger_agent_cycle("NASDAQ")
            self.assertEqual(len(created_tasks), 1)
            await created_tasks[0]

        self.assertEqual(response.data["market"], "NASDAQ")
        reconcile_watchlist.assert_awaited_once_with("NASDAQ")


if __name__ == "__main__":
    unittest.main()
