from __future__ import annotations

from dataclasses import dataclass

from core.config import settings
from scheduler.market_calendar import market_calendar
from trading.market_profile import is_us_market, normalize_market

_RESTRICTED_PRODUCT_TYPES = {"LEVERAGED_ETF", "INVERSE_ETF"}


@dataclass(frozen=True)
class ProductClassification:
    """상품 분류 결과"""

    symbol: str
    market: str
    name: str = ""
    category: str = ""
    product_type: str = "COMMON"
    is_leveraged: bool = False
    is_inverse: bool = False
    classification_source: str = "default"

    @property
    def is_restricted(self) -> bool:
        return self.product_type in _RESTRICTED_PRODUCT_TYPES

    def to_metadata(self) -> dict:
        """시그널/이벤트 메타데이터로 직렬화"""
        return {
            "product_type": self.product_type,
            "is_leveraged": self.is_leveraged,
            "is_inverse": self.is_inverse,
            "classification_source": self.classification_source,
            "category": self.category,
            "name": self.name,
        }


def _contains_keyword(text: str, keywords: list[str]) -> bool:
    return any(keyword and keyword in text for keyword in keywords)


def _build_classification(
    symbol: str,
    market: str,
    *,
    name: str = "",
    category: str = "",
    product_type: str = "COMMON",
    is_leveraged: bool = False,
    is_inverse: bool = False,
    classification_source: str = "default",
) -> ProductClassification:
    return ProductClassification(
        symbol=str(symbol).upper(),
        market=normalize_market(market),
        name=str(name or ""),
        category=str(category or ""),
        product_type=product_type,
        is_leveraged=is_leveraged,
        is_inverse=is_inverse,
        classification_source=classification_source,
    )


def _classify_from_text(
    symbol: str,
    market: str,
    *,
    name: str = "",
    category: str = "",
) -> ProductClassification:
    symbol_upper = str(symbol).upper()
    market_code = normalize_market(market)
    name_upper = str(name or "").upper()
    category_upper = str(category or "").upper()
    merged = " ".join(part for part in (category_upper, name_upper) if part).strip()

    if "ETN" in merged:
        return _build_classification(
            symbol_upper,
            market_code,
            name=name,
            category=category,
            product_type="ETN",
            classification_source="name_or_category",
        )

    if _contains_keyword(merged, settings.us_inverse_keywords_list):
        return _build_classification(
            symbol_upper,
            market_code,
            name=name,
            category=category,
            product_type="INVERSE_ETF",
            is_inverse=True,
            classification_source="name_or_category",
        )

    if _contains_keyword(merged, settings.us_leverage_keywords_list):
        return _build_classification(
            symbol_upper,
            market_code,
            name=name,
            category=category,
            product_type="LEVERAGED_ETF",
            is_leveraged=True,
            classification_source="name_or_category",
        )

    if "ETF" in merged:
        return _build_classification(
            symbol_upper,
            market_code,
            name=name,
            category=category,
            product_type="ETF",
            classification_source="name_or_category",
        )

    return _build_classification(
        symbol_upper,
        market_code,
        name=name,
        category=category,
    )


def classify_product(
    symbol: str,
    market: str,
    *,
    name: str = "",
    category: str = "",
) -> ProductClassification:
    """티커/이름/카테고리 기준 상품 분류"""
    classification = _classify_from_text(symbol, market, name=name, category=category)
    symbol_upper = classification.symbol

    if symbol_upper in settings.us_leverage_denylist_symbols:
        if classification.is_restricted:
            return _build_classification(
                symbol_upper,
                classification.market,
                name=classification.name,
                category=classification.category,
                product_type=classification.product_type,
                is_leveraged=classification.is_leveraged,
                is_inverse=classification.is_inverse,
                classification_source="denylist",
            )
        return _build_classification(
            symbol_upper,
            classification.market,
            name=classification.name,
            category=classification.category,
            product_type="LEVERAGED_ETF",
            is_leveraged=True,
            classification_source="denylist",
        )

    if symbol_upper in settings.us_leverage_allowlist_symbols:
        if classification.is_restricted:
            return _build_classification(
                symbol_upper,
                classification.market,
                name=classification.name,
                category=classification.category,
                product_type=classification.product_type,
                is_leveraged=classification.is_leveraged,
                is_inverse=classification.is_inverse,
                classification_source="allowlist",
            )
        return _build_classification(
            symbol_upper,
            classification.market,
            name=classification.name,
            category=classification.category,
            product_type="LEVERAGED_ETF",
            is_leveraged=True,
            classification_source="allowlist",
        )

    return classification


def classification_from_metadata(
    symbol: str,
    market: str,
    metadata: dict | None = None,
) -> ProductClassification:
    """메타데이터 우선으로 상품 분류 복원"""
    data = metadata or {}
    product_type = str(data.get("product_type") or "").upper()
    if product_type:
        return _build_classification(
            symbol,
            market,
            name=str(data.get("name") or ""),
            category=str(data.get("category") or ""),
            product_type=product_type,
            is_leveraged=bool(data.get("is_leveraged")),
            is_inverse=bool(data.get("is_inverse")),
            classification_source=str(data.get("classification_source") or "metadata"),
        )
    return classify_product(
        symbol=symbol,
        market=market,
        name=str(data.get("name") or ""),
        category=str(data.get("category") or ""),
    )


def coerce_strategy_for_product(
    strategy_type: str,
    classification: ProductClassification,
) -> str | None:
    """제한 상품의 허용 전략으로 강제 조정"""
    normalized = str(strategy_type or "").upper() or "STABLE_SHORT"
    if not classification.is_restricted or not is_us_market(classification.market):
        return normalized

    allowed = settings.us_leverage_allowed_strategies_list
    if not allowed:
        return None
    if normalized in allowed:
        return normalized
    return allowed[0]


def is_product_trade_allowed(
    classification: ProductClassification,
    *,
    strategy_type: str = "",
    session: str = "",
    side: str = "BUY",
) -> tuple[bool, str]:
    """상품 정책 기준 매수 허용 여부"""
    if str(side or "").upper() != "BUY":
        return True, "매도 주문"

    if classification.product_type == "ETN":
        return False, "ETN 상품은 자동매매 대상에서 제외"

    if not classification.is_restricted or not is_us_market(classification.market):
        return True, "일반 종목"

    if classification.symbol in settings.us_leverage_denylist_symbols:
        return False, "레버리지 차단 목록 종목"

    if classification.is_inverse and not settings.US_INVERSE_PRODUCTS_ENABLED:
        return False, "인버스 상품 비활성화"

    if classification.is_leveraged and not settings.US_LEVERAGED_PRODUCTS_ENABLED:
        return False, "레버리지 상품 비활성화"

    current_session = str(
        session or market_calendar.get_market_session(market=classification.market)
    ).upper()
    allowed_sessions = settings.us_leverage_allowed_sessions_list
    if allowed_sessions and current_session not in allowed_sessions:
        return False, f"제한 상품은 {', '.join(allowed_sessions)} 세션만 허용"

    allowed_strategies = settings.us_leverage_allowed_strategies_list
    normalized_strategy = str(strategy_type or "").upper()
    if allowed_strategies and normalized_strategy and normalized_strategy not in allowed_strategies:
        return False, f"제한 상품은 {', '.join(allowed_strategies)} 전략만 허용"

    return True, "제한 상품 허용 범위 내"

