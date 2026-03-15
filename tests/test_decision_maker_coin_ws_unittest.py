import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agent.decision_maker import DecisionMaker
from trading.enums import ActivityPhase, ActivityType


class DecisionMakerCoinWebSocketTest(unittest.IsolatedAsyncioTestCase):
    async def test_sync_coin_ws_order_logs_activity_and_returns_account_change(self):
        maker = DecisionMaker()
        existing = SimpleNamespace(
            status="SUBMITTED",
            filled_quantity=0.0,
            cycle_id="cycle-1",
            coin_name="비트코인",
            side="BUY",
            requested_price=149500000.0,
            currency="KRW",
            strategy_type="",
        )
        record = SimpleNamespace(cycle_id="cycle-1")
        payload = {
            "order_id": "order-1",
            "symbol": "BTC",
            "market": "BITHUMB",
            "side": "BUY",
            "status": "PARTIAL",
            "order_qty": 0.05,
            "filled_qty": 0.01,
            "order_price": 149500000.0,
            "currency": "KRW",
        }

        with patch.object(maker, "_load_broker_order", AsyncMock(return_value=existing)), \
                patch.object(maker, "_upsert_broker_order", AsyncMock(return_value=record)), \
                patch("agent.decision_maker.activity_logger.log", AsyncMock()) as log_mock, \
                patch("trading.account_manager.account_manager.invalidate_cache") as invalidate_mock:
            change = await maker.sync_coin_ws_order(payload)

        invalidate_mock.assert_called_once()
        log_mock.assert_awaited_once()
        args = log_mock.await_args.args
        self.assertEqual(args[0], ActivityType.ORDER)
        self.assertEqual(args[1], ActivityPhase.PROGRESS)
        self.assertIn("부분 체결", args[2])
        self.assertEqual(change["reason"], "order_update")
        self.assertEqual(change["symbol"], "BTC")
        self.assertEqual(change["status"], "PARTIAL")
        self.assertEqual(change["remaining_qty"], 0.04)


if __name__ == "__main__":
    unittest.main()
