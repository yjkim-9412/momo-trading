"""리스크 관리 - 포지션/손절/비중/일일한도"""
from loguru import logger

from core.config import settings
from services.activity_logger import activity_logger
from strategy.signal import TradeSignal
from trading.enums import ActivityPhase, ActivityType, SignalAction
from trading.risk_policy import DEFAULT_RR_FLOOR, resolve_rr_floor as resolve_shared_rr_floor
from trading.product_policy import (
    build_product_context,
    classification_from_metadata,
    is_product_trade_allowed,
)


class RiskManager:
    """
    리스크 관리자
    - 포지션 크기 제한
    - 총 비중 제한
    - 손절 검사
    - 일일 매매 한도
    - 단일 주문 금액 한도
    """

    def __init__(self):
        self.max_daily_trades = settings.MAX_DAILY_TRADES
        self.max_single_order_krw = settings.MAX_SINGLE_ORDER_KRW
        self.min_cash_ratio = settings.MIN_CASH_RATIO  # 기본 5%

    RR_FLOOR = dict(DEFAULT_RR_FLOOR)

    @classmethod
    def resolve_rr_floor(
        cls,
        market_regime: str,
        rr_floor_overrides: dict[str, float] | None = None,
    ) -> float:
        return resolve_shared_rr_floor(
            market_regime,
            rr_floor_overrides,
            defaults=cls.RR_FLOOR,
        )

    @staticmethod
    def _unit_price_krw(signal: TradeSignal) -> float:
        """시그널 단가를 KRW 기준으로 환산"""
        price = signal.suggested_price or 0
        metadata = signal.metadata or {}
        price_krw = metadata.get("price_krw")
        if isinstance(price_krw, (int, float)) and price_krw > 0:
            return float(price_krw)

        exchange_rate = metadata.get("exchange_rate_to_krw", 1.0)
        if isinstance(exchange_rate, (int, float)) and exchange_rate > 0:
            return price * float(exchange_rate)
        return price

    @staticmethod
    def _signal_product_context(signal: TradeSignal) -> dict:
        metadata = signal.metadata or {}
        market_code = str(metadata.get("market") or "KRX")
        return build_product_context(signal.symbol, market_code, metadata)

    @staticmethod
    def _format_daily_trade_progress(today_trade_count: int, effective_max_daily: int) -> str:
        if effective_max_daily <= 0:
            return f"{today_trade_count}/무제한"
        return f"{today_trade_count}/{effective_max_daily}"

    async def check(
        self,
        signal: TradeSignal,
        portfolio_cash: float,
        portfolio_budget: float,
        today_trade_count: int,
        current_holding_count: int,
        current_position: dict | None = None,
        max_position_pct: float = 20.0,
        cycle_id: str | None = None,
        dynamic_limits: dict | None = None,
        market_regime: str = "",
        rr_floor_overrides: dict[str, float] | None = None,
    ) -> dict:
        """
        리스크 검사

        Args:
            dynamic_limits: AI가 결정한 동적 한도 (있으면 기본값 대신 사용)

        Returns:
            {"approved": bool, "reason": str, "adjusted_quantity": int | None}
        """
        symbol = signal.symbol
        product_context = self._signal_product_context(signal)

        # 동적 한도 적용 (AI 결정값 또는 기본값)
        eff_max_daily = self.max_daily_trades
        eff_max_order = self.max_single_order_krw
        eff_min_qty = settings.MIN_BUY_QUANTITY
        eff_min_cash_ratio = self.min_cash_ratio
        eff_max_pos_pct = max_position_pct

        if dynamic_limits:
            eff_max_daily = dynamic_limits.get("max_daily_trades", eff_max_daily)
            eff_max_order = dynamic_limits.get("max_single_order_krw", eff_max_order)
            eff_min_qty = dynamic_limits.get("min_buy_quantity", eff_min_qty)
            eff_min_cash_ratio = dynamic_limits.get("min_cash_ratio", eff_min_cash_ratio)
            eff_max_pos_pct = dynamic_limits.get("max_position_pct", eff_max_pos_pct)

        # 매도는 기본적으로 허용
        if signal.action == SignalAction.SELL:
            result = {"approved": True, "reason": "매도 주문", "adjusted_quantity": None}
            await self._log_result(
                symbol, result, today_trade_count, cycle_id, product_context, eff_max_daily
            )
            return result

        # 매매 비활성화 검사
        if not settings.TRADING_ENABLED:
            result = {"approved": False, "reason": "매매가 비활성화되어 있습니다"}
            await self._log_result(
                symbol, result, today_trade_count, cycle_id, product_context, eff_max_daily
            )
            return result

        # 일일 매매 한도 검사 (0 = 무제한)
        if eff_max_daily > 0 and today_trade_count >= eff_max_daily:
            logger.warning("일일 매매 한도 초과: {}/{}", today_trade_count, eff_max_daily)
            result = {"approved": False, "reason": f"일일 매매 한도 초과 ({eff_max_daily}회)"}
            await self._log_result(
                symbol, result, today_trade_count, cycle_id, product_context, eff_max_daily
            )
            return result

        # 주문 금액 계산
        price = signal.suggested_price or 0
        quantity = signal.suggested_quantity or 0
        if price <= 0 or quantity <= 0:
            result = {"approved": False, "reason": "가격 또는 수량이 유효하지 않습니다"}
            await self._log_result(
                symbol, result, today_trade_count, cycle_id, product_context, eff_max_daily
            )
            return result

        metadata = signal.metadata or {}
        market_code = str(metadata.get("market") or "KRX")
        classification = classification_from_metadata(symbol, market_code, metadata)
        allowed, policy_reason = is_product_trade_allowed(
            classification,
            strategy_type=signal.strategy_type,
            session=str(metadata.get("session") or ""),
            side=signal.action.value,
        )
        if not allowed:
            result = {"approved": False, "reason": policy_reason}
            await self._log_result(
                symbol, result, today_trade_count, cycle_id, product_context, eff_max_daily
            )
            return result

        if classification.is_restricted:
            leverage_ratio = max(float(settings.US_LEVERAGE_MAX_SINGLE_ORDER_RATIO or 0), 0.0)
            if eff_max_order > 0 and leverage_ratio > 0:
                eff_max_order *= leverage_ratio
            if leverage_ratio > 0:
                eff_max_pos_pct *= leverage_ratio

        unit_price_krw = self._unit_price_krw(signal)
        total_amount = unit_price_krw * quantity
        current_position_value_krw = float((current_position or {}).get("current_value_krw") or 0.0)

        # 리스크:보상 비율 검사 (다른 조정 전에 먼저 확인)
        entry = signal.suggested_price or 0
        target = signal.target_price or 0
        stop = signal.stop_loss_price or 0

        if entry > 0 and target > 0 and stop > 0:
            reward = abs(target - entry)
            risk = abs(entry - stop)
            if risk > 0:
                rr_ratio = reward / risk
                min_rr = self.resolve_rr_floor(market_regime, rr_floor_overrides)
                if rr_ratio < min_rr:
                    result = {
                        "approved": False,
                        "reason": f"리스크:보상 비율 부족 ({rr_ratio:.1f}:1, 최소 {min_rr}:1 필요)",
                        "adjusted_quantity": None,
                    }
                    await self._log_result(
                        symbol, result, today_trade_count, cycle_id, product_context, eff_max_daily
                    )
                    return result

        requested_quantity = quantity
        adjustment_labels: list[str] = []

        def _apply_quantity_cap(capped_qty: int, label: str) -> tuple[bool, dict | None]:
            nonlocal quantity, total_amount
            if capped_qty >= quantity:
                return False, None
            if capped_qty < eff_min_qty:
                return True, {"approved": False, "reason": f"{label} 후 최소 수량 미달"}
            quantity = capped_qty
            total_amount = unit_price_krw * quantity
            adjustment_labels.append(label)
            return True, None

        # 단일 주문 금액 한도 (0이면 AI 자율 → 스킵)
        if eff_max_order > 0 and total_amount > eff_max_order:
            _, reject_result = _apply_quantity_cap(int(eff_max_order / unit_price_krw), "단일 주문 한도")
            if reject_result:
                await self._log_result(
                    symbol, reject_result, today_trade_count, cycle_id, product_context, eff_max_daily
                )
                return reject_result

        # 현금 부족 검사 (음수 현금 방어 포함)
        if portfolio_cash <= 0:
            result = {"approved": False, "reason": "가용 현금 없음"}
            await self._log_result(
                symbol, result, today_trade_count, cycle_id, product_context, eff_max_daily
            )
            return result

        if total_amount > portfolio_cash:
            _, reject_result = _apply_quantity_cap(int(portfolio_cash / unit_price_krw), "현금 부족")
            if reject_result:
                await self._log_result(
                    symbol, reject_result, today_trade_count, cycle_id, product_context, eff_max_daily
                )
                return reject_result

        # 최소 현금 비중 검사
        cash_after = portfolio_cash - total_amount
        if portfolio_budget > 0 and cash_after / portfolio_budget < eff_min_cash_ratio:
            max_spend = portfolio_cash - (portfolio_budget * eff_min_cash_ratio)
            if max_spend <= 0:
                result = {"approved": False, "reason": "현금 비중 최소 한도 미달"}
                await self._log_result(
                    symbol, result, today_trade_count, cycle_id, product_context, eff_max_daily
                )
                return result
            _, reject_result = _apply_quantity_cap(int(max_spend / unit_price_krw), "현금 비중 유지")
            if reject_result:
                await self._log_result(
                    symbol, reject_result, today_trade_count, cycle_id, product_context, eff_max_daily
                )
                return reject_result

        # 종목 비중 검사
        if portfolio_budget > 0:
            combined_position_pct = ((current_position_value_krw + total_amount) / portfolio_budget) * 100
            if combined_position_pct > eff_max_pos_pct:
                remaining_amount = max((portfolio_budget * eff_max_pos_pct / 100) - current_position_value_krw, 0.0)
                _, reject_result = _apply_quantity_cap(
                    int(remaining_amount / unit_price_krw) if unit_price_krw > 0 else 0,
                    "합산 비중 한도",
                )
                if reject_result:
                    reject_result["combined_position_pct"] = combined_position_pct
                    reject_result["current_position_value_krw"] = current_position_value_krw
                    await self._log_result(
                        symbol, reject_result, today_trade_count, cycle_id, product_context, eff_max_daily
                    )
                    return reject_result

        combined_position_pct = (
            ((current_position_value_krw + total_amount) / portfolio_budget) * 100
            if portfolio_budget > 0
            else 0.0
        )
        adjustment_reason = "리스크 검사 통과"
        adjusted_quantity = None
        if quantity != requested_quantity:
            adjusted_quantity = quantity
            adjustment_reason = (
                f"수량 조정 ({', '.join(adjustment_labels)}): {requested_quantity} → {quantity}"
            )
        result = {
            "approved": True,
            "reason": adjustment_reason,
            "adjusted_quantity": adjusted_quantity,
            "combined_position_pct": combined_position_pct,
            "current_position_value_krw": current_position_value_krw,
        }
        await self._log_result(
            symbol, result, today_trade_count, cycle_id, product_context, eff_max_daily
        )
        return result

    async def _log_result(
        self,
        symbol: str,
        result: dict,
        today_trade_count: int,
        cycle_id: str | None,
        product_context: dict | None = None,
        effective_max_daily: int | None = None,
    ) -> None:
        approved = result.get("approved", False)
        reason = result.get("reason", "")
        effective_limit = self.max_daily_trades if effective_max_daily is None else int(effective_max_daily)

        if approved:
            summary = (
                f"\U0001f6e1\ufe0f [{symbol}] 리스크 검사 통과"
                f"\n   일일거래: {self._format_daily_trade_progress(today_trade_count, effective_limit)} | {reason}"
            )
        else:
            summary = f"\U0001f6e1\ufe0f [{symbol}] 리스크 검사 미통과: {reason}"

        await activity_logger.log(
            ActivityType.RISK_CHECK, ActivityPhase.COMPLETE,
            summary,
            cycle_id=cycle_id,
            symbol=symbol,
            detail={
                **result,
                "product_context": product_context or {},
            },
        )


risk_manager = RiskManager()
