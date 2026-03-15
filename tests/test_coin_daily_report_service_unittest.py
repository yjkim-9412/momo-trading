import json
import unittest
from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import delete, select

from models.coin_activity_log import CoinActivityLog
from models.coin_daily_report import CoinDailyReport
from models.coin_trade_result import CoinTradeResult
from models.coin_trading_rule import CoinTradingRule
from services.coin_daily_report_service import CoinDailyReportService, CoinReportGenerationError
from tests.conftest import TestAsyncSessionLocal
from trading.models import MCPResponse


class CoinDailyReportServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async with TestAsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(delete(CoinTradingRule))
                await session.execute(delete(CoinTradeResult))
                await session.execute(delete(CoinActivityLog))
                await session.execute(delete(CoinDailyReport))

    async def test_generate_checkpoint_report_persists_and_deduplicates_cycle_id(self):
        service = CoinDailyReportService()
        fixed_now = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
        llm_payload = {
            "market_regime": "BULL_RUN",
            "market_summary": "거래대금이 BTC와 메이저 알트에 집중됐습니다.",
            "performance_review": "자동 스캔은 제한적으로 동작했고 체결은 없었습니다.",
            "lessons_learned": "추격 진입보다 거래대금 유지 확인이 우선입니다.",
            "next_cycle_plan": "다음 사이클도 BTC 강세 지속 여부를 먼저 본다.",
            "top_picks": ["BTC", "ETH"],
            "trade_evaluation": {
                "total_trades": 0,
                "profitable_trades": 0,
                "loss_trades": 0,
                "best_trade": "없음",
                "worst_trade": "없음",
                "missed_opportunities": "없음",
            },
            "success_patterns": ["거래대금 유지 종목 우선"],
            "failure_patterns": ["근거 약한 추격 매수"],
            "feedback_for_next_cycle": {
                "market_focus": "BTC 주도 여부 확인",
                "timing": "분봉 과열 완화 후 진입",
                "risk_management": "손절 거리 유지",
                "system_improvement": "confidence 하한 유지",
            },
            "risk_alerts": ["MEDIUM: 변동성 확대"],
            "action_items": [
                {
                    "rule_type": "PARAM_OVERRIDE",
                    "apply_scope": "ALL",
                    "param_name": "min_confidence",
                    "param_value": 0.66,
                    "reason": "노이즈 매매 억제",
                    "priority": "HIGH",
                }
            ],
        }

        balance = SimpleNamespace(total_asset=1_500_000.0, cash=650_000.0)
        holdings = [
            SimpleNamespace(
                symbol="BTC",
                quantity=0.01,
                current_price=100_000_000.0,
                pnl=25_000.0,
            )
        ]
        overview = SimpleNamespace(
            success=True,
            data={
                "items": [
                    {"symbol": "BTC", "trade_value": 5_000_000.0, "change_rate": 2.5},
                    {"symbol": "ETH", "trade_value": 3_000_000.0, "change_rate": 1.2},
                ]
            },
        )

        with patch("services.coin_daily_report_service.AsyncSessionLocal", TestAsyncSessionLocal), \
                patch("services.coin_daily_report_service.now_kst", return_value=fixed_now), \
                patch("services.coin_daily_report_service.activity_logger.context", return_value=nullcontext()), \
                patch("services.coin_daily_report_service.activity_logger.log", AsyncMock()), \
                patch(
                    "services.coin_daily_report_service.account_manager.get_account_snapshot",
                    AsyncMock(return_value=(balance, holdings)),
                ), \
                patch(
                    "services.coin_daily_report_service.bithumb_client.get_market_overview",
                    AsyncMock(return_value=overview),
                ), \
                patch(
                    "services.coin_daily_report_service.llm_factory.generate_tier1",
                    AsyncMock(return_value=(json.dumps(llm_payload, ensure_ascii=False), "TEST")),
                ) as generate_tier1, \
                patch(
                    "services.coin_daily_report_service.trading_rule_engine.generate_rules_from_review",
                    AsyncMock(),
                ) as generate_rules:
            first = await service.generate_checkpoint_report(
                market="BITHUMB",
                report_source="AUTO_PRE_CYCLE",
                trigger_reason="adaptive_rescan",
                applied_cycle_id="cycle-1",
            )
            second = await service.generate_checkpoint_report(
                market="BITHUMB",
                report_source="AUTO_PRE_CYCLE",
                trigger_reason="adaptive_rescan",
                applied_cycle_id="cycle-1",
            )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(first.id, second.id)
        self.assertEqual(first.report_source, "AUTO_PRE_CYCLE")
        self.assertEqual(first.applied_cycle_id, "cycle-1")
        self.assertEqual(first.market_regime, "BULL_RUN")
        self.assertEqual(first.period_ended_at.replace(tzinfo=timezone.utc), fixed_now)
        self.assertEqual(generate_tier1.await_count, 1)
        self.assertEqual(generate_tier1.await_args.kwargs["phase"], "report")
        generate_rules.assert_awaited_once()

        async with TestAsyncSessionLocal() as session:
            rows = list((await session.execute(select(CoinDailyReport))).scalars().all())

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].report_source, "AUTO_PRE_CYCLE")
        self.assertEqual(rows[0].trigger_reason, "adaptive_rescan")

    async def test_generate_checkpoint_report_raises_and_does_not_persist_when_market_overview_fails(self):
        service = CoinDailyReportService()
        fixed_now = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
        balance = SimpleNamespace(total_asset=1_000_000.0, cash=400_000.0)
        holdings = []

        with patch("services.coin_daily_report_service.AsyncSessionLocal", TestAsyncSessionLocal), \
                patch("services.coin_daily_report_service.now_kst", return_value=fixed_now), \
                patch("services.coin_daily_report_service.activity_logger.context", return_value=nullcontext()), \
                patch("services.coin_daily_report_service.activity_logger.log", AsyncMock()) as log_activity, \
                patch(
                    "services.coin_daily_report_service.account_manager.get_account_snapshot",
                    AsyncMock(return_value=(balance, holdings)),
                ), \
                patch(
                    "services.coin_daily_report_service.bithumb_client.get_market_overview",
                    AsyncMock(return_value=MCPResponse(success=False, error="dns failure")),
                ), \
                patch(
                    "services.coin_daily_report_service.bithumb_client.get_connectivity_status",
                    AsyncMock(
                        return_value={
                            "dns_api_ok": False,
                            "dns_ws_ok": False,
                            "market_catalog_ok": False,
                            "ticker_probe_ok": False,
                            "public_api_ok": False,
                            "last_error_stage": "dns_resolution",
                            "last_error": "nodename nor servname provided, or not known",
                            "checked_at": fixed_now.isoformat(),
                        }
                    ),
                ), \
                patch(
                    "services.coin_daily_report_service.llm_factory.generate_tier1",
                    AsyncMock(),
                ) as generate_tier1:
            with self.assertRaises(CoinReportGenerationError) as ctx:
                await service.generate_checkpoint_report(
                    market="BITHUMB",
                    report_source="MANUAL",
                    trigger_reason="manual_generate",
                    raise_on_error=True,
                )

        self.assertIn("DNS 해석 실패", ctx.exception.user_message)
        self.assertEqual(ctx.exception.detail.get("error_stage"), "dns_resolution")
        generate_tier1.assert_not_awaited()
        self.assertTrue(
            any(
                call.args[:2] == ("REPORT", "ERROR")
                and ("DNS 해석 실패" in call.args[2] or "Public API" in call.args[2])
                for call in log_activity.await_args_list
            )
        )

        async with TestAsyncSessionLocal() as session:
            rows = list((await session.execute(select(CoinDailyReport))).scalars().all())

        self.assertEqual(rows, [])

    async def test_generate_checkpoint_report_raises_with_ticker_batch_stage_when_overview_batch_fails(self):
        service = CoinDailyReportService()
        fixed_now = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
        balance = SimpleNamespace(total_asset=1_000_000.0, cash=400_000.0)
        holdings = []

        with patch("services.coin_daily_report_service.AsyncSessionLocal", TestAsyncSessionLocal), \
                patch("services.coin_daily_report_service.now_kst", return_value=fixed_now), \
                patch("services.coin_daily_report_service.activity_logger.context", return_value=nullcontext()), \
                patch("services.coin_daily_report_service.activity_logger.log", AsyncMock()) as log_activity, \
                patch(
                    "services.coin_daily_report_service.account_manager.get_account_snapshot",
                    AsyncMock(return_value=(balance, holdings)),
                ), \
                patch(
                    "services.coin_daily_report_service.bithumb_client.get_market_overview",
                    AsyncMock(
                        return_value=MCPResponse(
                            success=False,
                            error="HTTP 414 | content-type=- | body=<empty>",
                            data={
                                "error_stage": "ticker_batch",
                                "batch_index": 2,
                                "batch_count": 2,
                                "batch_size": 5,
                            },
                        )
                    ),
                ), \
                patch(
                    "services.coin_daily_report_service.bithumb_client.get_connectivity_status",
                    AsyncMock(
                        return_value={
                            "dns_api_ok": True,
                            "dns_ws_ok": True,
                            "market_catalog_ok": True,
                            "ticker_probe_ok": True,
                            "public_api_ok": True,
                            "last_error_stage": None,
                            "last_error": None,
                            "checked_at": fixed_now.isoformat(),
                        }
                    ),
                ), \
                patch(
                    "services.coin_daily_report_service.llm_factory.generate_tier1",
                    AsyncMock(),
                ) as generate_tier1:
            with self.assertRaises(CoinReportGenerationError) as ctx:
                await service.generate_checkpoint_report(
                    market="BITHUMB",
                    report_source="MANUAL",
                    trigger_reason="manual_generate",
                    raise_on_error=True,
                )

        self.assertIn("시장 개요 조회 실패", ctx.exception.user_message)
        self.assertIn("ticker batch", ctx.exception.user_message)
        self.assertEqual(ctx.exception.detail.get("error_stage"), "ticker_batch")
        self.assertEqual(ctx.exception.detail.get("overview_error", {}).get("batch_index"), 2)
        generate_tier1.assert_not_awaited()
        self.assertTrue(
            any(
                call.args[:2] == ("REPORT", "ERROR")
                and "HTTP 414" in call.args[2]
                for call in log_activity.await_args_list
            )
        )

        async with TestAsyncSessionLocal() as session:
            rows = list((await session.execute(select(CoinDailyReport))).scalars().all())

        self.assertEqual(rows, [])
