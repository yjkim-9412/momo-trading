import unittest
from unittest.mock import AsyncMock, patch

from agent.trading_agent import TradingAgent
from agent.trading_agent._types import MarketState


class CryptoCycleFeedbackPreparationTest(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_crypto_cycle_feedback_skips_auto_report_for_scheduled_auto(self):
        agent = TradingAgent()
        state = MarketState(
            scope="CRYPTO",
            market_regime="ALTSEASON",
            market_context="직전 회고 기반 모멘텀 우위",
        )

        with patch(
            "services.coin_daily_report_service.coin_daily_report_service.generate_checkpoint_report",
            AsyncMock(return_value=object()),
        ) as generate_report, patch.object(
            agent,
            "refresh_runtime_trading_rules",
            AsyncMock(return_value={"rules": []}),
        ) as refresh_rules, patch(
            "agent.trading_agent._cycle_mixin.activity_logger.log",
            AsyncMock(),
        ) as log_activity:
            await agent._prepare_crypto_cycle_feedback(
                "BITHUMB",
                state,
                cycle_id="cycle-auto",
                trigger_source="SCHEDULED_AUTO",
                trigger_reason="scheduled_rescan",
            )

        generate_report.assert_not_awaited()
        log_activity.assert_awaited_once()
        self.assertEqual(log_activity.await_args.args[:2], ("REPORT", "SKIP"))
        self.assertIn("최신 정산 리포트 기준 규칙만 재적용", log_activity.await_args.args[2])
        refresh_rules.assert_awaited_once_with(
            market="BITHUMB",
            cycle_id="cycle-auto",
            emit_activity=True,
        )

    async def test_prepare_crypto_cycle_feedback_skips_new_report_for_manual_cycle(self):
        agent = TradingAgent()
        state = MarketState(scope="CRYPTO")

        with patch(
            "services.coin_daily_report_service.coin_daily_report_service.generate_checkpoint_report",
            AsyncMock(),
        ) as generate_report, patch.object(
            agent,
            "refresh_runtime_trading_rules",
            AsyncMock(return_value={"rules": []}),
        ) as refresh_rules, patch(
            "agent.trading_agent._cycle_mixin.activity_logger.log",
            AsyncMock(),
        ) as log_activity:
            await agent._prepare_crypto_cycle_feedback(
                "BITHUMB",
                state,
                cycle_id="cycle-manual",
                trigger_source="MANUAL_API",
                trigger_reason="manual_trigger",
            )

        generate_report.assert_not_awaited()
        refresh_rules.assert_awaited_once_with(
            market="BITHUMB",
            cycle_id="cycle-manual",
            emit_activity=True,
        )
        log_activity.assert_awaited_once()
        self.assertEqual(log_activity.await_args.args[:2], ("REPORT", "SKIP"))
        self.assertIn("최신 정산 리포트 기준 규칙만 재적용", log_activity.await_args.args[2])

    async def test_prepare_crypto_cycle_feedback_always_refreshes_rules_without_report_generation(self):
        agent = TradingAgent()
        state = MarketState(scope="CRYPTO")

        with patch(
            "services.coin_daily_report_service.coin_daily_report_service.generate_checkpoint_report",
            AsyncMock(return_value=None),
        ) as generate_report, patch.object(
            agent,
            "refresh_runtime_trading_rules",
            AsyncMock(return_value={"rules": []}),
        ) as refresh_rules, patch(
            "agent.trading_agent._cycle_mixin.activity_logger.log",
            AsyncMock(),
        ) as log_activity:
            await agent._prepare_crypto_cycle_feedback(
                "BITHUMB",
                state,
                cycle_id="cycle-skip",
                trigger_source="SCHEDULED_AUTO",
                trigger_reason="scheduled_rescan",
            )

        generate_report.assert_not_awaited()
        refresh_rules.assert_awaited_once_with(
            market="BITHUMB",
            cycle_id="cycle-skip",
            emit_activity=True,
        )
        self.assertEqual(log_activity.await_args.args[:2], ("REPORT", "SKIP"))
        self.assertIn("최신 정산 리포트 기준 규칙만 재적용", log_activity.await_args.args[2])
