"""PortfolioMixin: 포트폴리오, 상품분류, 데이터검증, 포지션 판단"""
import pandas as pd
from loguru import logger
from sqlalchemy import func, select

from analysis.chart_analyzer import ChartAnalysisResult
from core.config import settings
from core.database import AsyncSessionLocal
from models.agent_activity import AgentActivityLog
from scheduler.market_calendar import market_calendar
from services.activity_logger import activity_logger
from trading.enums import ActivityPhase, ActivityType
from trading.market_profile import (
    is_crypto_market,
    is_us_market,
    market_currency,
    normalize_market,
    normalize_market_scope,
)
from trading.product_policy import build_product_context
from trading.quantity_policy import (
    cap_quantity_for_amount,
    format_quantity,
    format_quantity_with_unit,
    has_quantity,
    normalize_quantity,
)

from agent.trading_agent._types import (
    _AVERAGE_DOWN_DAILY_LIMIT,
    _DATA_CONSISTENCY_MAX_GAP_PCT,
    _ENTRY_MODE_AVERAGE_DOWN,
    _ENTRY_MODE_HOLD,
    _ENTRY_MODE_NEW,
    _ENTRY_MODE_PYRAMID,
    _ENTRY_MODES,
    _TIER2_PRICE_CONVERSION_MAX_GAP_PCT,
    _TIER2_PRICE_KRW_HINT_RATIO,
)


class PortfolioMixin:
    """포트폴리오 스냅샷, 상품 분류, 데이터 검증, 포지션 의도 판단 Mixin"""

    # ── 상품 분류 ──

    @staticmethod
    def _instrument_key(symbol: str, market: str) -> str:
        return f"{normalize_market(market)}:{str(symbol).upper()}"

    def _get_product_metadata(self, symbol: str, market: str) -> dict:
        return dict(self._product_metadata.get(self._instrument_key(symbol, market), {}))

    def _remember_product_metadata(self, symbol: str, market: str, metadata: dict | None) -> None:
        if not symbol or not metadata:
            return

        key = self._instrument_key(symbol, market)
        current = self._product_metadata.get(key, {})
        merged = {**current}
        for field_name in (
            "name",
            "category",
            "product_type",
            "is_leveraged",
            "is_inverse",
            "leverage_multiplier",
            "signed_exposure",
            "restricted_product",
            "classification_source",
        ):
            value = metadata.get(field_name)
            if value not in (None, ""):
                merged[field_name] = value
        if merged:
            self._product_metadata[key] = merged

    @staticmethod
    def _enrich_activity_detail(detail: dict | None, product_context: dict | None) -> dict | None:
        if not detail and not product_context:
            return None

        enriched = dict(detail or {})
        if product_context:
            enriched["product_context"] = dict(product_context)
        return enriched

    @staticmethod
    def _format_product_context_for_prompt(product_context: dict | None) -> str:
        context = product_context or {}
        product_type = str(context.get("product_type") or "COMMON")
        is_leveraged = bool(context.get("is_leveraged"))
        is_inverse = bool(context.get("is_inverse"))
        restricted = bool(context.get("restricted_product"))
        multiplier = float(context.get("leverage_multiplier") or 1.0)
        if is_inverse:
            exposure_text = f"-{multiplier:g}x"
        elif is_leveraged:
            exposure_text = f"+{multiplier:g}x"
        else:
            exposure_text = "1x"

        restriction_text = "제한 상품" if restricted else "일반 종목"
        guidance = (
            "레버리지/인버스 특성상 일반 종목보다 더 강한 추세·거래량 확인, 더 보수적인 수량 판단, "
            "더 타이트한 손절과 장마감 전 청산 가능성을 우선 검토하세요."
            if restricted
            else "일반 종목 기준으로 평가하되, 별도 배수 리스크는 없습니다."
        )
        return "\n".join(
            [
                f"- 상품 유형: {product_type}",
                f"- 노출 배수: {exposure_text}",
                f"- 제한 상품 여부: {restriction_text}",
                f"- 분류 근거: {context.get('classification_source') or 'default'}",
                f"- 분석 메모: {guidance}",
            ]
        )

    # ── 포트폴리오 ──

    def _build_holding_snapshot(
        self,
        holdings: list,
        total_asset: float,
    ) -> tuple[list[tuple[str, str]], dict[str, dict]]:
        symbols: list[tuple[str, str]] = []
        positions: dict[str, dict] = {}

        for holding in holdings:
            market_code = normalize_market(getattr(holding, "market", settings.primary_market_code))
            symbol = str(getattr(holding, "symbol", "") or "").upper()
            if not symbol:
                continue

            key = self._instrument_key(symbol, market_code)
            currency = str(getattr(holding, "currency", market_currency(market_code)) or market_currency(market_code))
            exchange_rate = float(getattr(holding, "exchange_rate_to_krw", 1.0) or 1.0)
            quantity = normalize_quantity(getattr(holding, "quantity", 0), market_code)
            current_price = float(getattr(holding, "current_price", 0.0) or 0.0)
            avg_buy_price = float(getattr(holding, "avg_buy_price", 0.0) or 0.0)
            market_value = current_price * quantity if current_price > 0 and quantity > 0 else avg_buy_price * quantity
            current_value_krw = market_value if currency == "KRW" else market_value * exchange_rate
            position_pct = (current_value_krw / total_asset * 100) if total_asset > 0 else 0.0

            symbols.append((market_code, symbol))
            positions[key] = {
                "symbol": symbol,
                "name": getattr(holding, "name", symbol),
                "market": market_code,
                "currency": currency,
                "quantity": quantity,
                "avg_buy_price": avg_buy_price,
                "current_price": current_price,
                "pnl": float(getattr(holding, "pnl", 0.0) or 0.0),
                "pnl_rate": float(getattr(holding, "pnl_rate", 0.0) or 0.0),
                "exchange_rate_to_krw": exchange_rate,
                "current_value_krw": current_value_krw,
                "position_pct": position_pct,
            }

        return symbols, positions

    def _get_existing_position(
        self,
        portfolio_snapshot: dict | None,
        symbol: str,
        market: str,
    ) -> dict | None:
        snapshot = portfolio_snapshot or {}
        positions = snapshot.get("holding_positions") or {}
        return positions.get(self._instrument_key(symbol, market))

    @staticmethod
    def _format_current_position_for_prompt(position: dict | None) -> str:
        if not position:
            return "현재 포지션 없음 (신규 진입 후보)"

        market_code = normalize_market(position.get("market") or "KRX")
        currency = str(position.get("currency") or "KRW")
        quantity = normalize_quantity(position.get("quantity") or 0, market_code)
        avg_buy_price = float(position.get("avg_buy_price") or 0.0)
        current_price = float(position.get("current_price") or 0.0)
        pnl = float(position.get("pnl") or 0.0)
        pnl_rate = float(position.get("pnl_rate") or 0.0)
        position_pct = float(position.get("position_pct") or 0.0)
        current_value_krw = float(position.get("current_value_krw") or 0.0)
        market_value = current_price * quantity if current_price > 0 and quantity > 0 else avg_buy_price * quantity

        price_text = f"{avg_buy_price:,.0f}원" if currency == "KRW" else f"{avg_buy_price:,.2f}{currency}"
        pnl_text = f"{pnl:+,.0f}원" if currency == "KRW" else f"{pnl:+,.2f}{currency}"
        position_value_text = (
            f"약 {market_value:,.2f}{currency}"
            if is_us_market(market_code) and currency != "KRW"
            else f"약 {current_value_krw:,.0f}원"
        )
        return "\n".join(
            [
                f"- 현재 보유 여부: 보유 중",
                f"- 보유 수량: {format_quantity_with_unit(quantity, market_code)}",
                f"- 평균단가: {price_text}",
                f"- 평가손익: {pnl_rate:+.2f}% ({pnl_text})",
                f"- 현재 비중: {position_pct:.1f}% ({position_value_text})",
                "- 해석: 이미 보유 중인 종목이므로 신규 진입보다 추가매수 필요성 검증이 우선입니다.",
            ]
        )

    @staticmethod
    def _resolve_effective_limits(
        dynamic_limits: dict | None,
        market: str,
        *,
        default_max_position_pct: float = 20.0,
    ) -> dict[str, float]:
        market_code = normalize_market(market)
        if is_crypto_market(market_code):
            eff_max_order = float(settings.CRYPTO_MAX_SINGLE_ORDER_KRW or 0)
            eff_min_cash_ratio = float(settings.CRYPTO_MIN_CASH_RATIO or 0)
            eff_max_pos_pct = float(settings.CRYPTO_MAX_POSITION_PCT or default_max_position_pct or 0)
        else:
            eff_max_order = float(settings.MAX_SINGLE_ORDER_KRW or 0)
            eff_min_cash_ratio = float(settings.MIN_CASH_RATIO or 0)
            eff_max_pos_pct = float(default_max_position_pct or 0)
        if dynamic_limits:
            eff_max_order = float(dynamic_limits.get("max_single_order_krw", eff_max_order) or 0)
            eff_min_cash_ratio = float(dynamic_limits.get("min_cash_ratio", eff_min_cash_ratio) or 0)
            eff_max_pos_pct = float(dynamic_limits.get("max_position_pct", eff_max_pos_pct) or 0)
        return {
            "max_single_order_krw": eff_max_order,
            "min_cash_ratio": eff_min_cash_ratio,
            "max_position_pct": eff_max_pos_pct,
        }

    def _build_account_context(
        self,
        *,
        market: str,
        portfolio_snapshot: dict | None,
        current_position: dict | None,
        dynamic_limits: dict | None,
        current_price: float,
        currency: str,
        exchange_rate_to_krw: float,
        orderable_amount_context: dict | None = None,
    ) -> dict[str, float | int | str | None]:
        snap = portfolio_snapshot or {}
        market_code = normalize_market(market)
        total_asset = float(snap.get("total_asset") or 0.0)
        snapshot_total_asset_foreign = float(snap.get("total_asset_foreign") or 0.0)
        broker_cash_krw = float(snap.get("cash") or 0.0)
        snapshot_cash_foreign = float(snap.get("cash_foreign") or 0.0)
        snapshot_effective_cash_foreign = float(snap.get("effective_cash_foreign") or 0.0)
        holding_count = int(snap.get("holding_count") or 0)
        cash_ratio = (broker_cash_krw / total_asset * 100) if total_asset > 0 else 0.0

        current_position_value_krw = float((current_position or {}).get("current_value_krw") or 0.0)
        current_position_pct = float((current_position or {}).get("position_pct") or 0.0)
        unit_price_krw = current_price if currency == "KRW" else current_price * float(exchange_rate_to_krw or 1.0)
        exchange_rate = float(exchange_rate_to_krw or 0.0)

        orderable_context = orderable_amount_context or {}
        orderable_amount_source: str | None = orderable_context.get("orderable_amount_source") or None
        orderable_error = str(orderable_context.get("error") or "").strip()
        if orderable_amount_source:
            symbol_orderable_amount_krw = float(orderable_context.get("orderable_amount_krw") or 0.0)
            symbol_orderable_amount_foreign = float(orderable_context.get("orderable_amount_foreign") or 0.0)
            symbol_orderable_qty = normalize_quantity(orderable_context.get("orderable_qty") or 0, market_code)
        else:
            symbol_orderable_amount_krw = None
            symbol_orderable_amount_foreign = None
            symbol_orderable_qty = None
        cash_cap_krw = broker_cash_krw
        if symbol_orderable_amount_krw is not None:
            cash_cap_krw = symbol_orderable_amount_krw
        cash_cap_foreign = None
        broker_cash_foreign = None
        if symbol_orderable_amount_foreign is not None and symbol_orderable_amount_foreign > 0:
            cash_cap_foreign = symbol_orderable_amount_foreign
        elif snapshot_effective_cash_foreign > 0:
            cash_cap_foreign = snapshot_effective_cash_foreign
        elif snapshot_cash_foreign > 0:
            cash_cap_foreign = snapshot_cash_foreign
        if snapshot_cash_foreign > 0:
            broker_cash_foreign = snapshot_cash_foreign

        limits = self._resolve_effective_limits(dynamic_limits, market_code)
        hard_caps: list[float] = [max(cash_cap_krw, 0.0)]
        if limits["max_single_order_krw"] > 0:
            hard_caps.append(limits["max_single_order_krw"])
        if total_asset > 0 and limits["min_cash_ratio"] > 0:
            hard_caps.append(max(cash_cap_krw - (total_asset * limits["min_cash_ratio"]), 0.0))
        if total_asset > 0 and limits["max_position_pct"] > 0:
            hard_caps.append(
                max((total_asset * limits["max_position_pct"] / 100) - current_position_value_krw, 0.0)
            )

        max_additional_amount = min(hard_caps) if hard_caps else 0.0
        max_additional_amount = max(max_additional_amount, 0.0)
        max_additional_quantity = cap_quantity_for_amount(max_additional_amount, unit_price_krw, market_code)
        projected_combined_position_pct = (
            (current_position_value_krw + max_additional_amount) / total_asset * 100
            if total_asset > 0
            else current_position_pct
        )
        use_foreign_display = is_us_market(market_code) and currency != "KRW"
        total_asset_foreign = (
            snapshot_total_asset_foreign
            if use_foreign_display and snapshot_total_asset_foreign > 0
            else None
        )
        current_position_value_foreign = (
            float((current_position or {}).get("current_price") or 0.0)
            * float((current_position or {}).get("quantity") or 0.0)
            if use_foreign_display
            else None
        )
        if use_foreign_display:
            if abs(max_additional_amount - cash_cap_krw) < 0.01:
                max_additional_amount_foreign = cash_cap_foreign
            else:
                max_additional_amount_foreign = (
                    max_additional_amount / exchange_rate if exchange_rate > 0 else None
                )
        else:
            max_additional_amount_foreign = None

        def _format_display_amount(
            amount_krw: float,
            *,
            amount_foreign: float | None = None,
        ) -> str:
            if use_foreign_display:
                resolved_foreign = amount_foreign
                if resolved_foreign is not None:
                    return f"{resolved_foreign:,.2f}{currency}"
                return f"조회값 없음 ({currency})"
            return f"{amount_krw:,.0f}원"

        add_label = "추가매수 가능 최대" if current_position_value_krw > 0 else "신규 진입 가능 최대"
        lines = [f"- 총자산: {_format_display_amount(total_asset, amount_foreign=total_asset_foreign)}"]
        if use_foreign_display:
            if orderable_amount_source and symbol_orderable_amount_krw is not None:
                qty_text = (
                    format_quantity_with_unit(symbol_orderable_qty, market_code)
                    if symbol_orderable_qty is not None and symbol_orderable_qty > 0
                    else "수량 정보 없음"
                )
                lines.append(
                    f"- 실주문 기준 현금: "
                    f"{_format_display_amount(cash_cap_krw, amount_foreign=symbol_orderable_amount_foreign or cash_cap_foreign)} "
                    f"(최대 {qty_text}, 현금비율 {cash_ratio:.1f}%)"
                )
            elif orderable_error:
                lines.append(f"- 실주문 기준 현금: 조회 실패 ({orderable_error})")
            else:
                lines.append(
                    f"- 가용 현금: "
                    f"{_format_display_amount(broker_cash_krw, amount_foreign=broker_cash_foreign)} "
                    f"(현금비율 {cash_ratio:.1f}%)"
                )
        else:
            lines.append(f"- 브로커 잔고 현금: {broker_cash_krw:,.0f}원 (현금비율 {cash_ratio:.1f}%)")

        lines.extend([
            f"- 현재 보유 종목 수: {holding_count}개",
            f"- 현재 이 종목 비중: {current_position_pct:.1f}%",
            f"- {add_label}: {_format_display_amount(max_additional_amount, amount_foreign=max_additional_amount_foreign)}",
            f"- 현재가 기준 최대 수량: {format_quantity(max_additional_quantity, market_code)}",
            f"- 하드 가드 기준 최대 집행 시 예상 합산 비중: {projected_combined_position_pct:.1f}%",
        ])
        if orderable_amount_source and symbol_orderable_amount_krw is not None and not is_us_market(market_code):
            foreign_text = f"{symbol_orderable_amount_foreign:,.2f}{currency}"
            qty_text = (
                format_quantity_with_unit(symbol_orderable_qty, market_code)
                if symbol_orderable_qty is not None and symbol_orderable_qty > 0
                else "수량 정보 없음"
            )
            lines.insert(2, f"- 이 종목 기준 주문가능금액: {symbol_orderable_amount_krw:,.0f}원 ({foreign_text}, 최대 {qty_text})")
        elif orderable_error and not is_us_market(market_code):
            lines.insert(2, f"- 이 종목 기준 주문가능금액: 조회 실패 ({orderable_error})")

        return {
            "text": "\n".join(lines),
            "total_asset": total_asset,
            "available_cash": cash_cap_krw,
            "broker_cash_krw": broker_cash_krw,
            "cash_ratio": cash_ratio,
            "holding_count": holding_count,
            "current_position_pct": current_position_pct,
            "current_position_value_krw": current_position_value_krw,
            "current_position_value_foreign": current_position_value_foreign,
            "max_additional_amount": max_additional_amount,
            "max_additional_amount_foreign": max_additional_amount_foreign,
            "max_additional_quantity": max_additional_quantity,
            "projected_combined_position_pct": projected_combined_position_pct,
            "max_position_pct": limits["max_position_pct"],
            "min_cash_ratio": limits["min_cash_ratio"],
            "symbol_orderable_amount_krw": symbol_orderable_amount_krw,
            "symbol_orderable_amount_foreign": symbol_orderable_amount_foreign,
            "available_cash_foreign": cash_cap_foreign,
            "total_asset_foreign": total_asset_foreign,
            "total_asset_text": _format_display_amount(total_asset, amount_foreign=total_asset_foreign),
            "max_additional_amount_text": _format_display_amount(
                max_additional_amount,
                amount_foreign=max_additional_amount_foreign,
            ),
            "use_foreign_display": use_foreign_display,
            "symbol_orderable_qty": symbol_orderable_qty,
            "orderable_amount_source": orderable_amount_source,
        }

    @staticmethod
    def _normalize_position_intent(intent: str | None, *, has_current_position: bool) -> str:
        normalized = str(intent or "").strip().upper()
        aliases = {
            "PYRAMID": _ENTRY_MODE_PYRAMID,
            "ADD_ON": _ENTRY_MODE_PYRAMID,
            "ADDON": _ENTRY_MODE_PYRAMID,
            "AVERAGE_DOWN": _ENTRY_MODE_AVERAGE_DOWN,
            "AVG_DOWN": _ENTRY_MODE_AVERAGE_DOWN,
        }
        normalized = aliases.get(normalized, normalized)
        if normalized in _ENTRY_MODES:
            return normalized
        return "" if has_current_position else _ENTRY_MODE_NEW

    # ── 데이터 검증 ──

    @staticmethod
    def _average_down_rebound_check(
        chart_result: ChartAnalysisResult,
        current_price: float,
    ) -> dict[str, object]:
        indicators = chart_result.indicators or {}
        trend = chart_result.trend
        rsi = indicators.get("rsi_14")
        macd = indicators.get("macd")
        macd_signal = indicators.get("macd_signal")
        macd_hist = indicators.get("macd_histogram")
        obv_trend = str(indicators.get("obv_trend") or "").lower()
        sma5 = indicators.get("sma_5")
        ema20 = indicators.get("ema_20")
        momentum = str(getattr(trend, "momentum", "") or "").upper()
        signal_direction = str(chart_result.signal_summary.get("direction", "") or "").upper()

        rsi_ok = isinstance(rsi, (int, float)) and 35 <= float(rsi) <= 60
        momentum_ok = momentum in {"REVERSING", "ACCELERATING"} or (
            isinstance(macd_hist, (int, float)) and float(macd_hist) > 0
        ) or (
            isinstance(macd, (int, float))
            and isinstance(macd_signal, (int, float))
            and float(macd) >= float(macd_signal)
        )
        volume_ok = obv_trend == "rising"
        price_recovery_ok = (
            isinstance(sma5, (int, float)) and current_price >= float(sma5)
        ) or (
            isinstance(ema20, (int, float)) and current_price >= float(ema20)
        )
        direction_ok = signal_direction in {"BULLISH", "NEUTRAL"}
        confirmed = rsi_ok and momentum_ok and volume_ok and price_recovery_ok and direction_ok

        return {
            "confirmed": confirmed,
            "rsi_ok": rsi_ok,
            "momentum_ok": momentum_ok,
            "volume_ok": volume_ok,
            "price_recovery_ok": price_recovery_ok,
            "direction_ok": direction_ok,
            "rsi": float(rsi) if isinstance(rsi, (int, float)) else None,
            "momentum": momentum or None,
            "obv_trend": obv_trend or None,
            "signal_direction": signal_direction or None,
        }

    @staticmethod
    def _should_skip_tier2(
        *,
        market_scope: str,
        is_restricted_product: bool,
        tier1_confidence: float,
        market_regime: str,
        recommendation: str,
        has_current_position: bool = False,
    ) -> bool:
        return (
            market_scope == "CRYPTO"
            and
            not has_current_position
            and
            not is_restricted_product
            and tier1_confidence >= 0.80
            and market_regime in ("THEME", "BULL", "BULL_RUN", "ALTSEASON", "ALT_SEASON")
            and recommendation == "BUY"
        )

    @staticmethod
    def _sort_market_data_frame(df: pd.DataFrame, time_column: str) -> pd.DataFrame:
        """시계열 DataFrame을 과거→현재 순으로 정렬"""
        if df.empty:
            return df
        if time_column not in df.columns:
            return df.reset_index(drop=True)

        ordered = (
            df.assign(_sort_key=df[time_column].fillna("").astype(str).str.strip())
            .sort_values("_sort_key")
            .drop(columns="_sort_key")
            .reset_index(drop=True)
        )
        return ordered

    @staticmethod
    def _detect_price_consistency_issue(
        current_price: float,
        daily_df: pd.DataFrame,
        minute_df: pd.DataFrame | None = None,
    ) -> dict | None:
        """실시간 현재가와 최신 일봉/분봉 종가 괴리 검증"""
        if current_price <= 0:
            return None

        anchors: list[tuple[str, float]] = []
        if not daily_df.empty and "close" in daily_df.columns:
            latest_daily_close = float(daily_df["close"].iloc[-1] or 0)
            if latest_daily_close > 0:
                anchors.append(("latest_daily_close", latest_daily_close))

        if minute_df is not None and not minute_df.empty and "close" in minute_df.columns:
            latest_minute_close = float(minute_df["close"].iloc[-1] or 0)
            if latest_minute_close > 0:
                anchors.append(("latest_minute_close", latest_minute_close))

        if not anchors:
            return None

        worst_anchor = ""
        worst_anchor_price = 0.0
        worst_gap_pct = 0.0
        for anchor_name, anchor_price in anchors:
            gap_pct = abs(current_price - anchor_price) / anchor_price
            if gap_pct > worst_gap_pct:
                worst_anchor = anchor_name
                worst_anchor_price = anchor_price
                worst_gap_pct = gap_pct

        if worst_gap_pct < _DATA_CONSISTENCY_MAX_GAP_PCT:
            return None

        detail = {
            "live_quote": round(current_price, 4),
            "anchor": worst_anchor,
            "anchor_price": round(worst_anchor_price, 4),
            "gap_pct": round(worst_gap_pct, 4),
        }
        for anchor_name, anchor_price in anchors:
            detail[anchor_name] = round(anchor_price, 4)
        return detail

    @staticmethod
    def _normalize_tier2_price_fields(
        parsed: dict | None,
        *,
        current_price: float,
        currency: str,
        exchange_rate_to_krw: float,
    ) -> dict | None:
        """미국장 Tier2 가격 필드가 원화로 반환된 경우 시장 통화로 보정"""
        if not parsed:
            return parsed
        if currency == "KRW" or current_price <= 0 or exchange_rate_to_krw <= 0:
            return parsed

        normalized: dict[str, dict[str, float | str]] = {}
        for key in ("entry_price", "target_price", "stop_loss_price", "take_profit_price"):
            value = parsed.get(key)
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if numeric <= 0:
                continue

            converted = numeric / exchange_rate_to_krw
            raw_ratio = numeric / current_price
            converted_gap_pct = abs(converted - current_price) / current_price

            if raw_ratio >= _TIER2_PRICE_KRW_HINT_RATIO and converted_gap_pct <= _TIER2_PRICE_CONVERSION_MAX_GAP_PCT:
                parsed[key] = round(converted, 4)
                normalized[key] = {
                    "raw_value": round(numeric, 4),
                    "normalized_value": round(converted, 4),
                    "currency": currency,
                }
            else:
                parsed[key] = round(numeric, 4)

        if normalized:
            parsed["normalized_price_fields"] = normalized
        return parsed

    # ── 리스크 / 포지션 의도 판단 ──

    async def _count_today_trade_result_entry_mode(
        self,
        *,
        symbol: str,
        market_code: str,
        scope: str,
        trading_date,
        entry_mode: str,
    ) -> int:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(func.count(AgentActivityLog.id)).where(
                    AgentActivityLog.symbol == symbol,
                    AgentActivityLog.market_scope == normalize_market_scope(scope),
                    AgentActivityLog.trading_date == trading_date,
                    AgentActivityLog.activity_type == ActivityType.TRADE_RESULT.value,
                    AgentActivityLog.phase == ActivityPhase.COMPLETE.value,
                    AgentActivityLog.detail.isnot(None),
                    AgentActivityLog.detail.contains(f'"entry_mode": "{entry_mode}"'),
                    AgentActivityLog.detail.contains(f'"market": "{market_code}"'),
                )
            )
        return int(result.scalar() or 0)

    async def _evaluate_position_intent(
        self,
        *,
        symbol: str,
        market_code: str,
        scope: str,
        trading_date,
        current_position: dict | None,
        entry_mode: str,
        chart_result: ChartAnalysisResult,
        current_price: float,
    ) -> dict[str, object]:
        quantity = normalize_quantity((current_position or {}).get("quantity") or 0, market_code)
        if not has_quantity(quantity, market_code):
            return {"approved": True, "entry_mode": _ENTRY_MODE_NEW, "detail": None}

        normalized = self._normalize_position_intent(entry_mode, has_current_position=True)
        detail = {
            "entry_mode": normalized or None,
            "current_position": dict(current_position or {}),
        }
        pnl_rate = float((current_position or {}).get("pnl_rate") or 0.0)

        if normalized not in {_ENTRY_MODE_PYRAMID, _ENTRY_MODE_AVERAGE_DOWN}:
            return {
                "approved": False,
                "reason": "보유 종목 BUY는 position_intent를 ADD_ON_PYRAMID 또는 ADD_ON_AVERAGE_DOWN으로 명시해야 합니다",
                "detail": detail,
            }

        if normalized == _ENTRY_MODE_PYRAMID:
            if pnl_rate <= 0:
                detail["pnl_rate"] = pnl_rate
                return {
                    "approved": False,
                    "reason": f"불타기 차단: 현재 포지션이 수익 구간이 아님 ({pnl_rate:+.2f}%)",
                    "detail": detail,
                }
            return {"approved": True, "entry_mode": normalized, "detail": detail}

        if pnl_rate >= 0:
            detail["pnl_rate"] = pnl_rate
            return {
                "approved": False,
                "reason": f"물타기 차단: 현재 포지션이 손실 구간이 아님 ({pnl_rate:+.2f}%)",
                "detail": detail,
            }

        rebound_check = self._average_down_rebound_check(chart_result, current_price)
        detail.update({
            "pnl_rate": pnl_rate,
            "rebound_check": rebound_check,
        })
        if not rebound_check["confirmed"]:
            return {
                "approved": False,
                "reason": "물타기 차단: RSI/모멘텀/거래량 기준의 반등 확인이 부족합니다",
                "detail": detail,
            }

        today_count = await self._count_today_trade_result_entry_mode(
            symbol=symbol,
            market_code=market_code,
            scope=scope,
            trading_date=trading_date,
            entry_mode=_ENTRY_MODE_AVERAGE_DOWN,
        )
        detail["today_average_down_count"] = today_count
        if today_count >= _AVERAGE_DOWN_DAILY_LIMIT:
            return {
                "approved": False,
                "reason": f"물타기 차단: 당일 허용 횟수({_AVERAGE_DOWN_DAILY_LIMIT}회)를 이미 사용했습니다",
                "detail": detail,
            }
        return {"approved": True, "entry_mode": normalized, "detail": detail}

    async def _log_existing_position_skip(
        self,
        *,
        symbol: str,
        name: str,
        market: str,
        cycle_id: str | None,
        position: dict | None,
        source: str,
        event_type: str | None = None,
    ) -> None:
        market_code = normalize_market(market)
        quantity = normalize_quantity((position or {}).get("quantity") or 0, market_code)
        prefix = "기보유 종목 이벤트 감지" if source == "event" else "기보유 종목 추가매수 차단"
        suffix = (
            "추가매수는 정규 사이클에서만 평가, 모니터링 유지"
            if source == "event"
            else "추가매수 정책 차단"
        )
        summary = f"\U0001f6ab [{name}] {prefix} — {suffix}"
        if quantity > 0:
            summary += f" ({format_quantity_with_unit(quantity, market_code)} 보유 중)"

        detail = {
            "reason": prefix,
            "source": source,
            "event_type": event_type,
            "market": market,
            "current_position": dict(position or {}),
        }
        await activity_logger.log(
            ActivityType.RISK_GATE,
            ActivityPhase.SKIP,
            summary,
            cycle_id=cycle_id,
            symbol=symbol,
            detail=detail,
        )

    # ── 매매 통계 조회 ──

    async def _get_today_trade_stats(self, market: str | None = None) -> dict:
        """오늘 매매 승/패 집계 (trade_results 테이블)"""
        from sqlalchemy import select
        from trading.market_profile import MARKET_SCOPE_CRYPTO, markets_for_scope

        scope = normalize_market_scope(market or settings.primary_market_code)
        trading_date = market_calendar.market_date(market=scope)
        start, end = market_calendar.market_day_bounds(scope, trading_date)
        stats = {"wins": 0, "losses": 0, "total": 0}
        try:
            async with AsyncSessionLocal() as session:
                if scope == MARKET_SCOPE_CRYPTO:
                    from models.coin_trade_result import CoinTradeResult

                    result = await session.execute(
                        select(CoinTradeResult.pnl).where(
                            CoinTradeResult.side == "BUY",
                            CoinTradeResult.exit_at.isnot(None),
                            CoinTradeResult.exit_at >= start,
                            CoinTradeResult.exit_at <= end,
                        )
                    )
                else:
                    from models.trade_result import TradeResult

                    result = await session.execute(
                        select(TradeResult.pnl).where(
                            TradeResult.market.in_(markets_for_scope(scope)),
                            TradeResult.exit_at.isnot(None),
                            TradeResult.exit_at >= start,
                            TradeResult.exit_at <= end,
                        )
                    )
                for (pnl,) in result:
                    stats["total"] += 1
                    if pnl >= 0:
                        stats["wins"] += 1
                    else:
                        stats["losses"] += 1
        except Exception:
            pass
        return stats

    async def _get_today_trade_count(self, market: str | None = None) -> int:
        """당일 체결 건수 조회"""
        try:
            from sqlalchemy import select, func
            from trading.market_profile import MARKET_SCOPE_CRYPTO, markets_for_scope

            scope = normalize_market_scope(market or settings.primary_market_code)
            trading_date = market_calendar.market_date(market=scope)
            start, end = market_calendar.market_day_bounds(scope, trading_date)
            async with AsyncSessionLocal() as session:
                if scope == MARKET_SCOPE_CRYPTO:
                    from models.coin_broker_order import CoinBrokerOrder

                    result = await session.execute(
                        select(func.count(CoinBrokerOrder.id)).where(
                            CoinBrokerOrder.filled_quantity > 0,
                            CoinBrokerOrder.filled_at.is_not(None),
                            CoinBrokerOrder.filled_at >= start,
                            CoinBrokerOrder.filled_at <= end,
                        )
                    )
                else:
                    from models.order import Order
                    from models.stock import Stock

                    result = await session.execute(
                        select(func.count(Order.id))
                        .select_from(Order)
                        .join(Stock, Stock.id == Order.stock_id)
                        .where(
                            Stock.market.in_(markets_for_scope(scope)),
                            Order.status == "FILLED",
                            Order.created_at >= start,
                            Order.created_at <= end,
                        )
                    )
                return result.scalar() or 0
        except Exception as e:
            logger.warning("당일 체결 건수 조회 실패: {}", str(e))
            return 0

    async def _get_today_buy_result_count(self, market: str | None = None) -> int:
        """당일 최종 BUY 결과 건수 조회 (추천 + AI 주문 생성 기준)"""
        try:
            from sqlalchemy import func, select
            from models.order import Order
            from models.recommendation import Recommendation
            from models.stock import Stock
            from trading.market_profile import MARKET_SCOPE_CRYPTO, markets_for_scope

            scope = normalize_market_scope(market or settings.primary_market_code)
            if scope == MARKET_SCOPE_CRYPTO:
                return 0

            trading_date = market_calendar.market_date(market=scope)
            start, end = market_calendar.market_day_bounds(scope, trading_date)

            async with AsyncSessionLocal() as session:
                recommendation_count = await session.scalar(
                    select(func.count(Recommendation.id))
                    .select_from(Recommendation)
                    .join(Stock, Stock.id == Recommendation.stock_id)
                    .where(
                        Stock.market.in_(markets_for_scope(scope)),
                        Recommendation.action == "BUY",
                        Recommendation.created_at >= start,
                        Recommendation.created_at <= end,
                    )
                )
                order_count = await session.scalar(
                    select(func.count(Order.id))
                    .select_from(Order)
                    .join(Stock, Stock.id == Order.stock_id)
                    .where(
                        Stock.market.in_(markets_for_scope(scope)),
                        Order.side == "BUY",
                        Order.source == "AI",
                        Order.created_at >= start,
                        Order.created_at <= end,
                    )
                )
                return int(recommendation_count or 0) + int(order_count or 0)
        except Exception as e:
            logger.warning("당일 BUY 결과 건수 조회 실패: {}", str(e))
            return 0

    # ── 유틸 ──

    def _parse_json(self, text: str) -> dict | None:
        from core.json_utils import parse_llm_json
        result = parse_llm_json(text)
        return result if result else None
