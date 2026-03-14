import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, PropertyMock, patch

from agent.trading_agent import trading_agent
from agent.trading_agent._state_mixin import StateMixin
from agent.trading_agent._types import MarketState
from api.routes import admin_coin
from api.routes.admin_coin import get_coin_activity_feed, get_coin_system_status, get_coin_watchlist
from realtime.coin_monitor import coin_realtime_monitor
from scheduler.scheduler import trading_scheduler


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _ExecuteResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _ScalarResult(self._rows)


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

        with patch.object(trading_agent, "_market_states", {"CRYPTO": runtime}), \
                patch.object(admin_coin.account_manager, "get_holdings", AsyncMock(return_value=[])), \
                patch(
                    "realtime.coin_stream_manager.coin_stream_manager.desired_keys",
                    return_value=set(),
                ), \
                patch(
                    "realtime.coin_stream_manager.coin_stream_manager.active_keys",
                    return_value=set(),
                ), \
                patch(
                    "realtime.coin_stream_manager.coin_stream_manager.stream_status",
                    return_value={"running": True, "active_count": 1},
                ), \
                patch("realtime.event_detector.event_detector._thresholds", {}):
            response = await get_coin_watchlist()

        self.assertEqual(len(response.data["symbols"]), 1)
        item = response.data["symbols"][0]
        self.assertEqual(item["symbol"], "BTC")
        self.assertEqual(item["market"], "BITHUMB")
        self.assertEqual(item["scan_source"], "DISCOVERY")
        self.assertEqual(item["trade_value"], 45678.0)
        self.assertEqual(item["reason"], "시장 기준점")

    async def test_get_coin_system_status_exposes_discovery_cache_metadata(self):
        runtime = MarketState(scope="CRYPTO", last_selected_watchlist=[{"symbol": "BTC"}])
        db = AsyncMock()
        db.scalar.return_value = 0

        with patch.object(trading_agent, "_market_states", {"CRYPTO": runtime}), \
                patch.object(
                    trading_agent,
                    "get_cycle_runtime_snapshot",
                    return_value={
                        "last_cycle_attempt_at": "2026-03-14T01:08:33+00:00",
                        "last_cycle_status": "COMPLETE",
                        "last_cycle_error": None,
                    },
                ), \
                patch.object(trading_agent, "_running", True), \
                patch.object(trading_agent, "_last_cycle_time", None), \
                patch.object(
                    admin_coin.market_calendar,
                    "get_session_schedule",
                    return_value={"current_session": "CRYPTO_ACTIVE", "sessions": []},
                ), \
                patch.object(
                    admin_coin.market_calendar,
                    "market_day_bounds",
                    return_value=(
                        datetime(2026, 3, 14, tzinfo=timezone.utc),
                        datetime(2026, 3, 15, tzinfo=timezone.utc),
                    ),
                ), \
                patch.object(type(admin_coin.coin_sse_manager), "client_count", new_callable=PropertyMock, return_value=1), \
                patch(
                    "realtime.coin_stream_manager.coin_stream_manager.stream_status",
                    return_value={"running": True, "connected": True},
                ), \
                patch(
                    "realtime.coin_stream_manager.coin_stream_manager.private_sync_status",
                    return_value={"running": True, "connected": True},
                ), \
                patch.object(
                    type(coin_realtime_monitor),
                    "is_running",
                    new_callable=PropertyMock,
                    return_value=True,
                ), \
                patch.object(
                    type(trading_scheduler),
                    "is_running",
                    new_callable=PropertyMock,
                    return_value=True,
                ), \
                patch(
                    "trading.bithumb_client.bithumb_client.get_discovery_cache_status",
                    return_value={
                        "source": "stale_cache",
                        "symbol_count": 30,
                        "refreshed_at": "2026-03-14T00:00:00+00:00",
                        "age_seconds": 120,
                        "last_error": "dns failure",
                    },
                ):
            response = await get_coin_system_status(db=db)

        self.assertTrue(response.data["crypto_dynamic_discovery_enabled"])
        self.assertEqual(response.data["discovery_cache"]["source"], "stale_cache")
        self.assertEqual(response.data["discovery_cache"]["symbol_count"], 30)
        self.assertEqual(response.data["watchlist_count"], 1)

    async def test_get_coin_activity_feed_returns_next_cursor_when_more_rows_exist(self):
        resolved_date = date(2026, 3, 14)
        newest = datetime(2026, 3, 14, 9, 0, tzinfo=timezone.utc)
        rows = [
            self._make_activity("log-3", newest, "BTC"),
            self._make_activity("log-2", newest - timedelta(seconds=5), "ETH"),
            self._make_activity("log-1", newest - timedelta(seconds=10), "XRP"),
        ]
        db = AsyncMock()
        db.execute.return_value = _ExecuteResult(rows)

        response = await get_coin_activity_feed(
            limit=2,
            target_date=resolved_date.isoformat(),
            db=db,
        )

        self.assertTrue(response.data.has_more)
        self.assertIsNotNone(response.data.next_cursor)
        self.assertEqual(response.data.next_cursor.before_id, "log-2")
        self.assertEqual(response.data.next_cursor.before_created_at, rows[1].created_at)
        self.assertEqual(response.data.resolved_trading_date, resolved_date)
        self.assertEqual([item.id for item in response.data.items], ["log-3", "log-2"])

    @staticmethod
    def _make_activity(activity_id: str, created_at: datetime, symbol: str) -> SimpleNamespace:
        return SimpleNamespace(
            id=activity_id,
            cycle_id="cycle-1",
            activity_type="DECISION",
            phase="COMPLETE",
            symbol=symbol,
            summary=f"{symbol} decision complete",
            detail='{"decision":"BUY"}',
            llm_provider="CODEX_CLI",
            llm_tier="TIER2",
            execution_time_ms=1200,
            confidence=0.91,
            error_message=None,
            created_at=created_at,
        )


class AgentStateBroadcastRoutingTest(unittest.IsolatedAsyncioTestCase):
    async def test_broadcast_agent_state_routes_bithumb_scope_to_coin_sse_manager(self):
        agent = StateMixin()
        payload = {"type": "agent_state", "data": {"cycle_active": True}}

        with patch.object(admin_coin.coin_sse_manager, "broadcast", AsyncMock()) as coin_broadcast, \
                patch("agent.trading_agent._state_mixin.sse_manager.broadcast", AsyncMock()) as stock_broadcast:
            await agent._broadcast_agent_state_safe("BITHUMB", payload, stage="unit-test")

        coin_broadcast.assert_awaited_once_with(payload)
        stock_broadcast.assert_not_awaited()

    async def test_broadcast_agent_state_keeps_non_crypto_scope_on_stock_sse_manager(self):
        agent = StateMixin()
        payload = {"type": "agent_state", "data": {"cycle_active": False}}

        with patch.object(admin_coin.coin_sse_manager, "broadcast", AsyncMock()) as coin_broadcast, \
                patch("agent.trading_agent._state_mixin.sse_manager.broadcast", AsyncMock()) as stock_broadcast:
            await agent._broadcast_agent_state_safe("KRX", payload, stage="unit-test")

        stock_broadcast.assert_awaited_once_with(payload)
        coin_broadcast.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
