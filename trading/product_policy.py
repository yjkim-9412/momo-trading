from __future__ import annotations

from dataclasses import dataclass
import re

from core.config import settings
from scheduler.market_calendar import market_calendar
from trading.market_profile import is_crypto_market, is_us_market, normalize_market

_RESTRICTED_PRODUCT_TYPES = {"LEVERAGED_ETF", "INVERSE_ETF"}
_LEVERAGE_MULTIPLIER_PATTERNS: tuple[tuple[re.Pattern[str], float], ...] = (
    (re.compile(r"(?<!\d)3(?:\s|-)?X\b"), 3.0),
    (re.compile(r"(?<!\d)2(?:\s|-)?X\b"), 2.0),
    (re.compile(r"(?<!\d)1(?:\s|-)?X\b"), 1.0),
)
_TRIPLE_LEVERAGE_HINTS = ("ULTRAPRO", "TRIPLE")
_DOUBLE_LEVERAGE_HINTS = ("ULTRA", "DOUBLE")


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
    leverage_multiplier: float = 1.0
    classification_source: str = "default"

    @property
    def is_restricted(self) -> bool:
        return self.product_type in _RESTRICTED_PRODUCT_TYPES

    @property
    def signed_exposure(self) -> float:
        base = self.leverage_multiplier if self.leverage_multiplier > 0 else 1.0
        return -base if self.is_inverse else base

    def to_metadata(self) -> dict:
        """시그널/이벤트 메타데이터로 직렬화"""
        return {
            "product_type": self.product_type,
            "is_leveraged": self.is_leveraged,
            "is_inverse": self.is_inverse,
            "leverage_multiplier": self.leverage_multiplier,
            "signed_exposure": self.signed_exposure,
            "restricted_product": self.is_restricted,
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
    leverage_multiplier: float = 1.0,
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
        leverage_multiplier=leverage_multiplier,
        classification_source=classification_source,
    )


def _infer_leverage_multiplier(
    text: str,
    *,
    is_leveraged: bool = False,
    is_inverse: bool = False,
) -> float:
    normalized = str(text or "").upper()
    for pattern, multiplier in _LEVERAGE_MULTIPLIER_PATTERNS:
        if pattern.search(normalized):
            return multiplier

    if any(keyword in normalized for keyword in _TRIPLE_LEVERAGE_HINTS):
        return 3.0
    if any(keyword in normalized for keyword in _DOUBLE_LEVERAGE_HINTS):
        return 2.0
    if is_inverse:
        return 1.0
    if is_leveraged:
        return 2.0
    return 1.0


def build_product_context(
    symbol: str,
    market: str,
    metadata: dict | None = None,
) -> dict:
    classification = classification_from_metadata(symbol, market, metadata)
    return {
        "product_type": classification.product_type,
        "is_leveraged": classification.is_leveraged,
        "is_inverse": classification.is_inverse,
        "leverage_multiplier": classification.leverage_multiplier,
        "signed_exposure": classification.signed_exposure,
        "restricted_product": classification.is_restricted,
        "classification_source": classification.classification_source,
    }


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
    default_multiplier = _infer_leverage_multiplier(merged)

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
            leverage_multiplier=_infer_leverage_multiplier(merged, is_inverse=True),
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
            leverage_multiplier=_infer_leverage_multiplier(merged, is_leveraged=True),
            classification_source="name_or_category",
        )

    if "ETF" in merged:
        return _build_classification(
            symbol_upper,
            market_code,
            name=name,
            category=category,
            product_type="ETF",
            leverage_multiplier=default_multiplier,
            classification_source="name_or_category",
        )

    return _build_classification(
        symbol_upper,
        market_code,
        name=name,
        category=category,
        leverage_multiplier=default_multiplier,
    )


def classify_product(
    symbol: str,
    market: str,
    *,
    name: str = "",
    category: str = "",
) -> ProductClassification:
    """티커/이름/카테고리 기준 상품 분류 (크립토는 항상 COMMON/Spot)"""
    if is_crypto_market(market):
        return _build_classification(
            symbol, normalize_market(market),
            name=name, category=category or "암호화폐",
            product_type="COMMON",
            classification_source="crypto_spot",
        )
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
                leverage_multiplier=classification.leverage_multiplier,
                classification_source="denylist",
            )
        return _build_classification(
            symbol_upper,
            classification.market,
            name=classification.name,
            category=classification.category,
            product_type="LEVERAGED_ETF",
            is_leveraged=True,
            leverage_multiplier=max(classification.leverage_multiplier, 2.0),
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
                leverage_multiplier=classification.leverage_multiplier,
                classification_source="allowlist",
            )
        return _build_classification(
            symbol_upper,
            classification.market,
            name=classification.name,
            category=classification.category,
            product_type="LEVERAGED_ETF",
            is_leveraged=True,
            leverage_multiplier=max(classification.leverage_multiplier, 2.0),
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
        name = str(data.get("name") or "")
        category = str(data.get("category") or "")
        merged = " ".join(part for part in (category, name) if part).strip()
        is_leveraged = bool(data.get("is_leveraged"))
        is_inverse = bool(data.get("is_inverse"))
        return _build_classification(
            symbol,
            market,
            name=name,
            category=category,
            product_type=product_type,
            is_leveraged=is_leveraged,
            is_inverse=is_inverse,
            leverage_multiplier=float(
                data.get("leverage_multiplier")
                or _infer_leverage_multiplier(
                    merged,
                    is_leveraged=is_leveraged,
                    is_inverse=is_inverse,
                )
            ),
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

