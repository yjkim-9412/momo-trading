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
from trading.risk_policy import get_crypto_trading_style_profile, resolve_crypto_rr_floor

from agent.trading_agent._types import _ENTRY_MODE_NEW


class AnalysisMixin:
    """핵심 분석 파이프라인 Mixin: _analyze_and_trade, Tier1/2, 컨텍스트 빌더"""

    @staticmethod
    def _try_float(value) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
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

        result = {"symbol": symbol, "signal": False, "executed": False}

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
        t1_timer = activity_logger.timer()
        await activity_logger.log(
            ActivityType.TIER1_ANALYSIS, ActivityPhase.START,
            f"\U0001f4ca [{name}] Tier1 분석 시작",
            cycle_id=cycle_id, symbol=symbol,
            detail=self._enrich_activity_detail(None, product_context),
        )

        price_payload = dict(price_resp.data or {})
        if market_code and not price_payload.get("market"):
            price_payload["market"] = market_code

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
        rule_min_conf = None
        for scope in [strategy_type, "ALL"]:
            val = _param_overrides.get(scope, {}).get("min_confidence")
            if val is not None and (rule_min_conf is None or val > rule_min_conf):
                rule_min_conf = val

        if rule_min_conf and tier1_confidence < rule_min_conf:
            await activity_logger.log(
                ActivityType.TRADING_RULE, ActivityPhase.SKIP,
                f"🚫 [{name}] 신뢰도 게이트 차단: {tier1_confidence:.0%} < "
                f"규칙 최소 {rule_min_conf:.0%} (일일 리뷰 피드백)",
                cycle_id=cycle_id, symbol=symbol,
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
                    rr_overrides = active_rules.get("rr_floor_overrides", {})
                    merged_rr_overrides = {
                        **(mkt_state.rr_floor_overrides or {}),
                        **rr_overrides,
                    }
                    if scope == "CRYPTO":
                        min_rr = resolve_crypto_rr_floor(
                            mkt_state.market_regime,
                            settings.crypto_trading_style_mode,
                            merged_rr_overrides,
                        )
                    else:
                        min_rr = risk_manager.resolve_rr_floor(
                            mkt_state.market_regime,
                            merged_rr_overrides,
                        )
                    if code_rr < min_rr:
                        await activity_logger.log(
                            ActivityType.TRADING_RULE, ActivityPhase.SKIP,
                            f"🚫 [{name}] RR 비율 검증 실패: "
                            f"코드 계산 {code_rr:.2f}:1 < 최소 {min_rr}:1 "
                            f"(target={t1_target:,.0f}, stop={t1_stop:,.0f}, "
                            f"현재가={current_price:,.0f})",
                            cycle_id=cycle_id, symbol=symbol,
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

            stop_loss_price = final.get("stop_loss_price")
            if not stop_loss_price and strategy:
                sl_pct = getattr(strategy, "stop_loss_pct", None) or -3
                stop_loss_price = final["entry_price"] * (1 + sl_pct / 100)

            target_price = final.get("target_price")
            if not target_price and strategy:
                tp_pct = getattr(strategy, "take_profit_pct", None) or 5
                target_price = final["entry_price"] * (1 + tp_pct / 100)

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
                strength=analysis.get("confidence", 0.7),
                suggested_price=final["entry_price"],
                suggested_quantity=signal_quantity,
                suggested_amount_krw=signal_amount_krw,
                target_price=target_price,
                stop_loss_price=stop_loss_price,
                urgency=SignalUrgency.IMMEDIATE,
                strategy_type=strategy_type,
                reason=final.get("reason", "Tier2 승인"),
                confidence=analysis.get("confidence", 0.7),
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

            signal.metadata = {
                **(signal.metadata or {}),
                "market": market_code,
                "currency": currency,
                "exchange_rate_to_krw": exchange_rate_to_krw,
                "live_price": current_price,
                "live_price_krw": price_krw or (current_price * exchange_rate_to_krw),
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
            "ai_recommendation": analysis.get("recommendation"),
            "ai_confidence": analysis.get("confidence"),
            "ai_target_price": analysis.get("target_price"),
            "ai_stop_loss_price": analysis.get("stop_loss_price"),
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

        feedback = parsed.get("feedback_for_tomorrow", {})
        trade_eval = parsed.get("trade_evaluation", {})

        stats = {
            "risk_alerts": parsed.get("risk_alerts", []),
            "success_patterns": parsed.get("success_patterns", []),
            "failure_patterns": parsed.get("failure_patterns", []),
            "feedback": feedback,
            "trade_evaluation": trade_eval,
        }

        async with AsyncSessionLocal() as session:
            async with session.begin():
                repo = DailyReportRepository(session)
                report = await repo.get_by_date(report_date, market_scope=market_scope)

                report_data = {
                    "market_scope": normalize_market_scope(market_scope),
                    "total_cycles": today_cycles,
                    "total_analyses": today_analyses,
                    "total_recommendations": today_recommendations,
                    "total_orders": today_orders,
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
        mkt_cfg = settings.get_market_config(target)

        now = now_kst().astimezone(ZoneInfo(market_timezone(target)))

        minutes_left: int | None = None
        if settings.market_has_force_liquidation(target):
            close_time = now.replace(
                hour=mkt_cfg["force_liquidation_hour"],
                minute=mkt_cfg["force_liquidation_minute"],
                second=0,
                microsecond=0,
            )
            minutes_left = max(0, int((close_time - now).total_seconds() / 60))

        minutes_until_buy_cutoff: int | None = None
        if settings.market_has_buy_cutoff(target):
            buy_cutoff_time = now.replace(
                hour=mkt_cfg["buy_cutoff_hour"],
                minute=mkt_cfg["buy_cutoff_minute"],
                second=0,
                microsecond=0,
            )
            minutes_until_buy_cutoff = max(0, int((buy_cutoff_time - now).total_seconds() / 60))
        session = market_calendar.get_market_session(dt=now, market=target)
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
                f"\n모드: 스윙 (STABLE {settings.MAX_HOLD_DAYS_STABLE}일, "
                f"AGGRESSIVE {settings.MAX_HOLD_DAYS_AGGRESSIVE}일)"
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
        exchange_rate_to_krw = float(price_data.get("exchange_rate_to_krw", 1.0) or 1.0)
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
            max_hold_days = (
                settings.MAX_HOLD_DAYS_AGGRESSIVE
                if strategy_type == "AGGRESSIVE_SHORT"
                else settings.MAX_HOLD_DAYS_STABLE
            )
            max_hold_window = f"{max_hold_days}일"

        review_prompt_template = get_final_review_prompt(
            market_code,
            trading_style_mode=settings.crypto_trading_style_mode if scope == "CRYPTO" else None,
        )
        prompt = review_prompt_template.format(
            tier1_analysis=json.dumps(tier1_prompt_payload, ensure_ascii=False, indent=2),
            stock_name=name,
            symbol=symbol,
            market=market_code,
            currency=currency,
            current_position_context=current_position_context,
            account_context=str(account_context["text"]),
            chart_snapshot=chart_snapshot,
            product_context=self._format_product_context_for_prompt(product_context),
            current_price_text=current_price_text,
            trade_value_text=trade_value_text,
            exchange_rate_to_krw=exchange_rate_to_krw,
            strategy_type=strategy_type,
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
