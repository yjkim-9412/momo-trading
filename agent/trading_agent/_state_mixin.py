"""StateMixin: __init__, 상태 관리, 런타임 접근"""
import asyncio
from datetime import date, datetime

from loguru import logger

from core.config import settings
from core.database import AsyncSessionLocal
from core.events import Event, event_bus
from scheduler.market_calendar import market_calendar
from services.activity_logger import activity_logger
from trading.enums import ActivityPhase, ActivityType
from trading.market_profile import (
    MARKET_SCOPE_CRYPTO,
    market_scope,
    normalize_market,
    normalize_market_scope,
)
from trading.mcp_client import mcp_client

from admin.sse_manager import sse_manager
from agent.trading_agent._types import MarketState, _default_strategies


class StateMixin:
    """인스턴스 속성 소유 + 상태 관리 Mixin (MRO 최우선)"""

    def __init__(self):
        self.strategies = _default_strategies()
        self._running = False
        self._last_cycle_time = None
        # 실시간 이벤트 중복 분석 방지 (종목별 쿨다운)
        self._analyzing: set[str] = set()
        self._cooldowns: dict[str, float] = {}  # symbol -> last_trigger_time
        self.EVENT_COOLDOWN_SEC = 120  # 동일 종목 재분석 최소 간격 (초)
        # 시장별 격리 상태 (KRX/US 동시 운영 지원)
        self._market_states: dict[str, MarketState] = {}
        # 종목별 상품 메타데이터 캐시 (실시간 이벤트 경로 재사용)
        self._product_metadata: dict[str, dict] = {}

    def _get_state(self, market: str) -> MarketState:
        """시장별 격리 상태 조회 (없으면 자동 생성)"""
        scope = normalize_market_scope(market)
        if scope not in self._market_states:
            self._market_states[scope] = MarketState(scope=scope)
        return self._market_states[scope]

    def get_runtime(self, market: str) -> MarketState:
        """외부 호출용 runtime 조회"""
        return self._get_state(market)

    def _refresh_runtime_date(self, runtime: MarketState, market: str) -> date:
        """시장 거래일 변경 시 runtime의 일일 상태를 리셋"""
        trading_date = market_calendar.market_date(market=market)
        if runtime.trading_date != trading_date:
            runtime.trading_date = trading_date
            runtime.daily_start_balance = 0.0
            runtime.available_cash = 0.0
            runtime.market_context = ""
            runtime.trading_context = ""
            runtime.market_regime = ""
            runtime.session_ids = {}
            runtime.active_trading_rules = {}
            runtime.rr_floor_overrides = {}
            runtime.strategies = _default_strategies()
            runtime.last_completed_review_date = None
            runtime.last_schedule_hint = {}
            runtime.last_selected_watchlist = []
            runtime.last_cycle_attempt_at = None
            runtime.last_cycle_status = None
            runtime.last_cycle_error = None
        return trading_date

    async def refresh_runtime_trading_rules(
        self,
        market: str | None = None,
        *,
        cycle_id: str | None = None,
        emit_activity: bool = False,
    ) -> dict:
        """현재 runtime에 활성 트레이딩 규칙을 다시 로드해 적용한다."""
        from analysis.feedback.trading_rules import trading_rule_engine

        scope = normalize_market_scope(market or settings.primary_market_code)
        runtime = self._get_state(scope)
        runtime.strategies = _default_strategies()

        active_rules = await trading_rule_engine.load_active_rules(scope)
        trading_rule_engine.apply_to_strategies(runtime.strategies, active_rules)
        runtime.active_trading_rules = active_rules
        runtime.rr_floor_overrides = active_rules.get("rr_floor_overrides", {})

        rules = list(active_rules.get("rules", []) or [])
        if rules and emit_activity:
            rule_summary = ", ".join(
                f"{rule.param_name}={rule.param_value}" for rule in rules[:5]
            )
            await activity_logger.log(
                ActivityType.TRADING_RULE,
                ActivityPhase.COMPLETE,
                f"📋 [{scope}] 트레이딩 규칙 {len(rules)}건 적용: {rule_summary}",
                cycle_id=cycle_id,
                market_scope=scope,
            )
            await trading_rule_engine.record_application(
                [str(rule.id) for rule in rules],
                market_scope=scope,
            )

        expired = await trading_rule_engine.expire_old_rules(scope)
        if expired:
            logger.info("[{}] 만료된 트레이딩 규칙 {}건 비활성화", scope, expired)
        return active_rules

    @staticmethod
    def _truncate_error_message(message: str | None, limit: int = 500) -> str | None:
        if not message:
            return None
        normalized = " ".join(str(message).split())
        return normalized[:limit]

    @staticmethod
    def _set_cycle_runtime_state(
        runtime: MarketState,
        *,
        attempted_at: datetime | None = None,
        status: str | None = None,
        error: str | None = None,
    ) -> None:
        if attempted_at is not None:
            runtime.last_cycle_attempt_at = attempted_at
        if status is not None:
            runtime.last_cycle_status = status
        runtime.last_cycle_error = StateMixin._truncate_error_message(error) if error else None

    async def _publish_event_safe(self, event: Event, *, scope: str, stage: str) -> None:
        try:
            await event_bus.publish(event)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[{}] 이벤트 발행 실패 ({}): {}", scope, stage, str(e))

    async def _broadcast_agent_state_safe(
        self,
        scope: str,
        payload: dict,
        *,
        stage: str,
    ) -> None:
        resolved_scope = normalize_market_scope(scope)
        manager = sse_manager
        if resolved_scope == MARKET_SCOPE_CRYPTO:
            try:
                from api.routes.admin_coin import coin_sse_manager

                manager = coin_sse_manager
            except ImportError:
                pass
        try:
            await manager.broadcast(payload)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(
                "[{}] agent_state 브로드캐스트 실패 ({}): {}",
                resolved_scope,
                stage,
                str(e),
            )

    def _schedule_cycle_error_log(
        self,
        *,
        cycle_id: str,
        summary: str,
        error_message: str | None = None,
    ) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        task = loop.create_task(
            activity_logger.log(
                ActivityType.CYCLE,
                ActivityPhase.ERROR,
                summary,
                cycle_id=cycle_id,
                error_message=self._truncate_error_message(error_message),
            )
        )

        def _consume_log_error(done_task: asyncio.Task) -> None:
            if done_task.cancelled():
                return
            try:
                done_task.exception()
            except Exception:
                pass

        task.add_done_callback(_consume_log_error)

    def get_cycle_runtime_snapshot(self, market: str | None = None) -> dict[str, str | None]:
        scope = normalize_market_scope(market or settings.primary_market_code)
        runtime = self._market_states.get(scope)
        if not runtime:
            return {
                "last_cycle_attempt_at": None,
                "last_cycle_status": None,
                "last_cycle_error": None,
            }

        return {
            "last_cycle_attempt_at": (
                runtime.last_cycle_attempt_at.isoformat()
                if runtime.last_cycle_attempt_at
                else None
            ),
            "last_cycle_status": runtime.last_cycle_status,
            "last_cycle_error": runtime.last_cycle_error,
        }

    @staticmethod
    def _skip_result(
        reason: str,
        scope: str,
        trading_date: date | None = None,
        *,
        mode: str | None = None,
    ) -> dict:
        result = {"skipped": True, "reason": reason, "market_scope": scope}
        if trading_date:
            result["trading_date"] = trading_date.isoformat()
        if mode:
            result["mode"] = mode
        return result

    async def _daily_report_exists(self, market_scope_code: str, trading_date: date) -> bool:
        from repositories.daily_report_repository import DailyReportRepository

        async with AsyncSessionLocal() as session:
            repo = DailyReportRepository(session)
            return await repo.get_by_date(trading_date, market_scope=market_scope_code) is not None

    async def _preview_after_hours_cycle(
        self,
        market: str,
        runtime: MarketState,
        trading_date: date,
        *,
        skip_running_checks: bool = False,
    ) -> dict:
        scope = runtime.scope

        if not skip_running_checks and runtime.after_hours_lock.locked():
            return self._skip_result(
                "after_hours_already_running",
                scope,
                trading_date,
                mode="AFTER_HOURS",
            )

        if not market_calendar.is_post_market_review_time(market):
            return self._skip_result(
                "review_window_not_open",
                scope,
                trading_date,
                mode="AFTER_HOURS",
            )

        if runtime.last_completed_review_date == trading_date:
            return self._skip_result(
                "already_reviewed",
                scope,
                trading_date,
                mode="AFTER_HOURS",
            )

        if await self._daily_report_exists(scope, trading_date):
            runtime.last_completed_review_date = trading_date
            return self._skip_result(
                "already_reviewed",
                scope,
                trading_date,
                mode="AFTER_HOURS",
            )

        return {
            "allowed": True,
            "mode": "AFTER_HOURS",
            "market_scope": scope,
            "trading_date": trading_date.isoformat(),
        }

    @property
    def last_cycle_time(self):
        return self._last_cycle_time
