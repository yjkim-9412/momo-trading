import unittest
from unittest.mock import AsyncMock, patch

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
            cash=400_000,
            raw_cash=400_000,
            effective_cash=400_000,
            cash_source="BROKER",
            stock_value=600_000,
            total_pnl=10_000,
            total_pnl_rate=1.0,
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
                AsyncMock(return_value={"overall": None}),
            ),
            patch(
                "strategy.ai_risk_tuner.llm_factory.generate_tier1",
                AsyncMock(return_value=(
                    '{"max_daily_trades": 2, "max_single_order_krw": 500000, "min_buy_quantity": 1, "max_position_pct": 30, "min_cash_ratio": 10, "reasoning": "ok"}',
                    "TEST",
                )),
            ),
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
        self.assertEqual(limits["max_single_order_krw"], 500000)
        self.assertIn("일일거래 2회", log_mock.await_args.args[2])

    async def test_compute_limits_formats_unlimited_daily_trades_in_summary(self):
        tuner = AIRiskTuner()
        valid_balance = AccountBalance(
            total_asset=372_000_000,
            cash=143_000_000,
            raw_cash=143_000_000,
            effective_cash=143_000_000,
            cash_source="BROKER",
            stock_value=229_000_000,
            total_pnl=1_000_000,
            total_pnl_rate=0.27,
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
                    '{"max_daily_trades": 0, "max_single_order_krw": 45000000, "min_buy_quantity": 1, "max_position_pct": 28, "min_cash_ratio": 7, "reasoning": "ok"}',
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


if __name__ == "__main__":
    unittest.main()
