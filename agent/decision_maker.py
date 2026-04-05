"""매매 결정 + 자율/반자율 모드 분기 + 체결 확인/기록"""
import asyncio
import json
from datetime import timedelta

from loguru import logger
from sqlalchemy import select

from core.config import settings
from core.database import AsyncSessionLocal
from core.events import Event, EventType, event_bus
from models.agent_activity import AgentActivityLog
from models.broker_order import BrokerOrder
from models.coin_broker_order import CoinBrokerOrder
from models.order import Order
from models.recommendation import Recommendation
from models.coin_analysis_result import CoinAnalysisResult
from models.coin_asset import CoinAsset
from models.coin_recommendation import CoinRecommendation
from models.coin_trade_result import CoinTradeResult
from models.trade_result import TradeResult
from realtime.event_detector import event_detector
from repositories.trade_result_repository import TradeResultRepository
from services.activity_logger import activity_logger
from strategy.signal import TradeSignal
from trading.enums import (
    ActivityPhase,
    ActivityType,
    AutonomyMode,
    OrderSource,
    RecommendationStatus,
    SignalAction,
)
from trading.market_profile import is_crypto_market, is_domestic_market, is_us_market, normalize_market, normalize_market_scope
from trading.mcp_client import mcp_client
from trading.models import coin_side_label
from trading.product_policy import build_product_context
from trading.quantity_policy import format_quantity, format_quantity_with_unit, normalize_quantity
from scheduler.market_calendar import market_calendar
from util.time_util import ensure_kst, now_kst

_US_ORDER_SANITY_MAX_GAP_PCT = 0.15
_OVERSEAS_CONFIRM_DELAYS_SECONDS = (3, 6, 10)
_PREMARKET_SCALP_HOLDING_POLICY = "PREMARKET_SCALP"


class DecisionMaker:
    """
    자율/반자율 모드에 따라 실행 방식을 분기.

    AUTONOMOUS: 스캔 → 분석 → 매매까지 전자동
    SEMI_AUTO: 스캔 → 분석 → 추천 생성 → 사용자 승인 대기
    """

    def __init__(self):
        self._pending_tasks: set[asyncio.Task] = set()
        self._pending_order_events: dict[str, asyncio.Event] = {}
        self._pending_order_data: dict[str, dict] = {}

        from trading.kis_websocket import kis_websocket
        kis_websocket.set_on_order(self.on_order_notification)

    @staticmethod
    def _price_display(price: float, currency: str, price_krw: float) -> str:
        if currency == "KRW":
            return f"@{price:,.0f}원"
        return f"@{price:,.2f}{currency} ({price_krw:,.0f}원)"

    @staticmethod
    def _detect_order_price_sanity_issue(signal: TradeSignal) -> dict | None:
        market_code = normalize_market(signal.metadata.get("market", "KRX"))
        if not is_us_market(market_code):
            return None

        requested_price = float(signal.suggested_price or 0)
        live_price = float(signal.metadata.get("live_price") or 0)
        if requested_price <= 0 or live_price <= 0:
            return None

        gap_pct = abs(requested_price - live_price) / live_price
        if gap_pct <= _US_ORDER_SANITY_MAX_GAP_PCT:
            return None

        currency = signal.metadata.get("currency", "USD")
        exchange_rate = float(signal.metadata.get("exchange_rate_to_krw") or 1.0)
        requested_price_krw = float(
            signal.metadata.get("entry_price_krw")
            or signal.metadata.get("price_krw")
            or (requested_price * exchange_rate if currency != "KRW" else requested_price)
        )
        live_price_krw = float(
            signal.metadata.get("live_price_krw")
            or (live_price * exchange_rate if currency != "KRW" else live_price)
        )
        return {
            "reason": "미국장 주문 가격이 실시간 현재가 대비 과도하게 벗어남",
            "market": market_code,
            "currency": currency,
            "requested_price": round(requested_price, 4),
            "requested_price_krw": round(requested_price_krw, 4),
            "live_price": round(live_price, 4),
            "live_price_krw": round(live_price_krw, 4),
            "gap_pct": round(gap_pct, 4),
        }

    @staticmethod
    def _detect_paper_us_session_block(signal: TradeSignal) -> dict | None:
        market_code = normalize_market(signal.metadata.get("market", "KRX"))
        if not settings.is_paper_trading or not is_us_market(market_code):
            return None

        session = market_calendar.get_market_session(market=market_code)
        if session == "US_REGULAR":
            return None

        session_label = {
            "US_PRE": "프리마켓",
            "US_AFTER": "애프터마켓",
            "CLOSED": "장외",
        }.get(session, session)
        return {
            "reason": "모의투자 미국주식 주문은 정규장만 지원",
            "market": market_code,
            "session": session,
            "session_label": session_label,
            "account_type": settings.kis_account_type_normalized,
            "currency": signal.metadata.get("currency", "USD"),
        }

    @staticmethod
    def _signal_product_context(signal: TradeSignal) -> dict:
        metadata = signal.metadata or {}
        market_code = normalize_market(metadata.get("market", "KRX"))
        return build_product_context(
            signal.symbol,
            market_code,
            metadata,
            market_regime=str(metadata.get("market_regime") or ""),
        )

    @staticmethod
    def _parse_trade_notes(notes: str | None) -> dict:
        if not notes or not isinstance(notes, str):
            return {}
        try:
            parsed = json.loads(notes)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _coerce_int(value) -> int | None:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    @classmethod
    def _first_positive_float(cls, *values) -> float | None:
        for value in values:
            numeric = cls._try_float(value)
            if numeric is not None and numeric > 0:
                return numeric
        return None

    @classmethod
    def _normalize_take_profit_levels(cls, raw_levels) -> list[dict]:
        if not isinstance(raw_levels, list):
            return []

        normalized: list[dict] = []
        for index, level in enumerate(raw_levels):
            if not isinstance(level, dict):
                continue
            level_type = str(level.get("type") or "TAKE_PROFIT").upper()
            if level_type != "TAKE_PROFIT":
                continue
            price = cls._try_float(level.get("price"))
            pct = cls._coerce_int(level.get("pct"))
            if price is None or price <= 0:
                continue
            normalized.append({
                "type": "TAKE_PROFIT",
                "price": round(price, 4),
                "pct": min(max(pct or 100, 1), 100),
                "reason": str(level.get("reason") or f"익절 레벨 {index + 1}"),
                "triggered": bool(level.get("triggered")),
                "triggered_at": level.get("triggered_at"),
            })

        if not normalized:
            return []

        normalized.sort(key=lambda item: item["price"])
        normalized[-1]["pct"] = 100
        return normalized

    @classmethod
    def _build_exit_plan_levels(cls, analysis_context: dict | None) -> list[dict]:
        ctx = analysis_context or {}
        raw_payload = ctx.get("trade_threshold_payload")
        payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}

        for raw_levels in (ctx.get("exit_levels"), payload.get("exit_levels")):
            normalized = cls._normalize_take_profit_levels(raw_levels)
            if normalized:
                return normalized

        payload_tp_levels = payload.get("tp_levels")
        if isinstance(payload_tp_levels, list) and payload_tp_levels:
            converted_levels = [
                {
                    "type": "TAKE_PROFIT",
                    "price": level.get("price"),
                    "pct": level.get("pct", 100),
                    "reason": level.get("reason") or f"익절 레벨 {index + 1}",
                    "triggered": False,
                    "triggered_at": None,
                }
                for index, level in enumerate(payload_tp_levels)
                if isinstance(level, dict)
            ]
            normalized = cls._normalize_take_profit_levels(converted_levels)
            if normalized:
                return normalized

        fallback_take_profit = cls._first_positive_float(
            ctx.get("ai_take_profit_price"),
            ctx.get("take_profit_price"),
            ctx.get("ai_target_price"),
            payload.get("take_profit"),
            payload.get("target_price"),
        )
        if fallback_take_profit is None:
            return []

        return [{
            "type": "TAKE_PROFIT",
            "price": round(fallback_take_profit, 4),
            "pct": 100,
            "reason": "단일 익절 (fallback)",
            "triggered": False,
            "triggered_at": None,
        }]

    @classmethod
    def _build_trade_notes(
        cls,
        *,
        analysis_context: dict | None,
        market: str,
        existing_notes: str | None = None,
    ) -> dict:
        ctx = analysis_context or {}
        trade_notes = cls._parse_trade_notes(existing_notes)

        updates = {
            "entry_mode": ctx.get("entry_mode"),
            "combined_position_pct": ctx.get("combined_position_pct"),
            "post_trade_cash_ratio": ctx.get("post_trade_cash_ratio"),
            "analysis_source": ctx.get("analysis_source"),
            "event_type": ctx.get("event_type"),
            "trailing_stop_pct": ctx.get("trailing_stop_pct"),
            "broker_cash_krw": ctx.get("broker_cash_krw"),
            "symbol_orderable_amount_krw": ctx.get("symbol_orderable_amount_krw"),
            "symbol_orderable_amount_foreign": ctx.get("symbol_orderable_amount_foreign"),
            "symbol_orderable_qty": ctx.get("symbol_orderable_qty"),
            "orderable_amount_source": ctx.get("orderable_amount_source"),
            "market": market,
        }
        for key, value in updates.items():
            if value is not None:
                trade_notes[key] = value

        planned_hold_days = cls._coerce_int(ctx.get("planned_hold_days"))
        if planned_hold_days is not None and planned_hold_days > 0:
            trade_notes["planned_hold_days"] = planned_hold_days
            trade_notes.setdefault("close_review_count", 0)
            trade_notes.setdefault("last_close_review_date", None)

        close_review_count = cls._coerce_int(ctx.get("close_review_count"))
        if close_review_count is not None and close_review_count >= 0:
            trade_notes["close_review_count"] = close_review_count

        if "last_close_review_date" in ctx:
            trade_notes["last_close_review_date"] = ctx.get("last_close_review_date")

        if ctx.get("entry_price_source"):
            trade_notes["entry_price_source"] = ctx.get("entry_price_source")
        if ctx.get("pending_buy_price_reconciliation") is not None:
            trade_notes["pending_buy_price_reconciliation"] = bool(ctx.get("pending_buy_price_reconciliation"))
        if ctx.get("pending_buy_reconcile_order_id"):
            trade_notes["pending_buy_reconcile_order_id"] = str(ctx.get("pending_buy_reconcile_order_id"))
        if ctx.get("exit_reasoning"):
            trade_notes["exit_reasoning"] = str(ctx.get("exit_reasoning"))

        exit_levels = cls._build_exit_plan_levels(ctx)
        if exit_levels:
            trade_notes["exit_levels"] = exit_levels

        trade_threshold_payload = cls._build_trade_threshold_payload(analysis_context=ctx)
        if trade_threshold_payload:
            trade_notes["trade_threshold_payload"] = trade_threshold_payload

        return trade_notes

    @classmethod
    def _enrich_detail(cls, signal: TradeSignal, detail: dict | None = None) -> dict:
        enriched = dict(detail or {})
        enriched["product_context"] = cls._signal_product_context(signal)
        metadata = signal.metadata or {}
        for key in (
            "entry_mode",
            "requested_amount_krw",
            "estimated_quantity",
            "combined_position_pct",
            "post_trade_cash_ratio",
            "current_position",
            "analysis_source",
            "event_type",
            "broker_cash_krw",
            "symbol_orderable_amount_krw",
            "symbol_orderable_amount_foreign",
            "symbol_orderable_qty",
            "orderable_amount_source",
        ):
            value = metadata.get(key)
            if value not in (None, "", {}):
                enriched[key] = value
        return enriched

    @staticmethod
    def _to_trade_krw(value: float, currency: str, exchange_rate: float) -> float:
        if currency == "KRW":
            return float(value)
        return float(value) * float(exchange_rate if exchange_rate > 0 else 1.0)

    @classmethod
    def _format_trade_price(cls, price: float, currency: str, exchange_rate: float) -> str:
        price = float(price or 0.0)
        if currency == "KRW":
            return f"{price:,.0f}원"
        price_krw = cls._to_trade_krw(price, currency, exchange_rate)
        return f"{price:,.2f}{currency} ({price_krw:,.0f}원)"

    @staticmethod
    def _trade_context_value(ctx: dict, key: str, fallback=0.0):
        value = ctx.get(key)
        return fallback if value in (None, "") else value

    @classmethod
    def _apply_premarket_scalp_trade_notes(
        cls,
        trade_notes: dict,
        *,
        market: str,
        session: str | None,
    ) -> dict:
        """프리마켓 단타 모드면 TradeResult.notes에 세션 태그를 기록."""
        if not settings.is_us_premarket_scalp_session(market, session):
            return trade_notes

        enriched = dict(trade_notes)
        enriched["entry_session"] = "US_PRE"
        enriched["holding_policy"] = _PREMARKET_SCALP_HOLDING_POLICY
        enriched["must_exit_by_time"] = (
            f"{int(settings.US_PREMARKET_SCALP_FORCE_LIQUIDATION_HOUR):02d}:"
            f"{int(settings.US_PREMARKET_SCALP_FORCE_LIQUIDATION_MINUTE):02d}"
        )
        enriched["must_exit_tz"] = "America/New_York"
        return enriched

    @staticmethod
    def _position_key(symbol: str, market: str) -> tuple[str, str]:
        return normalize_market(market), str(symbol or "").upper().strip()

    @classmethod
    def _mark_broker_reconciled_trade_notes(
        cls,
        existing_notes: str | None,
        *,
        broker_order: BrokerOrder,
    ) -> str:
        trade_notes = cls._parse_trade_notes(existing_notes)
        trade_notes["reconciled_from_broker_order_id"] = broker_order.id
        trade_notes["reconciled_from_kis_order_id"] = broker_order.kis_order_id
        trade_notes["reconciled_at"] = now_kst().isoformat()
        trade_notes["reconcile_source"] = "broker_order"
        return json.dumps(trade_notes, ensure_ascii=False, default=str)

    @staticmethod
    def _broker_order_filled_at(order: BrokerOrder):
        for value in (order.filled_at, order.submitted_at, getattr(order, "created_at", None)):
            if value:
                return ensure_kst(value)
        return None

    async def _find_stock_buy_dedupe_issue(
        self,
        *,
        symbol: str,
        market: str,
    ) -> dict | None:
        market_code = normalize_market(market)
        if is_crypto_market(market_code):
            return None

        symbol_code = str(symbol or "").upper().strip()
        cooldown_seconds = settings.buy_order_dedupe_cooldown_seconds
        pending_statuses = {"SUBMITTED", "OPEN", "PARTIAL"}
        now = now_kst()

        async with AsyncSessionLocal() as session:
            orders = list(
                (
                    await session.execute(
                        select(BrokerOrder)
                        .where(BrokerOrder.market == market_code)
                        .where(BrokerOrder.symbol == symbol_code)
                        .where(BrokerOrder.side == "BUY")
                        .order_by(BrokerOrder.created_at.desc())
                        .limit(10)
                    )
                ).scalars().all()
            )

        for order in orders:
            status = str(order.status or "").upper()
            if status in pending_statuses:
                return {
                    "reason": "동일 종목의 미체결 BUY 주문이 이미 존재합니다",
                    "reason_code": "BUY_ORDER_DEDUPE_PENDING",
                    "existing_order_id": order.kis_order_id,
                    "existing_status": status,
                    "cooldown_remaining_sec": None,
                }

        if cooldown_seconds <= 0:
            return None

        cooldown_delta = timedelta(seconds=cooldown_seconds)
        for order in orders:
            status = str(order.status or "").upper()
            if status != "FILLED":
                continue
            filled_at = self._broker_order_filled_at(order)
            if not filled_at:
                continue
            remaining = int((filled_at + cooldown_delta - now).total_seconds())
            if remaining > 0:
                return {
                    "reason": "동일 종목 BUY가 최근 체결되어 재매수 쿨다운 중입니다",
                    "reason_code": "BUY_ORDER_DEDUPE_COOLDOWN",
                    "existing_order_id": order.kis_order_id,
                    "existing_status": status,
                    "cooldown_remaining_sec": remaining,
                }
        return None

    async def _reject_duplicate_stock_buy(
        self,
        *,
        signal: TradeSignal,
        cycle_id: str | None,
        dedupe_issue: dict,
        phase_label: str,
    ) -> dict:
        result = {
            "mode": "AUTONOMOUS",
            "symbol": signal.symbol,
            "action": signal.action.value,
            "success": False,
            "order_id": "",
            "message": dedupe_issue["reason"],
            "data": dedupe_issue,
        }
        await activity_logger.log(
            ActivityType.DECISION, ActivityPhase.ERROR,
            f"⚠️ [{signal.symbol}] {phase_label}: {dedupe_issue['reason_code']}",
            cycle_id=cycle_id,
            symbol=signal.symbol,
            error_message=dedupe_issue["reason"],
            detail=self._enrich_detail(signal, dedupe_issue),
        )
        await event_bus.publish(Event(
            type=EventType.ORDER_EXECUTED,
            data=result,
            source="decision_maker",
        ))
        return result

    @staticmethod
    def _order_quantity(value: object, market: str) -> float:
        return normalize_quantity(value, market)

    @staticmethod
    def _quantity_text(quantity: float, market: str) -> str:
        return format_quantity(quantity, market)

    @staticmethod
    def _signal_amount_krw(
        signal: TradeSignal,
        market_code: str,
        *,
        price_krw: float,
    ) -> float:
        if isinstance(signal.suggested_amount_krw, (int, float)) and float(signal.suggested_amount_krw) > 0:
            return float(signal.suggested_amount_krw)
        quantity = normalize_quantity(signal.suggested_quantity, market_code)
        if price_krw > 0 and quantity > 0:
            return price_krw * quantity
        return 0.0

    @classmethod
    def _resolve_order_request(
        cls,
        signal: TradeSignal,
        *,
        market_code: str,
        price_krw: float,
    ) -> dict[str, float | None | str]:
        quantity = normalize_quantity(signal.suggested_quantity, market_code)
        requested_amount_krw = cls._signal_amount_krw(signal, market_code, price_krw=price_krw)

        if is_crypto_market(market_code) and signal.action == SignalAction.BUY:
            return {
                "order_quantity": requested_amount_krw,
                "order_price": None,
                "estimated_quantity": quantity,
                "requested_amount_krw": requested_amount_krw,
                "order_type": "PRICE",
            }

        suggested_price = float(signal.suggested_price or 0.0)
        order_type = "LIMIT"
        if suggested_price <= 0:
            order_type = "MARKET"
        elif is_crypto_market(market_code) and signal.action == SignalAction.SELL:
            order_type = "LIMIT"

        return {
            "order_quantity": quantity,
            "order_price": signal.suggested_price,
            "estimated_quantity": quantity,
            "requested_amount_krw": requested_amount_krw,
            "order_type": order_type,
        }

    async def _upsert_broker_order(
        self,
        *,
        cycle_id: str | None,
        order_id: str,
        symbol: str,
        stock_name: str,
        market: str,
        side: str,
        status: str,
        quantity: float,
        requested_price: float,
        requested_price_krw: float,
        requested_amount_krw: float = 0.0,
        currency: str,
        exchange_rate_to_krw: float,
        order_type: str = "",
        strategy_type: str = "",
        filled_quantity: float = 0,
        filled_price: float = 0.0,
        filled_price_krw: float = 0.0,
        status_detail: str = "",
        error_message: str = "",
        submitted_at=None,
        filled_at=None,
    ) -> BrokerOrder | CoinBrokerOrder | None:
        market_code = normalize_market(market)
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    if is_crypto_market(market_code):
                        existing = await session.scalar(
                            select(CoinBrokerOrder)
                            .where(CoinBrokerOrder.bithumb_order_id == order_id)
                            .limit(1)
                        )
                        if existing is None:
                            existing = CoinBrokerOrder(
                                cycle_id=cycle_id,
                                bithumb_order_id=order_id,
                                symbol=symbol,
                                coin_name=stock_name,
                                side=side,
                                quantity=float(quantity),
                                requested_price=requested_price,
                                requested_amount_krw=float(requested_amount_krw or 0.0),
                                currency=currency,
                                order_type=order_type or "LIMIT",
                                strategy_type=strategy_type,
                            )
                            session.add(existing)

                        existing.cycle_id = cycle_id or existing.cycle_id
                        existing.status = status
                        existing.coin_name = stock_name or existing.coin_name
                        existing.side = side
                        existing.quantity = float(quantity)
                        existing.requested_price = requested_price
                        existing.requested_amount_krw = float(requested_amount_krw or 0.0)
                        existing.currency = currency
                        existing.order_type = order_type or existing.order_type
                        existing.strategy_type = strategy_type or existing.strategy_type
                        existing.filled_quantity = float(filled_quantity)
                        existing.filled_price = filled_price
                        existing.status_detail = status_detail or None
                        existing.error_message = error_message or None
                        if submitted_at is not None:
                            existing.submitted_at = submitted_at
                        if filled_at is not None:
                            existing.filled_at = filled_at
                        await session.flush()
                        return existing

                    existing = await session.scalar(
                        select(BrokerOrder).where(BrokerOrder.kis_order_id == order_id).limit(1)
                    )
                    int_quantity = int(float(quantity or 0.0))
                    int_filled_quantity = int(float(filled_quantity or 0.0))
                    if existing is None:
                        existing = BrokerOrder(
                            cycle_id=cycle_id,
                            kis_order_id=order_id,
                            symbol=symbol,
                            stock_name=stock_name,
                            market=market_code,
                            side=side,
                            quantity=int_quantity,
                            requested_price=requested_price,
                            requested_price_krw=requested_price_krw,
                            currency=currency,
                            exchange_rate_to_krw=exchange_rate_to_krw,
                            strategy_type=strategy_type,
                        )
                        session.add(existing)

                    existing.cycle_id = cycle_id or existing.cycle_id
                    existing.status = status
                    existing.stock_name = stock_name or existing.stock_name
                    existing.market = market_code
                    existing.side = side
                    existing.quantity = int_quantity
                    existing.requested_price = requested_price
                    existing.requested_price_krw = requested_price_krw
                    existing.currency = currency
                    existing.exchange_rate_to_krw = exchange_rate_to_krw
                    existing.strategy_type = strategy_type or existing.strategy_type
                    existing.filled_quantity = int_filled_quantity
                    existing.filled_price = filled_price
                    existing.filled_price_krw = filled_price_krw
                    existing.status_detail = status_detail or None
                    existing.error_message = error_message or None
                    if submitted_at is not None:
                        existing.submitted_at = submitted_at
                    if filled_at is not None:
                        existing.filled_at = filled_at
                    await session.flush()
                    return existing
        except Exception:
            logger.exception(
                "[BrokerOrder] 저장 실패: order_id={} symbol={} market={} status={}",
                order_id,
                symbol,
                market,
                status,
            )
            return None

    async def _load_broker_order(
        self,
        order_id: str,
        market: str | None = None,
    ) -> BrokerOrder | CoinBrokerOrder | None:
        market_code = normalize_market(market) if market else ""
        async with AsyncSessionLocal() as session:
            if market_code and is_crypto_market(market_code):
                return await session.scalar(
                    select(CoinBrokerOrder)
                    .where(CoinBrokerOrder.bithumb_order_id == order_id)
                    .limit(1)
                )

            stock_order = await session.scalar(
                select(BrokerOrder).where(BrokerOrder.kis_order_id == order_id).limit(1)
            )
            if stock_order is not None or market_code:
                return stock_order

            return await session.scalar(
                select(CoinBrokerOrder)
                .where(CoinBrokerOrder.bithumb_order_id == order_id)
                .limit(1)
            )

    @staticmethod
    def _matched_order_from_broker_record(
        record: BrokerOrder | CoinBrokerOrder | None,
        market: str,
    ) -> dict | None:
        market_code = normalize_market(market)
        if record is None:
            return None

        if is_crypto_market(market_code) and isinstance(record, CoinBrokerOrder):
            quantity = float(record.quantity or 0.0)
            filled_quantity = float(record.filled_quantity or 0.0)
            remaining_quantity = max(quantity - filled_quantity, 0.0)
            return {
                "order_id": record.bithumb_order_id,
                "market": market_code,
                "symbol": record.symbol,
                "name": record.coin_name,
                "status": record.status,
                "order_type": str(record.order_type or ""),
                "order_qty": quantity,
                "filled_qty": filled_quantity,
                "filled_quantity": filled_quantity,
                "remaining_qty": remaining_quantity,
                "order_price": float(record.requested_price or 0.0),
                "requested_amount_krw": float(record.requested_amount_krw or 0.0),
                "filled_price": float(record.filled_price or 0.0),
                "currency": record.currency or "KRW",
                "exchange_rate_to_krw": 1.0,
            }

        if isinstance(record, BrokerOrder):
            quantity = float(record.quantity or 0.0)
            filled_quantity = float(record.filled_quantity or 0.0)
            remaining_quantity = max(quantity - filled_quantity, 0.0)
            return {
                "order_id": record.kis_order_id,
                "market": market_code,
                "symbol": record.symbol,
                "name": record.stock_name,
                "status": record.status,
                "order_qty": quantity,
                "filled_qty": filled_quantity,
                "filled_quantity": filled_quantity,
                "remaining_qty": remaining_quantity,
                "order_price": float(record.requested_price or 0.0),
                "filled_price": float(record.filled_price or 0.0),
                "currency": record.currency or "KRW",
                "exchange_rate_to_krw": float(record.exchange_rate_to_krw or 1.0),
            }
        return None

    @staticmethod
    def _coin_ws_activity_phase(status: str) -> ActivityPhase:
        if status == "FILLED":
            return ActivityPhase.COMPLETE
        if status == "CANCELED":
            return ActivityPhase.SKIP
        return ActivityPhase.PROGRESS

    @staticmethod
    def _coin_ws_status_label(status: str) -> str:
        labels = {
            "SUBMITTED": "주문 접수",
            "OPEN": "주문 대기",
            "PARTIAL": "부분 체결",
            "FILLED": "체결 완료",
            "CANCELED": "주문 취소",
        }
        return labels.get(status, status or "상태 변경")

    @staticmethod
    def _coin_ws_side_label(side: str) -> str:
        return coin_side_label(side)

    async def _log_coin_ws_order_activity(
        self,
        *,
        cycle_id: str | None,
        symbol: str,
        side: str,
        status: str,
        quantity: float,
        filled_qty: float,
        detail: dict,
    ) -> None:
        """코인 주문 상태 변경을 activity feed에 남긴다."""
        remaining_qty = max(quantity - filled_qty, 0.0)
        phase = self._coin_ws_activity_phase(status)
        side_label = self._coin_ws_side_label(side)
        summary = f"[{symbol}] {side_label} {self._coin_ws_status_label(status)}"
        if status in {"PARTIAL", "FILLED"}:
            summary += f" ({self._quantity_text(filled_qty, 'BITHUMB')} / {self._quantity_text(quantity, 'BITHUMB')})"
        elif status in {"SUBMITTED", "OPEN"}:
            summary += f" (미체결 {self._quantity_text(remaining_qty, 'BITHUMB')})"

        await activity_logger.log(
            ActivityType.ORDER,
            phase,
            summary,
            cycle_id=cycle_id,
            symbol=symbol,
            detail=detail,
            market_scope=normalize_market_scope("BITHUMB"),
        )

    async def sync_coin_ws_order(self, order_data: dict | None) -> dict | None:
        """Private MyOrder 수신 시 coin broker ledger를 즉시 동기화."""
        payload = order_data or {}
        order_id = str(payload.get("order_id") or "").strip()
        symbol = str(payload.get("symbol") or "").upper().strip()
        market_code = normalize_market(
            payload.get("market") or settings.crypto_primary_market_code,
            default=settings.crypto_primary_market_code,
        )
        if not order_id or not symbol or not is_crypto_market(market_code):
            return None

        existing = await self._load_broker_order(order_id, market_code)
        prev_status = str(getattr(existing, "status", "") or "")
        prev_filled_qty = float(getattr(existing, "filled_quantity", 0.0) or 0.0)

        status = str(payload.get("status") or payload.get("state") or "SUBMITTED").upper()
        quantity = self._order_quantity(
            payload.get("order_qty") or payload.get("volume"),
            market_code,
        )
        requested_amount_krw = float(
            payload.get("requested_amount_krw")
            or getattr(existing, "requested_amount_krw", 0.0)
            or 0.0
        )
        if quantity <= 0 and requested_amount_krw <= 0:
            return None

        filled_qty = self._order_quantity(
            payload.get("filled_qty") or payload.get("filled_quantity"),
            market_code,
        )
        filled_price = float(payload.get("filled_price") or payload.get("order_price") or 0.0)
        side = str(payload.get("side") or getattr(existing, "side", "") or "").upper()
        order_type = str(
            payload.get("order_type")
            or payload.get("ord_type")
            or getattr(existing, "order_type", "")
            or ("PRICE" if side == "BUY" and requested_amount_krw > 0 else "")
        ).upper()

        # 상태·체결수량 변경 없으면 DB upsert 및 후속 처리 생략
        if status == prev_status and filled_qty <= prev_filled_qty:
            return None

        detail_text = json.dumps(payload, ensure_ascii=False, default=str)
        record = await self._upsert_broker_order(
            cycle_id=getattr(existing, "cycle_id", None),
            order_id=order_id,
            symbol=symbol,
            stock_name=str(payload.get("name") or getattr(existing, "coin_name", "") or symbol),
            market=market_code,
            side=side,
            status=status,
            quantity=quantity,
            requested_price=float(payload.get("order_price") or getattr(existing, "requested_price", 0.0) or 0.0),
            requested_price_krw=float(payload.get("order_price") or getattr(existing, "requested_price", 0.0) or 0.0),
            requested_amount_krw=requested_amount_krw,
            currency=str(payload.get("currency") or getattr(existing, "currency", "KRW") or "KRW"),
            exchange_rate_to_krw=float(payload.get("exchange_rate_to_krw") or 1.0),
            order_type=order_type,
            strategy_type=str(getattr(existing, "strategy_type", "") or ""),
            filled_quantity=filled_qty,
            filled_price=filled_price,
            filled_price_krw=filled_price,
            status_detail=detail_text,
            error_message="",
            submitted_at=payload.get("submitted_at"),
            filled_at=payload.get("filled_at") if status in {"FILLED", "PARTIAL"} else None,
        )

        from trading.account_manager import account_manager

        account_manager.invalidate_cache()
        logger.info(
            "[CoinWS] 주문 상태 동기화: {} {} {} → {} (filled={})",
            symbol,
            order_id,
            prev_status or "NEW",
            status,
            self._quantity_text(filled_qty, market_code),
        )
        await self._log_coin_ws_order_activity(
            cycle_id=getattr(record, "cycle_id", None) if record is not None else getattr(existing, "cycle_id", None),
            symbol=symbol,
            side=side,
            status=status,
            quantity=quantity,
            filled_qty=filled_qty,
            detail=payload,
        )

        # SELL 체결 시 trade_result 종료 처리
        if side == "SELL" and status == "FILLED" and filled_qty > 0:
            try:
                await self._record_trade_result(
                    symbol=symbol,
                    market=market_code,
                    side=side,
                    order_id=order_id,
                    filled_qty=filled_qty,
                    filled_price=filled_price,
                    currency=str(
                        payload.get("currency")
                        or getattr(existing, "currency", "KRW")
                        or "KRW"
                    ),
                    exchange_rate_to_krw=float(
                        payload.get("exchange_rate_to_krw") or 1.0
                    ),
                    exit_reason="WS_FILL",
                    cycle_id=(
                        getattr(record, "cycle_id", None)
                        if record is not None
                        else getattr(existing, "cycle_id", None)
                    ),
                )
            except Exception as e:
                logger.error("[CoinWS] SELL trade_result 기록 실패 ({}): {}", symbol, str(e))

        return {
            "reason": "order_update",
            "symbol": symbol,
            "order_id": order_id,
            "status": status,
            "filled_qty": filled_qty,
            "remaining_qty": max(quantity - filled_qty, 0.0),
        }

    @staticmethod
    def _extract_broker_order_payload(activity: AgentActivityLog) -> dict | None:
        if not activity.detail:
            return None

        try:
            detail = json.loads(activity.detail)
        except Exception:
            return None

        if not detail.get("success"):
            return None

        order_id = str(detail.get("order_id") or "").strip()
        if not order_id:
            return None

        response_data = detail.get("data") or {}
        market_code = normalize_market(
            response_data.get("market")
            or detail.get("market")
            or ("KRX" if normalize_market_scope(activity.market_scope or "KRX") == "KRX" else "NASDAQ")
        )
        currency = str(
            detail.get("currency")
            or response_data.get("currency")
            or ("USD" if is_us_market(market_code) else "KRW")
        )
        requested_price = float(detail.get("requested_price") or 0.0)
        requested_price_krw = float(detail.get("requested_price_krw") or 0.0)
        exchange_rate = float(response_data.get("exchange_rate_to_krw") or 0.0)
        if exchange_rate <= 0:
            if currency != "KRW" and requested_price > 0 and requested_price_krw > 0:
                exchange_rate = requested_price_krw / requested_price
            else:
                exchange_rate = 1.0

        output = response_data.get("output") or {}
        if isinstance(output, list):
            output = output[0] if output else {}

        quantity = DecisionMaker._order_quantity(
            detail.get("requested_quantity")
            or output.get("ORD_QTY")
            or output.get("ord_qty")
            or response_data.get("filled_quantity"),
            market_code,
        )
        requested_amount_krw = float(detail.get("requested_amount_krw") or 0.0)
        if quantity <= 0 and requested_amount_krw <= 0:
            return None

        side = str(detail.get("action") or "").upper()
        if side not in {"BUY", "SELL"}:
            return None

        symbol = str(activity.symbol or detail.get("symbol") or "").upper()
        if not symbol:
            return None

        return {
            "cycle_id": activity.cycle_id,
            "order_id": order_id,
            "symbol": symbol,
            "stock_name": symbol,
            "market": market_code,
            "side": side,
            "status": "SUBMITTED",
            "quantity": quantity,
            "requested_price": requested_price,
            "requested_price_krw": requested_price_krw,
            "requested_amount_krw": requested_amount_krw,
            "currency": currency,
            "exchange_rate_to_krw": exchange_rate,
            "order_type": str(
                detail.get("order_type")
                or ("PRICE" if is_crypto_market(market_code) and side == "BUY" and requested_amount_krw > 0 else "LIMIT")
            ),
            "strategy_type": "",
            "status_detail": str(response_data.get("msg1") or detail.get("message") or "주문 접수"),
            "submitted_at": activity.created_at,
        }

    async def repair_recent_broker_orders(
        self,
        *,
        market_scope: str | None = None,
        trading_date=None,
    ) -> int:
        """activity log에만 남은 주문 접수 건을 broker ledger로 복구"""
        repaired = 0
        target_scope = normalize_market_scope(market_scope) if market_scope else None

        async with AsyncSessionLocal() as session:
            stmt = (
                select(AgentActivityLog)
                .where(
                    AgentActivityLog.activity_type == ActivityType.DECISION,
                    AgentActivityLog.phase == ActivityPhase.COMPLETE,
                )
                .order_by(AgentActivityLog.created_at.asc())
            )
            if target_scope:
                stmt = stmt.where(AgentActivityLog.market_scope == target_scope)
            if trading_date is not None:
                stmt = stmt.where(AgentActivityLog.trading_date == trading_date)
            rows = list((await session.execute(stmt)).scalars().all())

        for activity in rows:
            payload = self._extract_broker_order_payload(activity)
            if not payload:
                continue
            if await self._load_broker_order(payload["order_id"], payload["market"]):
                continue
            record = await self._upsert_broker_order(**payload)
            if record:
                repaired += 1

        if repaired:
            logger.warning(
                "[BrokerOrder] activity log 기반 누락 주문 {}건 복구 (market_scope={})",
                repaired,
                target_scope or "ALL",
            )
        return repaired

    async def repair_stale_open_trade_results(
        self,
        *,
        market_scope: str | None = None,
    ) -> int:
        """실보유가 없는 stale open BUY를 broker_orders FILLED 기준으로 close 처리"""
        from realtime.event_detector import event_detector
        from trading.account_manager import account_manager
        from trading.market_profile import markets_for_scope

        target_scope = normalize_market_scope(market_scope) if market_scope else None
        if target_scope == "CRYPTO":
            return 0

        representative_market = markets_for_scope(target_scope or settings.primary_market_code)[0]
        try:
            holdings = await account_manager.get_holdings(representative_market)
        except Exception as e:
            logger.warning(
                "[TradeResult] stale open 복구 스킵 — holdings 조회 실패 (market_scope={}): {}",
                target_scope or "ALL",
                str(e),
            )
            return 0

        holding_keys = {
            self._position_key(
                getattr(holding, "symbol", ""),
                getattr(holding, "market", None) or representative_market,
            )
            for holding in holdings
            if getattr(holding, "symbol", None) and float(getattr(holding, "quantity", 0) or 0) > 0
        }

        repaired = 0
        repaired_symbols: list[tuple[str, str]] = []

        async with AsyncSessionLocal() as session:
            async with session.begin():
                repo = TradeResultRepository(session)
                open_positions = await repo.get_all_open(market_scope=target_scope)

                for open_trade in open_positions:
                    position_key = self._position_key(open_trade.stock_symbol, open_trade.market)
                    if position_key in holding_keys:
                        continue

                    entry_at = ensure_kst(open_trade.entry_at) if open_trade.entry_at else None
                    sell_orders = list(
                        (
                            await session.execute(
                                select(BrokerOrder)
                                .where(BrokerOrder.market == open_trade.market)
                                .where(BrokerOrder.symbol == open_trade.stock_symbol)
                                .where(BrokerOrder.side == "SELL")
                                .where(BrokerOrder.status == "FILLED")
                                .order_by(BrokerOrder.filled_at.desc(), BrokerOrder.created_at.desc())
                            )
                        ).scalars().all()
                    )

                    matched_order = None
                    for sell_order in sell_orders:
                        filled_at = self._broker_order_filled_at(sell_order)
                        if entry_at and filled_at and filled_at < entry_at:
                            continue
                        matched_order = sell_order
                        break

                    if matched_order is None:
                        logger.warning(
                            "[TradeResult] stale open 유지: {} {} 실보유 없음 + SELL FILLED 부재",
                            open_trade.market,
                            open_trade.stock_symbol,
                        )
                        continue

                    exit_at = self._broker_order_filled_at(matched_order) or now_kst()
                    exit_price = float(matched_order.filled_price or 0.0)
                    exit_exchange_rate = float(
                        matched_order.exchange_rate_to_krw
                        or open_trade.exchange_rate_to_krw
                        or 1.0
                    )
                    exit_price_krw = float(
                        matched_order.filled_price_krw
                        or self._to_trade_krw(
                            exit_price,
                            matched_order.currency or open_trade.currency,
                            exit_exchange_rate,
                        )
                    )
                    quantity = int(open_trade.quantity or 0)
                    entry_price = float(open_trade.entry_price or 0.0)
                    raw_pnl = (exit_price - entry_price) * quantity if entry_price > 0 else 0.0
                    pnl = self._to_trade_krw(raw_pnl, open_trade.currency, exit_exchange_rate)
                    return_pct = ((exit_price - entry_price) / entry_price * 100) if entry_price > 0 else 0.0
                    hold_days = max(0, (exit_at - entry_at).days) if entry_at else 0

                    open_trade.exit_at = exit_at
                    open_trade.exit_price = exit_price
                    open_trade.exit_price_krw = exit_price_krw
                    open_trade.raw_pnl = raw_pnl
                    open_trade.pnl = pnl
                    open_trade.return_pct = round(return_pct, 2)
                    open_trade.is_win = pnl > 0
                    open_trade.hold_days = hold_days
                    open_trade.exit_reason = "BROKER_RECONCILE"
                    open_trade.notes = self._mark_broker_reconciled_trade_notes(
                        getattr(open_trade, "notes", None),
                        broker_order=matched_order,
                    )
                    repaired += 1
                    repaired_symbols.append(position_key)

        for market_code, symbol in repaired_symbols:
            event_detector.remove_levels(symbol, market=market_code)

        if repaired:
            logger.warning(
                "[TradeResult] stale open {}건 broker_orders 기준 자동 복구 (market_scope={})",
                repaired,
                target_scope or "ALL",
            )
        return repaired

    async def execute(
        self, signal: TradeSignal, analysis_id: str = "", cycle_id: str | None = None,
        analysis_context: dict | None = None,
    ) -> dict:
        """시그널에 따라 실행"""
        market_code = normalize_market(signal.metadata.get("market", "KRX"))
        mode = AutonomyMode(settings.autonomy_mode_for_market(market_code))

        if mode == AutonomyMode.AUTONOMOUS:
            return await self._execute_autonomous(signal, analysis_id, cycle_id, analysis_context)
        else:
            return await self._create_recommendation(signal, analysis_id, cycle_id)

    async def _execute_autonomous(
        self, signal: TradeSignal, analysis_id: str = "", cycle_id: str | None = None,
        analysis_context: dict | None = None,
    ) -> dict:
        """완전자율: MCP로 즉시 주문 실행"""
        logger.info(
            "[AUTONOMOUS] 주문 실행: {} {} qty={} price={} amount={}",
            signal.symbol, signal.action.value,
            signal.suggested_quantity, signal.suggested_price, signal.suggested_amount_krw,
        )

        market = signal.metadata.get("market", "KRX")
        market_code = normalize_market(market)
        currency = signal.metadata.get("currency", "KRW")
        exchange_rate = float(signal.metadata.get("exchange_rate_to_krw") or 1.0)
        qty = normalize_quantity(signal.suggested_quantity, market_code)
        price = signal.suggested_price or 0
        price_krw = float(
            signal.metadata.get("entry_price_krw")
            or signal.metadata.get("price_krw")
            or (price * exchange_rate if currency != "KRW" else price)
        )
        order_request = self._resolve_order_request(signal, market_code=market_code, price_krw=price_krw)
        order_quantity = float(order_request["order_quantity"] or 0.0)
        order_price = order_request["order_price"]
        requested_amount_krw = float(order_request["requested_amount_krw"] or 0.0)
        estimated_quantity = float(order_request["estimated_quantity"] or 0.0)
        order_type = str(order_request["order_type"] or "")
        amount = requested_amount_krw
        order_summary = (
            f"{amount:,.0f}원 (예상 {format_quantity_with_unit(estimated_quantity, market_code)})"
            if is_crypto_market(market_code) and signal.action == SignalAction.BUY
            else f"{format_quantity_with_unit(qty, market_code)} {self._price_display(price, currency, price_krw)}"
        )
        await activity_logger.log(
            ActivityType.DECISION, ActivityPhase.START,
            f"\U0001f4b0 [{signal.symbol}] 자동 주문 실행: {signal.action.value} {order_summary}",
            cycle_id=cycle_id,
            symbol=signal.symbol,
            detail=self._enrich_detail(
                signal,
                {
                    "market": market_code,
                    "currency": currency,
                    "order_type": order_type,
                    "requested_amount_krw": round(requested_amount_krw, 4),
                    "estimated_quantity": estimated_quantity,
                    "requested_quantity": qty,
                    "requested_price": price,
                    "requested_price_krw": round(price_krw, 4),
                    "live_price": signal.metadata.get("live_price"),
                    "live_price_krw": signal.metadata.get("live_price_krw"),
                },
            ),
        )

        sanity_issue = self._detect_order_price_sanity_issue(signal)
        if sanity_issue:
            result = {
                "mode": "AUTONOMOUS",
                "symbol": signal.symbol,
                "action": signal.action.value,
                "success": False,
                "order_id": "",
                "message": sanity_issue["reason"],
                "data": sanity_issue,
            }
            await activity_logger.log(
                ActivityType.DECISION, ActivityPhase.ERROR,
                f"\u274c [{signal.symbol}] 주문 차단: 실시간가 대비 지정가 괴리 {sanity_issue['gap_pct']:.1%}",
                cycle_id=cycle_id,
                symbol=signal.symbol,
                error_message=sanity_issue["reason"],
                detail=self._enrich_detail(signal, sanity_issue),
            )
            await event_bus.publish(Event(
                type=EventType.ORDER_EXECUTED,
                data=result,
                source="decision_maker",
            ))
            return result

        paper_session_block = self._detect_paper_us_session_block(signal)
        if paper_session_block:
            await activity_logger.log(
                ActivityType.DECISION, ActivityPhase.ERROR,
                f"⚠️ [{signal.symbol}] {paper_session_block['session_label']} 모의주문 불가 — 추천으로 전환",
                cycle_id=cycle_id,
                symbol=signal.symbol,
                error_message=paper_session_block["reason"],
                detail=self._enrich_detail(signal, paper_session_block),
            )
            recommendation = await self._create_recommendation(signal, analysis_id, cycle_id)
            recommendation["mode"] = "AUTONOMOUS_FALLBACK"
            recommendation["fallback_reason"] = paper_session_block["reason"]
            recommendation["fallback_session"] = paper_session_block["session"]
            return recommendation

        if signal.action == SignalAction.BUY and not is_crypto_market(market_code):
            dedupe_issue = await self._find_stock_buy_dedupe_issue(
                symbol=signal.symbol,
                market=market_code,
            )
            if dedupe_issue:
                return await self._reject_duplicate_stock_buy(
                    signal=signal,
                    cycle_id=cycle_id,
                    dedupe_issue=dedupe_issue,
                    phase_label="중복 BUY 차단",
                )

        if signal.action == SignalAction.BUY and not is_crypto_market(market_code):
            dedupe_issue = await self._find_stock_buy_dedupe_issue(
                symbol=signal.symbol,
                market=market_code,
            )
            if dedupe_issue:
                return await self._reject_duplicate_stock_buy(
                    signal=signal,
                    cycle_id=cycle_id,
                    dedupe_issue=dedupe_issue,
                    phase_label="주문 직전 중복 BUY 차단",
                )

        response = await mcp_client.place_order(
            symbol=signal.symbol,
            side=signal.action.value,
            quantity=order_quantity,
            price=order_price,
            market=market,
        )

        # 주문 응답 검증: MCP success + 주문번호 존재 확인
        # mcp_client.place_order()가 이미 order_id를 정규화함
        order_data = response.data or {}
        order_id = order_data.get("order_id", "")
        is_submitted = response.success and bool(order_id)

        result = {
            "mode": "AUTONOMOUS",
            "symbol": signal.symbol,
            "action": signal.action.value,
            "success": is_submitted,
            "order_id": order_id,
            "message": "주문 접수" if is_submitted else (response.error or "주문 응답 없음"),
            "requested_price": price,
            "requested_price_krw": round(price_krw, 4),
            "requested_amount_krw": round(requested_amount_krw, 4),
            "estimated_quantity": estimated_quantity,
            "order_type": order_type,
            "currency": currency,
            "data": response.data,
        }

        if is_submitted:
            order_submitted_at = now_kst()
            broker_order = await self._upsert_broker_order(
                cycle_id=cycle_id,
                order_id=order_id,
                symbol=signal.symbol,
                stock_name=str((analysis_context or {}).get("stock_name", signal.symbol)),
                market=market_code,
                side=signal.action.value,
                status="SUBMITTED",
                quantity=qty,
                requested_price=float(price or 0.0),
                requested_price_krw=float(price_krw or 0.0),
                requested_amount_krw=float(requested_amount_krw or 0.0),
                currency=str(currency),
                exchange_rate_to_krw=float(exchange_rate or 1.0),
                order_type=order_type,
                strategy_type=str((analysis_context or {}).get("strategy_type", "")),
                status_detail=str(order_data.get("msg1") or "주문 접수"),
                submitted_at=order_submitted_at,
            )
            result["broker_order_recorded"] = broker_order is not None
            if broker_order is not None:
                await activity_logger.log(
                    ActivityType.DECISION, ActivityPhase.COMPLETE,
                    f"\u2705 [{signal.symbol}] 주문 접수 완료 (체결 대기) — 주문번호: {order_id}",
                    cycle_id=cycle_id, symbol=signal.symbol,
                    detail=self._enrich_detail(signal, result),
                )
            else:
                ledger_error = "broker ledger 저장 실패"
                result["ledger_error"] = ledger_error
                await activity_logger.log(
                    ActivityType.DECISION, ActivityPhase.ERROR,
                    f"⚠️ [{signal.symbol}] 주문은 접수됐지만 broker ledger 저장 실패 — 주문번호: {order_id}",
                    cycle_id=cycle_id,
                    symbol=signal.symbol,
                    error_message=ledger_error,
                    detail=self._enrich_detail(signal, result),
                )
            # 체결 확인 + TradeResult 기록 (백그라운드, 매매 흐름 차단 안 함)
            task = asyncio.create_task(
                self.confirm_and_record(
                    symbol=signal.symbol,
                    market=market,
                    side=signal.action.value,
                    order_id=order_id,
                    quantity=qty,
                    expected_price=price,
                    requested_amount_krw=requested_amount_krw,
                    analysis_context=analysis_context,
                    cycle_id=cycle_id,
                )
            )
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)
        else:
            error_msg = response.error or order_data.get("msg1") or "주문번호 없음"
            if signal.action == SignalAction.BUY:
                event_detector.clear_trade_thresholds(signal.symbol, market=market_code)
            # 매매불가 종목 → 런타임 블록리스트 등록 (이후 스캔에서 제외)
            if "매매불가" in error_msg:
                from agent.market_scanner import market_scanner
                market_scanner.add_untradeable(signal.symbol, market=market)
                logger.warning("매매불가 종목 블록리스트 등록: {} → 이후 스캔에서 제외", signal.symbol)
            await activity_logger.log(
                ActivityType.DECISION, ActivityPhase.ERROR,
                f"\u274c [{signal.symbol}] 주문 실패: {error_msg}",
                cycle_id=cycle_id, symbol=signal.symbol,
                error_message=error_msg,
                detail=self._enrich_detail(
                    signal,
                    {
                        **result,
                        "market": market_code,
                        "response_success": response.success,
                    },
                ),
            )

        await event_bus.publish(Event(
            type=EventType.ORDER_EXECUTED,
            data=result,
            source="decision_maker",
        ))

        return result

    async def on_order_notification(self, notification: dict) -> None:
        """WebSocket 체결통보 콜백 — 대기 중인 주문에 이벤트 전달"""
        order_id = str(notification.get("order_id", ""))
        if order_id in self._pending_order_events:
            self._pending_order_data[order_id] = notification
            self._pending_order_events[order_id].set()
            logger.info(
                "[{}] WebSocket 체결통보 수신: {} {}주 @{}",
                notification.get("symbol"),
                notification.get("side"),
                notification.get("filled_qty"),
                notification.get("filled_price"),
            )

    async def confirm_and_record(
        self,
        symbol: str,
        market: str,
        side: str,
        order_id: str,
        quantity: float,
        expected_price: float,
        requested_amount_krw: float = 0.0,
        analysis_context: dict | None = None,
        cycle_id: str | None = None,
        exit_reason: str = "",
    ) -> None:
        """주문 접수 후 체결 확인 → TradeResult 기록

        해외장은 체결 반영이 늦을 수 있어 짧은 backoff polling 후 기록한다.
        """
        try:
            ctx = analysis_context or {}
            market_code = normalize_market(market)
            currency = str(ctx.get("currency") or ("USD" if is_us_market(market_code) else "KRW"))
            exchange_rate = float(ctx.get("exchange_rate_to_krw") or 0.0)
            if exchange_rate <= 0:
                exchange_rate = await mcp_client._get_exchange_rate_to_krw(market_code)

            matched_order: dict | None = None
            broker_record = await self._load_broker_order(order_id, market_code)
            broker_snapshot = self._matched_order_from_broker_record(broker_record, market_code)
            if broker_snapshot is not None:
                broker_filled_qty = self._order_quantity(
                    broker_snapshot.get("filled_qty") or broker_snapshot.get("filled_quantity"),
                    market_code,
                )
                if broker_filled_qty > 0:
                    matched_order = broker_snapshot

            # WebSocket 체결통보 대기 (최대 5초) — 코인은 별도 체결 확인
            if matched_order is None and not is_crypto_market(market_code):
                ws_event = asyncio.Event()
                self._pending_order_events[str(order_id)] = ws_event
                try:
                    await asyncio.wait_for(ws_event.wait(), timeout=5.0)
                    ws_notification = self._pending_order_data.pop(str(order_id), None)
                    if ws_notification and ws_notification.get("is_filled"):
                        matched_order = {
                            "order_id": order_id,
                            "filled_qty": ws_notification["filled_qty"],
                            "filled_price": ws_notification["filled_price"],
                            "status": "FILLED",
                            "currency": ws_notification.get("currency", currency),
                        }
                        logger.info("[{}] 주문 {} WebSocket 체결통보로 확인", symbol, order_id)
                except asyncio.TimeoutError:
                    logger.debug("[{}] 주문 {} WebSocket 체결통보 타임아웃 → REST polling", symbol, order_id)
                finally:
                    self._pending_order_events.pop(str(order_id), None)
                    self._pending_order_data.pop(str(order_id), None)

            for delay_seconds in _OVERSEAS_CONFIRM_DELAYS_SECONDS:
                if matched_order is not None:
                    existing_filled_qty_value = matched_order.get("filled_qty")
                    if existing_filled_qty_value in (None, ""):
                        existing_filled_qty_value = matched_order.get("filled_quantity")
                    existing_filled_qty = self._order_quantity(existing_filled_qty_value, market_code)
                    if existing_filled_qty > 0:
                        break
                await asyncio.sleep(delay_seconds)

                if is_crypto_market(market_code):
                    resp = await mcp_client.get_order(order_id=order_id, market=market)
                else:
                    resp = await mcp_client.get_order_list(market=market)
                if not resp.success:
                    logger.warning("[{}] 주문내역 조회 실패: {}", symbol, resp.error)
                    record = await self._upsert_broker_order(
                        cycle_id=cycle_id,
                        order_id=order_id,
                        symbol=symbol,
                        stock_name=str(ctx.get("stock_name", symbol)),
                        market=market_code,
                        side=side,
                        status="SUBMITTED",
                        quantity=quantity,
                        requested_price=float(expected_price or 0.0),
                        requested_price_krw=self._to_trade_krw(float(expected_price or 0.0), currency, exchange_rate),
                        currency=currency,
                        exchange_rate_to_krw=exchange_rate,
                        strategy_type=str(ctx.get("strategy_type", "")),
                        status_detail=str(resp.error or "주문내역 조회 실패"),
                        error_message=str(resp.error or ""),
                    )
                    if record is None:
                        await activity_logger.log(
                            ActivityType.DECISION,
                            ActivityPhase.ERROR,
                            f"⚠️ [{symbol}] broker ledger 업데이트 실패 — 주문번호: {order_id}",
                            cycle_id=cycle_id,
                            symbol=symbol,
                            error_message="broker ledger 저장 실패",
                        )
                    continue

                logger.debug("[체결확인] 주문 조회 응답: {}", str(resp.data)[:500])

                orders = []
                if is_crypto_market(market_code) and isinstance(resp.data, dict):
                    orders = [resp.data]
                elif isinstance(resp.data, dict):
                    orders = (
                        resp.data.get("output", [])
                        or resp.data.get("output1", [])
                        or resp.data.get("orders", [])
                    )
                    if isinstance(orders, dict):
                        orders = [orders]
                elif isinstance(resp.data, list):
                    orders = resp.data

                current_match = None
                for order in orders:
                    if not isinstance(order, dict):
                        continue
                    if str(order.get("order_id") or order.get("odno") or order.get("ODNO") or "") == str(order_id):
                        current_match = order
                        break
                if current_match is None:
                    continue
                matched_order = current_match
                current_filled_qty_value = current_match.get("filled_qty")
                if current_filled_qty_value in (None, ""):
                    current_filled_qty_value = current_match.get("filled_quantity")
                current_filled_qty = self._order_quantity(
                    current_filled_qty_value,
                    market_code,
                )
                if current_filled_qty > 0:
                    break

            if not matched_order:
                if side == "BUY":
                    event_detector.clear_trade_thresholds(symbol, market=market_code)
                logger.info("[{}] 주문 {} 미체결 (체결내역에서 미발견)", symbol, order_id)
                return

            currency = str(matched_order.get("currency") or currency)
            exchange_rate = float(matched_order.get("exchange_rate_to_krw") or exchange_rate or 1.0)
            filled_qty_value = matched_order.get("filled_qty")
            if filled_qty_value in (None, ""):
                filled_qty_value = matched_order.get("filled_quantity")
            if filled_qty_value in (None, ""):
                filled_qty_value = quantity
            filled_qty = self._order_quantity(
                filled_qty_value,
                market_code,
            )
            remaining_qty = self._order_quantity(matched_order.get("remaining_qty"), market_code)
            requested_price = self._first_positive_float(
                matched_order.get("order_price"),
                expected_price,
            ) or 0.0
            filled_price = self._first_positive_float(
                matched_order.get("filled_price"),
                matched_order.get("avg_prvs"),
                matched_order.get("ccld_pric"),
                requested_price,
            ) or 0.0
            filled_price_krw = self._to_trade_krw(filled_price, currency, exchange_rate)
            status_text = str(matched_order.get("status") or "체결 대기")
            reject_reason = str(matched_order.get("reject_reason") or "")
            resolved_requested_amount_krw = float(
                matched_order.get("requested_amount_krw")
                or requested_amount_krw
                or 0.0
            )
            order_type = str(
                matched_order.get("order_type")
                or matched_order.get("ord_type")
                or ("PRICE" if is_crypto_market(market_code) and side == "BUY" and resolved_requested_amount_krw > 0 else "")
            )

            if filled_qty <= 0:
                open_status = "OPEN" if remaining_qty > 0 else "SUBMITTED"
                record = await self._upsert_broker_order(
                    cycle_id=cycle_id,
                    order_id=order_id,
                    symbol=symbol,
                    stock_name=str(matched_order.get("name") or ctx.get("stock_name", symbol)),
                    market=str(matched_order.get("market") or market_code),
                    side=side,
                    status=open_status,
                    quantity=max(
                        float(quantity),
                        self._order_quantity(matched_order.get("order_qty"), market_code),
                    ),
                    requested_price=float(matched_order.get("order_price") or expected_price or 0.0),
                    requested_price_krw=self._to_trade_krw(
                        float(matched_order.get("order_price") or expected_price or 0.0),
                        currency,
                        exchange_rate,
                    ),
                    requested_amount_krw=resolved_requested_amount_krw,
                    currency=currency,
                    exchange_rate_to_krw=exchange_rate,
                    order_type=order_type,
                    strategy_type=str(ctx.get("strategy_type", "")),
                    filled_quantity=0,
                    filled_price=0.0,
                    filled_price_krw=0.0,
                    status_detail=" / ".join(part for part in (status_text, reject_reason) if part),
                    error_message=reject_reason,
                )
                if record is None:
                    await activity_logger.log(
                        ActivityType.DECISION,
                        ActivityPhase.ERROR,
                        f"⚠️ [{symbol}] broker ledger 업데이트 실패 — 주문번호: {order_id}",
                        cycle_id=cycle_id,
                        symbol=symbol,
                        error_message="broker ledger 저장 실패",
                    )
                if side == "BUY":
                    event_detector.clear_trade_thresholds(symbol, market=market_code)
                logger.info("[{}] 주문 {} 체결수량 0 → 미체결 ({})", symbol, order_id, status_text)
                return

            broker_status = "FILLED" if remaining_qty <= 0 else "PARTIAL"
            record = await self._upsert_broker_order(
                cycle_id=cycle_id,
                order_id=order_id,
                symbol=symbol,
                stock_name=str(matched_order.get("name") or ctx.get("stock_name", symbol)),
                market=str(matched_order.get("market") or market_code),
                side=side,
                status=broker_status,
                quantity=max(
                    float(quantity),
                    self._order_quantity(matched_order.get("order_qty"), market_code) or filled_qty,
                ),
                requested_price=float(matched_order.get("order_price") or expected_price or 0.0),
                requested_price_krw=self._to_trade_krw(
                    float(matched_order.get("order_price") or expected_price or 0.0),
                    currency,
                    exchange_rate,
                ),
                requested_amount_krw=resolved_requested_amount_krw,
                currency=currency,
                exchange_rate_to_krw=exchange_rate,
                order_type=order_type,
                strategy_type=str(ctx.get("strategy_type", "")),
                filled_quantity=filled_qty,
                filled_price=filled_price,
                filled_price_krw=filled_price_krw,
                status_detail=" / ".join(part for part in (status_text, reject_reason) if part),
                error_message=reject_reason,
                filled_at=now_kst(),
            )
            if record is None:
                await activity_logger.log(
                    ActivityType.DECISION,
                    ActivityPhase.ERROR,
                    f"⚠️ [{symbol}] broker ledger 업데이트 실패 — 주문번호: {order_id}",
                    cycle_id=cycle_id,
                    symbol=symbol,
                    error_message="broker ledger 저장 실패",
                )

            logger.info(
                "[체결확인] {} {} 수량 {} @{} 체결 완료 (주문번호: {}, 상태: {})",
                symbol,
                side,
                self._quantity_text(filled_qty, market_code),
                self._format_trade_price(filled_price, currency, exchange_rate),
                order_id,
                broker_status,
            )

            if side == "SELL" and broker_status != "FILLED":
                logger.info(
                    "[체결확인] {} 부분 매도 체결은 broker ledger까지만 반영 (주문번호: {})",
                    symbol,
                    order_id,
                )
                return

            record_analysis_context = dict(ctx)
            if requested_price > 0:
                record_analysis_context.setdefault("requested_price", requested_price)
                record_analysis_context.setdefault(
                    "requested_price_krw",
                    self._to_trade_krw(requested_price, currency, exchange_rate),
                )
            record_meta = await self._record_trade_result(
                symbol=symbol,
                market=market_code,
                side=side,
                order_id=order_id,
                filled_qty=filled_qty,
                filled_price=filled_price,
                currency=currency,
                exchange_rate_to_krw=exchange_rate,
                analysis_context=record_analysis_context,
                exit_reason=exit_reason,
                cycle_id=cycle_id,
            )
            if side == "BUY":
                record_meta_dict = record_meta if isinstance(record_meta, dict) else {}
                self._activate_buy_trade_thresholds(
                    symbol=symbol,
                    market=market_code,
                    analysis_context=record_analysis_context,
                    exit_plan_id=record_meta_dict.get("exit_plan_id"),
                )

            # 체결 확인 후 계좌 캐시 무효화 → 다음 조회 시 최신 반영
            from trading.account_manager import account_manager
            account_manager.invalidate_cache()

        except Exception as e:
            logger.error("[{}] 체결 확인/기록 실패: {}", symbol, str(e))

    async def _record_coin_trade_result(
        self,
        symbol: str,
        market: str,
        side: str,
        order_id: str,
        filled_qty: float,
        filled_price: float,
        analysis_context: dict | None = None,
        exit_reason: str = "",
        cycle_id: str | None = None,
    ) -> dict | None:
        """코인 체결 결과를 coin_trade_results에 기록한다."""
        ctx = dict(analysis_context or {})
        now = now_kst()
        qty_text = self._quantity_text(filled_qty, market)
        price_krw = float(filled_price or 0.0)
        should_sync_exit_plan = False
        should_deactivate_exit_plan = False
        is_add_on = False
        plan_avg_entry_price = 0.0
        plan_total_quantity = 0.0

        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    open_buy = await session.scalar(
                        select(CoinTradeResult)
                        .where(
                            CoinTradeResult.symbol == symbol,
                            CoinTradeResult.side == "BUY",
                            CoinTradeResult.exit_at.is_(None),
                        )
                        .order_by(CoinTradeResult.created_at.desc())
                        .limit(1)
                    )

                    if side == "BUY":
                        is_add_on = open_buy is not None
                        trade_notes = self._build_trade_notes(
                            analysis_context=ctx,
                            market=market,
                            existing_notes=getattr(open_buy, "notes", None) if open_buy else None,
                        )
                        if open_buy:
                            previous_qty = normalize_quantity(open_buy.quantity, market)
                            combined_qty = normalize_quantity(previous_qty + filled_qty, market)
                            if combined_qty <= 0:
                                return None
                            combined_price = (
                                ((open_buy.entry_price or 0.0) * previous_qty)
                                + (price_krw * filled_qty)
                            ) / combined_qty
                            open_buy.order_id = order_id
                            open_buy.coin_name = ctx.get("stock_name", symbol)
                            open_buy.strategy_type = ctx.get("strategy_type", "")
                            open_buy.entry_price = combined_price
                            open_buy.quantity = combined_qty
                            open_buy.ai_recommendation = ctx.get("ai_recommendation", "")
                            open_buy.ai_confidence = ctx.get("ai_confidence", 0.0)
                            open_buy.ai_target_price = ctx.get("ai_target_price")
                            open_buy.ai_stop_loss_price = ctx.get("ai_stop_loss_price")
                            open_buy.entry_rsi = ctx.get("entry_rsi")
                            open_buy.entry_macd_hist = ctx.get("entry_macd_hist")
                            open_buy.entry_bb_position = ctx.get("entry_bb_position")
                            open_buy.market_regime = ctx.get("market_regime", "")
                            open_buy.btc_dominance = ctx.get("btc_dominance")
                            open_buy.entry_24h_volume = ctx.get("entry_24h_volume")
                            open_buy.notes = json.dumps(trade_notes, ensure_ascii=False, default=str)
                            plan_avg_entry_price = combined_price
                            plan_total_quantity = combined_qty
                            logger.info(
                                "[CoinTradeResult] 추가매수 병합 기록: {} {} 추가 → 총 {} @{:,.0f}원",
                                symbol,
                                qty_text,
                                self._quantity_text(combined_qty, market),
                                combined_price,
                            )
                        else:
                            session.add(
                                CoinTradeResult(
                                    order_id=order_id,
                                    symbol=symbol,
                                    coin_name=ctx.get("stock_name", symbol),
                                    side="BUY",
                                    strategy_type=ctx.get("strategy_type", ""),
                                    entry_price=price_krw,
                                    exit_price=0.0,
                                    quantity=filled_qty,
                                    pnl=0.0,
                                    return_pct=0.0,
                                    is_win=False,
                                    hold_hours=0,
                                    ai_recommendation=ctx.get("ai_recommendation", ""),
                                    ai_confidence=ctx.get("ai_confidence", 0.0),
                                    ai_target_price=ctx.get("ai_target_price"),
                                    ai_stop_loss_price=ctx.get("ai_stop_loss_price"),
                                    entry_rsi=ctx.get("entry_rsi"),
                                    entry_macd_hist=ctx.get("entry_macd_hist"),
                                    entry_bb_position=ctx.get("entry_bb_position"),
                                    market_regime=ctx.get("market_regime", ""),
                                    btc_dominance=ctx.get("btc_dominance"),
                                    entry_24h_volume=ctx.get("entry_24h_volume"),
                                    notes=json.dumps(trade_notes, ensure_ascii=False, default=str),
                                    entry_at=now,
                                )
                            )
                            logger.info(
                                "[CoinTradeResult] 매수 기록 생성: {} {} @{:,.0f}원",
                                symbol,
                                qty_text,
                                price_krw,
                            )
                            plan_avg_entry_price = price_krw
                            plan_total_quantity = normalize_quantity(filled_qty, market)

                        should_sync_exit_plan = plan_avg_entry_price > 0 and plan_total_quantity > 0
                        await activity_logger.log(
                            ActivityType.TRADE_RESULT,
                            ActivityPhase.COMPLETE,
                            f"📝 [{symbol}] {'추가매수 체결 기록' if is_add_on else '매수 체결 기록'}: "
                            f"{qty_text} @{price_krw:,.0f}원",
                            cycle_id=cycle_id,
                            symbol=symbol,
                            detail={
                                "market": market,
                                "entry_price": price_krw,
                                "quantity": filled_qty,
                                "is_add_on": is_add_on,
                                "analysis_source": ctx.get("analysis_source"),
                                "event_type": ctx.get("event_type"),
                                "exit_levels": self._build_exit_plan_levels(ctx),
                                "trade_threshold_payload": self._build_trade_threshold_payload(
                                    analysis_context=ctx,
                                ),
                            },
                        )
                    else:
                        trade_notes = self._build_trade_notes(
                            analysis_context=ctx,
                            market=market,
                            existing_notes=getattr(open_buy, "notes", None) if open_buy else None,
                        )

                        if not open_buy:
                            logger.warning("[CoinTradeResult] {} 미청산 매수 기록 없음 → 매도 기록만 생성", symbol)
                            session.add(
                                CoinTradeResult(
                                    order_id=order_id,
                                    symbol=symbol,
                                    coin_name=ctx.get("stock_name", symbol),
                                    side="SELL",
                                    strategy_type=ctx.get("strategy_type", ""),
                                    entry_price=0.0,
                                    exit_price=price_krw,
                                    quantity=filled_qty,
                                    pnl=0.0,
                                    return_pct=0.0,
                                    is_win=False,
                                    hold_hours=0,
                                    exit_reason=exit_reason or "SIGNAL",
                                    notes=json.dumps(trade_notes, ensure_ascii=False, default=str),
                                    entry_at=now,
                                    exit_at=now,
                                )
                            )
                            should_deactivate_exit_plan = True
                        else:
                            open_qty = normalize_quantity(open_buy.quantity, market)
                            sell_qty = normalize_quantity(min(open_qty, float(filled_qty or 0.0)), market)
                            if sell_qty <= 0:
                                return None

                            remaining_qty = normalize_quantity(open_qty - sell_qty, market)
                            entry_price = float(open_buy.entry_price or 0.0)
                            pnl = (price_krw - entry_price) * sell_qty
                            return_pct = ((price_krw - entry_price) / entry_price * 100) if entry_price > 0 else 0.0
                            entry_at = ensure_kst(open_buy.entry_at) if open_buy.entry_at else None
                            hold_hours = (
                                max(0, int((now - entry_at).total_seconds() // 3600))
                                if entry_at
                                else 0
                            )
                            is_win = pnl > 0
                            pnl_sign = "+" if pnl >= 0 else ""

                            if remaining_qty > 0:
                                session.add(
                                    CoinTradeResult(
                                        order_id=order_id,
                                        symbol=symbol,
                                        coin_name=open_buy.coin_name,
                                        side="BUY",
                                        strategy_type=open_buy.strategy_type,
                                        entry_price=entry_price,
                                        exit_price=price_krw,
                                        quantity=sell_qty,
                                        pnl=pnl,
                                        return_pct=round(return_pct, 2),
                                        is_win=is_win,
                                        hold_hours=hold_hours,
                                        exit_reason=exit_reason or "SIGNAL",
                                        ai_recommendation=open_buy.ai_recommendation,
                                        ai_confidence=open_buy.ai_confidence,
                                        ai_target_price=open_buy.ai_target_price,
                                        ai_stop_loss_price=open_buy.ai_stop_loss_price,
                                        entry_rsi=open_buy.entry_rsi,
                                        entry_macd_hist=open_buy.entry_macd_hist,
                                        entry_bb_position=open_buy.entry_bb_position,
                                        market_regime=open_buy.market_regime,
                                        btc_dominance=open_buy.btc_dominance,
                                        entry_24h_volume=open_buy.entry_24h_volume,
                                        notes=json.dumps(trade_notes, ensure_ascii=False, default=str),
                                        entry_at=entry_at,
                                        exit_at=now,
                                    )
                                )
                                open_buy.notes = json.dumps(trade_notes, ensure_ascii=False, default=str)
                                open_buy.quantity = remaining_qty

                                logger.info(
                                    "[CoinTradeResult] 부분 청산: {} {} 진입@{:,.0f}원 → 청산@{:,.0f}원 = {}{:,.0f}원 ({:+.2f}%), 잔여 {}",
                                    symbol,
                                    self._quantity_text(sell_qty, market),
                                    entry_price,
                                    price_krw,
                                    pnl_sign,
                                    abs(pnl),
                                    return_pct,
                                    self._quantity_text(remaining_qty, market),
                                )
                                await activity_logger.log(
                                    ActivityType.TRADE_RESULT,
                                    ActivityPhase.COMPLETE,
                                    f"{'✅' if is_win else '❌'} [{symbol}] 부분 청산: "
                                    f"{pnl_sign}{abs(pnl):,.0f}원 ({return_pct:+.1f}%) "
                                    f"| {exit_reason or 'SIGNAL'} | 잔여 {self._quantity_text(remaining_qty, market)}",
                                    cycle_id=cycle_id,
                                    symbol=symbol,
                                    detail={
                                        "market": market,
                                        "entry_price": entry_price,
                                        "exit_price": price_krw,
                                        "quantity": sell_qty,
                                        "remaining_quantity": remaining_qty,
                                        "pnl": pnl,
                                        "return_pct": return_pct,
                                        "hold_hours": hold_hours,
                                        "is_partial_exit": True,
                                    },
                                )
                            else:
                                open_buy.order_id = order_id
                                open_buy.exit_price = price_krw
                                open_buy.quantity = sell_qty
                                open_buy.pnl = pnl
                                open_buy.return_pct = round(return_pct, 2)
                                open_buy.is_win = is_win
                                open_buy.hold_hours = hold_hours
                                open_buy.exit_reason = exit_reason or "SIGNAL"
                                open_buy.exit_at = now
                                open_buy.notes = json.dumps(trade_notes, ensure_ascii=False, default=str)
                                should_deactivate_exit_plan = True

                                logger.info(
                                    "[CoinTradeResult] 매도 청산: {} {} 진입@{:,.0f}원 → 청산@{:,.0f}원 = {}{:,.0f}원 ({:+.2f}%)",
                                    symbol,
                                    self._quantity_text(sell_qty, market),
                                    entry_price,
                                    price_krw,
                                    pnl_sign,
                                    abs(pnl),
                                    return_pct,
                                )
                                await activity_logger.log(
                                    ActivityType.TRADE_RESULT,
                                    ActivityPhase.COMPLETE,
                                    f"{'✅' if is_win else '❌'} [{symbol}] 매도 청산: "
                                    f"{pnl_sign}{abs(pnl):,.0f}원 ({return_pct:+.1f}%) "
                                    f"| {exit_reason or 'SIGNAL'} | {hold_hours}시간 보유",
                                    cycle_id=cycle_id,
                                    symbol=symbol,
                                    detail={
                                        "market": market,
                                        "entry_price": entry_price,
                                        "exit_price": price_krw,
                                        "quantity": sell_qty,
                                        "pnl": pnl,
                                        "return_pct": return_pct,
                                        "hold_hours": hold_hours,
                                        "is_partial_exit": False,
                                    },
                                )

            if should_sync_exit_plan:
                exit_plan = await self._sync_exit_plan(
                    symbol=symbol,
                    market=market,
                    avg_entry_price=plan_avg_entry_price,
                    total_quantity=plan_total_quantity,
                    analysis_context=ctx,
                    is_add_on=is_add_on,
                    order_id=order_id,
                )
                return {"exit_plan_id": getattr(exit_plan, "id", None) if exit_plan is not None else None}

            if should_deactivate_exit_plan:
                from strategy.exit_plan_manager import exit_plan_manager

                await exit_plan_manager.deactivate_by_symbol(symbol, market)

            return None
        except Exception as e:
            logger.error("[CoinTradeResult] 기록 실패 ({}): {}", symbol, str(e))
            return None

    async def _record_trade_result(
        self,
        symbol: str,
        market: str,
        side: str,
        order_id: str,
        filled_qty: float,
        filled_price: float,
        currency: str,
        exchange_rate_to_krw: float,
        analysis_context: dict | None = None,
        exit_reason: str = "",
        cycle_id: str | None = None,
    ) -> dict | None:
        """체결 확인 후 TradeResult 생성/업데이트"""
        market_code = normalize_market(market)
        if is_crypto_market(market_code):
            return await self._record_coin_trade_result(
                symbol=symbol,
                market=market_code,
                side=side,
                order_id=order_id,
                filled_qty=float(filled_qty or 0.0),
                filled_price=filled_price,
                analysis_context=analysis_context,
                exit_reason=exit_reason,
                cycle_id=cycle_id,
            )
            return None

        filled_qty = int(float(filled_qty or 0.0))
        ctx = dict(analysis_context or {})
        now = now_kst()
        filled_price_krw = self._to_trade_krw(filled_price, currency, exchange_rate_to_krw)
        effective_entry_price = self._first_positive_float(
            filled_price,
            ctx.get("requested_price"),
        ) or 0.0
        effective_entry_price_krw = self._first_positive_float(
            filled_price_krw if filled_price_krw > 0 else None,
            ctx.get("requested_price_krw"),
            self._to_trade_krw(effective_entry_price, currency, exchange_rate_to_krw)
            if effective_entry_price > 0
            else None,
        ) or 0.0
        entry_price_source = (
            "filled"
            if filled_price > 0
            else ("requested_fallback" if effective_entry_price > 0 else "missing")
        )
        if entry_price_source != "filled":
            ctx["entry_price_source"] = entry_price_source

        try:
            exit_plan_id: str | None = None
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    repo = TradeResultRepository(session)
                    current_session = market_calendar.get_market_session(market=market_code)
                    trade_notes = self._build_trade_notes(
                        analysis_context=ctx,
                        market=market,
                    )
                    trade_notes = self._apply_premarket_scalp_trade_notes(
                        trade_notes,
                        market=market_code,
                        session=current_session,
                    )

                    if side == "BUY":
                        open_buy = await repo.get_open_buy(symbol, market=market)
                        is_add_on = open_buy is not None
                        if open_buy:
                            trade_notes = self._build_trade_notes(
                                analysis_context=ctx,
                                market=market,
                                existing_notes=getattr(open_buy, "notes", None),
                            )
                            trade_notes = self._apply_premarket_scalp_trade_notes(
                                trade_notes,
                                market=market_code,
                                session=current_session,
                            )
                            previous_qty = int(open_buy.quantity or 0)
                            combined_qty = previous_qty + filled_qty
                            if effective_entry_price > 0:
                                combined_price = (
                                    ((open_buy.entry_price or 0.0) * previous_qty)
                                    + (effective_entry_price * filled_qty)
                                ) / combined_qty
                                combined_price_krw = (
                                    ((open_buy.entry_price_krw or 0.0) * previous_qty)
                                    + (effective_entry_price_krw * filled_qty)
                                ) / combined_qty
                            else:
                                combined_price = float(open_buy.entry_price or 0.0)
                                combined_price_krw = float(open_buy.entry_price_krw or 0.0)
                                ctx["pending_buy_price_reconciliation"] = True
                                ctx["pending_buy_reconcile_order_id"] = order_id
                                trade_notes["pending_buy_price_reconciliation"] = True
                                trade_notes["pending_buy_reconcile_order_id"] = order_id
                                logger.warning(
                                    "[TradeResult] 추가매수 체결가 누락: {} {} 주문 {} → 기존 평균단가 유지",
                                    market,
                                    symbol,
                                    order_id,
                                )
                            if entry_price_source != "filled":
                                trade_notes["entry_price_source"] = entry_price_source

                            open_buy.order_id = order_id
                            open_buy.stock_name = ctx.get("stock_name", symbol)
                            open_buy.currency = currency
                            open_buy.exchange_rate_to_krw = exchange_rate_to_krw
                            open_buy.strategy_type = ctx.get("strategy_type", "")
                            open_buy.entry_price = combined_price
                            open_buy.entry_price_krw = combined_price_krw
                            open_buy.quantity = combined_qty
                            open_buy.ai_recommendation = ctx.get("ai_recommendation", "")
                            open_buy.ai_confidence = ctx.get("ai_confidence", 0.0)
                            open_buy.ai_target_price = ctx.get("ai_target_price")
                            open_buy.ai_stop_loss_price = ctx.get("ai_stop_loss_price")
                            open_buy.ai_take_profit_price = ctx.get("ai_take_profit_price")
                            open_buy.entry_rsi = ctx.get("entry_rsi")
                            open_buy.entry_macd_hist = ctx.get("entry_macd_hist")
                            open_buy.market_regime = ctx.get("market_regime", "")
                            open_buy.notes = json.dumps(trade_notes, ensure_ascii=False, default=str)

                            logger.info(
                                "[TradeResult] 추가매수 병합 기록: {} {} {}주 추가 → 총 {}주 @{}",
                                market,
                                symbol,
                                filled_qty,
                                combined_qty,
                                self._format_trade_price(combined_price, currency, exchange_rate_to_krw),
                            )
                        else:
                            if effective_entry_price <= 0:
                                ctx["pending_buy_price_reconciliation"] = True
                                ctx["pending_buy_reconcile_order_id"] = order_id
                                trade_notes["pending_buy_price_reconciliation"] = True
                                trade_notes["pending_buy_reconcile_order_id"] = order_id
                                logger.warning(
                                    "[TradeResult] 신규 매수 체결가 누락: {} {} 주문 {} → 0원 기록, 추후 보정 필요",
                                    market,
                                    symbol,
                                    order_id,
                                )
                            if entry_price_source != "filled":
                                trade_notes["entry_price_source"] = entry_price_source
                            tr = TradeResult(
                                order_id=order_id,
                                stock_symbol=symbol,
                                stock_name=ctx.get("stock_name", symbol),
                                currency=currency,
                                exchange_rate_to_krw=exchange_rate_to_krw,
                                market=market,
                                side="BUY",
                                strategy_type=ctx.get("strategy_type", ""),
                                entry_price=effective_entry_price,
                                entry_price_krw=effective_entry_price_krw,
                                exit_price=0.0,
                                exit_price_krw=0.0,
                                quantity=filled_qty,
                                raw_pnl=0.0,
                                pnl=0.0,
                                return_pct=0.0,
                                is_win=False,
                                hold_days=0,
                                ai_recommendation=ctx.get("ai_recommendation", ""),
                                ai_confidence=ctx.get("ai_confidence", 0.0),
                                ai_target_price=ctx.get("ai_target_price"),
                                ai_stop_loss_price=ctx.get("ai_stop_loss_price"),
                                ai_take_profit_price=ctx.get("ai_take_profit_price"),
                                entry_rsi=ctx.get("entry_rsi"),
                                entry_macd_hist=ctx.get("entry_macd_hist"),
                                market_regime=ctx.get("market_regime", ""),
                                notes=json.dumps(trade_notes, ensure_ascii=False, default=str),
                                entry_at=now,
                            )
                            session.add(tr)

                            logger.info(
                                "[TradeResult] 매수 기록 생성: {} {} {}주 @{}",
                                market,
                                symbol,
                                filled_qty,
                                self._format_trade_price(effective_entry_price, currency, exchange_rate_to_krw),
                            )
                        await activity_logger.log(
                            ActivityType.TRADE_RESULT, ActivityPhase.COMPLETE,
                            f"\U0001f4dd [{symbol}] {'추가매수 체결 기록' if is_add_on else '매수 체결 기록'}: "
                            f"{filled_qty}주 @{self._format_trade_price(effective_entry_price, currency, exchange_rate_to_krw)}",
                            cycle_id=cycle_id,
                            symbol=symbol,
                            detail={
                                "market": market,
                                "currency": currency,
                                "exchange_rate_to_krw": exchange_rate_to_krw,
                                "entry_price": effective_entry_price,
                                "entry_price_krw": effective_entry_price_krw,
                                "entry_price_source": entry_price_source,
                                "entry_mode": ctx.get("entry_mode", ""),
                                "combined_position_pct": ctx.get("combined_position_pct"),
                                "post_trade_cash_ratio": ctx.get("post_trade_cash_ratio"),
                                "analysis_source": ctx.get("analysis_source"),
                                "event_type": ctx.get("event_type"),
                                "broker_cash_krw": ctx.get("broker_cash_krw"),
                                "planned_hold_days": ctx.get("planned_hold_days"),
                                "exit_levels": self._build_exit_plan_levels(ctx),
                                "trade_threshold_payload": self._build_trade_threshold_payload(
                                    analysis_context=ctx,
                                ),
                                "symbol_orderable_amount_krw": ctx.get("symbol_orderable_amount_krw"),
                                "symbol_orderable_amount_foreign": ctx.get("symbol_orderable_amount_foreign"),
                                "symbol_orderable_qty": ctx.get("symbol_orderable_qty"),
                                "orderable_amount_source": ctx.get("orderable_amount_source"),
                                "is_add_on": is_add_on,
                            },
                        )

                        # ExitPlan 생성/업데이트 (DB 트랜잭션 외부)
                        exit_plan = await self._sync_exit_plan(
                            symbol=symbol,
                            market=market_code,
                            avg_entry_price=combined_price if is_add_on else effective_entry_price,
                            total_quantity=combined_qty if is_add_on else filled_qty,
                            analysis_context=ctx,
                            is_add_on=is_add_on,
                            order_id=order_id,
                        )
                        exit_plan_id = getattr(exit_plan, "id", None) if exit_plan is not None else None

                    elif side == "SELL":
                        # 매도 체결 → 미청산 BUY 기록 찾아서 업데이트
                        open_buy = await repo.get_open_buy(symbol, market=market)
                        if not open_buy:
                            logger.warning(
                                "[TradeResult] {} 미청산 매수 기록 없음 → 매도 기록만 생성",
                                symbol,
                            )
                            # 매수 기록 없이 매도만 온 경우 → 독립 기록
                            tr = TradeResult(
                                order_id=order_id,
                                stock_symbol=symbol,
                                stock_name=ctx.get("stock_name", symbol),
                                currency=currency,
                                exchange_rate_to_krw=exchange_rate_to_krw,
                                market=market,
                                side="SELL",
                                strategy_type=ctx.get("strategy_type", ""),
                                entry_price=0.0,
                                entry_price_krw=0.0,
                                exit_price=filled_price,
                                exit_price_krw=filled_price_krw,
                                quantity=filled_qty,
                                raw_pnl=0.0,
                                pnl=0.0,
                                exit_reason=exit_reason or "SIGNAL",
                                exit_at=now,
                                entry_at=now,
                            )
                            session.add(tr)
                            return

                        # 손익 계산
                        trade_notes = self._build_trade_notes(
                            analysis_context=ctx,
                            market=market,
                            existing_notes=getattr(open_buy, "notes", None),
                        )
                        entry_price = open_buy.entry_price
                        entry_exchange_rate = float(open_buy.exchange_rate_to_krw or exchange_rate_to_krw or 1.0)
                        raw_pnl = (filled_price - entry_price) * open_buy.quantity
                        pnl = self._to_trade_krw(raw_pnl, open_buy.currency, entry_exchange_rate)
                        return_pct = ((filled_price - entry_price) / entry_price * 100) if entry_price > 0 else 0.0
                        is_win = pnl > 0
                        entry_at = ensure_kst(open_buy.entry_at) if open_buy.entry_at else None
                        hold_days = (now - entry_at).days if entry_at else 0

                        open_buy.exit_price = filled_price
                        open_buy.exit_price_krw = filled_price_krw
                        open_buy.raw_pnl = raw_pnl
                        open_buy.pnl = pnl
                        open_buy.return_pct = round(return_pct, 2)
                        open_buy.is_win = is_win
                        open_buy.hold_days = hold_days
                        open_buy.exit_reason = exit_reason or "SIGNAL"
                        open_buy.notes = json.dumps(trade_notes, ensure_ascii=False, default=str)
                        open_buy.exit_at = now

                        pnl_sign = "+" if pnl >= 0 else ""
                        raw_pnl_sign = "+" if raw_pnl >= 0 else ""
                        logger.info(
                            "[TradeResult] 매도 청산: {} {}주 진입@{} → 청산@{} "
                            "= {}{} ({}{:.0f}원, {}{:.1f}%)",
                            symbol,
                            open_buy.quantity,
                            self._format_trade_price(entry_price, open_buy.currency, entry_exchange_rate),
                            self._format_trade_price(filled_price, currency, exchange_rate_to_krw),
                            raw_pnl_sign,
                            self._format_trade_price(abs(raw_pnl), open_buy.currency, entry_exchange_rate),
                            pnl_sign,
                            abs(pnl),
                            pnl_sign,
                            return_pct,
                        )
                        await activity_logger.log(
                            ActivityType.TRADE_RESULT, ActivityPhase.COMPLETE,
                            f"{'✅' if is_win else '❌'} [{symbol}] 매도 청산: "
                            f"{raw_pnl_sign}{self._format_trade_price(abs(raw_pnl), open_buy.currency, entry_exchange_rate)} "
                            f"({pnl_sign}{abs(pnl):,.0f}원, {pnl_sign}{return_pct:.1f}%) "
                            f"| {exit_reason or 'SIGNAL'} | {hold_days}일 보유",
                            cycle_id=cycle_id,
                            symbol=symbol,
                            detail={
                                "currency": open_buy.currency,
                                "exchange_rate_to_krw": entry_exchange_rate,
                                "entry_price": entry_price,
                                "entry_price_krw": open_buy.entry_price_krw,
                                "exit_price": filled_price,
                                "exit_price_krw": filled_price_krw,
                                "raw_pnl": raw_pnl,
                                "pnl": pnl,
                                "return_pct": return_pct,
                                "hold_days": hold_days,
                            },
                        )

            if side == "BUY":
                return {"exit_plan_id": exit_plan_id}
            return None

        except Exception as e:
            logger.error("[TradeResult] 기록 실패 ({}): {}", symbol, str(e))
            return None

    async def _sync_exit_plan(
        self,
        *,
        symbol: str,
        market: str,
        avg_entry_price: float,
        total_quantity: float,
        analysis_context: dict | None = None,
        is_add_on: bool = False,
        order_id: str | None = None,
    ) -> object | None:
        """BUY 체결 후 ExitPlan 생성 또는 추가매수 시 업데이트"""
        ctx = analysis_context or {}
        exit_levels = self._build_exit_plan_levels(ctx)
        if not exit_levels:
            logger.warning(
                "[ExitPlan] 생성 스킵: {}/{} order_id={} exit_levels={} trade_threshold_payload={}",
                market,
                symbol,
                order_id or "",
                ctx.get("exit_levels"),
                ctx.get("trade_threshold_payload"),
            )
            return None

        # SL 레벨 추가
        sl_price = self._try_float(ctx.get("ai_stop_loss_price") or ctx.get("stop_loss_price"))
        levels_for_plan = list(exit_levels)  # 복사
        if sl_price and sl_price > 0:
            # 기존 SL 중복 방지
            has_sl = any(l.get("type") == "STOP_LOSS" for l in levels_for_plan)
            if not has_sl:
                levels_for_plan.append({
                    "type": "STOP_LOSS", "price": round(sl_price, 4),
                    "pct": 100, "triggered": False, "triggered_at": None,
                    "reason": "손절가",
                })

        trailing = self._try_float(ctx.get("trailing_stop_pct"))

        try:
            from strategy.exit_plan_manager import exit_plan_manager

            reason = ctx.get("position_intent", "ADD_ON_PYRAMID" if is_add_on else "INITIAL")
            if reason not in ("INITIAL", "ADD_ON_PYRAMID", "ADD_ON_AVERAGE_DOWN", "AI_REVIEW", "TRAILING_UPDATE"):
                reason = "ADD_ON_PYRAMID" if is_add_on else "INITIAL"

            if is_add_on:
                plan = await exit_plan_manager.update_plan(
                    symbol=symbol,
                    market=market,
                    new_levels=levels_for_plan,
                    new_avg_price=avg_entry_price,
                    new_qty=float(total_quantity),
                    reason=reason,
                    ai_reasoning=ctx.get("exit_reasoning", ""),
                )
                if plan is None:
                    plan = await exit_plan_manager.create_plan(
                        symbol=symbol,
                        market=market,
                        avg_entry_price=avg_entry_price,
                        total_quantity=float(total_quantity),
                        levels=levels_for_plan,
                        trailing_stop_pct=float(trailing or 0.0),
                        reason=reason,
                        ai_reasoning=ctx.get("exit_reasoning", ""),
                    )
            else:
                plan = await exit_plan_manager.create_plan(
                    symbol=symbol,
                    market=market,
                    avg_entry_price=avg_entry_price,
                    total_quantity=float(total_quantity),
                    levels=levels_for_plan,
                    trailing_stop_pct=float(trailing or 0.0),
                    reason=reason,
                    ai_reasoning=ctx.get("exit_reasoning", ""),
                )
            logger.info(
                "[ExitPlan] {} {}/{} avg={:.2f} qty={} levels={}",
                "업데이트" if is_add_on else "생성",
                symbol, market, avg_entry_price, self._quantity_text(total_quantity, market), len(levels_for_plan),
            )
            return plan
        except Exception as e:
            logger.error("[ExitPlan] 생성/업데이트 실패 ({}): {}", symbol, str(e))
            return None

    @staticmethod
    def _try_float(value) -> float | None:
        if value is None:
            return None
        try:
            v = float(value)
            return v if not (v != v) else None  # NaN 체크
        except (TypeError, ValueError):
            return None

    @classmethod
    def _build_trade_threshold_payload(
        cls,
        *,
        analysis_context: dict | None,
        exit_plan_id: str | None = None,
    ) -> dict:
        """체결 후 활성화할 거래용 임계값 payload를 정규화한다."""
        ctx = analysis_context or {}
        raw_payload = ctx.get("trade_threshold_payload")
        payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}

        stop_loss = cls._first_positive_float(
            payload.get("stop_loss"),
            ctx.get("ai_stop_loss_price"),
            ctx.get("stop_loss_price"),
        )
        if stop_loss and stop_loss > 0:
            payload["stop_loss"] = round(stop_loss, 4)

        take_profit = cls._first_positive_float(
            payload.get("take_profit"),
            ctx.get("ai_take_profit_price"),
            ctx.get("take_profit_price"),
            ctx.get("ai_target_price"),
        )
        if take_profit and take_profit > 0:
            payload["take_profit"] = round(take_profit, 4)

        trailing = cls._first_positive_float(
            payload.get("trailing_stop_pct"),
            ctx.get("trailing_stop_pct"),
        )
        if trailing and trailing > 0:
            payload["trailing_stop_pct"] = round(trailing, 4)

        raw_tp_levels = payload.get("tp_levels")
        if not isinstance(raw_tp_levels, list):
            raw_tp_levels = []

        normalized_exit_levels = cls._build_exit_plan_levels(ctx)
        if normalized_exit_levels:
            payload["exit_levels"] = normalized_exit_levels

        if not raw_tp_levels:
            if normalized_exit_levels:
                raw_tp_levels = [
                    {
                        "price": round(float(level["price"]), 4),
                        "pct": float(level["pct"]),
                        "level_index": index,
                    }
                    for index, level in enumerate(normalized_exit_levels)
                    if isinstance(level, dict)
                    and level.get("type") == "TAKE_PROFIT"
                    and not level.get("triggered")
                    and cls._try_float(level.get("price"))
                    and cls._try_float(level.get("pct")) is not None
                ]

        if raw_tp_levels:
            payload["tp_levels"] = [
                {
                    "price": round(float(level["price"]), 4),
                    "pct": float(level["pct"]),
                    "level_index": int(level.get("level_index", index)),
                }
                for index, level in enumerate(raw_tp_levels)
                if isinstance(level, dict)
                and cls._try_float(level.get("price"))
                and cls._try_float(level.get("pct")) is not None
            ]
            if ("take_profit" not in payload or not payload["take_profit"]) and payload["tp_levels"]:
                payload["take_profit"] = payload["tp_levels"][0]["price"]

        if exit_plan_id:
            payload["exit_plan_id"] = exit_plan_id

        return payload

    def _activate_buy_trade_thresholds(
        self,
        *,
        symbol: str,
        market: str,
        analysis_context: dict | None,
        exit_plan_id: str | None = None,
    ) -> None:
        """BUY 체결 후에만 손절/익절 감시 임계값을 활성화한다."""
        payload = self._build_trade_threshold_payload(
            analysis_context=analysis_context,
            exit_plan_id=exit_plan_id,
        )
        kwargs = {
            key: payload[key]
            for key in ("stop_loss", "take_profit", "trailing_stop_pct")
            if key in payload
        }
        tp_levels = payload.get("tp_levels")
        detector_kwargs = dict(kwargs)
        if payload.get("exit_plan_id"):
            detector_kwargs["exit_plan_id"] = payload["exit_plan_id"]

        if not detector_kwargs and not tp_levels:
            return

        event_detector.set_thresholds(
            symbol,
            market=market,
            tp_levels=tp_levels if isinstance(tp_levels, list) and tp_levels else None,
            **detector_kwargs,
        )
        logger.info(
            "[TradeThreshold] BUY 체결 후 활성화: {} {}{}",
            market,
            symbol,
            f" (TP레벨 {len(tp_levels)}개)" if isinstance(tp_levels, list) and tp_levels else "",
        )

    async def _create_recommendation(
        self, signal: TradeSignal, analysis_id: str, cycle_id: str | None = None,
    ) -> dict:
        """반자율: 추천 생성 → 사용자 승인 대기"""
        market_code = normalize_market(signal.metadata.get("market", "KRX"))

        if is_crypto_market(market_code):
            return await self._create_coin_recommendation(signal, analysis_id, cycle_id)

        expires_at = now_kst() + timedelta(
            minutes=settings.recommendation_expire_minutes_for_market(market_code)
        )

        rec_data = {
            "stock_id": signal.stock_id,
            "analysis_id": analysis_id,
            "market": signal.metadata.get("market", "KRX"),
            "currency": signal.metadata.get("currency", "KRW"),
            "product_type": signal.metadata.get("product_type", "COMMON"),
            "is_leveraged": bool(signal.metadata.get("is_leveraged")),
            "is_inverse": bool(signal.metadata.get("is_inverse")),
            "leverage_multiplier": float(signal.metadata.get("leverage_multiplier") or 1.0),
            "signed_exposure": float(signal.metadata.get("signed_exposure") or 1.0),
            "restricted_product": bool(signal.metadata.get("restricted_product")),
            "action": signal.action.value,
            "suggested_price": signal.suggested_price or 0,
            "suggested_quantity": signal.suggested_quantity or 0,
            "reason": signal.reason,
            "confidence": signal.confidence,
            "status": RecommendationStatus.PENDING.value,
            "expires_at": expires_at,
        }

        qty = signal.suggested_quantity or 0
        price = signal.suggested_price or 0
        amount = (signal.metadata.get("price_krw") or price) * qty

        logger.info(
            "[SEMI_AUTO] 추천 생성: {} {} x{} (만료: {})",
            signal.symbol, signal.action.value,
            qty, expires_at,
        )

        market_code = normalize_market(signal.metadata.get("market", "KRX"))

        await activity_logger.log(
            ActivityType.DECISION, ActivityPhase.COMPLETE,
            f"\U0001f4dd 매수 추천 생성: {signal.symbol} {format_quantity_with_unit(qty, market_code)} "
            f"@{price:,.2f}{signal.metadata.get('currency', 'KRW')} ({amount:,.0f}원)"
            f"\n   \u2192 사용자 승인 대기 (SEMI_AUTO 모드)",
            cycle_id=cycle_id,
            symbol=signal.symbol,
            confidence=signal.confidence,
            detail=self._enrich_detail(signal, rec_data),
        )

        await event_bus.publish(Event(
            type=EventType.RECOMMENDATION_CREATED,
            data={**rec_data, "symbol": signal.symbol},
            source="decision_maker",
        ))

        return {
            "mode": "SEMI_AUTO",
            "symbol": signal.symbol,
            "action": signal.action.value,
            "recommendation": rec_data,
        }

    async def _create_coin_recommendation(
        self, signal: TradeSignal, analysis_id: str, cycle_id: str | None = None,
    ) -> dict:
        """반자율: 코인 추천 생성 → CoinRecommendation 테이블 저장"""
        ts = now_kst()
        expires_at = ts + timedelta(
            minutes=settings.recommendation_expire_minutes_for_market(
                settings.crypto_primary_market_code
            )
        )

        # coin_asset_id 조회 (symbol → CoinAsset.id)
        coin_asset_id: str | None = None
        try:
            async with AsyncSessionLocal() as session:
                row = await session.scalar(
                    select(CoinAsset.id).where(CoinAsset.symbol == signal.symbol).limit(1)
                )
                coin_asset_id = row
        except Exception as e:
            logger.warning("[CoinRecommendation] CoinAsset 조회 실패 ({}): {}", signal.symbol, e)

        if not coin_asset_id:
            logger.warning(
                "[CoinRecommendation] CoinAsset 미등록 — symbol={}, stock_id를 대체 사용",
                signal.symbol,
            )
            coin_asset_id = signal.stock_id or signal.symbol

        # analysis_id 가 없으면 stub CoinAnalysisResult 생성
        resolved_analysis_id = analysis_id
        if not resolved_analysis_id:
            try:
                async with AsyncSessionLocal() as session:
                    async with session.begin():
                        stub = CoinAnalysisResult(
                            coin_asset_id=coin_asset_id,
                            type="TECHNICAL",
                            recommendation=signal.action.value,
                            confidence=signal.confidence,
                            summary=signal.reason or f"{signal.symbol} {signal.action.value} 추천",
                            llm_provider="system",
                            llm_tier="T0",
                        )
                        session.add(stub)
                        await session.flush()
                        resolved_analysis_id = stub.id
            except Exception as e:
                logger.error("[CoinRecommendation] stub CoinAnalysisResult 생성 실패: {}", e)

        # CoinRecommendation DB 저장
        qty = float(signal.suggested_quantity or 0)
        price = float(signal.suggested_price or 0)
        amount = self._signal_amount_krw(signal, settings.crypto_primary_market_code, price_krw=price)
        coin_rec: CoinRecommendation | None = None

        if resolved_analysis_id:
            try:
                async with AsyncSessionLocal() as session:
                    async with session.begin():
                        coin_rec = CoinRecommendation(
                            coin_asset_id=coin_asset_id,
                            analysis_id=resolved_analysis_id,
                            action=signal.action.value,
                            suggested_price=price,
                            suggested_quantity=qty,
                            suggested_amount_krw=amount,
                            reason=signal.reason or "",
                            confidence=signal.confidence,
                            status=RecommendationStatus.PENDING.value,
                            expires_at=expires_at,
                        )
                        session.add(coin_rec)
                        await session.flush()
            except Exception as e:
                logger.error("[CoinRecommendation] DB 저장 실패 ({}): {}", signal.symbol, e)

        rec_data = {
            "coin_asset_id": coin_asset_id,
            "analysis_id": resolved_analysis_id or "",
            "recommendation_id": coin_rec.id if coin_rec else None,
            "market": signal.metadata.get("market", settings.crypto_primary_market_code),
            "currency": signal.metadata.get("currency", "KRW"),
            "action": signal.action.value,
            "suggested_price": price,
            "suggested_quantity": qty,
            "suggested_amount_krw": amount,
            "reason": signal.reason,
            "confidence": signal.confidence,
            "status": RecommendationStatus.PENDING.value,
            "expires_at": expires_at,
        }

        logger.info(
            "[SEMI_AUTO][CRYPTO] 코인 추천 생성: {} {} amount={} qty={} (만료: {})",
            signal.symbol, signal.action.value,
            amount, qty, expires_at,
        )

        await activity_logger.log(
            ActivityType.DECISION, ActivityPhase.COMPLETE,
            f"\U0001f4dd 코인 추천 생성: {signal.symbol} {amount:,.0f}원 "
            f"(예상 {format_quantity_with_unit(qty, settings.crypto_primary_market_code)}) "
            f"@{price:,.2f}{signal.metadata.get('currency', 'KRW')}"
            f"\n   \u2192 사용자 승인 대기 (SEMI_AUTO 모드)",
            cycle_id=cycle_id,
            market_scope="CRYPTO",
            symbol=signal.symbol,
            confidence=signal.confidence,
            detail=self._enrich_detail(signal, rec_data),
        )

        await event_bus.publish(Event(
            type=EventType.RECOMMENDATION_CREATED,
            data={**rec_data, "symbol": signal.symbol},
            source="decision_maker",
        ))

        return {
            "mode": "SEMI_AUTO",
            "symbol": signal.symbol,
            "action": signal.action.value,
            "recommendation": rec_data,
        }


decision_maker = DecisionMaker()
