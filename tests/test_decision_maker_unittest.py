import asyncio
import unittest
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import delete, select

from agent.decision_maker import DecisionMaker
from models.agent_activity import AgentActivityLog
from models.base import Base
from models.broker_order import BrokerOrder
from models.coin_trade_result import CoinTradeResult
from models.exit_plan import ExitPlan, ExitPlanHistory
from models.trade_result import TradeResult
from realtime.event_detector import event_detector
from strategy.signal import TradeSignal
from trading.enums import SignalAction, SignalUrgency
from trading.models import HoldingInfo, MCPResponse
from tests.conftest import TestAsyncSessionLocal, test_async_engine
from util.time_util import KST, now_kst


class DecisionMakerPriceGuardTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        event_detector.clear_all()

    async def asyncTearDown(self):
        event_detector.clear_all()

    async def test_us_order_price_guard_blocks_krw_mispriced_limit_order(self):
        signal = TradeSignal(
            symbol="COIN",
            stock_id="",
            action=SignalAction.BUY,
            strength=0.7,
            suggested_price=42300.0,
            suggested_quantity=1,
            urgency=SignalUrgency.IMMEDIATE,
            metadata={
                "market": "NASDAQ",
                "currency": "USD",
                "exchange_rate_to_krw": 215.16,
                "live_price": 196.6,
                "live_price_krw": 42300.456,
                "entry_price_krw": 42300.0,
            },
        )

        maker = DecisionMaker()

        with (
            patch("agent.decision_maker.activity_logger.log", AsyncMock()) as log_mock,
            patch("agent.decision_maker.event_bus.publish", AsyncMock()) as publish_mock,
            patch("agent.decision_maker.mcp_client.place_order", AsyncMock()) as place_order_mock,
        ):
            result = await maker.execute(signal, cycle_id="cycle-1")

        self.assertFalse(result["success"])
        self.assertIn("실시간 현재가 대비 과도하게 벗어남", result["message"])
        place_order_mock.assert_not_awaited()
        publish_mock.assert_awaited_once()
        self.assertGreaterEqual(log_mock.await_count, 2)

    async def test_failed_buy_clears_trade_thresholds_only(self):
        signal = TradeSignal(
            symbol="CRCD",
            stock_id="stock-1",
            action=SignalAction.BUY,
            strength=0.7,
            suggested_price=7.17,
            suggested_quantity=2,
            urgency=SignalUrgency.IMMEDIATE,
            metadata={
                "market": "AMEX",
                "currency": "USD",
                "exchange_rate_to_krw": 1450.0,
                "live_price": 7.17,
                "live_price_krw": 10396.5,
                "entry_price_krw": 10396.5,
            },
        )
        event_detector.set_thresholds(
            "CRCD",
            market="AMEX",
            surge_pct=3.5,
            drop_pct=-3.5,
            volume_spike_ratio=4.0,
            stop_loss=6.8,
            take_profit=7.9,
            trailing_stop_pct=2.0,
        )

        maker = DecisionMaker()

        with (
            patch("agent.decision_maker.settings.KIS_ACCOUNT_TYPE", "REAL"),
            patch("agent.decision_maker.activity_logger.log", AsyncMock()),
            patch("agent.decision_maker.event_bus.publish", AsyncMock()),
            patch(
                "agent.decision_maker.mcp_client.place_order",
                AsyncMock(return_value=MCPResponse(success=False, error="주문 실패")),
            ),
        ):
            result = await maker.execute(signal, cycle_id="cycle-failed-buy")

        self.assertFalse(result["success"])
        thresholds = event_detector.get_thresholds("CRCD", market="AMEX")
        self.assertEqual(thresholds.surge_pct, 3.5)
        self.assertEqual(thresholds.drop_pct, -3.5)
        self.assertEqual(thresholds.volume_spike_ratio, 4.0)
        self.assertEqual(thresholds.stop_loss, 0.0)
        self.assertEqual(thresholds.take_profit, 0.0)
        self.assertEqual(thresholds.trailing_stop_pct, 0.0)

    async def test_paper_us_premarket_falls_back_to_recommendation(self):
        signal = TradeSignal(
            symbol="COIN",
            stock_id="stock-1",
            action=SignalAction.BUY,
            strength=0.7,
            suggested_price=198.78,
            suggested_quantity=1,
            urgency=SignalUrgency.IMMEDIATE,
            confidence=0.81,
            reason="Premarket breakout",
            metadata={
                "market": "NASDAQ",
                "currency": "USD",
                "price_krw": 42700.0,
                "live_price": 198.78,
                "live_price_krw": 42700.0,
            },
        )

        maker = DecisionMaker()

        with (
            patch("agent.decision_maker.settings.KIS_ACCOUNT_TYPE", "VIRTUAL"),
            patch("agent.decision_maker.market_calendar.get_market_session", return_value="US_PRE"),
            patch("agent.decision_maker.activity_logger.log", AsyncMock()) as log_mock,
            patch("agent.decision_maker.event_bus.publish", AsyncMock()) as publish_mock,
            patch("agent.decision_maker.mcp_client.place_order", AsyncMock()) as place_order_mock,
        ):
            result = await maker.execute(signal, analysis_id="analysis-1", cycle_id="cycle-2")

        self.assertEqual(result["mode"], "AUTONOMOUS_FALLBACK")
        self.assertEqual(result["fallback_session"], "US_PRE")
        self.assertEqual(result["recommendation"]["analysis_id"], "analysis-1")
        self.assertEqual(result["recommendation"]["status"], "PENDING")
        expires_at = result["recommendation"]["expires_at"]
        self.assertGreater(expires_at, now_kst())
        self.assertLess(expires_at, now_kst() + timedelta(hours=1))
        place_order_mock.assert_not_awaited()
        publish_mock.assert_awaited_once()
        self.assertGreaterEqual(log_mock.await_count, 3)

    async def test_confirm_and_record_retries_until_overseas_fill_is_visible(self):
        maker = DecisionMaker()
        maker._load_broker_order = AsyncMock(return_value=None)
        maker._upsert_broker_order = AsyncMock()
        maker._record_trade_result = AsyncMock()

        responses = [
            MCPResponse(success=True, data={
                "output": [{
                    "order_id": "0000041303",
                    "symbol": "PLTR",
                    "name": "Palantir",
                    "market": "NASDAQ",
                    "currency": "USD",
                    "order_qty": 1000,
                    "filled_qty": 0,
                    "remaining_qty": 1000,
                    "order_price": 154.16,
                    "filled_price": 0.0,
                    "exchange_rate_to_krw": 1450.0,
                    "status": "접수",
                }],
            }),
            MCPResponse(success=True, data={
                "output": [{
                    "order_id": "0000041303",
                    "symbol": "PLTR",
                    "name": "Palantir",
                    "market": "NASDAQ",
                    "currency": "USD",
                    "order_qty": 1000,
                    "filled_qty": 1000,
                    "remaining_qty": 0,
                    "order_price": 154.16,
                    "filled_price": 154.16,
                    "exchange_rate_to_krw": 1450.0,
                    "status": "체결",
                }],
            }),
        ]

        with (
            patch("agent.decision_maker.asyncio.sleep", AsyncMock()),
            patch("agent.decision_maker.mcp_client.get_order_list", AsyncMock(side_effect=responses)) as order_list_mock,
            patch("agent.decision_maker.mcp_client._get_exchange_rate_to_krw", AsyncMock(return_value=1450.0)),
            patch("trading.account_manager.account_manager.invalidate_cache"),
        ):
            await maker.confirm_and_record(
                symbol="PLTR",
                market="NASDAQ",
                side="BUY",
                order_id="0000041303",
                quantity=1000,
                expected_price=154.16,
                analysis_context={"stock_name": "Palantir", "currency": "USD", "strategy_type": "AGGRESSIVE_SHORT"},
                cycle_id="cycle-3",
            )

        self.assertEqual(order_list_mock.await_count, 2)
        maker._upsert_broker_order.assert_awaited_once()
        maker._record_trade_result.assert_awaited_once()
        self.assertEqual(maker._record_trade_result.await_args.kwargs["filled_qty"], 1000)
        self.assertEqual(maker._record_trade_result.await_args.kwargs["currency"], "USD")

    async def test_confirm_and_record_polls_domestic_order_list_until_fill_is_visible(self):
        maker = DecisionMaker()
        maker._load_broker_order = AsyncMock(return_value=None)
        maker._upsert_broker_order = AsyncMock()
        maker._record_trade_result = AsyncMock()

        filled_response = MCPResponse(success=True, data={
            "output": [{
                "order_id": "0000012345",
                "market": "KRX",
                "symbol": "005930",
                "name": "삼성전자",
                "status": "체결",
                "order_qty": 10,
                "filled_qty": 10,
                "remaining_qty": 0,
                "order_price": 70000.0,
                "filled_price": 70100.0,
                "currency": "KRW",
                "exchange_rate_to_krw": 1.0,
            }],
        })

        async def _timeout_wait_for(coro, timeout):
            coro.close()
            raise asyncio.TimeoutError

        with (
            patch("agent.decision_maker.asyncio.sleep", AsyncMock()),
            patch("agent.decision_maker.asyncio.wait_for", AsyncMock(side_effect=_timeout_wait_for)),
            patch(
                "agent.decision_maker.mcp_client.get_order_list",
                AsyncMock(return_value=filled_response),
            ) as order_list_mock,
            patch("agent.decision_maker.mcp_client._get_exchange_rate_to_krw", AsyncMock(return_value=1.0)),
            patch("trading.account_manager.account_manager.invalidate_cache"),
        ):
            await maker.confirm_and_record(
                symbol="005930",
                market="KRX",
                side="BUY",
                order_id="0000012345",
                quantity=10,
                expected_price=70000.0,
                analysis_context={"stock_name": "삼성전자", "currency": "KRW"},
                cycle_id="cycle-domestic",
            )

        order_list_mock.assert_awaited_once()
        maker._record_trade_result.assert_awaited_once()
        self.assertEqual(maker._record_trade_result.await_args.kwargs["filled_price"], 70100.0)
        self.assertEqual(maker._record_trade_result.await_args.kwargs["filled_qty"], 10)

    async def test_confirm_and_record_uses_requested_price_when_filled_price_is_string_zero(self):
        maker = DecisionMaker()
        maker._load_broker_order = AsyncMock(return_value=None)
        maker._upsert_broker_order = AsyncMock()
        maker._record_trade_result = AsyncMock()

        filled_response = MCPResponse(success=True, data={
            "output": [{
                "order_id": "0000036701",
                "market": "KRX",
                "symbol": "003670",
                "name": "포스코퓨처엠",
                "status": "체결",
                "order_qty": 1,
                "filled_qty": 1,
                "remaining_qty": 0,
                "order_price": 227500.0,
                "filled_price": "0",
                "currency": "KRW",
                "exchange_rate_to_krw": 1.0,
            }],
        })

        async def _timeout_wait_for(coro, timeout):
            coro.close()
            raise asyncio.TimeoutError

        with (
            patch("agent.decision_maker.asyncio.sleep", AsyncMock()),
            patch("agent.decision_maker.asyncio.wait_for", AsyncMock(side_effect=_timeout_wait_for)),
            patch(
                "agent.decision_maker.mcp_client.get_order_list",
                AsyncMock(return_value=filled_response),
            ),
            patch("agent.decision_maker.mcp_client._get_exchange_rate_to_krw", AsyncMock(return_value=1.0)),
            patch("trading.account_manager.account_manager.invalidate_cache"),
        ):
            await maker.confirm_and_record(
                symbol="003670",
                market="KRX",
                side="BUY",
                order_id="0000036701",
                quantity=1,
                expected_price=227500.0,
                analysis_context={"stock_name": "포스코퓨처엠", "currency": "KRW"},
                cycle_id="cycle-domestic-zero-price",
            )

        maker._record_trade_result.assert_awaited_once()
        record_kwargs = maker._record_trade_result.await_args.kwargs
        self.assertEqual(record_kwargs["filled_price"], 227500.0)
        self.assertEqual(record_kwargs["analysis_context"]["requested_price"], 227500.0)

    async def test_confirm_and_record_buy_fill_activates_trade_thresholds_from_payload(self):
        maker = DecisionMaker()
        maker._load_broker_order = AsyncMock(return_value=None)
        maker._upsert_broker_order = AsyncMock()
        maker._record_trade_result = AsyncMock(return_value={"exit_plan_id": "plan-319400"})

        event_detector.set_thresholds(
            "319400",
            market="KRX",
            surge_pct=3.0,
            drop_pct=-3.0,
            volume_spike_ratio=4.0,
        )

        filled_response = MCPResponse(success=True, data={
            "output": [{
                "order_id": "0000091001",
                "market": "KRX",
                "symbol": "319400",
                "name": "테스트종목",
                "status": "체결",
                "order_qty": 6,
                "filled_qty": 6,
                "remaining_qty": 0,
                "order_price": 29500.0,
                "filled_price": 29480.0,
                "currency": "KRW",
                "exchange_rate_to_krw": 1.0,
            }],
        })

        async def _timeout_wait_for(coro, timeout):
            coro.close()
            raise asyncio.TimeoutError

        with (
            patch("agent.decision_maker.asyncio.sleep", AsyncMock()),
            patch("agent.decision_maker.asyncio.wait_for", AsyncMock(side_effect=_timeout_wait_for)),
            patch(
                "agent.decision_maker.mcp_client.get_order_list",
                AsyncMock(return_value=filled_response),
            ) as order_list_mock,
            patch("agent.decision_maker.mcp_client._get_exchange_rate_to_krw", AsyncMock(return_value=1.0)),
            patch("trading.account_manager.account_manager.invalidate_cache"),
        ):
            await maker.confirm_and_record(
                symbol="319400",
                market="KRX",
                side="BUY",
                order_id="0000091001",
                quantity=6,
                expected_price=29500.0,
                analysis_context={
                    "stock_name": "테스트종목",
                    "currency": "KRW",
                    "ai_stop_loss_price": 28825.0,
                    "ai_take_profit_price": 30035.0,
                    "trailing_stop_pct": 2.8,
                    "trade_threshold_payload": {
                        "stop_loss": 28825.0,
                        "take_profit": 30035.0,
                        "trailing_stop_pct": 2.8,
                        "tp_levels": [
                            {"price": 30035.0, "pct": 50, "level_index": 0},
                            {"price": 30750.0, "pct": 100, "level_index": 1},
                        ],
                    },
                },
                cycle_id="cycle-threshold-activate",
            )

        order_list_mock.assert_awaited_once()
        maker._record_trade_result.assert_awaited_once()
        thresholds = event_detector.get_thresholds("319400", market="KRX")
        self.assertEqual(thresholds.surge_pct, 3.0)
        self.assertEqual(thresholds.drop_pct, -3.0)
        self.assertEqual(thresholds.volume_spike_ratio, 4.0)
        self.assertEqual(thresholds.stop_loss, 28825.0)
        self.assertEqual(thresholds.take_profit, 30035.0)
        self.assertEqual(thresholds.trailing_stop_pct, 2.8)
        self.assertEqual(thresholds.exit_plan_id, "plan-319400")
        self.assertEqual(len(thresholds.tp_levels), 2)

    async def test_confirm_and_record_zero_fill_clears_trade_thresholds_only(self):
        maker = DecisionMaker()
        maker._load_broker_order = AsyncMock(return_value=None)
        maker._upsert_broker_order = AsyncMock(return_value=object())
        maker._record_trade_result = AsyncMock()

        event_detector.set_thresholds(
            "319400",
            market="KRX",
            surge_pct=3.0,
            drop_pct=-3.0,
            volume_spike_ratio=4.0,
            stop_loss=28825.0,
            take_profit=30035.0,
            trailing_stop_pct=2.8,
        )

        open_response = MCPResponse(success=True, data={
            "output": [{
                "order_id": "0000091002",
                "market": "KRX",
                "symbol": "319400",
                "name": "테스트종목",
                "status": "접수",
                "order_qty": 6,
                "filled_qty": 0,
                "remaining_qty": 6,
                "order_price": 29500.0,
                "filled_price": 0.0,
                "currency": "KRW",
                "exchange_rate_to_krw": 1.0,
            }],
        })

        async def _timeout_wait_for(coro, timeout):
            coro.close()
            raise asyncio.TimeoutError

        with (
            patch("agent.decision_maker.asyncio.sleep", AsyncMock()),
            patch("agent.decision_maker.asyncio.wait_for", AsyncMock(side_effect=_timeout_wait_for)),
            patch(
                "agent.decision_maker.mcp_client.get_order_list",
                AsyncMock(return_value=open_response),
            ),
            patch("agent.decision_maker.mcp_client._get_exchange_rate_to_krw", AsyncMock(return_value=1.0)),
        ):
            await maker.confirm_and_record(
                symbol="319400",
                market="KRX",
                side="BUY",
                order_id="0000091002",
                quantity=6,
                expected_price=29500.0,
                analysis_context={"stock_name": "테스트종목", "currency": "KRW"},
                cycle_id="cycle-threshold-clear",
            )

        maker._record_trade_result.assert_not_awaited()
        thresholds = event_detector.get_thresholds("319400", market="KRX")
        self.assertEqual(thresholds.surge_pct, 3.0)
        self.assertEqual(thresholds.drop_pct, -3.0)
        self.assertEqual(thresholds.volume_spike_ratio, 4.0)
        self.assertEqual(thresholds.stop_loss, 0.0)
        self.assertEqual(thresholds.take_profit, 0.0)
        self.assertEqual(thresholds.trailing_stop_pct, 0.0)

    async def test_execute_logs_error_when_broker_ledger_write_fails(self):
        signal = TradeSignal(
            symbol="COIN",
            stock_id="stock-1",
            action=SignalAction.BUY,
            strength=0.7,
            suggested_price=200.86,
            suggested_quantity=300,
            urgency=SignalUrgency.IMMEDIATE,
            metadata={
                "market": "NASDAQ",
                "currency": "USD",
                "exchange_rate_to_krw": 1450.0,
                "live_price": 200.86,
                "live_price_krw": 291247.0,
                "entry_price_krw": 291247.0,
            },
        )

        maker = DecisionMaker()
        maker._upsert_broker_order = AsyncMock(return_value=None)
        maker.confirm_and_record = AsyncMock()

        class DummyTask:
            def add_done_callback(self, callback):
                return None

        def fake_create_task(coro):
            coro.close()
            return DummyTask()

        with (
            patch("agent.decision_maker.activity_logger.log", AsyncMock()) as log_mock,
            patch("agent.decision_maker.event_bus.publish", AsyncMock()),
            patch("agent.decision_maker.market_calendar.get_market_session", return_value="US_REGULAR"),
            patch("agent.decision_maker.asyncio.create_task", side_effect=fake_create_task),
            patch.object(maker, "_find_stock_buy_dedupe_issue", AsyncMock(return_value=None)),
            patch(
                "agent.decision_maker.mcp_client.place_order",
                AsyncMock(return_value=MCPResponse(success=True, data={
                    "order_id": "0000041726",
                    "market": "NASDAQ",
                    "currency": "USD",
                    "exchange_rate_to_krw": 1450.0,
                    "msg1": "모의투자 매수주문이 완료 되었습니다.",
                    "output": {"ORD_QTY": "300"},
                })),
            ),
        ):
            result = await maker.execute(signal, cycle_id="cycle-ledger")

        self.assertTrue(result["success"])
        self.assertFalse(result["broker_order_recorded"])
        self.assertEqual(log_mock.await_args_list[-1].args[1], "ERROR")
        self.assertIn("broker ledger 저장 실패", log_mock.await_args_list[-1].args[2])

    def test_extract_broker_order_payload_from_activity_log(self):
        created_at = now_kst()
        activity = AgentActivityLog(
            cycle_id="cycle-1",
            market_scope="US",
            trading_date=created_at.date(),
            activity_type="DECISION",
            phase="COMPLETE",
            symbol="COIN",
            summary="✅ [COIN] 주문 접수 완료 (체결 대기) — 주문번호: 0000041726",
            detail="""{
                "symbol": "COIN",
                "action": "BUY",
                "success": true,
                "order_id": "0000041726",
                "requested_price": 200.86,
                "requested_price_krw": 291247.0,
                "currency": "USD",
                "message": "주문 접수",
                "data": {
                    "market": "NASDAQ",
                    "currency": "USD",
                    "exchange_rate_to_krw": 1450.0,
                    "msg1": "모의투자 매수주문이 완료 되었습니다.",
                    "output": {"ORD_QTY": "300"}
                }
            }""",
            created_at=created_at,
        )

        payload = DecisionMaker._extract_broker_order_payload(activity)

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["order_id"], "0000041726")
        self.assertEqual(payload["market"], "NASDAQ")
        self.assertEqual(payload["quantity"], 300)
        self.assertEqual(payload["status"], "SUBMITTED")
        self.assertEqual(payload["currency"], "USD")


class DecisionMakerTradeResultPersistenceTest(unittest.IsolatedAsyncioTestCase):
    async def test_record_trade_result_persists_trailing_stop_pct_in_notes(self):
        maker = DecisionMaker()
        repo = MagicMock()
        repo.get_open_buy = AsyncMock(return_value=None)

        class DummyTransaction:
            async def __aenter__(self):
                return None

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class DummySession:
            def __init__(self):
                self.added = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def begin(self):
                return DummyTransaction()

            def add(self, obj):
                self.added.append(obj)

        session = DummySession()

        with (
            patch("agent.decision_maker.AsyncSessionLocal", return_value=session),
            patch("agent.decision_maker.TradeResultRepository", return_value=repo),
            patch("agent.decision_maker.activity_logger.log", AsyncMock()),
        ):
            await maker._record_trade_result(
                symbol="AAPL",
                market="NASDAQ",
                side="BUY",
                order_id="ord-1",
                filled_qty=3,
                filled_price=151.5,
                currency="USD",
                exchange_rate_to_krw=1450.0,
                analysis_context={
                    "stock_name": "Apple",
                    "strategy_type": "STABLE_SHORT",
                    "ai_recommendation": "BUY",
                    "ai_confidence": 0.82,
                    "ai_target_price": 160.0,
                    "ai_stop_loss_price": 145.0,
                    "ai_take_profit_price": 157.0,
                    "trailing_stop_pct": 3.5,
                    "planned_hold_days": 4,
                },
                cycle_id="cycle-tr-1",
            )

        self.assertEqual(len(session.added), 1)
        trade_result = session.added[0]
        notes = json.loads(trade_result.notes)
        self.assertEqual(notes["trailing_stop_pct"], 3.5)
        self.assertEqual(notes["planned_hold_days"], 4)
        self.assertEqual(notes["close_review_count"], 0)
        self.assertIsNone(notes["last_close_review_date"])
        self.assertEqual(trade_result.ai_take_profit_price, 157.0)
        self.assertEqual(trade_result.ai_stop_loss_price, 145.0)

    async def test_record_trade_result_persists_exit_plan_context_in_notes(self):
        maker = DecisionMaker()
        repo = MagicMock()
        repo.get_open_buy = AsyncMock(return_value=None)
        maker._sync_exit_plan = AsyncMock(return_value=MagicMock(id="plan-aapl"))

        class DummyTransaction:
            async def __aenter__(self):
                return None

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class DummySession:
            def __init__(self):
                self.added = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def begin(self):
                return DummyTransaction()

            def add(self, obj):
                self.added.append(obj)

        session = DummySession()

        with (
            patch("agent.decision_maker.AsyncSessionLocal", return_value=session),
            patch("agent.decision_maker.TradeResultRepository", return_value=repo),
            patch("agent.decision_maker.activity_logger.log", AsyncMock()),
        ):
            await maker._record_trade_result(
                symbol="AAPL",
                market="NASDAQ",
                side="BUY",
                order_id="ord-1a",
                filled_qty=2,
                filled_price=151.5,
                currency="USD",
                exchange_rate_to_krw=1450.0,
                analysis_context={
                    "stock_name": "Apple",
                    "strategy_type": "STABLE_SHORT",
                    "ai_recommendation": "BUY",
                    "ai_confidence": 0.82,
                    "ai_target_price": 160.0,
                    "ai_stop_loss_price": 145.0,
                    "ai_take_profit_price": 157.0,
                    "trailing_stop_pct": 3.5,
                    "planned_hold_days": 4,
                    "trade_threshold_payload": {
                        "stop_loss": 145.0,
                        "take_profit": 157.0,
                        "trailing_stop_pct": 3.5,
                    },
                },
                cycle_id="cycle-tr-1a",
            )

        self.assertEqual(len(session.added), 1)
        trade_result = session.added[0]
        notes = json.loads(trade_result.notes)
        self.assertEqual(notes["trade_threshold_payload"]["stop_loss"], 145.0)
        self.assertEqual(notes["trade_threshold_payload"]["take_profit"], 157.0)
        self.assertEqual(notes["exit_levels"][0]["price"], 157.0)
        self.assertEqual(notes["exit_levels"][0]["pct"], 100)

    async def test_record_trade_result_buy_add_on_uses_requested_price_fallback(self):
        maker = DecisionMaker()
        open_buy = TradeResult(
            order_id="buy-open",
            stock_symbol="003670",
            stock_name="포스코퓨처엠",
            currency="KRW",
            exchange_rate_to_krw=1.0,
            market="KRX",
            side="BUY",
            strategy_type="AGGRESSIVE_SHORT",
            entry_price=227500.0,
            entry_price_krw=227500.0,
            exit_price=0.0,
            exit_price_krw=0.0,
            quantity=1,
            raw_pnl=0.0,
            pnl=0.0,
            return_pct=0.0,
            is_win=False,
            hold_days=0,
            ai_recommendation="BUY",
            ai_confidence=0.7,
            entry_at=now_kst(),
            notes=json.dumps({}, ensure_ascii=False),
        )
        repo = MagicMock()
        repo.get_open_buy = AsyncMock(return_value=open_buy)
        maker._sync_exit_plan = AsyncMock(return_value=MagicMock(id="plan-003670"))

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

        session = DummySession()

        with (
            patch("agent.decision_maker.AsyncSessionLocal", return_value=session),
            patch("agent.decision_maker.TradeResultRepository", return_value=repo),
            patch("agent.decision_maker.activity_logger.log", AsyncMock()),
        ):
            await maker._record_trade_result(
                symbol="003670",
                market="KRX",
                side="BUY",
                order_id="ord-003670-add",
                filled_qty=1,
                filled_price=0.0,
                currency="KRW",
                exchange_rate_to_krw=1.0,
                analysis_context={
                    "stock_name": "포스코퓨처엠",
                    "strategy_type": "AGGRESSIVE_SHORT",
                    "ai_recommendation": "BUY",
                    "ai_confidence": 0.7,
                    "ai_target_price": 250000.0,
                    "ai_stop_loss_price": 212500.0,
                    "ai_take_profit_price": 250000.0,
                    "requested_price": 228000.0,
                    "requested_price_krw": 228000.0,
                },
                cycle_id="cycle-tr-003670-add",
            )

        self.assertEqual(open_buy.quantity, 2)
        self.assertAlmostEqual(open_buy.entry_price, 227750.0)
        self.assertAlmostEqual(open_buy.entry_price_krw, 227750.0)
        notes = json.loads(open_buy.notes)
        self.assertEqual(notes["entry_price_source"], "requested_fallback")

    async def test_sync_exit_plan_creates_single_tp_fallback_when_exit_levels_missing(self):
        maker = DecisionMaker()
        created_plan = MagicMock(id="plan-003670")

        with (
            patch(
                "strategy.exit_plan_manager.exit_plan_manager.create_plan",
                AsyncMock(return_value=created_plan),
            ) as create_plan_mock,
            patch(
                "strategy.exit_plan_manager.exit_plan_manager.update_plan",
                AsyncMock(return_value=None),
            ),
        ):
            result = await maker._sync_exit_plan(
                symbol="003670",
                market="KRX",
                avg_entry_price=227666.6667,
                total_quantity=3,
                analysis_context={
                    "ai_stop_loss_price": 212500.0,
                    "ai_take_profit_price": 250000.0,
                    "trailing_stop_pct": 6.5,
                    "trade_threshold_payload": {
                        "stop_loss": 212500.0,
                        "take_profit": 250000.0,
                        "trailing_stop_pct": 6.5,
                    },
                },
                is_add_on=True,
                order_id="ord-003670-plan",
            )

        self.assertIs(result, created_plan)
        create_kwargs = create_plan_mock.await_args.kwargs
        self.assertEqual(create_kwargs["avg_entry_price"], 227666.6667)
        self.assertEqual(create_kwargs["total_quantity"], 3)
        self.assertEqual(create_kwargs["trailing_stop_pct"], 6.5)
        self.assertEqual(create_kwargs["levels"][0]["type"], "TAKE_PROFIT")
        self.assertEqual(create_kwargs["levels"][0]["price"], 250000.0)
        self.assertEqual(create_kwargs["levels"][-1]["type"], "STOP_LOSS")
        self.assertEqual(create_kwargs["levels"][-1]["price"], 212500.0)

    async def test_record_trade_result_tags_us_premarket_scalp_entry_in_notes(self):
        maker = DecisionMaker()
        repo = MagicMock()
        repo.get_open_buy = AsyncMock(return_value=None)

        class DummyTransaction:
            async def __aenter__(self):
                return None

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class DummySession:
            def __init__(self):
                self.added = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def begin(self):
                return DummyTransaction()

            def add(self, obj):
                self.added.append(obj)

        session = DummySession()

        with (
            patch("agent.decision_maker.AsyncSessionLocal", return_value=session),
            patch("agent.decision_maker.TradeResultRepository", return_value=repo),
            patch("agent.decision_maker.activity_logger.log", AsyncMock()),
            patch("agent.decision_maker.market_calendar.get_market_session", return_value="US_PRE"),
            patch("agent.decision_maker.settings.US_PREMARKET_ENABLED", True),
            patch("agent.decision_maker.settings.US_PREMARKET_SCALP_ENABLED", True),
            patch("agent.decision_maker.settings.US_PREMARKET_SCALP_FORCE_LIQUIDATION_HOUR", 9),
            patch("agent.decision_maker.settings.US_PREMARKET_SCALP_FORCE_LIQUIDATION_MINUTE", 25),
        ):
            await maker._record_trade_result(
                symbol="PLTR",
                market="NASDAQ",
                side="BUY",
                order_id="ord-pre-1",
                filled_qty=2,
                filled_price=25.5,
                currency="USD",
                exchange_rate_to_krw=1450.0,
                analysis_context={
                    "stock_name": "Palantir",
                    "strategy_type": "AGGRESSIVE_SHORT",
                },
                cycle_id="cycle-pre-1",
            )

        self.assertEqual(len(session.added), 1)
        trade_result = session.added[0]
        notes = json.loads(trade_result.notes)
        self.assertEqual(notes["entry_session"], "US_PRE")
        self.assertEqual(notes["holding_policy"], "PREMARKET_SCALP")
        self.assertEqual(notes["must_exit_by_time"], "09:25")
        self.assertEqual(notes["must_exit_tz"], "America/New_York")


class DecisionMakerBuyDedupeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async with test_async_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with TestAsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(delete(BrokerOrder))

    async def asyncTearDown(self):
        async with TestAsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(delete(BrokerOrder))

    @staticmethod
    def _signal() -> TradeSignal:
        return TradeSignal(
            symbol="003670",
            stock_id="stock-003670",
            action=SignalAction.BUY,
            strength=0.8,
            suggested_price=228000.0,
            suggested_quantity=1,
            urgency=SignalUrgency.IMMEDIATE,
            metadata={
                "market": "KRX",
                "currency": "KRW",
                "exchange_rate_to_krw": 1.0,
                "live_price": 228000.0,
                "live_price_krw": 228000.0,
                "entry_price_krw": 228000.0,
            },
        )

    async def test_execute_blocks_duplicate_buy_when_pending_order_exists(self):
        async with TestAsyncSessionLocal() as session:
            async with session.begin():
                session.add(
                    BrokerOrder(
                        cycle_id="cycle-existing",
                        market="KRX",
                        symbol="003670",
                        stock_name="포스코퓨처엠",
                        side="BUY",
                        status="OPEN",
                        strategy_type="STABLE_SHORT",
                        quantity=1,
                        requested_price=227500.0,
                        requested_price_krw=227500.0,
                        filled_quantity=0,
                        filled_price=0.0,
                        filled_price_krw=0.0,
                        currency="KRW",
                        exchange_rate_to_krw=1.0,
                        kis_order_id="pending-003670",
                        submitted_at=now_kst(),
                    )
                )

        maker = DecisionMaker()
        with (
            patch("agent.decision_maker.AsyncSessionLocal", TestAsyncSessionLocal),
            patch("agent.decision_maker.activity_logger.log", AsyncMock()) as log_mock,
            patch("agent.decision_maker.event_bus.publish", AsyncMock()) as publish_mock,
            patch("agent.decision_maker.mcp_client.place_order", AsyncMock()) as place_order_mock,
        ):
            result = await maker.execute(self._signal(), cycle_id="cycle-dedupe-open")

        self.assertFalse(result["success"])
        self.assertEqual(result["data"]["reason_code"], "BUY_ORDER_DEDUPE_PENDING")
        place_order_mock.assert_not_awaited()
        publish_mock.assert_awaited_once()
        self.assertGreaterEqual(log_mock.await_count, 2)

    async def test_execute_blocks_duplicate_buy_during_recent_fill_cooldown(self):
        recent_fill_time = now_kst() - timedelta(seconds=60)
        async with TestAsyncSessionLocal() as session:
            async with session.begin():
                session.add(
                    BrokerOrder(
                        cycle_id="cycle-filled",
                        market="KRX",
                        symbol="003670",
                        stock_name="포스코퓨처엠",
                        side="BUY",
                        status="FILLED",
                        strategy_type="STABLE_SHORT",
                        quantity=1,
                        requested_price=227500.0,
                        requested_price_krw=227500.0,
                        filled_quantity=1,
                        filled_price=227500.0,
                        filled_price_krw=227500.0,
                        currency="KRW",
                        exchange_rate_to_krw=1.0,
                        kis_order_id="filled-003670",
                        submitted_at=recent_fill_time,
                        filled_at=recent_fill_time,
                    )
                )

        maker = DecisionMaker()
        with (
            patch("agent.decision_maker.AsyncSessionLocal", TestAsyncSessionLocal),
            patch("agent.decision_maker.activity_logger.log", AsyncMock()) as log_mock,
            patch("agent.decision_maker.event_bus.publish", AsyncMock()) as publish_mock,
            patch("agent.decision_maker.mcp_client.place_order", AsyncMock()) as place_order_mock,
        ):
            result = await maker.execute(self._signal(), cycle_id="cycle-dedupe-filled")

        self.assertFalse(result["success"])
        self.assertEqual(result["data"]["reason_code"], "BUY_ORDER_DEDUPE_COOLDOWN")
        self.assertGreater(result["data"]["cooldown_remaining_sec"], 0)
        place_order_mock.assert_not_awaited()
        publish_mock.assert_awaited_once()
        self.assertGreaterEqual(log_mock.await_count, 2)


class DecisionMakerCoinExitPlanTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        event_detector.clear_all()
        async with test_async_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with TestAsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(delete(ExitPlanHistory))
                await session.execute(delete(ExitPlan))
                await session.execute(delete(CoinTradeResult))

    async def asyncTearDown(self):
        event_detector.clear_all()
        async with TestAsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(delete(ExitPlanHistory))
                await session.execute(delete(ExitPlan))
                await session.execute(delete(CoinTradeResult))

    @staticmethod
    async def _get_coin_trades(symbol: str) -> list[CoinTradeResult]:
        async with TestAsyncSessionLocal() as session:
            result = await session.execute(
                select(CoinTradeResult)
                .where(CoinTradeResult.symbol == symbol)
                .order_by(CoinTradeResult.created_at.asc())
            )
            return list(result.scalars().all())

    async def test_record_trade_result_coin_buy_returns_exit_plan_id_and_persists_exit_context(self):
        maker = DecisionMaker()
        maker._sync_exit_plan = AsyncMock(return_value=MagicMock(id="plan-btc"))

        with (
            patch("agent.decision_maker.AsyncSessionLocal", TestAsyncSessionLocal),
            patch("agent.decision_maker.activity_logger.log", AsyncMock()),
        ):
            result = await maker._record_trade_result(
                symbol="BTC",
                market="BITHUMB",
                side="BUY",
                order_id="coin-buy-1",
                filled_qty=0.125,
                filled_price=150000000.0,
                currency="KRW",
                exchange_rate_to_krw=1.0,
                analysis_context={
                    "stock_name": "비트코인",
                    "strategy_type": "STABLE_SHORT",
                    "ai_recommendation": "BUY",
                    "ai_confidence": 0.91,
                    "ai_target_price": 154000000.0,
                    "ai_stop_loss_price": 145000000.0,
                    "ai_take_profit_price": 151000000.0,
                    "trailing_stop_pct": 3.0,
                    "trade_threshold_payload": {
                        "stop_loss": 145000000.0,
                        "take_profit": 151000000.0,
                        "trailing_stop_pct": 3.0,
                    },
                },
                cycle_id="coin-cycle-buy",
            )

        self.assertEqual(result, {"exit_plan_id": "plan-btc"})
        maker._sync_exit_plan.assert_awaited_once()
        self.assertAlmostEqual(maker._sync_exit_plan.await_args.kwargs["total_quantity"], 0.125, places=8)

        trades = await self._get_coin_trades("BTC")
        self.assertEqual(len(trades), 1)
        notes = json.loads(trades[0].notes)
        self.assertEqual(notes["market"], "BITHUMB")
        self.assertEqual(notes["trade_threshold_payload"]["stop_loss"], 145000000.0)
        self.assertEqual(notes["exit_levels"][0]["price"], 151000000.0)
        self.assertEqual(notes["exit_levels"][0]["pct"], 100)

    async def test_record_trade_result_coin_partial_sell_keeps_open_position(self):
        maker = DecisionMaker()
        entry_at = now_kst() - timedelta(hours=3)

        async with TestAsyncSessionLocal() as session:
            async with session.begin():
                session.add(
                    CoinTradeResult(
                        order_id="coin-open-1",
                        symbol="BTC",
                        coin_name="비트코인",
                        side="BUY",
                        strategy_type="STABLE_SHORT",
                        entry_price=150000000.0,
                        exit_price=0.0,
                        quantity=0.12,
                        pnl=0.0,
                        return_pct=0.0,
                        is_win=False,
                        hold_hours=0,
                        ai_recommendation="BUY",
                        ai_confidence=0.88,
                        ai_target_price=154000000.0,
                        ai_stop_loss_price=145000000.0,
                        market_regime="BULL_RUN",
                        notes=json.dumps({"market": "BITHUMB"}, ensure_ascii=False),
                        entry_at=entry_at,
                    )
                )

        with (
            patch("agent.decision_maker.AsyncSessionLocal", TestAsyncSessionLocal),
            patch("agent.decision_maker.activity_logger.log", AsyncMock()),
            patch(
                "strategy.exit_plan_manager.exit_plan_manager.deactivate_by_symbol",
                AsyncMock(),
            ) as deactivate_mock,
        ):
            result = await maker._record_trade_result(
                symbol="BTC",
                market="BITHUMB",
                side="SELL",
                order_id="coin-sell-1",
                filled_qty=0.03,
                filled_price=151000000.0,
                currency="KRW",
                exchange_rate_to_krw=1.0,
                analysis_context={"stock_name": "비트코인"},
                exit_reason="TAKE_PROFIT",
                cycle_id="coin-cycle-sell",
            )

        self.assertIsNone(result)
        deactivate_mock.assert_not_awaited()

        trades = await self._get_coin_trades("BTC")
        open_trades = [trade for trade in trades if trade.side == "BUY" and trade.exit_at is None]
        closed_trades = [trade for trade in trades if trade.side == "BUY" and trade.exit_at is not None]

        self.assertEqual(len(open_trades), 1)
        self.assertEqual(len(closed_trades), 1)
        self.assertAlmostEqual(open_trades[0].quantity, 0.09, places=8)
        self.assertAlmostEqual(closed_trades[0].quantity, 0.03, places=8)
        self.assertEqual(closed_trades[0].exit_reason, "TAKE_PROFIT")
        self.assertGreater(closed_trades[0].pnl, 0.0)


class DecisionMakerStaleRepairTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        event_detector.clear_all()
        async with test_async_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with TestAsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(delete(BrokerOrder))
                await session.execute(delete(TradeResult))

    async def asyncTearDown(self):
        event_detector.clear_all()
        async with TestAsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(delete(BrokerOrder))
                await session.execute(delete(TradeResult))

    @staticmethod
    async def _get_trade(symbol: str) -> TradeResult | None:
        async with TestAsyncSessionLocal() as session:
            return await session.scalar(
                select(TradeResult)
                .where(TradeResult.stock_symbol == symbol)
                .limit(1)
            )

    async def test_record_trade_result_sell_normalizes_naive_entry_at(self):
        maker = DecisionMaker()
        async with TestAsyncSessionLocal() as session:
            async with session.begin():
                session.add(
                    TradeResult(
                        order_id="buy-042660",
                        stock_symbol="042660",
                        stock_name="한화오션",
                        currency="KRW",
                        exchange_rate_to_krw=1.0,
                        market="KRX",
                        side="BUY",
                        strategy_type="STABLE_SHORT",
                        entry_price=128500.0,
                        entry_price_krw=128500.0,
                        quantity=1,
                        raw_pnl=0.0,
                        pnl=0.0,
                        return_pct=0.0,
                        is_win=False,
                        hold_days=0,
                        entry_at=datetime(2026, 3, 26, 10, 9, 37, 62402),
                    )
                )

        fixed_now = datetime(2026, 3, 26, 11, 20, 59, 757095, tzinfo=KST)
        with (
            patch("agent.decision_maker.AsyncSessionLocal", TestAsyncSessionLocal),
            patch("agent.decision_maker.activity_logger.log", AsyncMock()),
            patch("agent.decision_maker.now_kst", return_value=fixed_now),
        ):
            await maker._record_trade_result(
                symbol="042660",
                market="KRX",
                side="SELL",
                order_id="sell-042660",
                filled_qty=1,
                filled_price=125900.0,
                currency="KRW",
                exchange_rate_to_krw=1.0,
                analysis_context={"stock_name": "한화오션"},
                exit_reason="STOP_LOSS_HIT",
                cycle_id="cycle-sell-1",
            )

        trade = await self._get_trade("042660")
        self.assertIsNotNone(trade)
        assert trade is not None
        self.assertEqual(trade.exit_reason, "STOP_LOSS_HIT")
        self.assertEqual(trade.exit_price, 125900.0)
        self.assertEqual(trade.exit_price_krw, 125900.0)
        self.assertEqual(trade.hold_days, 0)
        self.assertLess(trade.pnl, 0)
        self.assertIsNotNone(trade.exit_at)

    async def test_repair_stale_open_trade_results_closes_missing_holding_from_filled_sell(self):
        maker = DecisionMaker()
        event_detector.set_thresholds("042660", market="KRX", stop_loss=126100.0)

        async with TestAsyncSessionLocal() as session:
            async with session.begin():
                session.add(
                    TradeResult(
                        order_id="buy-042660",
                        stock_symbol="042660",
                        stock_name="한화오션",
                        currency="KRW",
                        exchange_rate_to_krw=1.0,
                        market="KRX",
                        side="BUY",
                        strategy_type="STABLE_SHORT",
                        entry_price=128500.0,
                        entry_price_krw=128500.0,
                        quantity=1,
                        raw_pnl=0.0,
                        pnl=0.0,
                        return_pct=0.0,
                        is_win=False,
                        hold_days=0,
                        notes=json.dumps({"planned_hold_days": 2}, ensure_ascii=False),
                        entry_at=datetime(2026, 3, 26, 10, 9, 37, 62402),
                    )
                )
                session.add(
                    BrokerOrder(
                        cycle_id="cycle-042660",
                        market="KRX",
                        symbol="042660",
                        stock_name="한화오션",
                        side="SELL",
                        status="FILLED",
                        strategy_type="STABLE_SHORT",
                        quantity=1,
                        requested_price=125900.0,
                        requested_price_krw=125900.0,
                        filled_quantity=1,
                        filled_price=125900.0,
                        filled_price_krw=125900.0,
                        currency="KRW",
                        exchange_rate_to_krw=1.0,
                        kis_order_id="0017655400",
                        submitted_at=datetime(2026, 3, 26, 11, 20, 55),
                        filled_at=datetime(2026, 3, 26, 11, 20, 59, 757095),
                    )
                )

        with (
            patch("agent.decision_maker.AsyncSessionLocal", TestAsyncSessionLocal),
            patch("trading.account_manager.account_manager.get_holdings", AsyncMock(return_value=[])),
        ):
            repaired = await maker.repair_stale_open_trade_results(market_scope="KRX")

        self.assertEqual(repaired, 1)
        trade = await self._get_trade("042660")
        self.assertIsNotNone(trade)
        assert trade is not None
        notes = json.loads(trade.notes)
        self.assertEqual(trade.exit_reason, "BROKER_RECONCILE")
        self.assertEqual(trade.exit_price, 125900.0)
        self.assertEqual(notes["reconciled_from_kis_order_id"], "0017655400")
        self.assertEqual(notes["reconcile_source"], "broker_order")
        self.assertIsNotNone(trade.exit_at)
        self.assertNotIn("KRX:042660", event_detector.monitored_symbols)

    async def test_repair_stale_open_trade_results_skips_when_actual_holding_exists(self):
        maker = DecisionMaker()
        event_detector.set_thresholds("042660", market="KRX", stop_loss=126100.0)

        async with TestAsyncSessionLocal() as session:
            async with session.begin():
                session.add(
                    TradeResult(
                        order_id="buy-042660",
                        stock_symbol="042660",
                        stock_name="한화오션",
                        currency="KRW",
                        exchange_rate_to_krw=1.0,
                        market="KRX",
                        side="BUY",
                        strategy_type="STABLE_SHORT",
                        entry_price=128500.0,
                        entry_price_krw=128500.0,
                        quantity=1,
                        raw_pnl=0.0,
                        pnl=0.0,
                        return_pct=0.0,
                        is_win=False,
                        hold_days=0,
                        entry_at=datetime(2026, 3, 26, 10, 9, 37, 62402),
                    )
                )
                session.add(
                    BrokerOrder(
                        cycle_id="cycle-042660",
                        market="KRX",
                        symbol="042660",
                        stock_name="한화오션",
                        side="SELL",
                        status="FILLED",
                        strategy_type="STABLE_SHORT",
                        quantity=1,
                        requested_price=125900.0,
                        requested_price_krw=125900.0,
                        filled_quantity=1,
                        filled_price=125900.0,
                        filled_price_krw=125900.0,
                        currency="KRW",
                        exchange_rate_to_krw=1.0,
                        kis_order_id="0017655400",
                        submitted_at=datetime(2026, 3, 26, 11, 20, 55),
                        filled_at=datetime(2026, 3, 26, 11, 20, 59, 757095),
                    )
                )

        with (
            patch("agent.decision_maker.AsyncSessionLocal", TestAsyncSessionLocal),
            patch(
                "trading.account_manager.account_manager.get_holdings",
                AsyncMock(return_value=[
                    HoldingInfo(
                        symbol="042660",
                        name="한화오션",
                        market="KRX",
                        quantity=1,
                        avg_buy_price=128500.0,
                        current_price=125900.0,
                        pnl=-2600.0,
                        pnl_rate=-2.02,
                    )
                ]),
            ),
        ):
            repaired = await maker.repair_stale_open_trade_results(market_scope="KRX")

        self.assertEqual(repaired, 0)
        trade = await self._get_trade("042660")
        self.assertIsNotNone(trade)
        assert trade is not None
        self.assertIsNone(trade.exit_at)
        self.assertIn("KRX:042660", event_detector.monitored_symbols)


if __name__ == "__main__":
    unittest.main()
