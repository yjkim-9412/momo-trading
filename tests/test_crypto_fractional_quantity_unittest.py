import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agent.decision_maker import DecisionMaker
from agent.trading_agent import TradingAgent
from core.config import settings
from strategy.risk_manager import RiskManager
from strategy.signal import TradeSignal
from trading.bithumb_client import BithumbClient
from trading.enums import SignalAction, SignalUrgency
from trading.models import MCPResponse
from trading.quantity_policy import CRYPTO_MIN_ORDER_AMOUNT_KRW, normalize_quantity


class CryptoQuantityPolicyTest(unittest.TestCase):
    def test_normalize_quantity_keeps_fractional_crypto_precision(self):
        self.assertAlmostEqual(normalize_quantity(0.123456789, "BITHUMB"), 0.12345678)
        self.assertEqual(normalize_quantity(3.9, "NASDAQ"), 3.0)

    def test_build_holding_snapshot_preserves_fractional_crypto_quantity(self):
        agent = TradingAgent()
        holdings = [
            SimpleNamespace(
                symbol="BTC",
                name="비트코인",
                market="BITHUMB",
                currency="KRW",
                exchange_rate_to_krw=1.0,
                quantity=0.1,
                current_price=150_000_000.0,
                avg_buy_price=120_000_000.0,
                pnl=3_000_000.0,
                pnl_rate=25.0,
            )
        ]

        holding_symbols, holding_positions = agent._build_holding_snapshot(holdings, total_asset=20_000_000)

        self.assertEqual(holding_symbols, [("BITHUMB", "BTC")])
        self.assertAlmostEqual(holding_positions["BITHUMB:BTC"]["quantity"], 0.1)
        self.assertGreater(holding_positions["BITHUMB:BTC"]["position_pct"], 0.0)

    def test_build_account_context_keeps_fractional_max_quantity_for_crypto(self):
        agent = TradingAgent()

        context = agent._build_account_context(
            market="BITHUMB",
            portfolio_snapshot={
                "cash": 15_000_000.0,
                "total_asset": 20_000_000.0,
                "holding_count": 0,
            },
            current_position=None,
            dynamic_limits=None,
            current_price=150_000_000.0,
            currency="KRW",
            exchange_rate_to_krw=1.0,
        )

        self.assertGreater(context["max_additional_quantity"], 0.0)
        self.assertLess(context["max_additional_quantity"], 1.0)


class RiskManagerCryptoFractionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._original = {
            "CRYPTO_TRADING_ENABLED": settings.CRYPTO_TRADING_ENABLED,
            "CRYPTO_MAX_SINGLE_ORDER_KRW": settings.CRYPTO_MAX_SINGLE_ORDER_KRW,
            "CRYPTO_MIN_BUY_QUANTITY": settings.CRYPTO_MIN_BUY_QUANTITY,
            "CRYPTO_MIN_CASH_RATIO": settings.CRYPTO_MIN_CASH_RATIO,
            "CRYPTO_MAX_POSITION_PCT": settings.CRYPTO_MAX_POSITION_PCT,
        }
        settings.CRYPTO_TRADING_ENABLED = True
        settings.CRYPTO_MAX_SINGLE_ORDER_KRW = 0
        settings.CRYPTO_MIN_BUY_QUANTITY = 0.0
        settings.CRYPTO_MIN_CASH_RATIO = 0.0
        settings.CRYPTO_MAX_POSITION_PCT = 100.0

    def tearDown(self):
        for field_name, value in self._original.items():
            setattr(settings, field_name, value)

    async def test_crypto_risk_cap_returns_amount_and_fractional_adjusted_quantity(self):
        manager = RiskManager()
        signal = TradeSignal(
            symbol="BTC",
            stock_id="",
            action=SignalAction.BUY,
            strength=0.8,
            suggested_price=150_000_000.0,
            suggested_quantity=0.05,
            suggested_amount_krw=7_500_000.0,
            urgency=SignalUrgency.IMMEDIATE,
            strategy_type="AGGRESSIVE_SHORT",
            metadata={
                "market": "BITHUMB",
                "price_krw": 150_000_000.0,
            },
        )

        with patch("strategy.risk_manager.activity_logger.log", AsyncMock()):
            result = await manager.check(
                signal=signal,
                portfolio_cash=10_000_000.0,
                portfolio_budget=30_000_000.0,
                today_trade_count=0,
                current_holding_count=0,
                dynamic_limits={
                    "max_single_order_krw": 4_500_000.0,
                    "min_buy_quantity": 0.001,
                    "min_cash_ratio": 0.0,
                    "max_position_pct": 100.0,
                },
            )

        self.assertTrue(result["approved"])
        self.assertAlmostEqual(result["adjusted_amount_krw"], 4_500_000.0)
        self.assertAlmostEqual(result["adjusted_quantity"], 0.03)

    async def test_crypto_risk_rejects_buy_below_minimum_order_amount(self):
        manager = RiskManager()
        signal = TradeSignal(
            symbol="BTC",
            stock_id="",
            action=SignalAction.BUY,
            strength=0.5,
            suggested_price=150_000_000.0,
            suggested_amount_krw=4_999.0,
            urgency=SignalUrgency.IMMEDIATE,
            metadata={
                "market": "BITHUMB",
                "price_krw": 150_000_000.0,
            },
        )

        with patch("strategy.risk_manager.activity_logger.log", AsyncMock()):
            result = await manager.check(
                signal=signal,
                portfolio_cash=10_000_000.0,
                portfolio_budget=30_000_000.0,
                today_trade_count=0,
                current_holding_count=0,
            )

        self.assertFalse(result["approved"])
        self.assertIn(f"{CRYPTO_MIN_ORDER_AMOUNT_KRW:,.0f}원", result["reason"])


class DecisionMakerCryptoFractionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._original = {
            "CRYPTO_AUTONOMY_MODE": settings.CRYPTO_AUTONOMY_MODE,
        }
        settings.CRYPTO_AUTONOMY_MODE = "AUTONOMOUS"

    def tearDown(self):
        for field_name, value in self._original.items():
            setattr(settings, field_name, value)

    async def test_execute_passes_amount_based_crypto_buy_to_order_call(self):
        signal = TradeSignal(
            symbol="BTC",
            stock_id="coin-btc",
            action=SignalAction.BUY,
            strength=0.7,
            suggested_price=150_000_000.0,
            suggested_quantity=0.001,
            suggested_amount_krw=150_000.0,
            urgency=SignalUrgency.IMMEDIATE,
            metadata={
                "market": "BITHUMB",
                "currency": "KRW",
                "entry_price_krw": 150_000_000.0,
                "live_price": 150_000_000.0,
                "live_price_krw": 150_000_000.0,
            },
        )

        maker = DecisionMaker()
        maker._upsert_broker_order = AsyncMock(return_value=object())
        maker.confirm_and_record = AsyncMock()

        class DummyTask:
            def add_done_callback(self, callback):
                return None

        def fake_create_task(coro):
            coro.close()
            return DummyTask()

        with (
            patch("agent.decision_maker.activity_logger.log", AsyncMock()),
            patch("agent.decision_maker.event_bus.publish", AsyncMock()),
            patch("agent.decision_maker.asyncio.create_task", side_effect=fake_create_task),
            patch(
                "agent.decision_maker.mcp_client.place_order",
                AsyncMock(return_value=MCPResponse(success=True, data={"order_id": "bithumb-1"})),
            ) as place_order_mock,
        ):
            result = await maker.execute(signal, cycle_id="cycle-crypto-fraction")

        self.assertTrue(result["success"])
        self.assertAlmostEqual(place_order_mock.await_args.kwargs["quantity"], 150_000.0)
        self.assertIsNone(place_order_mock.await_args.kwargs["price"])


class BithumbClientFractionalPayloadTest(unittest.IsolatedAsyncioTestCase):
    async def test_limit_order_preserves_fractional_volume_payload(self):
        client = BithumbClient()

        with patch.object(
            client,
            "_private_request",
            AsyncMock(return_value=MCPResponse(success=True, data={"uuid": "order-1"})),
        ) as request_mock:
            await client.place_order(
                symbol="BTC",
                side="BUY",
                quantity=0.001,
                price=150_000_000.0,
                market="BITHUMB",
            )

        body = request_mock.await_args.kwargs["body"]
        self.assertEqual(body["volume"], "0.001")
