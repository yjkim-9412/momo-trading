import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, PropertyMock, patch

from agent.trading_agent import TradingAgent
from core.config import settings
from core.database import validate_database_schema
from trading.kis_websocket import KISWebSocket
from trading.mcp_client import mcp_client


class DatabaseSchemaValidationTest(unittest.IsolatedAsyncioTestCase):
    async def test_validate_database_schema_raises_when_required_table_missing(self):
        with patch("core.database.get_existing_table_names", AsyncMock(return_value={"alembic_version"})):
            with self.assertRaises(RuntimeError) as ctx:
                await validate_database_schema(required_tables={"agent_activity_logs"})

        self.assertIn("agent_activity_logs", str(ctx.exception))


class TradingAgentBrokerAvailabilityTest(unittest.IsolatedAsyncioTestCase):
    async def test_run_cycle_skips_when_mcp_is_unavailable(self):
        agent = TradingAgent()

        with patch.object(settings, "DAY_TRADING_ONLY", False), \
                patch("agent.trading_agent.market_calendar.is_trading_hours", return_value=True), \
                patch("agent.trading_agent.activity_logger.log", AsyncMock()), \
                patch.object(type(mcp_client), "is_connected", new_callable=PropertyMock, return_value=False):
            result = await agent.run_cycle(market="KRX")

        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "mcp_unavailable")
        self.assertEqual(result["selected_symbols"], [])
        runtime = agent.get_runtime("KRX")
        self.assertIsNotNone(runtime.last_cycle_attempt_at)
        self.assertEqual(runtime.last_cycle_status, "SKIPPED")
        self.assertIsNone(runtime.last_cycle_error)


class TradingAgentCycleFailureHandlingTest(unittest.IsolatedAsyncioTestCase):
    async def test_run_trading_cycle_records_error_and_cleans_up_runtime_state(self):
        agent = TradingAgent()
        fixed_now = datetime(2026, 3, 14, 1, 0, tzinfo=timezone.utc)

        with patch("util.time_util.now_kst", return_value=fixed_now), \
                patch("agent.trading_agent.activity_logger.start_cycle", return_value="cycle-1"), \
                patch("agent.trading_agent.activity_logger.timer", return_value=100.0), \
                patch("agent.trading_agent.activity_logger.log", AsyncMock()) as log_activity, \
                patch.object(agent, "_publish_event_safe", AsyncMock()), \
                patch.object(agent, "_broadcast_agent_state_safe", AsyncMock()) as broadcast_state, \
                patch("agent.trading_agent._cycle_mixin.llm_factory.start_session"), \
                patch("agent.trading_agent._cycle_mixin.llm_factory.end_session", return_value="session-ended"), \
                patch(
                    "agent.trading_agent._cycle_mixin.market_scanner.scan",
                    AsyncMock(side_effect=RuntimeError("scan exploded")),
                ):
            with self.assertRaises(RuntimeError):
                await agent._run_trading_cycle("KRX", scheduled_budget_remaining=1)

        runtime = agent.get_runtime("KRX")
        self.assertEqual(runtime.last_cycle_attempt_at, fixed_now)
        self.assertEqual(runtime.last_cycle_status, "ERROR")
        self.assertEqual(runtime.last_cycle_error, "market_scan: scan exploded")
        self.assertEqual(runtime._pipeline_snapshot, {})
        self.assertEqual(runtime.session_ids["cycle"], "session-ended")
        self.assertTrue(
            any(
                call.args[:2] == ("CYCLE", "ERROR")
                and "market_scan" in call.args[2]
                for call in log_activity.await_args_list
            )
        )
        final_payload = broadcast_state.await_args_list[-1].args[1]
        self.assertFalse(final_payload["data"]["cycle_active"])
        self.assertEqual(final_payload["data"]["cycle_id"], "cycle-1")

    async def test_run_trading_cycle_records_cancelled_state_and_cleanup(self):
        agent = TradingAgent()
        fixed_now = datetime(2026, 3, 14, 1, 5, tzinfo=timezone.utc)

        with patch("util.time_util.now_kst", return_value=fixed_now), \
                patch("agent.trading_agent.activity_logger.start_cycle", return_value="cycle-cancel"), \
                patch("agent.trading_agent.activity_logger.timer", return_value=200.0), \
                patch("agent.trading_agent.activity_logger.log", AsyncMock()), \
                patch.object(agent, "_publish_event_safe", AsyncMock()), \
                patch.object(agent, "_broadcast_agent_state_safe", AsyncMock()) as broadcast_state, \
                patch.object(agent, "_schedule_cycle_error_log") as schedule_error_log, \
                patch("agent.trading_agent._cycle_mixin.llm_factory.start_session"), \
                patch(
                    "agent.trading_agent._cycle_mixin.llm_factory.end_session",
                    return_value="session-cancelled",
                ), \
                patch(
                    "agent.trading_agent._cycle_mixin.market_scanner.scan",
                    AsyncMock(side_effect=asyncio.CancelledError()),
                ):
            with self.assertRaises(asyncio.CancelledError):
                await agent._run_trading_cycle("KRX", scheduled_budget_remaining=1)

        runtime = agent.get_runtime("KRX")
        self.assertEqual(runtime.last_cycle_attempt_at, fixed_now)
        self.assertEqual(runtime.last_cycle_status, "CANCELLED")
        self.assertEqual(
            runtime.last_cycle_error,
            "market_scan: asyncio.CancelledError",
        )
        self.assertEqual(runtime._pipeline_snapshot, {})
        self.assertEqual(runtime.session_ids["cycle"], "session-cancelled")
        schedule_error_log.assert_called_once()
        final_payload = broadcast_state.await_args_list[-1].args[1]
        self.assertFalse(final_payload["data"]["cycle_active"])
        self.assertEqual(final_payload["data"]["cycle_id"], "cycle-cancel")


class KISWebSocketConnectionTest(unittest.IsolatedAsyncioTestCase):
    async def test_connect_requires_approval_key(self):
        ws = KISWebSocket()

        with patch.object(KISWebSocket, "_get_approval_key", AsyncMock()):
            with self.assertRaises(ConnectionError):
                await ws.connect()

        self.assertFalse(ws._running)


if __name__ == "__main__":
    unittest.main()
