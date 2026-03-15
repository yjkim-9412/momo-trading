from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from trading.enums import OrderSide, OrderType


class CoinOrderRequestBase(BaseModel):
    symbol: str
    side: OrderSide
    order_type: OrderType
    amount_krw: float | None = None
    quantity: float | None = None
    limit_price: float | None = None
    reason: str | None = None


class CoinOrderPreviewRequest(CoinOrderRequestBase):
    pass


class CoinOrderPlaceRequest(CoinOrderRequestBase):
    pass


class CoinOrderPreviewResponse(BaseModel):
    symbol: str
    market: str = "BITHUMB"
    side: str
    order_type: str
    broker_order_type: str
    placeable: bool
    limit_price: float | None = None
    reference_price: float = 0.0
    requested_amount_krw: float = 0.0
    normalized_amount_krw: float = 0.0
    requested_quantity: float = 0.0
    normalized_quantity: float = 0.0
    available_krw: float | None = None
    available_quantity: float | None = None
    orderable_quantity: float | None = None
    estimated_fee_krw: float = 0.0
    estimated_locked_krw: float = 0.0
    min_order_amount_krw: float = 0.0
    warnings: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    broker_payload_preview: dict[str, Any] = Field(default_factory=dict)


class CoinOrderStatusResponse(BaseModel):
    order_id: str
    symbol: str
    market: str = "BITHUMB"
    side: str
    order_type: str
    broker_order_type: str
    source: str | None = None
    status: str
    broker_state: str = ""
    requested_price: float = 0.0
    requested_amount_krw: float = 0.0
    order_quantity: float = 0.0
    filled_quantity: float = 0.0
    remaining_quantity: float = 0.0
    filled_price: float = 0.0
    can_cancel: bool = False
    broker_synced: bool = True
    message: str | None = None
    submitted_at: str | None = None
    updated_at: str | None = None
    error_category: str | None = None
    broker_error_code: str | None = None
    broker_error_message: str | None = None


class CoinOrderExecutionResponse(BaseModel):
    order_id: str
    symbol: str
    market: str = "BITHUMB"
    side: str
    order_type: str
    broker_order_type: str
    source: str = "MANUAL_API"
    status: str
    broker_state: str = ""
    requested_price: float = 0.0
    requested_amount_krw: float = 0.0
    normalized_amount_krw: float = 0.0
    normalized_quantity: float = 0.0
    filled_quantity: float = 0.0
    remaining_quantity: float = 0.0
    filled_price: float = 0.0
    broker_synced: bool = True
    message: str
    preview: CoinOrderPreviewResponse


class CoinOrderCancelResponse(BaseModel):
    order_id: str
    status: str
    broker_state: str = ""
    canceled: bool
    already_canceled: bool = False
    filled_quantity: float = 0.0
    remaining_quantity: float = 0.0
    message: str
