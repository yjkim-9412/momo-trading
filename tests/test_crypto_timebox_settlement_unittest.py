import unittest
from contextlib import ExitStack, nullcontext
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from agent.decision_maker import decision_maker
from agent.trading_agent import trading_agent
from agent.trading_agent._types import MarketState
from core.config import settings
from scheduler.market_calendar import market_calendar
from scheduler.scheduler import TradingScheduler
from services.coin_daily_report_service import coin_daily_report_service
from trading.account_manager import account_manager
from trading.mcp_client import mcp_client
from trading.models import AccountBalance, AccountOverview, HoldingInfo, MCPResponse


class CryptoTimeboxSettlementSweepTest(unittest.IsolatedAsyncioTestCase):
    async def test_settlement_sweep_generates_auto_report_only_after_confirmed_liquidation(self):
        scheduler = TradingScheduler()
        fixed_now = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
        runtime = MarketState(
            scope="CRYPTO",
            market_regime="ALTSEASON",
            market_context="최근 자동 정산 기준 알트 모멘텀 우위",
        )
        due_trade = SimpleNamespace(
            id="trade-1",
            symbol="BTC",
            coin_name="비트코인",
            strategy_type="AGGRESSIVE_SHORT",
            market_regime="ALTSEASON",
            ai_confidence=0.82,
            ai_target_price=102_000_000.0,
            ai_stop_loss_price=97_000_000.0,
            entry_at=fixed_now - timedelta(hours=13),
            entry_price=100_000_000.0,
        )
        holdings = [
            HoldingInfo(
                symbol="BTC",
                name="비트코인",
                market="BITHUMB",
                currency="KRW",
                quantity=0.01,
                avg_buy_price=100_000_000.0,
                current_price=101_000_000.0,
                pnl=10_000.0,
                pnl_rate=1.0,
                exchange_rate_to_krw=1.0,
            )
        ]
        overview = AccountOverview(
            balance=AccountBalance(
                total_asset=1_000_000.0,
                cash=500_000.0,
                stock_value=500_000.0,
                total_pnl=0.0,
                total_pnl_rate=0.0,
                market="BITHUMB",
                currency="KRW",
                exchange_rate_to_krw=1.0,
                raw_cash=500_000.0,
                effective_cash=500_000.0,
                is_valid=True,
            ),
            holdings=[],
            pending_orders=[],
        )
        report = SimpleNamespace(id="report-1")

        with ExitStack() as stack:
            stack.enter_context(patch.object(settings, "CRYPTO_ENABLED", True))
            stack.enter_context(patch.object(settings, "CRYPTO_TRADING_ENABLED", True))
            stack.enter_context(patch.object(settings, "CRYPTO_TIMEBOX_HOURS", 12))
            stack.enter_context(patch.object(trading_agent, "get_runtime", return_value=runtime))
            refresh_rules = stack.enter_context(
                patch.object(
                    trading_agent,
                    "refresh_runtime_trading_rules",
                    AsyncMock(return_value={"rules": []}),
                )
            )
            stack.enter_context(
                patch.object(scheduler, "_load_due_crypto_timebox_trades", AsyncMock(return_value=[due_trade]))
            )
            stack.enter_context(
                patch.object(scheduler, "_is_coin_trade_settled", AsyncMock(return_value=True))
            )
            stack.enter_context(patch.object(market_calendar, "market_date", return_value=date(2026, 3, 15)))
            stack.enter_context(patch("util.time_util.now_kst", return_value=fixed_now))
            stack.enter_context(
                patch("services.activity_logger.activity_logger.context", return_value=nullcontext())
            )
            stack.enter_context(patch("services.activity_logger.activity_logger.log", AsyncMock()))
            stack.enter_context(
                patch("services.activity_logger.activity_logger.start_cycle", return_value="settlement-cycle")
            )
            stack.enter_context(patch.object(account_manager, "get_holdings", AsyncMock(return_value=holdings)))
            stack.enter_context(patch.object(account_manager, "get_pending_orders", AsyncMock(return_value=[])))
            stack.enter_context(
                patch.object(account_manager, "get_account_overview", AsyncMock(return_value=overview))
            )
            stack.enter_context(patch.object(account_manager, "invalidate_cache", MagicMock()))
            place_order = stack.enter_context(
                patch.object(
                    mcp_client,
                    "place_order",
                    AsyncMock(return_value=MCPResponse(success=True, data={"order_id": "order-1"})),
                )
            )
            confirm_and_record = stack.enter_context(
                patch.object(decision_maker, "confirm_and_record", AsyncMock())
            )
            generate_report = stack.enter_context(
                patch.object(
                    coin_daily_report_service,
                    "generate_checkpoint_report",
                    AsyncMock(return_value=report),
                )
            )
            remove_levels = stack.enter_context(
                patch("realtime.event_detector.event_detector.remove_levels", MagicMock())
            )
            stack.enter_context(patch.object(scheduler, "_log_schedule", AsyncMock()))
            await scheduler._crypto_settlement_sweep("BITHUMB")

        place_order.assert_awaited_once_with(
            symbol="BTC",
            side="SELL",
            quantity=0.01,
            price=None,
            market="BITHUMB",
        )
        confirm_and_record.assert_awaited_once()
        generate_report.assert_awaited_once_with(
            market="BITHUMB",
            report_source="AUTO_SETTLEMENT",
            trigger_reason="TIMEBOX_12H",
            applied_cycle_id="settlement-cycle",
            market_regime="ALTSEASON",
            market_context="최근 자동 정산 기준 알트 모멘텀 우위",
            period_anchor_sources={"AUTO_SETTLEMENT"},
            raise_on_error=True,
        )
        refresh_rules.assert_awaited_once_with(
            market="BITHUMB",
            cycle_id="settlement-cycle",
            emit_activity=True,
        )
        remove_levels.assert_called_once_with("BTC", market="BITHUMB")
