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
from repositories.trade_result_repository import TradeResultRepository
from services.activity_logger import activity_logger
from strategy.signal import TradeSignal
from trading.enums import ActivityPhase, ActivityType, AutonomyMode, OrderSource, RecommendationStatus
from trading.market_profile import is_crypto_market, is_us_market, normalize_market, normalize_market_scope
from trading.mcp_client import mcp_client
from trading.product_policy import build_product_context
from trading.quantity_policy import format_quantity, format_quantity_with_unit, normalize_quantity
from scheduler.market_calendar import market_calendar
from util.time_util import now_kst

_US_ORDER_SANITY_MAX_GAP_PCT = 0.15
_OVERSEAS_CONFIRM_DELAYS_SECONDS = (3, 6, 10)


class DecisionMaker:
    """
    자율/반자율 모드에 따라 실행 방식을 분기.

    AUTONOMOUS: 스캔 → 분석 → 매매까지 전자동
    SEMI_AUTO: 스캔 → 분석 → 추천 생성 → 사용자 승인 대기
    """

    def __init__(self):
        self._pending_tasks: set[asyncio.Task] = set()

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
            "account_type": settings.KIS_ACCOUNT_TYPE,
            "currency": signal.metadata.get("currency", "USD"),
        }

    @staticmethod
    def _signal_product_context(signal: TradeSignal) -> dict:
        metadata = signal.metadata or {}
        market_code = normalize_market(metadata.get("market", "KRX"))
        return build_product_context(signal.symbol, market_code, metadata)

    @classmethod
    def _enrich_detail(cls, signal: TradeSignal, detail: dict | None = None) -> dict:
        enriched = dict(detail or {})
        enriched["product_context"] = cls._signal_product_context(signal)
        metadata = signal.metadata or {}
        for key in (
            "entry_mode",
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

    @staticmethod
    def _order_quantity(value: object, market: str) -> float:
        return normalize_quantity(value, market)

    @staticmethod
    def _quantity_text(quantity: float, market: str) -> str:
        return format_quantity(quantity, market)

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
        currency: str,
        exchange_rate_to_krw: float,
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
                                currency=currency,
                                strategy_type=strategy_type,
                            )
                            session.add(existing)

                        existing.cycle_id = cycle_id or existing.cycle_id
                        existing.status = status
                        existing.coin_name = stock_name or existing.coin_name
                        existing.side = side
                        existing.quantity = float(quantity)
                        existing.requested_price = requested_price
                        existing.currency = currency
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
                "order_qty": quantity,
                "filled_qty": filled_quantity,
                "filled_quantity": filled_quantity,
                "remaining_qty": remaining_quantity,
                "order_price": float(record.requested_price or 0.0),
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

    async def sync_coin_ws_order(self, order_data: dict | None) -> None:
        """Private MyOrder 수신 시 coin broker ledger를 즉시 동기화."""
        payload = order_data or {}
        order_id = str(payload.get("order_id") or "").strip()
        symbol = str(payload.get("symbol") or "").upper().strip()
        market_code = normalize_market(
            payload.get("market") or settings.crypto_primary_market_code,
            default=settings.crypto_primary_market_code,
        )
        if not order_id or not symbol or not is_crypto_market(market_code):
            return

        existing = await self._load_broker_order(order_id, market_code)
        prev_status = str(getattr(existing, "status", "") or "")
        prev_filled_qty = float(getattr(existing, "filled_quantity", 0.0) or 0.0)

        status = str(payload.get("status") or payload.get("state") or "SUBMITTED").upper()
        quantity = self._order_quantity(
            payload.get("order_qty") or payload.get("volume"),
            market_code,
        )
        if quantity <= 0:
            return

        filled_qty = self._order_quantity(
            payload.get("filled_qty") or payload.get("filled_quantity"),
            market_code,
        )
        filled_price = float(payload.get("filled_price") or payload.get("order_price") or 0.0)
        detail_text = json.dumps(payload, ensure_ascii=False, default=str)

        await self._upsert_broker_order(
            cycle_id=getattr(existing, "cycle_id", None),
            order_id=order_id,
            symbol=symbol,
            stock_name=str(payload.get("name") or getattr(existing, "coin_name", "") or symbol),
            market=market_code,
            side=str(payload.get("side") or getattr(existing, "side", "") or "").upper(),
            status=status,
            quantity=quantity,
            requested_price=float(payload.get("order_price") or getattr(existing, "requested_price", 0.0) or 0.0),
            requested_price_krw=float(payload.get("order_price") or getattr(existing, "requested_price", 0.0) or 0.0),
            currency=str(payload.get("currency") or getattr(existing, "currency", "KRW") or "KRW"),
            exchange_rate_to_krw=float(payload.get("exchange_rate_to_krw") or 1.0),
            strategy_type=str(getattr(existing, "strategy_type", "") or ""),
            filled_quantity=filled_qty,
            filled_price=filled_price,
            filled_price_krw=filled_price,
            status_detail=detail_text,
            error_message="",
            submitted_at=payload.get("submitted_at"),
            filled_at=payload.get("filled_at") if status in {"FILLED", "PARTIAL"} else None,
        )

        if status != prev_status or filled_qty > prev_filled_qty:
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
        if quantity <= 0:
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
            "currency": currency,
            "exchange_rate_to_krw": exchange_rate,
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
            "[AUTONOMOUS] 주문 실행: {} {} x{} @ {}",
            signal.symbol, signal.action.value,
            signal.suggested_quantity, signal.suggested_price,
        )

        market = signal.metadata.get("market", "KRX")
        market_code = normalize_market(market)
        currency = signal.metadata.get("currency", "KRW")
        exchange_rate = float(signal.metadata.get("exchange_rate_to_krw") or 1.0)
        qty = signal.suggested_quantity or 0
        price = signal.suggested_price or 0
        price_krw = float(
            signal.metadata.get("entry_price_krw")
            or signal.metadata.get("price_krw")
            or (price * exchange_rate if currency != "KRW" else price)
        )
        amount = price_krw * qty
        await activity_logger.log(
            ActivityType.DECISION, ActivityPhase.START,
            f"\U0001f4b0 [{signal.symbol}] 자동 주문 실행: "
            f"{signal.action.value} {format_quantity_with_unit(qty, market_code)} "
            f"{self._price_display(price, currency, price_krw)}",
            cycle_id=cycle_id,
            symbol=signal.symbol,
            detail=self._enrich_detail(
                signal,
                {
                    "market": market_code,
                    "currency": currency,
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

        response = await mcp_client.place_order(
            symbol=signal.symbol,
            side=signal.action.value,
            quantity=signal.suggested_quantity or 0,
            price=signal.suggested_price,
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
                currency=str(currency),
                exchange_rate_to_krw=float(exchange_rate or 1.0),
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
                    analysis_context=analysis_context,
                    cycle_id=cycle_id,
                )
            )
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)
        else:
            error_msg = response.error or order_data.get("msg1") or "주문번호 없음"
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

    async def confirm_and_record(
        self,
        symbol: str,
        market: str,
        side: str,
        order_id: str,
        quantity: float,
        expected_price: float,
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

            for delay_seconds in _OVERSEAS_CONFIRM_DELAYS_SECONDS:
                if matched_order is not None:
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
                current_filled_qty = self._order_quantity(
                    current_match.get("filled_qty")
                    or current_match.get("filled_quantity"),
                    market_code,
                )
                if current_filled_qty > 0:
                    break

            if not matched_order:
                logger.info("[{}] 주문 {} 미체결 (체결내역에서 미발견)", symbol, order_id)
                return

            currency = str(matched_order.get("currency") or currency)
            exchange_rate = float(matched_order.get("exchange_rate_to_krw") or exchange_rate or 1.0)
            filled_qty = self._order_quantity(
                matched_order.get("filled_qty")
                or matched_order.get("filled_quantity")
                or quantity,
                market_code,
            )
            remaining_qty = self._order_quantity(matched_order.get("remaining_qty"), market_code)
            filled_price = mcp_client._to_float(
                matched_order.get("filled_price")
                or matched_order.get("avg_prvs")
                or matched_order.get("ccld_pric")
                or expected_price
            )
            filled_price_krw = self._to_trade_krw(filled_price, currency, exchange_rate)
            status_text = str(matched_order.get("status") or "체결 대기")
            reject_reason = str(matched_order.get("reject_reason") or "")

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
                    currency=currency,
                    exchange_rate_to_krw=exchange_rate,
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
                currency=currency,
                exchange_rate_to_krw=exchange_rate,
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

            await self._record_trade_result(
                symbol=symbol,
                market=market_code,
                side=side,
                order_id=order_id,
                filled_qty=filled_qty,
                filled_price=filled_price,
                currency=currency,
                exchange_rate_to_krw=exchange_rate,
                analysis_context=analysis_context,
                exit_reason=exit_reason,
                cycle_id=cycle_id,
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
    ) -> None:
        """코인 체결 결과를 coin_trade_results에 기록"""
        ctx = analysis_context or {}
        now = now_kst()
        qty_text = self._quantity_text(filled_qty, market)
        price_krw = float(filled_price or 0.0)
        trade_notes = {
            "entry_mode": ctx.get("entry_mode", ""),
            "combined_position_pct": ctx.get("combined_position_pct"),
            "post_trade_cash_ratio": ctx.get("post_trade_cash_ratio"),
            "analysis_source": ctx.get("analysis_source"),
            "event_type": ctx.get("event_type"),
            "broker_cash_krw": ctx.get("broker_cash_krw"),
            "symbol_orderable_amount_krw": ctx.get("symbol_orderable_amount_krw"),
            "symbol_orderable_amount_foreign": ctx.get("symbol_orderable_amount_foreign"),
            "symbol_orderable_qty": ctx.get("symbol_orderable_qty"),
            "orderable_amount_source": ctx.get("orderable_amount_source"),
            "market": market,
        }

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
                        if open_buy:
                            previous_qty = float(open_buy.quantity or 0.0)
                            combined_qty = previous_qty + filled_qty
                            if combined_qty <= 0:
                                return
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
                            },
                        )
                        return

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
                        return

                    sell_qty = min(float(open_buy.quantity or 0.0), float(filled_qty or 0.0))
                    if sell_qty <= 0:
                        return

                    entry_price = float(open_buy.entry_price or 0.0)
                    pnl = (price_krw - entry_price) * sell_qty
                    return_pct = ((price_krw - entry_price) / entry_price * 100) if entry_price > 0 else 0.0
                    hold_hours = (
                        max(0, int((now - open_buy.entry_at).total_seconds() // 3600))
                        if open_buy.entry_at
                        else 0
                    )
                    is_win = pnl > 0

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

                    pnl_sign = "+" if pnl >= 0 else ""
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
                        },
                    )
        except Exception as e:
            logger.error("[CoinTradeResult] 기록 실패 ({}): {}", symbol, str(e))

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
    ) -> None:
        """체결 확인 후 TradeResult 생성/업데이트"""
        market_code = normalize_market(market)
        if is_crypto_market(market_code):
            await self._record_coin_trade_result(
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
            return

        filled_qty = int(float(filled_qty or 0.0))
        ctx = analysis_context or {}
        now = now_kst()
        filled_price_krw = self._to_trade_krw(filled_price, currency, exchange_rate_to_krw)

        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    repo = TradeResultRepository(session)
                    trade_notes = {
                        "entry_mode": ctx.get("entry_mode", ""),
                        "combined_position_pct": ctx.get("combined_position_pct"),
                        "post_trade_cash_ratio": ctx.get("post_trade_cash_ratio"),
                        "analysis_source": ctx.get("analysis_source"),
                        "event_type": ctx.get("event_type"),
                        "broker_cash_krw": ctx.get("broker_cash_krw"),
                        "symbol_orderable_amount_krw": ctx.get("symbol_orderable_amount_krw"),
                        "symbol_orderable_amount_foreign": ctx.get("symbol_orderable_amount_foreign"),
                        "symbol_orderable_qty": ctx.get("symbol_orderable_qty"),
                        "orderable_amount_source": ctx.get("orderable_amount_source"),
                        "market": market,
                    }

                    if side == "BUY":
                        open_buy = await repo.get_open_buy(symbol, market=market)
                        is_add_on = open_buy is not None
                        if open_buy:
                            previous_qty = int(open_buy.quantity or 0)
                            combined_qty = previous_qty + filled_qty
                            combined_price = (
                                ((open_buy.entry_price or 0.0) * previous_qty) + (filled_price * filled_qty)
                            ) / combined_qty
                            combined_price_krw = (
                                ((open_buy.entry_price_krw or 0.0) * previous_qty) + (filled_price_krw * filled_qty)
                            ) / combined_qty

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
                            tr = TradeResult(
                                order_id=order_id,
                                stock_symbol=symbol,
                                stock_name=ctx.get("stock_name", symbol),
                                currency=currency,
                                exchange_rate_to_krw=exchange_rate_to_krw,
                                market=market,
                                side="BUY",
                                strategy_type=ctx.get("strategy_type", ""),
                                entry_price=filled_price,
                                entry_price_krw=filled_price_krw,
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
                                self._format_trade_price(filled_price, currency, exchange_rate_to_krw),
                            )
                        await activity_logger.log(
                            ActivityType.TRADE_RESULT, ActivityPhase.COMPLETE,
                            f"\U0001f4dd [{symbol}] {'추가매수 체결 기록' if is_add_on else '매수 체결 기록'}: "
                            f"{filled_qty}주 @{self._format_trade_price(filled_price, currency, exchange_rate_to_krw)}",
                            cycle_id=cycle_id,
                            symbol=symbol,
                            detail={
                                "market": market,
                                "currency": currency,
                                "exchange_rate_to_krw": exchange_rate_to_krw,
                                "entry_price": filled_price,
                                "entry_price_krw": filled_price_krw,
                                "entry_mode": ctx.get("entry_mode", ""),
                                "combined_position_pct": ctx.get("combined_position_pct"),
                                "post_trade_cash_ratio": ctx.get("post_trade_cash_ratio"),
                                "analysis_source": ctx.get("analysis_source"),
                                "event_type": ctx.get("event_type"),
                                "broker_cash_krw": ctx.get("broker_cash_krw"),
                                "symbol_orderable_amount_krw": ctx.get("symbol_orderable_amount_krw"),
                                "symbol_orderable_amount_foreign": ctx.get("symbol_orderable_amount_foreign"),
                                "symbol_orderable_qty": ctx.get("symbol_orderable_qty"),
                                "orderable_amount_source": ctx.get("orderable_amount_source"),
                                "is_add_on": is_add_on,
                            },
                        )

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
                        entry_price = open_buy.entry_price
                        entry_exchange_rate = float(open_buy.exchange_rate_to_krw or exchange_rate_to_krw or 1.0)
                        raw_pnl = (filled_price - entry_price) * open_buy.quantity
                        pnl = self._to_trade_krw(raw_pnl, open_buy.currency, entry_exchange_rate)
                        return_pct = ((filled_price - entry_price) / entry_price * 100) if entry_price > 0 else 0.0
                        is_win = pnl > 0
                        hold_days = (now - open_buy.entry_at).days if open_buy.entry_at else 0

                        open_buy.exit_price = filled_price
                        open_buy.exit_price_krw = filled_price_krw
                        open_buy.raw_pnl = raw_pnl
                        open_buy.pnl = pnl
                        open_buy.return_pct = round(return_pct, 2)
                        open_buy.is_win = is_win
                        open_buy.hold_days = hold_days
                        open_buy.exit_reason = exit_reason or "SIGNAL"
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

        except Exception as e:
            logger.error("[TradeResult] 기록 실패 ({}): {}", symbol, str(e))

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
        amount = price * qty
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
            "reason": signal.reason,
            "confidence": signal.confidence,
            "status": RecommendationStatus.PENDING.value,
            "expires_at": expires_at,
        }

        logger.info(
            "[SEMI_AUTO][CRYPTO] 코인 추천 생성: {} {} x{} (만료: {})",
            signal.symbol, signal.action.value,
            qty, expires_at,
        )

        await activity_logger.log(
            ActivityType.DECISION, ActivityPhase.COMPLETE,
            f"\U0001f4dd 코인 추천 생성: {signal.symbol} {qty} "
            f"@{price:,.2f}{signal.metadata.get('currency', 'KRW')} ({amount:,.0f}원)"
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
