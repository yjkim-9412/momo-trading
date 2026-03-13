"""트레이딩 에이전트 스케줄러 — 멀티마켓 자동 운영

각 활성 시장(ENABLED_MARKETS)별로 독립된 잡 세트를 등록하여
KRX와 US 등을 동시에 자동 매매할 수 있다.

타임라인 예시 — KRX (Asia/Seoul):
  08:50  장 시작 전 준비 — 어제 리뷰 피드백 확인
  09:00  KRX 개장
  09:05  장 시작 스캔 → 종목 선정 → 실시간 모니터링 돌입
  09:00~14:30  WebSocket 실시간 이벤트 → AI 분석/매매 (이벤트 기반)
              + 1시간 간격 보유종목 안전 점검 (시간 기반 조기 청산 포함)
  11:00/13:00  장중 재스캔 — 새로운 기회 탐색
  14:30  신규 매수 마감 (청산 시간 확보)
  15:10  보유종목 전량 시장가 강제 청산 (종가경매 전, 병렬 실행)
  15:30  KRX 폐장
  15:40  장 마감 성과 리뷰 (KRX 종가 기반, 피드백 학습)
  16:00  포트폴리오 정산 (KIS ↔ DB 동기화)
  16:30  일봉 데이터 보관용 수집

타임라인 예시 — US (America/New_York, US_PREMARKET_ENABLED=true):
  03:50  프리마켓 준비
  04:05  프리마켓 시작 스캔
  10:30~15:30  보유종목 점검 (1시간 간격)
  11:00/13:00  장중 재스캔
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
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from core.config import settings
from trading.enums import ActivityPhase, ActivityType


@dataclass(frozen=True)
class MarketScheduleProfile:
    prep_time: time
    open_scan_time: time
    resume_sessions: frozenset[str]
    session_label: str


class TradingScheduler:
    """멀티마켓 자동 운영 스케줄러"""

    _OPEN_SCAN_GRACE = timedelta(minutes=10)

    def __init__(self):
        self.scheduler = AsyncIOScheduler()  # 잡별 timezone 사용
        self._running = False

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
        from trading.market_profile import is_us_market

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
        from trading.market_profile import market_timezone
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
            "intraday_rescan": (
                f"장중 재스캔 시작 ({market_now.strftime('%H:%M') if market_now else '--:--'})",
                f"🔄 [{market}] 장중 재스캔 시작 ({market_now.strftime('%H:%M') if market_now else '--:--'}) — 새로운 기회 탐색",
                f"✅ [{market}] 장중 재스캔 완료",
            ),
        }
        return trigger_messages[trigger_reason]

    def _setup_jobs(self) -> None:
        from scheduler.jobs.portfolio_sync_job import portfolio_sync_job
        from scheduler.jobs.market_data_job import market_data_job
        from trading.market_profile import is_us_market, market_timezone, normalize_market
        from zoneinfo import ZoneInfo

        for market_group in settings.enabled_market_groups:
            market = normalize_market(market_group)
            tz = ZoneInfo(market_timezone(market))
            is_us = is_us_market(market)
            mkt_cfg = settings.get_market_config(market)
            schedule = self._market_schedule_profile(market)
            label = market  # 잡 ID 서픽스

            # 시장별 시간 계산
            pre_h, pre_m = schedule.prep_time.hour, schedule.prep_time.minute
            open_h, open_m = schedule.open_scan_time.hour, schedule.open_scan_time.minute
            holdings_hours = "10-15" if is_us else "9-14"
            force_h = mkt_cfg["force_liquidation_hour"]
            force_m = mkt_cfg["force_liquidation_minute"]
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
        """서버 기동 시 현재 시간대에 맞는 초기 작업 실행"""
        import asyncio
        from scheduler.market_calendar import market_calendar
        from trading.mcp_client import mcp_client
        from trading.market_profile import normalize_market

        # 기동 직후 약간의 딜레이 (MCP 연결 안정화)
        await asyncio.sleep(3)

        mcp_available = mcp_client.is_connected
        scheduled_tasks = False
        for market_group in settings.enabled_market_groups:
            market = normalize_market(market_group)
            if not market_calendar.is_trading_hours(market):
                next_open = market_calendar.next_market_open(market=market)
                logger.info(
                    "서버 기동: {} 장외 → 다음 장 시작: {}",
                    market, next_open.strftime("%m/%d %H:%M"),
                )
                continue

            action = self._startup_trading_action(market)
            session = market_calendar.get_market_session(market=market)
            market_now = self._market_now(market)
            schedule = self._market_schedule_profile(market)

            if action is None:
                open_scan_at = datetime.combine(
                    market_now.date(),
                    schedule.open_scan_time,
                    tzinfo=market_now.tzinfo,
                )
                if market_now < open_scan_at:
                    logger.info(
                        "서버 기동: {} {} 세션 — {} 장 시작 스캔 대기 ({})",
                        market,
                        session,
                        schedule.session_label,
                        open_scan_at.strftime("%H:%M"),
                    )
                else:
                    logger.info(
                        "서버 기동: {} {} 세션 — startup 트레이딩 catch-up 대상 아님",
                        market,
                        session,
                    )
                continue

            if not mcp_available:
                logger.warning("[{}] 서버 기동: MCP 미연결 → {} 스킵", market, action)
                continue

            logger.info("서버 기동: {} {} 실행", market, action)
            asyncio.create_task(self._market_open_scan(market, trigger_reason=action))
            scheduled_tasks = True

        if not scheduled_tasks:
            asyncio.create_task(self._post_market_if_needed())

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
            from analysis.feedback.trading_rules import trading_rule_engine
            from agent.trading_agent import trading_agent
            from strategy.risk_manager import risk_manager

            active_rules = await trading_rule_engine.load_active_rules(scope)
            rules = active_rules.get("rules", [])

            if rules:
                runtime = trading_agent.get_runtime(scope)
                trading_rule_engine.apply_to_strategies(
                    runtime.strategies, active_rules,
                )
                runtime.active_trading_rules = active_rules
                runtime.rr_floor_overrides = active_rules.get("rr_floor_overrides", {})

                rule_summary = ", ".join(
                    f"{r.param_name}={r.param_value}" for r in rules[:5]
                )
                await activity_logger.log(
                    ActivityType.TRADING_RULE, ActivityPhase.COMPLETE,
                    f"📋 [{market}] 트레이딩 규칙 {len(rules)}건 적용: {rule_summary}",
                )
                await trading_rule_engine.record_application(
                    [r.id for r in rules],
                    market_scope=scope,
                )

            expired = await trading_rule_engine.expire_old_rules(scope)
            if expired:
                logger.info("[{}] 만료된 트레이딩 규칙 {}건 비활성화", market, expired)
        except Exception as e:
            logger.warning("[{}] 트레이딩 규칙 로드 실패: {}", market, str(e))

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

        market_now = self._market_now(market)
        header, start_summary, complete_prefix = self._scan_trigger_messages(
            market,
            trigger_reason,
            market_now,
        )

        if market_calendar.is_holiday(market):
            logger.info("[{}] 휴장일 — 트레이딩 스캔 스킵", market)
            return

        if not mcp_client.is_connected:
            logger.warning("[{}] MCP 미연결 — 트레이딩 스캔 스킵", market)
            await self._log_schedule(
                market,
                ActivityPhase.ERROR,
                f"⚠️ [{market}] MCP 미연결 — 트레이딩 스캔 스킵",
            )
            return

        logger.info("=== [{}] {} ===", market, header)
        await self._log_schedule(market, ActivityPhase.PROGRESS, start_summary)

        try:
            if include_gap_check and not settings.DAY_TRADING_ONLY:
                await self._check_overnight_gap(market)

            result = await trading_agent.run_cycle(market=market)
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

            selected = result.get("selected_symbols", [])
            holdings = await account_manager.get_holdings(market)
            holding_symbols = [(h.symbol, h.market) for h in holdings if h.symbol]
            all_symbols = list(
                {(m, symbol): (symbol, m) for symbol, m in selected + holding_symbols}.values()
            )[:41]

            if all_symbols:
                from realtime.stream_manager import stream_manager

                await stream_manager.replace_market_subscriptions(market, all_symbols)

            await self._log_schedule(
                market,
                ActivityPhase.PROGRESS,
                f"{complete_prefix} — 분석 {result.get('analyzed', 0)}건, "
                f"매매 {result.get('executed', 0)}건, "
                f"실시간 감시 {len(all_symbols)}종목 → 모니터링 돌입",
            )
        except Exception as e:
            logger.error("[{}] 트레이딩 스캔 오류: {}", market, str(e))

    async def _market_open_scan(self, market: str, trigger_reason: str = "scheduled_open") -> None:
        """장 시작 직후 — 전체 시장 스캔 → 종목 선정 → 매매

        AI Agent가 전체 시장 데이터를 받아서 어떤 종목에 투자할지 판단하고,
        선정된 종목을 WebSocket 실시간 구독에 등록하여 이후 이벤트 기반 매매.
        """
        await self._execute_trading_scan(
            market,
            trigger_reason=trigger_reason,
            include_gap_check=trigger_reason in {"scheduled_open", "startup_catchup"},
        )

    async def _intraday_rescan(self, market: str) -> None:
        """장중 재스캔 — 새로운 기회 탐색

        기존 run_cycle()을 재사용하여 시장 재스캔 → 분석 → 매매.
        cycle_lock이 잡혀있으면 자동 스킵.
        """
        from scheduler.market_calendar import market_calendar
        from util.time_util import now_kst
        from trading.market_profile import market_timezone
        from zoneinfo import ZoneInfo

        if market_calendar.is_holiday(market):
            return

        # 매수 마감 시간 이후면 재스캔 불필요
        if settings.DAY_TRADING_ONLY:
            from datetime import time as _time
            mkt_cfg = settings.get_market_config(market)
            cutoff = _time(mkt_cfg["buy_cutoff_hour"], mkt_cfg["buy_cutoff_minute"])
            tz = ZoneInfo(market_timezone(market))
            market_now = now_kst().astimezone(tz)
            if market_now.time() >= cutoff:
                logger.info("[{}] 매수 마감 시간 경과 → 장중 재스캔 스킵", market)
                return

        tz = ZoneInfo(market_timezone(market))
        market_now = now_kst().astimezone(tz)
        await self._execute_trading_scan(
            market,
            trigger_reason="intraday_rescan",
            include_gap_check=False,
        )

    async def _update_realtime_subscriptions(self, market: str) -> None:
        """보유종목 WebSocket 구독 갱신 (임계값은 AI가 설정)"""
        try:
            from trading.account_manager import account_manager
            from realtime.stream_manager import stream_manager

            holdings = await account_manager.get_holdings(market)
            if holdings:
                symbols = [(h.symbol, h.market) for h in holdings if h.symbol]
                await stream_manager.replace_market_subscriptions(market, symbols)
                logger.info("[{}] WebSocket 구독 갱신: {}종목", market, len(symbols))
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
            from trading.account_manager import account_manager
            from trading.mcp_client import mcp_client as _mcp

            holdings = await account_manager.get_holdings(market)
            if not holdings:
                return

            # 구독 갱신 (WebSocket 연결 복원 대비)
            await self._update_realtime_subscriptions(market)

            # 강제 청산까지 남은 시간 계산 (시장 현지 시간 기준)
            mkt_cfg = settings.get_market_config(market)
            tz = ZoneInfo(market_timezone(market))
            now = now_kst().astimezone(tz)
            close_time = now.replace(
                hour=mkt_cfg["force_liquidation_hour"],
                minute=mkt_cfg["force_liquidation_minute"],
                second=0, microsecond=0,
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

                # AI가 설정한 임계값이 있으면 우선 사용, 없으면 기본값
                from realtime.event_detector import event_detector
                th = event_detector.get_thresholds(h.symbol, market=h.market)

                stop_loss_pct = -3.0  # 기본값
                take_profit_pct = 5.0
                if th.stop_loss > 0 and h.avg_buy_price > 0:
                    stop_loss_pct = ((th.stop_loss - h.avg_buy_price) / h.avg_buy_price) * 100
                if th.take_profit > 0 and h.avg_buy_price > 0:
                    take_profit_pct = ((th.take_profit - h.avg_buy_price) / h.avg_buy_price) * 100

                # 손절/익절
                if pnl_rate <= stop_loss_pct:
                    should_sell = True
                    reason = f"손절 도달 ({pnl_rate:+.1f}%, 기준 {stop_loss_pct:+.1f}%)"
                elif pnl_rate >= take_profit_pct:
                    should_sell = True
                    reason = f"익절 도달 ({pnl_rate:+.1f}%, 기준 {take_profit_pct:+.1f}%)"
                # 시간 기반 조건 (데이트레이딩 전용)
                elif settings.DAY_TRADING_ONLY:
                    if minutes_left <= 60 and pnl_rate > 1.0:
                        should_sell = True
                        reason = f"잔여 {minutes_left}분 + 수익 {pnl_rate:+.1f}% → 조기 익절"
                    elif minutes_left <= 30 and pnl_rate < -1.0:
                        should_sell = True
                        reason = f"잔여 {minutes_left}분 + 손실 {pnl_rate:+.1f}% → 조기 손절"

                if should_sell and settings.TRADING_ENABLED:
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
                    # TRADING_ENABLED=false이면 알림만
                    alerts.append(
                        f"\u26a0\ufe0f {h.name}({h.symbol}): {reason} (TRADING_ENABLED=false)"
                    )

            if alerts:
                await activity_logger.log(
                    ActivityType.HOLDINGS_CHECK, ActivityPhase.PROGRESS,
                    f"\U0001f50d [{market}] 보유종목 점검 (잔여 {minutes_left}분):\n" + "\n".join(alerts),
                )
        except Exception as e:
            logger.warning("[{}] 보유종목 점검 오류: {}", market, str(e))

    async def _post_market(self, market: str) -> None:
        """장 마감 성과 리뷰"""
        from agent.trading_agent import trading_agent
        from scheduler.market_calendar import market_calendar
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
        market_now = self._market_now(market)
        if not market_calendar.is_trading_day(market, market_now):
            return

        if not market_calendar.is_post_market_review_time(market, market_now):
            logger.debug("[{}] 장마감 리뷰 시각 이전 — startup catch-up 스킵", market)
            return

        preview = await trading_agent.preview_cycle(market=market)
        if preview.get("skipped"):
            logger.info("[{}] 장외 리뷰 체크 결과 스킵: {}", market, preview["reason"])
            return

        logger.info("[{}] 오늘 리뷰 미완료 — 장외 리뷰 실행", market)
        await trading_agent.run_cycle(market=market)

    async def _force_liquidation(self, market: str) -> None:
        """장 마감 전 청산

        DAY_TRADING_ONLY=True: 보유종목 전량 시장가 매도 (기존 동작)
        DAY_TRADING_ONLY=False: 종목별 스마트 판정 (HOLD/SELL)
        """
        import asyncio
        from scheduler.market_calendar import market_calendar
        from services.activity_logger import activity_logger

        if market_calendar.is_holiday(market):
            return

        if not settings.TRADING_ENABLED:
            logger.info("[{}] 매매 비활성 — 청산 스킵", market)
            return

        try:
            from trading.account_manager import account_manager
            from trading.mcp_client import mcp_client as _mcp

            holdings = await account_manager.get_holdings(market)
            if not holdings:
                await self._log_schedule(
                    market,
                    ActivityPhase.PROGRESS,
                    f"\u2705 [{market}] 보유종목 없음 — 청산 불필요",
                )
                return

            sellable = [h for h in holdings if h.quantity > 0]
            if not sellable:
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
                        logger.error("[{}] 청산 재시도 실패: {}({}) — {}", market, h.name, h.symbol, resp.error or "")

            summary = f"\U0001f6a8 [{market}] {mode_label} 완료: {sold_count}건 매도"
            if to_hold:
                hold_names = ", ".join(f"{h.name}" for h in to_hold)
                summary += f" | HOLD {len(to_hold)}건: {hold_names}"
            if failed_holdings:
                summary += f" | 실패 {len(failed_holdings)}건"
            await self._log_schedule(market, ActivityPhase.PROGRESS, summary)

            # 매도한 종목만 이벤트 감시 임계값 제거 (HOLD 종목은 유지)
            from realtime.event_detector import event_detector
            sold_symbols = {(h.market, h.symbol) for h in to_sell}
            for h in holdings:
                if (h.market, h.symbol) in sold_symbols:
                    event_detector.remove_levels(h.symbol, market=h.market)

        except Exception as e:
            logger.error("[{}] 청산 오류: {}", market, str(e))
            await self._log_schedule(
                market,
                ActivityPhase.ERROR,
                f"\u274c [{market}] 청산 오류: {str(e)[:100]}",
            )

    async def _smart_liquidation(self, sellable: list, market: str) -> tuple[list, list]:
        """스윙 모드: 종목별 HOLD/SELL 판정 (코드 룰 기반)

        Returns:
            (to_sell, to_hold) 두 리스트
        """
        from core.database import AsyncSessionLocal
        from repositories.trade_result_repository import TradeResultRepository
        from strategy.holding_policy import evaluate_overnight_hold
        from trading.mcp_client import mcp_client as _mcp

        to_sell = []
        to_hold = []

        async with AsyncSessionLocal() as session:
            repo = TradeResultRepository(session)

            for h in sellable:
                try:
                    # 현재가 조회
                    resp = await _mcp.get_current_price(h.symbol, market=h.market)
                    current_price = 0.0
                    if resp.success and resp.data:
                        current_price = float(resp.data.get("price", 0))

                    if current_price <= 0:
                        to_sell.append(h)
                        logger.warning("[{}] 현재가 조회 실패 {} → SELL", market, h.symbol)
                        continue

                    # TradeResult (미청산 매수)
                    trade_result = await repo.get_open_buy(h.symbol, market=h.market)

                    decision = evaluate_overnight_hold(
                        h, trade_result, current_price, settings,
                    )

                    if decision.action == "HOLD":
                        to_hold.append(h)
                        logger.info("[{}] 스마트 청산 HOLD: {} — {}", market, h.symbol, decision.reason)
                    else:
                        to_sell.append(h)
                        logger.info("[{}] 스마트 청산 SELL: {} — {}", market, h.symbol, decision.reason)
                except Exception as e:
                    to_sell.append(h)
                    logger.warning("[{}] 스마트 청산 판정 오류 {} → SELL: {}", market, h.symbol, str(e))

        return to_sell, to_hold

    async def _check_overnight_positions(self, market: str) -> None:
        """오버나이트 포지션 프리마켓 점검

        서버 재시작 대비 event_detector 임계값 재설정 + 보유일 경고.
        """
        from services.activity_logger import activity_logger

        try:
            from core.database import AsyncSessionLocal
            from realtime.event_detector import event_detector
            from repositories.trade_result_repository import TradeResultRepository

            async with AsyncSessionLocal() as session:
                repo = TradeResultRepository(session)
                open_positions = await repo.get_all_open()

            if not open_positions:
                return

            restored = 0
            warnings = []
            for tr in open_positions:
                # event_detector 임계값 재설정
                kwargs = {}
                if tr.ai_stop_loss_price and tr.ai_stop_loss_price > 0:
                    kwargs["stop_loss"] = tr.ai_stop_loss_price
                if tr.ai_target_price and tr.ai_target_price > 0:
                    kwargs["take_profit"] = tr.ai_target_price
                if kwargs:
                    event_detector.set_thresholds(tr.stock_symbol, market=tr.market, **kwargs)
                    restored += 1

                # 최대 보유일 경고
                from strategy.holding_policy import _calc_hold_days, _get_max_hold_days
                hold_days = _calc_hold_days(tr)
                max_days = _get_max_hold_days(tr.strategy_type, settings)
                if hold_days >= max_days:
                    warnings.append(
                        f"{tr.stock_name}({tr.stock_symbol}): 보유 {hold_days}일 ≥ 최대 {max_days}일"
                    )

            msg = f"\U0001f30d [{market}] 오버나이트 포지션 {len(open_positions)}건 점검"
            if restored:
                msg += f" | 임계값 복원 {restored}건"
            if warnings:
                msg += f" | ⚠️ 초과보유: {', '.join(warnings)}"

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
            open_map = {(tr.market, tr.stock_symbol): tr for tr in open_positions}

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

                # 갭 하락 → 손절가 이하
                if tr.ai_stop_loss_price and current <= tr.ai_stop_loss_price:
                    should_sell = True
                    reason = f"갭 하락 손절 (현재 {current:,.0f} ≤ 손절 {tr.ai_stop_loss_price:,.0f})"

                # 갭 상승 → 익절가 이상
                elif tr.ai_target_price and current >= tr.ai_target_price:
                    should_sell = True
                    reason = f"갭 상승 익절 (현재 {current:,.0f} ≥ 목표 {tr.ai_target_price:,.0f})"

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
