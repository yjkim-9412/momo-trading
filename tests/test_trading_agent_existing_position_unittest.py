import unittest
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from agent.trading_agent import TradingAgent
from analysis.chart_analyzer import ChartAnalysisResult
from core.config import settings
from core.events import Event, EventType
from scheduler.market_calendar import market_calendar
from trading.models import AccountBalance, HoldingInfo, MCPResponse


class TradingAgentExistingPositionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.agent = TradingAgent()

    @staticmethod
    def _balance() -> AccountBalance:
        return AccountBalance(
            total_asset=300_000_000,
            cash=100_000_000,
            stock_value=200_000_000,
            total_pnl=0.0,
            total_pnl_rate=0.0,
            market="NASDAQ",
            currency="USD",
            exchange_rate_to_krw=1479.8,
            raw_cash=100_000_000,
            effective_cash=100_000_000,
            cash_source="BROKER",
            is_valid=True,
        )

    @staticmethod
    def _holding(
        symbol: str = "PLTR",
        quantity: int = 1000,
        *,
        current_price: float = 149.63,
        pnl: float = -4050.0,
        pnl_rate: float = -2.64,
    ) -> HoldingInfo:
        return HoldingInfo(
            symbol=symbol,
            name="Palantir",
            market="NASDAQ",
            currency="USD",
            quantity=quantity,
            avg_buy_price=153.68,
            current_price=current_price,
            pnl=pnl,
            pnl_rate=pnl_rate,
            exchange_rate_to_krw=1479.8,
        )

    async def test_tier1_prompt_includes_current_position_and_account_context(self):
        chart_result = ChartAnalysisResult(
            indicators_text="지표",
            patterns_text="패턴",
            trend_text="추세",
        )
        current_position = {
            "symbol": "PLTR",
            "market": "NASDAQ",
            "currency": "USD",
            "quantity": 1000,
            "avg_buy_price": 153.68,
            "current_price": 149.63,
            "pnl": -4050.0,
            "pnl_rate": -2.64,
            "exchange_rate_to_krw": 1479.8,
            "current_value_krw": 221_421_074.0,
            "position_pct": 73.8,
        }

        with patch(
            "agent.trading_agent.llm_factory.generate_tier1",
            AsyncMock(return_value=('{"recommendation":"HOLD","confidence":0.4}', "CODEX_CLI")),
        ) as tier1_mock:
            await self.agent._tier1_analysis(
                symbol="PLTR",
                name="팔란티어 테크",
                current_price=149.63,
                chart_result=chart_result,
                price_data={
                    "market": "NASDAQ",
                    "currency": "USD",
                    "price": 149.63,
                    "change": 3.87,
                    "change_rate": -2.52,
                    "volume": 12_725_830,
                },
                feedback_context="매매 이력 없음",
                current_position_context=(
                    "- 현재 보유 여부: 보유 중\n"
                    "- 보유 수량: 1000주\n"
                    "- 평균단가: 153.68USD"
                ),
                portfolio_snapshot={
                    "cash": 100_000_000,
                    "total_asset": 300_000_000,
                    "holding_count": 1,
                },
                current_position=current_position,
                dynamic_limits={"max_single_order_krw": 30_000_000, "max_position_pct": 25.0, "min_cash_ratio": 0.05},
                market_context="시장 컨텍스트 없음",
                trading_context="현재 세션: US_REGULAR",
                orderable_amount_context={
                    "orderable_amount_source": "INQUIRE_PSAMOUNT",
                    "orderable_amount_krw": 6_500_000,
                    "orderable_amount_foreign": 4_392.0,
                    "orderable_qty": 43,
                },
                cycle_id="cycle-1",
            )

        prompt = tier1_mock.await_args.args[0]
        self.assertIn("### 현재 종목 포지션", prompt)
        self.assertIn("보유 수량: 1000주", prompt)
        self.assertIn("### 계좌 상태", prompt)
        self.assertIn("브로커 잔고 현금", prompt)
        self.assertIn("이 종목 기준 주문가능금액", prompt)
        self.assertIn("현재 이 종목 비중: 73.8%", prompt)
        self.assertNotIn("### 기존 보유 계획 상태", prompt)

    async def test_crypto_tier1_analysis_falls_back_to_change_price_when_change_is_direction_string(self):
        chart_result = ChartAnalysisResult(
            indicators_text="지표",
            patterns_text="패턴",
            trend_text="추세",
        )

        with patch(
            "agent.trading_agent.llm_factory.generate_tier1",
            AsyncMock(return_value=('{"recommendation":"HOLD","confidence":0.4}', "CODEX_CLI")),
        ) as tier1_mock:
            result = await self.agent._tier1_analysis(
                symbol="BTC",
                name="비트코인",
                current_price=145_000_000,
                chart_result=chart_result,
                price_data={
                    "market": "BITHUMB",
                    "currency": "KRW",
                    "price": 145_000_000,
                    "change": "FALL",
                    "change_price": 1_250_000,
                    "change_rate": -0.85,
                    "volume": 12_345.67,
                },
                feedback_context="매매 이력 없음",
                current_position_context="현재 포지션 없음 (신규 진입 후보)",
                portfolio_snapshot={
                    "cash": 50_000,
                    "total_asset": 50_000,
                    "holding_count": 0,
                },
                current_position=None,
                dynamic_limits={"max_single_order_krw": 30_000, "max_position_pct": 90.0, "min_cash_ratio": 0.05},
                market_context="시장 컨텍스트 없음",
                trading_context="현재 세션: CRYPTO_ACTIVE",
                orderable_amount_context=None,
                cycle_id="cycle-crypto-1",
            )

        self.assertIsNotNone(result)
        prompt = tier1_mock.await_args.args[0]
        self.assertIn("-1,250,000.00원", prompt)

    async def test_on_market_event_analyzes_held_symbol(self):
        self.agent._running = True
        event = Event(
            type=EventType.VOLUME_SPIKE,
            data={
                "symbol": "PLTR",
                "market": "NASDAQ",
                "price": 149.18,
                "change_rate": -2.81,
                "currency": "USD",
            },
            source="test",
        )

        with patch.object(self.agent, "_build_trading_context", AsyncMock(return_value="ctx")), \
                patch.object(self.agent, "_get_today_trade_count", AsyncMock(return_value=0)), \
                patch("agent.trading_agent._event_mixin.settings.AI_RISK_TUNING_ENABLED", False), \
                patch("trading.account_manager.account_manager.get_account_snapshot", AsyncMock(return_value=(self._balance(), [self._holding()]))), \
                patch("agent.trading_agent.activity_logger.log", AsyncMock()) as log_mock, \
                patch.object(self.agent, "_ensure_realtime_subscription", AsyncMock()) as subscribe_mock, \
                patch.object(self.agent, "_analyze_and_trade", AsyncMock(return_value={"executed": False})) as analyze_mock:
            await self.agent._on_market_event(event)

        analyze_mock.assert_awaited_once()
        subscribe_mock.assert_awaited_once_with("PLTR", market="NASDAQ")
        stock_info = analyze_mock.await_args.args[0]
        self.assertEqual(stock_info["analysis_source"], "event")
        self.assertEqual(stock_info["event_type"], "VOLUME_SPIKE")
        self.assertFalse(
            any("기보유 종목 이벤트 감지" in call.args[2] for call in log_mock.await_args_list),
        )

    async def test_analyze_and_trade_runs_cycle_for_held_symbol_when_tier2_approves_add_on(self):
        holding_symbols, holding_positions = self.agent._build_holding_snapshot(
            [self._holding(current_price=159.63, pnl=5950.0, pnl_rate=3.87)],
            total_asset=300_000_000,
        )

        portfolio_snapshot = {
            "cash": 100_000_000,
            "total_asset": 300_000_000,
            "holding_count": 1,
            "holding_symbols": holding_symbols,
            "holding_positions": holding_positions,
            "today_trade_count": 0,
        }
        chart_result = ChartAnalysisResult(
            indicators={
                "rsi_14": 49.0,
                "macd_histogram": 1.2,
                "obv_trend": "rising",
                "sma_5": 149.0,
            },
            signal_summary={"direction": "BULLISH"},
        )
        price_response = MCPResponse(
            success=True,
            data={
                "market": "NASDAQ",
                "currency": "USD",
                "price": 150.5,
                "price_krw": 222_209.9,
                "exchange_rate_to_krw": 1479.8,
                "change": 1.0,
                "change_rate": 0.67,
                "volume": 120_000,
            },
        )
        daily_response = MCPResponse(
            success=True,
            data={
                "prices": [
                    {"date": "20260310", "open": 145.0, "high": 147.0, "low": 144.0, "close": 146.0, "volume": 1000},
                    {"date": "20260311", "open": 146.0, "high": 148.0, "low": 145.0, "close": 147.0, "volume": 1100},
                    {"date": "20260312", "open": 147.0, "high": 149.0, "low": 146.0, "close": 148.0, "volume": 1200},
                    {"date": "20260313", "open": 148.0, "high": 151.0, "low": 147.0, "close": 150.0, "volume": 1300},
                    {"date": "20260314", "open": 149.0, "high": 152.0, "low": 148.0, "close": 150.5, "volume": 1400},
                ]
            },
        )
        minute_response = MCPResponse(
            success=True,
            data={
                "prices": [
                    {"time": "0930", "open": 149.0, "high": 149.5, "low": 148.8, "close": 149.3, "volume": 100},
                    {"time": "0935", "open": 149.3, "high": 150.2, "low": 149.1, "close": 150.1, "volume": 150},
                ]
            },
        )
        orderable_response = MCPResponse(
            success=True,
            data={
                "orderable_amount_source": "INQUIRE_PSAMOUNT",
                "orderable_amount_krw": 6_500_000,
                "orderable_amount_foreign": 4_392.0,
                "orderable_qty": 43,
            },
        )

        class DummySession:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        detector = MagicMock()
        detector.get_thresholds.return_value = type(
            "Thresholds",
            (),
            {
                "stop_loss": 144.0,
                "take_profit": 156.5,
                "trailing_stop_pct": 3.25,
            },
        )()
        repo = MagicMock()
        repo.get_open_buy = AsyncMock(return_value=type(
            "TradeResult",
            (),
            {
                "currency": "USD",
                "entry_at": datetime(2026, 3, 13, 9, 31, 0),
                "created_at": datetime(2026, 3, 13, 9, 31, 0),
                "ai_stop_loss_price": 145.0,
                "ai_take_profit_price": 157.0,
                "ai_target_price": 160.0,
                "notes": (
                    '{"planned_hold_days": 4, "close_review_count": 1, '
                    '"last_close_review_date": "2026-03-13", "trailing_stop_pct": 3.25}'
                ),
            },
        )())
        expected_hold_days = max(0, (market_calendar.market_date(market="NASDAQ") - date(2026, 3, 13)).days)

        with patch("agent.trading_agent.activity_logger.log", AsyncMock()), \
                patch("agent.trading_agent.AsyncSessionLocal", return_value=DummySession()), \
                patch("repositories.trade_result_repository.TradeResultRepository", return_value=repo), \
                patch(
                    "analysis.feedback.performance_tracker.PerformanceTracker.get_consecutive_losses",
                    AsyncMock(return_value=0),
                ), \
                patch("agent.trading_agent.FeedbackContextBuilder.build_full_context", AsyncMock(return_value="매매 이력 없음")), \
                patch("agent.trading_agent.chart_analyzer.analyze", return_value=chart_result), \
                patch("agent.trading_agent.mcp_client.get_current_price", AsyncMock(return_value=price_response)) as price_mock, \
                patch("agent.trading_agent.mcp_client.get_daily_price", AsyncMock(return_value=daily_response)) as daily_mock, \
                patch("agent.trading_agent.mcp_client.get_minute_price", AsyncMock(return_value=minute_response)) as minute_mock, \
                patch("agent.trading_agent.mcp_client.get_orderable_amount", AsyncMock(return_value=orderable_response)) as orderable_mock, \
                patch.object(self.agent, "_tier1_analysis", AsyncMock(return_value={
                    "recommendation": "BUY",
                    "confidence": 0.72,
                    "target_price": 160.0,
                    "stop_loss_price": 145.0,
                    "trailing_stop_pct": 4.0,
                })) as tier1_mock, \
                patch.object(self.agent, "_tier2_review", AsyncMock(return_value={
                    "approved": True,
                    "action": "BUY",
                    "position_intent": "ADD_ON_PYRAMID",
                    "suggested_quantity": 5,
                    "entry_price": 151.0,
                    "target_price": 160.0,
                    "stop_loss_price": 145.0,
                    "take_profit_price": 157.0,
                    "planned_hold_days": 4,
                    "reason": "수익 구간 불타기",
                    "provider": "CODEX_CLI",
                })) as tier2_mock, \
                patch("agent.trading_agent._analysis_mixin.event_detector", detector), \
                patch("agent.trading_agent.risk_manager.check", AsyncMock(return_value={
                    "approved": True,
                    "adjusted_quantity": None,
                    "combined_position_pct": 12.0,
                    "cash_basis_krw": 6_500_000,
                })) as risk_mock, \
                patch("agent.trading_agent.decision_maker.execute", AsyncMock(return_value={"success": False})) as decision_mock:
            result = await self.agent._analyze_and_trade(
                {
                    "symbol": "PLTR",
                    "name": "팔란티어 테크",
                    "market": "NASDAQ",
                    "strategy_type": "STABLE_SHORT",
                    "analysis_source": "event",
                    "event_type": "PRICE_SURGE",
                },
                cycle_id="cycle-2",
                portfolio_snapshot=portfolio_snapshot,
            )

        self.assertTrue(result["signal"])
        self.assertFalse(result["executed"])
        price_mock.assert_awaited_once()
        daily_mock.assert_awaited_once()
        minute_mock.assert_awaited_once()
        orderable_mock.assert_awaited_once_with("PLTR", 150.5, market="NASDAQ")
        tier1_mock.assert_awaited_once()
        tier2_mock.assert_awaited_once()
        self.assertEqual(risk_mock.await_args.kwargs["orderable_cash_krw"], 6_500_000)
        decision_mock.assert_awaited_once()
        hold_plan_context = tier2_mock.await_args.kwargs["hold_plan_context"]
        self.assertIn("- 보유 계획 추적 상태: 활성", hold_plan_context)
        self.assertIn("- planned_hold_days: 4일", hold_plan_context)
        self.assertIn("- close_review_count: 1회", hold_plan_context)
        self.assertIn("- last_close_review_date: 2026-03-13", hold_plan_context)
        self.assertIn(f"- 실제 보유일(달력 기준): {expected_hold_days}일", hold_plan_context)
        self.assertIn("- 현재 stop_loss_price: 145.00USD", hold_plan_context)
        self.assertIn("- 현재 take_profit_price: 157.00USD", hold_plan_context)
        self.assertIn("- 현재 trailing_stop_pct: 3.25%", hold_plan_context)
        self.assertIn("장중 stop_loss / take_profit / trailing stop은 별도로 살아 있으며 우선 실행됩니다.", hold_plan_context)
        signal = decision_mock.await_args.args[0]
        analysis_context = decision_mock.await_args.kwargs["analysis_context"]
        self.assertEqual(signal.metadata["entry_mode"], "ADD_ON_PYRAMID")
        self.assertEqual(signal.metadata["analysis_source"], "event")
        self.assertEqual(signal.metadata["event_type"], "PRICE_SURGE")
        self.assertEqual(signal.metadata["broker_cash_krw"], 100_000_000)
        self.assertEqual(signal.metadata["symbol_orderable_amount_krw"], 6_500_000)
        self.assertEqual(signal.metadata["symbol_orderable_qty"], 43)
        self.assertEqual(signal.metadata["orderable_amount_source"], "INQUIRE_PSAMOUNT")
        self.assertEqual(signal.stop_loss_price, 144.0)
        self.assertEqual(signal.take_profit_price, 156.5)
        self.assertEqual(signal.metadata["trailing_stop_pct"], 3.25)
        self.assertEqual(signal.metadata["planned_hold_days"], 4)
        self.assertEqual(signal.metadata["existing_hold_plan_tracking_status"], "tracked")
        self.assertEqual(signal.metadata["existing_planned_hold_days"], 4)
        self.assertEqual(signal.metadata["existing_close_review_count"], 1)
        self.assertEqual(signal.metadata["existing_last_close_review_date"], "2026-03-13")
        self.assertEqual(signal.metadata["existing_calendar_hold_days"], expected_hold_days)
        self.assertEqual(signal.metadata["existing_hold_plan_stop_loss_price"], 145.0)
        self.assertEqual(signal.metadata["existing_hold_plan_take_profit_price"], 157.0)
        self.assertEqual(signal.metadata["existing_hold_plan_trailing_stop_pct"], 3.25)
        self.assertEqual(analysis_context["entry_mode"], "ADD_ON_PYRAMID")
        self.assertEqual(analysis_context["analysis_source"], "event")
        self.assertEqual(analysis_context["event_type"], "PRICE_SURGE")
        self.assertEqual(analysis_context["broker_cash_krw"], 100_000_000)
        self.assertEqual(analysis_context["symbol_orderable_amount_krw"], 6_500_000)
        self.assertEqual(analysis_context["symbol_orderable_qty"], 43)
        self.assertEqual(analysis_context["ai_target_price"], 160.0)
        self.assertEqual(analysis_context["ai_stop_loss_price"], 144.0)
        self.assertEqual(analysis_context["ai_take_profit_price"], 156.5)
        self.assertEqual(analysis_context["trailing_stop_pct"], 3.25)
        self.assertEqual(analysis_context["planned_hold_days"], 4)
        self.assertEqual(analysis_context["existing_hold_plan_tracking_status"], "tracked")
        self.assertEqual(analysis_context["existing_planned_hold_days"], 4)
        self.assertEqual(analysis_context["existing_close_review_count"], 1)
        self.assertEqual(analysis_context["existing_last_close_review_date"], "2026-03-13")
        self.assertEqual(analysis_context["existing_calendar_hold_days"], expected_hold_days)
        self.assertEqual(analysis_context["existing_hold_plan_stop_loss_price"], 145.0)
        self.assertEqual(analysis_context["existing_hold_plan_take_profit_price"], 157.0)
        self.assertEqual(analysis_context["existing_hold_plan_trailing_stop_pct"], 3.25)

    async def test_evaluate_position_intent_blocks_second_average_down_same_day(self):
        chart_result = ChartAnalysisResult(
            indicators={
                "rsi_14": 41.0,
                "macd_histogram": 0.8,
                "obv_trend": "rising",
                "sma_5": 149.0,
            },
            signal_summary={"direction": "BULLISH"},
        )

        with patch.object(self.agent, "_count_today_trade_result_entry_mode", AsyncMock(return_value=1)):
            result = await self.agent._evaluate_position_intent(
                symbol="PLTR",
                market_code="NASDAQ",
                scope="US",
                trading_date=date(2026, 3, 14),
                current_position={
                    "symbol": "PLTR",
                    "market": "NASDAQ",
                    "quantity": 100,
                    "pnl_rate": -4.2,
                    "current_value_krw": 22_000_000,
                },
                entry_mode="ADD_ON_AVERAGE_DOWN",
                chart_result=chart_result,
                current_price=150.0,
            )

        self.assertFalse(result["approved"])
        self.assertIn("당일 허용 횟수", result["reason"])


if __name__ == "__main__":
    unittest.main()
