"""AnalysisMixin: 분석 파이프라인, Tier1/2, 컨텍스트 빌더, 임계값 적용"""
import asyncio
import json
from collections.abc import Callable

import pandas as pd
from loguru import logger
from sqlalchemy import func, select

from agent.decision_maker import decision_maker
from analysis.chart_analyzer import ChartAnalysisResult, chart_analyzer
from analysis.feedback.context_builder import FeedbackContextBuilder
from analysis.llm.llm_factory import llm_factory
from analysis.llm.prompts.final_review import (
    FINAL_REVIEW_PROMPT, FINAL_REVIEW_SYSTEM,
    STOCK_CLOSE_REVIEW_PROMPT,
    STOCK_CLOSE_REVIEW_SYSTEM,
    get_final_review_prompt, get_final_review_system,
)
from analysis.llm.prompts.stock_analysis import (
    STOCK_ANALYSIS_PROMPT, STOCK_ANALYSIS_SYSTEM,
    get_stock_analysis_prompt, get_stock_analysis_system,
)
from core.config import settings
from core.database import AsyncSessionLocal
from core.events import Event, EventType, event_bus
from admin.sse_manager import sse_manager
from realtime.event_detector import event_detector
from scheduler.market_calendar import market_calendar
from services.activity_logger import activity_logger
from services.report_display import build_report_balance_metrics, sum_trade_pnl
from strategy.risk_manager import risk_manager
from strategy.signal import TradeSignal
from trading.enums import (
    ActivityPhase,
    ActivityType,
    LLMTier,
    SignalAction,
    SignalUrgency,
    Tier1Profile,
)
from trading.market_profile import (
    is_us_market,
    market_currency,
    market_scope,
    market_timezone,
    normalize_market,
    normalize_market_scope,
)
from trading.mcp_client import mcp_client
from trading.product_policy import (
    build_product_context,
    classification_from_metadata,
    coerce_strategy_for_product,
    is_product_trade_allowed,
)
from trading.quantity_policy import (
    cap_quantity_for_amount,
    format_quantity,
    format_quantity_with_unit,
    has_quantity,
    normalize_quantity,
)
from trading.risk_policy import (
    get_crypto_trading_style_profile,
    resolve_crypto_rr_floor,
    resolve_rr_floor,
)

from agent.trading_agent._types import _ENTRY_MODE_NEW


class AnalysisMixin:
    """핵심 분석 파이프라인 Mixin: _analyze_and_trade, Tier1/2, 컨텍스트 빌더"""

    @staticmethod
    def _try_float(value) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _try_int(value) -> int | None:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    @classmethod
    def _normalize_planned_hold_days(
        cls,
        value,
        *,
        default: int = 1,
        minimum: int = 1,
    ) -> int:
        normalized = cls._try_int(value)
        if normalized is None:
            normalized = default
        return max(minimum, normalized)

    @staticmethod
    def _resolve_rule_min_confidence(
        strategy_type: str,
        param_overrides: dict | None,
    ) -> float | None:
        rule_min_conf = None
        for scope_name in [strategy_type, "ALL"]:
            value = (param_overrides or {}).get(scope_name, {}).get("min_confidence")
            if value is None:
                continue
            numeric = AnalysisMixin._try_float(value)
            if numeric is None:
                continue
            if rule_min_conf is None or numeric > rule_min_conf:
                rule_min_conf = numeric
        return rule_min_conf

    def _resolve_runtime_rr_floor(
        self,
        *,
        market_scope_code: str,
        market_regime: str,
        active_rules: dict | None,
        runtime_rr_overrides: dict[str, float] | None,
ㅈ    ) -> float:
        rr_overrides = (active_rules or {}).get("rr_floor_overrides", {})
        merged_rr_overrides = {
            **(runtime_rr_overrides or {}),
            **rr_overrides,
        }
        if market_scope_code == "CRYPTO":
            return resolve_crypto_rr_floor(
                market_regime,
                settings.crypto_trading_style_mode,
                merged_rr_overrides,
            )
        return risk_manager.resolve_rr_floor(
            market_regime,
            merged_rr_overrides,
        )

    @staticmethod
    def _resolve_strategy_default_min_confidence(strategy) -> float:
        value = AnalysisMixin._try_float(getattr(strategy, "min_confidence", None))
        if value is not None:
            return value
        return 0.5

    def _build_soft_explore_overlay(
        self,
        *,
        market_scope_code: str,
        strategy,
        market_regime: str,
        rule_min_conf: float | None,
        min_rr: float | None,
    ) -> dict[str, float]:
        if market_scope_code == "CRYPTO":
            return {}

        overlay: dict[str, float] = {}
        if rule_min_conf is not None:
            strategy_default = self._resolve_strategy_default_min_confidence(strategy)
            relaxed_min_conf = max(strategy_default, round(float(rule_min_conf) - 0.05, 4))
            if relaxed_min_conf < float(rule_min_conf):
                overlay["min_confidence"] = relaxed_min_conf

        if min_rr is not None:
            default_rr = float(resolve_rr_floor(market_regime, None))
            relaxed_rr = max(default_rr, round(float(min_rr) - 0.10, 4))
            if relaxed_rr < float(min_rr):
                overlay["rr_floor"] = relaxed_rr

        return overlay

    def _build_soft_explore_candidate(
        self,
        *,
        stock_info: dict,
        analysis: dict,
        strategy_type: str,
        market_code: str,
        gate_type: str,
        gate_reason: str,
        overlay: dict[str, float],
        tier1_confidence: float,
        rule_min_conf: float | None = None,
        code_rr: float | None = None,
        min_rr: float | None = None,
    ) -> dict | None:
        if market_scope(market_code) == "CRYPTO" or not overlay:
            return None

        rank_score = 999.0
        if gate_type == "CONFIDENCE":
            relaxed_min_conf = self._try_float(overlay.get("min_confidence"))
            if relaxed_min_conf is None or tier1_confidence < relaxed_min_conf:
                return None
            rank_score = max(0.0, float(rule_min_conf or relaxed_min_conf) - tier1_confidence)
        elif gate_type == "RR":
            relaxed_rr = self._try_float(overlay.get("rr_floor"))
            if relaxed_rr is None or code_rr is None or code_rr < relaxed_rr:
                return None
            rank_score = max(0.0, float(min_rr or relaxed_rr) - float(code_rr))
        else:
            return None

        return {
            "symbol": str(stock_info.get("symbol") or "").upper(),
            "market": market_code,
            "strategy_type": strategy_type,
            "stock_info": dict(stock_info),
            "tier1_analysis": dict(analysis),
            "overlay": dict(overlay),
            "gate_type": gate_type,
            "gate_reason": gate_reason,
            "tier1_confidence": float(tier1_confidence or 0.0),
            "rule_min_confidence": rule_min_conf,
            "code_rr": code_rr,
            "min_rr": min_rr,
            "rank_score": round(rank_score, 4),
        }

    @staticmethod
    def _parse_trade_notes(notes: str | None) -> dict:
        if not notes or not isinstance(notes, str):
            return {}

        try:
            parsed = json.loads(notes)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @classmethod
    def _resolve_trade_hold_plan(cls, trade_result) -> dict[str, int | str | None]:
        notes = cls._parse_trade_notes(getattr(trade_result, "notes", None))
        planned_hold_days = cls._normalize_planned_hold_days(notes.get("planned_hold_days"), default=1)
        close_review_count = cls._try_int(notes.get("close_review_count"))
        if close_review_count is None or close_review_count < 0:
            close_review_count = 0
        last_close_review_date = notes.get("last_close_review_date")
        if last_close_review_date in ("", None):
            last_close_review_date = None
        return {
            "planned_hold_days": planned_hold_days,
            "close_review_count": close_review_count,
            "last_close_review_date": last_close_review_date,
            "notes": notes,
        }

    @staticmethod
    def _empty_existing_hold_plan_metadata() -> dict[str, object | None]:
        return {
            "existing_hold_plan_tracking_status": "none",
            "existing_planned_hold_days": None,
            "existing_close_review_count": None,
            "existing_last_close_review_date": None,
            "existing_calendar_hold_days": None,
            "existing_hold_plan_stop_loss_price": None,
            "existing_hold_plan_take_profit_price": None,
            "existing_hold_plan_trailing_stop_pct": None,
        }

    def _build_existing_hold_plan_context(
        self,
        *,
        symbol: str,
        market_code: str,
        current_position: dict | None,
        trade_result=None,
    ) -> tuple[str, dict[str, object | None]]:
        """Tier2 전용 보유 계획 컨텍스트를 구성한다."""
        has_current_position = bool(
            current_position and has_quantity(current_position.get("quantity") or 0, market_code)
        )
        if not has_current_position:
            return "보유 계획 없음 (신규 진입 후보)", self._empty_existing_hold_plan_metadata()

        currency = str(
            (current_position or {}).get("currency")
            or getattr(trade_result, "currency", None)
            or market_currency(market_code)
        )

        def _format_price_text(value: float | None) -> str:
            if value and value > 0:
                return f"{value:,.2f}{'원' if currency == 'KRW' else currency}"
            return "미기록"

        if not trade_result:
            legacy_metadata = self._empty_existing_hold_plan_metadata()
            legacy_metadata["existing_hold_plan_tracking_status"] = "legacy"
            legacy_lines = [
                "- 보유 계획 추적 상태: 레거시/미기록 포지션",
                "- planned_hold_days: 미기록",
                "- close_review_count: 미기록",
                "- last_close_review_date: 미기록",
                "- 참고: 이 보유 계획 정보는 참고용입니다. 장중 stop_loss / take_profit / trailing stop은 별도로 살아 있으며 우선 실행됩니다.",
            ]
            return "\n".join(legacy_lines), legacy_metadata

        trade_plan = self._resolve_trade_hold_plan(trade_result)
        plan_notes = trade_plan["notes"]
        has_tracked_plan = any(
            key in plan_notes
            for key in ("planned_hold_days", "close_review_count", "last_close_review_date")
        )
        entry_at = getattr(trade_result, "entry_at", None) or getattr(trade_result, "created_at", None)
        calendar_hold_days = None
        if entry_at:
            calendar_hold_days = max(0, (market_calendar.market_date(market=market_code) - entry_at.date()).days)

        stop_loss_price = self._try_float(getattr(trade_result, "ai_stop_loss_price", None))
        take_profit_price = (
            self._try_float(getattr(trade_result, "ai_take_profit_price", None))
            or self._try_float(getattr(trade_result, "ai_target_price", None))
        )
        trailing_stop_pct = self._try_float(plan_notes.get("trailing_stop_pct"))
        tracking_status = "tracked" if has_tracked_plan else "legacy"

        metadata = self._empty_existing_hold_plan_metadata()
        metadata.update({
            "existing_hold_plan_tracking_status": tracking_status,
            "existing_planned_hold_days": (
                trade_plan["planned_hold_days"] if has_tracked_plan else None
            ),
            "existing_close_review_count": (
                trade_plan["close_review_count"] if has_tracked_plan else None
            ),
            "existing_last_close_review_date": (
                trade_plan["last_close_review_date"] if has_tracked_plan else None
            ),
            "existing_calendar_hold_days": calendar_hold_days,
            "existing_hold_plan_stop_loss_price": stop_loss_price,
            "existing_hold_plan_take_profit_price": take_profit_price,
            "existing_hold_plan_trailing_stop_pct": trailing_stop_pct,
        })

        lines = [
            f"- 보유 계획 추적 상태: {'활성' if tracking_status == 'tracked' else '레거시/미기록'}",
            (
                f"- planned_hold_days: {trade_plan['planned_hold_days']}일"
                if has_tracked_plan
                else "- planned_hold_days: 미기록"
            ),
            (
                f"- close_review_count: {trade_plan['close_review_count']}회"
                if has_tracked_plan
                else "- close_review_count: 미기록"
            ),
            (
                f"- last_close_review_date: {trade_plan['last_close_review_date']}"
                if has_tracked_plan and trade_plan["last_close_review_date"]
                else "- last_close_review_date: 미기록"
            ),
            (
                f"- 실제 보유일(달력 기준): {calendar_hold_days}일"
                if calendar_hold_days is not None
                else "- 실제 보유일(달력 기준): 미확인"
            ),
            f"- 현재 stop_loss_price: {_format_price_text(stop_loss_price)}",
            f"- 현재 take_profit_price: {_format_price_text(take_profit_price)}",
            (
                f"- 현재 trailing_stop_pct: {float(trailing_stop_pct):.2f}%"
                if trailing_stop_pct and trailing_stop_pct > 0
                else "- 현재 trailing_stop_pct: 미사용"
            ),
            "- 참고: 이 보유 계획 정보는 참고용입니다. 장중 stop_loss / take_profit / trailing stop은 별도로 살아 있으며 우선 실행됩니다.",
        ]
        return "\n".join(lines), metadata

    async def _load_existing_hold_plan_context(
        self,
        *,
        symbol: str,
        market_code: str,
        current_position: dict | None,
    ) -> tuple[str, dict[str, object | None]]:
        """Tier2 직전에 미청산 포지션의 보유 계획을 조회한다."""
        if market_scope(market_code) == "CRYPTO":
            return "보유 계획 없음 (신규 진입 후보)", self._empty_existing_hold_plan_metadata()

        trade_result = None
        if current_position and has_quantity(current_position.get("quantity") or 0, market_code):
            try:
                from repositories.trade_result_repository import TradeResultRepository

                async with AsyncSessionLocal() as session:
                    trade_result = await TradeResultRepository(session).get_open_buy(
                        symbol,
                        market=market_code,
                    )
            except Exception as e:
                logger.warning("[{}] 기존 보유 계획 조회 실패 {}: {}", market_code, symbol, str(e))

        return self._build_existing_hold_plan_context(
            symbol=symbol,
            market_code=market_code,
            current_position=current_position,
            trade_result=trade_result,
        )

    @classmethod
    def _validate_stock_tier2_buy_prices(
        cls,
        final: dict | None,
        *,
        market_code: str,
    ) -> str | None:
        """주식 BUY는 Tier2가 동적 진입/목표/손절/익절 가격을 모두 확정해야 한다."""
        if not final or market_scope(market_code) == "CRYPTO":
            return None

        action = str(final.get("action") or "").upper()
        if action != "BUY":
            return None

        required_prices: dict[str, float] = {}
        for key in ("entry_price", "target_price", "stop_loss_price", "take_profit_price"):
            numeric = cls._try_float(final.get(key))
            if numeric is None or numeric <= 0:
                return f"Tier2 {key} 누락 또는 비정상 값"
            final[key] = round(numeric, 4)
            required_prices[key] = numeric

        planned_hold_days = cls._try_int(final.get("planned_hold_days"))
        if planned_hold_days is None or planned_hold_days <= 0:
            return "Tier2 planned_hold_days 누락 또는 비정상 값"
        final["planned_hold_days"] = planned_hold_days

        if required_prices["stop_loss_price"] >= required_prices["entry_price"]:
            return "Tier2 손절가가 진입가 이상으로 설정됨"
        if required_prices["take_profit_price"] <= required_prices["entry_price"]:
            return "Tier2 익절가가 진입가 이하로 설정됨"
        if required_prices["take_profit_price"] > required_prices["target_price"]:
            return "Tier2 익절가가 목표가를 초과함"
        return None

    @classmethod
    def _resolve_crypto_buy_plan(
        cls,
        final: dict | None,
        *,
        market_code: str,
        exchange_rate_to_krw: float,
    ) -> dict[str, float | None]:
        payload = final or {}
        entry_price = cls._try_float(payload.get("entry_price"))
        entry_price_krw = cls._try_float(payload.get("entry_price_krw"))
        if entry_price and (entry_price_krw is None or entry_price_krw <= 0):
            entry_price_krw = entry_price * exchange_rate_to_krw

        suggested_amount_krw = cls._try_float(payload.get("suggested_amount_krw"))
        suggested_quantity = normalize_quantity(payload.get("suggested_quantity"), market_code)

        if (suggested_amount_krw is None or suggested_amount_krw <= 0) and entry_price_krw and suggested_quantity > 0:
            suggested_amount_krw = entry_price_krw * suggested_quantity

        if suggested_amount_krw and suggested_amount_krw > 0 and entry_price_krw and entry_price_krw > 0:
            if suggested_quantity <= 0:
                suggested_quantity = cap_quantity_for_amount(
                    suggested_amount_krw,
                    entry_price_krw,
                    market_code,
                )

        return {
            "entry_price": entry_price,
            "entry_price_krw": entry_price_krw,
            "suggested_amount_krw": suggested_amount_krw,
            "suggested_quantity": suggested_quantity or None,
        }

    @staticmethod
    def _resolve_signal_amount_krw(signal: TradeSignal, exchange_rate_to_krw: float) -> float:
        if isinstance(signal.suggested_amount_krw, (int, float)) and float(signal.suggested_amount_krw) > 0:
            return float(signal.suggested_amount_krw)

        entry_price_krw = (
            signal.metadata.get("entry_price_krw")
            or signal.metadata.get("price_krw")
            or (signal.suggested_price or 0) * exchange_rate_to_krw
        )
        quantity = float(signal.suggested_quantity or 0)
        if entry_price_krw and quantity > 0:
            return float(entry_price_krw) * quantity
        return 0.0

    @classmethod
    def _resolve_prompt_change_value(cls, price_data: dict, *, scope: str) -> float:
        numeric = cls._try_float(price_data.get("change"))
        if numeric is not None:
            return numeric

        if scope != "CRYPTO":
            return 0.0

        numeric = cls._try_float(price_data.get("signed_change_price"))
        if numeric is not None:
            return numeric

        numeric = cls._try_float(price_data.get("change_price"))
        if numeric is None:
            logger.warning(
                "코인 변화값 파싱 실패: change={}, change_price={}, direction={}",
                price_data.get("change"),
                price_data.get("change_price"),
                price_data.get("change_direction"),
            )
            return 0.0

        direction = str(price_data.get("change_direction") or price_data.get("change") or "").upper()
        if direction == "FALL" and numeric > 0:
            return -numeric
        return numeric

    async def _analyze_and_trade(
        self, stock_info: dict, cycle_id: str,
        dynamic_limits: dict | None = None,
        portfolio_snapshot: dict | None = None,
        executed_count_ref: Callable | None = None,
        tier1_override: dict | None = None,
        soft_explore_overlay: dict | None = None,
        soft_explore_mode: bool = False,
    ) -> dict:
        """개별 종목 분석 → 전략 평가 → 매매 결정"""
        symbol = stock_info.get("symbol", "")
        name = stock_info.get("name", symbol)
        strategy_type = stock_info.get("strategy_type", "STABLE_SHORT")
        market_code = normalize_market(stock_info.get("market", settings.primary_market_code))
        scope = market_scope(market_code)
        snap = portfolio_snapshot or {}
        cached_product = self._get_product_metadata(symbol, market_code)
        mkt_state = self._get_state(scope)
        trading_date = self._refresh_runtime_date(mkt_state, scope)
        current_position = self._get_existing_position(snap, symbol, market_code)
        current_position_context = self._format_current_position_for_prompt(current_position)
        analysis_source = str(stock_info.get("analysis_source") or "cycle")
        event_type = str(stock_info.get("event_type") or stock_info.get("trigger") or "").upper()
        analysis_trading_context = mkt_state.trading_context
        hold_plan_context = "보유 계획 없음 (신규 진입 후보)"
        existing_hold_plan_metadata = self._empty_existing_hold_plan_metadata()
        if analysis_source == "event":
            event_price = stock_info.get("event_price")
            event_change_rate = stock_info.get("event_change_rate")
            event_parts = [analysis_trading_context or "트레이딩 컨텍스트 없음"]
            if event_type:
                event_parts.append(f"이벤트 트리거: {event_type}")
            if isinstance(event_price, (int, float)) or isinstance(event_change_rate, (int, float)):
                price_text = f"{float(event_price):,.2f}" if isinstance(event_price, (int, float)) else "N/A"
                change_text = (
                    f"{float(event_change_rate):+.2f}%"
                    if isinstance(event_change_rate, (int, float))
                    else "N/A"
                )
                event_parts.append(f"이벤트 감지값: 가격 {price_text}, 변동률 {change_text}")
            analysis_trading_context = "\n".join(event_parts)

        result = {
            "symbol": symbol,
            "signal": False,
            "executed": False,
            "soft_explored": soft_explore_mode,
        }

        # 피드백 하드 룰: 연속 손실 차단 (매수만 차단, 매도는 허용)
        try:
            async with AsyncSessionLocal() as session:
                from analysis.feedback.performance_tracker import PerformanceTracker
                tracker = PerformanceTracker(session)
                consecutive = await tracker.get_consecutive_losses(market_scope=scope)
                if consecutive >= 5:
                    logger.warning("[하드 룰] 연속 {}회 손실 → 전체 매수 일시 중단", consecutive)
                    await activity_logger.log(
                        ActivityType.RISK_GATE, ActivityPhase.SKIP,
                        f"🛑 연속 {consecutive}회 손실 → 매수 차단 (하드 룰)",
                        cycle_id=cycle_id, symbol=symbol,
                    )
                    return result
        except Exception:
            pass

        # MCP로 데이터 병렬 조회 (일봉 60일 + 분봉 5분 + 현재가)
        price_resp, daily_resp, minute_resp = await asyncio.gather(
            mcp_client.get_current_price(symbol, market=market_code),
            mcp_client.get_daily_price(symbol, count=60, market=market_code),
            mcp_client.get_minute_price(symbol, period="5", market=market_code),
        )

        current_price = 0
        currency = market_currency(market_code)
        exchange_rate_to_krw = 1.0
        price_krw = 0.0
        if price_resp.success and price_resp.data:
            current_price = float(price_resp.data.get("price", price_resp.data.get("current_price", 0)))
            name = str(price_resp.data.get("name") or cached_product.get("name") or name)
            currency = price_resp.data.get("currency", currency)
            exchange_rate_to_krw = float(price_resp.data.get("exchange_rate_to_krw", 1.0) or 1.0)
            price_krw = float(price_resp.data.get("price_krw", current_price * exchange_rate_to_krw) or 0.0)
        else:
            logger.warning("[{}] 현재가 조회 실패: {}", symbol, price_resp.error or "응답 없음")

        orderable_amount_context: dict | None = None
        if is_us_market(market_code) and current_price > 0:
            orderable_resp = await mcp_client.get_orderable_amount(
                symbol,
                current_price,
                market=market_code,
            )
            if orderable_resp.success and orderable_resp.data:
                orderable_amount_context = dict(orderable_resp.data)
            else:
                orderable_amount_context = {
                    "error": orderable_resp.error or "종목별 주문가능금액 조회 실패",
                }

        broker_cash_krw = float(snap.get("cash") or 0.0)
        orderable_signal_metadata = {
            "broker_cash_krw": broker_cash_krw,
            "symbol_orderable_amount_krw": (
                orderable_amount_context.get("orderable_amount_krw")
                if orderable_amount_context
                else None
            ),
            "symbol_orderable_amount_foreign": (
                orderable_amount_context.get("orderable_amount_foreign")
                if orderable_amount_context
                else None
            ),
            "symbol_orderable_qty": (
                orderable_amount_context.get("orderable_qty")
                if orderable_amount_context
                else None
            ),
            "orderable_amount_source": (
                orderable_amount_context.get("orderable_amount_source")
                if orderable_amount_context
                else None
            ),
        }
        orderable_detail = {
            key: value
            for key, value in orderable_signal_metadata.items()
            if value not in (None, "", {})
        }

        product_metadata = {
            **cached_product,
            "name": stock_info.get("name") or cached_product.get("name") or name,
            "category": stock_info.get("category") or cached_product.get("category")
            or ((price_resp.data or {}).get("category") if price_resp.success and price_resp.data else ""),
            "product_type": stock_info.get("product_type") or cached_product.get("product_type"),
            "is_leveraged": stock_info.get("is_leveraged", cached_product.get("is_leveraged")),
            "is_inverse": stock_info.get("is_inverse", cached_product.get("is_inverse")),
            "classification_source": stock_info.get("classification_source")
            or cached_product.get("classification_source"),
        }
        classification = classification_from_metadata(symbol, market_code, product_metadata)
        product_metadata = {
            **product_metadata,
            **classification.to_metadata(),
            "name": classification.name or name,
            "category": classification.category,
        }
        self._remember_product_metadata(symbol, market_code, product_metadata)
        stock_info.update(product_metadata)
        name = product_metadata.get("name", name) or name
        product_context = build_product_context(symbol, market_code, product_metadata)

        effective_strategy = coerce_strategy_for_product(strategy_type, classification)
        if not effective_strategy:
            await activity_logger.log(
                ActivityType.RISK_GATE, ActivityPhase.SKIP,
                f"🚫 [{name}] 제한 상품 허용 전략 미설정",
                cycle_id=cycle_id, symbol=symbol,
                detail=self._enrich_activity_detail(
                    {"reason": "제한 상품 허용 전략 미설정"},
                    product_context,
                ),
            )
            return result

        if effective_strategy != strategy_type:
            logger.info(
                "제한 상품 전략 조정: {} {} {} → {}",
                market_code, symbol, strategy_type, effective_strategy,
            )
            strategy_type = effective_strategy
            stock_info["strategy_type"] = effective_strategy

        allowed, policy_reason = is_product_trade_allowed(
            classification,
            strategy_type=strategy_type,
            session=market_calendar.get_market_session(market=market_code),
        )
        if not allowed:
            await activity_logger.log(
                ActivityType.RISK_GATE, ActivityPhase.SKIP,
                f"🚫 [{name}] 제한 상품 정책 차단: {policy_reason}",
                cycle_id=cycle_id, symbol=symbol,
                detail=self._enrich_activity_detail(
                    {"reason": policy_reason},
                    product_context,
                ),
            )
            return result

        # 3b. DataFrame 변환 + 차트 종합 분석
        daily_df = pd.DataFrame()
        minute_df = None
        chart_result = ChartAnalysisResult()

        if daily_resp.success and daily_resp.data:
            daily_items = daily_resp.data.get("prices", daily_resp.data.get("items", []))
            if daily_items:
                daily_df = pd.DataFrame(daily_items)
                for col in ["open", "high", "low", "close"]:
                    if col in daily_df.columns:
                        daily_df[col] = pd.to_numeric(daily_df[col], errors="coerce")
                if "volume" in daily_df.columns:
                    daily_df["volume"] = pd.to_numeric(daily_df["volume"], errors="coerce")
                daily_df = self._sort_market_data_frame(daily_df, "date")
            else:
                logger.warning("[{}] 일봉 응답은 성공이나 prices 비어있음", symbol)
        else:
            logger.warning("[{}] 일봉 조회 실패: {}", symbol, daily_resp.error or "응답 없음")

        if minute_resp.success and minute_resp.data:
            minute_items = minute_resp.data.get("prices", [])
            if minute_items:
                minute_df = pd.DataFrame(minute_items)
                for col in ["open", "high", "low", "close"]:
                    if col in minute_df.columns:
                        minute_df[col] = pd.to_numeric(minute_df[col], errors="coerce")
                if "volume" in minute_df.columns:
                    minute_df["volume"] = pd.to_numeric(minute_df["volume"], errors="coerce")
                minute_df = self._sort_market_data_frame(minute_df, "time")

        # 핵심 데이터 없으면 AI 분석 스킵 (단기 전략에서는 현재가와 일봉이 모두 필수)
        missing_fields = []
        if current_price <= 0:
            missing_fields.append("현재가")
        if daily_df.empty:
            missing_fields.append("일봉")
        if missing_fields:
            logger.warning("[{}] 핵심 데이터 누락({}) → 분석 스킵", symbol, ", ".join(missing_fields))
            await activity_logger.log(
                ActivityType.TIER1_ANALYSIS, ActivityPhase.SKIP,
                f"⚠️ [{name}] 데이터 부족으로 분석 스킵 ({'·'.join(missing_fields)} 조회 실패)",
                cycle_id=cycle_id, symbol=symbol,
                detail=self._enrich_activity_detail(
                    {
                        "missing_fields": missing_fields,
                        "live_quote": round(current_price, 4),
                        "latest_daily_close": float(daily_df["close"].iloc[-1]) if not daily_df.empty and "close" in daily_df.columns else None,
                        "latest_minute_close": float(minute_df["close"].iloc[-1]) if minute_df is not None and not minute_df.empty and "close" in minute_df.columns else None,
                    },
                    product_context,
                ),
            )
            return result

        consistency_issue = self._detect_price_consistency_issue(current_price, daily_df, minute_df)
        if consistency_issue:
            anchor_labels = {
                "latest_daily_close": "최신 일봉 종가",
                "latest_minute_close": "최신 분봉 종가",
            }
            anchor_label = anchor_labels.get(consistency_issue["anchor"], consistency_issue["anchor"])
            logger.warning(
                "[{}] 데이터 정합성 차단: 현재가 {:.2f}, {} {:.2f}, 괴리 {:.1%}",
                symbol,
                consistency_issue["live_quote"],
                anchor_label,
                consistency_issue["anchor_price"],
                consistency_issue["gap_pct"],
            )
            await activity_logger.log(
                ActivityType.TIER1_ANALYSIS, ActivityPhase.SKIP,
                f"⚠️ [{name}] 데이터 정합성 차단 ({anchor_label} 대비 괴리 {consistency_issue['gap_pct']:.1%})",
                cycle_id=cycle_id,
                symbol=symbol,
                detail=self._enrich_activity_detail(consistency_issue, product_context),
            )
            return result
        chart_result = chart_analyzer.analyze(daily_df, minute_df)

        indicators = chart_result.indicators

        # 3c. 피드백 컨텍스트 빌드
        feedback_context = "매매 이력 없음"
        try:
            async with AsyncSessionLocal() as session:
                builder = FeedbackContextBuilder(session, market_scope=scope)
                rsi_val = indicators.get("rsi_14")
                feedback_context = await builder.build_full_context(
                    strategy_type=strategy_type,
                    symbol=symbol,
                    current_rsi=rsi_val,
                    market_scope=scope,
                )
        except Exception as e:
            logger.warning("피드백 컨텍스트 빌드 실패: {}", str(e))

        # 3d. Tier 1 AI 심층 분석
        t1_elapsed = 0
        price_payload = dict(price_resp.data or {})
        if market_code and not price_payload.get("market"):
            price_payload["market"] = market_code

        if tier1_override is not None:
            analysis = dict(tier1_override)
            await activity_logger.log(
                ActivityType.TRADING_RULE,
                ActivityPhase.PROGRESS,
                f"🧪 [{name}] 당일 BUY 0건 soft 탐색 재평가",
                cycle_id=cycle_id,
                symbol=symbol,
                detail=self._enrich_activity_detail(
                    {
                        "soft_explore_mode": True,
                        "gate_overlay": dict(soft_explore_overlay or {}),
                        "reason": "기존 Tier1 결과 재사용",
                    },
                    product_context,
                ),
            )
        else:
            t1_timer = activity_logger.timer()
            await activity_logger.log(
                ActivityType.TIER1_ANALYSIS, ActivityPhase.START,
                f"\U0001f4ca [{name}] Tier1 분석 시작",
                cycle_id=cycle_id, symbol=symbol,
                detail=self._enrich_activity_detail(None, product_context),
            )

            analysis = await self._tier1_analysis(
                symbol, name, current_price, chart_result,
                price_payload, feedback_context,
                market=market_code,
                product_context=product_context,
                current_position_context=current_position_context,
                portfolio_snapshot=snap,
                current_position=current_position,
                dynamic_limits=dynamic_limits,
                market_context=mkt_state.market_context,
                trading_context=analysis_trading_context,
                orderable_amount_context=orderable_amount_context,
                cycle_id=cycle_id,
            )
            t1_elapsed = activity_logger.elapsed_ms(t1_timer)

            if not analysis:
                await activity_logger.log(
                    ActivityType.TIER1_ANALYSIS, ActivityPhase.COMPLETE,
                    f"\U0001f4ca [{name}] Tier1: 분석 실패 (응답 파싱 불가)",
                    cycle_id=cycle_id, symbol=symbol,
                    detail=self._enrich_activity_detail({"reason": "응답 파싱 불가"}, product_context),
                    llm_tier="TIER1",
                    execution_time_ms=t1_elapsed,
                )
                return result

        recommendation = analysis.get("recommendation", "HOLD")

        # 스캔 파이프라인은 매수 기회 탐색 전용 — SELL 추천은 무시
        if recommendation == "SELL":
            reason = analysis.get("reason") or "AI SELL 추천"
            await activity_logger.log(
                ActivityType.TIER1_ANALYSIS, ActivityPhase.COMPLETE,
                f"\U0001f4ca [{name}] Tier1: SELL → 스캔 경로에서 매도 스킵 | {reason[:100]}",
                cycle_id=cycle_id, symbol=symbol,
                detail=self._enrich_activity_detail(
                    {
                        "recommendation": "SELL",
                        "reason": reason,
                        "confidence": analysis.get("confidence") or 0,
                        **orderable_detail,
                    },
                    product_context,
                ),
                llm_provider=analysis.get("provider"),
                llm_tier="TIER1",
                execution_time_ms=t1_elapsed,
                confidence=analysis.get("confidence") or 0,
            )
            return result

        if recommendation == "HOLD":
            reason = analysis.get("reason") or analysis.get("summary", "판단 근거 없음")
            await activity_logger.log(
                ActivityType.TIER1_ANALYSIS, ActivityPhase.COMPLETE,
                f"\U0001f4ca [{name}] Tier1: HOLD → 스킵 | {reason[:100]}",
                cycle_id=cycle_id, symbol=symbol,
                detail=self._enrich_activity_detail(
                    {
                        "recommendation": "HOLD",
                        "reason": reason,
                        "confidence": analysis.get("confidence") or 0,
                        "key_factors": analysis.get("key_factors", []),
                        **orderable_detail,
                    },
                    product_context,
                ),
                llm_provider=analysis.get("provider"),
                llm_tier="TIER1",
                execution_time_ms=t1_elapsed,
                confidence=analysis.get("confidence") or 0,
            )
            return result

        if not soft_explore_mode:
            await activity_logger.log(
                ActivityType.TIER1_ANALYSIS, ActivityPhase.COMPLETE,
                f"\U0001f4ca [{name}] Tier1 완료: {analysis.get('recommendation', '')} "
                f"| 신뢰도 {(analysis.get('confidence') or 0):.0%}",
                cycle_id=cycle_id, symbol=symbol,
                detail=self._enrich_activity_detail(
                    {
                        "recommendation": analysis.get("recommendation"),
                        "reason": analysis.get("reason") or analysis.get("summary", ""),
                        "target_price": analysis.get("target_price"),
                        "stop_loss": analysis.get("stop_loss_price"),
                        **orderable_detail,
                    },
                    product_context,
                ),
                llm_provider=analysis.get("provider"),
                llm_tier="TIER1",
                execution_time_ms=t1_elapsed,
                confidence=analysis.get("confidence"),
            )

        # ── [하드 게이트] 트레이딩 규칙 기반 검증 (Tier2 진행 전) ──
        tier1_confidence = analysis.get("confidence") or 0
        active_rules = mkt_state.active_trading_rules or {}
        _param_overrides = active_rules.get("param_overrides", {})
        _validation_flags = active_rules.get("validation_flags", {})

        # (A) 신뢰도 게이트
        rule_min_conf = self._resolve_rule_min_confidence(strategy_type, _param_overrides)
        effective_min_conf = self._try_float((soft_explore_overlay or {}).get("min_confidence"))
        if effective_min_conf is None:
            effective_min_conf = rule_min_conf

        if effective_min_conf and tier1_confidence < effective_min_conf:
            soft_candidate = None
            if not soft_explore_mode:
                strategy = mkt_state.strategies.get(strategy_type)
                overlay = self._build_soft_explore_overlay(
                    market_scope_code=scope,
                    strategy=strategy,
                    market_regime=mkt_state.market_regime,
                    rule_min_conf=rule_min_conf,
                    min_rr=None,
                )
                soft_candidate = self._build_soft_explore_candidate(
                    stock_info=stock_info,
                    analysis=analysis,
                    strategy_type=strategy_type,
                    market_code=market_code,
                    gate_type="CONFIDENCE",
                    gate_reason="confidence_gate",
                    overlay=overlay,
                    tier1_confidence=float(tier1_confidence),
                    rule_min_conf=rule_min_conf,
                )
                if soft_candidate:
                    result["soft_explore_candidate"] = soft_candidate
            await activity_logger.log(
                ActivityType.TRADING_RULE, ActivityPhase.SKIP,
                (
                    f"🚫 [{name}] soft 탐색 신뢰도 재평가 미달: {tier1_confidence:.0%} < "
                    f"완화 기준 {effective_min_conf:.0%}"
                    if soft_explore_mode
                    else f"🚫 [{name}] 신뢰도 게이트 차단: {tier1_confidence:.0%} < "
                    f"규칙 최소 {rule_min_conf:.0%} (일일 리뷰 피드백)"
                ),
                cycle_id=cycle_id,
                symbol=symbol,
                detail=self._enrich_activity_detail(
                    {
                        "tier1_confidence": tier1_confidence,
                        "rule_min_confidence": rule_min_conf,
                        "effective_min_confidence": effective_min_conf,
                        "soft_explore_mode": soft_explore_mode,
                        "soft_explore_candidate": bool(result.get("soft_explore_candidate")),
                    },
                    product_context,
                ),
            )
            return result

        # (B) RR 비율 코드 레벨 재검증
        if _validation_flags.get("revalidate_rr_ratio"):
            t1_target = analysis.get("target_price") or 0
            t1_stop = analysis.get("stop_loss_price") or 0

            if current_price > 0 and t1_target > 0 and t1_stop > 0:
                code_reward = abs(t1_target - current_price)
                code_risk = abs(current_price - t1_stop)

                if code_risk > 0:
                    code_rr = code_reward / code_risk
                    min_rr = self._resolve_runtime_rr_floor(
                        market_scope_code=scope,
                        market_regime=mkt_state.market_regime,
                        active_rules=active_rules,
                        runtime_rr_overrides=mkt_state.rr_floor_overrides,
                    )
                    effective_min_rr = self._try_float((soft_explore_overlay or {}).get("rr_floor"))
                    if effective_min_rr is None:
                        effective_min_rr = min_rr

                    if code_rr < effective_min_rr:
                        if not soft_explore_mode:
                            strategy = mkt_state.strategies.get(strategy_type)
                            overlay = self._build_soft_explore_overlay(
                                market_scope_code=scope,
                                strategy=strategy,
                                market_regime=mkt_state.market_regime,
                                rule_min_conf=None,
                                min_rr=min_rr,
                            )
                            soft_candidate = self._build_soft_explore_candidate(
                                stock_info=stock_info,
                                analysis=analysis,
                                strategy_type=strategy_type,
                                market_code=market_code,
                                gate_type="RR",
                                gate_reason="rr_floor_gate",
                                overlay=overlay,
                                tier1_confidence=float(tier1_confidence),
                                code_rr=code_rr,
                                min_rr=min_rr,
                            )
                            if soft_candidate:
                                result["soft_explore_candidate"] = soft_candidate
                        await activity_logger.log(
                            ActivityType.TRADING_RULE, ActivityPhase.SKIP,
                            (
                                f"🚫 [{name}] soft 탐색 RR 재평가 미달: "
                                f"{code_rr:.2f}:1 < 완화 기준 {effective_min_rr}:1 "
                                f"(target={t1_target:,.0f}, stop={t1_stop:,.0f}, 현재가={current_price:,.0f})"
                                if soft_explore_mode
                                else f"🚫 [{name}] RR 비율 검증 실패: "
                                f"코드 계산 {code_rr:.2f}:1 < 최소 {min_rr}:1 "
                                f"(target={t1_target:,.0f}, stop={t1_stop:,.0f}, 현재가={current_price:,.0f})"
                            ),
                            cycle_id=cycle_id,
                            symbol=symbol,
                            detail=self._enrich_activity_detail(
                                {
                                    "code_rr": round(code_rr, 4),
                                    "min_rr": min_rr,
                                    "effective_min_rr": effective_min_rr,
                                    "target_price": t1_target,
                                    "stop_loss_price": t1_stop,
                                    "current_price": current_price,
                                    "soft_explore_mode": soft_explore_mode,
                                    "soft_explore_candidate": bool(result.get("soft_explore_candidate")),
                                },
                                product_context,
                            ),
                        )
                        return result
                elif code_risk == 0 and analysis.get("recommendation") == "BUY":
                    await activity_logger.log(
                        ActivityType.TRADING_RULE, ActivityPhase.SKIP,
                        f"🚫 [{name}] 손절가=현재가 → RR 계산 불가, 차단",
                        cycle_id=cycle_id, symbol=symbol,
                    )
                    return result

        # (C) 손절가 필수 검증
        if _validation_flags.get("require_stop_loss_logging"):
            if analysis.get("recommendation") == "BUY":
                t1_stop = analysis.get("stop_loss_price") or 0
                if t1_stop <= 0:
                    await activity_logger.log(
                        ActivityType.TRADING_RULE, ActivityPhase.SKIP,
                        f"🚫 [{name}] 손절가 미설정 차단 (require_stop_loss_logging 규칙)",
                        cycle_id=cycle_id, symbol=symbol,
                    )
                    return result

        # 3d. Tier 2 최종 검토 (또는 fast-path 스킵)
        skip_tier2 = self._should_skip_tier2(
            market_scope=scope,
            is_restricted_product=classification.is_restricted,
            tier1_confidence=tier1_confidence,
            market_regime=mkt_state.market_regime,
            recommendation=str(analysis.get("recommendation") or ""),
            has_current_position=bool(
                current_position and has_quantity(current_position.get("quantity") or 0, market_code)
            ),
        )

        if skip_tier2:
            final = {
                "approved": True,
                "action": "BUY",
                "confidence": tier1_confidence,
                "entry_price": current_price,
                "target_price": analysis.get("target_price"),
                "stop_loss_price": analysis.get("stop_loss_price"),
                "trailing_stop_pct": analysis.get("trailing_stop_pct", 0),
                "planned_hold_days": 1,
                "reason": f"Tier2 fast-path: Tier1 신뢰도 {tier1_confidence:.0%} + {mkt_state.market_regime} 국면",
                "provider": "fast-path",
            }
            await activity_logger.log(
                ActivityType.TIER2_REVIEW, ActivityPhase.COMPLETE,
                f"\u26a1 [{name}] Tier2 스킵: fast-path "
                f"(신뢰도 {tier1_confidence:.0%}, {mkt_state.market_regime} 국면)",
                cycle_id=cycle_id, symbol=symbol,
                detail=self._enrich_activity_detail(
                    {"approved": True, "skip_reason": "fast-path"},
                    product_context,
                ),
                llm_tier="TIER2",
            )
        else:
            hold_plan_context, existing_hold_plan_metadata = await self._load_existing_hold_plan_context(
                symbol=symbol,
                market_code=market_code,
                current_position=current_position,
            )
            t2_timer = activity_logger.timer()
            await activity_logger.log(
                ActivityType.TIER2_REVIEW, ActivityPhase.START,
                f"\U0001f9e0 [{name}] Tier2 최종 검토 시작",
                cycle_id=cycle_id, symbol=symbol,
                detail=self._enrich_activity_detail(None, product_context),
            )

            final = await self._tier2_review(
                symbol, name, current_price, strategy_type, analysis,
                market=market_code,
                feedback_context=feedback_context,
                product_context=product_context,
                current_position_context=current_position_context,
                current_position=current_position,
                chart_result=chart_result,
                dynamic_limits=dynamic_limits,
                market_context=mkt_state.market_context,
                trading_context=analysis_trading_context,
                portfolio_snapshot=snap,
                orderable_amount_context=orderable_amount_context,
                hold_plan_context=hold_plan_context,
                cycle_id=cycle_id,
            )
            t2_elapsed = activity_logger.elapsed_ms(t2_timer)

            if not final or not final.get("approved"):
                reason = final.get("reason", "") if final else "응답 없음"
                await activity_logger.log(
                    ActivityType.TIER2_REVIEW, ActivityPhase.COMPLETE,
                    f"\U0001f9e0 [{name}] Tier2: 미승인 - {reason[:80]}",
                    cycle_id=cycle_id, symbol=symbol,
                    detail=self._enrich_activity_detail(
                        {
                            "approved": False,
                            "reason": reason,
                            **orderable_detail,
                        },
                        product_context,
                    ),
                    llm_provider=final.get("provider") if final else None,
                    llm_tier="TIER2",
                    execution_time_ms=t2_elapsed,
                )
                logger.info("Tier 2 검토 미승인: {} - {}", symbol, reason)
                return result

            stock_tier2_issue = self._validate_stock_tier2_buy_prices(final, market_code=market_code)
            if stock_tier2_issue:
                await activity_logger.log(
                    ActivityType.TIER2_REVIEW,
                    ActivityPhase.COMPLETE,
                    f"🧠 [{name}] Tier2: 동적 가격 검증 실패 - {stock_tier2_issue}",
                    cycle_id=cycle_id,
                    symbol=symbol,
                    detail=self._enrich_activity_detail(
                        {
                            "approved": False,
                            "reason": stock_tier2_issue,
                            "entry_price": final.get("entry_price"),
                            "target_price": final.get("target_price"),
                            "stop_loss_price": final.get("stop_loss_price"),
                            "take_profit_price": final.get("take_profit_price"),
                            **orderable_detail,
                        },
                        product_context,
                    ),
                    llm_provider=final.get("provider"),
                    llm_tier="TIER2",
                    execution_time_ms=t2_elapsed,
                )
                logger.info("Tier 2 동적 가격 검증 실패: {} - {}", symbol, stock_tier2_issue)
                return result

            crypto_buy_plan = (
                self._resolve_crypto_buy_plan(
                    final,
                    market_code=market_code,
                    exchange_rate_to_krw=exchange_rate_to_krw,
                )
                if market_scope(market_code) == "CRYPTO"
                else {}
            )
            tier2_amount_text = ""
            if crypto_buy_plan.get("suggested_amount_krw"):
                tier2_amount_text = f" | 금액 {float(crypto_buy_plan['suggested_amount_krw']):,.0f}원"
            elif final.get("suggested_quantity"):
                tier2_amount_text = (
                    f" | 수량 {format_quantity_with_unit(final.get('suggested_quantity'), market_code)}"
                )
            await activity_logger.log(
                ActivityType.TIER2_REVIEW, ActivityPhase.COMPLETE,
                f"\U0001f9e0 [{name}] Tier2: \u2705 승인{tier2_amount_text}",
                cycle_id=cycle_id, symbol=symbol,
                detail=self._enrich_activity_detail(
                    {
                        "approved": True,
                        "reason": final.get("reason", ""),
                        "suggested_amount_krw": (
                            crypto_buy_plan.get("suggested_amount_krw")
                            or final.get("suggested_amount_krw")
                        ),
                        "suggested_quantity": final.get("suggested_quantity"),
                        "entry_price": final.get("entry_price"),
                        "entry_price_currency": currency,
                        "entry_price_krw": final.get("entry_price_krw"),
                        "target_price": final.get("target_price"),
                        "target_price_currency": currency,
                        "target_price_krw": final.get("target_price_krw"),
                        "stop_loss_price": final.get("stop_loss_price"),
                        "stop_loss_price_krw": final.get("stop_loss_price_krw"),
                        "take_profit_price": final.get("take_profit_price"),
                        "take_profit_price_krw": final.get("take_profit_price_krw"),
                        "normalized_price_fields": final.get("normalized_price_fields"),
                        **orderable_detail,
                    },
                    product_context,
                ),
                llm_provider=final.get("provider"),
                llm_tier="TIER2",
                execution_time_ms=t2_elapsed,
            )

        entry_mode = self._normalize_position_intent(
            (final or {}).get("position_intent") or analysis.get("position_intent"),
            has_current_position=bool(
                current_position and has_quantity(current_position.get("quantity") or 0, market_code)
            ),
        )
        # 4. 전략 적용
        strategy = mkt_state.strategies.get(strategy_type)
        crypto_buy_plan = (
            self._resolve_crypto_buy_plan(
                final,
                market_code=market_code,
                exchange_rate_to_krw=exchange_rate_to_krw,
            )
            if market_scope(market_code) == "CRYPTO"
            else {}
        )
        crypto_buy_has_size = bool(
            crypto_buy_plan.get("entry_price")
            and (
                crypto_buy_plan.get("suggested_amount_krw")
                or crypto_buy_plan.get("suggested_quantity")
            )
        )

        # Tier2가 수량/가격까지 제시한 경우 → AI 결정으로 직접 시그널 생성
        if (
            (market_scope(market_code) == "CRYPTO" and crypto_buy_has_size)
            or (market_scope(market_code) != "CRYPTO" and final.get("suggested_quantity") and final.get("entry_price"))
        ):
            t2_action = str(final.get("action") or analysis.get("recommendation") or "BUY").upper()
            if t2_action != "BUY":
                await activity_logger.log(
                    ActivityType.TIER2_REVIEW, ActivityPhase.SKIP,
                    f"🚫 [{name}] Tier2 action={t2_action} → 매수 시그널 생성 스킵",
                    cycle_id=cycle_id,
                    symbol=symbol,
                    detail=self._enrich_activity_detail(
                        {"action": t2_action, "entry_mode": entry_mode},
                        product_context,
                    ),
                )
                return result
            action = SignalAction.BUY if t2_action == "BUY" else SignalAction.SELL
            final_confidence = float(final.get("confidence") or analysis.get("confidence") or 0.7)
            stop_loss_price = final.get("stop_loss_price")
            target_price = final.get("target_price")
            take_profit_price = final.get("take_profit_price") or target_price

            signal_quantity = (
                crypto_buy_plan.get("suggested_quantity")
                if market_scope(market_code) == "CRYPTO"
                else final.get("suggested_quantity")
            )
            signal_amount_krw = (
                crypto_buy_plan.get("suggested_amount_krw")
                if market_scope(market_code) == "CRYPTO"
                else None
            )

            signal = TradeSignal(
                symbol=symbol,
                stock_id=stock_info.get("stock_id", ""),
                action=action,
                strength=final_confidence,
                suggested_price=final["entry_price"],
                suggested_quantity=signal_quantity,
                suggested_amount_krw=signal_amount_krw,
                target_price=target_price,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                urgency=SignalUrgency.IMMEDIATE,
                strategy_type=strategy_type,
                reason=final.get("reason", "Tier2 승인"),
                confidence=final_confidence,
                metadata={
                    "market": market_code,
                    "currency": currency,
                    "exchange_rate_to_krw": exchange_rate_to_krw,
                    "price_krw": final.get("entry_price_krw") or price_krw or (final["entry_price"] * exchange_rate_to_krw),
                    "live_price": current_price,
                    "live_price_krw": price_krw or (current_price * exchange_rate_to_krw),
                    "entry_price_krw": final.get("entry_price_krw") or (final["entry_price"] * exchange_rate_to_krw),
                    "target_price_krw": final.get("target_price_krw"),
                    "stop_loss_price_krw": final.get("stop_loss_price_krw"),
                    "take_profit_price_krw": final.get("take_profit_price_krw"),
                    "entry_mode": entry_mode,
                    "current_position": dict(current_position or {}),
                    "analysis_source": analysis_source,
                    "event_type": event_type or None,
                    **orderable_signal_metadata,
                    **classification.to_metadata(),
                },
            )

            result["signal"] = True
            signal_summary = (
                f"{float(signal.suggested_amount_krw or 0):,.0f}원"
                if market_scope(market_code) == "CRYPTO" and signal.suggested_amount_krw
                else format_quantity_with_unit(signal.suggested_quantity, market_code)
            )
            await activity_logger.log(
                ActivityType.STRATEGY_EVAL, ActivityPhase.COMPLETE,
                f"\U0001f4c8 [{name}] Tier2 승인 기반 시그널: {action.value} "
                f"{signal_summary} "
                f"@{signal.suggested_price:,.2f}{currency}",
                cycle_id=cycle_id, symbol=symbol,
                detail=self._enrich_activity_detail(
                    {
                        "action": action.value,
                        "suggested_amount_krw": signal.suggested_amount_krw,
                        "suggested_quantity": signal.suggested_quantity,
                        "entry_price": signal.suggested_price,
                        "currency": currency,
                        "entry_price_krw": signal.metadata.get("entry_price_krw"),
                        "entry_mode": entry_mode,
                        **orderable_detail,
                    },
                    product_context,
                ),
            )
        else:
            # Tier2가 구체적 수량/가격을 제시하지 않은 경우 → 전략 평가로 폴백
            analysis_for_strategy = {
                **analysis,
                "indicators": indicators,
                "chart_result": chart_result,
                "symbol": symbol,
                "stock_id": stock_info.get("stock_id", ""),
                "current_price": current_price,
                "market": market_code,
                "currency": currency,
                "exchange_rate_to_krw": exchange_rate_to_krw,
                "price_krw": price_krw,
                **classification.to_metadata(),
            }

            if not strategy:
                return result

            signal = await strategy.evaluate(analysis_for_strategy, market_regime=mkt_state.market_regime)
            if not signal or signal.action == SignalAction.HOLD:
                await activity_logger.log(
                    ActivityType.STRATEGY_EVAL, ActivityPhase.COMPLETE,
                    f"\U0001f4c8 [{name}] 전략 평가: HOLD → 스킵",
                    cycle_id=cycle_id, symbol=symbol,
                    detail=self._enrich_activity_detail({"action": "HOLD"}, product_context),
                )
                return result

            result["signal"] = True
            strategy_signal_summary = (
                f"{float(signal.suggested_amount_krw or 0):,.0f}원"
                if market_scope(market_code) == "CRYPTO" and signal.suggested_amount_krw
                else format_quantity_with_unit(signal.suggested_quantity or 0, market_code)
            )
            await activity_logger.log(
                ActivityType.STRATEGY_EVAL, ActivityPhase.COMPLETE,
                f"\U0001f4c8 [{name}] 전략({strategy_type}): {signal.action.value} "
                f"{strategy_signal_summary} "
                f"@{(signal.suggested_price or 0):,.0f}원",
                cycle_id=cycle_id, symbol=symbol,
                detail=self._enrich_activity_detail(
                    {
                        "action": signal.action.value,
                        "suggested_amount_krw": signal.suggested_amount_krw,
                        "suggested_quantity": signal.suggested_quantity or 0,
                        "entry_price": signal.suggested_price or 0,
                        "currency": currency,
                    },
                    product_context,
                ),
            )

            # Tier 2에서 제안한 값이 있으면 적용
            if market_scope(market_code) == "CRYPTO" and crypto_buy_plan.get("suggested_amount_krw"):
                signal.suggested_amount_krw = crypto_buy_plan["suggested_amount_krw"]
            if market_scope(market_code) == "CRYPTO" and crypto_buy_plan.get("suggested_quantity"):
                signal.suggested_quantity = crypto_buy_plan["suggested_quantity"]
            elif final.get("suggested_quantity"):
                signal.suggested_quantity = final["suggested_quantity"]
            if final.get("entry_price"):
                signal.suggested_price = final["entry_price"]
            if final.get("target_price"):
                signal.target_price = final["target_price"]
            if final.get("stop_loss_price"):
                signal.stop_loss_price = final["stop_loss_price"]
            if final.get("take_profit_price"):
                signal.take_profit_price = final["take_profit_price"]
            elif final.get("target_price"):
                signal.take_profit_price = final["target_price"]

            signal.metadata = {
                **(signal.metadata or {}),
                "market": market_code,
                "currency": currency,
                "exchange_rate_to_krw": exchange_rate_to_krw,
                "live_price": current_price,
                "live_price_krw": price_krw or (current_price * exchange_rate_to_krw),
                "entry_price_krw": final.get("entry_price_krw"),
                "target_price_krw": final.get("target_price_krw"),
                "stop_loss_price_krw": final.get("stop_loss_price_krw"),
                "take_profit_price_krw": final.get("take_profit_price_krw"),
                "entry_mode": entry_mode,
                "current_position": dict(current_position or {}),
                "analysis_source": analysis_source,
                "event_type": event_type or None,
                **orderable_signal_metadata,
                **classification.to_metadata(),
            }

        if signal.action == SignalAction.BUY:
            position_gate = await self._evaluate_position_intent(
                symbol=symbol,
                market_code=market_code,
                scope=scope,
                trading_date=trading_date,
                current_position=current_position,
                entry_mode=entry_mode,
                chart_result=chart_result,
                current_price=current_price,
            )
            if not position_gate.get("approved"):
                await activity_logger.log(
                    ActivityType.RISK_GATE, ActivityPhase.SKIP,
                    f"🚫 [{name}] {position_gate.get('reason')}",
                    cycle_id=cycle_id,
                    symbol=symbol,
                    detail=self._enrich_activity_detail(
                        position_gate.get("detail"),
                        product_context,
                    ),
                )
                return result
            entry_mode = str(position_gate.get("entry_mode") or _ENTRY_MODE_NEW)
            signal.metadata.update({
                "entry_mode": entry_mode,
                "current_position": dict(current_position or {}),
            })

        # AI가 결정한 손절/익절/트레일링 스탑을 event_detector에 설정
        self._apply_trade_thresholds(symbol, analysis, final, market=market_code)
        applied_thresholds = self._resolve_applied_trade_thresholds(
            symbol,
            signal,
            market=market_code,
            fallback_trailing_pct=(
                final.get("trailing_stop_pct") or analysis.get("trailing_stop_pct")
            ),
        )
        signal.stop_loss_price = applied_thresholds["stop_loss_price"]
        signal.take_profit_price = applied_thresholds["take_profit_price"]
        signal.metadata["trailing_stop_pct"] = applied_thresholds["trailing_stop_pct"]
        if signal.action == SignalAction.BUY and market_scope(market_code) != "CRYPTO":
            signal.metadata["planned_hold_days"] = self._normalize_planned_hold_days(
                final.get("planned_hold_days"),
                default=1,
            )
        signal.metadata.update(existing_hold_plan_metadata)

        # 4.5 매도 시 보유 여부 확인
        if signal.action == SignalAction.SELL:
            snap = portfolio_snapshot or {}
            holding_symbols = snap.get("holding_symbols", [])
            if (market_code, symbol) not in holding_symbols:
                logger.info("미보유 종목 매도 스킵: {} (보유: {})", symbol, holding_symbols)
                await activity_logger.log(
                    ActivityType.RISK_CHECK, ActivityPhase.SKIP,
                    f"🚫 [{name}] 미보유 종목 매도 차단",
                    cycle_id=cycle_id, symbol=symbol,
                    detail=self._enrich_activity_detail(
                        {"reason": "미보유 종목 매도 차단"},
                        product_context,
                    ),
                )
                return result

        # 5. 리스크 검사
        risk_result = await risk_manager.check(
            signal=signal,
            portfolio_cash=snap.get("cash", 0),
            portfolio_budget=snap.get("total_asset", 0),
            today_trade_count=snap.get("today_trade_count", 0),
            current_holding_count=snap.get("holding_count", 0),
            current_position=current_position,
            orderable_cash_krw=(
                orderable_amount_context.get("orderable_amount_krw")
                if orderable_amount_context
                else None
            ),
            cycle_id=cycle_id,
            dynamic_limits=dynamic_limits,
            market_regime=mkt_state.market_regime,
            rr_floor_overrides=mkt_state.rr_floor_overrides,
        )

        if not risk_result.get("approved"):
            logger.info("리스크 검사 미통과: {} - {}", symbol, risk_result.get("reason"))
            return result

        if risk_result.get("adjusted_amount_krw"):
            signal.suggested_amount_krw = risk_result["adjusted_amount_krw"]
        if risk_result.get("adjusted_quantity"):
            signal.suggested_quantity = risk_result["adjusted_quantity"]

        unit_price_krw = float(
            signal.metadata.get("entry_price_krw")
            or signal.metadata.get("price_krw")
            or (signal.suggested_price or 0) * (exchange_rate_to_krw if currency != "KRW" else 1.0)
        )
        total_amount = self._resolve_signal_amount_krw(signal, exchange_rate_to_krw)
        current_value_krw = float((current_position or {}).get("current_value_krw") or 0.0)
        total_asset = float(snap.get("total_asset") or 0.0)
        cash_basis_krw = float(risk_result.get("cash_basis_krw") or broker_cash_krw)
        combined_position_pct = (
            (current_value_krw + total_amount) / total_asset * 100
            if total_asset > 0
            else float((current_position or {}).get("position_pct") or 0.0)
        )
        post_trade_cash_ratio = (
            (cash_basis_krw - total_amount) / total_asset * 100
            if total_asset > 0
            else 0.0
        )
        signal.metadata.update({
            "entry_mode": entry_mode,
            "requested_amount_krw": round(total_amount, 4),
            "estimated_quantity": signal.suggested_quantity,
            "combined_position_pct": round(combined_position_pct, 4),
            "post_trade_cash_ratio": round(post_trade_cash_ratio, 4),
            "current_position": dict(current_position or {}),
            "analysis_source": analysis_source,
            "event_type": event_type or None,
            **orderable_signal_metadata,
        })

        # 6. 매매 결정 (자율/반자율)
        analysis_context = {
            "ai_recommendation": signal.action.value,
            "ai_confidence": signal.confidence or final.get("confidence") or analysis.get("confidence"),
            "ai_target_price": signal.target_price,
            "ai_stop_loss_price": signal.stop_loss_price,
            "ai_take_profit_price": signal.take_profit_price or signal.target_price,
            "trailing_stop_pct": signal.metadata.get("trailing_stop_pct"),
            "planned_hold_days": signal.metadata.get("planned_hold_days"),
            **existing_hold_plan_metadata,
            "entry_rsi": indicators.get("rsi_14"),
            "entry_macd_hist": indicators.get("macd_histogram"),
            "market_regime": mkt_state.market_regime,
            "strategy_type": strategy_type,
            "stock_name": name,
            "market": market_code,
            "currency": currency,
            "exchange_rate_to_krw": exchange_rate_to_krw,
            "product_type": product_metadata.get("product_type", "COMMON"),
            "is_leveraged": bool(product_metadata.get("is_leveraged")),
            "is_inverse": bool(product_metadata.get("is_inverse")),
            "leverage_multiplier": float(product_context.get("leverage_multiplier") or 1.0),
            "signed_exposure": float(product_context.get("signed_exposure") or 1.0),
            "restricted_product": bool(product_context.get("restricted_product")),
            "classification_source": product_context.get("classification_source"),
            "entry_mode": entry_mode,
            "combined_position_pct": signal.metadata.get("combined_position_pct"),
            "post_trade_cash_ratio": signal.metadata.get("post_trade_cash_ratio"),
            "current_position": dict(current_position or {}),
            "analysis_source": analysis_source,
            "event_type": event_type or None,
            **orderable_signal_metadata,
        }
        exec_result = await decision_maker.execute(
            signal, cycle_id=cycle_id, analysis_context=analysis_context,
        )
        result["executed"] = exec_result.get("success", True)

        # 주문 금액 기록 (병렬 잔고 트래커용)
        if result["executed"] and signal.action == SignalAction.BUY:
            result["order_amount"] = total_amount or (
                (signal.metadata.get("price_krw") or signal.suggested_price or 0)
                * (signal.suggested_quantity or 0)
            )

        return result

    # ── 컨텍스트 빌더 ──

    def _build_market_context(self, scan_result: dict) -> str:
        """시장 스캔 결과에서 Tier1/Tier2용 시장 컨텍스트 빌드"""
        parts = []

        regime = scan_result.get("market_regime", "")
        if regime:
            parts.append(f"시장 국면: {regime}")

        analysis = scan_result.get("market_analysis", scan_result.get("market_summary", ""))
        if analysis:
            parts.append(f"시장 분석: {analysis}")

        sectors = scan_result.get("leading_sectors", [])
        if sectors:
            parts.append(f"주도 섹터: {', '.join(sectors)}")

        if not parts:
            return "시장 컨텍스트 없음"

        return "\n".join(parts)

    async def _save_daily_report(
        self,
        report_date,
        parsed: dict,
        market_scope: str = "KRX",
        today_cycles: int = 0, today_analyses: int = 0,
        today_recommendations: int = 0, today_orders: int = 0,
    ) -> None:
        """장 마감 리뷰 AI 결과를 DailyReport에 저장"""
        from models.daily_report import DailyReport
        from repositories.daily_report_repository import DailyReportRepository
        from repositories.trade_result_repository import TradeResultRepository
        from trading.account_manager import account_manager
        from trading.market_profile import market_scope as resolve_market_scope

        scope = normalize_market_scope(market_scope)
        feedback = parsed.get("feedback_for_tomorrow", {})
        trade_eval = parsed.get("trade_evaluation", {})

        stats = {
            "risk_alerts": parsed.get("risk_alerts", []),
            "success_patterns": parsed.get("success_patterns", []),
            "failure_patterns": parsed.get("failure_patterns", []),
            "feedback": feedback,
            "trade_evaluation": trade_eval,
        }

        # 계좌 스냅샷 조회 (MCP 호출 — DB 세션 밖에서)
        open_position_count = 0
        balance_metrics = build_report_balance_metrics(scope, None, None)
        representative_market = "NASDAQ" if scope == "US" else scope
        try:
            balance, holdings = await account_manager.get_account_snapshot(representative_market)
            scoped_holdings = [h for h in holdings if resolve_market_scope(h.market) == scope]
            balance_metrics = build_report_balance_metrics(scope, balance, scoped_holdings)
            open_position_count = len(scoped_holdings)
        except Exception as e:
            logger.warning("일일 리포트 계좌 스냅샷 조회 실패 (계속): {}", str(e))

        async with AsyncSessionLocal() as session:
            async with session.begin():
                repo = DailyReportRepository(session)
                report = await repo.get_by_date(report_date, market_scope=scope)

                # TradeResult 기반 거래 통계 집계
                trade_result_repo = TradeResultRepository(session)
                opened_trades = await trade_result_repo.get_opened_by_date(report_date, market_scope=scope)
                completed_trades = await trade_result_repo.get_completed_by_date(report_date, market_scope=scope)

                buy_count = len(opened_trades)
                sell_count = len(completed_trades)
                win_count = sum(1 for t in completed_trades if t.is_win)
                loss_count = sum(1 for t in completed_trades if not t.is_win)
                total_pnl = sum_trade_pnl(completed_trades, scope)

                if open_position_count == 0:
                    all_open = await trade_result_repo.get_all_open(market_scope=scope)
                    if all_open:
                        open_position_count = len(all_open)

                report_data = {
                    "market_scope": scope,
                    "report_currency": balance_metrics.report_currency,
                    "total_cycles": today_cycles,
                    "total_analyses": today_analyses,
                    "total_recommendations": today_recommendations,
                    "total_orders": today_orders,
                    "buy_count": buy_count,
                    "sell_count": sell_count,
                    "win_count": win_count,
                    "loss_count": loss_count,
                    "total_pnl": total_pnl,
                    "unrealized_pnl": balance_metrics.unrealized_pnl,
                    "open_position_count": open_position_count,
                    "market_summary": parsed.get("today_review", ""),
                    "performance_review": json.dumps(trade_eval, ensure_ascii=False),
                    "lessons_learned": feedback.get("system_improvement", ""),
                    "next_day_plan": "",
                    "top_picks": "[]",
                    "strategy_stats": json.dumps(stats, ensure_ascii=False),
                }

                if report:
                    for k, v in report_data.items():
                        setattr(report, k, v)
                    logger.info("일일 리포트 갱신 완료: {}", report_date)
                else:
                    report = DailyReport(report_date=report_date, **report_data)
                    session.add(report)
                    logger.info("일일 리포트 생성 완료: {}", report_date)

    async def _build_trading_context(self, market: str | None = None) -> str:
        """트레이딩 컨텍스트 (프롬프트 주입용)"""
        from util.time_util import now_kst
        from trading.account_manager import account_manager
        from zoneinfo import ZoneInfo

        target = normalize_market(market or settings.primary_market_code)
        scope = market_scope(target)
        state = self._get_state(scope)

        now = now_kst().astimezone(ZoneInfo(market_timezone(target)))
        session = market_calendar.get_market_session(dt=now, market=target)
        mkt_cfg = settings.get_market_config(target, session=session)

        minutes_left: int | None = None
        if settings.market_has_force_liquidation(target, session=session):
            close_time = now.replace(
                hour=mkt_cfg["force_liquidation_hour"],
                minute=mkt_cfg["force_liquidation_minute"],
                second=0,
                microsecond=0,
            )
            minutes_left = max(0, int((close_time - now).total_seconds() / 60))

        minutes_until_buy_cutoff: int | None = None
        if settings.market_has_buy_cutoff(target, session=session):
            buy_cutoff_time = now.replace(
                hour=mkt_cfg["buy_cutoff_hour"],
                minute=mkt_cfg["buy_cutoff_minute"],
                second=0,
                microsecond=0,
            )
            minutes_until_buy_cutoff = max(0, int((buy_cutoff_time - now).total_seconds() / 60))
        timezone_label = now.tzname() or "LOCAL"

        daily_pnl_pct = 0.0
        if state.daily_start_balance > 0:
            try:
                balance = await account_manager.get_balance(target)
                daily_pnl_pct = (
                    (balance.total_asset - state.daily_start_balance)
                    / state.daily_start_balance * 100
                )
            except Exception:
                pass

        stats = await self._get_today_trade_stats(scope)

        context = (
            f"현재 세션: {session} | 현지 시각({timezone_label}): {now.strftime('%H:%M')}\n"
            f"신규 매수 마감까지: "
            f"{f'{minutes_until_buy_cutoff}분' if minutes_until_buy_cutoff is not None else '제한 없음'} | "
            f"강제 청산까지: "
            f"{f'{minutes_left}분' if minutes_left is not None else '없음'}\n"
            f"오늘 누적 손익: {daily_pnl_pct:+.2f}% | "
            f"매매 성적: {stats['wins']}승 {stats['losses']}패 "
            f"(총 {stats['total']}건)"
        )

        if scope == "CRYPTO":
            trading_style_profile = get_crypto_trading_style_profile(settings.crypto_trading_style_mode)
            context += (
                f"\n코인 운영: 포지션별 최대 {settings.crypto_timebox_hours}시간 보유 | "
                f"만료 시 자동 청산·정산 | 스캔 전 자동 리포트 없음"
                f"\n코인 매매 성향: {trading_style_profile['mode']} "
                f"({trading_style_profile['label']} | {trading_style_profile['summary']})"
            )
        elif not settings.DAY_TRADING_ONLY:
            context += (
                "\n모드: 스윙 (종목별 planned_hold_days 설정 + 매일 장마감 AI 재리뷰)"
            )

        if scope == "CRYPTO":
            review_reference = await self._load_coin_review_reference()
            if review_reference:
                context += f"\n최근 회고 참고:\n{review_reference}"

        return context

    async def _load_coin_review_reference(self) -> str:
        """최신 코인 회고 리포트를 프롬프트용 짧은 문장으로 정리한다."""
        from repositories.coin_daily_report_repository import CoinDailyReportRepository
        from util.time_util import ensure_kst

        try:
            async with AsyncSessionLocal() as session:
                repo = CoinDailyReportRepository(session)
                report = await repo.get_latest()
        except Exception as e:
            logger.debug("코인 회고 리포트 참조 로드 실패: {}", str(e))
            return ""

        if not report:
            return ""

        source_map = {
            "AUTO_PRE_CYCLE": "자동 회고",
            "AUTO_SETTLEMENT": "자동 정산",
            "MANUAL": "수동 리포트",
        }
        source_label = source_map.get(str(report.report_source or "").upper(), "코인 리포트")
        ended_at = ensure_kst(report.period_ended_at or report.created_at)
        lines = [f"- 기준 시각: {ended_at.strftime('%m/%d %H:%M')} KST ({source_label})"]
        if report.market_summary:
            lines.append(f"- 시장 요약: {str(report.market_summary)[:180]}")
        if report.lessons_learned:
            lines.append(f"- 학습 포인트: {str(report.lessons_learned)[:180]}")
        if report.next_day_plan:
            next_plan_label = (
                "다음 정산 윈도우 포인트"
                if str(report.report_source or "").upper() == "AUTO_SETTLEMENT"
                else "다음 사이클 계획"
            )
            lines.append(f"- {next_plan_label}: {str(report.next_day_plan)[:180]}")
        return "\n".join(lines)

    # ── 임계값 적용 ──

    def _apply_scan_thresholds(self, candidates: list[dict]) -> None:
        """시장 스캔 결과에서 AI가 결정한 모니터링 임계값을 event_detector에 적용"""
        applied = 0
        for c in candidates:
            symbol = c.get("symbol", "")
            market_code = normalize_market(c.get("market", settings.primary_market_code))
            monitoring = c.get("monitoring")
            self._remember_product_metadata(symbol, market_code, c)
            if not symbol or not isinstance(monitoring, dict):
                continue

            kwargs = {}
            for key in ("surge_pct", "drop_pct", "volume_spike_ratio"):
                raw_value = monitoring.get(key)
                if raw_value in (None, ""):
                    continue
                try:
                    kwargs[key] = float(raw_value)
                except (TypeError, ValueError):
                    logger.debug(
                        "[{}:{}] 스캔 모니터링 임계값 무시: {}={!r}",
                        market_code,
                        symbol,
                        key,
                        raw_value,
                    )

            if kwargs:
                event_detector.set_thresholds(symbol, market=market_code, **kwargs)
                applied += 1

        if applied:
            logger.info("AI 모니터링 임계값 설정: {}종목", applied)

    def _apply_trade_thresholds(
        self, symbol: str, tier1: dict, tier2: dict, market: str | None = None,
    ) -> None:
        """Tier1/Tier2 분석 결과에서 손절/익절/트레일링 스탑을 event_detector에 적용"""
        kwargs = {}

        stop_loss = tier2.get("stop_loss_price") or tier1.get("stop_loss_price")
        if stop_loss and float(stop_loss) > 0:
            kwargs["stop_loss"] = float(stop_loss)

        take_profit = (
            tier2.get("take_profit_price")
            or tier2.get("target_price")
            or tier1.get("target_price")
        )
        if take_profit and float(take_profit) > 0:
            kwargs["take_profit"] = float(take_profit)

        trailing = tier2.get("trailing_stop_pct") or tier1.get("trailing_stop_pct")
        if trailing and float(trailing) > 0:
            kwargs["trailing_stop_pct"] = float(trailing)

        if kwargs:
            event_detector.set_thresholds(symbol, market=market, **kwargs)
            logger.info(
                "AI 손절/익절 설정: {} → {}",
                symbol,
                ", ".join(f"{k}={v}" for k, v in kwargs.items()),
            )

    def _resolve_applied_trade_thresholds(
        self,
        symbol: str,
        signal: TradeSignal,
        *,
        market: str | None = None,
        fallback_trailing_pct: float | None = None,
    ) -> dict[str, float | None]:
        """event_detector 기준 최종 적용값을 DB 저장 컨텍스트용으로 정규화"""
        thresholds = event_detector.get_thresholds(symbol, market=market)

        stop_loss_price = self._try_float(getattr(thresholds, "stop_loss", None))
        if stop_loss_price is None or stop_loss_price <= 0:
            stop_loss_price = self._try_float(signal.stop_loss_price)

        take_profit_price = self._try_float(getattr(thresholds, "take_profit", None))
        if take_profit_price is None or take_profit_price <= 0:
            take_profit_price = self._try_float(signal.take_profit_price)
        if take_profit_price is None or take_profit_price <= 0:
            take_profit_price = self._try_float(signal.target_price)

        trailing_stop_pct = self._try_float(getattr(thresholds, "trailing_stop_pct", None))
        if trailing_stop_pct is None or trailing_stop_pct <= 0:
            trailing_stop_pct = self._try_float(fallback_trailing_pct)

        return {
            "stop_loss_price": round(stop_loss_price, 4) if stop_loss_price and stop_loss_price > 0 else None,
            "take_profit_price": (
                round(take_profit_price, 4)
                if take_profit_price and take_profit_price > 0
                else None
            ),
            "trailing_stop_pct": (
                round(trailing_stop_pct, 4)
                if trailing_stop_pct and trailing_stop_pct > 0
                else None
            ),
        }

    # ── Tier1 / Tier2 ──

    async def _tier1_analysis(
        self, symbol: str, name: str, current_price: float,
        chart_result: ChartAnalysisResult, price_data: dict,
        feedback_context: str = "",
        market: str | None = None,
        product_context: dict | None = None,
        current_position_context: str = "현재 포지션 없음 (신규 진입 후보)",
        portfolio_snapshot: dict | None = None,
        current_position: dict | None = None,
        dynamic_limits: dict | None = None,
        market_context: str = "",
        trading_context: str = "",
        orderable_amount_context: dict | None = None,
        cycle_id: str | None = None,
    ) -> dict | None:
        """Tier 1 AI 심층 분석"""
        market_code = normalize_market(market or price_data.get("market", settings.primary_market_code))
        scope = market_scope(market_code)
        currency = price_data.get("currency", market_currency(market_code))
        exchange_rate_to_krw = float(price_data.get("exchange_rate_to_krw", 0.0) or 0.0)
        if exchange_rate_to_krw <= 0 and currency != "KRW":
            price_krw_hint = self._try_float(price_data.get("price_krw"))
            if price_krw_hint is not None and price_krw_hint > 0 and current_price > 0:
                exchange_rate_to_krw = price_krw_hint / current_price
            elif current_position:
                exchange_rate_to_krw = float(current_position.get("exchange_rate_to_krw") or 0.0)
        if exchange_rate_to_krw <= 0:
            exchange_rate_to_krw = 1.0
        account_context = self._build_account_context(
            market=market_code,
            portfolio_snapshot=portfolio_snapshot,
            current_position=current_position,
            dynamic_limits=dynamic_limits,
            current_price=current_price,
            currency=currency,
            exchange_rate_to_krw=exchange_rate_to_krw,
            orderable_amount_context=orderable_amount_context,
        )
        current_price_text = f"{current_price:,.2f}{'원' if currency == 'KRW' else currency}"
        trade_value = float(price_data.get("trade_value") or 0)
        trade_value_text = (
            f"{trade_value:,.0f}원"
            if currency == "KRW"
            else f"{trade_value:,.2f}{currency}"
        )
        change_value = self._resolve_prompt_change_value(price_data, scope=scope)
        change_text = f"{change_value:+,.2f}{'원' if currency == 'KRW' else currency}"
        prompt_template = get_stock_analysis_prompt(market_code)
        prompt = prompt_template.format(
            stock_name=name,
            symbol=symbol,
            market=market_code,
            currency=currency,
            timebox_hours=settings.crypto_timebox_hours if scope == "CRYPTO" else "",
            current_price_text=current_price_text,
            change_text=change_text,
            change_rate=float(price_data.get("change_rate") or 0),
            volume=int(float(price_data.get("volume") or 0)),
            trade_value_text=trade_value_text,
            technical_indicators=chart_result.indicators_text or "지표 데이터 없음",
            chart_patterns=chart_result.patterns_text or "차트 패턴 데이터 없음",
            daily_data=chart_result.trend_text or "추세 데이터 없음",
            product_context=self._format_product_context_for_prompt(product_context),
            current_position_context=current_position_context,
            account_context=str(account_context["text"]),
            per=price_data.get("per", "N/A"),
            pbr=price_data.get("pbr", "N/A"),
            market_cap=price_data.get("market_cap", "N/A"),
            feedback_context=feedback_context or "매매 이력 없음",
            market_context=market_context or "시장 컨텍스트 없음",
            trading_context=trading_context or "트레이딩 컨텍스트 없음",
        )

        try:
            result_text, provider = await llm_factory.generate_tier1(
                prompt,
                system_prompt=get_stock_analysis_system(
                    market_code,
                    trading_style_mode=settings.crypto_trading_style_mode if scope == "CRYPTO" else None,
                ),
                profile=Tier1Profile.ANALYSIS,
                scope=scope,
                phase="cycle",
                symbol=symbol,
                cycle_id=cycle_id,
            )
            parsed = self._parse_json(result_text)
            if parsed:
                parsed["provider"] = provider
                parsed["market"] = market_code
                parsed["currency"] = currency
                parsed["exchange_rate_to_krw"] = exchange_rate_to_krw
                parsed["price_krw"] = float(price_data.get("price_krw", current_price) or 0.0)
                parsed["trade_value"] = trade_value
                parsed["product_context"] = dict(product_context or {})
            return parsed
        except Exception as e:
            logger.error("Tier 1 분석 실패 ({}): {}", symbol, str(e))
            return None

    async def _tier2_review(
        self, symbol: str, name: str, current_price: float,
        strategy_type: str, tier1_analysis: dict,
        feedback_context: str = "",
        market: str | None = None,
        product_context: dict | None = None,
        current_position_context: str = "현재 포지션 없음 (신규 진입 후보)",
        hold_plan_context: str = "보유 계획 없음 (신규 진입 후보)",
        current_position: dict | None = None,
        chart_result: ChartAnalysisResult | None = None,
        dynamic_limits: dict | None = None,
        market_context: str = "",
        trading_context: str = "",
        portfolio_snapshot: dict | None = None,
        orderable_amount_context: dict | None = None,
        cycle_id: str | None = None,
    ) -> dict | None:
        """Tier 2 최종 검토"""
        snap = portfolio_snapshot or {}
        market_code = normalize_market(market or tier1_analysis.get("market", settings.primary_market_code))
        scope = market_scope(market_code)
        strategy = self._get_state(scope).strategies.get(strategy_type)
        currency = tier1_analysis.get("currency", market_currency(market_code))
        exchange_rate_to_krw = float(tier1_analysis.get("exchange_rate_to_krw", 1.0) or 1.0)
        account_context = self._build_account_context(
            market=market_code,
            portfolio_snapshot=portfolio_snapshot,
            current_position=current_position,
            dynamic_limits=dynamic_limits,
            current_price=current_price,
            currency=currency,
            exchange_rate_to_krw=exchange_rate_to_krw,
            orderable_amount_context=orderable_amount_context,
        )
        current_price_text = f"{current_price:,.2f}{'원' if currency == 'KRW' else currency}"
        trade_value = float(tier1_analysis.get("trade_value") or 0)
        trade_value_text = (
            f"{trade_value:,.0f}원"
            if currency == "KRW"
            else f"{trade_value:,.2f}{currency}"
        )
        tier1_prompt_payload = {
            key: value
            for key, value in tier1_analysis.items()
            if key != "price_krw"
        }
        chart_snapshot = chart_result.prompt_text if chart_result and chart_result.prompt_text else "차트 요약 없음"

        tuning_suggestions = "조정 제안 없음"
        if chart_result and chart_result.trend:
            trend = chart_result.trend
            suggestions = []
            if trend.direction == "BEARISH" and trend.strength == "STRONG":
                suggestions.append("강한 하락 추세 - 매수 진입 자제, 손절 타이트하게 설정 권장")
            if trend.momentum == "DECELERATING":
                suggestions.append("모멘텀 감속 중 - 진입 시점 재고 필요")
            if trend.volatility_state == "EXPANDING":
                suggestions.append("변동성 확대 구간 - 포지션 사이즈 축소 권장")
            if trend.volatility_state == "CONTRACTING":
                suggestions.append("변동성 수축 - 돌파 대기, 포지션 준비")
            if suggestions:
                tuning_suggestions = "\n".join(f"- {s}" for s in suggestions)

        if scope == "CRYPTO":
            max_hold_window = f"{settings.crypto_timebox_hours}시간 (만료 시 자동 청산)"
        else:
            max_hold_window = "장마감 AI 재리뷰 기반 동적 보유"

        review_prompt_template = get_final_review_prompt(
            market_code,
            trading_style_mode=settings.crypto_trading_style_mode if scope == "CRYPTO" else None,
        )
        exchange_rate_line = ""
        if currency != "KRW" and not bool(account_context.get("use_foreign_display")):
            exchange_rate_line = f"- 환산 참고: 1{currency} ≈ {exchange_rate_to_krw:,.2f}원"
        prompt = review_prompt_template.format(
            tier1_analysis=json.dumps(tier1_prompt_payload, ensure_ascii=False, indent=2),
            stock_name=name,
            symbol=symbol,
            market=market_code,
            currency=currency,
            current_position_context=current_position_context,
            hold_plan_context=hold_plan_context,
            account_context=str(account_context["text"]),
            chart_snapshot=chart_snapshot,
            product_context=self._format_product_context_for_prompt(product_context),
            current_price_text=current_price_text,
            trade_value_text=trade_value_text,
            exchange_rate_line=exchange_rate_line,
            exchange_rate_to_krw=exchange_rate_to_krw,
            strategy_type=strategy_type,
            max_amount_text=account_context["max_additional_amount_text"],
            max_amount=account_context["max_additional_amount"] or 0,
            max_quantity=format_quantity(account_context["max_additional_quantity"] or 0, market_code),
            holding_count=snap.get("holding_count") or 0,
            current_position_pct=account_context["current_position_pct"] or 0,
            position_pct=account_context["projected_combined_position_pct"] or 0,
            stop_loss_pct=getattr(strategy, "stop_loss_pct", None) or -3,
            take_profit_pct=getattr(strategy, "take_profit_pct", None) or 5,
            max_hold_window=max_hold_window,
            max_position_pct=account_context["max_position_pct"],
            feedback_context=feedback_context or "매매 이력 없음",
            tuning_suggestions=tuning_suggestions,
            market_context=market_context or "시장 컨텍스트 없음",
            trading_context=trading_context or "트레이딩 컨텍스트 없음",
        )

        try:
            result_text, provider = await llm_factory.generate_tier2(
                prompt,
                system_prompt=get_final_review_system(
                    market_code,
                    trading_style_mode=settings.crypto_trading_style_mode if scope == "CRYPTO" else None,
                ),
                scope=scope,
                phase="cycle",
                symbol=symbol,
                cycle_id=cycle_id,
            )
            parsed = self._parse_json(result_text)
            if parsed:
                parsed["provider"] = provider
                parsed["market"] = market_code
                parsed["currency"] = currency
                parsed["product_context"] = dict(product_context or {})
                parsed = self._normalize_tier2_price_fields(
                    parsed,
                    current_price=current_price,
                    currency=currency,
                    exchange_rate_to_krw=exchange_rate_to_krw,
                )
                for price_key in ("entry_price", "target_price", "stop_loss_price", "take_profit_price"):
                    price_value = parsed.get(price_key)
                    if price_value:
                        parsed[f"{price_key}_krw"] = round(
                            float(price_value) * exchange_rate_to_krw if currency != "KRW" else float(price_value),
                            4,
                        )
            return parsed
        except Exception as e:
            logger.error("Tier 2 검토 실패 ({}): {}", symbol, str(e))
            return None

    @classmethod
    def _validate_close_hold_review(
        cls,
        review: dict | None,
        *,
        current_price: float,
    ) -> str | None:
        if not review:
            return "응답 없음"

        action = str(review.get("action") or "").upper()
        if action not in {"HOLD", "SELL"}:
            return "action은 HOLD 또는 SELL만 허용"
        review["action"] = action

        confidence = cls._try_float(review.get("confidence"))
        review["confidence"] = round(confidence, 4) if confidence is not None else 0.0

        trailing_stop_pct = cls._try_float(review.get("trailing_stop_pct"))
        review["trailing_stop_pct"] = (
            round(trailing_stop_pct, 4)
            if trailing_stop_pct and trailing_stop_pct > 0
            else 0.0
        )

        if action == "SELL":
            planned_hold_days = review.get("planned_hold_days")
            if planned_hold_days is not None:
                review["planned_hold_days"] = cls._normalize_planned_hold_days(planned_hold_days, default=1)
            return None

        stop_loss_price = cls._try_float(review.get("stop_loss_price"))
        take_profit_price = cls._try_float(review.get("take_profit_price"))
        if stop_loss_price is None or stop_loss_price <= 0:
            return "HOLD 응답 stop_loss_price 누락 또는 비정상 값"
        if take_profit_price is None or take_profit_price <= 0:
            return "HOLD 응답 take_profit_price 누락 또는 비정상 값"
        if stop_loss_price >= current_price:
            return "HOLD 응답 손절가가 현재가 이상으로 설정됨"
        if take_profit_price <= current_price:
            return "HOLD 응답 익절가가 현재가 이하로 설정됨"

        review["stop_loss_price"] = round(stop_loss_price, 4)
        review["take_profit_price"] = round(take_profit_price, 4)
        review["planned_hold_days"] = cls._normalize_planned_hold_days(review.get("planned_hold_days"), default=1)
        return None

    async def review_close_hold_position(
        self,
        *,
        holding,
        trade_result,
        current_price: float,
        market: str | None = None,
        cycle_id: str | None = None,
    ) -> dict | None:
        """장마감 보유 포지션을 AI로 재리뷰해 HOLD/SELL 판단을 반환한다."""
        from trading.account_manager import account_manager

        market_code = normalize_market(
            market
            or getattr(holding, "market", None)
            or getattr(trade_result, "market", None)
            or settings.primary_market_code
        )
        scope = market_scope(market_code)
        if scope == "CRYPTO":
            return {
                "action": "SELL",
                "reason": "주식 장마감 보유 재리뷰 전용 경로",
                "provider": "system-guard",
            }

        symbol = str(
            getattr(holding, "symbol", None)
            or getattr(trade_result, "stock_symbol", "")
            or ""
        ).upper()
        name = str(
            getattr(holding, "name", None)
            or getattr(trade_result, "stock_name", None)
            or symbol
        )
        currency = str(
            getattr(holding, "currency", None)
            or getattr(trade_result, "currency", None)
            or market_currency(market_code)
        )
        exchange_rate_to_krw = float(
            getattr(holding, "exchange_rate_to_krw", None)
            or getattr(trade_result, "exchange_rate_to_krw", None)
            or 1.0
        )

        if not symbol or current_price <= 0:
            return {
                "action": "SELL",
                "reason": "장마감 재리뷰 입력값 부족",
                "provider": "system-guard",
            }

        trade_plan = self._resolve_trade_hold_plan(trade_result)
        market_date = market_calendar.market_date(market=market_code)
        entry_at = getattr(trade_result, "entry_at", None) or getattr(trade_result, "created_at", None)
        calendar_hold_days = 0
        if entry_at:
            calendar_hold_days = max(0, (market_date - entry_at.date()).days)

        try:
            balance, holdings = await account_manager.get_account_snapshot(market_code)
            holding_symbols, holding_positions = self._build_holding_snapshot(holdings, balance.total_asset)
            portfolio_snapshot = {
                "cash": balance.cash,
                "total_asset": balance.total_asset,
                "holding_count": len(holdings),
                "holding_symbols": holding_symbols,
                "holding_positions": holding_positions,
            }
            current_position = self._get_existing_position(portfolio_snapshot, symbol, market_code)
        except Exception as e:
            logger.warning("[{}] 장마감 계좌 스냅샷 조회 실패 {}: {}", market_code, symbol, str(e))
            return {
                "action": "SELL",
                "reason": f"장마감 계좌 스냅샷 조회 실패: {str(e)[:80]}",
                "provider": "system-guard",
            }

        if not current_position:
            _, fallback_positions = self._build_holding_snapshot([holding], 0.0)
            current_position = fallback_positions.get(self._instrument_key(symbol, market_code))
        if not current_position:
            return {
                "action": "SELL",
                "reason": "장마감 포지션 스냅샷 불일치",
                "provider": "system-guard",
            }

        try:
            daily_resp = await mcp_client.get_daily_price(symbol, market=market_code)
            minute_resp = await mcp_client.get_minute_price(symbol, market=market_code)
        except Exception as e:
            logger.warning("[{}] 장마감 차트 데이터 조회 실패 {}: {}", market_code, symbol, str(e))
            return {
                "action": "SELL",
                "reason": f"장마감 차트 데이터 조회 실패: {str(e)[:80]}",
                "provider": "system-guard",
            }

        if not daily_resp.success or not daily_resp.data:
            return {
                "action": "SELL",
                "reason": "장마감 일봉 데이터 조회 실패",
                "provider": "system-guard",
            }
        if not minute_resp.success or not minute_resp.data:
            return {
                "action": "SELL",
                "reason": "장마감 분봉 데이터 조회 실패",
                "provider": "system-guard",
            }

        daily_df = self._sort_market_data_frame(pd.DataFrame(daily_resp.data.get("prices") or []), "date")
        minute_df = self._sort_market_data_frame(pd.DataFrame(minute_resp.data.get("prices") or []), "time")
        consistency_issue = self._detect_price_consistency_issue(current_price, daily_df, minute_df)
        if consistency_issue:
            logger.warning(
                "[{}] 장마감 데이터 정합성 실패 {}: {}",
                market_code,
                symbol,
                consistency_issue,
            )
            return {
                "action": "SELL",
                "reason": "장마감 데이터 정합성 실패",
                "provider": "system-guard",
                "risk_warnings": [json.dumps(consistency_issue, ensure_ascii=False)],
            }

        chart_result = chart_analyzer.analyze(daily_df, minute_df)
        current_position_context = self._format_current_position_for_prompt(current_position)
        runtime = self._get_state(scope)
        market_context = runtime.market_context or "시장 컨텍스트 없음"
        trading_context = runtime.trading_context or await self._build_trading_context(market_code)

        feedback_context = "매매 이력 없음"
        try:
            async with AsyncSessionLocal() as session:
                feedback_context = await FeedbackContextBuilder(
                    session,
                    market_scope=scope,
                ).build_full_context(
                    trade_result.strategy_type or "",
                    symbol,
                    current_regime=runtime.market_regime or "",
                    current_rsi=chart_result.indicators.get("rsi_14"),
                    market_scope=scope,
                )
        except Exception as e:
            logger.warning("[{}] 장마감 피드백 컨텍스트 생성 실패 {}: {}", market_code, symbol, str(e))

        product_metadata = self._get_product_metadata(symbol, market_code)
        if not product_metadata:
            product_metadata = {"name": name}
        product_context = build_product_context(symbol, market_code, product_metadata)

        entry_price = float(getattr(trade_result, "entry_price", 0.0) or 0.0)
        target_price = self._try_float(getattr(trade_result, "ai_target_price", None))
        stop_loss_price = self._try_float(getattr(trade_result, "ai_stop_loss_price", None))
        take_profit_price = self._try_float(getattr(trade_result, "ai_take_profit_price", None))
        trailing_stop_pct = trade_plan["notes"].get("trailing_stop_pct")
        chart_snapshot = chart_result.prompt_text if chart_result and chart_result.prompt_text else "차트 요약 없음"
        entry_snapshot_lines = [
            f"- entry_at: {entry_at.isoformat() if entry_at else '없음'}",
            f"- entry_price: {entry_price:,.2f}{currency}" if entry_price > 0 else "- entry_price: 없음",
            f"- ai_confidence: {float(getattr(trade_result, 'ai_confidence', 0.0) or 0.0):.2f}",
            f"- ai_target_price: {target_price:,.2f}{currency}" if target_price else "- ai_target_price: 없음",
            f"- ai_stop_loss_price: {stop_loss_price:,.2f}{currency}" if stop_loss_price else "- ai_stop_loss_price: 없음",
            (
                f"- ai_take_profit_price: {take_profit_price:,.2f}{currency}"
                if take_profit_price
                else "- ai_take_profit_price: 없음"
            ),
            f"- 진입 사유: {getattr(trade_result, 'ai_recommendation', '') or '기록 없음'}",
        ]

        prompt = STOCK_CLOSE_REVIEW_PROMPT.format(
            market_context=market_context,
            trading_context=trading_context,
            current_position_context=current_position_context,
            account_context=self._build_account_context(
                market=market_code,
                portfolio_snapshot=portfolio_snapshot,
                current_position=current_position,
                dynamic_limits=None,
                current_price=current_price,
                currency=currency,
                exchange_rate_to_krw=exchange_rate_to_krw,
                orderable_amount_context=None,
            )["text"],
            entry_snapshot="\n".join(entry_snapshot_lines),
            chart_snapshot=chart_snapshot,
            product_context=self._format_product_context_for_prompt(product_context),
            stock_name=name,
            symbol=symbol,
            market=market_code,
            currency=currency,
            current_price_text=(
                f"{current_price:,.2f}원" if currency == "KRW" else f"{current_price:,.2f}{currency}"
            ),
            exchange_rate_line="" if is_us_market(market_code) or currency == "KRW" else f"- 환산 참고: 1{currency} ≈ {exchange_rate_to_krw:,.2f}원",
            exchange_rate_to_krw=exchange_rate_to_krw,
            strategy_type=getattr(trade_result, "strategy_type", "") or "",
            planned_hold_days=trade_plan["planned_hold_days"],
            close_review_count=trade_plan["close_review_count"],
            last_close_review_date=trade_plan["last_close_review_date"] or "없음",
            calendar_hold_days=calendar_hold_days,
            existing_stop_loss_text=(
                f"{stop_loss_price:,.2f}{currency}" if stop_loss_price else "없음"
            ),
            existing_take_profit_text=(
                f"{take_profit_price:,.2f}{currency}" if take_profit_price else "없음"
            ),
            existing_trailing_text=(
                f"{float(trailing_stop_pct):.2f}%"
                if self._try_float(trailing_stop_pct) and float(trailing_stop_pct) > 0
                else "미사용"
            ),
            feedback_context=feedback_context or "매매 이력 없음",
        )

        try:
            result_text, provider = await llm_factory.generate_tier2(
                prompt,
                system_prompt=STOCK_CLOSE_REVIEW_SYSTEM,
                scope=scope,
                phase="close_review",
                symbol=symbol,
                cycle_id=cycle_id,
            )
        except Exception as e:
            logger.error("장마감 보유 재리뷰 실패 ({}): {}", symbol, str(e))
            return None

        parsed = self._parse_json(result_text)
        if not parsed:
            return None

        parsed["provider"] = provider
        parsed["market"] = market_code
        parsed["currency"] = currency
        parsed = self._normalize_tier2_price_fields(
            parsed,
            current_price=current_price,
            currency=currency,
            exchange_rate_to_krw=exchange_rate_to_krw,
        )

        validation_issue = self._validate_close_hold_review(parsed, current_price=current_price)
        if validation_issue:
            logger.warning("[{}] 장마감 보유 재리뷰 응답 검증 실패 {}: {}", market_code, symbol, validation_issue)
            return None

        if parsed.get("action") == "HOLD":
            parsed["planned_hold_days"] = max(
                int(parsed["planned_hold_days"]),
                int(trade_plan["close_review_count"]) + 1,
            )

        return parsed
