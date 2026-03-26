import asyncio
import unittest
import json
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from agent.decision_maker import DecisionMaker
from models.agent_activity import AgentActivityLog
from strategy.signal import TradeSignal
from trading.enums import SignalAction, SignalUrgency
from trading.models import MCPResponse
from util.time_util import now_kst


class DecisionMakerPriceGuardTest(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
