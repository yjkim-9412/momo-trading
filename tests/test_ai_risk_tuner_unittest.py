import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.config import settings
from strategy.ai_risk_tuner import AIRiskTuner
from trading.enums import ActivityPhase, ActivityType
from trading.models import AccountBalance


class AIRiskTunerTest(unittest.IsolatedAsyncioTestCase):
    async def test_compute_limits_skips_llm_when_balance_is_invalid(self):
        tuner = AIRiskTuner()
        invalid_balance = AccountBalance(
            total_asset=0,
            cash=0,
            raw_cash=0,
            effective_cash=0,
            cash_source="BROKER",
            stock_value=0,
            total_pnl=0,
            total_pnl_rate=0,
            market="NASDAQ",
            currency="KRW",
            exchange_rate_to_krw=1.0,
            status_message="해외 잔고 응답 불완전",
            is_valid=False,
        )

        with (
            patch(
                "strategy.ai_risk_tuner.account_manager.get_balance",
                AsyncMock(return_value=invalid_balance),
            ),
            patch(
                "strategy.ai_risk_tuner.llm_factory.generate_tier1",
                AsyncMock(),
            ) as generate_mock,
            patch(
                "strategy.ai_risk_tuner.activity_logger.log",
                AsyncMock(),
            ) as log_mock,
        ):
            limits = await tuner.compute_limits(
                market="NASDAQ",
                risk_appetite="AGGRESSIVE",
                cycle_id="cycle-1",
            )

        self.assertEqual(limits["max_daily_trades"], 1)
        self.assertEqual(limits["max_single_order_krw"], 1)
        self.assertEqual(limits["display_currency"], "USD")
        self.assertEqual(limits["min_cash_ratio"], 1.0)
        self.assertIn("계좌 잔고 조회 실패", limits["reasoning"])
        generate_mock.assert_not_awaited()
        log_mock.assert_awaited_once()
        self.assertEqual(log_mock.await_args.args[0], ActivityType.RISK_TUNING)
        self.assertEqual(log_mock.await_args.args[1], ActivityPhase.SKIP)

    async def test_compute_limits_uses_provided_balance_without_refetch(self):
        tuner = AIRiskTuner()
        valid_balance = AccountBalance(
            total_asset=1_000_000,
            total_asset_foreign=800,
            cash=400_000,
            cash_foreign=300,
            raw_cash=400_000,
            effective_cash=400_000,
            effective_cash_foreign=300,
            cash_source="BROKER",
            stock_value=600_000,
            stock_value_foreign=500,
            total_pnl=10_000,
            total_pnl_rate=1.0,
            raw_total_pnl=25,
            raw_total_pnl_rate=3.12,
            market="NASDAQ",
            currency="KRW",
            exchange_rate_to_krw=1450.0,
            is_valid=True,
        )

        class _DummySession:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        with (
            patch(
                "strategy.ai_risk_tuner.account_manager.get_balance",
                AsyncMock(),
            ) as get_balance_mock,
            patch(
                "strategy.ai_risk_tuner.AsyncSessionLocal",
                return_value=_DummySession(),
            ),
            patch(
                "strategy.ai_risk_tuner.PerformanceTracker.get_overall_stats",
                AsyncMock(return_value={"overall": SimpleNamespace(
                    total_trades=7,
                    win_rate=0.571,
                    avg_return=1.23,
                    total_pnl=45_000,
                    raw_total_pnl=30.5,
                )}),
            ),
            patch(
                "strategy.ai_risk_tuner.llm_factory.generate_tier1",
                AsyncMock(return_value=(
                    '{"max_daily_trades": 2, "max_single_order_amount": 120, "max_single_order_currency": "USD", "min_buy_quantity": 1, "max_position_pct": 30, "min_cash_ratio": 10, "reasoning": "ok"}',
                    "TEST",
                )),
            ) as generate_mock,
            patch(
                "strategy.ai_risk_tuner.activity_logger.log",
                AsyncMock(),
            ) as log_mock,
        ):
            limits = await tuner.compute_limits(
                market="NASDAQ",
                risk_appetite="AGGRESSIVE",
                cycle_id="cycle-2",
                balance=valid_balance,
            )

        get_balance_mock.assert_not_awaited()
        self.assertEqual(limits["max_daily_trades"], 2)
        self.assertEqual(limits["max_single_order_krw"], 174000)
        self.assertEqual(limits["max_single_order_foreign"], 120.0)
        self.assertEqual(limits["display_currency"], "USD")
        self.assertIn("일일거래 2회", log_mock.await_args.args[2])
        self.assertIn("주문한도 120.00 USD", log_mock.await_args.args[2])
        prompt = generate_mock.await_args.args[0]
        self.assertIn("기준 통화: USD", prompt)
        self.assertIn("총손익 +30.50 USD", prompt)
        self.assertIn('"max_single_order_currency": "USD"', prompt)

    async def test_compute_limits_formats_unlimited_daily_trades_in_summary(self):
        tuner = AIRiskTuner()
        valid_balance = AccountBalance(
            total_asset=372_000_000,
            total_asset_foreign=1000,
            cash=143_000_000,
            cash_foreign=400,
            raw_cash=143_000_000,
            effective_cash=143_000_000,
            effective_cash_foreign=400,
            cash_source="BROKER",
            stock_value=229_000_000,
            stock_value_foreign=600,
            total_pnl=1_000_000,
            total_pnl_rate=0.27,
            raw_total_pnl=50,
            raw_total_pnl_rate=5.0,
            market="NASDAQ",
            currency="KRW",
            exchange_rate_to_krw=1450.0,
            is_valid=True,
        )

        class _DummySession:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        with (
            patch("strategy.ai_risk_tuner.AsyncSessionLocal", return_value=_DummySession()),
            patch("strategy.ai_risk_tuner.PerformanceTracker.get_overall_stats", AsyncMock(return_value={"overall": None})),
            patch(
                "strategy.ai_risk_tuner.llm_factory.generate_tier1",
                AsyncMock(return_value=(
                    '{"max_daily_trades": 0, "max_single_order_amount": 0, "max_single_order_currency": "USD", "min_buy_quantity": 1, "max_position_pct": 28, "min_cash_ratio": 7, "reasoning": "ok"}',
                    "TEST",
                )),
            ),
            patch("strategy.ai_risk_tuner.activity_logger.log", AsyncMock()) as log_mock,
        ):
            limits = await tuner.compute_limits(
                market="NASDAQ",
                risk_appetite="AGGRESSIVE",
                cycle_id="cycle-3",
                balance=valid_balance,
            )

        self.assertEqual(limits["max_daily_trades"], 0)
        self.assertIn("일일거래 무제한", log_mock.await_args.args[2])
        self.assertIn("주문한도 무제한", log_mock.await_args.args[2])

    async def test_compute_limits_adds_us_paper_cash_interpretation_note(self):
        tuner = AIRiskTuner()
        valid_balance = AccountBalance(
            total_asset=372_000_000,
            total_asset_foreign=260,
            cash=0,
            cash_foreign=0,
            raw_cash=0,
            effective_cash=0,
            effective_cash_foreign=0,
            cash_source="BROKER",
            stock_value=381_000_000,
            stock_value_foreign=260,
            total_pnl=1_000_000,
            total_pnl_rate=0.27,
            raw_total_pnl=-15,
            raw_total_pnl_rate=-5.45,
            market="NASDAQ",
            currency="KRW",
            exchange_rate_to_krw=1450.0,
            is_valid=True,
        )

        class _DummySession:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        with (
            patch.object(settings, "KIS_ACCOUNT_TYPE", "VIRTUAL"),
            patch("strategy.ai_risk_tuner.AsyncSessionLocal", return_value=_DummySession()),
            patch("strategy.ai_risk_tuner.PerformanceTracker.get_overall_stats", AsyncMock(return_value={"overall": None})),
            patch(
                "strategy.ai_risk_tuner.llm_factory.generate_tier1",
                AsyncMock(return_value=(
                    '{"max_daily_trades": 0, "max_single_order_amount": 30, "max_single_order_currency": "USD", "min_buy_quantity": 1, "max_position_pct": 28, "min_cash_ratio": 0.05, "reasoning": "ok"}',
                    "TEST",
                )),
            ) as generate_mock,
            patch("strategy.ai_risk_tuner.activity_logger.log", AsyncMock()),
        ):
            await tuner.compute_limits(
                market="NASDAQ",
                risk_appetite="AGGRESSIVE",
                cycle_id="cycle-4",
                balance=valid_balance,
            )

        prompt = generate_mock.await_args.args[0]
        self.assertIn("기준 통화: USD", prompt)
        self.assertIn("inquire-psamount", prompt)
        self.assertIn("broker cash 0만으로 신규 진입을 단정 차단하지 말고", prompt)
        self.assertIn('"max_single_order_currency": "USD"', prompt)

    async def test_compute_limits_keeps_krw_display_for_domestic_market(self):
        tuner = AIRiskTuner()
        valid_balance = AccountBalance(
            total_asset=1_000_000,
            cash=400_000,
            raw_cash=400_000,
            effective_cash=400_000,
            cash_source="BROKER",
            stock_value=600_000,
            total_pnl=10_000,
            total_pnl_rate=1.0,
            purchase_amount=590_000,
            market="KRX",
            currency="KRW",
            exchange_rate_to_krw=1.0,
            is_valid=True,
        )

        class _DummySession:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        with (
            patch("strategy.ai_risk_tuner.AsyncSessionLocal", return_value=_DummySession()),
            patch("strategy.ai_risk_tuner.PerformanceTracker.get_overall_stats", AsyncMock(return_value={"overall": None})),
            patch(
                "strategy.ai_risk_tuner.llm_factory.generate_tier1",
                AsyncMock(return_value=(
                    '{"max_daily_trades": 3, "max_single_order_krw": 500000, "min_buy_quantity": 1, "max_position_pct": 25, "min_cash_ratio": 0.05, "reasoning": "ok"}',
                    "TEST",
                )),
            ) as generate_mock,
            patch("strategy.ai_risk_tuner.activity_logger.log", AsyncMock()) as log_mock,
        ):
            limits = await tuner.compute_limits(
                market="KRX",
                risk_appetite="MODERATE",
                cycle_id="cycle-5",
                balance=valid_balance,
            )

        self.assertEqual(limits["display_currency"], "KRW")
        self.assertEqual(limits["max_single_order_krw"], 500000)
        self.assertIn("주문한도 500,000원", log_mock.await_args.args[2])
        prompt = generate_mock.await_args.args[0]
        self.assertIn("기준 통화: KRW", prompt)


if __name__ == "__main__":
    unittest.main()
