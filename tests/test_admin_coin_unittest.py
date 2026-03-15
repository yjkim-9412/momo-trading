import asyncio
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, PropertyMock, patch

from agent.trading_agent import trading_agent
from agent.trading_agent._state_mixin import StateMixin
from agent.trading_agent._types import MarketState
from api.routes import admin_coin
from api.routes.admin_coin import (
    cancel_coin_order,
    get_coin_order,
    get_coin_settings,
    _serialize_recommendation,
    generate_coin_report,
    get_coin_activity_feed,
    get_coin_overview,
    get_coin_system_status,
    get_coin_watchlist,
    place_coin_order,
    preview_coin_order,
    trigger_coin_cycle,
    update_coin_settings,
)
from realtime.coin_monitor import coin_realtime_monitor
from scheduler.scheduler import trading_scheduler
from schemas.coin_order_schema import (
    CoinOrderCancelResponse,
    CoinOrderExecutionResponse,
    CoinOrderPlaceRequest,
    CoinOrderPreviewRequest,
    CoinOrderPreviewResponse,
    CoinOrderStatusResponse,
)
from services.coin_daily_report_service import CoinReportGenerationError
from trading.enums import OrderSide, OrderType
from trading.models import AccountBalance, AccountOverview, HoldingInfo, PendingOrderInfo


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
    async def test_trigger_coin_cycle_runs_manual_api_context(self):
        preview = {
            "allowed": True,
            "mode": "TRADING",
            "market_scope": "CRYPTO",
            "trading_date": "2026-03-14",
        }
        created_tasks = []
        original_create_task = asyncio.create_task

        def capture_task(coro):
            task = original_create_task(coro)
            created_tasks.append(task)
            return task

        with patch.object(admin_coin.settings, "CRYPTO_ENABLED", True), \
                patch.object(trading_agent, "preview_cycle", AsyncMock(return_value=preview)), \
                patch("api.routes.admin_coin.activity_logger.log", AsyncMock()), \
                patch.object(
                    trading_agent,
                    "run_cycle",
                    AsyncMock(return_value={"skipped": False}),
                ) as run_cycle, \
                patch(
                    "api.routes.admin_coin.asyncio.create_task",
                    side_effect=capture_task,
                ), \
                patch(
                    "services.watchlist_sync.reconcile_market_watchlist",
                    AsyncMock(return_value=["BTC"]),
                ) as reconcile_watchlist:
            response = await trigger_coin_cycle()
            await asyncio.gather(*created_tasks)

        run_cycle.assert_awaited_once_with(
            market="BITHUMB",
            trigger_source="MANUAL_API",
            trigger_reason="manual_trigger",
        )
        reconcile_watchlist.assert_awaited_once_with("BITHUMB")
        self.assertFalse(response.data["skipped"])
        self.assertEqual(response.data["market"], "BITHUMB")
        self.assertEqual(response.data["market_scope"], "CRYPTO")

    async def test_generate_coin_report_uses_service_and_refreshes_rules(self):
        runtime = MarketState(scope="CRYPTO", market_regime="BULL_RUN", market_context="최근 회고 문맥")
        report_time = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
        report = SimpleNamespace(
            id="report-1",
            market_scope="CRYPTO",
            report_date=report_time.date(),
            report_source="MANUAL",
            trigger_reason="manual_generate",
            applied_cycle_id=None,
            period_started_at=report_time - timedelta(hours=4),
            period_ended_at=report_time,
            total_cycles=2,
            total_analyses=4,
            total_recommendations=1,
            total_orders=1,
            buy_count=1,
            sell_count=0,
            win_count=0,
            loss_count=0,
            total_pnl=25000.0,
            unrealized_pnl=10000.0,
            open_position_count=1,
            total_24h_volume=123456789.0,
            btc_dominance=52.4,
            market_regime="BULL_RUN",
            market_summary="시장 요약",
            performance_review="성과 요약",
            lessons_learned="학습 포인트",
            next_day_plan="다음 사이클 계획",
            top_picks='["BTC"]',
            strategy_stats="{}",
            created_at=report_time,
        )

        with patch.object(admin_coin.settings, "CRYPTO_ENABLED", True), \
                patch.object(trading_agent, "get_runtime", return_value=runtime), \
                patch.object(
                    admin_coin.coin_daily_report_service,
                    "generate_checkpoint_report",
                    AsyncMock(return_value=report),
                ) as generate_report, \
                patch.object(
                    trading_agent,
                    "refresh_runtime_trading_rules",
                    AsyncMock(),
                ) as refresh_rules:
            response = await generate_coin_report()

        generate_report.assert_awaited_once_with(
            market="BITHUMB",
            report_source="MANUAL",
            trigger_reason="manual_generate",
            market_regime="BULL_RUN",
            market_context="최근 회고 문맥",
            period_anchor_sources={"AUTO_SETTLEMENT"},
            raise_on_error=True,
        )
        refresh_rules.assert_awaited_once_with(
            market="CRYPTO",
            emit_activity=True,
        )
        self.assertEqual(response.data.report_source, "MANUAL")
        self.assertEqual(response.data.market_scope, "CRYPTO")
        self.assertEqual(response.message, "코인 리포트 생성 완료")

    async def test_generate_coin_report_returns_failure_message_on_market_overview_error(self):
        runtime = MarketState(scope="CRYPTO", market_regime="BULL_RUN", market_context="최근 회고 문맥")

        with patch.object(admin_coin.settings, "CRYPTO_ENABLED", True), \
                patch.object(trading_agent, "get_runtime", return_value=runtime), \
                patch.object(
                    admin_coin.coin_daily_report_service,
                    "generate_checkpoint_report",
                    AsyncMock(
                        side_effect=CoinReportGenerationError(
                            "시장 개요 조회 실패",
                            user_message="시장 개요 조회 실패로 코인 리포트를 생성하지 않았습니다: DNS 해석 실패",
                        )
                    ),
                ), \
                patch.object(
                    trading_agent,
                    "refresh_runtime_trading_rules",
                    AsyncMock(),
                ) as refresh_rules:
            response = await generate_coin_report()

        refresh_rules.assert_not_awaited()
        self.assertIsNone(response.data)
        self.assertIn("시장 개요 조회 실패", response.message)

    async def test_get_coin_settings_includes_timebox_hours(self):
        with patch.object(admin_coin.settings, "CRYPTO_TIMEBOX_HOURS", 24):
            response = await get_coin_settings()

        self.assertEqual(response.data["CRYPTO_TIMEBOX_HOURS"], 24)

    async def test_update_coin_settings_accepts_only_12_or_24_for_timebox(self):
        original = admin_coin.settings.CRYPTO_TIMEBOX_HOURS

        try:
            with TemporaryDirectory() as temp_dir:
                env_file = Path(temp_dir) / ".env"
                env_file.write_text("CRYPTO_ENABLED=true\nCRYPTO_TIMEBOX_HOURS=12\n", encoding="utf-8")
                with patch.object(admin_coin, "ENV_FILE_PATH", env_file), \
                        patch("api.routes.admin_coin.activity_logger.log", AsyncMock()):
                    accepted = await update_coin_settings({"CRYPTO_TIMEBOX_HOURS": 24})
                    persisted_after_accept = env_file.read_text(encoding="utf-8")
                    rejected = await update_coin_settings({"CRYPTO_TIMEBOX_HOURS": 18})

                self.assertIn("CRYPTO_TIMEBOX_HOURS=24", persisted_after_accept)
                self.assertIn("CRYPTO_ENABLED=true", persisted_after_accept)
                self.assertEqual(env_file.read_text(encoding="utf-8"), persisted_after_accept)

            self.assertEqual(admin_coin.settings.CRYPTO_TIMEBOX_HOURS, 24)
            self.assertEqual(accepted.data["CRYPTO_TIMEBOX_HOURS"]["new"], 24)
            self.assertEqual(rejected.message, "코인 설정 변경이 거부되었습니다")
            self.assertEqual(rejected.data, {})
        finally:
            admin_coin.settings.CRYPTO_TIMEBOX_HOURS = original

    async def test_preview_coin_order_wraps_service_result(self):
        preview = CoinOrderPreviewResponse(
            symbol="BTC",
            side="BUY",
            order_type="MARKET",
            broker_order_type="PRICE",
            placeable=True,
        )

        with patch("api.routes.admin_coin.CoinOrderService.preview_order", AsyncMock(return_value=preview)):
            response = await preview_coin_order(
                CoinOrderPreviewRequest(
                    symbol="BTC",
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    amount_krw=5000,
                ),
                db=AsyncMock(),
            )

        self.assertEqual(response.data.symbol, "BTC")
        self.assertEqual(response.message, "코인 주문 미리보기 계산 완료")

    async def test_place_and_cancel_coin_order_wrap_service_result(self):
        execution = CoinOrderExecutionResponse(
            order_id="order-1",
            symbol="BTC",
            side="BUY",
            order_type="MARKET",
            broker_order_type="PRICE",
            status="SUBMITTED",
            message="주문이 접수되었습니다",
            preview=CoinOrderPreviewResponse(
                symbol="BTC",
                side="BUY",
                order_type="MARKET",
                broker_order_type="PRICE",
                placeable=True,
            ),
        )
        status = CoinOrderStatusResponse(
            order_id="order-1",
            symbol="BTC",
            side="BUY",
            order_type="MARKET",
            broker_order_type="PRICE",
            status="SUBMITTED",
        )
        cancel = CoinOrderCancelResponse(
            order_id="order-1",
            status="CANCELED",
            canceled=True,
            message="주문이 취소되었습니다",
        )

        with (
            patch("api.routes.admin_coin.CoinOrderService.place_order", AsyncMock(return_value=execution)),
            patch("api.routes.admin_coin.CoinOrderService.get_order_status", AsyncMock(return_value=status)),
            patch("api.routes.admin_coin.CoinOrderService.cancel_order", AsyncMock(return_value=cancel)),
        ):
            place_response = await place_coin_order(
                CoinOrderPlaceRequest(
                    symbol="BTC",
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    amount_krw=5000,
                ),
                db=AsyncMock(),
            )
            get_response = await get_coin_order("order-1", db=AsyncMock())
            cancel_response = await cancel_coin_order("order-1", db=AsyncMock())

        self.assertEqual(place_response.data.order_id, "order-1")
        self.assertEqual(get_response.data.status, "SUBMITTED")
        self.assertEqual(cancel_response.data.status, "CANCELED")
        self.assertEqual(cancel_response.message, "주문이 취소되었습니다")

    async def test_serialize_recommendation_exposes_amount_first_fields(self):
        row = SimpleNamespace(
            id="rec-1",
            coin_asset_id="coin-btc",
            action="BUY",
            suggested_price=150000000.0,
            suggested_quantity=0.001,
            suggested_amount_krw=150000.0,
            reason="금액 기준 테스트",
            confidence=0.82,
            status="PENDING",
            expires_at=datetime(2026, 3, 14, 13, 0, tzinfo=timezone.utc),
            created_at=datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc),
        )

        payload = _serialize_recommendation(row)

        self.assertEqual(payload["suggested_amount_krw"], 150000.0)
        self.assertEqual(payload["suggested_quantity"], 0.001)
        self.assertEqual(payload["suggested_price"], 150000000.0)

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
                    "trading.bithumb_client.bithumb_client.get_connectivity_status",
                    AsyncMock(
                        return_value={
                            "dns_api_ok": False,
                            "dns_ws_ok": False,
                            "market_catalog_ok": False,
                            "ticker_probe_ok": False,
                            "public_api_ok": False,
                            "api_host": "api.bithumb.com",
                            "ws_host": "ws-api.bithumb.com",
                            "api_ip": None,
                            "ws_ip": None,
                            "last_error_stage": "dns_resolution",
                            "last_error": "nodename nor servname provided, or not known",
                            "checked_at": "2026-03-14T00:01:00+00:00",
                        }
                    ),
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
        self.assertFalse(response.data["bithumb_connectivity"]["dns_api_ok"])
        self.assertFalse(response.data["bithumb_connectivity"]["market_catalog_ok"])
        self.assertFalse(response.data["bithumb_connectivity"]["ticker_probe_ok"])
        self.assertFalse(response.data["bithumb_connectivity"]["public_api_ok"])
        self.assertEqual(response.data["bithumb_connectivity"]["last_error_stage"], "dns_resolution")

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

    async def test_get_coin_overview_serializes_pending_orders_and_locked_krw(self):
        overview = AccountOverview(
            balance=AccountBalance(
                total_asset=10000000,
                cash=4200000,
                raw_cash=4200000,
                effective_cash=4200000,
                cash_source="BROKER",
                stock_value=5100000,
                locked_krw=700000,
                total_pnl=120000,
                total_pnl_rate=1.2,
                market="BITHUMB",
                currency="KRW",
            ),
            holdings=[
                HoldingInfo(
                    symbol="BTC",
                    name="비트코인",
                    market="BITHUMB",
                    currency="KRW",
                    quantity=0.12,
                    avg_buy_price=145000000,
                    current_price=150000000,
                    pnl=600000,
                    pnl_rate=3.44,
                )
            ],
            pending_orders=[
                PendingOrderInfo(
                    order_id="order-1",
                    symbol="BTC",
                    name="비트코인",
                    market="BITHUMB",
                    currency="KRW",
                    side="매수",
                    order_qty=0.05,
                    filled_qty=0.01,
                    remaining_qty=0.04,
                    order_price=149500000,
                    order_time="120501",
                    exchange_rate_to_krw=1.0,
                    status="PARTIAL",
                    status_detail="partial fill",
                    submitted_at="2026-03-14T12:05:01+09:00",
                    updated_at="2026-03-14T12:05:10+09:00",
                )
            ],
        )

        with patch.object(admin_coin.account_manager, "get_account_overview", AsyncMock(return_value=overview)):
            response = await get_coin_overview()

        self.assertEqual(response.data["balance"]["locked_krw"], 700000)
        self.assertEqual(response.data["holdings"][0]["symbol"], "BTC")
        self.assertEqual(response.data["pending_orders"][0]["order_id"], "order-1")
        self.assertEqual(response.data["pending_orders"][0]["status"], "PARTIAL")
        self.assertEqual(response.data["pending_orders"][0]["updated_at"], "2026-03-14T12:05:10+09:00")

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
