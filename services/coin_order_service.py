from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from exceptions.common import ServiceException
from exceptions.error_codes import ErrorCode
from models.coin_broker_order import CoinBrokerOrder
from schemas.coin_order_schema import (
    CoinOrderCancelResponse,
    CoinOrderExecutionResponse,
    CoinOrderPlaceRequest,
    CoinOrderPreviewRequest,
    CoinOrderPreviewResponse,
    CoinOrderStatusResponse,
)
from services.activity_logger import activity_logger
from trading.account_manager import account_manager
from trading.bithumb_client import bithumb_client
from trading.enums import ActivityPhase, ActivityType, OrderSide, OrderType
from trading.market_profile import MARKET_SCOPE_CRYPTO
from trading.models import MCPResponse
from trading.quantity_policy import CRYPTO_QUANTITY_STEP, cap_quantity_for_amount, min_order_amount_krw, normalize_quantity
from util.time_util import now_kst

_DEFAULT_SOURCE = "MANUAL_API"
_ORDER_WAIT_RETRY_CODES = {"order_not_ready"}
_AUTH_ERROR_CODES = {"jwt_verification", "expired_jwt", "NotAllowIP", "out_of_scope"}
_VALIDATION_ERROR_CODES = {"invalid_parameter", "invalid_price", "under_price_limit_bid", "cross_trading"}
_ORDER_STATE_ERROR_CODES = {"order_not_found", "order_not_ready"}


@dataclass
class _BrokerErrorInfo:
    category: str
    code: str
    message: str


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    try:
        if value in (None, ""):
            return default
        return Decimal(str(value))
    except Exception:
        return default


def _quantize_limit_price(value: float | None) -> float | None:
    if value is None:
        return None
    quantized = _to_decimal(value).quantize(Decimal("1"), rounding=ROUND_DOWN)
    if quantized <= 0:
        return None
    return float(quantized)


def _round_up_quantity_for_amount(amount_krw: float, unit_price: float) -> float:
    amount = _to_decimal(amount_krw)
    price = _to_decimal(unit_price)
    if amount <= 0 or price <= 0:
        return 0.0
    raw = amount / price
    normalized = (raw / CRYPTO_QUANTITY_STEP).quantize(Decimal("1"), rounding=ROUND_CEILING) * CRYPTO_QUANTITY_STEP
    return float(normalized)


class CoinOrderService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.market = settings.crypto_primary_market_code

    async def preview_order(self, request: CoinOrderPreviewRequest | CoinOrderPlaceRequest) -> CoinOrderPreviewResponse:
        side = request.side.value
        order_type = request.order_type.value
        symbol = str(request.symbol or "").upper().strip()
        if not symbol:
            return CoinOrderPreviewResponse(
                symbol="",
                side=side,
                order_type=order_type,
                broker_order_type=self._broker_order_type(side, order_type),
                placeable=False,
                validation_errors=["심볼이 필요합니다"],
            )

        price_resp = await bithumb_client.get_current_price(symbol, market=self.market)
        if not price_resp.success or not price_resp.data:
            raise self._service_error(
                status_code=500,
                error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                message=f"현재가 조회 실패: {price_resp.error or '응답 없음'}",
                category="upstream",
                broker_code="current_price_unavailable",
                broker_message=price_resp.error or "응답 없음",
            )

        reference_price = _to_float(price_resp.data.get("price") or price_resp.data.get("current_price"))
        warnings: list[str] = []
        validation_errors: list[str] = []
        limit_price = _quantize_limit_price(request.limit_price)
        broker_order_type = self._broker_order_type(side, order_type)
        min_order_amount = min_order_amount_krw(self.market)

        requested_amount_krw = 0.0
        normalized_amount_krw = 0.0
        requested_quantity = normalize_quantity(request.quantity, self.market)
        normalized_quantity = 0.0
        available_krw: float | None = None
        available_quantity: float | None = None
        orderable_quantity: float | None = None
        bid_fee_rate = 0.0

        if side == OrderSide.BUY.value:
            requested_amount_krw = _to_float(request.amount_krw)
            if requested_amount_krw <= 0:
                validation_errors.append("매수 주문은 amount_krw가 필요합니다")
            if requested_quantity > 0:
                validation_errors.append("매수 주문은 quantity가 아니라 amount_krw를 사용하세요")
            if order_type == OrderType.MARKET.value and request.limit_price is not None:
                validation_errors.append("시장가 주문에는 limit_price를 넣을 수 없습니다")
            if order_type == OrderType.LIMIT.value and limit_price is None:
                validation_errors.append("지정가 주문에는 limit_price가 필요합니다")
            if min_order_amount > 0 and requested_amount_krw > 0 and requested_amount_krw < min_order_amount:
                validation_errors.append(f"최소 주문금액 미달 ({min_order_amount:,.0f}원)")

            quote_price = limit_price if order_type == OrderType.LIMIT.value else reference_price
            if quote_price <= 0:
                validation_errors.append("기준 가격이 유효하지 않습니다")

            if order_type == OrderType.LIMIT.value and quote_price > 0 and requested_amount_krw > 0:
                normalized_quantity = _round_up_quantity_for_amount(requested_amount_krw, quote_price)
                normalized_amount_krw = round(normalized_quantity * quote_price, 4)
                amount_diff = normalized_amount_krw - requested_amount_krw
                if amount_diff > 0:
                    warnings.append(
                        f"수량 8자리 보정으로 실제 주문금액이 {amount_diff:,.4f}원 증가합니다"
                    )
            elif order_type == OrderType.MARKET.value and quote_price > 0 and requested_amount_krw > 0:
                normalized_amount_krw = round(requested_amount_krw, 4)
                normalized_quantity = cap_quantity_for_amount(requested_amount_krw, quote_price, self.market)

            if (requested_amount_krw > 0 and quote_price > 0) or order_type == OrderType.MARKET.value:
                chance_resp = await bithumb_client.get_orderable_amount(symbol, quote_price or reference_price, market=self.market)
                if chance_resp.success and chance_resp.data:
                    available_krw = _to_float(chance_resp.data.get("available_krw"))
                    orderable_quantity = _to_float(chance_resp.data.get("orderable_quantity"))
                    bid_fee_rate = _to_float(chance_resp.data.get("bid_fee_rate"))
                else:
                    validation_errors.append(
                        f"주문 가능 금액 조회 실패: {chance_resp.error or '응답 없음'}"
                    )

            if normalized_quantity <= 0 and requested_amount_krw > 0 and not validation_errors:
                validation_errors.append("계산된 주문 수량이 0입니다")

            estimated_fee_krw = round(normalized_amount_krw * bid_fee_rate, 4)
            estimated_locked_krw = round(normalized_amount_krw + estimated_fee_krw, 4)
            if available_krw is not None and estimated_locked_krw > (available_krw + 1e-9):
                validation_errors.append(
                    f"주문 가능 KRW 부족 (필요 {estimated_locked_krw:,.4f}원 / 보유 {available_krw:,.4f}원)"
                )
            if orderable_quantity is not None and normalized_quantity > (orderable_quantity + 1e-9):
                validation_errors.append(
                    f"주문 가능 수량 초과 (요청 {normalized_quantity:.8f} / 가능 {orderable_quantity:.8f})"
                )
        else:
            if request.amount_krw not in (None, 0, 0.0):
                validation_errors.append("매도 주문은 amount_krw를 받지 않습니다")
            if requested_quantity <= 0:
                validation_errors.append("매도 주문은 quantity가 필요합니다")
            if order_type == OrderType.MARKET.value and request.limit_price is not None:
                validation_errors.append("시장가 주문에는 limit_price를 넣을 수 없습니다")
            if order_type == OrderType.LIMIT.value and limit_price is None:
                validation_errors.append("지정가 주문에는 limit_price가 필요합니다")

            normalized_quantity = requested_quantity
            quote_price = limit_price if order_type == OrderType.LIMIT.value else reference_price
            if quote_price <= 0:
                validation_errors.append("기준 가격이 유효하지 않습니다")
            normalized_amount_krw = round(normalized_quantity * quote_price, 4) if quote_price > 0 else 0.0

            holdings_resp = await bithumb_client.get_holdings(market=self.market)
            if holdings_resp.success and holdings_resp.data:
                available_quantity = 0.0
                for item in holdings_resp.data.get("holdings", []):
                    if str(item.get("symbol") or "").upper() != symbol:
                        continue
                    available_quantity = _to_float(item.get("available_quantity"))
                    break
                if available_quantity <= 0:
                    validation_errors.append("매도 가능한 보유 수량이 없습니다")
            else:
                validation_errors.append(
                    f"보유 수량 조회 실패: {holdings_resp.error or '응답 없음'}"
                )

            if available_quantity is not None and normalized_quantity > (available_quantity + 1e-9):
                validation_errors.append(
                    f"보유 가능 수량 부족 (요청 {normalized_quantity:.8f} / 가능 {available_quantity:.8f})"
                )
            estimated_fee_krw = 0.0
            estimated_locked_krw = 0.0

        broker_payload_preview = self._build_broker_payload_preview(
            symbol=symbol,
            side=side,
            broker_order_type=broker_order_type,
            normalized_quantity=normalized_quantity,
            normalized_amount_krw=normalized_amount_krw,
            limit_price=limit_price,
        )

        return CoinOrderPreviewResponse(
            symbol=symbol,
            market=self.market,
            side=side,
            order_type=order_type,
            broker_order_type=broker_order_type,
            placeable=not validation_errors,
            limit_price=limit_price,
            reference_price=round(reference_price, 4),
            requested_amount_krw=round(requested_amount_krw, 4),
            normalized_amount_krw=round(normalized_amount_krw, 4),
            requested_quantity=round(requested_quantity, 8),
            normalized_quantity=round(normalized_quantity, 8),
            available_krw=available_krw,
            available_quantity=available_quantity,
            orderable_quantity=orderable_quantity,
            estimated_fee_krw=round(locals().get("estimated_fee_krw", 0.0), 4),
            estimated_locked_krw=round(locals().get("estimated_locked_krw", 0.0), 4),
            min_order_amount_krw=min_order_amount,
            warnings=warnings,
            validation_errors=validation_errors,
            broker_payload_preview=broker_payload_preview,
        )

    async def place_order(self, request: CoinOrderPlaceRequest) -> CoinOrderExecutionResponse:
        self._ensure_trading_enabled()
        preview = await self.preview_order(request)
        if not preview.placeable:
            self._raise_from_preview(preview)

        response = await bithumb_client.place_order(
            symbol=preview.symbol,
            side=preview.side,
            quantity=preview.requested_amount_krw if preview.side == OrderSide.BUY.value and preview.order_type == OrderType.MARKET.value else preview.normalized_quantity,
            price=preview.limit_price if preview.order_type == OrderType.LIMIT.value else None,
            market=self.market,
        )
        if not response.success:
            await self._log_order_error(preview.symbol, request, response)
            self._raise_broker_error(response)

        order_data = response.data or {}
        order_id = str(order_data.get("order_id") or "").strip()
        if not order_id:
            raise self._service_error(
                status_code=500,
                error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                message="주문은 성공으로 응답했지만 order_id가 없습니다",
                category="upstream",
                broker_code="missing_order_id",
                broker_message="주문번호 없음",
            )

        synced_payload, error_info = await self._poll_broker_order(order_id)
        status_response = (
            self._build_status_response(
                order_id=order_id,
                payload=synced_payload,
                source=_DEFAULT_SOURCE,
                broker_synced=True,
                message="주문이 접수되었습니다",
            )
            if synced_payload
            else CoinOrderStatusResponse(
                order_id=order_id,
                symbol=preview.symbol,
                market=self.market,
                side=preview.side,
                order_type=preview.order_type,
                broker_order_type=preview.broker_order_type,
                source=_DEFAULT_SOURCE,
                status="SUBMITTED",
                broker_state=str(order_data.get("state") or "WAIT").upper(),
                requested_price=preview.limit_price or 0.0,
                requested_amount_krw=preview.normalized_amount_krw if preview.side == OrderSide.BUY.value else 0.0,
                order_quantity=preview.normalized_quantity,
                filled_quantity=0.0,
                remaining_quantity=preview.normalized_quantity,
                filled_price=0.0,
                can_cancel=True,
                broker_synced=False,
                message="주문 접수 후 상세 조회를 기다리는 중입니다",
                broker_error_code=error_info.code if error_info else None,
                broker_error_message=error_info.message if error_info else None,
                error_category=error_info.category if error_info else None,
            )
        )

        await self._upsert_broker_order(
            order_id=order_id,
            payload=synced_payload,
            source=_DEFAULT_SOURCE,
            symbol=preview.symbol,
            side=preview.side,
            quantity=preview.normalized_quantity,
            requested_price=preview.limit_price or 0.0,
            requested_amount_krw=preview.normalized_amount_krw if preview.side == OrderSide.BUY.value else 0.0,
            order_type=preview.broker_order_type,
            status=status_response.status,
            status_detail=status_response.message or "주문 접수",
        )
        account_manager.invalidate_cache()

        await activity_logger.log(
            ActivityType.ORDER,
            ActivityPhase.COMPLETE,
            f"🧾 [{preview.symbol}] 수동 주문 접수 — {preview.side} {preview.order_type}",
            market_scope=MARKET_SCOPE_CRYPTO,
            symbol=preview.symbol,
            detail={
                "order_id": order_id,
                "source": _DEFAULT_SOURCE,
                "status": status_response.status,
                "broker_state": status_response.broker_state,
                "requested_amount_krw": status_response.requested_amount_krw,
                "normalized_quantity": status_response.order_quantity,
                "reason": request.reason,
            },
        )

        return CoinOrderExecutionResponse(
            order_id=order_id,
            symbol=status_response.symbol,
            market=self.market,
            side=status_response.side,
            order_type=status_response.order_type,
            broker_order_type=status_response.broker_order_type,
            source=_DEFAULT_SOURCE,
            status=status_response.status,
            broker_state=status_response.broker_state,
            requested_price=status_response.requested_price,
            requested_amount_krw=status_response.requested_amount_krw,
            normalized_amount_krw=preview.normalized_amount_krw,
            normalized_quantity=preview.normalized_quantity,
            filled_quantity=status_response.filled_quantity,
            remaining_quantity=status_response.remaining_quantity,
            filled_price=status_response.filled_price,
            broker_synced=status_response.broker_synced,
            message=status_response.message or "주문이 접수되었습니다",
            preview=preview,
        )

    async def get_order_status(self, order_id: str) -> CoinOrderStatusResponse:
        ledger = await self._get_ledger_order(order_id)
        payload, error_info = await self._poll_broker_order(order_id)
        if payload:
            response = self._build_status_response(
                order_id=order_id,
                payload=payload,
                source=getattr(ledger, "source", None),
                broker_synced=True,
            )
            if ledger is not None:
                await self._upsert_broker_order(
                    order_id=order_id,
                    payload=payload,
                    source=response.source or getattr(ledger, "source", _DEFAULT_SOURCE),
                    symbol=response.symbol,
                    side=response.side,
                    quantity=response.order_quantity,
                    requested_price=response.requested_price,
                    requested_amount_krw=response.requested_amount_krw,
                    order_type=response.broker_order_type,
                    status=response.status,
                    status_detail=response.message or response.broker_state,
                    create_if_missing=False,
                )
            return response

        if error_info and error_info.code == "order_not_found" and ledger is not None:
            return self._build_ledger_status_response(
                ledger,
                broker_synced=False,
                message="브로커 주문 조회 실패 — 원장 기준 상태를 반환합니다",
                error_info=error_info,
            )

        if error_info and error_info.code == "order_not_ready":
            raise self._service_error(
                status_code=409,
                error_code=ErrorCode.BAD_REQUEST,
                message="주문 상태가 아직 준비되지 않았습니다. 잠시 후 다시 시도하세요",
                category=error_info.category,
                broker_code=error_info.code,
                broker_message=error_info.message,
            )

        if error_info and error_info.code == "order_not_found":
            raise self._service_error(
                status_code=404,
                error_code=ErrorCode.NOT_FOUND,
                message=f"주문을 찾을 수 없습니다: {order_id}",
                category=error_info.category,
                broker_code=error_info.code,
                broker_message=error_info.message,
            )

        if error_info:
            raise self._service_error(
                status_code=500,
                error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                message=f"주문 상태 조회 실패: {error_info.message}",
                category=error_info.category,
                broker_code=error_info.code,
                broker_message=error_info.message,
            )

        raise self._service_error(
            status_code=500,
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            message="주문 상태 조회 실패",
            category="upstream",
        )

    async def cancel_order(self, order_id: str) -> CoinOrderCancelResponse:
        self._ensure_trading_enabled()
        ledger = await self._get_ledger_order(order_id)
        payload, error_info = await self._poll_broker_order(order_id)

        if payload:
            current_status = self._internal_status(payload)
            if current_status == "CANCELED":
                if ledger is not None:
                    await self._upsert_broker_order(
                        order_id=order_id,
                        payload=payload,
                        source=getattr(ledger, "source", _DEFAULT_SOURCE),
                        symbol=str(payload.get("symbol") or getattr(ledger, "symbol", "")),
                        side=str(payload.get("side") or getattr(ledger, "side", "")),
                        quantity=_to_float(payload.get("order_qty") or getattr(ledger, "quantity", 0.0)),
                        requested_price=_to_float(payload.get("order_price") or getattr(ledger, "requested_price", 0.0)),
                        requested_amount_krw=_to_float(payload.get("requested_amount_krw") or getattr(ledger, "requested_amount_krw", 0.0)),
                        order_type=str(payload.get("order_type") or getattr(ledger, "order_type", "")),
                        status="CANCELED",
                        status_detail="이미 취소된 주문",
                    )
                return CoinOrderCancelResponse(
                    order_id=order_id,
                    status="CANCELED",
                    broker_state=str(payload.get("state") or "").upper(),
                    canceled=True,
                    already_canceled=True,
                    filled_quantity=_to_float(payload.get("filled_qty")),
                    remaining_quantity=_to_float(payload.get("remaining_qty")),
                    message="이미 취소된 주문입니다",
                )

            if current_status == "FILLED":
                if ledger is not None:
                    await self._upsert_broker_order(
                        order_id=order_id,
                        payload=payload,
                        source=getattr(ledger, "source", _DEFAULT_SOURCE),
                        symbol=str(payload.get("symbol") or getattr(ledger, "symbol", "")),
                        side=str(payload.get("side") or getattr(ledger, "side", "")),
                        quantity=_to_float(payload.get("order_qty") or getattr(ledger, "quantity", 0.0)),
                        requested_price=_to_float(payload.get("order_price") or getattr(ledger, "requested_price", 0.0)),
                        requested_amount_krw=_to_float(payload.get("requested_amount_krw") or getattr(ledger, "requested_amount_krw", 0.0)),
                        order_type=str(payload.get("order_type") or getattr(ledger, "order_type", "")),
                        status="FILLED",
                        status_detail="이미 전량 체결된 주문",
                    )
                raise self._service_error(
                    status_code=409,
                    error_code=ErrorCode.ORDER_CANCEL_FAILED,
                    message="이미 전량 체결된 주문은 취소할 수 없습니다",
                    category="order_state",
                    broker_code="already_filled",
                    broker_message="이미 체결 완료",
                )
        elif error_info and error_info.code == "order_not_found":
            if ledger is not None and str(ledger.status or "").upper() == "CANCELED":
                return CoinOrderCancelResponse(
                    order_id=order_id,
                    status="CANCELED",
                    broker_state="CANCEL",
                    canceled=True,
                    already_canceled=True,
                    filled_quantity=_to_float(getattr(ledger, "filled_quantity", 0.0)),
                    remaining_quantity=max(
                        _to_float(getattr(ledger, "quantity", 0.0)) - _to_float(getattr(ledger, "filled_quantity", 0.0)),
                        0.0,
                    ),
                    message="이미 취소된 주문입니다",
                )
            raise self._service_error(
                status_code=404,
                error_code=ErrorCode.NOT_FOUND,
                message=f"주문을 찾을 수 없습니다: {order_id}",
                category=error_info.category,
                broker_code=error_info.code,
                broker_message=error_info.message,
            )
        elif error_info and error_info.code == "order_not_ready":
            raise self._service_error(
                status_code=409,
                error_code=ErrorCode.ORDER_CANCEL_FAILED,
                message="주문이 아직 취소 가능한 상태로 준비되지 않았습니다. 잠시 후 다시 시도하세요",
                category=error_info.category,
                broker_code=error_info.code,
                broker_message=error_info.message,
            )
        elif error_info:
            raise self._service_error(
                status_code=500,
                error_code=ErrorCode.ORDER_CANCEL_FAILED,
                message=f"주문 상태 확인 실패: {error_info.message}",
                category=error_info.category,
                broker_code=error_info.code,
                broker_message=error_info.message,
            )

        cancel_resp = await bithumb_client.cancel_order(order_id=order_id, market=self.market)
        if not cancel_resp.success:
            cancel_error = self._extract_broker_error(cancel_resp)
            if cancel_error.code == "order_not_ready":
                payload, retry_error = await self._poll_broker_order(order_id)
                if payload and self._internal_status(payload) == "CANCELED":
                    cancel_resp = MCPResponse(success=True, data=payload)
                elif retry_error is not None:
                    self._raise_broker_error(cancel_resp, default_error=retry_error, cancel_operation=True)
            else:
                self._raise_broker_error(cancel_resp, default_error=cancel_error, cancel_operation=True)

        synced_payload, error_info = await self._poll_broker_order(order_id)
        if synced_payload is None:
            if error_info and error_info.code not in _ORDER_WAIT_RETRY_CODES and error_info.code != "order_not_found":
                raise self._service_error(
                    status_code=500,
                    error_code=ErrorCode.ORDER_CANCEL_FAILED,
                    message=f"취소 후 주문 상태 조회 실패: {error_info.message}",
                    category=error_info.category,
                    broker_code=error_info.code,
                    broker_message=error_info.message,
                )
            synced_payload = {
                "order_id": order_id,
                "symbol": getattr(ledger, "symbol", "") if ledger else "",
                "side": getattr(ledger, "side", "") if ledger else "",
                "order_type": getattr(ledger, "order_type", "") if ledger else "",
                "state": "CANCEL",
                "order_qty": _to_float(getattr(ledger, "quantity", 0.0)) if ledger else 0.0,
                "filled_qty": _to_float(getattr(ledger, "filled_quantity", 0.0)) if ledger else 0.0,
                "remaining_qty": max(
                    _to_float(getattr(ledger, "quantity", 0.0)) - _to_float(getattr(ledger, "filled_quantity", 0.0)),
                    0.0,
                ) if ledger else 0.0,
                "order_price": _to_float(getattr(ledger, "requested_price", 0.0)) if ledger else 0.0,
                "requested_amount_krw": _to_float(getattr(ledger, "requested_amount_krw", 0.0)) if ledger else 0.0,
                "filled_price": _to_float(getattr(ledger, "filled_price", 0.0)) if ledger else 0.0,
            }

        response = self._build_status_response(
            order_id=order_id,
            payload=synced_payload,
            source=getattr(ledger, "source", _DEFAULT_SOURCE) if ledger else _DEFAULT_SOURCE,
            broker_synced=synced_payload is not None,
            message="주문이 취소되었습니다",
        )
        await self._upsert_broker_order(
            order_id=order_id,
            payload=synced_payload,
            source=response.source or _DEFAULT_SOURCE,
            symbol=response.symbol,
            side=response.side,
            quantity=response.order_quantity,
            requested_price=response.requested_price,
            requested_amount_krw=response.requested_amount_krw,
            order_type=response.broker_order_type,
            status="CANCELED",
            status_detail="주문 취소",
            create_if_missing=True,
        )
        account_manager.invalidate_cache()

        await activity_logger.log(
            ActivityType.ORDER,
            ActivityPhase.COMPLETE,
            f"🧾 [{response.symbol or order_id}] 수동 주문 취소 완료",
            market_scope=MARKET_SCOPE_CRYPTO,
            symbol=response.symbol or None,
            detail={
                "order_id": order_id,
                "status": "CANCELED",
                "source": response.source or _DEFAULT_SOURCE,
            },
        )

        return CoinOrderCancelResponse(
            order_id=order_id,
            status="CANCELED",
            broker_state=response.broker_state,
            canceled=True,
            already_canceled=False,
            filled_quantity=response.filled_quantity,
            remaining_quantity=response.remaining_quantity,
            message="주문이 취소되었습니다",
        )

    @staticmethod
    def _broker_order_type(side: str, order_type: str) -> str:
        if order_type == OrderType.LIMIT.value:
            return "LIMIT"
        if side == OrderSide.BUY.value:
            return "PRICE"
        return "MARKET"

    @staticmethod
    def _logical_order_type(broker_order_type: str) -> str:
        return OrderType.LIMIT.value if str(broker_order_type or "").upper() == "LIMIT" else OrderType.MARKET.value

    def _build_broker_payload_preview(
        self,
        *,
        symbol: str,
        side: str,
        broker_order_type: str,
        normalized_quantity: float,
        normalized_amount_krw: float,
        limit_price: float | None,
    ) -> dict[str, Any]:
        market_pair = f"KRW-{symbol}"
        payload: dict[str, Any] = {
            "market": market_pair,
            "side": "bid" if side == OrderSide.BUY.value else "ask",
        }
        if broker_order_type == "LIMIT":
            payload["ord_type"] = "limit"
            payload["volume"] = str(Decimal(str(normalized_quantity)))
            payload["price"] = str(Decimal(str(limit_price or 0)).quantize(Decimal("1"), rounding=ROUND_DOWN))
        elif broker_order_type == "PRICE":
            payload["ord_type"] = "price"
            payload["price"] = str(Decimal(str(normalized_amount_krw)))
        else:
            payload["ord_type"] = "market"
            payload["volume"] = str(Decimal(str(normalized_quantity)))
        return payload

    @staticmethod
    def _internal_status(payload: dict[str, Any]) -> str:
        raw_status = str(payload.get("status") or "").upper()
        if raw_status in {"SUBMITTED", "OPEN", "PARTIAL", "FILLED", "CANCELED"}:
            return raw_status

        state = str(payload.get("state") or "").strip().lower()
        filled_qty = _to_float(payload.get("filled_qty") or payload.get("filled_quantity") or payload.get("executed_volume"))
        remaining_qty = _to_float(payload.get("remaining_qty") or payload.get("remaining_volume"))
        if state == "done" or (filled_qty > 0 and remaining_qty <= 0):
            return "FILLED"
        if state == "cancel":
            return "CANCELED"
        if filled_qty > 0:
            return "PARTIAL"
        if state == "trade":
            return "OPEN"
        return "SUBMITTED"

    def _build_status_response(
        self,
        *,
        order_id: str,
        payload: dict[str, Any] | None,
        source: str | None,
        broker_synced: bool,
        message: str | None = None,
    ) -> CoinOrderStatusResponse:
        payload = payload or {}
        broker_order_type = str(payload.get("order_type") or payload.get("ord_type") or "").upper()
        status = self._internal_status(payload)
        submitted_at = payload.get("created_at")
        if isinstance(submitted_at, datetime):
            submitted_at = submitted_at.isoformat()
        return CoinOrderStatusResponse(
            order_id=order_id,
            symbol=str(payload.get("symbol") or ""),
            market=self.market,
            side=str(payload.get("side") or ""),
            order_type=self._logical_order_type(broker_order_type),
            broker_order_type=broker_order_type,
            source=source,
            status=status,
            broker_state=str(payload.get("state") or "").upper(),
            requested_price=_to_float(payload.get("order_price") or payload.get("price")),
            requested_amount_krw=_to_float(payload.get("requested_amount_krw")),
            order_quantity=_to_float(payload.get("order_qty") or payload.get("volume")),
            filled_quantity=_to_float(payload.get("filled_qty") or payload.get("filled_quantity")),
            remaining_quantity=_to_float(payload.get("remaining_qty") or payload.get("remaining_volume")),
            filled_price=_to_float(payload.get("filled_price")),
            can_cancel=status in {"SUBMITTED", "OPEN", "PARTIAL"},
            broker_synced=broker_synced,
            message=message or self._status_message(status),
            submitted_at=submitted_at if isinstance(submitted_at, str) else None,
        )

    def _build_ledger_status_response(
        self,
        ledger: CoinBrokerOrder,
        *,
        broker_synced: bool,
        message: str,
        error_info: _BrokerErrorInfo | None = None,
    ) -> CoinOrderStatusResponse:
        remaining_quantity = max(
            _to_float(ledger.quantity) - _to_float(ledger.filled_quantity),
            0.0,
        )
        return CoinOrderStatusResponse(
            order_id=str(ledger.bithumb_order_id),
            symbol=str(ledger.symbol),
            market=self.market,
            side=str(ledger.side),
            order_type=self._logical_order_type(str(ledger.order_type)),
            broker_order_type=str(ledger.order_type or ""),
            source=str(ledger.source or ""),
            status=str(ledger.status or ""),
            broker_state=str(ledger.status_detail or ""),
            requested_price=_to_float(ledger.requested_price),
            requested_amount_krw=_to_float(ledger.requested_amount_krw),
            order_quantity=_to_float(ledger.quantity),
            filled_quantity=_to_float(ledger.filled_quantity),
            remaining_quantity=remaining_quantity,
            filled_price=_to_float(ledger.filled_price),
            can_cancel=str(ledger.status or "").upper() in {"SUBMITTED", "OPEN", "PARTIAL"},
            broker_synced=broker_synced,
            message=message,
            submitted_at=ledger.submitted_at.isoformat() if ledger.submitted_at else None,
            updated_at=ledger.updated_at.isoformat() if ledger.updated_at else None,
            error_category=error_info.category if error_info else None,
            broker_error_code=error_info.code if error_info else None,
            broker_error_message=error_info.message if error_info else None,
        )

    @staticmethod
    def _status_message(status: str) -> str:
        return {
            "SUBMITTED": "주문 접수",
            "OPEN": "주문 대기",
            "PARTIAL": "부분 체결",
            "FILLED": "체결 완료",
            "CANCELED": "주문 취소",
        }.get(status, status or "주문 상태 확인")

    async def _poll_broker_order(
        self,
        order_id: str,
        *,
        attempts: int = 3,
        delay_seconds: float = 0.35,
    ) -> tuple[dict[str, Any] | None, _BrokerErrorInfo | None]:
        last_error: _BrokerErrorInfo | None = None
        for attempt in range(attempts):
            response = await bithumb_client.get_order(order_id=order_id, market=self.market)
            if response.success and response.data:
                return response.data, None
            error_info = self._extract_broker_error(response)
            last_error = error_info
            if error_info.code in _ORDER_WAIT_RETRY_CODES and attempt < attempts - 1:
                await asyncio.sleep(delay_seconds)
                continue
            break
        return None, last_error

    async def _get_ledger_order(self, order_id: str) -> CoinBrokerOrder | None:
        return await self.db.scalar(
            select(CoinBrokerOrder)
            .where(CoinBrokerOrder.bithumb_order_id == order_id)
            .limit(1)
        )

    async def _upsert_broker_order(
        self,
        *,
        order_id: str,
        payload: dict[str, Any] | None,
        source: str,
        symbol: str,
        side: str,
        quantity: float,
        requested_price: float,
        requested_amount_krw: float,
        order_type: str,
        status: str,
        status_detail: str,
        error_message: str = "",
        create_if_missing: bool = True,
    ) -> CoinBrokerOrder | None:
        existing = await self._get_ledger_order(order_id)
        if existing is None and not create_if_missing:
            return None
        if existing is None:
            existing = CoinBrokerOrder(
                bithumb_order_id=order_id,
                symbol=symbol,
                coin_name=symbol,
                side=side,
                quantity=float(quantity or 0.0),
                requested_price=float(requested_price or 0.0),
                requested_amount_krw=float(requested_amount_krw or 0.0),
                currency="KRW",
                order_type=order_type or "LIMIT",
                strategy_type="MANUAL_API",
                source=source or _DEFAULT_SOURCE,
                status=status or "SUBMITTED",
                submitted_at=now_kst(),
            )
            self.db.add(existing)

        payload = payload or {}
        existing.symbol = symbol or existing.symbol
        existing.coin_name = str(payload.get("name") or existing.coin_name or symbol)
        existing.side = side or existing.side
        existing.quantity = _to_float(payload.get("order_qty") or quantity or existing.quantity)
        existing.requested_price = _to_float(payload.get("order_price") or payload.get("price") or requested_price or existing.requested_price)
        existing.requested_amount_krw = _to_float(
            payload.get("requested_amount_krw") or requested_amount_krw or existing.requested_amount_krw
        )
        existing.filled_quantity = _to_float(payload.get("filled_qty") or payload.get("filled_quantity") or existing.filled_quantity)
        existing.filled_price = _to_float(payload.get("filled_price") or existing.filled_price)
        existing.order_type = str(payload.get("order_type") or order_type or existing.order_type or "LIMIT")
        existing.currency = "KRW"
        existing.strategy_type = "MANUAL_API"
        existing.source = source or existing.source or _DEFAULT_SOURCE
        existing.status = status or existing.status
        existing.status_detail = status_detail or existing.status_detail
        existing.error_message = error_message or existing.error_message
        if existing.submitted_at is None:
            existing.submitted_at = now_kst()
        if existing.status in {"FILLED", "PARTIAL"} and existing.filled_quantity > 0:
            existing.filled_at = existing.filled_at or now_kst()
        await self.db.flush()
        return existing

    async def _log_order_error(
        self,
        symbol: str,
        request: CoinOrderPlaceRequest,
        response: MCPResponse,
    ) -> None:
        error_info = self._extract_broker_error(response)
        await activity_logger.log(
            ActivityType.ORDER,
            ActivityPhase.ERROR,
            f"❌ [{symbol}] 수동 주문 실패",
            market_scope=MARKET_SCOPE_CRYPTO,
            symbol=symbol,
            detail={
                "side": request.side.value,
                "order_type": request.order_type.value,
                "amount_krw": request.amount_krw,
                "quantity": request.quantity,
                "limit_price": request.limit_price,
                "broker_error_code": error_info.code,
                "broker_error_message": error_info.message,
                "error_category": error_info.category,
                "reason": request.reason,
            },
            error_message=error_info.message,
        )

    @staticmethod
    def _extract_broker_error(response: MCPResponse) -> _BrokerErrorInfo:
        raw_message = str(response.error or "")
        raw_code = ""

        if isinstance(response.data, dict):
            err = response.data.get("error")
            if isinstance(err, dict):
                raw_code = str(err.get("name") or raw_code)
                raw_message = str(err.get("message") or raw_message)

        if not raw_code:
            match = re.match(r"^\[([^\]]+)\]\s*(.*)$", raw_message)
            if match:
                raw_code = match.group(1).strip()
                raw_message = match.group(2).strip()

        category = "upstream"
        if raw_code in _VALIDATION_ERROR_CODES:
            category = "validation"
        elif raw_code in _AUTH_ERROR_CODES:
            category = "auth"
        elif raw_code in _ORDER_STATE_ERROR_CODES:
            category = "order_state"
        elif any(token in raw_message for token in ("잔고", "보유 가능 수량", "보유수량", "주문 가능", "insufficient")):
            category = "funds"

        return _BrokerErrorInfo(
            category=category,
            code=raw_code,
            message=raw_message or raw_code or "브로커 오류",
        )

    def _raise_broker_error(
        self,
        response: MCPResponse,
        *,
        default_error: _BrokerErrorInfo | None = None,
        cancel_operation: bool = False,
    ) -> None:
        info = default_error or self._extract_broker_error(response)
        if info.category == "validation":
            raise self._service_error(
                status_code=400,
                error_code=ErrorCode.BAD_REQUEST,
                message=info.message,
                category=info.category,
                broker_code=info.code,
                broker_message=info.message,
            )
        if info.category == "funds":
            raise self._service_error(
                status_code=400,
                error_code=ErrorCode.INSUFFICIENT_BALANCE,
                message=info.message,
                category=info.category,
                broker_code=info.code,
                broker_message=info.message,
            )
        if info.category == "order_state":
            if info.code == "order_not_found":
                raise self._service_error(
                    status_code=404,
                    error_code=ErrorCode.NOT_FOUND,
                    message=info.message,
                    category=info.category,
                    broker_code=info.code,
                    broker_message=info.message,
                )
            raise self._service_error(
                status_code=409,
                error_code=ErrorCode.ORDER_CANCEL_FAILED if cancel_operation else ErrorCode.BAD_REQUEST,
                message=info.message,
                category=info.category,
                broker_code=info.code,
                broker_message=info.message,
            )
        raise self._service_error(
            status_code=500,
            error_code=ErrorCode.ORDER_CANCEL_FAILED if cancel_operation else ErrorCode.INTERNAL_SERVER_ERROR,
            message=info.message,
            category=info.category,
            broker_code=info.code,
            broker_message=info.message,
        )

    @staticmethod
    def _service_error(
        *,
        status_code: int,
        error_code: ErrorCode,
        message: str,
        category: str,
        broker_code: str | None = None,
        broker_message: str | None = None,
    ) -> ServiceException:
        data = {"error_category": category}
        if broker_code:
            data["broker_error_code"] = broker_code
        if broker_message:
            data["broker_error_message"] = broker_message
        return ServiceException(
            error_code=error_code,
            message=message,
            status_code=status_code,
            data=data,
        )

    @staticmethod
    def _preview_error_category(preview: CoinOrderPreviewResponse) -> str:
        joined = " ".join(preview.validation_errors)
        if any(token in joined for token in ("KRW 부족", "보유 가능 수량 부족", "보유 수량", "가능 수량", "가능 KRW")):
            return "funds"
        return "validation"

    def _raise_from_preview(self, preview: CoinOrderPreviewResponse) -> None:
        category = self._preview_error_category(preview)
        error_code = ErrorCode.INSUFFICIENT_BALANCE if category == "funds" else ErrorCode.BAD_REQUEST
        raise self._service_error(
            status_code=400,
            error_code=error_code,
            message=preview.validation_errors[0] if preview.validation_errors else "주문 검증 실패",
            category=category,
            broker_code="preview_validation_failed",
            broker_message=" | ".join(preview.validation_errors),
        )

    @staticmethod
    def _ensure_trading_enabled() -> None:
        if not settings.CRYPTO_ENABLED:
            raise ServiceException(
                error_code=ErrorCode.TRADING_DISABLED,
                message="코인 기능이 비활성화되어 있습니다 (CRYPTO_ENABLED=false)",
                status_code=400,
                data={"error_category": "validation"},
            )
        if not settings.CRYPTO_TRADING_ENABLED:
            raise ServiceException(
                error_code=ErrorCode.TRADING_DISABLED,
                message="코인 매매가 비활성화되어 있습니다 (CRYPTO_TRADING_ENABLED=false)",
                status_code=400,
                data={"error_category": "validation"},
            )
