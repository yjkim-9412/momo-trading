"""코인 체크포인트 회고 리포트 서비스"""
from __future__ import annotations

import json
from datetime import datetime, time

from loguru import logger
from sqlalchemy import select

from analysis.feedback.trading_rules import trading_rule_engine
from analysis.llm.llm_factory import llm_factory
from analysis.llm.prompts.crypto_cycle_review import (
    CRYPTO_CYCLE_REVIEW_PROMPT,
    CRYPTO_CYCLE_REVIEW_SYSTEM,
)
from core.database import AsyncSessionLocal
from core.json_utils import parse_llm_json
from models.coin_activity_log import CoinActivityLog
from models.coin_daily_report import CoinDailyReport
from models.coin_trade_result import CoinTradeResult
from repositories.coin_daily_report_repository import CoinDailyReportRepository
from services.activity_logger import activity_logger
from trading.account_manager import account_manager
from trading.bithumb_client import bithumb_client
from trading.enums import ActivityPhase, ActivityType, Tier1Profile
from trading.market_profile import MARKET_SCOPE_CRYPTO
from trading.risk_policy import normalize_crypto_regime
from util.time_util import KST, ensure_kst, now_kst


class CoinReportGenerationError(RuntimeError):
    """코인 회고 리포트 생성에 필요한 입력이 부족할 때 발생한다."""

    def __init__(
        self,
        message: str,
        *,
        user_message: str | None = None,
        detail: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.user_message = user_message or message
        self.detail = detail or {}


class CoinDailyReportService:
    """코인 자동 체크포인트 회고 리포트 생성기"""

    async def generate_checkpoint_report(
        self,
        *,
        market: str = "BITHUMB",
        report_source: str = "AUTO_PRE_CYCLE",
        trigger_reason: str | None = None,
        applied_cycle_id: str | None = None,
        market_regime: str | None = None,
        market_context: str | None = None,
        raise_on_error: bool = False,
    ) -> CoinDailyReport | None:
        """직전 구간 코인 회고 리포트를 생성한다."""
        period_ended_at = now_kst()
        report_date = period_ended_at.date()

        with activity_logger.context(market_scope=MARKET_SCOPE_CRYPTO, trading_date=report_date):
            try:
                async with AsyncSessionLocal() as session:
                    repo = CoinDailyReportRepository(session)
                    if applied_cycle_id:
                        existing = await repo.get_by_applied_cycle_id(applied_cycle_id)
                        if existing:
                            return existing

                    previous_report = await repo.get_latest_before(period_ended_at)
                    period_started_at = self._resolve_period_start(previous_report, report_date)

                await activity_logger.log(
                    ActivityType.REPORT,
                    ActivityPhase.START,
                    (
                        f"📘 [CRYPTO] 체크포인트 회고 리포트 생성 시작 "
                        f"({report_source}, {period_started_at.strftime('%m/%d %H:%M')} ~ "
                        f"{period_ended_at.strftime('%m/%d %H:%M')})"
                    ),
                    cycle_id=applied_cycle_id,
                    detail={
                        "report_source": report_source,
                        "trigger_reason": trigger_reason,
                        "period_started_at": period_started_at.isoformat(),
                        "period_ended_at": period_ended_at.isoformat(),
                    },
                )

                account_snapshot = await self._load_account_snapshot(market)
                overview_snapshot = await self._load_market_overview(market)
                if not overview_snapshot["ok"]:
                    raise CoinReportGenerationError(
                        overview_snapshot["error"] or "시장 개요 조회 실패",
                        user_message=self._build_market_overview_user_message(overview_snapshot),
                        detail={
                            "report_source": report_source,
                            "trigger_reason": trigger_reason,
                            "period_started_at": period_started_at.isoformat(),
                            "period_ended_at": period_ended_at.isoformat(),
                            "error_stage": overview_snapshot.get("error_stage"),
                            "overview_error": overview_snapshot.get("error_detail"),
                            "connectivity": overview_snapshot.get("connectivity"),
                        },
                    )
                activity_snapshot = await self._load_activity_snapshot(period_started_at, period_ended_at)
                prompt = self._build_prompt(
                    report_source=report_source,
                    applied_cycle_id=applied_cycle_id,
                    market_regime=market_regime,
                    market_context=market_context,
                    previous_report=previous_report,
                    period_started_at=period_started_at,
                    period_ended_at=period_ended_at,
                    account_snapshot=account_snapshot,
                    overview_snapshot=overview_snapshot,
                    activity_snapshot=activity_snapshot,
                )

                result_text, provider = await llm_factory.generate_tier1(
                    prompt,
                    system_prompt=CRYPTO_CYCLE_REVIEW_SYSTEM,
                    profile=Tier1Profile.ANALYSIS,
                    scope=MARKET_SCOPE_CRYPTO,
                    phase="report",
                    cycle_id=applied_cycle_id,
                )
                parsed = self._parse_json(result_text)

                async with AsyncSessionLocal() as session:
                    async with session.begin():
                        report = CoinDailyReport(
                            report_date=report_date,
                            report_source=report_source,
                            trigger_reason=trigger_reason,
                            applied_cycle_id=applied_cycle_id,
                            period_started_at=period_started_at,
                            period_ended_at=period_ended_at,
                            total_cycles=activity_snapshot["total_cycles"],
                            total_analyses=activity_snapshot["total_analyses"],
                            total_recommendations=activity_snapshot["total_recommendations"],
                            total_orders=activity_snapshot["total_orders"],
                            buy_count=activity_snapshot["buy_count"],
                            sell_count=activity_snapshot["sell_count"],
                            win_count=activity_snapshot["win_count"],
                            loss_count=activity_snapshot["loss_count"],
                            total_pnl=activity_snapshot["total_pnl"],
                            unrealized_pnl=account_snapshot["unrealized_pnl"],
                            open_position_count=account_snapshot["open_position_count"],
                            total_24h_volume=overview_snapshot["total_24h_volume"],
                            btc_dominance=overview_snapshot["btc_dominance"],
                            market_regime=self._resolve_market_regime(
                                parsed=parsed,
                                market_regime=market_regime,
                                previous_report=previous_report,
                            ),
                            market_summary=self._parsed_text(
                                parsed,
                                "market_summary",
                                fallback="체크포인트 시장 요약 생성 실패",
                            ),
                            performance_review=self._parsed_text(
                                parsed,
                                "performance_review",
                                fallback=f"활동 {activity_snapshot['activity_count']}건 기록됨",
                            ),
                            lessons_learned=self._resolve_lessons_learned(parsed),
                            next_day_plan=self._resolve_next_cycle_plan(parsed),
                            top_picks=json.dumps(parsed.get("top_picks", []) if parsed else [], ensure_ascii=False),
                            strategy_stats=json.dumps(
                                {
                                    "trade_evaluation": parsed.get("trade_evaluation", {}) if parsed else {},
                                    "success_patterns": parsed.get("success_patterns", []) if parsed else [],
                                    "failure_patterns": parsed.get("failure_patterns", []) if parsed else [],
                                    "feedback_for_next_cycle": parsed.get("feedback_for_next_cycle", {}) if parsed else {},
                                    "risk_alerts": parsed.get("risk_alerts", []) if parsed else [],
                                    "activity_counts": activity_snapshot["activity_counts"],
                                },
                                ensure_ascii=False,
                                default=str,
                            ),
                        )
                        session.add(report)

                if parsed:
                    await trading_rule_engine.generate_rules_from_review(
                        parsed,
                        report_date=report_date,
                        market_scope=MARKET_SCOPE_CRYPTO,
                    )

                await activity_logger.log(
                    ActivityType.REPORT,
                    ActivityPhase.COMPLETE,
                    (
                        f"📘 [CRYPTO] 체크포인트 회고 리포트 생성 완료 "
                        f"({report_source}) | 실현 {activity_snapshot['total_pnl']:+,.0f}원 "
                        f"| 미실현 {account_snapshot['unrealized_pnl']:+,.0f}원"
                    ),
                    cycle_id=applied_cycle_id,
                    detail={
                        "report_source": report_source,
                        "trigger_reason": trigger_reason,
                        "period_started_at": period_started_at.isoformat(),
                        "period_ended_at": period_ended_at.isoformat(),
                        "total_cycles": activity_snapshot["total_cycles"],
                        "buy_count": activity_snapshot["buy_count"],
                        "sell_count": activity_snapshot["sell_count"],
                        "total_pnl": activity_snapshot["total_pnl"],
                        "unrealized_pnl": account_snapshot["unrealized_pnl"],
                    },
                )
                logger.info(
                    "[CRYPTO] 체크포인트 회고 리포트 생성 완료: {} {}",
                    report_source,
                    period_ended_at.isoformat(),
                )

                async with AsyncSessionLocal() as session:
                    repo = CoinDailyReportRepository(session)
                    return await repo.get_by_applied_cycle_id(applied_cycle_id) if applied_cycle_id else await repo.get_latest()
            except Exception as e:
                error_detail = {
                    "report_source": report_source,
                    "trigger_reason": trigger_reason,
                }
                if isinstance(e, CoinReportGenerationError) and e.detail:
                    error_detail.update(e.detail)

                logger.error("[CRYPTO] 체크포인트 회고 리포트 생성 실패: {}", str(e))
                await activity_logger.log(
                    ActivityType.REPORT,
                    ActivityPhase.ERROR,
                    f"❌ [CRYPTO] 체크포인트 회고 리포트 생성 실패: {str(e)[:120]}",
                    cycle_id=applied_cycle_id,
                    detail=error_detail,
                    error_message=str(e),
                )
                if raise_on_error:
                    raise
                return None

    @staticmethod
    def _resolve_period_start(previous_report: CoinDailyReport | None, report_date) -> datetime:
        if previous_report and previous_report.period_ended_at:
            return ensure_kst(previous_report.period_ended_at)
        return datetime.combine(report_date, time.min, tzinfo=KST)

    async def _load_account_snapshot(self, market: str) -> dict[str, object]:
        total_asset = 0.0
        cash = 0.0
        coin_value = 0.0
        unrealized_pnl = 0.0
        holdings_summary = "보유 코인 없음"
        open_position_count = 0

        try:
            balance, holdings = await account_manager.get_account_snapshot(market)
            total_asset = float(balance.total_asset or 0.0)
            cash = float(balance.cash or 0.0)
            coin_value = sum(float(h.current_price or 0.0) * float(h.quantity or 0.0) for h in holdings)
            unrealized_pnl = sum(float(h.pnl or 0.0) for h in holdings)
            open_position_count = len(holdings)
            holdings_summary = self._format_holdings_summary(holdings)
        except Exception as e:
            logger.warning("[CRYPTO] 계좌 스냅샷 조회 실패 (리포트 계속): {}", str(e))

        return {
            "total_asset": total_asset,
            "cash": cash,
            "coin_value": coin_value,
            "unrealized_pnl": unrealized_pnl,
            "open_position_count": open_position_count,
            "holdings_summary": holdings_summary,
        }

    async def _load_market_overview(self, market: str) -> dict[str, object]:
        total_24h_volume = 0.0
        btc_dominance = None
        market_overview_summary = "시장 개요 데이터 없음"
        error_message = None
        error_stage = None
        error_detail: dict[str, object] = {}
        connectivity = None

        try:
            overview = await bithumb_client.get_market_overview(market)
            items = overview.data.get("items", []) if overview.success and overview.data else []
            if items:
                total_24h_volume = sum(float(item.get("trade_value") or 0.0) for item in items)
                btc_item = next((item for item in items if str(item.get("symbol") or "").upper() == "BTC"), None)
                if btc_item and total_24h_volume > 0:
                    btc_dominance = round((float(btc_item.get("trade_value") or 0.0) / total_24h_volume) * 100, 2)
                market_overview_summary = self._format_market_overview_summary(items)
            else:
                error_message = overview.error or "시장 개요 응답이 비어 있습니다"
                if isinstance(overview.data, dict):
                    error_stage = str(overview.data.get("error_stage") or "") or None
                    error_detail = dict(overview.data)
                if not error_stage:
                    error_stage = "overview_empty"
        except Exception as e:
            error_message = str(e)
            error_stage = "market_overview"

        if error_message:
            connectivity = await bithumb_client.get_connectivity_status(force=True)
            if not connectivity.get("dns_api_ok"):
                error_stage = "dns_resolution"
                error_message = f"빗썸 Public API DNS 해석 실패: {connectivity.get('last_error') or error_message}"
            elif not connectivity.get("market_catalog_ok"):
                error_stage = "market_catalog"
                error_message = f"빗썸 마켓 카탈로그 조회 실패: {connectivity.get('last_error') or error_message}"
            elif not connectivity.get("ticker_probe_ok"):
                error_stage = str(connectivity.get("last_error_stage") or "ticker_probe")
                error_message = f"빗썸 ticker probe 호출 실패: {connectivity.get('last_error') or error_message}"
            elif error_stage == "ticker_batch":
                error_message = f"빗썸 시장 개요 ticker batch 조회 실패: {error_message}"
            logger.warning("[CRYPTO] 시장 개요 조회 실패: {}", error_message)

        return {
            "ok": error_message is None,
            "total_24h_volume": total_24h_volume,
            "btc_dominance": btc_dominance,
            "market_overview_summary": market_overview_summary,
            "error": error_message,
            "error_stage": error_stage,
            "error_detail": error_detail,
            "connectivity": connectivity,
        }

    @staticmethod
    def _build_market_overview_user_message(overview_snapshot: dict[str, object]) -> str:
        error = str(overview_snapshot.get("error") or "시장 개요 조회 실패")
        stage = str(overview_snapshot.get("error_stage") or "")
        if stage == "dns_resolution":
            return f"빗썸 DNS 해석 실패로 코인 리포트를 생성하지 않았습니다: {error}"
        if stage == "ticker_batch":
            return f"빗썸 시장 개요 조회 실패로 코인 리포트를 생성하지 않았습니다: {error}"
        if stage == "market_catalog":
            return f"빗썸 마켓 카탈로그 조회 실패로 코인 리포트를 생성하지 않았습니다: {error}"
        return f"시장 개요 조회 실패로 코인 리포트를 생성하지 않았습니다: {error}"

    async def _load_activity_snapshot(
        self,
        period_started_at: datetime,
        period_ended_at: datetime,
    ) -> dict[str, object]:
        async with AsyncSessionLocal() as session:
            activity_rows = list(
                (
                    await session.execute(
                        select(CoinActivityLog)
                        .where(CoinActivityLog.created_at >= period_started_at)
                        .where(CoinActivityLog.created_at <= period_ended_at)
                        .order_by(CoinActivityLog.created_at.desc(), CoinActivityLog.id.desc())
                        .limit(400)
                    )
                ).scalars().all()
            )

            opened_trades = list(
                (
                    await session.execute(
                        select(CoinTradeResult)
                        .where(CoinTradeResult.side == "BUY")
                        .where(CoinTradeResult.entry_at.is_not(None))
                        .where(CoinTradeResult.entry_at >= period_started_at)
                        .where(CoinTradeResult.entry_at <= period_ended_at)
                        .order_by(CoinTradeResult.entry_at.desc(), CoinTradeResult.created_at.desc())
                    )
                ).scalars().all()
            )
            completed_trades = list(
                (
                    await session.execute(
                        select(CoinTradeResult)
                        .where(CoinTradeResult.side == "BUY")
                        .where(CoinTradeResult.exit_at.is_not(None))
                        .where(CoinTradeResult.exit_at >= period_started_at)
                        .where(CoinTradeResult.exit_at <= period_ended_at)
                        .order_by(CoinTradeResult.exit_at.desc(), CoinTradeResult.created_at.desc())
                    )
                ).scalars().all()
            )

        activity_counts: dict[str, int] = {}
        total_cycles = 0
        total_analyses = 0
        total_recommendations = 0
        for activity in activity_rows:
            activity_type = str(activity.activity_type or "")
            activity_counts[activity_type] = activity_counts.get(activity_type, 0) + 1
            if activity_type == "CYCLE" and str(activity.phase or "") == "COMPLETE":
                total_cycles += 1
            if activity_type == "TIER1_ANALYSIS" and str(activity.phase or "") == "COMPLETE":
                total_analyses += 1
            if activity_type == "DECISION" and str(activity.phase or "") == "COMPLETE":
                total_recommendations += 1

        buy_count = len(opened_trades)
        sell_count = len(completed_trades)
        total_pnl = sum(float(trade.pnl or 0.0) for trade in completed_trades)
        win_count = sum(1 for trade in completed_trades if bool(trade.is_win))
        loss_count = sum(1 for trade in completed_trades if not bool(trade.is_win))

        return {
            "activity_count": len(activity_rows),
            "activity_counts": activity_counts,
            "recent_activities": self._format_recent_activities(activity_rows),
            "trade_summary": self._format_trade_summary(opened_trades, completed_trades),
            "total_cycles": total_cycles,
            "total_analyses": total_analyses,
            "total_recommendations": total_recommendations,
            "total_orders": buy_count + sell_count,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "win_count": win_count,
            "loss_count": loss_count,
            "total_pnl": total_pnl,
        }

    def _build_prompt(
        self,
        *,
        report_source: str,
        applied_cycle_id: str | None,
        market_regime: str | None,
        market_context: str | None,
        previous_report: CoinDailyReport | None,
        period_started_at: datetime,
        period_ended_at: datetime,
        account_snapshot: dict[str, object],
        overview_snapshot: dict[str, object],
        activity_snapshot: dict[str, object],
    ) -> str:
        resolved_regime = self._resolve_market_regime(
            parsed=None,
            market_regime=market_regime,
            previous_report=previous_report,
        ) or "UNKNOWN"
        resolved_context = str(
            market_context
            or (previous_report.market_summary if previous_report else "")
            or "최근 시장 컨텍스트 없음"
        )
        btc_dominance = overview_snapshot["btc_dominance"]
        btc_dominance_text = (
            f"{float(btc_dominance):.2f}%"
            if isinstance(btc_dominance, (int, float))
            else "데이터 없음"
        )

        return CRYPTO_CYCLE_REVIEW_PROMPT.format(
            period_started_at=period_started_at.strftime("%Y-%m-%d %H:%M KST"),
            period_ended_at=period_ended_at.strftime("%Y-%m-%d %H:%M KST"),
            report_source_label="자동 회고" if report_source == "AUTO_PRE_CYCLE" else "수동 생성",
            applied_cycle_label=applied_cycle_id or "즉시 반영",
            market_regime=resolved_regime,
            market_context=resolved_context,
            total_asset=float(account_snapshot["total_asset"] or 0.0),
            cash=float(account_snapshot["cash"] or 0.0),
            coin_value=float(account_snapshot["coin_value"] or 0.0),
            unrealized_pnl=float(account_snapshot["unrealized_pnl"] or 0.0),
            open_position_count=int(account_snapshot["open_position_count"] or 0),
            holdings_summary=str(account_snapshot["holdings_summary"]),
            total_24h_volume=float(overview_snapshot["total_24h_volume"] or 0.0),
            btc_dominance_text=btc_dominance_text,
            market_overview_summary=str(overview_snapshot["market_overview_summary"]),
            total_cycles=int(activity_snapshot["total_cycles"] or 0),
            total_analyses=int(activity_snapshot["total_analyses"] or 0),
            total_recommendations=int(activity_snapshot["total_recommendations"] or 0),
            buy_count=int(activity_snapshot["buy_count"] or 0),
            sell_count=int(activity_snapshot["sell_count"] or 0),
            win_count=int(activity_snapshot["win_count"] or 0),
            loss_count=int(activity_snapshot["loss_count"] or 0),
            total_pnl=float(activity_snapshot["total_pnl"] or 0.0),
            trade_summary=str(activity_snapshot["trade_summary"]),
            recent_activities=str(activity_snapshot["recent_activities"]),
        )

    @staticmethod
    def _format_holdings_summary(holdings: list) -> str:
        if not holdings:
            return "보유 코인 없음"

        lines = []
        for holding in holdings[:8]:
            symbol = str(getattr(holding, "symbol", "") or "")
            quantity = float(getattr(holding, "quantity", 0.0) or 0.0)
            current_price = float(getattr(holding, "current_price", 0.0) or 0.0)
            pnl = float(getattr(holding, "pnl", 0.0) or 0.0)
            lines.append(
                f"- {symbol}: {quantity:.8f}개 | 현재가 {current_price:,.0f}원 | 손익 {pnl:+,.0f}원"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_market_overview_summary(items: list[dict]) -> str:
        if not items:
            return "시장 개요 데이터 없음"

        by_trade_value = sorted(items, key=lambda item: float(item.get("trade_value") or 0.0), reverse=True)[:5]
        by_change = sorted(items, key=lambda item: float(item.get("change_rate") or 0.0), reverse=True)
        top_gainers = by_change[:3]
        top_losers = list(reversed(by_change[-3:])) if len(by_change) >= 3 else []

        lines = [
            "거래대금 상위:",
            *[
                f"- {item.get('symbol', '')}: 거래대금 {float(item.get('trade_value') or 0.0):,.0f}원"
                for item in by_trade_value
            ],
        ]
        if top_gainers:
            gainers = ", ".join(
                f"{item.get('symbol', '')} {float(item.get('change_rate') or 0.0):+.2f}%"
                for item in top_gainers
            )
            lines.append(f"급등 상위: {gainers}")
        if top_losers:
            losers = ", ".join(
                f"{item.get('symbol', '')} {float(item.get('change_rate') or 0.0):+.2f}%"
                for item in top_losers
            )
            lines.append(f"급락 상위: {losers}")
        return "\n".join(lines)

    @staticmethod
    def _format_recent_activities(activities: list[CoinActivityLog]) -> str:
        if not activities:
            return "활동 없음"
        lines = []
        for activity in activities[:30]:
            lines.append(
                f"[{activity.activity_type}/{activity.phase}] {activity.summary}"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_trade_summary(opened_trades: list[CoinTradeResult], completed_trades: list[CoinTradeResult]) -> str:
        lines = []
        if opened_trades:
            lines.append("진입:")
            for trade in opened_trades[:5]:
                lines.append(
                    f"- {trade.symbol}: 진입 {float(trade.entry_price or 0.0):,.0f}원 / 수량 {float(trade.quantity or 0.0):.8f}"
                )
        if completed_trades:
            lines.append("청산:")
            for trade in completed_trades[:5]:
                lines.append(
                    f"- {trade.symbol}: 손익 {float(trade.pnl or 0.0):+,.0f}원 ({float(trade.return_pct or 0.0):+.2f}%)"
                )
        return "\n".join(lines) if lines else "체결/청산 없음"

    @staticmethod
    def _parse_json(text: str) -> dict | None:
        result = parse_llm_json(text)
        return result or None

    @staticmethod
    def _parsed_text(parsed: dict | None, key: str, *, fallback: str = "") -> str:
        if not parsed:
            return fallback
        value = str(parsed.get(key) or "").strip()
        return value or fallback

    @staticmethod
    def _resolve_lessons_learned(parsed: dict | None) -> str:
        if not parsed:
            return ""
        lessons = str(parsed.get("lessons_learned") or "").strip()
        if lessons:
            return lessons
        feedback = parsed.get("feedback_for_next_cycle", {})
        return str(feedback.get("system_improvement") or "").strip()

    @staticmethod
    def _resolve_next_cycle_plan(parsed: dict | None) -> str:
        if not parsed:
            return ""
        return str(parsed.get("next_cycle_plan") or parsed.get("next_day_plan") or "").strip()

    @staticmethod
    def _resolve_market_regime(
        *,
        parsed: dict | None,
        market_regime: str | None,
        previous_report: CoinDailyReport | None,
    ) -> str:
        parsed_regime = normalize_crypto_regime(parsed.get("market_regime")) if parsed else ""
        if parsed_regime:
            return parsed_regime
        current_regime = normalize_crypto_regime(market_regime)
        if current_regime:
            return current_regime
        return normalize_crypto_regime(previous_report.market_regime if previous_report else "")


coin_daily_report_service = CoinDailyReportService()
