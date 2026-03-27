"""AI 자율 한도 결정 - 계좌 상태 + 성과 + 리스크 성향 기반"""
from loguru import logger

from core.json_utils import parse_llm_json

from analysis.feedback.performance_tracker import PerformanceTracker
from analysis.llm.llm_factory import llm_factory
from analysis.llm.prompts.risk_tuning import (
    RISK_APPETITE_GUIDELINES,
    RISK_TUNING_PROMPT,
    RISK_TUNING_SYSTEM,
)
from core.config import settings
from core.database import AsyncSessionLocal
from services.activity_logger import activity_logger
from services.report_display import format_report_amount
from trading.account_manager import account_manager
from trading.enums import ActivityPhase, ActivityType, Tier1Profile
from trading.market_profile import is_crypto_market, is_us_market, market_scope, normalize_market
from trading.models import AccountBalance
from trading.quantity_policy import normalize_quantity


class AIRiskTuner:
    """AI가 계좌 상태 + 성과 + 리스크 성향을 분석하여 한도를 자율 결정"""

    @staticmethod
    def _format_daily_trade_limit(limit: int) -> str:
        return "무제한" if int(limit or 0) == 0 else f"{int(limit)}회"

    @staticmethod
    def _display_currency(market: str) -> str:
        return "USD" if is_us_market(normalize_market(market)) else "KRW"

    @staticmethod
    def _format_order_limit(amount: float, currency: str) -> str:
        if float(amount or 0.0) <= 0:
            return "무제한"
        return format_report_amount(amount, currency)

    @staticmethod
    def _market_defaults(market: str) -> dict[str, float | int]:
        market_code = normalize_market(market)
        if is_crypto_market(market_code):
            return {
                "max_daily_trades": settings.CRYPTO_MAX_DAILY_TRADES,
                "max_single_order_krw": settings.CRYPTO_MAX_SINGLE_ORDER_KRW,
                "min_buy_quantity": settings.CRYPTO_MIN_BUY_QUANTITY,
                "max_position_pct": settings.CRYPTO_MAX_POSITION_PCT,
                "min_cash_ratio": settings.CRYPTO_MIN_CASH_RATIO,
            }
        return {
            "max_daily_trades": settings.MAX_DAILY_TRADES,
            "max_single_order_krw": settings.MAX_SINGLE_ORDER_KRW,
            "min_buy_quantity": settings.MIN_BUY_QUANTITY,
            "max_position_pct": 25.0,
            "min_cash_ratio": 0.05,
        }

    def _build_balance_display_metrics(self, balance: AccountBalance, market: str) -> dict[str, float | str]:
        currency = self._display_currency(market)

        if currency == "USD":
            total_asset = float(getattr(balance, "total_asset_foreign", 0.0) or 0.0)
            cash = float(
                getattr(balance, "effective_cash_foreign", 0.0)
                or getattr(balance, "cash_foreign", 0.0)
                or 0.0
            )
            stock_value = float(getattr(balance, "stock_value_foreign", 0.0) or 0.0)
            operating_cash = float(getattr(balance, "operating_cash_foreign", 0.0) or 0.0)
            if total_asset <= 0 and (cash > 0 or stock_value > 0):
                total_asset = cash + stock_value
            if operating_cash <= 0 and total_asset > 0:
                operating_cash = max(total_asset - stock_value, 0.0)
            buyable_amount = max(
                min(cash, operating_cash if operating_cash > 0 else cash),
                0.0,
            )
            total_pnl = float(getattr(balance, "raw_total_pnl", 0.0) or 0.0)
            total_pnl_rate = float(getattr(balance, "raw_total_pnl_rate", 0.0) or 0.0)
            purchase_amount = max(stock_value - total_pnl, 0.0)
        else:
            total_asset = float(getattr(balance, "total_asset", 0.0) or 0.0)
            cash = float(
                getattr(balance, "effective_cash", 0.0)
                or getattr(balance, "cash", 0.0)
                or 0.0
            )
            stock_value = float(getattr(balance, "stock_value", 0.0) or 0.0)
            buyable_amount = max(total_asset - stock_value, 0.0)
            total_pnl = float(getattr(balance, "total_pnl", 0.0) or 0.0)
            total_pnl_rate = float(getattr(balance, "total_pnl_rate", 0.0) or 0.0)
            purchase_amount = float(getattr(balance, "purchase_amount", 0.0) or 0.0)

        cash_ratio = (cash / total_asset) * 100 if total_asset > 0 else 0.0
        return {
            "display_currency": currency,
            "cash_ratio": cash_ratio,
            "total_pnl_rate": total_pnl_rate,
            "total_asset_text": format_report_amount(total_asset, currency),
            "cash_text": format_report_amount(cash, currency),
            "purchase_amount_text": format_report_amount(purchase_amount, currency),
            "stock_value_text": format_report_amount(stock_value, currency),
            "buyable_amount_text": format_report_amount(buyable_amount, currency),
            "total_pnl_text": format_report_amount(total_pnl, currency, signed=True),
        }

    def _format_performance_summary(self, overall, market: str) -> str:
        currency = self._display_currency(market)
        total_pnl = (
            float(getattr(overall, "raw_total_pnl", 0.0) or 0.0)
            if currency == "USD"
            else float(getattr(overall, "total_pnl", 0.0) or 0.0)
        )
        return (
            f"총 {overall.total_trades}거래, "
            f"승률 {overall.win_rate * 100:.1f}%, "
            f"총손익 {format_report_amount(total_pnl, currency, signed=True)}, "
            f"평균수익률 {overall.avg_return:+.2f}%"
        )

    async def compute_limits(
        self,
        market: str | None = None,
        risk_appetite: str = "MODERATE",
        cycle_id: str | None = None,
        balance: AccountBalance | None = None,
    ) -> dict:
        """적정 한도 계산"""
        target = normalize_market(market or settings.primary_market_code)
        defaults = self._market_defaults(target)
        timer = activity_logger.timer()

        try:
            # 1. 계좌 잔고 조회
            if balance is None:
                balance = await account_manager.get_balance(target)
            if not balance.is_valid:
                reason = balance.status_message or "계좌 잔고 조회 실패"
                limits = self._blocked_limits(reason, target)
                elapsed = activity_logger.elapsed_ms(timer)
                logger.warning("[{}] AI 한도 결정 스킵: {}", target, reason)
                await activity_logger.log(
                    ActivityType.RISK_TUNING, ActivityPhase.SKIP,
                    f"🎯 AI 한도 결정 스킵 ({risk_appetite}): 계좌 잔고 조회 실패",
                    cycle_id=cycle_id,
                    detail={
                        **limits,
                        "blocked_reason": reason,
                        "market": target,
                    },
                    execution_time_ms=elapsed,
                )
                return limits

            # 2. 최근 매매 성과 조회
            performance_summary = "매매 이력 없음"
            try:
                async with AsyncSessionLocal() as session:
                    tracker = PerformanceTracker(session)
                    stats = await tracker.get_overall_stats(market_scope=market_scope(target))
                    overall = stats.get("overall")
                    if overall and overall.total_trades > 0:
                        performance_summary = self._format_performance_summary(overall, target)
            except Exception as e:
                logger.warning("성과 데이터 조회 실패: {}", str(e))

            # 3. 리스크 성향 가이드라인
            risk_guideline = RISK_APPETITE_GUIDELINES.get(
                risk_appetite, RISK_APPETITE_GUIDELINES["MODERATE"]
            )
            display_metrics = self._build_balance_display_metrics(balance, target)

            cash_interpretation_note = (
                "브로커 잔고의 현금은 계좌 수준 참고치입니다. 이 단계에서는 계좌 전체의 집중도와 최소 현금 비율을 우선 조정하세요."
            )
            if settings.is_paper_trading and is_us_market(target):
                cash_interpretation_note = (
                    "미국장 모의투자에서는 present-balance 현금이 0으로 보일 수 있습니다. "
                    "이 단계에서는 broker cash 0만으로 신규 진입을 단정 차단하지 말고, "
                    "계좌 수준의 집중도와 최소 현금 비율을 우선 판단하세요. "
                    "실제 종목별 주문가능금액은 개별 분석 단계에서 inquire-psamount로 다시 확인합니다."
                )

            # 6. LLM에게 한도 요청
            prompt = RISK_TUNING_PROMPT.format(
                display_currency=display_metrics["display_currency"],
                total_asset_text=display_metrics["total_asset_text"],
                cash_text=display_metrics["cash_text"],
                purchase_amount_text=display_metrics["purchase_amount_text"],
                stock_value_text=display_metrics["stock_value_text"],
                buyable_amount_text=display_metrics["buyable_amount_text"],
                cash_ratio=display_metrics["cash_ratio"],
                total_pnl_text=display_metrics["total_pnl_text"],
                total_pnl_rate=display_metrics["total_pnl_rate"],
                performance_summary=performance_summary,
                risk_guideline=risk_guideline,
                max_daily_trades=defaults["max_daily_trades"],
                min_buy_quantity=defaults["min_buy_quantity"],
                cash_interpretation_note=cash_interpretation_note,
            )

            result_text, provider = await llm_factory.generate_tier1(
                prompt,
                system_prompt=RISK_TUNING_SYSTEM,
                profile=Tier1Profile.ANALYSIS,
                scope=market_scope(target),
                phase="cycle",
            )

            # 6. 파싱 + clamp (settings 상한선으로 제한)
            parsed = self._parse_json(result_text)
            if not parsed:
                logger.warning("AI 한도 파싱 실패, 기본값 사용")
                return self._default_limits(target)

            limits = self._clamp_limits(
                parsed,
                target,
                exchange_rate_to_krw=float(getattr(balance, "exchange_rate_to_krw", 1.0) or 1.0),
            )
            elapsed = activity_logger.elapsed_ms(timer)
            display_currency = str(limits.get("display_currency") or self._display_currency(target)).upper()
            order_limit_amount = (
                float(limits.get("max_single_order_foreign") or 0.0)
                if display_currency == "USD"
                else float(limits.get("max_single_order_krw") or 0.0)
            )

            await activity_logger.log(
                ActivityType.RISK_TUNING, ActivityPhase.COMPLETE,
                f"\U0001f3af AI 한도 결정 ({risk_appetite}): "
                f"일일거래 {self._format_daily_trade_limit(limits['max_daily_trades'])}, "
                f"주문한도 {self._format_order_limit(order_limit_amount, display_currency)}",
                cycle_id=cycle_id,
                detail=limits,
                llm_provider=provider,
                llm_tier="TIER1",
                execution_time_ms=elapsed,
            )

            return limits

        except Exception as e:
            logger.error("AI 한도 결정 실패: {}", str(e))
            return self._default_limits(target)

    def _clamp_limits(self, parsed: dict, market: str, *, exchange_rate_to_krw: float = 1.0) -> dict:
        """AI 결정값 정규화 (최소 안전값만 적용, 상한선 없음)"""
        defaults = self._market_defaults(market)
        display_currency = self._display_currency(market)
        amount_value = parsed.get("max_single_order_amount")
        amount_currency = str(parsed.get("max_single_order_currency") or display_currency).upper()

        if amount_value is None:
            max_single_order_krw = max(
                int(parsed.get("max_single_order_krw", defaults["max_single_order_krw"])),
                0,
            )
            max_single_order_foreign = (
                (max_single_order_krw / exchange_rate_to_krw)
                if display_currency == "USD" and exchange_rate_to_krw > 0
                else None
            )
        else:
            normalized_amount = max(float(amount_value or 0.0), 0.0)
            if amount_currency == "USD":
                max_single_order_foreign = normalized_amount
                if display_currency == "USD" and exchange_rate_to_krw > 0:
                    max_single_order_krw = int(round(normalized_amount * exchange_rate_to_krw))
                else:
                    max_single_order_krw = int(round(normalized_amount))
            else:
                max_single_order_krw = int(round(normalized_amount))
                if display_currency == "USD" and exchange_rate_to_krw > 0:
                    max_single_order_foreign = normalized_amount / exchange_rate_to_krw
                else:
                    max_single_order_foreign = None

        limits = {
            "max_daily_trades": max(
                int(parsed.get("max_daily_trades", defaults["max_daily_trades"])), 0
            ),  # 0 = 무제한
            "max_single_order_krw": max(max_single_order_krw, 0),
            "min_buy_quantity": normalize_quantity(
                parsed.get("min_buy_quantity", defaults["min_buy_quantity"]),
                market,
            ),
            "max_position_pct": max(
                float(parsed.get("max_position_pct", defaults["max_position_pct"])),
                5.0,
            ),  # 상한선 없음
            "min_cash_ratio": max(
                float(parsed.get("min_cash_ratio", defaults["min_cash_ratio"])),
                float(defaults["min_cash_ratio"]),
            ),
            "display_currency": display_currency,
            "reasoning": parsed.get("reasoning", ""),
        }
        if display_currency == "USD":
            limits["max_single_order_foreign"] = max(float(max_single_order_foreign or 0.0), 0.0)
        return limits

    def _default_limits(self, market: str) -> dict:
        """기본 한도값 (AI 실패 시)"""
        defaults = self._market_defaults(market)
        return {
            "max_daily_trades": defaults["max_daily_trades"],  # 0 = 무제한
            "max_single_order_krw": defaults["max_single_order_krw"],  # 0 = 무제한
            "min_buy_quantity": defaults["min_buy_quantity"],
            "max_position_pct": defaults["max_position_pct"],
            "min_cash_ratio": defaults["min_cash_ratio"],
            "display_currency": self._display_currency(market),
            "reasoning": "AI 한도 결정 실패, 기본값 사용",
        }

    def _blocked_limits(self, reason: str, market: str) -> dict:
        """잔고 조회 실패 시 신규 매수를 사실상 차단하는 안전 한도"""
        defaults = self._market_defaults(market)
        return {
            "max_daily_trades": 1,
            "max_single_order_krw": 1,
            "min_buy_quantity": defaults["min_buy_quantity"],
            "max_position_pct": 5.0,
            "min_cash_ratio": 1.0,
            "display_currency": self._display_currency(market),
            "reasoning": f"계좌 잔고 조회 실패로 신규 매수를 차단했습니다: {reason}",
        }

    def _parse_json(self, text: str) -> dict | None:
        result = parse_llm_json(text)
        return result or None


ai_risk_tuner = AIRiskTuner()
