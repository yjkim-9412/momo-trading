import unittest
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


class KISWebSocketConnectionTest(unittest.IsolatedAsyncioTestCase):
    async def test_connect_requires_approval_key(self):
        ws = KISWebSocket()

        with patch.object(KISWebSocket, "_get_approval_key", AsyncMock()):
            with self.assertRaises(ConnectionError):
                await ws.connect()

        self.assertFalse(ws._running)


if __name__ == "__main__":
    unittest.main()
