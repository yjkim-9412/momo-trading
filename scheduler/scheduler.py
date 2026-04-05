"""트레이딩 에이전트 스케줄러 — 멀티마켓 자동 운영

각 활성 시장(ENABLED_MARKETS)별로 독립된 잡 세트를 등록하여
KRX와 US 등을 동시에 자동 매매할 수 있다.

타임라인 예시 — KRX (Asia/Seoul):
  08:50  장 시작 전 준비 — 어제 리뷰 피드백 확인
  09:00  KRX 개장
  09:05  장 시작 스캔 → 종목 선정 → 실시간 모니터링 돌입
  09:00~14:30  WebSocket 실시간 이벤트 → AI 분석/매매 (이벤트 기반)
              + 1시간 간격 보유종목 안전 점검 (시간 기반 조기 청산 포함)
  장중 adaptive 재스캔  장 시작 스캔 이후 AI가 다음 시점을 one-shot 예약
  14:30  신규 매수 마감 (청산 시간 확보)
  15:10  보유종목 전량 시장가 강제 청산 (종가경매 전, 병렬 실행)
  15:30  KRX 폐장
  15:40  장 마감 성과 리뷰 (KRX 종가 기반, 피드백 학습)
  16:00  포트폴리오 정산 (KIS ↔ DB 동기화)
  16:30  일봉 데이터 보관용 수집

타임라인 예시 — US (America/New_York, US_PREMARKET_ENABLED=true):
  03:50  프리마켓 준비
  04:05  프리마켓 시작 스캔
  04:30~15:30  보유종목 점검 (1시간 간격)
  장중 adaptive 재스캔  세션 강도에 따라 다음 스캔 시점 동적 예약
  15:40  강제 청산
  16:10  장 마감 리뷰
  16:30  포트폴리오 정산
  17:00  일봉 데이터 수집

타임라인 예시 — US (America/New_York, US_PREMARKET_ENABLED=false):
  09:20  장 시작 전 준비
  09:35  장 시작 스캔

※ DAY_TRADING_ONLY=true: 당일 매수→당일 청산 필수 (오버나이트 없음)
※ DAY_TRADING_ONLY=false: 스윙 모드 — 유망 종목 오버나이트 보유 (스마트 청산)
"""
import asyncio
import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from core.config import settings
from trading.enums import ActivityPhase, ActivityType

_PREMARKET_SCALP_HOLDING_POLICY = "PREMARKET_SCALP"
_PREMARKET_SCALP_EXIT_REASON = "PREMARKET_SCALP_CUTOFF"


@dataclass(frozen=True)
class MarketScheduleProfile:
    prep_time: time
    open_scan_time: time
    resume_sessions: frozenset[str]
    session_label: str


@dataclass
class AdaptiveRescanState:
    trading_date: date | None = None
    scheduled_cycle_count_today: int = 0
    next_adaptive_run_at: datetime | None = None
    last_schedule_hint: dict = field(default_factory=dict)
    last_run_at: datetime | None = None
    last_error: str | None = None


class TradingScheduler:
    """멀티마켓 자동 운영 스케줄러"""

    _OPEN_SCAN_GRACE = timedelta(minutes=10)

    def __init__(self):
        self.scheduler = AsyncIOScheduler()  # 잡별 timezone 사용
        self._running = False
        self._adaptive_states: dict[str, AdaptiveRescanState] = {}

    async def start(self) -> None:
        if not settings.SCHEDULER_ENABLED:
            logger.info("스케줄러 비활성화 (SCHEDULER_ENABLED=false)")
            return

        if self._running:
            logger.debug("스케줄러 이미 시작됨 — 중복 시작 스킵")
            return

        self._setup_jobs()
        self.scheduler.start()
        self._running = True
        logger.info("스케줄러 시작 — 트레이딩 타임라인 활성화")

        # 서버 기동 시 현재 상태에 맞는 초기 작업 실행
        await self._on_startup()

    async def stop(self) -> None:
        if self._running:
            self.scheduler.shutdown(wait=False)
            self._running = False
            logger.info("스케줄러 중지")

    @staticmethod
    def _market_schedule_profile(market: str) -> MarketScheduleProfile:
        from trading.market_profile import is_crypto_market, is_us_market

        normalized = market
        if is_crypto_market(normalized):
            return MarketScheduleProfile(
                prep_time=time(0, 0),
                open_scan_time=time(0, 5),
                resume_sessions=frozenset({"CRYPTO_ACTIVE"}),
                session_label="CRYPTO 24H",
            )

        if is_us_market(market):
            if settings.US_PREMARKET_ENABLED:
                return MarketScheduleProfile(
                    prep_time=time(3, 50),
                    open_scan_time=time(4, 5),
                    resume_sessions=frozenset({"US_PRE", "US_REGULAR"}),
                    session_label="프리마켓",
                )
            return MarketScheduleProfile(
                prep_time=time(9, 20),
                open_scan_time=time(9, 35),
                resume_sessions=frozenset({"US_REGULAR"}),
                session_label="정규장",
            )

        return MarketScheduleProfile(
            prep_time=time(8, 50),
            open_scan_time=time(9, 5),
            resume_sessions=frozenset({"KRX_NXT", "KRX_CLOSE"}),
            session_label="장 시작",
        )

    @staticmethod
    def _market_now(market: str, dt: datetime | None = None) -> datetime:
        from trading.market_profile import is_crypto_market, market_timezone
        from util.time_util import now_kst
        from zoneinfo import ZoneInfo

        current = dt or now_kst()
        tz = ZoneInfo(market_timezone(market))
        if current.tzinfo is None:
            return current.replace(tzinfo=tz)
        return current.astimezone(tz)

    def _resolve_market_context(self, market: str) -> tuple[str, object]:
        from scheduler.market_calendar import market_calendar
        from trading.market_profile import market_scope

        scope = market_scope(market)
        trading_date = market_calendar.market_date(market=scope)
        return scope, trading_date

    @classmethod
    def _market_session(cls, market: str, dt: datetime | None = None) -> str:
        from scheduler.market_calendar import market_calendar

        market_now = cls._market_now(market, dt)
        return market_calendar.get_market_session(dt=market_now, market=market)

    @classmethod
    def _runtime_market_config(cls, market: str, dt: datetime | None = None) -> tuple[datetime, str, dict]:
        market_now = cls._market_now(market, dt)
        session = cls._market_session(market, market_now)
        return market_now, session, settings.get_market_config(market, session=session)

    @classmethod
    def _run_crosses_buy_cutoff(cls, market: str, run_at: datetime) -> bool:
        """현재 세션 정책 기준으로 다음 예약이 신규 진입 cutoff를 넘는지 반환."""
        current_session = cls._market_session(market)
        if settings.is_us_premarket_scalp_session(market, current_session):
            scalp_cfg = settings.get_market_config(market, session="US_PRE")
            scalp_cutoff = run_at.replace(
                hour=scalp_cfg["buy_cutoff_hour"],
                minute=scalp_cfg["buy_cutoff_minute"],
                second=0,
                microsecond=0,
            )
            if run_at >= scalp_cutoff:
                return True

        session = cls._market_session(market, run_at)
        if not settings.should_enforce_buy_cutoff(market, session=session):
            return False

        mkt_cfg = settings.get_market_config(market, session=session)
        cutoff = run_at.replace(
            hour=mkt_cfg["buy_cutoff_hour"],
            minute=mkt_cfg["buy_cutoff_minute"],
            second=0,
            microsecond=0,
        )
        return run_at >= cutoff

    async def _log_schedule(self, market: str, phase: ActivityPhase | str, summary: str) -> None:
        from services.activity_logger import activity_logger

        scope, trading_date = self._resolve_market_context(market)
        await activity_logger.log(
            ActivityType.SCHEDULE,
            phase,
            summary,
            market_scope=scope,
            trading_date=trading_date,
        )

    def _startup_trading_action(self, market: str, dt: datetime | None = None) -> str | None:
        from scheduler.market_calendar import market_calendar

        if not market_calendar.is_trading_hours(market, dt):
            return None

        profile = self._market_schedule_profile(market)
        market_now = self._market_now(market, dt)
        open_scan_at = datetime.combine(
            market_now.date(),
            profile.open_scan_time,
            tzinfo=market_now.tzinfo,
        )
        if market_now < open_scan_at:
            return None

        if market_now <= open_scan_at + self._OPEN_SCAN_GRACE:
            return "startup_catchup"

        session = market_calendar.get_market_session(dt=market_now, market=market)
        if session in profile.resume_sessions:
            return "startup_resume"
        return None

    def _scan_trigger_messages(
        self,
        market: str,
        trigger_reason: str,
        market_now: datetime | None = None,
    ) -> tuple[str, str, str]:
        profile = self._market_schedule_profile(market)
        trigger_messages = {
            "scheduled_open": (
                f"{profile.session_label} 시작 스캔 — 전체 시장 분석 + 매매 시작",
                f"🔔 [{market}] {profile.session_label} 시작! 전체 시장 스캔 → AI 종목 선정 → 분석/매매 시작",
                f"✅ [{market}] {profile.session_label} 시작 완료",
            ),
            "startup_catchup": (
                f"서버 기동 후 {profile.session_label} catch-up 스캔",
                f"🟡 [{market}] 서버 기동 후 {profile.session_label} catch-up — 놓친 시작 스캔 보정",
                f"✅ [{market}] 서버 기동 후 {profile.session_label} catch-up 완료",
            ),
            "startup_resume": (
                "서버 기동 후 장중 재개 스캔",
                f"🔄 [{market}] 서버 기동 후 장중 재개 — 현재 세션 복구",
                f"✅ [{market}] 서버 기동 후 장중 재개 완료",
            ),
            "startup_recovery": (
                "서버 기동 후 adaptive recovery 스캔",
                f"🟡 [{market}] 서버 기동 후 adaptive recovery 예약 스캔 실행",
                f"✅ [{market}] 서버 기동 후 adaptive recovery 완료",
            ),
            "intraday_rescan": (
                f"장중 재스캔 시작 ({market_now.strftime('%H:%M') if market_now else '--:--'})",
                f"🔄 [{market}] 장중 재스캔 시작 ({market_now.strftime('%H:%M') if market_now else '--:--'}) — 새로운 기회 탐색",
                f"✅ [{market}] 장중 재스캔 완료",
            ),
            "adaptive_rescan": (
                f"AI 동적 재스캔 시작 ({market_now.strftime('%H:%M') if market_now else '--:--'})",
                f"🤖 [{market}] AI 동적 재스캔 시작 ({market_now.strftime('%H:%M') if market_now else '--:--'})",
                f"✅ [{market}] AI 동적 재스캔 완료",
            ),
        }
        return trigger_messages[trigger_reason]

    @staticmethod
    def _holdings_check_hours(market: str) -> str:
        from trading.market_profile import is_us_market

        if is_us_market(market):
            return "4-15" if settings.US_PREMARKET_ENABLED else "10-15"
        return "9-14"

    @staticmethod
    def _adaptive_rescan_job_id(market: str) -> str:
        from trading.market_profile import normalize_market

        return f"adaptive_rescan_{normalize_market(market)}"

    @staticmethod
    def _adaptive_trigger_uses_budget(trigger_reason: str) -> bool:
        return trigger_reason in {"adaptive_rescan", "startup_recovery"}

    @staticmethod
    def _adaptive_trigger_can_reschedule(trigger_reason: str) -> bool:
        return trigger_reason in {
            "scheduled_open",
            "startup_catchup",
            "adaptive_rescan",
            "startup_recovery",
        }

    def _adaptive_state(self, market: str) -> AdaptiveRescanState:
        scope, trading_date = self._resolve_market_context(market)
        state = self._adaptive_states.setdefault(
            scope,
            AdaptiveRescanState(trading_date=trading_date),
        )
        if state.trading_date != trading_date:
            state.trading_date = trading_date
            state.scheduled_cycle_count_today = 0
            state.next_adaptive_run_at = None
            state.last_schedule_hint = {}
            state.last_run_at = None
            state.last_error = None
        return state

    def _reset_adaptive_state(self, market: str) -> None:
        state = self._adaptive_state(market)
        state.scheduled_cycle_count_today = 0
        state.next_adaptive_run_at = None
        state.last_schedule_hint = {}
        state.last_run_at = None
        state.last_error = None

    async def _restore_adaptive_count(self, market: str) -> None:
        """서버 기동 시 당일 완료된 adaptive 사이클 수를 DB에서 복원"""
        from scheduler.market_calendar import market_calendar
        from trading.market_profile import market_scope

        scope = market_scope(market)
        trading_date = market_calendar.market_date(market=scope)
        state = self._adaptive_state(market)

        try:
            from core.database import AsyncSessionLocal
            from models.agent_activity import AgentActivityLog
            from sqlalchemy import func, or_, select as sa_select

            async with AsyncSessionLocal() as session:
                count = await session.scalar(
                    sa_select(func.count(AgentActivityLog.id)).where(
                        AgentActivityLog.market_scope == scope,
                        AgentActivityLog.trading_date == trading_date,
                        AgentActivityLog.activity_type == "SCHEDULE",
                        or_(
                            AgentActivityLog.summary.like(f"✅ [{scope}]%재스캔 완료%"),
                            AgentActivityLog.summary.like(f"✅ [{scope}]%adaptive recovery 완료%"),
                        ),
                    )
                )
                restored = count or 0
                state.scheduled_cycle_count_today = restored
                if restored > 0:
                    logger.info(
                        "[{}] adaptive 재스캔 횟수 복원: {}/{}",
                        market,
                        restored,
                        settings.AI_DYNAMIC_RESCAN_MAX_CYCLES_PER_SESSION,
                    )
        except Exception as e:
            logger.warning("[{}] adaptive 재스캔 횟수 복원 실패: {}", market, str(e))

    def _remaining_scheduled_budget(
        self,
        market: str,
        *,
        include_current_run: bool = False,
        trigger_reason: str | None = None,
    ) -> int:
        state = self._adaptive_state(market)
        remaining = settings.AI_DYNAMIC_RESCAN_MAX_CYCLES_PER_SESSION - state.scheduled_cycle_count_today
        if include_current_run and trigger_reason and self._adaptive_trigger_uses_budget(trigger_reason):
            remaining -= 1
        return max(0, remaining)

    @staticmethod
    def _clamp_adaptive_interval(minutes: int | float | None) -> int:
        allowed = settings.ai_dynamic_rescan_allowed_intervals_list
        default_interval = int(settings.AI_DYNAMIC_RESCAN_DEFAULT_INTERVAL_MINUTES or 60)
        if not allowed:
            return default_interval
        try:
            numeric = int(float(minutes or default_interval))
        except (TypeError, ValueError):
            numeric = default_interval
        return min(allowed, key=lambda value: (abs(value - numeric), value))

    def _clear_adaptive_rescan_job(self, market: str) -> None:
        state = self._adaptive_state(market)
        job_id = self._adaptive_rescan_job_id(market)
        existing = self.scheduler.get_job(job_id)
        if existing:
            self.scheduler.remove_job(job_id)
        state.next_adaptive_run_at = None

    def _record_completed_adaptive_cycle(self, market: str, trigger_reason: str) -> None:
        if not self._adaptive_trigger_uses_budget(trigger_reason):
            return
        state = self._adaptive_state(market)
        state.scheduled_cycle_count_today += 1

    async def _schedule_startup_recovery(self, market: str, startup_action: str) -> None:
        if not settings.AI_DYNAMIC_RESCAN_ENABLED:
            return
        if self._remaining_scheduled_budget(market) <= 0:
            return

        delay = self._clamp_adaptive_interval(settings.AI_DYNAMIC_RESCAN_MIN_INTERVAL_MINUTES)
        run_at = self._market_now(market) + timedelta(minutes=delay)
        if self._run_crosses_buy_cutoff(market, run_at):
            return
        state = self._adaptive_state(market)
        self._clear_adaptive_rescan_job(market)
        self.scheduler.add_job(
            self._adaptive_rescan,
            "date",
            run_date=run_at,
            args=[market, "startup_recovery"],
            id=self._adaptive_rescan_job_id(market),
            name=f"Adaptive recovery 재스캔 ({market})",
            misfire_grace_time=600,
        )
        state.next_adaptive_run_at = run_at
        state.last_schedule_hint = {
            "action": "SCHEDULE_NEXT",
            "next_run_in_minutes": delay,
            "reason": f"{startup_action} → recovery one-shot 예약",
            "confidence": 1.0,
            "source": "fallback",
        }
        await self._log_schedule(
            market,
            ActivityPhase.PROGRESS,
            f"⏰ [{market}] 서버 기동 recovery 예약: {delay}분 뒤 ({run_at.strftime('%H:%M')})",
        )

    async def _schedule_next_adaptive_rescan(self, market: str, cycle_result: dict) -> None:
        if not settings.AI_DYNAMIC_RESCAN_ENABLED:
            return

        state = self._adaptive_state(market)
        remaining = self._remaining_scheduled_budget(market)
        if remaining <= 0:
            self._clear_adaptive_rescan_job(market)
            await self._log_schedule(
                market,
                ActivityPhase.PROGRESS,
                f"🛑 [{market}] scheduled 재스캔 예산 소진 — 추가 예약 중단",
            )
            return

        schedule_hint = dict(cycle_result.get("schedule_hint") or {})
        state.last_schedule_hint = schedule_hint
        if schedule_hint.get("action") != "SCHEDULE_NEXT":
            self._clear_adaptive_rescan_job(market)
            await self._log_schedule(
                market,
                ActivityPhase.PROGRESS,
                f"🛑 [{market}] AI가 세션 종료 판단 — {schedule_hint.get('reason', '사유 없음')}",
            )
            return

        delay = self._clamp_adaptive_interval(schedule_hint.get("next_run_in_minutes"))
        run_at = self._market_now(market) + timedelta(minutes=delay)

        if self._run_crosses_buy_cutoff(market, run_at):
            self._clear_adaptive_rescan_job(market)
            await self._log_schedule(
                market,
                ActivityPhase.PROGRESS,
                f"🛑 [{market}] 다음 재스캔이 매수 마감 이후여서 예약 중단",
            )
            return

        self._clear_adaptive_rescan_job(market)
        self.scheduler.add_job(
            self._adaptive_rescan,
            "date",
            run_date=run_at,
            args=[market],
            id=self._adaptive_rescan_job_id(market),
            name=f"AI 동적 재스캔 ({market})",
            misfire_grace_time=600,
        )
        state.next_adaptive_run_at = run_at
        await self._log_schedule(
            market,
            ActivityPhase.PROGRESS,
            f"⏰ [{market}] 다음 AI 재스캔 {delay}분 뒤 ({run_at.strftime('%H:%M')}) "
            f"| 남은 예산 {remaining}회 | {schedule_hint.get('source', 'unknown')} "
            f"| {schedule_hint.get('reason', '사유 없음')}",
        )

    def _setup_jobs(self) -> None:
        from scheduler.jobs.portfolio_sync_job import portfolio_sync_job
        from scheduler.jobs.market_data_job import market_data_job
        from trading.market_profile import is_crypto_market, is_us_market, market_timezone, normalize_market
        from zoneinfo import ZoneInfo

        for market_group in settings.enabled_market_groups:
            market = normalize_market(market_group)
            tz = ZoneInfo(market_timezone(market))
            label = market  # 잡 ID 서픽스

            # ── CRYPTO 마켓: 24/7 전용 잡 세트 ──
            if is_crypto_market(market):
                # 고정 간격 스캔 (기본 4시간)
                self.scheduler.add_job(
                    self._market_open_scan,
                    "interval",
                    args=[market],
                    hours=settings.CRYPTO_SCAN_INTERVAL_HOURS,
                    timezone=tz,
                    id=f"crypto_scan_{label}",
                    name=f"크립토 정기 스캔 ({label})",
                    misfire_grace_time=600,
                )

                # 보유종목 점검 (기본 2시간)
                self.scheduler.add_job(
                    self._holdings_check,
                    "interval",
                    args=[market],
                    hours=settings.CRYPTO_HOLDINGS_CHECK_INTERVAL_HOURS,
                    timezone=tz,
                    id=f"holdings_check_{label}",
                    name=f"크립토 보유종목 점검 ({label})",
                    misfire_grace_time=300,
                )

                # 타임박스 정산 스윕 (기본 30분)
                self.scheduler.add_job(
                    self._crypto_settlement_sweep,
                    "interval",
                    args=[market],
                    minutes=30,
                    timezone=tz,
                    id=f"crypto_settlement_{label}",
                    name=f"크립토 타임박스 정산 ({label})",
                    misfire_grace_time=300,
                )
                continue

            is_us = is_us_market(market)
            mkt_cfg = settings.get_market_config(market)
            schedule = self._market_schedule_profile(market)

            # 시장별 시간 계산
            pre_h, pre_m = schedule.prep_time.hour, schedule.prep_time.minute
            open_h, open_m = schedule.open_scan_time.hour, schedule.open_scan_time.minute
            holdings_hours = self._holdings_check_hours(market)
            force_h = mkt_cfg["force_liquidation_hour"]
            force_m = mkt_cfg["force_liquidation_minute"]
            pre_scalp_force_h = settings.US_PREMARKET_SCALP_FORCE_LIQUIDATION_HOUR
            pre_scalp_force_m = settings.US_PREMARKET_SCALP_FORCE_LIQUIDATION_MINUTE
            post_h, post_m = (16, 10) if is_us else (15, 40)
            sync_h, sync_m = (16, 30) if is_us else (16, 0)
            data_h, data_m = (17, 0) if is_us else (16, 30)

            # ── 장 시작 전 준비 ──
            self.scheduler.add_job(
                self._pre_market,
                "cron",
                args=[market],
                hour=pre_h, minute=pre_m,
                day_of_week="mon-fri",
                timezone=tz,
                id=f"pre_market_{label}",
                name=f"장 시작 전 준비 ({label})",
                misfire_grace_time=600,
            )

            # ── 장 시작 스캔 ──
            self.scheduler.add_job(
                self._market_open_scan,
                "cron",
                args=[market],
                hour=open_h, minute=open_m,
                day_of_week="mon-fri",
                timezone=tz,
                id=f"market_open_scan_{label}",
                name=f"장 시작 스캔 + 매매 ({label})",
                misfire_grace_time=600,
            )

            # ── 장중 재스캔 ──
            if not settings.AI_DYNAMIC_RESCAN_ENABLED:
                self.scheduler.add_job(
                    self._intraday_rescan,
                    "cron",
                    args=[market],
                    hour="11,13", minute=0,
                    day_of_week="mon-fri",
                    timezone=tz,
                    id=f"intraday_rescan_{label}",
                    name=f"장중 재스캔 ({label})",
                    misfire_grace_time=600,
                )

            # ── 장중 보유종목 점검 ──
            self.scheduler.add_job(
                self._holdings_check,
                "cron",
                args=[market],
                minute="30",
                hour=holdings_hours,
                day_of_week="mon-fri",
                timezone=tz,
                id=f"holdings_check_{label}",
                name=f"보유종목 손절/익절 점검 ({label})",
                misfire_grace_time=300,
            )

            if is_us and settings.US_PREMARKET_ENABLED and settings.US_PREMARKET_SCALP_ENABLED:
                self.scheduler.add_job(
                    self._premarket_scalp_liquidation,
                    "cron",
                    args=[market],
                    hour=pre_scalp_force_h,
                    minute=pre_scalp_force_m,
                    day_of_week="mon-fri",
                    timezone=tz,
                    id=f"premarket_scalp_liquidation_{label}",
                    name=f"프리마켓 단타 청산 ({label})",
                    misfire_grace_time=300,
                )

            # ── 장 마감 전 청산 ──
            self.scheduler.add_job(
                self._force_liquidation,
                "cron",
                args=[market],
                hour=force_h,
                minute=force_m,
                day_of_week="mon-fri",
                timezone=tz,
                id=f"force_liquidation_{label}",
                name=f"장 마감 전 청산 ({label})",
                misfire_grace_time=300,
            )

            # ── 장 마감 리뷰 ──
            self.scheduler.add_job(
                self._post_market,
                "cron",
                args=[market],
                hour=post_h, minute=post_m,
                day_of_week="mon-fri",
                timezone=tz,
                id=f"post_market_{label}",
                name=f"장 마감 성과 리뷰 ({label})",
                misfire_grace_time=3600,
            )

            # ── 포트폴리오 정산 ──
            self.scheduler.add_job(
                portfolio_sync_job,
                "cron",
                args=[market],
                hour=sync_h, minute=sync_m,
                timezone=tz,
                id=f"portfolio_sync_{label}",
                name=f"포트폴리오 정산 ({label})",
                misfire_grace_time=3600,
            )

            # ── 일봉 데이터 수집 ──
            self.scheduler.add_job(
                market_data_job,
                "cron",
                args=[market],
                hour=data_h, minute=data_m,
                timezone=tz,
                id=f"market_data_{label}",
                name=f"일봉 데이터 수집 ({label})",
                misfire_grace_time=3600,
            )

        # ── 만료 추천 정리 (시장 무관, 1개만) ──
        self.scheduler.add_job(
            self._expire_recommendations,
            "interval",
            hours=1,
            id="expire_recommendations",
            name="만료 추천 처리",
        )

    # ─────────── 스케줄 작업 구현 ───────────

    async def _on_startup(self) -> None:
        """서버 기동 시 즉시 매매사이클은 막고 장외 리뷰만 체크"""
        import asyncio
        from agent.decision_maker import decision_maker
        from scheduler.market_calendar import market_calendar
        from trading.market_profile import normalize_market

        # 기동 직후 약간의 딜레이 (하위 시스템 연결 안정화)
        await asyncio.sleep(3)

        for market_group in settings.enabled_market_groups:
            market = normalize_market(market_group)
            try:
                repaired = await decision_maker.repair_recent_broker_orders(
                    market_scope=market,
                    trading_date=market_calendar.market_date(market=market),
                )
                if repaired:
                    await self._log_schedule(
                        market,
                        ActivityPhase.PROGRESS,
                        f"🧾 [{market}] 누락 broker ledger {repaired}건 복구",
                    )
            except Exception as e:
                logger.warning("[{}] broker ledger startup 복구 실패: {}", market, str(e))
            try:
                repaired_open = await decision_maker.repair_stale_open_trade_results(market_scope=market)
                if repaired_open:
                    await self._log_schedule(
                        market,
                        ActivityPhase.PROGRESS,
                        f"🧹 [{market}] stale open trade_result {repaired_open}건 복구",
                    )
            except Exception as e:
                logger.warning("[{}] stale open startup 복구 실패: {}", market, str(e))
            await self._seed_startup_holdings_watchlist(market)
            restored = await self._restore_open_position_thresholds(market)
            if restored:
                logger.info("[{}] 서버 기동 open position 임계값 복원: {}건", market, restored)
            if self._is_premarket_scalp_liquidation_due(market):
                await self._premarket_scalp_liquidation(market, startup_recovery=True)
            await self._restore_adaptive_count(market)
            startup_action = self._startup_trading_action(market)
            if settings.AI_DYNAMIC_RESCAN_ENABLED and startup_action:
                logger.info(
                    "서버 기동: {} {} → 즉시 매매 대신 recovery one-shot 예약",
                    market,
                    startup_action,
                )
                await self._schedule_startup_recovery(market, startup_action)
                continue

            if market_calendar.is_trading_hours(market):
                session = market_calendar.get_market_session(market=market)
                logger.info(
                    "서버 기동: {} {} 세션 — startup 매매사이클 비활성, 다음 정시 스캔 대기",
                    market,
                    session,
                )
                continue

            next_open = market_calendar.next_market_open(market=market)
            logger.info(
                "서버 기동: {} 장외 → 다음 장 시작: {}",
                market, next_open.strftime("%m/%d %H:%M"),
            )

        asyncio.create_task(self._post_market_if_needed())

    async def _seed_startup_holdings_watchlist(self, market: str) -> None:
        """서버 기동 직후 보유종목은 즉시 desired watchlist에 복구"""
        try:
            from services.watchlist_sync import reconcile_market_watchlist

            symbols = await reconcile_market_watchlist(market)
            if symbols:
                logger.info("[{}] 서버 기동 보유종목 감시 복원: {}종목", market, len(symbols))
        except Exception as e:
            logger.warning("[{}] 서버 기동 보유종목 감시 복원 실패: {}", market, str(e))

    @staticmethod
    def _parse_trade_notes(notes: str | None) -> dict:
        """TradeResult.notes JSON을 안전하게 파싱"""
        if not notes or not isinstance(notes, str):
            return {}

        try:
            parsed = json.loads(notes)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @classmethod
    def _is_premarket_scalp_trade(cls, trade_result) -> bool:
        """프리마켓 단타 태그 포지션 여부."""
        notes = cls._parse_trade_notes(getattr(trade_result, "notes", None))
        return str(notes.get("holding_policy") or "").upper() == _PREMARKET_SCALP_HOLDING_POLICY

    @staticmethod
    def _premarket_scalp_key(symbol: str | None, market: str | None) -> tuple[str, str]:
        return str(market or "").upper().strip(), str(symbol or "").upper().strip()

    @classmethod
    def _is_premarket_scalp_liquidation_due(cls, market: str, dt: datetime | None = None) -> bool:
        """프리마켓 단타 포지션 강제 청산 시각 도달 여부."""
        from trading.market_profile import is_us_market, normalize_market

        market_code = normalize_market(market)
        if not (
            is_us_market(market_code)
            and settings.US_PREMARKET_ENABLED
            and settings.US_PREMARKET_SCALP_ENABLED
        ):
            return False

        market_now = cls._market_now(market_code, dt)
        session = cls._market_session(market_code, market_now)
        if session not in {"US_PRE", "US_REGULAR"}:
            return False

        cutoff = market_now.replace(
            hour=settings.US_PREMARKET_SCALP_FORCE_LIQUIDATION_HOUR,
            minute=settings.US_PREMARKET_SCALP_FORCE_LIQUIDATION_MINUTE,
            second=0,
            microsecond=0,
        )
        return market_now >= cutoff

    @staticmethod
    def _build_premarket_scalp_sell_context(trade_result, holding) -> dict[str, object]:
        """프리마켓 단타 청산용 최소 분석 컨텍스트."""
        return {
            "stock_name": str(getattr(holding, "name", "") or getattr(trade_result, "stock_name", "")),
            "strategy_type": str(getattr(trade_result, "strategy_type", "") or "STABLE_SHORT"),
            "market_regime": str(getattr(trade_result, "market_regime", "") or ""),
            "ai_confidence": float(getattr(trade_result, "ai_confidence", 0.0) or 0.0),
            "ai_target_price": getattr(trade_result, "ai_target_price", None),
            "ai_stop_loss_price": getattr(trade_result, "ai_stop_loss_price", None),
            "ai_take_profit_price": getattr(trade_result, "ai_take_profit_price", None),
            "analysis_source": "PREMARKET_SCALP_LIQUIDATION",
            "event_type": _PREMARKET_SCALP_EXIT_REASON,
            "currency": str(getattr(trade_result, "currency", "") or getattr(holding, "currency", "") or "USD"),
            "exchange_rate_to_krw": float(
                getattr(trade_result, "exchange_rate_to_krw", 0.0)
                or getattr(holding, "exchange_rate_to_krw", 0.0)
                or 1.0
            ),
        }

    @staticmethod
    def _coerce_int(value, *, default: int = 0, minimum: int = 0) -> int:
        try:
            normalized = int(float(value))
        except (TypeError, ValueError):
            normalized = default
        return max(minimum, normalized)

    @staticmethod
    def _coerce_float(value, *, default: float = 0.0, minimum: float | None = None) -> float:
        try:
            normalized = float(value)
        except (TypeError, ValueError):
            normalized = default
        if minimum is not None:
            normalized = max(minimum, normalized)
        return normalized

    @classmethod
    def _resolve_open_position_hold_plan(cls, trade_result) -> dict[str, int | str | None]:
        notes = cls._parse_trade_notes(getattr(trade_result, "notes", None))
        planned_hold_days = cls._coerce_int(notes.get("planned_hold_days"), default=1, minimum=1)
        close_review_count = cls._coerce_int(notes.get("close_review_count"), default=0, minimum=0)
        last_close_review_date = notes.get("last_close_review_date")
        if last_close_review_date in ("", None):
            last_close_review_date = None
        return {
            "notes": notes,
            "planned_hold_days": planned_hold_days,
            "close_review_count": close_review_count,
            "last_close_review_date": last_close_review_date,
        }

    @classmethod
    def _apply_close_review_hold_update(
        cls,
        trade_result,
        review: dict,
        *,
        review_date: str,
    ) -> dict:
        hold_plan = cls._resolve_open_position_hold_plan(trade_result)
        notes = dict(hold_plan["notes"])
        close_review_count = int(hold_plan["close_review_count"])
        if hold_plan["last_close_review_date"] != review_date:
            close_review_count += 1

        planned_hold_days = cls._coerce_int(
            review.get("planned_hold_days"),
            default=close_review_count,
            minimum=close_review_count,
        )
        trailing_stop_pct = float(review.get("trailing_stop_pct") or 0.0)

        trade_result.ai_confidence = float(review.get("confidence") or trade_result.ai_confidence or 0.0)
        trade_result.ai_stop_loss_price = float(review.get("stop_loss_price") or 0.0)
        trade_result.ai_take_profit_price = float(review.get("take_profit_price") or 0.0)
        trade_result.ai_target_price = trade_result.ai_take_profit_price

        notes["planned_hold_days"] = planned_hold_days
        notes["close_review_count"] = close_review_count
        notes["last_close_review_date"] = review_date
        notes["trailing_stop_pct"] = trailing_stop_pct
        trade_result.notes = json.dumps(notes, ensure_ascii=False, default=str)
        return notes

    @classmethod
    def _build_existing_close_review_payload(cls, trade_result, hold_plan: dict) -> dict[str, float | int]:
        """AI 재리뷰 없이 기존 보유 계획을 1회 연장할 때 사용할 payload."""
        return {
            "planned_hold_days": hold_plan["planned_hold_days"],
            "confidence": getattr(trade_result, "ai_confidence", 0.0),
            "stop_loss_price": getattr(trade_result, "ai_stop_loss_price", 0.0),
            "take_profit_price": (
                getattr(trade_result, "ai_take_profit_price", None)
                or getattr(trade_result, "ai_target_price", 0.0)
            ),
            "trailing_stop_pct": hold_plan["notes"].get("trailing_stop_pct") or 0.0,
        }

    @classmethod
    def _should_run_close_review_llm(
        cls,
        *,
        holding,
        trade_result,
        current_price: float,
        hold_plan: dict,
    ) -> tuple[bool, str]:
        """리스크가 높거나 기존 계획을 벗어날 때만 장마감 Tier2 재리뷰를 수행한다."""
        planned_hold_days = int(hold_plan["planned_hold_days"])
        next_review_count = int(hold_plan["close_review_count"]) + 1
        if next_review_count >= planned_hold_days:
            return True, "planned_hold_days 경계 재검토"

        confidence = cls._coerce_float(getattr(trade_result, "ai_confidence", None), default=0.0)
        if confidence < 0.70:
            return True, "low_confidence"

        pnl_rate = cls._coerce_float(getattr(holding, "pnl_rate", None), default=0.0)
        if pnl_rate <= -4.0 or pnl_rate >= 7.0:
            return True, "손익률 변동 확대"

        stop_loss_price = cls._coerce_float(
            getattr(trade_result, "ai_stop_loss_price", None),
            default=0.0,
            minimum=0.0,
        )
        if stop_loss_price > 0:
            if current_price <= stop_loss_price:
                return True, "stop_loss 근접/하향"
            distance_to_stop = abs(current_price - stop_loss_price) / current_price * 100
            if distance_to_stop <= 1.0:
                return True, "stop_loss 근접/하향"

        take_profit_price = cls._coerce_float(
            getattr(trade_result, "ai_take_profit_price", None)
            or getattr(trade_result, "ai_target_price", None),
            default=0.0,
            minimum=0.0,
        )
        if take_profit_price > 0:
            if current_price >= take_profit_price:
                return True, "take_profit 도달/근접"
            distance_to_take_profit = abs(take_profit_price - current_price) / current_price * 100
            if distance_to_take_profit <= 1.5:
                return True, "take_profit 도달/근접"

        return False, "기존 보유 계획 유지"

    @classmethod
    def _build_open_position_threshold_kwargs(cls, trade_result) -> dict[str, float]:
        """미청산 포지션에서 복원할 TP/SL/트레일링 값을 추출"""
        kwargs: dict[str, float] = {}
        stop_loss_price = getattr(trade_result, "ai_stop_loss_price", None)
        take_profit_price = (
            getattr(trade_result, "ai_take_profit_price", None)
            or getattr(trade_result, "ai_target_price", None)
        )
        notes = cls._parse_trade_notes(getattr(trade_result, "notes", None))
        trailing_stop_pct = notes.get("trailing_stop_pct")

        try:
            if stop_loss_price and float(stop_loss_price) > 0:
                kwargs["stop_loss"] = float(stop_loss_price)
        except (TypeError, ValueError):
            pass

        try:
            if take_profit_price and float(take_profit_price) > 0:
                kwargs["take_profit"] = float(take_profit_price)
        except (TypeError, ValueError):
            pass

        try:
            if trailing_stop_pct and float(trailing_stop_pct) > 0:
                kwargs["trailing_stop_pct"] = float(trailing_stop_pct)
        except (TypeError, ValueError):
            pass

        return kwargs

    async def _restore_open_position_thresholds(
        self,
        market: str,
        open_positions: list | None = None,
    ) -> int:
        """DB의 미청산 포지션 기준으로 event_detector 임계값 복원"""
        from core.database import AsyncSessionLocal
        from realtime.event_detector import event_detector
        from repositories.trade_result_repository import TradeResultRepository
        from trading.market_profile import market_scope

        positions = open_positions
        if positions is None:
            async with AsyncSessionLocal() as session:
                repo = TradeResultRepository(session)
                positions = await repo.get_all_open(market_scope=market_scope(market))

        restored = 0
        for trade_result in positions or []:
            kwargs = self._build_open_position_threshold_kwargs(trade_result)
            if not kwargs:
                continue
            event_detector.set_thresholds(
                trade_result.stock_symbol,
                market=trade_result.market,
                **kwargs,
            )
            restored += 1

        return restored

    async def _pre_market(self, market: str) -> None:
        """장 시작 전 준비 — 어제 리뷰 피드백 확인"""
        from scheduler.market_calendar import market_calendar
        from services.activity_logger import activity_logger
        from trading.market_profile import market_scope, market_timezone

        scope = market_scope(market)
        trading_date = market_calendar.market_date(market=scope)

        with activity_logger.context(market_scope=scope, trading_date=trading_date):
            if market_calendar.is_holiday(market):
                holiday_name = market_calendar.get_holiday_name(market=market) or "공휴일"
                logger.info("[{}] 오늘은 휴장일 ({}) — 장 시작 전 준비 스킵", market, holiday_name)
                await self._log_schedule(
                    market,
                    ActivityPhase.PROGRESS,
                    f"\U0001f3d6\ufe0f [{market}] 오늘은 휴장일 ({holiday_name}) — 매매 스킵",
                )
                return

            logger.info("=== [{}] 장 시작 전 준비 ===", market)
            await self._log_schedule(
                market,
                ActivityPhase.PROGRESS,
                f"\u2615 [{market}] 장 시작 전 준비 — 곧 개장",
            )

            # 1. 일일 기준 자산 설정 (데이트레이딩 손익 계산용)
            try:
                from agent.trading_agent import trading_agent
                from trading.account_manager import account_manager

                balance = await account_manager.get_balance(market)
                runtime = trading_agent.get_runtime(scope)
                runtime.daily_start_balance = balance.total_asset
                runtime.trading_date = trading_date
                logger.info("[{}] 일일 기준 자산 설정: {:,.0f}원", market, balance.total_asset)
            except Exception as e:
                logger.warning("[{}] 기준 자산 설정 실패: {}", market, str(e))

        # 2. 어제 리뷰 피드백 확인 (AI 학습용)
        try:
            from datetime import timedelta
            from zoneinfo import ZoneInfo
            from util.time_util import now_kst
            from core.database import AsyncSessionLocal
            from repositories.daily_report_repository import DailyReportRepository

            tz = ZoneInfo(market_timezone(market))
            yesterday = (now_kst().astimezone(tz) - timedelta(days=1)).date()
            async with AsyncSessionLocal() as session:
                repo = DailyReportRepository(session)
                report = await repo.get_by_date(yesterday, market_scope=scope)
                if report and report.lessons_learned:
                    await self._log_schedule(
                        market,
                        ActivityPhase.PROGRESS,
                        f"\U0001f4cb [{market}] 어제 리뷰 피드백: {report.lessons_learned[:200]}",
                    )
        except Exception as e:
            logger.debug("[{}] 어제 리뷰 로드 실패: {}", market, str(e))

        # 3. 오버나이트 포지션 점검 (스윙 모드)
        if not settings.DAY_TRADING_ONLY:
            await self._check_overnight_positions(market)

        # 4. 활성 트레이딩 규칙 로드 + 적용 (일일 리뷰 피드백 자동 학습)
        try:
            from agent.trading_agent import trading_agent

            await trading_agent.refresh_runtime_trading_rules(
                market=scope,
                emit_activity=True,
            )
        except Exception as e:
            logger.warning("[{}] 트레이딩 규칙 로드 실패: {}", market, str(e))

    async def _premarket_scalp_liquidation(
        self,
        market: str,
        *,
        startup_recovery: bool = False,
    ) -> None:
        """프리마켓 단타 태그 포지션만 정규장 전 강제 청산."""
        from agent.decision_maker import decision_maker
        from core.database import AsyncSessionLocal
        from realtime.event_detector import event_detector
        from repositories.trade_result_repository import TradeResultRepository
        from trading.account_manager import account_manager
        from trading.market_profile import market_scope as resolve_scope
        from trading.mcp_client import mcp_client as _mcp

        if not self._is_premarket_scalp_liquidation_due(market):
            return
        if not settings.is_trading_enabled_for_market(market):
            logger.info("[{}] 프리마켓 단타 청산 스킵 — trading disabled", market)
            return

        try:
            repaired = await decision_maker.repair_stale_open_trade_results(market_scope=market)
            if repaired:
                logger.warning("[{}] 프리마켓 단타 청산 전 stale open {}건 복구", market, repaired)

            async with AsyncSessionLocal() as session:
                repo = TradeResultRepository(session)
                open_positions = await repo.get_all_open(market_scope=resolve_scope(market))

            tagged_positions = [tr for tr in open_positions if self._is_premarket_scalp_trade(tr)]
            if not tagged_positions:
                return

            holdings = await account_manager.get_holdings(market)
            holding_map = {
                self._premarket_scalp_key(h.symbol, h.market): h
                for h in holdings
                if float(getattr(h, "quantity", 0) or 0) > 0
            }
            tagged_map = {
                self._premarket_scalp_key(tr.stock_symbol, tr.market): tr
                for tr in tagged_positions
            }
            sellable_pairs = [
                (holding_map[key], trade_result)
                for key, trade_result in tagged_map.items()
                if key in holding_map
            ]
            missing_pairs = [
                trade_result
                for key, trade_result in tagged_map.items()
                if key not in holding_map
            ]

            if not sellable_pairs:
                if missing_pairs:
                    logger.warning(
                        "[{}] 프리마켓 단타 태그 {}건은 DB에만 존재 — holding 없음",
                        market,
                        len(missing_pairs),
                    )
                return

            mode_label = "startup 정리" if startup_recovery else "정시 청산"
            await self._log_schedule(
                market,
                ActivityPhase.PROGRESS,
                f"⏰ [{market}] 프리마켓 단타 {mode_label} — 매도 {len(sellable_pairs)}건",
            )

            async def _sell_one(holding, trade_result):
                try:
                    resp = await _mcp.place_order(
                        symbol=holding.symbol,
                        side="SELL",
                        quantity=holding.quantity,
                        price=None,
                        market=holding.market,
                    )
                    return resp, holding, trade_result, None
                except Exception as exc:  # pragma: no cover - gather 보호용
                    return None, holding, trade_result, exc

            first_pass = await asyncio.gather(
                *[_sell_one(holding, trade_result) for holding, trade_result in sellable_pairs]
            )

            confirmations = []
            sold_count = 0
            failed_pairs: list[tuple[object, object]] = []

            for resp, holding, trade_result, error in first_pass:
                if error is not None:
                    logger.error("[{}] 프리마켓 단타 청산 오류: {}({}) — {}", market, holding.name, holding.symbol, str(error))
                    failed_pairs.append((holding, trade_result))
                    continue

                if not resp or not resp.success:
                    logger.error(
                        "[{}] 프리마켓 단타 청산 실패: {}({}) — {}",
                        market,
                        holding.name,
                        holding.symbol,
                        (resp.error if resp else "응답 없음"),
                    )
                    failed_pairs.append((holding, trade_result))
                    continue

                sold_count += 1
                event_detector.remove_levels(holding.symbol, market=holding.market)
                await self._log_schedule(
                    market,
                    ActivityPhase.PROGRESS,
                    f"🚨 [{market}] 프리마켓 단타 청산 접수: {holding.name}({holding.symbol}) {holding.quantity}주",
                )

                order_id = str((resp.data or {}).get("order_id") or "")
                if order_id:
                    confirmations.append(
                        decision_maker.confirm_and_record(
                            symbol=holding.symbol,
                            market=holding.market,
                            side="SELL",
                            order_id=order_id,
                            quantity=holding.quantity,
                            expected_price=float(
                                getattr(holding, "current_price", 0.0)
                                or getattr(holding, "avg_buy_price", 0.0)
                                or 0.0
                            ),
                            analysis_context=self._build_premarket_scalp_sell_context(trade_result, holding),
                            exit_reason=_PREMARKET_SCALP_EXIT_REASON,
                        )
                    )

            if failed_pairs:
                logger.warning("[{}] 프리마켓 단타 청산 {}건 실패 → 5초 후 재시도", market, len(failed_pairs))
                await asyncio.sleep(5)
                retry_results = await asyncio.gather(
                    *[_sell_one(holding, trade_result) for holding, trade_result in failed_pairs]
                )
                remaining_failed: list[tuple[object, object]] = []
                for resp, holding, trade_result, error in retry_results:
                    if error is not None or not resp or not resp.success:
                        remaining_failed.append((holding, trade_result))
                        logger.error(
                            "[{}] 프리마켓 단타 청산 재시도 실패: {}({}) — {}",
                            market,
                            holding.name,
                            holding.symbol,
                            str(error or (resp.error if resp else "응답 없음")),
                        )
                        continue

                    sold_count += 1
                    event_detector.remove_levels(holding.symbol, market=holding.market)
                    order_id = str((resp.data or {}).get("order_id") or "")
                    if order_id:
                        confirmations.append(
                            decision_maker.confirm_and_record(
                                symbol=holding.symbol,
                                market=holding.market,
                                side="SELL",
                                order_id=order_id,
                                quantity=holding.quantity,
                                expected_price=float(
                                    getattr(holding, "current_price", 0.0)
                                    or getattr(holding, "avg_buy_price", 0.0)
                                    or 0.0
                                ),
                                analysis_context=self._build_premarket_scalp_sell_context(trade_result, holding),
                                exit_reason=_PREMARKET_SCALP_EXIT_REASON,
                            )
                        )
                failed_pairs = remaining_failed

            if confirmations:
                confirm_results = await asyncio.gather(*confirmations, return_exceptions=True)
                for confirm_result in confirm_results:
                    if isinstance(confirm_result, BaseException):
                        logger.warning("[{}] 프리마켓 단타 체결 기록 후속 확인 실패: {}", market, str(confirm_result))

            summary = f"⏰ [{market}] 프리마켓 단타 청산 완료: {sold_count}건 매도"
            if failed_pairs:
                summary += f" | 실패 {len(failed_pairs)}건"
            if missing_pairs:
                summary += f" | holding 없음 {len(missing_pairs)}건"
            await self._log_schedule(market, ActivityPhase.PROGRESS, summary)
            await self._update_realtime_subscriptions(market)
        except Exception as e:
            logger.error("[{}] 프리마켓 단타 청산 오류: {}", market, str(e))
            await self._log_schedule(
                market,
                ActivityPhase.ERROR,
                f"❌ [{market}] 프리마켓 단타 청산 오류: {str(e)[:100]}",
            )

    async def _execute_trading_scan(
        self,
        market: str,
        *,
        trigger_reason: str,
        include_gap_check: bool,
    ) -> None:
        """시장 스캔 실행 공통부."""
        from agent.trading_agent import trading_agent
        from scheduler.market_calendar import market_calendar
        from trading.account_manager import account_manager
        from trading.mcp_client import mcp_client
        from trading.market_profile import requires_mcp_connection

        market_now = self._market_now(market)
        header, start_summary, complete_prefix = self._scan_trigger_messages(
            market,
            trigger_reason,
            market_now,
        )

        if market_calendar.is_holiday(market):
            logger.info("[{}] 휴장일 — 트레이딩 스캔 스킵", market)
            return

        if requires_mcp_connection(market) and not mcp_client.is_connected:
            logger.warning("[{}] MCP 미연결 — 트레이딩 스캔 스킵", market)
            await self._log_schedule(
                market,
                ActivityPhase.ERROR,
                f"⚠️ [{market}] MCP 미연결 — 트레이딩 스캔 스킵",
            )
            return

        logger.info("=== [{}] {} ===", market, header)
        await self._log_schedule(market, ActivityPhase.PROGRESS, start_summary)
        state = self._adaptive_state(market)
        state.last_run_at = market_now
        state.last_error = None

        try:
            if include_gap_check and not settings.DAY_TRADING_ONLY:
                await self._check_overnight_gap(market)

            scheduled_budget_remaining = None
            if settings.should_use_adaptive_rescan(market) and self._adaptive_trigger_can_reschedule(trigger_reason):
                scheduled_budget_remaining = self._remaining_scheduled_budget(
                    market,
                    include_current_run=True,
                    trigger_reason=trigger_reason,
                )

            result = await trading_agent.run_cycle(
                market=market,
                scheduled_budget_remaining=scheduled_budget_remaining,
                trigger_source="SCHEDULED_AUTO",
                trigger_reason=trigger_reason,
            )
            if result.get("skipped"):
                reason = result.get("reason", "skipped")
                if reason == "mcp_unavailable":
                    logger.warning("[{}] MCP 미연결 — 트레이딩 후속 작업 생략", market)
                    await self._log_schedule(
                        market,
                        ActivityPhase.ERROR,
                        f"⚠️ [{market}] MCP 미연결 — 트레이딩 후속 작업 생략",
                    )
                    return

                logger.info("[{}] 트레이딩 스캔 스킵: {}", market, reason)
                await self._log_schedule(
                    market,
                    ActivityPhase.PROGRESS,
                    f"⏭️ [{market}] 트레이딩 스캔 스킵 — {reason}",
                )
                return

            if settings.should_use_adaptive_rescan(market) and self._adaptive_trigger_uses_budget(trigger_reason):
                self._record_completed_adaptive_cycle(market, trigger_reason)

            from services.watchlist_sync import reconcile_market_watchlist

            desired_symbols = await reconcile_market_watchlist(market)

            await self._log_schedule(
                market,
                ActivityPhase.PROGRESS,
                f"{complete_prefix} — 분석 {result.get('analyzed', 0)}건, "
                f"매매 {result.get('executed', 0)}건, "
                f"실시간 감시 {len(desired_symbols)}종목 → 모니터링 돌입",
            )
            if settings.should_use_adaptive_rescan(market) and self._adaptive_trigger_can_reschedule(trigger_reason):
                await self._schedule_next_adaptive_rescan(market, result)
        except asyncio.CancelledError:
            err_msg = f"{trigger_reason}: asyncio.CancelledError"
            state.last_error = err_msg
            await self._log_schedule(
                market,
                ActivityPhase.ERROR,
                f"❌ [{market}] 트레이딩 스캔 취소 ({trigger_reason})",
            )
            logger.warning("[{}] 트레이딩 스캔 취소 ({})", market, trigger_reason)
            raise
        except Exception as e:
            err_msg = " ".join((str(e) or repr(e)).split())[:500]
            state.last_error = err_msg
            await self._log_schedule(
                market,
                ActivityPhase.ERROR,
                f"❌ [{market}] 트레이딩 스캔 오류 ({trigger_reason}): {err_msg[:120]}",
            )
            logger.error("[{}] 트레이딩 스캔 오류 ({}): {}", market, trigger_reason, err_msg)

    async def _market_open_scan(self, market: str, trigger_reason: str = "scheduled_open") -> None:
        """장 시작 직후 — 전체 시장 스캔 → 종목 선정 → 매매

        AI Agent가 전체 시장 데이터를 받아서 어떤 종목에 투자할지 판단하고,
        선정된 종목을 WebSocket 실시간 구독에 등록하여 이후 이벤트 기반 매매.
        """
        if settings.should_use_adaptive_rescan(market):
            self._reset_adaptive_state(market)
            self._clear_adaptive_rescan_job(market)
        await self._execute_trading_scan(
            market,
            trigger_reason=trigger_reason,
            include_gap_check=trigger_reason in {"scheduled_open", "startup_catchup"},
        )

    async def _adaptive_rescan(
        self,
        market: str,
        trigger_reason: str = "adaptive_rescan",
    ) -> None:
        """AI가 예약한 one-shot 장중 재스캔"""
        from scheduler.market_calendar import market_calendar
        from util.time_util import now_kst
        from trading.market_profile import is_crypto_market, market_timezone
        from zoneinfo import ZoneInfo

        if not settings.should_use_adaptive_rescan(market):
            self._clear_adaptive_rescan_job(market)
            return

        if market_calendar.is_holiday(market):
            return
        if self._remaining_scheduled_budget(market) <= 0:
            self._clear_adaptive_rescan_job(market)
            return

        tz = ZoneInfo(market_timezone(market))
        market_now = now_kst().astimezone(tz)
        session = market_calendar.get_market_session(dt=market_now, market=market)
        if settings.should_enforce_buy_cutoff(market, session=session):
            mkt_cfg = settings.get_market_config(market, session=session)
            cutoff = time(mkt_cfg["buy_cutoff_hour"], mkt_cfg["buy_cutoff_minute"])
            if market_now.time() >= cutoff:
                logger.info("[{}] 매수 마감 시간 경과 → adaptive 재스캔 스킵", market)
                self._clear_adaptive_rescan_job(market)
                return

        self._clear_adaptive_rescan_job(market)
        await self._execute_trading_scan(
            market,
            trigger_reason=trigger_reason,
            include_gap_check=False,
        )

    async def _intraday_rescan(self, market: str) -> None:
        """장중 재스캔 — 새로운 기회 탐색

        기존 run_cycle()을 재사용하여 시장 재스캔 → 분석 → 매매.
        cycle_lock이 잡혀있으면 자동 스킵.
        """
        if settings.AI_DYNAMIC_RESCAN_ENABLED:
            logger.debug("[{}] 고정 intraday_rescan 무시 — adaptive mode 활성", market)
            return

        from scheduler.market_calendar import market_calendar
        from util.time_util import now_kst
        from trading.market_profile import market_timezone
        from zoneinfo import ZoneInfo

        if market_calendar.is_holiday(market):
            return

        tz = ZoneInfo(market_timezone(market))
        market_now = now_kst().astimezone(tz)

        # 매수 마감 시간 이후면 재스캔 불필요
        session = market_calendar.get_market_session(dt=market_now, market=market)
        if settings.should_enforce_buy_cutoff(market, session=session):
            from datetime import time as _time
            mkt_cfg = settings.get_market_config(market, session=session)
            cutoff = _time(mkt_cfg["buy_cutoff_hour"], mkt_cfg["buy_cutoff_minute"])
            if market_now.time() >= cutoff:
                logger.info("[{}] 매수 마감 시간 경과 → 장중 재스캔 스킵", market)
                return

        await self._execute_trading_scan(
            market,
            trigger_reason="intraday_rescan",
            include_gap_check=False,
        )

    async def _update_realtime_subscriptions(self, market: str) -> None:
        """최근 선정 종목 + 보유 종목 기준으로 WebSocket 구독 갱신."""
        try:
            from services.watchlist_sync import reconcile_market_watchlist

            desired_symbols = await reconcile_market_watchlist(market)
            logger.info("[{}] WebSocket 구독 갱신: {}종목", market, len(desired_symbols))
        except Exception as e:
            logger.warning("[{}] WebSocket 구독 갱신 실패: {}", market, str(e))

    async def _holdings_check(self, market: str) -> None:
        """보유종목 현재가 점검 — WebSocket 보완용 안전망 + 시간 기반 조기 청산

        WebSocket 끊김이나 누락 대비, MCP로 보유종목 현재가를 직접 조회하여
        손절/익절 조건을 체크한다. 데이트레이딩 모드에서는 잔여 시간에 따라
        조기 익절/손절도 실행한다. 해당 시장 장중에만 작동.
        """
        from scheduler.market_calendar import market_calendar
        if not market_calendar.is_trading_hours(market):
            return

        from services.activity_logger import activity_logger
        from util.time_util import now_kst
        from trading.market_profile import market_timezone
        from zoneinfo import ZoneInfo

        try:
            from agent.decision_maker import decision_maker
            from trading.account_manager import account_manager
            from trading.mcp_client import mcp_client as _mcp

            repaired = await decision_maker.repair_stale_open_trade_results(market_scope=market)
            if repaired:
                logger.warning("[{}] 보유종목 점검 전 stale open {}건 복구", market, repaired)

            holdings = await account_manager.get_holdings(market)
            if not holdings:
                return

            # 구독 갱신 (WebSocket 연결 복원 대비)
            await self._update_realtime_subscriptions(market)

            # 강제 청산까지 남은 시간 계산 (시장 현지 시간 기준, 크립토는 비적용)
            now, session, mkt_cfg = self._runtime_market_config(market)
            minutes_left: int | None = None
            if settings.market_has_force_liquidation(market, session=session):
                close_time = now.replace(
                    hour=mkt_cfg["force_liquidation_hour"],
                    minute=mkt_cfg["force_liquidation_minute"],
                    second=0,
                    microsecond=0,
                )
                minutes_left = max(0, int((close_time - now).total_seconds() / 60))

            alerts = []
            for h in holdings:
                if h.avg_buy_price <= 0 or h.quantity <= 0:
                    continue
                # MCP로 현재가 직접 조회
                resp = await _mcp.get_current_price(h.symbol, market=h.market)
                if not resp.success or not resp.data:
                    continue
                current = float(resp.data.get("price", 0))
                if current <= 0:
                    continue
                pnl_rate = (current - h.avg_buy_price) / h.avg_buy_price * 100

                should_sell = False
                reason = ""

                # AI가 설정한 임계값이 있을 때만 가격 기반 익절/손절을 수행한다.
                from realtime.event_detector import event_detector
                th = event_detector.get_thresholds(h.symbol, market=h.market)

                stop_loss_pct: float | None = None
                take_profit_pct: float | None = None
                if th.stop_loss > 0 and h.avg_buy_price > 0:
                    stop_loss_pct = ((th.stop_loss - h.avg_buy_price) / h.avg_buy_price) * 100
                if th.take_profit > 0 and h.avg_buy_price > 0:
                    take_profit_pct = ((th.take_profit - h.avg_buy_price) / h.avg_buy_price) * 100
                if (
                    stop_loss_pct is None
                    and take_profit_pct is None
                    and not is_crypto_market(h.market)
                ):
                    logger.warning("[{}] 손절/익절 임계값 미설정: {}", h.market, h.symbol)

                # 손절/익절
                if stop_loss_pct is not None and pnl_rate <= stop_loss_pct:
                    should_sell = True
                    reason = f"손절 도달 ({pnl_rate:+.1f}%, 기준 {stop_loss_pct:+.1f}%)"
                elif take_profit_pct is not None and pnl_rate >= take_profit_pct:
                    should_sell = True
                    reason = f"익절 도달 ({pnl_rate:+.1f}%, 기준 {take_profit_pct:+.1f}%)"
                # 시간 기반 조건 (데이트레이딩 전용)
                elif settings.DAY_TRADING_ONLY and minutes_left is not None:
                    if minutes_left <= 60 and pnl_rate > 1.0:
                        should_sell = True
                        reason = f"잔여 {minutes_left}분 + 수익 {pnl_rate:+.1f}% → 조기 익절"
                    elif minutes_left <= 30 and pnl_rate < -1.0:
                        should_sell = True
                        reason = f"잔여 {minutes_left}분 + 손실 {pnl_rate:+.1f}% → 조기 손절"

                if should_sell and settings.is_trading_enabled_for_market(h.market):
                    try:
                        sell_resp = await _mcp.place_order(
                            symbol=h.symbol,
                            side="SELL",
                            quantity=h.quantity,
                            price=None,
                            market=h.market,
                        )
                        status = "성공" if sell_resp.success else f"실패: {sell_resp.error or ''}"
                        alerts.append(
                            f"\U0001f6a8 {h.name}({h.symbol}): {reason} → 매도 {status}"
                        )
                        if sell_resp.success:
                            from realtime.event_detector import event_detector
                            event_detector.remove_levels(h.symbol, market=h.market)
                    except Exception as e:
                        alerts.append(
                            f"\u274c {h.name}({h.symbol}): {reason} → 매도 오류: {str(e)[:50]}"
                        )
                elif should_sell:
                    # 시장별 주문 비활성화면 알림만
                    alerts.append(
                        f"\u26a0\ufe0f {h.name}({h.symbol}): {reason} (trading disabled)"
                    )

            if alerts:
                time_hint = f"잔여 {minutes_left}분" if minutes_left is not None else "24/7 제한 없음"
                await activity_logger.log(
                    ActivityType.HOLDINGS_CHECK, ActivityPhase.PROGRESS,
                    f"\U0001f50d [{market}] 보유종목 점검 ({time_hint}):\n" + "\n".join(alerts),
                )
        except Exception as e:
            logger.warning("[{}] 보유종목 점검 오류: {}", market, str(e))

    async def _load_due_crypto_timebox_trades(self, deadline: datetime) -> list[object]:
        """타임박스 만료된 미청산 코인 매수 기록을 로드한다."""
        from sqlalchemy import select

        from core.database import AsyncSessionLocal
        from models.coin_trade_result import CoinTradeResult

        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    select(CoinTradeResult)
                    .where(CoinTradeResult.side == "BUY")
                    .where(CoinTradeResult.exit_at.is_(None))
                    .where(CoinTradeResult.entry_at.is_not(None))
                    .where(CoinTradeResult.entry_at <= deadline)
                    .order_by(CoinTradeResult.entry_at.asc(), CoinTradeResult.created_at.asc())
                )
            ).scalars().all()
        return list(rows)

    async def _is_coin_trade_settled(self, trade_result_id: str) -> bool:
        """매도 확인 후 코인 trade_result가 청산 완료되었는지 확인한다."""
        from sqlalchemy import select

        from core.database import AsyncSessionLocal
        from models.coin_trade_result import CoinTradeResult

        async with AsyncSessionLocal() as session:
            row = await session.scalar(
                select(CoinTradeResult)
                .where(CoinTradeResult.id == trade_result_id)
                .limit(1)
            )
        return bool(row and row.exit_at is not None)

    @staticmethod
    def _build_crypto_settlement_context(open_trade, holding, exit_reason: str) -> dict[str, object]:
        """타임박스 정산용 최소 분석 컨텍스트를 구성한다."""
        return {
            "stock_name": str(getattr(holding, "name", "") or getattr(open_trade, "coin_name", "") or getattr(open_trade, "symbol", "")),
            "strategy_type": str(getattr(open_trade, "strategy_type", "") or "TIMEBOX"),
            "market_regime": str(getattr(open_trade, "market_regime", "") or ""),
            "ai_confidence": float(getattr(open_trade, "ai_confidence", 0.0) or 0.0),
            "ai_target_price": getattr(open_trade, "ai_target_price", None),
            "ai_stop_loss_price": getattr(open_trade, "ai_stop_loss_price", None),
            "analysis_source": "TIMEBOX_SETTLEMENT",
            "event_type": exit_reason,
            "currency": "KRW",
        }

    async def _crypto_settlement_sweep(self, market: str) -> None:
        """코인 타임박스 만료 포지션을 청산하고 정산 리포트를 생성한다."""
        from agent.decision_maker import decision_maker
        from agent.trading_agent import trading_agent
        from realtime.event_detector import event_detector
        from scheduler.market_calendar import market_calendar
        from services.activity_logger import activity_logger
        from services.coin_daily_report_service import (
            CoinReportGenerationError,
            coin_daily_report_service,
        )
        from trading.account_manager import account_manager
        from trading.market_profile import market_scope
        from trading.mcp_client import mcp_client as _mcp
        from util.time_util import ensure_kst, now_kst

        scope = market_scope(market)
        runtime = trading_agent.get_runtime(scope)
        trading_date = market_calendar.market_date(market=scope)

        if not settings.CRYPTO_ENABLED:
            logger.debug("[{}] 타임박스 정산 스킵 — CRYPTO_ENABLED=false", market)
            return
        if runtime.cycle_lock.locked():
            logger.debug("[{}] 타임박스 정산 스킵 — 코인 사이클 실행 중", market)
            return
        if runtime.settlement_lock.locked():
            logger.debug("[{}] 타임박스 정산 스킵 — 기존 정산 스윕 진행 중", market)
            return

        with activity_logger.context(market_scope=scope, trading_date=trading_date):
            async with runtime.settlement_lock:
                try:
                    if runtime.cycle_lock.locked():
                        logger.debug("[{}] 타임박스 정산 취소 — 코인 사이클이 먼저 시작됨", market)
                        return

                    now = now_kst()
                    timebox_hours = settings.crypto_timebox_hours
                    exit_reason = f"TIMEBOX_{timebox_hours}H"
                    deadline = now - timedelta(hours=timebox_hours)
                    due_trades = await self._load_due_crypto_timebox_trades(deadline)
                    if not due_trades:
                        return

                    holdings = await account_manager.get_holdings(market)
                    if not holdings:
                        logger.info("[{}] 타임박스 만료 trade_result는 있으나 실제 보유 코인이 없음", market)
                        return

                    holdings_map = {
                        str(holding.symbol or "").upper(): holding
                        for holding in holdings
                        if getattr(holding, "symbol", None) and getattr(holding, "quantity", 0) > 0
                    }
                    pending_orders = await account_manager.get_pending_orders(market)
                    pending_sell_symbols = {
                        str(order.symbol or "").upper()
                        for order in pending_orders
                        if str(order.side or "") == "매도"
                    }

                    candidates: list[tuple[object, object]] = []
                    for open_trade in due_trades:
                        symbol = str(getattr(open_trade, "symbol", "") or "").upper()
                        if not symbol or symbol in pending_sell_symbols:
                            continue
                        holding = holdings_map.get(symbol)
                        if holding is None:
                            continue
                        candidates.append((open_trade, holding))

                    if not candidates:
                        return

                    if not settings.is_trading_enabled_for_market(market):
                        await self._log_schedule(
                            market,
                            ActivityPhase.PROGRESS,
                            f"⚠️ [{market}] 타임박스 만료 {len(candidates)}건 있으나 trading disabled로 정산 스킵",
                        )
                        return

                    settlement_cycle_id = activity_logger.start_cycle()
                    await activity_logger.log(
                        ActivityType.CYCLE,
                        ActivityPhase.START,
                        f"⏱️ [{market}] 타임박스 정산 시작 — 만료 {len(candidates)}건 | 기준 {timebox_hours}시간",
                        cycle_id=settlement_cycle_id,
                        detail={
                            "market": market,
                            "timebox_hours": timebox_hours,
                            "candidate_count": len(candidates),
                        },
                    )

                    settled_symbols: list[str] = []
                    failed_symbols: list[str] = []

                    for open_trade, holding in candidates:
                        symbol = str(getattr(holding, "symbol", "") or "").upper()
                        entry_at = ensure_kst(getattr(open_trade, "entry_at", None))
                        hold_hours = (
                            max(0, int((now - entry_at).total_seconds() // 3600))
                            if entry_at
                            else timebox_hours
                        )
                        current_price = float(
                            getattr(holding, "current_price", 0.0)
                            or getattr(open_trade, "entry_price", 0.0)
                            or 0.0
                        )
                        requested_amount_krw = max(current_price * float(getattr(holding, "quantity", 0.0) or 0.0), 0.0)

                        await activity_logger.log(
                            ActivityType.ORDER,
                            ActivityPhase.PROGRESS,
                            f"⏱️ [{symbol}] 타임박스 만료 {hold_hours}시간 → 자동 청산 시도",
                            cycle_id=settlement_cycle_id,
                            symbol=symbol,
                            detail={
                                "market": market,
                                "exit_reason": exit_reason,
                                "hold_hours": hold_hours,
                                "entry_at": entry_at.isoformat() if entry_at else None,
                                "quantity": float(getattr(holding, "quantity", 0.0) or 0.0),
                            },
                        )

                        try:
                            response = await _mcp.place_order(
                                symbol=symbol,
                                side="SELL",
                                quantity=float(getattr(holding, "quantity", 0.0) or 0.0),
                                price=None,
                                market=market,
                            )
                        except Exception as exc:
                            failed_symbols.append(symbol)
                            logger.error("[{}] 타임박스 청산 주문 오류 ({}): {}", market, symbol, str(exc))
                            await activity_logger.log(
                                ActivityType.ORDER,
                                ActivityPhase.ERROR,
                                f"❌ [{symbol}] 타임박스 청산 주문 오류: {str(exc)[:100]}",
                                cycle_id=settlement_cycle_id,
                                symbol=symbol,
                                error_message=str(exc),
                            )
                            continue

                        if not response.success:
                            failed_symbols.append(symbol)
                            error_message = str(response.error or "알 수 없는 오류")
                            logger.error("[{}] 타임박스 청산 실패 ({}): {}", market, symbol, error_message)
                            await activity_logger.log(
                                ActivityType.ORDER,
                                ActivityPhase.ERROR,
                                f"❌ [{symbol}] 타임박스 청산 실패: {error_message[:100]}",
                                cycle_id=settlement_cycle_id,
                                symbol=symbol,
                                error_message=error_message,
                            )
                            continue

                        order_id = str((response.data or {}).get("order_id") or "")
                        if not order_id:
                            failed_symbols.append(symbol)
                            await activity_logger.log(
                                ActivityType.ORDER,
                                ActivityPhase.ERROR,
                                f"❌ [{symbol}] 타임박스 청산 실패: 주문번호 누락",
                                cycle_id=settlement_cycle_id,
                                symbol=symbol,
                                error_message="order_id_missing",
                            )
                            continue

                        await decision_maker.confirm_and_record(
                            symbol=symbol,
                            market=market,
                            side="SELL",
                            order_id=order_id,
                            quantity=float(getattr(holding, "quantity", 0.0) or 0.0),
                            expected_price=current_price,
                            requested_amount_krw=requested_amount_krw,
                            analysis_context=self._build_crypto_settlement_context(open_trade, holding, exit_reason),
                            cycle_id=settlement_cycle_id,
                            exit_reason=exit_reason,
                        )

                        if await self._is_coin_trade_settled(str(getattr(open_trade, "id", ""))):
                            settled_symbols.append(symbol)
                            event_detector.remove_levels(symbol, market=market)
                        else:
                            failed_symbols.append(symbol)
                            await activity_logger.log(
                                ActivityType.ORDER,
                                ActivityPhase.ERROR,
                                f"❌ [{symbol}] 타임박스 청산 주문은 접수됐지만 체결 확인이 완료되지 않았습니다",
                                cycle_id=settlement_cycle_id,
                                symbol=symbol,
                                error_message="settlement_confirmation_missing",
                            )

                    if not settled_symbols:
                        await activity_logger.log(
                            ActivityType.CYCLE,
                            ActivityPhase.SKIP,
                            f"⏱️ [{market}] 타임박스 정산 종료 — 실제 청산 확정 없음",
                            cycle_id=settlement_cycle_id,
                            detail={
                                "market": market,
                                "timebox_hours": timebox_hours,
                                "failed_symbols": failed_symbols,
                            },
                        )
                        return

                    account_manager.invalidate_cache()
                    overview = await account_manager.get_account_overview(market)
                    if not overview.balance.is_valid:
                        message = overview.balance.status_message or "계좌 overview 재동기화 실패"
                        await activity_logger.log(
                            ActivityType.REPORT,
                            ActivityPhase.ERROR,
                            f"❌ [{market}] 타임박스 정산 후 overview 재동기화 실패: {message[:100]}",
                            cycle_id=settlement_cycle_id,
                            error_message=message,
                        )
                        return

                    report = None
                    try:
                        report = await coin_daily_report_service.generate_checkpoint_report(
                            market=market,
                            report_source="AUTO_SETTLEMENT",
                            trigger_reason=exit_reason,
                            applied_cycle_id=settlement_cycle_id,
                            market_regime=runtime.market_regime,
                            market_context=runtime.market_context,
                            period_anchor_sources={"AUTO_SETTLEMENT"},
                            raise_on_error=True,
                        )
                    except CoinReportGenerationError as exc:
                        logger.warning("[{}] 자동 정산 리포트 생성 실패: {}", market, exc.user_message)
                    except Exception as exc:
                        logger.error("[{}] 자동 정산 리포트 생성 오류: {}", market, str(exc))

                    if report is not None:
                        await trading_agent.refresh_runtime_trading_rules(
                            market=market,
                            cycle_id=settlement_cycle_id,
                            emit_activity=True,
                        )

                    await self._log_schedule(
                        market,
                        ActivityPhase.PROGRESS,
                        f"✅ [{market}] 타임박스 정산 완료: {len(settled_symbols)}건 청산"
                        f"{' | 자동 정산 리포트 생성' if report is not None else ''}",
                    )
                    await activity_logger.log(
                        ActivityType.CYCLE,
                        ActivityPhase.COMPLETE,
                        f"✅ [{market}] 타임박스 정산 완료: {len(settled_symbols)}건 청산",
                        cycle_id=settlement_cycle_id,
                        detail={
                            "market": market,
                            "timebox_hours": timebox_hours,
                            "exit_reason": exit_reason,
                            "settled_symbols": settled_symbols,
                            "failed_symbols": failed_symbols,
                            "report_generated": report is not None,
                        },
                    )
                except Exception as exc:
                    logger.error("[{}] 타임박스 정산 스윕 오류: {}", market, str(exc))
                    await self._log_schedule(
                        market,
                        ActivityPhase.ERROR,
                        f"❌ [{market}] 타임박스 정산 오류: {str(exc)[:100]}",
                    )

    async def _post_market(self, market: str) -> None:
        """장 마감 성과 리뷰"""
        from agent.trading_agent import trading_agent
        from scheduler.market_calendar import market_calendar

        if settings.AI_DYNAMIC_RESCAN_ENABLED:
            self._clear_adaptive_rescan_job(market)
        if market_calendar.is_holiday(market):
            logger.info("[{}] 휴장일 — 장 마감 리뷰 스킵", market)
            return

        logger.info("=== [{}] 장 마감 리뷰 시작 ===", market)
        await self._log_schedule(
            market,
            ActivityPhase.PROGRESS,
            f"\U0001f319 [{market}] 장 마감 — 오늘 매매 성과 리뷰 시작",
        )

        try:
            preview = await trading_agent.preview_cycle(market=market)
            if preview.get("skipped"):
                logger.info("[{}] 장 마감 리뷰 스킵: {}", market, preview["reason"])
                return
            await trading_agent.run_cycle(market=market)  # 장외이므로 자동으로 _run_after_hours_cycle 실행
        except Exception as e:
            logger.error("[{}] 장 마감 리뷰 오류: {}", market, str(e))

    async def _post_market_if_needed(self) -> None:
        """장외 기동 시 오늘 리뷰가 아직 안 되었으면 각 시장별로 실행"""
        from trading.market_profile import normalize_market

        for market_group in settings.enabled_market_groups:
            market = normalize_market(market_group)
            try:
                await self._post_market_if_needed_for(market)
            except Exception as e:
                logger.warning("[{}] 장외 리뷰 체크 실패: {}", market, str(e))

    async def _post_market_if_needed_for(self, market: str) -> None:
        """특정 시장의 장외 리뷰가 아직 안 되었으면 실행"""
        from scheduler.market_calendar import market_calendar
        from agent.trading_agent import trading_agent
        from trading.market_profile import is_crypto_market

        if is_crypto_market(market):
            logger.debug("[{}] 코인 시장은 startup 장마감 리뷰를 사용하지 않음", market)
            return

        market_now = self._market_now(market)
        if not market_calendar.is_trading_day(market, market_now):
            return

        if not market_calendar.is_post_market_review_time(market, market_now):
            logger.debug("[{}] 장마감 리뷰 시각 이전 — startup catch-up 스킵", market)
            return

        logger.info("[{}] 오늘 리뷰 미완료 — 장외 리뷰 실행", market)
        await trading_agent.run_immediate_review(market=market)

    async def _force_liquidation(self, market: str) -> None:
        """장 마감 전 청산

        DAY_TRADING_ONLY=True: 보유종목 전량 시장가 매도 (기존 동작)
        DAY_TRADING_ONLY=False: 종목별 스마트 판정 (HOLD/SELL)

        프리/애프터마켓 비활성 시:
        - 보유 유지 종목 포함 전체 WebSocket 구독 해제 (AI 비용 절감)
        - 즉시 성과 리포트 생성 (기존 30분 대기 제거)
        """
        import asyncio
        from scheduler.market_calendar import market_calendar
        from services.activity_logger import activity_logger

        if settings.AI_DYNAMIC_RESCAN_ENABLED:
            self._clear_adaptive_rescan_job(market)
        if market_calendar.is_holiday(market):
            return

        if not settings.TRADING_ENABLED:
            logger.info("[{}] 매매 비활성 — 청산 스킵", market)
            return

        after_hours_disabled = self._is_after_hours_disabled(market)

        try:
            from trading.account_manager import account_manager
            from trading.mcp_client import mcp_client as _mcp
            from services.watchlist_sync import cleanup_post_market_stock_watchlist

            async def _cleanup_after_close(retained_symbols: list[tuple[str, str]]) -> None:
                effective_retained = [] if after_hours_disabled else retained_symbols
                try:
                    await cleanup_post_market_stock_watchlist(
                        market,
                        retained_symbols=effective_retained,
                    )
                except Exception as cleanup_error:
                    logger.warning("[{}] 장후 감시 정리 실패: {}", market, str(cleanup_error))

            holdings = await account_manager.get_holdings(market)
            if not holdings:
                await _cleanup_after_close([])
                await self._log_schedule(
                    market,
                    ActivityPhase.PROGRESS,
                    f"\u2705 [{market}] 보유종목 없음 — 청산 불필요",
                )
                return

            sellable = [h for h in holdings if h.quantity > 0]
            if not sellable:
                await _cleanup_after_close([])
                return

            # 스윙 모드: 종목별 HOLD/SELL 판정
            if not settings.DAY_TRADING_ONLY:
                to_sell, to_hold = await self._smart_liquidation(sellable, market)
            else:
                to_sell = sellable
                to_hold = []

            mode_label = "스마트 청산" if not settings.DAY_TRADING_ONLY else "강제 청산"
            logger.warning("=== [{}] 장 마감 전 {} 시작 (매도 {}건, HOLD {}건) ===",
                           market, mode_label, len(to_sell), len(to_hold))
            await self._log_schedule(
                market,
                ActivityPhase.PROGRESS,
                f"\U0001f6a8 [{market}] {mode_label} — 매도 {len(to_sell)}건, HOLD {len(to_hold)}건",
            )

            if not to_sell:
                await _cleanup_after_close(
                    [(str(h.symbol or "").upper(), h.market) for h in to_hold if h.quantity > 0]
                )
                return

            async def _sell_one(h):
                resp = await _mcp.place_order(
                    symbol=h.symbol,
                    side="SELL",
                    quantity=h.quantity,
                    price=None,
                    market=h.market,
                )
                return (resp, h)

            results = await asyncio.gather(
                *[_sell_one(h) for h in to_sell],
                return_exceptions=True,
            )

            sold_count = 0
            failed_holdings = []

            for r in results:
                if isinstance(r, BaseException):
                    logger.error("[{}] 청산 주문 오류: {}", market, str(r))
                    continue

                resp, h = r
                if resp.success:
                    sold_count += 1
                    pnl_text = f"{h.pnl_rate:+.1f}%" if hasattr(h, "pnl_rate") else ""
                    await activity_logger.log(
                        ActivityType.ORDER, ActivityPhase.COMPLETE,
                        f"\U0001f6a8 [{market}] 청산: {h.name}({h.symbol}) "
                        f"{h.quantity}주 시장가 매도 {pnl_text}",
                        symbol=h.symbol,
                    )
                else:
                    failed_holdings.append(h)
                    logger.error(
                        "[{}] 청산 실패: {}({}) — {}",
                        market, h.name, h.symbol, resp.error or "알 수 없는 오류",
                    )
                    await activity_logger.log(
                        ActivityType.ORDER, ActivityPhase.ERROR,
                        f"\u274c [{market}] 청산 실패: {h.name}({h.symbol}) — {resp.error or ''}",
                        symbol=h.symbol,
                    )

            # 실패 종목 2차 재시도 (5초 후)
            if failed_holdings:
                logger.warning("[{}] 청산 {}건 실패 → 5초 후 재시도", market, len(failed_holdings))
                await self._log_schedule(
                    market,
                    ActivityPhase.PROGRESS,
                    f"\u26a0\ufe0f [{market}] 청산 {len(failed_holdings)}건 실패 → 5초 후 재시도",
                )
                await asyncio.sleep(5)
                remaining_failed_holdings = []
                retry_results = await asyncio.gather(
                    *[_sell_one(h) for h in failed_holdings],
                    return_exceptions=True,
                )
                for r in retry_results:
                    if isinstance(r, BaseException):
                        logger.error("[{}] 청산 재시도 오류: {}", market, str(r))
                        continue
                    resp, h = r
                    if resp.success:
                        sold_count += 1
                        logger.info("[{}] 청산 재시도 성공: {}({})", market, h.name, h.symbol)
                    else:
                        remaining_failed_holdings.append(h)
                        logger.error("[{}] 청산 재시도 실패: {}({}) — {}", market, h.name, h.symbol, resp.error or "")
                failed_holdings = remaining_failed_holdings

            summary = f"\U0001f6a8 [{market}] {mode_label} 완료: {sold_count}건 매도"
            if to_hold:
                hold_names = ", ".join(f"{h.name}" for h in to_hold)
                summary += f" | HOLD {len(to_hold)}건: {hold_names}"
            if failed_holdings:
                summary += f" | 실패 {len(failed_holdings)}건"
            await self._log_schedule(market, ActivityPhase.PROGRESS, summary)

            retained_symbols = [
                (str(h.symbol or "").upper(), h.market)
                for h in [*to_hold, *failed_holdings]
                if h.quantity > 0
            ]
            await _cleanup_after_close(retained_symbols)

        except Exception as e:
            logger.error("[{}] 청산 오류: {}", market, str(e))
            await self._log_schedule(
                market,
                ActivityPhase.ERROR,
                f"\u274c [{market}] 청산 오류: {str(e)[:100]}",
            )
        finally:
            # 청산 완료 플래그 설정 → 이벤트 기반 분석 차단 (모든 경로에서 설정)
            try:
                from agent.trading_agent import trading_agent as _ta
                from trading.market_profile import market_scope as _ms
                _state = _ta._get_state(_ms(market))
                _state.liquidation_complete = True
                logger.info("[{}] 청산 완료 — 이벤트 분석 차단 플래그 설정", market)
            except Exception as flag_err:
                logger.warning("[{}] 청산 플래그 설정 실패: {}", market, str(flag_err))

            # KIS 체결조회 기반 TradeResult 보정 (fallback: holding 스냅샷)
            try:
                await self._reconcile_from_kis(market)
            except Exception as reconcile_err:
                logger.warning("[{}] KIS 체결 보정 실패 → holding fallback: {}", market, str(reconcile_err))

            if after_hours_disabled:
                try:
                    await self._trigger_immediate_review(market)
                except Exception as review_err:
                    logger.error("[{}] 즉시 리뷰 트리거 실패: {}", market, str(review_err))

    def _is_after_hours_disabled(self, market: str) -> bool:
        """장후/시간외 매매 비활성 여부"""
        from trading.market_profile import is_domestic_market, is_us_market, normalize_market

        market_code = normalize_market(market)
        if is_us_market(market_code):
            return not settings.US_AFTERMARKET_ENABLED
        if is_domestic_market(market_code):
            return not settings.KRX_NXT_AFTER_ENABLED
        return False

    async def _reconcile_from_kis(self, market: str) -> None:
        """KIS 일별 체결조회 API로 TradeResult를 실제 체결가 기반으로 보정"""
        from collections import defaultdict
        from datetime import datetime

        from core.database import AsyncSessionLocal
        from models.trade_result import TradeResult
        from repositories.trade_result_repository import TradeResultRepository
        from scheduler.market_calendar import market_calendar
        from trading.kis_api import get_domestic_order_list
        from trading.market_profile import normalize_market
        from uuid import uuid4

        market_code = normalize_market(market)
        today = market_calendar.market_date(market=market_code)
        today_str = today.strftime("%Y%m%d")

        result = await get_domestic_order_list(today_str, today_str)
        if not result.get("success"):
            logger.warning("[{}] KIS 체결내역 조회 실패 — 보정 스킵", market)
            return

        orders = result.get("output1", [])
        if not orders:
            logger.info("[{}] KIS 체결내역 0건 — 보정 불필요", market)
            return

        # 시간순 정렬 + 종목별 매수/매도 큐
        orders.sort(key=lambda o: o.get("ord_tmd", ""))
        buys_queue: dict[str, list[dict]] = defaultdict(list)
        sells_queue: dict[str, list[dict]] = defaultdict(list)

        for o in orders:
            symbol = o.get("pdno", "")
            side = o.get("sll_buy_dvsn_cd")
            qty = int(o.get("tot_ccld_qty", 0))
            if qty == 0 or not symbol:
                continue
            entry = {
                "symbol": symbol,
                "name": o.get("prdt_name", ""),
                "price": float(o.get("avg_prvs", 0)),
                "qty": qty,
                "time": o.get("ord_tmd", ""),
                "order_id": o.get("odno", ""),
            }
            if side == "02":
                buys_queue[symbol].append(entry)
            elif side == "01":
                sells_queue[symbol].append(entry)

        # FIFO 페어링
        pairs = []
        all_symbols = set(list(buys_queue.keys()) + list(sells_queue.keys()))
        for symbol in all_symbols:
            buy_list = buys_queue.get(symbol, [])
            sell_list = sells_queue.get(symbol, [])
            for i, buy in enumerate(buy_list):
                sell = sell_list[i] if i < len(sell_list) else None
                pairs.append({"symbol": symbol, "name": buy["name"], "buy": buy, "sell": sell})
            # 매도만 있고 매수 없는 경우 (어제 진입 종목)
            for j in range(len(buy_list), len(sell_list)):
                pairs.append({"symbol": symbol, "name": sell_list[j]["name"], "buy": None, "sell": sell_list[j]})

        async with AsyncSessionLocal() as session:
            async with session.begin():
                repo = TradeResultRepository(session)
                updated = 0
                created = 0

                for p in pairs:
                    buy = p["buy"]
                    sell = p["sell"]

                    if buy:
                        # order_id로 기존 TradeResult 매칭
                        from sqlalchemy import select
                        stmt = select(TradeResult).where(
                            TradeResult.order_id == buy["order_id"],
                        ).limit(1)
                        res = await session.execute(stmt)
                        tr = res.scalar_one_or_none()

                        if tr:
                            # 매수가 보정
                            if buy["price"] > 0 and (tr.entry_price == 0 or tr.entry_price is None):
                                tr.entry_price = buy["price"]
                                tr.entry_price_krw = buy["price"]
                            # 매도 보정
                            if sell:
                                sell_time = sell["time"]
                                tr.exit_at = datetime(
                                    today.year, today.month, today.day,
                                    int(sell_time[:2]), int(sell_time[2:4]), int(sell_time[4:6]),
                                )
                                tr.exit_price = sell["price"]
                                tr.exit_price_krw = sell["price"]
                                entry_p = tr.entry_price or buy["price"]
                                tr.pnl = (sell["price"] - entry_p) * buy["qty"] if entry_p > 0 else 0
                                tr.raw_pnl = tr.pnl
                                tr.return_pct = ((sell["price"] - entry_p) / entry_p * 100) if entry_p > 0 else 0
                                tr.is_win = tr.pnl > 0
                                tr.exit_reason = "FORCE_LIQUIDATION" if sell_time >= "150000" else "SIGNAL"
                                tr.hold_days = max(1, (today - tr.entry_at.date()).days) if tr.entry_at else 1
                            updated += 1
                        else:
                            # 새 TradeResult 생성
                            buy_time = buy["time"]
                            buy_dt = datetime(
                                today.year, today.month, today.day,
                                int(buy_time[:2]), int(buy_time[2:4]), int(buy_time[4:6]),
                            )
                            sell_dt = None
                            exit_price = 0.0
                            pnl = 0.0
                            pnl_rate = 0.0
                            exit_reason = ""
                            if sell:
                                sell_time = sell["time"]
                                sell_dt = datetime(
                                    today.year, today.month, today.day,
                                    int(sell_time[:2]), int(sell_time[2:4]), int(sell_time[4:6]),
                                )
                                exit_price = sell["price"]
                                pnl = (sell["price"] - buy["price"]) * buy["qty"] if buy["price"] > 0 else 0
                                pnl_rate = ((sell["price"] - buy["price"]) / buy["price"] * 100) if buy["price"] > 0 else 0
                                exit_reason = "FORCE_LIQUIDATION" if sell_time >= "150000" else "SIGNAL"
                            session.add(TradeResult(
                                id=str(uuid4()),
                                order_id=buy["order_id"],
                                stock_symbol=p["symbol"],
                                stock_name=p["name"],
                                currency="KRW",
                                side="BUY",
                                strategy_type="",
                                entry_price=buy["price"],
                                entry_price_krw=buy["price"],
                                exit_price=exit_price,
                                exit_price_krw=exit_price,
                                quantity=buy["qty"],
                                pnl=pnl,
                                raw_pnl=pnl,
                                return_pct=pnl_rate,
                                is_win=pnl > 0,
                                hold_days=1,
                                exit_reason=exit_reason,
                                market=market_code,
                                entry_at=buy_dt,
                                exit_at=sell_dt,
                            ))
                            created += 1

                    elif sell:
                        # 매도만 있는 경우 (어제 진입 종목)
                        open_tr = await repo.get_open_buy(p["symbol"], market=market_code)
                        if open_tr:
                            sell_time = sell["time"]
                            open_tr.exit_at = datetime(
                                today.year, today.month, today.day,
                                int(sell_time[:2]), int(sell_time[2:4]), int(sell_time[4:6]),
                            )
                            open_tr.exit_price = sell["price"]
                            open_tr.exit_price_krw = sell["price"]
                            if open_tr.entry_price and open_tr.entry_price > 0:
                                open_tr.pnl = (sell["price"] - open_tr.entry_price) * (open_tr.quantity or sell["qty"])
                                open_tr.return_pct = (sell["price"] - open_tr.entry_price) / open_tr.entry_price * 100
                            open_tr.raw_pnl = open_tr.pnl
                            open_tr.is_win = open_tr.pnl > 0
                            open_tr.exit_reason = "FORCE_LIQUIDATION"
                            open_tr.hold_days = max(1, (today - open_tr.entry_at.date()).days) if open_tr.entry_at else 1
                            updated += 1

        logger.info("[{}] KIS 체결 보정 완료: {}건 업데이트, {}건 신규 생성", market, updated, created)

    async def _close_trade_results(
        self,
        market: str,
        to_sell: list,
        failed_holdings: list,
    ) -> None:
        """청산 매도 성공 종목의 TradeResult를 close 처리 (exit_at, pnl 기록)"""
        from core.database import AsyncSessionLocal
        from repositories.trade_result_repository import TradeResultRepository
        from util.time_util import now_kst

        failed_symbols = {h.symbol for h in failed_holdings}
        sold_holdings = [h for h in to_sell if h.symbol not in failed_symbols]
        if not sold_holdings:
            return

        exit_time = now_kst()
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    repo = TradeResultRepository(session)
                    closed = 0
                    for h in sold_holdings:
                        open_trade = await repo.get_open_buy(h.symbol, market=h.market)
                        if not open_trade:
                            continue
                        open_trade.exit_at = exit_time
                        open_trade.exit_price = float(h.current_price or 0)
                        if open_trade.currency != "KRW" and h.exchange_rate_to_krw > 0:
                            open_trade.exit_price_krw = open_trade.exit_price * h.exchange_rate_to_krw
                        else:
                            open_trade.exit_price_krw = open_trade.exit_price
                        open_trade.pnl = float(h.pnl or 0)
                        if open_trade.entry_price and open_trade.entry_price > 0:
                            open_trade.return_pct = float(h.pnl_rate or 0)
                        open_trade.raw_pnl = open_trade.pnl
                        open_trade.is_win = open_trade.pnl > 0
                        open_trade.exit_reason = "FORCE_LIQUIDATION"
                        open_trade.hold_days = max(
                            1, (exit_time.date() - open_trade.entry_at.date()).days
                        ) if open_trade.entry_at else 1
                        closed += 1
            if closed:
                logger.info("[{}] 청산 TradeResult {} 건 close 완료", market, closed)
        except Exception as e:
            logger.warning("[{}] TradeResult close 처리 실패: {}", market, str(e))

    async def _trigger_immediate_review(self, market: str) -> None:
        """장후 비활성 시 즉시 리뷰 트리거"""
        from agent.trading_agent import trading_agent
        from services.activity_logger import activity_logger

        logger.info("[{}] 장후 비활성 — 즉시 리뷰 트리거", market)
        await self._log_schedule(
            market,
            ActivityPhase.PROGRESS,
            f"\U0001f4cb [{market}] 장후 비활성 — 즉시 성과 리뷰 시작",
        )
        try:
            result = await trading_agent.run_immediate_review(market=market)
            if result.get("skipped"):
                logger.info("[{}] 즉시 리뷰 스킵: {}", market, result.get("reason"))
        except Exception as e:
            logger.error("[{}] 즉시 리뷰 오류: {}", market, str(e))

    async def _smart_liquidation(self, sellable: list, market: str) -> tuple[list, list]:
        """스윙 모드: 장마감 AI 재리뷰 기반 HOLD/SELL 판정

        Returns:
            (to_sell, to_hold) 두 리스트
        """
        from agent.trading_agent import trading_agent
        from core.database import AsyncSessionLocal
        from realtime.event_detector import event_detector
        from repositories.trade_result_repository import TradeResultRepository
        from scheduler.market_calendar import market_calendar
        from trading.mcp_client import mcp_client as _mcp

        to_sell = []
        to_hold = []
        review_date = market_calendar.market_date(market=market).isoformat()

        for h in sellable:
            try:
                resp = await _mcp.get_current_price(h.symbol, market=h.market)
                current_price = 0.0
                if resp.success and resp.data:
                    current_price = float(resp.data.get("price", 0))

                if current_price <= 0:
                    to_sell.append(h)
                    logger.warning("[{}] 현재가 조회 실패 {} → SELL", market, h.symbol)
                    continue

                async with AsyncSessionLocal() as session:
                    repo = TradeResultRepository(session)
                    trade_result = await repo.get_open_buy(h.symbol, market=h.market)

                if not trade_result:
                    to_sell.append(h)
                    logger.warning("[{}] 미청산 TradeResult 없음 {} → SELL", market, h.symbol)
                    continue
                if self._is_premarket_scalp_trade(trade_result):
                    to_sell.append(h)
                    logger.warning("[{}] 프리마켓 단타 태그 잔존 {} → HOLD 없이 SELL", market, h.symbol)
                    continue

                hold_plan = self._resolve_open_position_hold_plan(trade_result)
                should_run_llm, llm_reason = self._should_run_close_review_llm(
                    holding=h,
                    trade_result=trade_result,
                    current_price=current_price,
                    hold_plan=hold_plan,
                )
                if not should_run_llm:
                    async with AsyncSessionLocal() as session:
                        async with session.begin():
                            repo = TradeResultRepository(session)
                            open_trade = await repo.get_open_buy(h.symbol, market=h.market)
                            if not open_trade:
                                to_sell.append(h)
                                logger.warning("[{}] 기존 HOLD 계획 반영 중 TradeResult 유실 {} → SELL", market, h.symbol)
                                continue
                            notes = self._apply_close_review_hold_update(
                                open_trade,
                                self._build_existing_close_review_payload(open_trade, hold_plan),
                                review_date=review_date,
                            )
                    hold_trade = open_trade
                    threshold_kwargs = {
                        "stop_loss": 0.0,
                        "take_profit": 0.0,
                        "trailing_stop_pct": 0.0,
                        **self._build_open_position_threshold_kwargs(hold_trade),
                    }
                    threshold_kwargs["highest_price"] = (
                        current_price if float(threshold_kwargs.get("trailing_stop_pct") or 0.0) > 0 else 0.0
                    )
                    event_detector.set_thresholds(h.symbol, market=h.market, **threshold_kwargs)
                    to_hold.append(h)
                    logger.info(
                        "[{}] 스마트 청산 HOLD(규칙): {} — {} | planned {}일, review {}회",
                        market,
                        h.symbol,
                        llm_reason,
                        notes.get("planned_hold_days"),
                        notes.get("close_review_count"),
                    )
                    continue

                decision = await trading_agent.review_close_hold_position(
                    holding=h,
                    trade_result=trade_result,
                    current_price=current_price,
                    market=h.market,
                )

                if decision is None:
                    if int(hold_plan["close_review_count"]) < int(hold_plan["planned_hold_days"]):
                        open_trade = None
                        async with AsyncSessionLocal() as session:
                            async with session.begin():
                                repo = TradeResultRepository(session)
                                open_trade = await repo.get_open_buy(h.symbol, market=h.market)
                                if open_trade:
                                    notes = self._apply_close_review_hold_update(
                                        open_trade,
                                        self._build_existing_close_review_payload(open_trade, hold_plan),
                                        review_date=review_date,
                                    )
                                else:
                                    notes = hold_plan["notes"]
                        hold_trade = open_trade or trade_result
                        threshold_kwargs = {
                            "stop_loss": 0.0,
                            "take_profit": 0.0,
                            "trailing_stop_pct": 0.0,
                            **self._build_open_position_threshold_kwargs(hold_trade),
                        }
                        threshold_kwargs["highest_price"] = (
                            current_price if float(threshold_kwargs.get("trailing_stop_pct") or 0.0) > 0 else 0.0
                        )
                        event_detector.set_thresholds(h.symbol, market=h.market, **threshold_kwargs)
                        to_hold.append(h)
                        logger.warning(
                            "[{}] 장마감 AI 재리뷰 실패 {} → 기존 계획 유지 HOLD ({}/{})",
                            market,
                            h.symbol,
                            notes.get("close_review_count", hold_plan["close_review_count"]),
                            hold_plan["planned_hold_days"],
                        )
                    else:
                        to_sell.append(h)
                        logger.warning(
                            "[{}] 장마감 AI 재리뷰 실패 {} → 계획 보유일 도달로 SELL ({}/{})",
                            market,
                            h.symbol,
                            hold_plan["close_review_count"],
                            hold_plan["planned_hold_days"],
                        )
                    continue

                action = str(decision.get("action") or "").upper()
                reason = str(decision.get("reason") or "").strip() or "사유 없음"
                if action == "HOLD":
                    async with AsyncSessionLocal() as session:
                        async with session.begin():
                            repo = TradeResultRepository(session)
                            open_trade = await repo.get_open_buy(h.symbol, market=h.market)
                            if not open_trade:
                                to_sell.append(h)
                                logger.warning("[{}] HOLD 반영 중 TradeResult 유실 {} → SELL", market, h.symbol)
                                continue
                            notes = self._apply_close_review_hold_update(
                                open_trade,
                                decision,
                                review_date=review_date,
                            )

                    trailing_stop_pct = float(notes.get("trailing_stop_pct") or 0.0)
                    threshold_kwargs = {
                        "stop_loss": float(decision.get("stop_loss_price") or 0.0),
                        "take_profit": float(decision.get("take_profit_price") or 0.0),
                        "trailing_stop_pct": trailing_stop_pct,
                        "highest_price": current_price if trailing_stop_pct > 0 else 0.0,
                    }
                    event_detector.set_thresholds(h.symbol, market=h.market, **threshold_kwargs)
                    to_hold.append(h)
                    logger.info(
                        "[{}] 스마트 청산 HOLD: {} — {} | planned {}일, review {}회",
                        market,
                        h.symbol,
                        reason,
                        notes.get("planned_hold_days"),
                        notes.get("close_review_count"),
                    )
                else:
                    to_sell.append(h)
                    logger.info("[{}] 스마트 청산 SELL: {} — {}", market, h.symbol, reason)
            except Exception as e:
                to_sell.append(h)
                logger.warning("[{}] 스마트 청산 판정 오류 {} → SELL: {}", market, h.symbol, str(e))

        return to_sell, to_hold

    async def _check_overnight_positions(self, market: str) -> None:
        """오버나이트 포지션 프리마켓 점검

        서버 재시작 대비 event_detector 임계값 재설정 + 계획 보유 상태 요약.
        """
        from services.activity_logger import activity_logger

        try:
            from agent.decision_maker import decision_maker
            from core.database import AsyncSessionLocal
            from repositories.trade_result_repository import TradeResultRepository
            from trading.market_profile import market_scope

            repaired = await decision_maker.repair_stale_open_trade_results(market_scope=market)
            if repaired:
                logger.warning("[{}] 오버나이트 점검 전 stale open {}건 복구", market, repaired)

            async with AsyncSessionLocal() as session:
                repo = TradeResultRepository(session)
                open_positions = await repo.get_all_open(market_scope=market_scope(market))

            if not open_positions:
                return

            premarket_scalps = [tr for tr in open_positions if self._is_premarket_scalp_trade(tr)]
            open_positions = [tr for tr in open_positions if not self._is_premarket_scalp_trade(tr)]
            if premarket_scalps:
                logger.warning(
                    "[{}] 오버나이트 점검에서 프리마켓 단타 태그 {}건 제외",
                    market,
                    len(premarket_scalps),
                )
            if not open_positions:
                return

            restored = await self._restore_open_position_thresholds(market, open_positions=open_positions)
            warnings = []
            for tr in open_positions:
                hold_plan = self._resolve_open_position_hold_plan(tr)
                if int(hold_plan["close_review_count"]) >= int(hold_plan["planned_hold_days"]):
                    warnings.append(
                        f"{tr.stock_name}({tr.stock_symbol}): planned {hold_plan['planned_hold_days']}일, "
                        f"review {hold_plan['close_review_count']}회"
                    )

            msg = f"\U0001f30d [{market}] 오버나이트 포지션 {len(open_positions)}건 점검"
            if restored:
                msg += f" | 임계값 복원 {restored}건"
            if warnings:
                msg += f" | ⚠️ 재리뷰 기준 도달: {', '.join(warnings)}"

            logger.info(msg)
            await self._log_schedule(market, ActivityPhase.PROGRESS, msg)
        except Exception as e:
            logger.warning("[{}] 오버나이트 포지션 점검 오류: {}", market, str(e))

    async def _check_overnight_gap(self, market: str) -> None:
        """장 시작 갭 체크 — 오버나이트 포지션 손절/익절 즉시 처리"""
        from services.activity_logger import activity_logger

        try:
            from core.database import AsyncSessionLocal
            from repositories.trade_result_repository import TradeResultRepository
            from trading.account_manager import account_manager
            from trading.mcp_client import mcp_client as _mcp

            holdings = await account_manager.get_holdings(market)
            if not holdings:
                return

            async with AsyncSessionLocal() as session:
                repo = TradeResultRepository(session)
                open_positions = await repo.get_all_open()

            # symbol → TradeResult 매핑
            open_map = {
                (tr.market, tr.stock_symbol): tr
                for tr in open_positions
                if not self._is_premarket_scalp_trade(tr)
            }

            alerts = []
            for h in holdings:
                if h.quantity <= 0:
                    continue
                tr = open_map.get((h.market, h.symbol))
                if not tr:
                    continue  # 당일 매수 등 — 갭 체크 불필요

                resp = await _mcp.get_current_price(h.symbol, market=h.market)
                if not resp.success or not resp.data:
                    continue
                current = float(resp.data.get("price", 0))
                if current <= 0:
                    continue

                should_sell = False
                reason = ""
                take_profit_price = getattr(tr, "ai_take_profit_price", None) or tr.ai_target_price

                # 갭 하락 → 손절가 이하
                if tr.ai_stop_loss_price and current <= tr.ai_stop_loss_price:
                    should_sell = True
                    reason = f"갭 하락 손절 (현재 {current:,.0f} ≤ 손절 {tr.ai_stop_loss_price:,.0f})"

                # 갭 상승 → 익절가 이상
                elif take_profit_price and current >= take_profit_price:
                    should_sell = True
                    reason = f"갭 상승 익절 (현재 {current:,.0f} ≥ 익절 {take_profit_price:,.0f})"

                if should_sell and settings.TRADING_ENABLED:
                    sell_resp = await _mcp.place_order(
                        symbol=h.symbol, side="SELL",
                        quantity=h.quantity, price=None, market=h.market,
                    )
                    status = "성공" if sell_resp.success else f"실패: {sell_resp.error or ''}"
                    alerts.append(f"\U0001f6a8 {h.name}({h.symbol}): {reason} → 매도 {status}")
                    if sell_resp.success:
                        from realtime.event_detector import event_detector
                        event_detector.remove_levels(h.symbol, market=h.market)
                elif should_sell:
                    alerts.append(f"\u26a0\ufe0f {h.name}({h.symbol}): {reason} (TRADING_ENABLED=false)")

            if alerts:
                msg = f"\U0001f30d [{market}] 오버나이트 갭 체크:\n" + "\n".join(alerts)
                logger.info(msg)
                await self._log_schedule(market, ActivityPhase.PROGRESS, msg)
        except Exception as e:
            logger.warning("[{}] 오버나이트 갭 체크 오류: {}", market, str(e))

    async def _expire_recommendations(self) -> None:
        """만료된 추천 처리"""
        logger.debug("만료 추천 처리 실행")

    @property
    def is_running(self) -> bool:
        return self._running


trading_scheduler = TradingScheduler()
