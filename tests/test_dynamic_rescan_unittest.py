import unittest
from datetime import datetime
from unittest.mock import AsyncMock, PropertyMock, patch

from agent.trading_agent import TradingAgent
from core.config import settings
from scheduler.market_calendar import market_calendar
from scheduler.scheduler import TradingScheduler
from trading.mcp_client import mcp_client


class DynamicRescanSchedulerSetupTest(unittest.TestCase):
    def setUp(self):
        self._original_markets = settings.ENABLED_MARKETS
        self._original_dynamic = settings.AI_DYNAMIC_RESCAN_ENABLED
        self._original_crypto_enabled = settings.CRYPTO_ENABLED
        self._original_premarket = settings.US_PREMARKET_ENABLED
        self._original_premarket_scalp = settings.US_PREMARKET_SCALP_ENABLED

    def tearDown(self):
        settings.ENABLED_MARKETS = self._original_markets
        settings.AI_DYNAMIC_RESCAN_ENABLED = self._original_dynamic
        settings.CRYPTO_ENABLED = self._original_crypto_enabled
        settings.US_PREMARKET_ENABLED = self._original_premarket
        settings.US_PREMARKET_SCALP_ENABLED = self._original_premarket_scalp

    def test_setup_jobs_omits_fixed_intraday_cron_when_dynamic_mode_enabled(self):
        settings.ENABLED_MARKETS = "KRX"
        settings.AI_DYNAMIC_RESCAN_ENABLED = True
        scheduler = TradingScheduler()

        scheduler._setup_jobs()

        job_ids = {job.id for job in scheduler.scheduler.get_jobs()}
        self.assertNotIn("intraday_rescan_KRX", job_ids)
        self.assertIn("market_open_scan_KRX", job_ids)

    def test_setup_jobs_keeps_fixed_intraday_cron_when_dynamic_mode_disabled(self):
        settings.ENABLED_MARKETS = "KRX"
        settings.AI_DYNAMIC_RESCAN_ENABLED = False
        scheduler = TradingScheduler()

        scheduler._setup_jobs()

        job_ids = {job.id for job in scheduler.scheduler.get_jobs()}
        self.assertIn("intraday_rescan_KRX", job_ids)

    def test_setup_jobs_registers_crypto_settlement_sweep_instead_of_post_market_review(self):
        settings.ENABLED_MARKETS = "BITHUMB"
        settings.CRYPTO_ENABLED = True
        scheduler = TradingScheduler()

        scheduler._setup_jobs()

        job_ids = {job.id for job in scheduler.scheduler.get_jobs()}
        self.assertIn("crypto_settlement_BITHUMB", job_ids)
        self.assertNotIn("post_market_BITHUMB", job_ids)

    def test_setup_jobs_registers_us_premarket_scalp_liquidation_job(self):
        settings.ENABLED_MARKETS = "US"
        settings.US_PREMARKET_ENABLED = True
        settings.US_PREMARKET_SCALP_ENABLED = True
        scheduler = TradingScheduler()

        scheduler._setup_jobs()

        job_ids = {job.id for job in scheduler.scheduler.get_jobs()}
        self.assertIn("premarket_scalp_liquidation_NASDAQ", job_ids)


class DynamicRescanSchedulerRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._original_dynamic = settings.AI_DYNAMIC_RESCAN_ENABLED
        self._original_day_trading = settings.DAY_TRADING_ONLY
        self._original_max_cycles = settings.AI_DYNAMIC_RESCAN_MAX_CYCLES_PER_SESSION
        self._original_premarket = settings.US_PREMARKET_ENABLED
        self._original_premarket_scalp = settings.US_PREMARKET_SCALP_ENABLED
        settings.AI_DYNAMIC_RESCAN_ENABLED = True
        settings.DAY_TRADING_ONLY = False
        settings.AI_DYNAMIC_RESCAN_MAX_CYCLES_PER_SESSION = 3
        self.scheduler = TradingScheduler()

    async def asyncTearDown(self):
        settings.AI_DYNAMIC_RESCAN_ENABLED = self._original_dynamic
        settings.DAY_TRADING_ONLY = self._original_day_trading
        settings.AI_DYNAMIC_RESCAN_MAX_CYCLES_PER_SESSION = self._original_max_cycles
        settings.US_PREMARKET_ENABLED = self._original_premarket
        settings.US_PREMARKET_SCALP_ENABLED = self._original_premarket_scalp

    async def test_schedule_next_adaptive_rescan_registers_one_shot_job(self):
        with patch.object(self.scheduler, "_log_schedule", AsyncMock()):
            await self.scheduler._schedule_next_adaptive_rescan(
                "KRX",
                {
                    "schedule_hint": {
                        "action": "SCHEDULE_NEXT",
                        "next_run_in_minutes": 34,
                        "reason": "강한 추세 확인 필요",
                        "confidence": 0.82,
                        "source": "ai",
                    }
                },
            )

        job = self.scheduler.scheduler.get_job("adaptive_rescan_KRX")
        self.assertIsNotNone(job)
        self.assertEqual(job.id, "adaptive_rescan_KRX")
        self.assertEqual(job.args[0], "KRX")

    async def test_budget_exhaustion_prevents_future_adaptive_job(self):
        state = self.scheduler._adaptive_state("KRX")
        state.scheduled_cycle_count_today = settings.AI_DYNAMIC_RESCAN_MAX_CYCLES_PER_SESSION

        with patch.object(self.scheduler, "_log_schedule", AsyncMock()) as log_schedule:
            await self.scheduler._schedule_next_adaptive_rescan(
                "KRX",
                {
                    "schedule_hint": {
                        "action": "SCHEDULE_NEXT",
                        "next_run_in_minutes": 30,
                        "reason": "재확인",
                        "confidence": 0.75,
                        "source": "ai",
                    }
                },
            )

        self.assertIsNone(self.scheduler.scheduler.get_job("adaptive_rescan_KRX"))
        log_schedule.assert_awaited()

    async def test_schedule_next_adaptive_rescan_stops_after_us_premarket_scalp_cutoff(self):
        settings.US_PREMARKET_ENABLED = True
        settings.US_PREMARKET_SCALP_ENABLED = True

        with patch.object(self.scheduler, "_log_schedule", AsyncMock()) as log_schedule, \
                patch.object(
                    self.scheduler,
                    "_market_now",
                    side_effect=lambda _market, dt=None: dt or datetime(2026, 3, 13, 9, 20),
                ):
            await self.scheduler._schedule_next_adaptive_rescan(
                "NASDAQ",
                {
                    "schedule_hint": {
                        "action": "SCHEDULE_NEXT",
                        "next_run_in_minutes": 10,
                        "reason": "프리마켓 후속 확인",
                        "confidence": 0.82,
                        "source": "ai",
                    }
                },
            )

        self.assertIsNone(self.scheduler.scheduler.get_job("adaptive_rescan_NASDAQ"))
        log_schedule.assert_awaited()

    async def test_skipped_adaptive_cycle_does_not_consume_budget(self):
        with patch.object(market_calendar, "is_holiday", return_value=False), patch.object(
            type(mcp_client),
            "is_connected",
            new_callable=PropertyMock,
            return_value=True,
        ), patch(
            "agent.trading_agent.trading_agent.run_cycle",
            AsyncMock(return_value={"skipped": True, "reason": "cycle_already_running"}),
        ), patch.object(
            self.scheduler,
            "_log_schedule",
            AsyncMock(),
        ):
            await self.scheduler._execute_trading_scan(
                "KRX",
                trigger_reason="adaptive_rescan",
                include_gap_check=False,
            )

        state = self.scheduler._adaptive_state("KRX")
        self.assertEqual(state.scheduled_cycle_count_today, 0)

    async def test_execute_trading_scan_records_last_error_on_cycle_failure(self):
        with patch.object(market_calendar, "is_holiday", return_value=False), patch.object(
            type(mcp_client),
            "is_connected",
            new_callable=PropertyMock,
            return_value=True,
        ), patch(
            "agent.trading_agent.trading_agent.run_cycle",
            AsyncMock(side_effect=RuntimeError("cycle exploded")),
        ), patch.object(
            self.scheduler,
            "_log_schedule",
            AsyncMock(),
        ) as log_schedule:
            await self.scheduler._execute_trading_scan(
                "KRX",
                trigger_reason="adaptive_rescan",
                include_gap_check=False,
            )

        state = self.scheduler._adaptive_state("KRX")
        self.assertIsNotNone(state.last_run_at)
        self.assertEqual(state.scheduled_cycle_count_today, 0)
        self.assertIn("cycle exploded", state.last_error)
        self.assertTrue(
            any(
                call.args[1] == "ERROR" and "cycle exploded" in call.args[2]
                for call in log_schedule.await_args_list
            )
        )

    async def test_execute_trading_scan_reconciles_watchlist_after_successful_cycle(self):
        with patch.object(market_calendar, "is_holiday", return_value=False), patch.object(
            type(mcp_client),
            "is_connected",
            new_callable=PropertyMock,
            return_value=True,
        ), patch(
            "agent.trading_agent.trading_agent.run_cycle",
            AsyncMock(return_value={"analyzed": 2, "executed": 1, "selected_symbols": [("NVDA", "NASDAQ")]}),
        ), patch(
            "services.watchlist_sync.reconcile_market_watchlist",
            AsyncMock(return_value=[("NVDA", "NASDAQ"), ("PLTR", "NASDAQ")]),
        ) as reconcile_watchlist, patch.object(
            self.scheduler,
            "_log_schedule",
            AsyncMock(),
        ) as log_schedule:
            await self.scheduler._execute_trading_scan(
                "NASDAQ",
                trigger_reason="adaptive_rescan",
                include_gap_check=False,
            )

        reconcile_watchlist.assert_awaited_once_with("NASDAQ")
        self.assertTrue(
            any("실시간 감시 2종목" in call.args[2] for call in log_schedule.await_args_list)
        )

    async def test_execute_trading_scan_allows_crypto_without_mcp(self):
        with patch.object(market_calendar, "is_holiday", return_value=False), patch.object(
            type(mcp_client),
            "is_connected",
            new_callable=PropertyMock,
            return_value=False,
        ), patch(
            "agent.trading_agent.trading_agent.run_cycle",
            AsyncMock(return_value={"analyzed": 1, "executed": 0, "selected_symbols": [("BTC", "BITHUMB")]}),
        ) as run_cycle, patch(
            "services.watchlist_sync.reconcile_market_watchlist",
            AsyncMock(return_value=[("BTC", "BITHUMB")]),
        ), patch.object(
            self.scheduler,
            "_log_schedule",
            AsyncMock(),
        ) as log_schedule:
            await self.scheduler._execute_trading_scan(
                "BITHUMB",
                trigger_reason="adaptive_rescan",
                include_gap_check=False,
            )

        run_cycle.assert_awaited_once()
        self.assertEqual(run_cycle.await_args.kwargs["market"], "BITHUMB")
        self.assertFalse(
            any(
                call.args[1] == "ERROR" and "MCP 미연결" in call.args[2]
                for call in log_schedule.await_args_list
            )
        )

    async def test_adaptive_rescan_clears_stale_next_run_at_before_execution(self):
        state = self.scheduler._adaptive_state("KRX")
        state.next_adaptive_run_at = datetime(2026, 3, 14, 9, 35)

        with patch.object(market_calendar, "is_holiday", return_value=False), patch.object(
            self.scheduler,
            "_execute_trading_scan",
            AsyncMock(),
        ) as execute_scan:
            await self.scheduler._adaptive_rescan("KRX")

        self.assertIsNone(state.next_adaptive_run_at)
        execute_scan.assert_awaited_once_with(
            "KRX",
            trigger_reason="adaptive_rescan",
            include_gap_check=False,
        )


class TradingAgentScheduleHintTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._original_dynamic = settings.AI_DYNAMIC_RESCAN_ENABLED
        settings.AI_DYNAMIC_RESCAN_ENABLED = True
        self.agent = TradingAgent()
        runtime = self.agent.get_runtime("KRX")
        runtime.market_regime = "BULL"
        runtime.market_context = "시장 국면: BULL"
        runtime.trading_context = "현재 세션: KRX_OPEN"
        runtime.daily_start_balance = 1_000_000

    async def asyncTearDown(self):
        settings.AI_DYNAMIC_RESCAN_ENABLED = self._original_dynamic

    async def test_generate_schedule_hint_clamps_ai_interval_to_allowed_bucket(self):
        with patch.object(
            TradingAgent,
            "_minutes_until_market_buy_cutoff",
            return_value=120,
        ), patch(
            "agent.trading_agent.market_calendar.is_trading_hours",
            return_value=True,
        ), patch(
            "agent.trading_agent.market_calendar.get_market_session",
            return_value="KRX_OPEN",
        ), patch(
            "agent.trading_agent.llm_factory.generate_tier1",
            AsyncMock(return_value=(
                '{"action":"SCHEDULE_NEXT","next_run_in_minutes":33,"reason":"추세 확인","confidence":0.81}',
                "CODEX_CLI",
            )),
        ):
            hint = await self.agent._generate_schedule_hint(
                "KRX",
                results={"scanned": 5, "analyzed": 3, "signals": 1, "executed": 0},
                snapshot={"cash": 500_000, "total_asset": 1_100_000, "today_trade_count": 1},
                scheduled_budget_remaining=2,
                cycle_id="cycle-1",
            )

        self.assertEqual(hint["action"], "SCHEDULE_NEXT")
        self.assertEqual(hint["next_run_in_minutes"], 30)
        self.assertEqual(hint["source"], "ai")

    async def test_generate_schedule_hint_falls_back_on_invalid_ai_action(self):
        with patch.object(
            TradingAgent,
            "_minutes_until_market_buy_cutoff",
            return_value=120,
        ), patch(
            "agent.trading_agent.market_calendar.is_trading_hours",
            return_value=True,
        ), patch(
            "agent.trading_agent.market_calendar.get_market_session",
            return_value="KRX_OPEN",
        ), patch(
            "agent.trading_agent.llm_factory.generate_tier1",
            AsyncMock(return_value=(
                '{"action":"WAIT","next_run_in_minutes":20,"reason":"invalid","confidence":0.20}',
                "CODEX_CLI",
            )),
        ):
            hint = await self.agent._generate_schedule_hint(
                "KRX",
                results={"scanned": 4, "analyzed": 2, "signals": 1, "executed": 0},
                snapshot={"cash": 500_000, "total_asset": 1_050_000, "today_trade_count": 1},
                scheduled_budget_remaining=2,
                cycle_id="cycle-2",
            )

        self.assertEqual(hint["action"], "SCHEDULE_NEXT")
        self.assertEqual(hint["next_run_in_minutes"], 45)
        self.assertEqual(hint["source"], "fallback")
        self.assertIn("AI action 무효", hint["reason"])

    async def test_fallback_schedule_hint_shortens_interval_for_crypto_momentum_regime(self):
        runtime = self.agent.get_runtime("BITHUMB")
        runtime.market_regime = "ALTSEASON"

        with patch.object(
            TradingAgent,
            "_minutes_until_market_buy_cutoff",
            return_value=None,
        ), patch(
            "agent.trading_agent.market_calendar.is_trading_hours",
            return_value=True,
        ):
            hint = self.agent._fallback_schedule_hint(
                "BITHUMB",
                results={"scanned": 5, "analyzed": 5, "signals": 0, "executed": 0},
                scheduled_budget_remaining=2,
            )

        self.assertEqual(hint["action"], "SCHEDULE_NEXT")
        self.assertEqual(hint["next_run_in_minutes"], 30)
        self.assertIn("ALTSEASON 국면 지속", hint["reason"])

    async def test_fallback_schedule_hint_shortens_interval_when_day_buy_result_is_zero(self):
        runtime = self.agent.get_runtime("KRX")
        runtime.market_regime = "SIDEWAYS"
        runtime.soft_exploration_attempted = False

        with patch.object(
            TradingAgent,
            "_minutes_until_market_buy_cutoff",
            return_value=120,
        ), patch(
            "agent.trading_agent.market_calendar.is_trading_hours",
            return_value=True,
        ):
            hint = self.agent._fallback_schedule_hint(
                "KRX",
                results={
                    "scanned": 5,
                    "analyzed": 5,
                    "signals": 0,
                    "executed": 0,
                    "today_buy_result_count": 0,
                },
                scheduled_budget_remaining=2,
            )

        self.assertEqual(hint["action"], "SCHEDULE_NEXT")
        self.assertEqual(hint["next_run_in_minutes"], 30)
        self.assertIn("당일 BUY 0건", hint["reason"])

    async def test_fallback_schedule_hint_relaxes_to_45_after_soft_exploration_attempt(self):
        runtime = self.agent.get_runtime("KRX")
        runtime.market_regime = "SIDEWAYS"
        runtime.soft_exploration_attempted = True

        with patch.object(
            TradingAgent,
            "_minutes_until_market_buy_cutoff",
            return_value=120,
        ), patch(
            "agent.trading_agent.market_calendar.is_trading_hours",
            return_value=True,
        ):
            hint = self.agent._fallback_schedule_hint(
                "KRX",
                results={
                    "scanned": 5,
                    "analyzed": 5,
                    "signals": 0,
                    "executed": 0,
                    "today_buy_result_count": 0,
                },
                scheduled_budget_remaining=2,
            )

        self.assertEqual(hint["action"], "SCHEDULE_NEXT")
        self.assertEqual(hint["next_run_in_minutes"], 45)
        self.assertIn("당일 BUY 0건 지속", hint["reason"])


if __name__ == "__main__":
    unittest.main()
