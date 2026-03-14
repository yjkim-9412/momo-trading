"""리스크 관리 - 포지션/손절/비중/일일한도"""
from loguru import logger

from core.config import settings
from services.activity_logger import activity_logger
from strategy.signal import TradeSignal
from trading.enums import ActivityPhase, ActivityType, SignalAction
from trading.market_profile import is_crypto_market, is_us_market
from trading.risk_policy import DEFAULT_RR_FLOOR, resolve_rr_floor as resolve_shared_rr_floor
from trading.product_policy import (
    build_product_context,
    classification_from_metadata,
    is_product_trade_allowed,
)
from trading.quantity_policy import cap_quantity_for_amount, format_quantity, normalize_quantity


class RiskManager:
    """
    리스크 관리자
    - 포지션 크기 제한
    - 총 비중 제한
    - 손절 검사
    - 일일 매매 한도
    - 단일 주문 금액 한도
    """

    # 크립토 전용 R:R floor (주식보다 넓은 스탑 반영)
    CRYPTO_RR_FLOOR = {
        "BULL_RUN": 2.0,
        "BEAR_MARKET": 1.5,
        "CONSOLIDATION": 1.5,
        "ALTSEASON": 2.0,
        "ALT_SEASON": 2.0,  # 프롬프트 변형 호환
        # 주식 regime 호환
        "BULL": 2.0,
        "BEAR": 1.5,
        "SIDEWAYS": 1.5,
        "THEME": 2.0,
    }

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
        orderable_cash_krw: float | None = None,
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
            {"approved": bool, "reason": str, "adjusted_quantity": float | None}
        """
        symbol = signal.symbol
        product_context = self._signal_product_context(signal)

        # 크립토 전용 기본값 적용
        metadata = signal.metadata or {}
        market_code = str(metadata.get("market") or "KRX")
        _is_crypto = is_crypto_market(market_code)

        if _is_crypto:
            eff_max_daily = settings.CRYPTO_MAX_DAILY_TRADES
            eff_max_order = settings.CRYPTO_MAX_SINGLE_ORDER_KRW
            eff_min_qty = settings.CRYPTO_MIN_BUY_QUANTITY
            eff_min_cash_ratio = settings.CRYPTO_MIN_CASH_RATIO
            eff_max_pos_pct = settings.CRYPTO_MAX_POSITION_PCT
        else:
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

        # 매매 비활성화 검사 (크립토는 독립 설정)
        trading_enabled = settings.CRYPTO_TRADING_ENABLED if _is_crypto else settings.TRADING_ENABLED
        if not trading_enabled:
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
        quantity = normalize_quantity(signal.suggested_quantity, market_code)
        if price <= 0 or quantity <= 0:
            result = {"approved": False, "reason": "가격 또는 수량이 유효하지 않습니다"}
            await self._log_result(
                symbol, result, today_trade_count, cycle_id, product_context, eff_max_daily
            )
            return result

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
        broker_cash_krw = float(portfolio_cash or 0.0)
        cash_basis_krw = broker_cash_krw

        # 크립토는 orderable_cash 별도 조회 불필요 (잔고 기반)
        if _is_crypto:
            cash_basis_krw = broker_cash_krw
        elif is_us_market(market_code):
            if orderable_cash_krw is None:
                result = {
                    "approved": False,
                    "reason": "종목별 주문가능금액 조회 실패",
                    "broker_cash_krw": broker_cash_krw,
                    "cash_basis_krw": broker_cash_krw,
                    "orderable_cash_krw": None,
                }
                await self._log_result(
                    symbol, result, today_trade_count, cycle_id, product_context, eff_max_daily
                )
                return result

            cash_basis_krw = float(orderable_cash_krw or 0.0)
            if cash_basis_krw <= 0:
                result = {
                    "approved": False,
                    "reason": "종목별 주문가능금액 없음",
                    "broker_cash_krw": broker_cash_krw,
                    "cash_basis_krw": cash_basis_krw,
                    "orderable_cash_krw": cash_basis_krw,
                }
                await self._log_result(
                    symbol, result, today_trade_count, cycle_id, product_context, eff_max_daily
                )
                return result

        # 리스크:보상 비율 검사 (다른 조정 전에 먼저 확인)
        entry = signal.suggested_price or 0
        target = signal.target_price or 0
        stop = signal.stop_loss_price or 0

        if entry > 0 and target > 0 and stop > 0:
            reward = abs(target - entry)
            risk = abs(entry - stop)
            if risk > 0:
                rr_ratio = reward / risk
                if _is_crypto:
                    min_rr = self.CRYPTO_RR_FLOOR.get(market_regime, 1.5)
                else:
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

        def _apply_quantity_cap(capped_qty: float, label: str) -> tuple[bool, dict | None]:
            nonlocal quantity, total_amount
            if capped_qty >= quantity:
                return False, None
            if capped_qty <= 0:
                return True, {"approved": False, "reason": f"{label} 후 주문 가능 수량 없음"}
            if capped_qty < eff_min_qty:
                return True, {"approved": False, "reason": f"{label} 후 최소 수량 미달"}
            quantity = normalize_quantity(capped_qty, market_code)
            total_amount = unit_price_krw * quantity
            adjustment_labels.append(label)
            return True, None

        # 단일 주문 금액 한도 (0이면 AI 자율 → 스킵)
        if eff_max_order > 0 and total_amount > eff_max_order:
            _, reject_result = _apply_quantity_cap(
                cap_quantity_for_amount(eff_max_order, unit_price_krw, market_code),
                "단일 주문 한도",
            )
            if reject_result:
                reject_result.update({
                    "broker_cash_krw": broker_cash_krw,
                    "cash_basis_krw": cash_basis_krw,
                    "orderable_cash_krw": orderable_cash_krw,
                })
                await self._log_result(
                    symbol, reject_result, today_trade_count, cycle_id, product_context, eff_max_daily
                )
                return reject_result

        # 현금 부족 검사 (음수 현금 방어 포함)
        if cash_basis_krw <= 0:
            result = {
                "approved": False,
                "reason": "가용 현금 없음",
                "broker_cash_krw": broker_cash_krw,
                "cash_basis_krw": cash_basis_krw,
                "orderable_cash_krw": orderable_cash_krw,
            }
            await self._log_result(
                symbol, result, today_trade_count, cycle_id, product_context, eff_max_daily
            )
            return result

        if total_amount > cash_basis_krw:
            _, reject_result = _apply_quantity_cap(
                cap_quantity_for_amount(cash_basis_krw, unit_price_krw, market_code),
                "현금 부족",
            )
            if reject_result:
                reject_result.update({
                    "broker_cash_krw": broker_cash_krw,
                    "cash_basis_krw": cash_basis_krw,
                    "orderable_cash_krw": orderable_cash_krw,
                })
                await self._log_result(
                    symbol, reject_result, today_trade_count, cycle_id, product_context, eff_max_daily
                )
                return reject_result

        # 최소 현금 비중 검사
        cash_after = cash_basis_krw - total_amount
        if portfolio_budget > 0 and cash_after / portfolio_budget < eff_min_cash_ratio:
            max_spend = cash_basis_krw - (portfolio_budget * eff_min_cash_ratio)
            if max_spend <= 0:
                result = {
                    "approved": False,
                    "reason": "현금 비중 최소 한도 미달",
                    "broker_cash_krw": broker_cash_krw,
                    "cash_basis_krw": cash_basis_krw,
                    "orderable_cash_krw": orderable_cash_krw,
                }
                await self._log_result(
                    symbol, result, today_trade_count, cycle_id, product_context, eff_max_daily
                )
                return result
            _, reject_result = _apply_quantity_cap(
                cap_quantity_for_amount(max_spend, unit_price_krw, market_code),
                "현금 비중 유지",
            )
            if reject_result:
                reject_result.update({
                    "broker_cash_krw": broker_cash_krw,
                    "cash_basis_krw": cash_basis_krw,
                    "orderable_cash_krw": orderable_cash_krw,
                })
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
                    cap_quantity_for_amount(remaining_amount, unit_price_krw, market_code),
                    "합산 비중 한도",
                )
                if reject_result:
                    reject_result.update({
                        "combined_position_pct": combined_position_pct,
                        "current_position_value_krw": current_position_value_krw,
                        "broker_cash_krw": broker_cash_krw,
                        "cash_basis_krw": cash_basis_krw,
                        "orderable_cash_krw": orderable_cash_krw,
                    })
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
                f"수량 조정 ({', '.join(adjustment_labels)}): "
                f"{format_quantity(requested_quantity, market_code)} → {format_quantity(quantity, market_code)}"
            )
        result = {
            "approved": True,
            "reason": adjustment_reason,
            "adjusted_quantity": adjusted_quantity,
            "combined_position_pct": combined_position_pct,
            "current_position_value_krw": current_position_value_krw,
            "broker_cash_krw": broker_cash_krw,
            "cash_basis_krw": cash_basis_krw,
            "orderable_cash_krw": orderable_cash_krw,
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
