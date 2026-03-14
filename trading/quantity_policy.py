"""시장별 수량 정규화 정책.

주식은 기존 정수 수량 semantics를 유지하고,
코인만 8자리 소수점 floor 정책을 사용한다.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN

from trading.market_profile import is_crypto_market, normalize_market

CRYPTO_QUANTITY_STEP = Decimal("0.00000001")
_STOCK_QUANTITY_STEP = Decimal("1")


def _to_decimal(value: object, default: Decimal = Decimal("0")) -> Decimal:
    try:
        if value in (None, ""):
            return default
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def quantity_step(market: str) -> Decimal:
    market_code = normalize_market(market)
    if is_crypto_market(market_code):
        return CRYPTO_QUANTITY_STEP
    return _STOCK_QUANTITY_STEP


def normalize_quantity(value: object, market: str) -> float:
    market_code = normalize_market(market)
    quantity = _to_decimal(value)
    if quantity <= 0:
        return 0.0

    if is_crypto_market(market_code):
        return float(quantity.quantize(CRYPTO_QUANTITY_STEP, rounding=ROUND_DOWN))

    return float(max(int(quantity), 0))


def cap_quantity_for_amount(max_amount: float, unit_price: float, market: str) -> float:
    market_code = normalize_market(market)
    amount = _to_decimal(max_amount)
    price = _to_decimal(unit_price)
    if amount <= 0 or price <= 0:
        return 0.0

    if is_crypto_market(market_code):
        raw_quantity = amount / price
        return float(raw_quantity.quantize(CRYPTO_QUANTITY_STEP, rounding=ROUND_DOWN))

    return float(max(int(amount / price), 0))


def has_quantity(value: object, market: str) -> bool:
    return normalize_quantity(value, market) > 0


def format_quantity(quantity: object, market: str) -> str:
    normalized = normalize_quantity(quantity, market)
    market_code = normalize_market(market)
    if is_crypto_market(market_code):
        return f"{normalized:.8f}".rstrip("0").rstrip(".") or "0"
    return str(int(normalized))


def quantity_unit(market: str) -> str:
    if is_crypto_market(normalize_market(market)):
        return "개"
    return "주"


def format_quantity_with_unit(quantity: object, market: str) -> str:
    return f"{format_quantity(quantity, market)}{quantity_unit(market)}"
