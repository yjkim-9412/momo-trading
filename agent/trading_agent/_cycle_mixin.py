"""CycleMixin: 사이클 오케스트레이션, 스케줄 생성, 장마감 리뷰"""
import asyncio
import json

from loguru import logger

from core.config import settings
from core.database import AsyncSessionLocal
from core.events import Event, EventType, event_bus
from agent.market_scanner import market_scanner
from analysis.llm.llm_factory import llm_factory
from analysis.llm.prompts.cycle_scheduler import SCHEDULE_HINT_PROMPT, SCHEDULE_HINT_SYSTEM
from analysis.llm.prompts.daily_plan import DAILY_PLAN_PROMPT, DAILY_PLAN_SYSTEM
from scheduler.market_calendar import market_calendar
from services.activity_logger import activity_logger
from trading.enums import ActivityPhase, ActivityType, Tier1Profile
from trading.market_profile import (
    is_crypto_market,
    market_currency,
    market_scope,
    market_timezone,
    normalize_market,
    normalize_market_scope,
    requires_mcp_connection,
)
from trading.mcp_client import mcp_client
from trading.quantity_policy import normalize_quantity
from trading.risk_policy import normalize_crypto_regime


class CycleMixin:
    """사이클 오케스트레이션 Mixin"""

    async def preview_cycle(self, market: str | None = None) -> dict:
        """사이클 실행 가능 여부와 skip 사유를 사전 판정"""
        target = normalize_market(market or settings.primary_market_code)
        scope = market_scope(target)
        runtime = self._get_state(scope)
        trading_date = self._refresh_runtime_date(runtime, scope)

        if not market_calendar.is_trading_hours(target) and runtime.after_hours_lock.locked():
            return self._skip_result(
                "after_hours_already_running",
                scope,
                trading_date,
                mode="AFTER_HOURS",
            )

        if runtime.cycle_lock.locked():
            return self._skip_result("cycle_already_running", scope, trading_date)

        if is_crypto_market(target) and runtime.settlement_lock.locked():
            return self._skip_result("settlement_already_running", scope, trading_date)

        if market_calendar.is_trading_hours(target):
            if requires_mcp_connection(target) and not mcp_client.is_connected:
                result = self._skip_result("mcp_unavailable", scope, trading_date, mode="TRADING")
                result.update({
                    "scanned": 0,
                    "analyzed": 0,
                    "signals": 0,
                    "executed": 0,
                    "selected_symbols": [],
                })
                return result
            return {
                "allowed": True,
                "mode": "TRADING",
                "market_scope": scope,
                "trading_date": trading_date.isoformat(),
            }

        return await self._preview_after_hours_cycle(
            target,
            runtime,
            trading_date,
            skip_running_checks=True,
        )

    async def start(self) -> None:
        """에이전트 시작 - 실시간 이벤트 구독"""
        if self._running:
            logger.debug("AI Trading Agent 이미 시작됨 — 이벤트 재구독 스킵")
            return
        self._running = True
        event_bus.subscribe(EventType.VOLUME_SPIKE, self._on_market_event)
        event_bus.subscribe(EventType.PRICE_SURGE, self._on_market_event)
        event_bus.subscribe(EventType.PRICE_DROP, self._on_market_event)
        event_bus.subscribe(EventType.STOP_LOSS_HIT, self._on_stop_loss)
        event_bus.subscribe(EventType.TAKE_PROFIT_HIT, self._on_take_profit)
        logger.info("AI Trading Agent 시작 — 실시간 이벤트 구독 활성화")

    async def stop(self) -> None:
        """에이전트 중지"""
        self._running = False
        self._analyzing.clear()
        logger.info("AI Trading Agent 중지")

    async def run_cycle(
        self,
        market: str | None = None,
        *,
        scheduled_budget_remaining: int | None = None,
        trigger_source: str = "SYSTEM",
        trigger_reason: str | None = None,
    ) -> dict:
        """에이전트 1회 실행 사이클 — 장중이면 매매, 장외면 리뷰"""
        target = normalize_market(market or settings.primary_market_code)
        scope = market_scope(target)
        runtime = self._get_state(scope)
        trading_date = self._refresh_runtime_date(runtime, scope)

        if not market_calendar.is_trading_hours(target) and runtime.after_hours_lock.locked():
            logger.info("[{}] 장마감 리뷰 이미 실행 중 — 중복 트리거 무시", scope)
            return self._skip_result(
                "after_hours_already_running",
                scope,
                trading_date,
                mode="AFTER_HOURS",
            )

        if runtime.cycle_lock.locked():
            logger.warning("[{}] 사이클 이미 실행 중 — 중복 트리거 무시", scope)
            return self._skip_result("cycle_already_running", scope, trading_date)

        if is_crypto_market(target) and runtime.settlement_lock.locked():
            logger.warning("[{}] 타임박스 정산 실행 중 — 사이클 중복 트리거 무시", scope)
            return self._skip_result("settlement_already_running", scope, trading_date)

        with activity_logger.context(market_scope=scope, trading_date=trading_date):
            async with runtime.cycle_lock:
                if market_calendar.is_trading_hours(target):
                    if requires_mcp_connection(target) and not mcp_client.is_connected:
                        from util.time_util import now_kst

                        self._set_cycle_runtime_state(
                            runtime,
                            attempted_at=now_kst(),
                            status="SKIPPED",
                        )
                        logger.warning("[{}] MCP 미연결 → 장중 매매 사이클 스킵", target)
                        await activity_logger.log(
                            ActivityType.CYCLE,
                            ActivityPhase.ERROR,
                            f"\u26a0\ufe0f [{target}] MCP 미연결 — 장중 매매 사이클 스킵",
                        )
                        return {
                            "skipped": True,
                            "reason": "mcp_unavailable",
                            "market_scope": scope,
                            "scanned": 0,
                            "analyzed": 0,
                            "signals": 0,
                            "executed": 0,
                            "selected_symbols": [],
                        }
                    # 데이트레이딩 모드: 매수 마감 시간 이후 신규 매수 차단
                    if settings.DAY_TRADING_ONLY:
                        from datetime import time as _time
                        from util.time_util import now_kst
                        from zoneinfo import ZoneInfo

                        mkt_cfg = settings.get_market_config(target)
                        cutoff = _time(mkt_cfg["buy_cutoff_hour"], mkt_cfg["buy_cutoff_minute"])
                        market_now = now_kst().astimezone(ZoneInfo(market_timezone(target)))
                        if market_now.time() >= cutoff:
                            self._set_cycle_runtime_state(
                                runtime,
                                attempted_at=now_kst(),
                                status="SKIPPED",
                            )
                            logger.info("매수 마감 시간({}) 경과 → 신규 매매 사이클 스킵", cutoff)
                            await activity_logger.log(
                                ActivityType.CYCLE,
                                ActivityPhase.COMPLETE,
                                f"\u23f0 매수 마감({cutoff.strftime('%H:%M')}) — 신규 매수 차단, 보유종목 모니터링만 유지",
                            )
                            return {"skipped": True, "reason": "buy_cutoff", "market_scope": scope}
                    return await self._run_trading_cycle(
                        target,
                        scheduled_budget_remaining=scheduled_budget_remaining,
                        trigger_source=trigger_source,
                        trigger_reason=trigger_reason,
                    )
                review_preview = await self._preview_after_hours_cycle(target, runtime, trading_date)
                if review_preview.get("skipped"):
                    logger.info(
                        "[{}] 장마감 리뷰 스킵: {}",
                        scope,
                        review_preview["reason"],
                    )
                    return review_preview
                return await self._run_after_hours_cycle(target)

    async def _run_trading_cycle(
        self,
        market: str | None = None,
        *,
        scheduled_budget_remaining: int | None = None,
        trigger_source: str = "SYSTEM",
        trigger_reason: str | None = None,
    ) -> dict:
        """장중 사이클: 스캔 → 분석 → 매매"""
        target = normalize_market(market or settings.primary_market_code)
        scope = market_scope(target)
        state = self._get_state(scope)
        trading_date = self._refresh_runtime_date(state, scope)
        from util.time_util import now_kst

        cycle_id = activity_logger.start_cycle()
        cycle_timer = activity_logger.timer()
        attempted_at = now_kst()
        self._set_cycle_runtime_state(
            state,
            attempted_at=attempted_at,
            status="RUNNING",
        )
        stage = "session_start"
        session_started = False

        results = {
            "market_scope": scope,
            "trading_date": trading_date.isoformat(),
            "scanned": 0,
            "analyzed": 0,
            "signals": 0,
            "executed": 0,
            "selected_symbols": [],
        }
        selected_watchlist: list[dict[str, object]] = []
        snapshot: dict = {
            "cash": 0,
            "total_asset": 0,
            "holding_count": 0,
            "holding_symbols": [],
            "holding_positions": {},
            "today_trade_count": 0,
        }
        prefetched_snapshot = None
        prefetched_balance = None
        complete_payload = {
            "type": "agent_state",
            "data": {
                "market_scope": scope,
                "cycle_active": False,
                "cycle_id": cycle_id,
                "scanned_count": 0,
                "analyzed_count": 0,
                "selected_symbols": [],
            },
        }

        try:
            llm_factory.start_session(scope=scope, phase="cycle")
            session_started = True

            logger.info("=== Agent 장중 사이클 시작 ===")
            stage = "publish_cycle_start_event"
            await self._publish_event_safe(
                Event(type=EventType.AGENT_CYCLE_START, source="trading_agent"),
                scope=scope,
                stage=stage,
            )
            stage = "log_cycle_start"
            await activity_logger.log(
                ActivityType.CYCLE, ActivityPhase.START,
                "\U0001f504 장중 매매 사이클 시작",
                cycle_id=cycle_id,
            )

            if is_crypto_market(target):
                stage = "prepare_crypto_cycle_feedback"
                await self._prepare_crypto_cycle_feedback(
                    target,
                    state,
                    cycle_id=cycle_id,
                    trigger_source=trigger_source,
                    trigger_reason=trigger_reason,
                )

            state._pipeline_snapshot = {
                "cycle_id": cycle_id,
                "started_at": attempted_at.isoformat(),
                "phase": "BOOTSTRAP",
                "scanned_count": 0,
                "analyzed_count": 0,
                "selected_symbols": [],
            }
            stage = "broadcast_bootstrap_state"
            await self._broadcast_agent_state_safe(
                scope,
                {
                    "type": "agent_state",
                    "data": {
                        "market_scope": scope,
                        "cycle_active": True,
                        **state._pipeline_snapshot,
                    },
                },
                stage=stage,
            )

            stage = "prefetch_account_snapshot"
            try:
                from trading.account_manager import account_manager

                prefetched_snapshot = await account_manager.get_account_snapshot(target)
                prefetched_balance, _ = prefetched_snapshot
            except Exception as e:
                logger.warning("사이클 시작 계좌 스냅샷 사전 조회 실패: {}", str(e))

            dynamic_limits = None
            if settings.AI_RISK_TUNING_ENABLED:
                stage = "risk_tuning"
                try:
                    from strategy.ai_risk_tuner import ai_risk_tuner
                    dynamic_limits = await ai_risk_tuner.compute_limits(
                        market=target,
                        risk_appetite=settings.risk_appetite_for_market(target),
                        cycle_id=cycle_id,
                        balance=prefetched_balance,
                    )
                except Exception as e:
                    logger.warning("AI 한도 결정 실패, 기본값 사용: {}", str(e))

            # 1. 시장 스캔 + 종목 선별 (통합 1회 LLM 호출)
            stage = "market_scan"
            if is_crypto_market(target):
                from agent.crypto_scanner import crypto_scanner
                scan_result = await crypto_scanner.scan(
                    market=target,
                    cycle_id=cycle_id,
                    dynamic_limits=dynamic_limits,
                    account_snapshot=prefetched_snapshot,
                )
            else:
                scan_result = await market_scanner.scan(
                    market=target,
                    cycle_id=cycle_id,
                    dynamic_limits=dynamic_limits,
                    account_snapshot=prefetched_snapshot,
                )
            candidates = scan_result.get("selected", [])
            results["scanned"] = len(candidates)

            # 1b. 시장 국면 + 컨텍스트 빌드 (Tier1/Tier2/전략/리스크에 전달)
            if is_crypto_market(target):
                state.market_regime = normalize_crypto_regime(scan_result.get("market_regime", ""))
            else:
                state.market_regime = scan_result.get("market_regime", "")
            state.market_context = self._build_market_context(scan_result)

            # 1c. 데이트레이딩 컨텍스트 빌드 (시간/손익/매매성적)
            state.trading_context = await self._build_trading_context(target)

            if not candidates:
                logger.info("스캔 결과 선정 종목 없음, 사이클 종료")
                await activity_logger.log(
                    ActivityType.CYCLE, ActivityPhase.COMPLETE,
                    "\u2705 사이클 종료: 선정 종목 없음",
                    cycle_id=cycle_id,
                    execution_time_ms=activity_logger.elapsed_ms(cycle_timer),
                )
            else:
                # 선정 종목을 결과에 저장 (WebSocket 구독용)
                selected_watchlist = [
                    {
                        "symbol": str(c.get("symbol", "")).upper(),
                        "market": normalize_market(c.get("market", target)),
                        "name": str(c.get("name", "") or ""),
                        "price": c.get("price"),
                        "change_rate": c.get("change_rate"),
                        "volume": c.get("volume"),
                        "trade_value": c.get("trade_value"),
                        "strategy_type": c.get("strategy_type", ""),
                        "reason": c.get("reason", ""),
                        "scan_source": c.get("scan_source", ""),
                    }
                    for c in candidates if c.get("symbol")
                ]
                results["selected_symbols"] = [
                    (item["symbol"], item["market"])
                    for item in selected_watchlist
                ]

                # 파이프라인 모니터 상태 업데이트 + SSE 브로드캐스트
                from util.time_util import now_kst as _now_kst

                selected_syms = [
                    {
                        "symbol": item["symbol"],
                        "market": item["market"],
                        "name": item["name"],
                        "scan_source": item.get("scan_source", ""),
                    }
                    for item in selected_watchlist
                ]
                state._pipeline_snapshot = {
                    "cycle_id": cycle_id,
                    "started_at": _now_kst().isoformat(),
                    "phase": "ANALYSIS",
                    "scanned_count": len(candidates),
                    "analyzed_count": 0,
                    "selected_symbols": selected_syms,
                }
                stage = "broadcast_analysis_state"
                await self._broadcast_agent_state_safe(
                    scope,
                    {
                        "type": "agent_state",
                        "data": {
                            "market_scope": scope,
                            "cycle_active": True,
                            **state._pipeline_snapshot,
                        },
                    },
                    stage=stage,
                )

                # AI가 결정한 모니터링 임계값을 event_detector에 설정
                self._apply_scan_thresholds(candidates)

                # 3. 포트폴리오 스냅샷 (병렬 분석 전 공유 상태 조회, MCP 1회)
                balance_ok = False
                stage = "portfolio_snapshot"
                try:
                    if prefetched_snapshot is not None:
                        balance, holdings = prefetched_snapshot
                    else:
                        from trading.account_manager import account_manager

                        balance, holdings = await account_manager.get_account_snapshot(target)
                    if not balance.is_valid:
                        logger.error("계좌 조회 실패 → 매매 사이클 중단")
                        await activity_logger.log(
                            ActivityType.CYCLE, ActivityPhase.ERROR,
                            "\U0001f6d1 계좌 조회 실패 → 매매 사이클 중단 (데이터 신뢰성 보호)",
                            cycle_id=cycle_id,
                        )
                    else:
                        balance_ok = True
                        snapshot["cash"] = balance.effective_cash
                        snapshot["total_asset"] = balance.total_asset
                        snapshot["holding_count"] = len(holdings)
                        holding_symbols, holding_positions = self._build_holding_snapshot(
                            holdings,
                            balance.total_asset,
                        )
                        snapshot["holding_symbols"] = holding_symbols
                        snapshot["holding_positions"] = holding_positions
                        snapshot["today_trade_count"] = await self._get_today_trade_count(scope)
                        for holding in holdings:
                            self._remember_product_metadata(
                                holding.symbol,
                                holding.market,
                                {"name": holding.name},
                            )
                        # 인스턴스 레벨 현금 트래커 갱신
                        async with state.cash_lock:
                            state.available_cash = balance.effective_cash
                except Exception as e:
                    logger.warning("포트폴리오 스냅샷 조회 실패, 기본값 사용: {}", str(e))

                # 일일 기준 자산 설정 (첫 사이클에서만)
                if state.daily_start_balance == 0 and snapshot["total_asset"] > 0:
                    state.daily_start_balance = snapshot["total_asset"]

                if balance_ok:
                    stage = "analyze_candidates"
                    # 4. 후보 종목별 심층 분석 + 전략 평가 + 매매 (병렬)
                    # 세션 일시 중지 → 각 종목 분석은 독립 호출 (병렬 가능)
                    # 스크리닝 맥락은 state.market_context로 프롬프트에 전달됨
                    paused_sid = llm_factory.pause_session(scope=scope, phase="cycle")

                    semaphore = asyncio.Semaphore(3)
                    executed_count = 0

                    # 최소 주문 금액 (사전 차단용)
                    if is_crypto_market(target):
                        eff_min_qty = normalize_quantity(
                            dynamic_limits.get("min_buy_quantity", settings.CRYPTO_MIN_BUY_QUANTITY)
                            if dynamic_limits
                            else settings.CRYPTO_MIN_BUY_QUANTITY,
                            target,
                        )
                        eff_min_order_amount = max(eff_min_qty * 1000, 5000.0)
                    else:
                        eff_min_qty = normalize_quantity(
                            dynamic_limits.get("min_buy_quantity", settings.MIN_BUY_QUANTITY)
                            if dynamic_limits
                            else settings.MIN_BUY_QUANTITY,
                            target,
                        )
                        eff_min_order_amount = eff_min_qty * 1000  # 보수적 추정: 최소 수량 × 1000원

                    async def _analyze_with_limit(stock_info: dict) -> dict:
                        nonlocal executed_count
                        async with semaphore:
                            # 잔고 사전 확인 — 최소 주문금액 미달 시 스킵
                            async with state.cash_lock:
                                if state.available_cash < eff_min_order_amount:
                                    logger.info(
                                        "[{}] 현금 부족으로 스킵: {:,.0f} < {:,.0f}",
                                        stock_info.get("symbol", "?"),
                                        state.available_cash,
                                        eff_min_order_amount,
                                    )
                                    return {"skipped": True, "reason": "현금 부족"}
                                local_snapshot = {**snapshot, "cash": state.available_cash}

                            r = await self._analyze_and_trade(
                                stock_info, cycle_id,
                                dynamic_limits=dynamic_limits,
                                portfolio_snapshot=local_snapshot,
                                executed_count_ref=lambda: executed_count,
                            )
                            if r.get("executed"):
                                executed_count += 1
                                # 체결된 주문 금액만큼 잔고 차감
                                order_amount = r.get("order_amount", 0)
                                if order_amount > 0:
                                    async with state.cash_lock:
                                        state.available_cash -= order_amount
                                        logger.debug(
                                            "[{}] 주문 {:,.0f}원 차감 → 잔여 현금 {:,.0f}원",
                                            stock_info.get("symbol", "?"),
                                            order_amount,
                                            state.available_cash,
                                        )
                            return r

                    try:
                        all_results = await asyncio.gather(
                            *[_analyze_with_limit(s) for s in candidates],
                            return_exceptions=True,
                        )
                    finally:
                        if paused_sid:
                            llm_factory.resume_session(paused_sid, scope=scope, phase="cycle")

                    for i, r in enumerate(all_results):
                        if isinstance(r, Exception):
                            sym = candidates[i].get("symbol", "?")
                            logger.error("종목 분석 오류 ({}): {}", sym, str(r))
                            await activity_logger.log(
                                ActivityType.TIER1_ANALYSIS, ActivityPhase.ERROR,
                                f"\u274c [{sym}] 분석 오류: {str(r)[:100]}",
                                cycle_id=cycle_id,
                                symbol=sym,
                                error_message=str(r),
                            )
                        elif isinstance(r, dict):
                            results["analyzed"] += 1
                            if r.get("signal"):
                                results["signals"] += 1
                            if r.get("executed"):
                                results["executed"] += 1
            if settings.should_use_adaptive_rescan(target) and scheduled_budget_remaining is not None:
                stage = "generate_schedule_hint"
                schedule_hint = await self._generate_schedule_hint(
                    target,
                    results=results,
                    snapshot=snapshot,
                    scheduled_budget_remaining=scheduled_budget_remaining,
                    cycle_id=cycle_id,
                )
                results["schedule_hint"] = schedule_hint
                state.last_schedule_hint = dict(schedule_hint)

            state.last_selected_watchlist = list(selected_watchlist)
            self._last_cycle_time = now_kst()
            elapsed = activity_logger.elapsed_ms(cycle_timer)
            self._set_cycle_runtime_state(state, status="COMPLETE")
            complete_payload["data"]["scanned_count"] = results.get("scanned", 0)
            complete_payload["data"]["analyzed_count"] = results.get("analyzed", 0)

            stage = "publish_cycle_end_event"
            await self._publish_event_safe(
                Event(type=EventType.AGENT_CYCLE_END, data=results, source="trading_agent"),
                scope=scope,
                stage=stage,
            )
            stage = "log_cycle_complete"
            await activity_logger.log(
                ActivityType.CYCLE, ActivityPhase.COMPLETE,
                f"\u2705 사이클 완료: 분석 {results['analyzed']}건, "
                f"추천 {results['signals']}건, 소요 {elapsed / 1000:.1f}초",
                cycle_id=cycle_id,
                detail=results,
                execution_time_ms=elapsed,
            )

            logger.info("=== Agent 장중 사이클 종료: {} ===", results)
            return results
        except asyncio.CancelledError:
            runtime_error = f"{stage}: asyncio.CancelledError"
            self._set_cycle_runtime_state(state, status="CANCELLED", error=runtime_error)
            logger.warning("Agent 사이클 취소 ({}) @ {}", scope, stage)
            self._schedule_cycle_error_log(
                cycle_id=cycle_id,
                summary=f"\u274c 사이클 취소: {stage}",
                error_message=runtime_error,
            )
            raise
        except Exception as e:
            err_msg = str(e) or repr(e)
            runtime_error = f"{stage}: {err_msg}"
            self._set_cycle_runtime_state(state, status="ERROR", error=runtime_error)
            logger.error("Agent 사이클 오류 ({} @ {}): {}", type(e).__name__, stage, err_msg)
            await activity_logger.log(
                ActivityType.CYCLE, ActivityPhase.ERROR,
                f"\u274c 사이클 오류: [{type(e).__name__}] {stage} - {err_msg[:100]}",
                cycle_id=cycle_id,
                detail={"stage": stage, "partial_results": results},
                error_message=self._truncate_error_message(runtime_error),
            )
            raise
        finally:
            state._pipeline_snapshot = {}
            complete_payload["data"]["cycle_id"] = cycle_id
            complete_payload["data"]["scanned_count"] = results.get("scanned", 0)
            complete_payload["data"]["analyzed_count"] = results.get("analyzed", 0)
            await self._broadcast_agent_state_safe(
                scope,
                complete_payload,
                stage="broadcast_cycle_complete_state",
            )
            if session_started:
                try:
                    state.session_ids["cycle"] = llm_factory.end_session(scope=scope, phase="cycle")
                except Exception as e:
                    logger.warning("LLM 세션 종료 실패 [{}]: {}", scope, str(e))

    async def _prepare_crypto_cycle_feedback(
        self,
        market: str,
        state,
        *,
        cycle_id: str,
        trigger_source: str,
        trigger_reason: str | None,
    ) -> None:
        """코인 사이클 시작 전에 최신 정산 리포트 기준 규칙만 재적용한다."""
        if trigger_source in {"SCHEDULED_AUTO", "MANUAL_API"}:
            await activity_logger.log(
                ActivityType.REPORT,
                ActivityPhase.SKIP,
                "📘 [CRYPTO] 코인 사이클 시작 시 자동 리포트는 생성하지 않고 최신 정산 리포트 기준 규칙만 재적용합니다",
                cycle_id=cycle_id,
                detail={
                    "trigger_source": trigger_source,
                    "trigger_reason": trigger_reason,
                    "market": market,
                },
            )

        await self.refresh_runtime_trading_rules(
            market=market,
            cycle_id=cycle_id,
            emit_activity=True,
        )

    async def _run_after_hours_cycle(self, market: str | None = None) -> dict:
        """장외 사이클: 오늘 데이트레이딩 성과 리뷰 (피드백 학습용)"""
        target = normalize_market(market or settings.primary_market_code)
        scope = market_scope(target)
        state = self._get_state(scope)

        from trading.account_manager import account_manager

        trading_date = self._refresh_runtime_date(state, scope)
        async with state.after_hours_lock:
            preview = await self._preview_after_hours_cycle(
                target,
                state,
                trading_date,
                skip_running_checks=True,
            )
            if preview.get("skipped"):
                logger.info("[{}] 장마감 리뷰 스킵: {}", scope, preview["reason"])
                return preview

            llm_factory.start_session(scope=scope, phase="after_hours")

            cycle_id = activity_logger.start_cycle()
            cycle_timer = activity_logger.timer()

            logger.info("=== Agent 장 마감 리뷰 시작 ===")
            await event_bus.publish(Event(
                type=EventType.AGENT_CYCLE_START, source="trading_agent",
            ))
            await activity_logger.log(
                ActivityType.CYCLE, ActivityPhase.START,
                "\U0001f319 장 마감 리뷰 시작 — 오늘 매매 성과 분석",
                cycle_id=cycle_id,
            )

            results = {
                "mode": "AFTER_HOURS",
                "market_scope": scope,
                "trading_date": trading_date.isoformat(),
                "review_generated": False,
            }

            try:
                # 1. 오늘 시장 마감 데이터 수집 (MCP)
                market_close_data, volume_rank_data, surge_data, drop_data = await self._collect_market_close_data(target)

                # 2. 포트폴리오 현황 (데이트레이딩이면 청산 완료 상태)
                balance = await account_manager.get_balance(target)

                effective_cash = balance.effective_cash
                cash_ratio = 0.0
                if balance.total_asset > 0:
                    cash_ratio = (effective_cash / balance.total_asset) * 100

                # 3. 오늘 활동 집계
                today_date = trading_date
                activity_summary = "활동 없음"
                today_cycles = 0
                today_analyses = 0
                today_recommendations = 0
                today_orders = 0

                try:
                    async with AsyncSessionLocal() as session:
                        from repositories.agent_activity_repository import AgentActivityRepository
                        activity_repo = AgentActivityRepository(session)
                        activity_counts = await activity_repo.count_by_date(today_date, market_scope=scope)
                        activities = await activity_repo.get_by_date(today_date, limit=50, market_scope=scope)

                        today_cycles = activity_counts.get("CYCLE", 0) // 2
                        today_analyses = activity_counts.get("TIER1_ANALYSIS", 0)
                        today_recommendations = activity_counts.get("DECISION", 0)
                        today_orders = activity_counts.get("ORDER", 0)

                        if activities:
                            summary_lines = []
                            for a in activities[-20:]:
                                summary_lines.append(f"[{a.activity_type}/{a.phase}] {a.summary}")
                            activity_summary = "\n".join(summary_lines)
                except Exception as e:
                    logger.warning("활동 집계 실패: {}", str(e))

                # 4. 과거 매매 성과
                performance_summary = "매매 이력 없음"
                try:
                    from analysis.feedback.performance_tracker import PerformanceTracker
                    async with AsyncSessionLocal() as session:
                        tracker = PerformanceTracker(session)
                        stats = await tracker.get_overall_stats(market_scope=scope)
                        overall = stats.get("overall")
                        if overall and overall.total_trades > 0:
                            performance_summary = (
                                f"총 {overall.total_trades}거래, "
                                f"승률 {overall.win_rate * 100:.1f}%, "
                                f"총손익 {overall.total_pnl:+,.0f}원"
                            )
                except Exception as e:
                    logger.warning("성과 요약 실패: {}", str(e))

                # 5. 오버나이트 보유종목 현황 (스윙 모드)
                overnight_holdings_text = "없음 (당일 청산 모드)" if settings.DAY_TRADING_ONLY else "없음"
                if not settings.DAY_TRADING_ONLY:
                    try:
                        async with AsyncSessionLocal() as session:
                            from repositories.trade_result_repository import TradeResultRepository
                            from strategy.holding_policy import _calc_hold_days, _get_max_hold_days
                            repo = TradeResultRepository(session)
                            open_positions = await repo.get_all_open(market_scope=scope)
                            if open_positions:
                                lines = []
                                for tr in open_positions:
                                    hold_days = _calc_hold_days(tr)
                                    max_days = _get_max_hold_days(tr.strategy_type, settings)
                                    conf = tr.ai_confidence or 0.0
                                    target_pct = ""
                                    if tr.ai_target_price and tr.entry_price > 0:
                                        target_pct = f", 목표 도달률 {(tr.entry_price / tr.ai_target_price) * 100:.0f}%"
                                    lines.append(
                                        f"- {tr.stock_name}({tr.stock_symbol}): "
                                        f"보유 {hold_days}/{max_days}일, "
                                        f"신뢰도 {conf:.2f}, "
                                        f"전략 {tr.strategy_type}"
                                        f"{target_pct}"
                                    )
                                overnight_holdings_text = "\n".join(lines)
                    except Exception as e:
                        logger.warning("오버나이트 보유종목 조회 실패: {}", str(e))

                # 6. LLM으로 성과 리뷰
                t1_timer = activity_logger.timer()
                await activity_logger.log(
                    ActivityType.DAILY_PLAN, ActivityPhase.START,
                    "\U0001f4cb 장 마감 성과 리뷰 생성 중...",
                    cycle_id=cycle_id,
                )

                prompt = DAILY_PLAN_PROMPT.format(
                    today_date=today_date,
                    market_close_data=market_close_data,
                    volume_rank_data=volume_rank_data,
                    surge_data=surge_data,
                    drop_data=drop_data,
                    total_asset=balance.total_asset,
                    cash=effective_cash,
                    cash_ratio=cash_ratio,
                    stock_value=balance.stock_value,
                    total_pnl=balance.total_pnl,
                    total_pnl_rate=balance.total_pnl_rate,
                    today_cycles=today_cycles,
                    today_analyses=today_analyses,
                    today_recommendations=today_recommendations,
                    today_orders=today_orders,
                    activity_summary=activity_summary,
                    performance_summary=performance_summary,
                    overnight_holdings_text=overnight_holdings_text,
                )

                result_text, provider = await llm_factory.generate_tier1(
                    prompt,
                    system_prompt=DAILY_PLAN_SYSTEM,
                    scope=scope,
                    phase="after_hours",
                )
                t1_elapsed = activity_logger.elapsed_ms(t1_timer)

                parsed = self._parse_json(result_text)
                if parsed:
                    results["review_generated"] = True

                    today_review = parsed.get("today_review", "")
                    trade_eval = parsed.get("trade_evaluation", {})
                    success_patterns = parsed.get("success_patterns", [])
                    failure_patterns = parsed.get("failure_patterns", [])
                    feedback = parsed.get("feedback_for_tomorrow", {})
                    risk_alerts = parsed.get("risk_alerts", [])

                    summary_msg = "\U0001f4cb 장 마감 리뷰 완료"
                    if today_review:
                        summary_msg += f"\n\U0001f4dd 리뷰: {today_review[:150]}"
                    if trade_eval.get("total_trades"):
                        summary_msg += (
                            f"\n\U0001f4ca 매매: {trade_eval['total_trades']}건 "
                            f"(수익 {trade_eval.get('profitable_trades', 0)}건, "
                            f"손실 {trade_eval.get('loss_trades', 0)}건)"
                        )
                    if success_patterns:
                        summary_msg += f"\n\u2705 성공 패턴: {success_patterns[0][:80]}"
                    if failure_patterns:
                        summary_msg += f"\n\u274c 실패 패턴: {failure_patterns[0][:80]}"
                    if feedback.get("system_improvement"):
                        summary_msg += f"\n\U0001f527 개선: {feedback['system_improvement'][:80]}"
                    if risk_alerts:
                        summary_msg += f"\n\u26a0\ufe0f 리스크: {', '.join(risk_alerts[:3])}"

                    await activity_logger.log(
                        ActivityType.DAILY_PLAN, ActivityPhase.COMPLETE,
                        summary_msg,
                        cycle_id=cycle_id,
                        detail=parsed,
                        llm_provider=provider,
                        llm_tier="TIER1",
                        execution_time_ms=t1_elapsed,
                    )

                    # 일일 리포트 DB 저장
                    try:
                        await self._save_daily_report(
                            today_date, parsed,
                            market_scope=scope,
                            today_cycles=today_cycles,
                            today_analyses=today_analyses,
                            today_recommendations=today_recommendations,
                            today_orders=today_orders,
                        )
                        state.last_completed_review_date = today_date
                    except Exception as e:
                        logger.warning("일일 리포트 저장 실패: {}", str(e))

                    # 일일 리뷰 → 트레이딩 규칙 자동 생성 (내일 코드 레벨 강제 적용)
                    try:
                        from analysis.feedback.trading_rules import trading_rule_engine
                        rules = await trading_rule_engine.generate_rules_from_review(
                            parsed,
                            today_date,
                            market_scope=scope,
                        )
                        if rules:
                            rule_summary = ", ".join(
                                f"{r.param_name}={r.param_value}" for r in rules
                            )
                            await activity_logger.log(
                                ActivityType.TRADING_RULE, ActivityPhase.COMPLETE,
                                f"\U0001f4cb 트레이딩 규칙 {len(rules)}건 생성 (내일 자동 적용): {rule_summary}",
                                cycle_id=cycle_id,
                                detail=[{"param": r.param_name, "value": r.param_value, "reason": r.reason} for r in rules],
                            )
                    except Exception as e:
                        logger.warning("트레이딩 규칙 생성 실패: {}", str(e))
                else:
                    await activity_logger.log(
                        ActivityType.DAILY_PLAN, ActivityPhase.ERROR,
                        "\u274c 장 마감 리뷰 생성 실패 (응답 파싱 불가)",
                        cycle_id=cycle_id,
                        llm_provider=provider,
                        execution_time_ms=t1_elapsed,
                    )

            except Exception as e:
                logger.error("장외 사이클 오류: {}", str(e))
                await activity_logger.log(
                    ActivityType.CYCLE, ActivityPhase.ERROR,
                    f"\u274c 장외 사이클 오류: {str(e)[:100]}",
                    cycle_id=cycle_id,
                    error_message=str(e),
                )

            from util.time_util import now_kst
            self._last_cycle_time = now_kst()
            elapsed = activity_logger.elapsed_ms(cycle_timer)

            next_open = market_calendar.next_market_open(market=target)
            await event_bus.publish(Event(
                type=EventType.AGENT_CYCLE_END, data=results, source="trading_agent",
            ))
            await activity_logger.log(
                ActivityType.CYCLE, ActivityPhase.COMPLETE,
                f"\U0001f319 장 마감 리뷰 완료 (소요 {elapsed / 1000:.1f}초) "
                f"| 다음 장 시작: {next_open.strftime('%m/%d %H:%M')}",
                cycle_id=cycle_id,
                detail=results,
                execution_time_ms=elapsed,
            )
            llm_factory.end_session(scope=scope, phase="after_hours")
            state.session_ids["after_hours"] = None

            logger.info("=== Agent 장 마감 리뷰 종료 ===")
            return results

    async def _collect_market_close_data(self, market: str | None = None) -> tuple[str, str, str, str]:
        """오늘 시장 마감 데이터 수집 (MCP) — 장외 리뷰용

        Returns:
            (market_close_data, volume_rank_data, surge_data, drop_data)
        """
        market_close_data = "시장 데이터 조회 실패"
        volume_rank_text = "데이터 없음"
        surge_text = "데이터 없음"
        drop_text = "데이터 없음"

        try:
            # 병렬로 시장 데이터 수집
            primary_market = normalize_market(market or settings.primary_market_code)
            volume_resp, surge_resp, drop_resp = await asyncio.gather(
                mcp_client.get_volume_rank(market=primary_market),
                mcp_client.get_fluctuation_rank(market=primary_market, sort="top"),
                mcp_client.get_fluctuation_rank(market=primary_market, sort="bottom"),
                return_exceptions=True,
            )

            # 거래량 상위
            if not isinstance(volume_resp, Exception) and volume_resp.success and volume_resp.data:
                items = volume_resp.data.get("stocks", volume_resp.data.get("items", []))
                if items:
                    lines = []
                    for i, item in enumerate(items[:15], 1):
                        name = item.get("name", "")
                        symbol = item.get("symbol", item.get("code", ""))
                        price = item.get("price", item.get("current_price", ""))
                        change_rate = item.get("change_rate", "")
                        volume = item.get("volume", "")
                        unit = "원" if primary_market == "KRX" else market_currency(primary_market)
                        lines.append(f"{i}. {name}({symbol}) {price}{unit} {change_rate}% 거래량:{volume}")
                    volume_rank_text = "\n".join(lines)

            # 등락률 상위 (급등)
            if not isinstance(surge_resp, Exception) and surge_resp.success and surge_resp.data:
                items = surge_resp.data.get("stocks", surge_resp.data.get("items", []))
                if items:
                    lines = []
                    for i, item in enumerate(items[:15], 1):
                        name = item.get("name", "")
                        symbol = item.get("symbol", item.get("code", ""))
                        price = item.get("price", item.get("current_price", ""))
                        change_rate = item.get("change_rate", "")
                        unit = "원" if primary_market == "KRX" else market_currency(primary_market)
                        lines.append(f"{i}. {name}({symbol}) {price}{unit} {change_rate}%")
                    surge_text = "\n".join(lines)

            # 등락률 하위 (급락)
            if not isinstance(drop_resp, Exception) and drop_resp.success and drop_resp.data:
                items = drop_resp.data.get("stocks", drop_resp.data.get("items", []))
                if items:
                    lines = []
                    for i, item in enumerate(items[:15], 1):
                        name = item.get("name", "")
                        symbol = item.get("symbol", item.get("code", ""))
                        price = item.get("price", item.get("current_price", ""))
                        change_rate = item.get("change_rate", "")
                        unit = "원" if primary_market == "KRX" else market_currency(primary_market)
                        lines.append(f"{i}. {name}({symbol}) {price}{unit} {change_rate}%")
                    drop_text = "\n".join(lines)

            # 시장 요약은 등락률 상위/하위 데이터로 판단
            market_close_data = "거래량/등락률 상위 데이터로 오늘 시장 흐름 파악"

        except Exception as e:
            logger.warning("시장 마감 데이터 수집 실패: {}", str(e))

        return market_close_data, volume_rank_text, surge_text, drop_text

    async def _get_stock_trend_summary(self, symbol: str, name: str, market: str | None = None) -> str:
        """종목 일봉 기반 간단 추세 요약 (장 마감 후 사용)"""
        try:
            market_code = normalize_market(market or settings.primary_market_code)
            resp = await mcp_client.get_daily_price(symbol, count=20, market=market_code)
            if not resp.success or not resp.data:
                return ""

            prices = resp.data.get("prices", [])
            if len(prices) < 5:
                return ""

            # 최근 5일 종가 추출
            recent = prices[:5]
            closes = [float(p.get("close", 0)) for p in recent if float(p.get("close", 0)) > 0]
            if len(closes) < 3:
                return ""

            latest = closes[0]
            avg_5 = sum(closes) / len(closes)

            # 20일 평균
            all_closes = [float(p.get("close", 0)) for p in prices[:20] if float(p.get("close", 0)) > 0]
            avg_20 = sum(all_closes) / len(all_closes) if all_closes else latest

            # 5일 등락률
            change_5d = ((closes[0] - closes[-1]) / closes[-1] * 100) if closes[-1] > 0 else 0

            # 추세 판단
            if latest > avg_5 > avg_20:
                trend = "상승추세"
            elif latest < avg_5 < avg_20:
                trend = "하락추세"
            else:
                trend = "횡보"

            # 최근 거래량 추이
            volumes = [int(p.get("volume", 0)) for p in recent if int(p.get("volume", 0)) > 0]
            vol_text = ""
            if len(volumes) >= 3:
                avg_vol = sum(volumes) / len(volumes)
                if volumes[0] > avg_vol * 1.5:
                    vol_text = ", 거래량 급증"
                elif volumes[0] < avg_vol * 0.5:
                    vol_text = ", 거래량 감소"

            return (
                f"- {name}({symbol}): {trend} | "
                f"종가 {latest:,.2f}{'원' if market_currency(market_code) == 'KRW' else market_currency(market_code)} | "
                f"5일 {change_5d:+.1f}% | "
                f"5MA {avg_5:,.2f} / 20MA {avg_20:,.2f}{vol_text}"
            )
        except Exception as e:
            logger.debug("종목 추세 요약 실패 ({}): {}", symbol, str(e))
            return ""

    @staticmethod
    def _normalize_schedule_confidence(value: object, default: float = 0.65) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            numeric = default
        return max(0.0, min(1.0, numeric))

    @staticmethod
    def _minutes_until_market_buy_cutoff(market: str) -> int | None:
        from util.time_util import now_kst
        from zoneinfo import ZoneInfo

        target = normalize_market(market)
        if not settings.market_has_buy_cutoff(target):
            return None
        now = now_kst().astimezone(ZoneInfo(market_timezone(target)))
        mkt_cfg = settings.get_market_config(target)
        buy_cutoff_time = now.replace(
            hour=mkt_cfg["buy_cutoff_hour"],
            minute=mkt_cfg["buy_cutoff_minute"],
            second=0,
            microsecond=0,
        )
        return max(0, int((buy_cutoff_time - now).total_seconds() / 60))

    @staticmethod
    def _bucketize_schedule_interval(minutes: int | float | None) -> int:
        allowed = settings.ai_dynamic_rescan_allowed_intervals_list
        default_interval = int(settings.AI_DYNAMIC_RESCAN_DEFAULT_INTERVAL_MINUTES or 60)
        if not allowed:
            return default_interval

        try:
            numeric = int(float(minutes or default_interval))
        except (TypeError, ValueError):
            numeric = default_interval

        return min(allowed, key=lambda value: (abs(value - numeric), value))

    def _fallback_schedule_hint(
        self,
        market: str,
        *,
        results: dict,
        scheduled_budget_remaining: int,
        reason_prefix: str = "",
    ) -> dict:
        target = normalize_market(market)
        state = self._get_state(market_scope(target))
        minutes_until_buy_cutoff = self._minutes_until_market_buy_cutoff(target)
        prefix = f"{reason_prefix} | " if reason_prefix else ""

        if scheduled_budget_remaining <= 0:
            return {
                "action": "STOP_FOR_SESSION",
                "next_run_in_minutes": None,
                "reason": f"{prefix}scheduled budget 소진".strip(),
                "confidence": 0.95,
                "source": "fallback",
            }

        if (
            settings.DAY_TRADING_ONLY
            and minutes_until_buy_cutoff is not None
            and minutes_until_buy_cutoff <= 0
        ):
            return {
                "action": "STOP_FOR_SESSION",
                "next_run_in_minutes": None,
                "reason": f"{prefix}매수 마감 경과".strip(),
                "confidence": 0.95,
                "source": "fallback",
            }

        if not market_calendar.is_trading_hours(target):
            return {
                "action": "STOP_FOR_SESSION",
                "next_run_in_minutes": None,
                "reason": f"{prefix}장중 세션 종료".strip(),
                "confidence": 0.95,
                "source": "fallback",
            }

        if results.get("executed", 0) > 0 or results.get("signals", 0) > 0:
            interval = 45
            reason = "유효 신호/체결 발생 → 후속 확인 우선"
        elif state.market_regime in (
            ("THEME", "BULL_RUN", "ALTSEASON")
            if is_crypto_market(target)
            else ("THEME", "BULL")
        ) and (
            minutes_until_buy_cutoff is None or minutes_until_buy_cutoff > 60
        ):
            interval = 30
            reason = f"{state.market_regime} 국면 지속 → 짧은 후속 확인"
        elif results.get("scanned", 0) == 0 or results.get("analyzed", 0) == 0:
            interval = 90
            reason = "후보/분석 부족 → 더 긴 관찰 간격"
        else:
            interval = int(settings.AI_DYNAMIC_RESCAN_DEFAULT_INTERVAL_MINUTES or 60)
            reason = "중립 상태 → 기본 간격 유지"

        interval = self._bucketize_schedule_interval(interval)
        return {
            "action": "SCHEDULE_NEXT",
            "next_run_in_minutes": interval,
            "reason": f"{prefix}{reason}".strip(),
            "confidence": 0.65,
            "source": "fallback",
        }

    async def _generate_schedule_hint(
        self,
        market: str,
        *,
        results: dict,
        snapshot: dict | None,
        scheduled_budget_remaining: int,
        cycle_id: str | None = None,
    ) -> dict:
        from util.time_util import now_kst
        from zoneinfo import ZoneInfo

        target = normalize_market(market)
        scope = market_scope(target)
        state = self._get_state(scope)
        snap = snapshot or {}
        remaining_budget = max(0, int(scheduled_budget_remaining))
        minutes_until_buy_cutoff = self._minutes_until_market_buy_cutoff(target)
        allowed_intervals = settings.ai_dynamic_rescan_allowed_intervals_list
        fallback = self._fallback_schedule_hint(
            target,
            results=results,
            scheduled_budget_remaining=remaining_budget,
        )

        if fallback["action"] == "STOP_FOR_SESSION":
            return fallback

        market_now = now_kst().astimezone(ZoneInfo(market_timezone(target)))
        available_cash = float(snap.get("cash", state.available_cash) or 0.0)
        today_trade_count = int(snap.get("today_trade_count") or 0)
        total_asset = float(snap.get("total_asset") or 0.0)
        daily_pnl_pct = 0.0
        if state.daily_start_balance > 0 and total_asset > 0:
            daily_pnl_pct = (
                (total_asset - state.daily_start_balance)
                / state.daily_start_balance * 100
            )

        prompt = SCHEDULE_HINT_PROMPT.format(
            market=target,
            market_session=market_calendar.get_market_session(dt=market_now, market=target),
            market_regime=state.market_regime or "UNKNOWN",
            local_time=market_now.strftime("%H:%M"),
            minutes_until_buy_cutoff=(
                minutes_until_buy_cutoff if minutes_until_buy_cutoff is not None else "N/A"
            ),
            scanned=int(results.get("scanned", 0) or 0),
            analyzed=int(results.get("analyzed", 0) or 0),
            signals=int(results.get("signals", 0) or 0),
            executed=int(results.get("executed", 0) or 0),
            available_cash=available_cash,
            today_trade_count=today_trade_count,
            daily_pnl_pct=daily_pnl_pct,
            remaining_scheduled_budget=remaining_budget,
            allowed_intervals=", ".join(str(v) for v in allowed_intervals),
            market_context=state.market_context or "시장 컨텍스트 없음",
            trading_context=state.trading_context or "트레이딩 컨텍스트 없음",
        )

        try:
            result_text, _provider = await llm_factory.generate_tier1(
                prompt,
                system_prompt=SCHEDULE_HINT_SYSTEM,
                profile=Tier1Profile.ANALYSIS,
                scope=scope,
                phase="cycle",
                cycle_id=cycle_id,
            )
            parsed = self._parse_json(result_text)
            if not parsed:
                return self._fallback_schedule_hint(
                    target,
                    results=results,
                    scheduled_budget_remaining=remaining_budget,
                    reason_prefix="AI schedule_hint 파싱 실패",
                )

            action = str(parsed.get("action") or "").strip().upper()
            reason = str(parsed.get("reason") or "").strip() or "AI 스케줄 판단"
            confidence = self._normalize_schedule_confidence(parsed.get("confidence"))
            if action == "STOP_FOR_SESSION":
                return {
                    "action": "STOP_FOR_SESSION",
                    "next_run_in_minutes": None,
                    "reason": reason,
                    "confidence": confidence,
                    "source": "ai",
                }
            if action != "SCHEDULE_NEXT":
                return self._fallback_schedule_hint(
                    target,
                    results=results,
                    scheduled_budget_remaining=remaining_budget,
                    reason_prefix=f"AI action 무효: {action or 'empty'}",
                )

            interval = self._bucketize_schedule_interval(parsed.get("next_run_in_minutes"))
            return {
                "action": "SCHEDULE_NEXT",
                "next_run_in_minutes": interval,
                "reason": reason,
                "confidence": confidence,
                "source": "ai",
            }
        except Exception as e:
            return self._fallback_schedule_hint(
                target,
                results=results,
                scheduled_budget_remaining=remaining_budget,
                reason_prefix=f"AI schedule_hint 실패: {type(e).__name__}",
            )
