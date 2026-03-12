from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from trading.enums import Market


@dataclass(frozen=True)
class MarketProfile:
    """시장별 런타임 프로필"""

    code: str
    region: str
    label: str
    currency: str
    timezone: str
    kis_exchange_code: str
    is_domestic: bool


_MARKET_PROFILES = {
    "KRX": MarketProfile(
        code="KRX",
        region="KR",
        label="한국 주식 시장",
        currency="KRW",
        timezone="Asia/Seoul",
        kis_exchange_code="KRX",
        is_domestic=True,
    ),
    "KOSPI": MarketProfile(
        code="KOSPI",
        region="KR",
        label="코스피",
        currency="KRW",
        timezone="Asia/Seoul",
        kis_exchange_code="KOSPI",
        is_domestic=True,
    ),
    "KOSDAQ": MarketProfile(
        code="KOSDAQ",
        region="KR",
        label="코스닥",
        currency="KRW",
        timezone="Asia/Seoul",
        kis_exchange_code="KOSDAQ",
        is_domestic=True,
    ),
    "NASDAQ": MarketProfile(
        code="NASDAQ",
        region="US",
        label="미국 나스닥",
        currency="USD",
        timezone="America/New_York",
        kis_exchange_code="NAS",
        is_domestic=False,
    ),
    "NYSE": MarketProfile(
        code="NYSE",
        region="US",
        label="미국 뉴욕증권거래소",
        currency="USD",
        timezone="America/New_York",
        kis_exchange_code="NYS",
        is_domestic=False,
    ),
    "AMEX": MarketProfile(
        code="AMEX",
        region="US",
        label="미국 아멕스",
        currency="USD",
        timezone="America/New_York",
        kis_exchange_code="AMS",
        is_domestic=False,
    ),
}

_MARKET_ALIASES = {
    "": "KRX",
    "DOMESTIC": "KRX",
    "KOREA": "KRX",
    "OVERSEAS": "NASDAQ",
    "US": "NASDAQ",
    "USA": "NASDAQ",
    "NAS": "NASDAQ",
    "NYS": "NYSE",
    "AMS": "AMEX",
}

_US_SCAN_TARGETS = ("NASDAQ", "NYSE", "AMEX")
_ORDER_EXCHANGE_CODES = {
    "NASDAQ": "NASD",
    "NYSE": "NYSE",
    "AMEX": "AMEX",
}
_BALANCE_EXCHANGE_CODES = {
    "NASDAQ": "NASD",
    "NYSE": "NASD",
    "AMEX": "NASD",
}
_WS_DELAYED_PREFIX = {
    "NASDAQ": "NAS",
    "NYSE": "NYS",
    "AMEX": "AMS",
}
_WS_DAYTIME_PREFIX = {
    "NASDAQ": "BAQ",
    "NYSE": "BAY",
    "AMEX": "BAA",
}


def normalize_market(market: str | Market | None, default: str = "KRX") -> str:
    """시장 코드를 정규화"""
    if isinstance(market, Market):
        raw = market.value
    else:
        raw = str(market or default).strip().upper()

    raw = _MARKET_ALIASES.get(raw, raw)
    if raw in _MARKET_PROFILES:
        return raw
    return _MARKET_ALIASES.get(default.upper(), default.upper())


def get_market_profile(market: str | Market | None) -> MarketProfile:
    """시장 프로필 반환"""
    return _MARKET_PROFILES[normalize_market(market)]


def is_domestic_market(market: str | Market | None) -> bool:
    """국내 시장 여부"""
    return get_market_profile(market).is_domestic


def is_us_market(market: str | Market | None) -> bool:
    """미국 시장 여부"""
    return get_market_profile(market).region == "US"


def market_currency(market: str | Market | None) -> str:
    """시장 통화 코드 반환"""
    return get_market_profile(market).currency


def kis_exchange_code(market: str | Market | None) -> str:
    """KIS 해외거래소 코드 반환"""
    return get_market_profile(market).kis_exchange_code


def kis_order_exchange_code(market: str | Market | None) -> str:
    """KIS 해외주문 거래소 코드 반환"""
    normalized = normalize_market(market)
    return _ORDER_EXCHANGE_CODES.get(normalized, kis_exchange_code(normalized))


def kis_balance_exchange_code(market: str | Market | None) -> str:
    """KIS 해외 잔고/주문내역 거래소 코드 반환"""
    normalized = normalize_market(market)
    return _BALANCE_EXCHANGE_CODES.get(normalized, kis_order_exchange_code(normalized))


def market_label(market: str | Market | None) -> str:
    """사용자 표시용 시장명 반환"""
    return get_market_profile(market).label


def market_timezone(market: str | Market | None) -> str:
    """시장 타임존 반환"""
    return get_market_profile(market).timezone


def build_ws_key(
    symbol: str,
    market: str | Market | None,
    session: str = "",
) -> str:
    """해외 WebSocket 구독 키를 생성"""
    normalized = normalize_market(market)
    if is_domestic_market(normalized):
        return symbol.upper()

    if session == "US_DAYTIME":
        return f"R{_WS_DAYTIME_PREFIX.get(normalized, 'BAQ')}{symbol.upper()}"
    return f"D{_WS_DELAYED_PREFIX.get(normalized, 'NAS')}{symbol.upper()}"


def expand_scan_markets(markets: Iterable[str]) -> list[str]:
    """스캔 대상 시장 목록 확장"""
    expanded: list[str] = []
    for market in markets:
        normalized = normalize_market(market)
        if normalized == "NASDAQ" and str(market).strip().upper() in {"US", "USA"}:
            for code in _US_SCAN_TARGETS:
                if code not in expanded:
                    expanded.append(code)
            continue
        if normalized not in expanded:
            expanded.append(normalized)
    return expanded or ["KRX"]
