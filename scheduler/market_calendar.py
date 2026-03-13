from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

try:
    import holidays
except ImportError:  # pragma: no cover - 의존성 미설치 로컬 폴백
    holidays = None

from core.config import settings
from trading.market_profile import (
    is_domestic_market,
    is_us_market,
    normalize_market,
    normalize_market_scope,
)
from util.time_util import KST, now_kst

_KR_HOLIDAYS = holidays.KR(years=range(2024, 2031)) if holidays else set()
_US_HOLIDAYS = (
    holidays.NYSE(years=range(2024, 2031))
    if holidays and hasattr(holidays, "NYSE")
    else holidays.US(years=range(2024, 2031)) if holidays else set()
)
_NY_TZ = ZoneInfo("America/New_York")


class MarketCalendar:
    """시장 거래 시간 관리"""

    KRX_OPEN = time(9, 0)
    KRX_CLOSE = time(15, 30)

    NXT_PRE_OPEN = time(8, 0)
    NXT_PRE_CLOSE = time(8, 50)
    NXT_AFTER_OPEN = time(15, 30)
    NXT_AFTER_CLOSE = time(20, 0)

    US_PRE_OPEN = time(4, 0)
    US_REGULAR_OPEN = time(9, 30)
    US_REGULAR_CLOSE = time(16, 0)
    US_AFTER_CLOSE = time(20, 0)

    @staticmethod
    def _to_kst(dt: datetime | None = None) -> datetime:
        current = dt or now_kst()
        if current.tzinfo is None:
            return current.replace(tzinfo=KST)
        return current.astimezone(KST)

    @staticmethod
    def _to_new_york(dt: datetime | None = None) -> datetime:
        return MarketCalendar._to_kst(dt).astimezone(_NY_TZ)

    @staticmethod
    def _us_session_flags() -> tuple[bool, bool]:
        return settings.US_PREMARKET_ENABLED, settings.US_AFTERMARKET_ENABLED

    @staticmethod
    def is_holiday(market: str | None = None, dt: datetime | None = None) -> bool:
        """시장 휴장일 여부"""
        market_code = normalize_market(market or settings.primary_market_code)
        current = MarketCalendar._to_kst(dt)

        if is_domestic_market(market_code):
            if current.weekday() >= 5:
                return True
            return current.date() in _KR_HOLIDAYS

        if is_us_market(market_code):
            ny_dt = current.astimezone(_NY_TZ)
            if ny_dt.weekday() >= 5:
                return True
            return ny_dt.date() in _US_HOLIDAYS

        return False

    @staticmethod
    def is_trading_day(market: str | None = None, dt: datetime | None = None) -> bool:
        """시장 거래일 여부"""
        return not MarketCalendar.is_holiday(market, dt)

    @staticmethod
    def is_krx_holiday(dt: datetime | None = None) -> bool:
        """KRX/NXT 휴장일 여부"""
        return MarketCalendar.is_holiday("KRX", dt)

    @staticmethod
    def is_krx_trading_day(dt: datetime | None = None) -> bool:
        """KRX/NXT 거래일 여부"""
        return MarketCalendar.is_trading_day("KRX", dt)

    @staticmethod
    def is_krx_trading_hours(dt: datetime | None = None) -> bool:
        """KRX 정규장 장중 여부"""
        current = MarketCalendar._to_kst(dt)
        if MarketCalendar.is_krx_holiday(current):
            return False
        return MarketCalendar.KRX_OPEN <= current.time() <= MarketCalendar.KRX_CLOSE

    @staticmethod
    def is_nxt_pre_market(dt: datetime | None = None) -> bool:
        """NXT 프리마켓 여부"""
        current = MarketCalendar._to_kst(dt)
        if MarketCalendar.is_krx_holiday(current):
            return False
        return MarketCalendar.NXT_PRE_OPEN <= current.time() <= MarketCalendar.NXT_PRE_CLOSE

    @staticmethod
    def is_nxt_after_market(dt: datetime | None = None) -> bool:
        """NXT 애프터마켓 여부"""
        current = MarketCalendar._to_kst(dt)
        if MarketCalendar.is_krx_holiday(current):
            return False
        return MarketCalendar.NXT_AFTER_OPEN <= current.time() <= MarketCalendar.NXT_AFTER_CLOSE

    @staticmethod
    def is_nxt_trading_hours(dt: datetime | None = None) -> bool:
        """NXT 거래 가능 시간 여부"""
        current = MarketCalendar._to_kst(dt)
        if MarketCalendar.is_krx_holiday(current):
            return False
        return MarketCalendar.NXT_PRE_OPEN <= current.time() <= MarketCalendar.NXT_AFTER_CLOSE

    @staticmethod
    def is_domestic_trading_hours(dt: datetime | None = None) -> bool:
        """국내 시장 거래 가능 여부"""
        return MarketCalendar.is_nxt_trading_hours(dt)

    @staticmethod
    def is_us_trading_hours(dt: datetime | None = None) -> bool:
        """미국장 거래 가능 여부"""
        ny_dt = MarketCalendar._to_new_york(dt)
        if MarketCalendar.is_holiday("NASDAQ", ny_dt):
            return False

        include_pre, include_after = MarketCalendar._us_session_flags()
        current_time = ny_dt.time()

        if include_pre and MarketCalendar.US_PRE_OPEN <= current_time < MarketCalendar.US_REGULAR_OPEN:
            return True
        if MarketCalendar.US_REGULAR_OPEN <= current_time <= MarketCalendar.US_REGULAR_CLOSE:
            return True
        if include_after and MarketCalendar.US_REGULAR_CLOSE < current_time <= MarketCalendar.US_AFTER_CLOSE:
            return True
        return False

    @staticmethod
    def is_trading_hours(market: str | None = None, dt: datetime | None = None) -> bool:
        """시장별 거래 가능 여부"""
        market_code = normalize_market(market or settings.primary_market_code)
        if is_domestic_market(market_code):
            return MarketCalendar.is_domestic_trading_hours(dt)
        if is_us_market(market_code):
            return MarketCalendar.is_us_trading_hours(dt)
        return False

    @staticmethod
    def get_market_session(dt: datetime | None = None, market: str | None = None) -> str:
        """현재 시장 세션 반환"""
        market_code = normalize_market(market or settings.primary_market_code)
        current = MarketCalendar._to_kst(dt)

        if is_domestic_market(market_code):
            if MarketCalendar.is_krx_holiday(current):
                return "CLOSED"
            t = current.time()
            if MarketCalendar.NXT_PRE_OPEN <= t < MarketCalendar.NXT_PRE_CLOSE:
                return "NXT_PRE"
            if time(9, 0) <= t < time(15, 20):
                return "KRX_NXT"
            if time(15, 20) <= t <= MarketCalendar.KRX_CLOSE:
                return "KRX_CLOSE"
            if MarketCalendar.NXT_AFTER_OPEN < t <= MarketCalendar.NXT_AFTER_CLOSE:
                return "NXT_AFTER"
            return "CLOSED"

        ny_dt = current.astimezone(_NY_TZ)
        if MarketCalendar.is_holiday(market_code, ny_dt):
            return "CLOSED"

        include_pre, include_after = MarketCalendar._us_session_flags()
        t = ny_dt.time()
        if include_pre and MarketCalendar.US_PRE_OPEN <= t < MarketCalendar.US_REGULAR_OPEN:
            return "US_PRE"
        if MarketCalendar.US_REGULAR_OPEN <= t <= MarketCalendar.US_REGULAR_CLOSE:
            return "US_REGULAR"
        if include_after and MarketCalendar.US_REGULAR_CLOSE < t <= MarketCalendar.US_AFTER_CLOSE:
            return "US_AFTER"
        return "CLOSED"

    @staticmethod
    def is_primary_market_trading_hours(dt: datetime | None = None) -> bool:
        """대표 시장 거래 가능 여부"""
        return MarketCalendar.is_trading_hours(settings.primary_market_code, dt)

    @staticmethod
    def is_primary_market_holiday(dt: datetime | None = None) -> bool:
        """대표 시장 휴장일 여부"""
        return MarketCalendar.is_holiday(settings.primary_market_code, dt)

    @staticmethod
    def next_primary_open(dt: datetime | None = None) -> datetime:
        """대표 시장 다음 개장 시각"""
        return MarketCalendar.next_market_open(dt, settings.primary_market_code)

    @staticmethod
    def market_date(dt: datetime | None = None, market: str | None = None):
        """시장 현지 기준 날짜"""
        market_code = normalize_market_scope(market or settings.primary_market_code)
        if is_us_market(market_code):
            return MarketCalendar._to_new_york(dt).date()
        return MarketCalendar._to_kst(dt).date()

    @staticmethod
    def market_day_bounds(
        market: str | None = None,
        trading_date: date | None = None,
    ) -> tuple[datetime, datetime]:
        """시장 거래일의 KST 기준 시작/종료 시각 반환"""
        scope = normalize_market_scope(market or settings.primary_market_code)
        local_date = trading_date or MarketCalendar.market_date(market=scope)
        tz = _NY_TZ if is_us_market(scope) else KST
        start_local = datetime.combine(local_date, time.min, tzinfo=tz)
        end_local = datetime.combine(local_date, time.max, tzinfo=tz)
        start_kst = start_local.astimezone(KST).replace(tzinfo=None)
        end_kst = end_local.astimezone(KST).replace(tzinfo=None)
        return start_kst, end_kst

    @staticmethod
    def is_any_market_open(dt: datetime | None = None) -> bool:
        """국내 또는 미국장 개장 여부"""
        return (
            MarketCalendar.is_domestic_trading_hours(dt)
            or MarketCalendar.is_us_trading_hours(dt)
        )

    @staticmethod
    def _next_us_open(dt: datetime | None = None) -> datetime:
        current = MarketCalendar._to_new_york(dt)
        include_pre, _ = MarketCalendar._us_session_flags()
        open_time = MarketCalendar.US_PRE_OPEN if include_pre else MarketCalendar.US_REGULAR_OPEN

        if MarketCalendar.is_trading_day("NASDAQ", current) and current.time() < open_time:
            target = datetime.combine(current.date(), open_time, tzinfo=_NY_TZ)
            return target.astimezone(KST)

        next_day = current + timedelta(days=1)
        while not MarketCalendar.is_trading_day("NASDAQ", next_day):
            next_day += timedelta(days=1)
        target = datetime.combine(next_day.date(), open_time, tzinfo=_NY_TZ)
        return target.astimezone(KST)

    @staticmethod
    def next_krx_open(dt: datetime | None = None) -> datetime:
        """다음 KRX 개장 시각"""
        current = MarketCalendar._to_kst(dt)
        if current.time() < MarketCalendar.KRX_OPEN and MarketCalendar.is_krx_trading_day(current):
            return current.replace(hour=9, minute=0, second=0, microsecond=0)
        next_day = current + timedelta(days=1)
        while MarketCalendar.is_krx_holiday(next_day):
            next_day += timedelta(days=1)
        return next_day.replace(hour=9, minute=0, second=0, microsecond=0)

    @staticmethod
    def next_market_open(dt: datetime | None = None, market: str | None = None) -> datetime:
        """시장별 다음 개장 시각"""
        market_code = normalize_market(market or settings.primary_market_code)
        if is_domestic_market(market_code):
            current = MarketCalendar._to_kst(dt)
            if current.time() < MarketCalendar.NXT_PRE_OPEN and MarketCalendar.is_krx_trading_day(current):
                return current.replace(hour=8, minute=0, second=0, microsecond=0)
            next_day = current + timedelta(days=1)
            while MarketCalendar.is_krx_holiday(next_day):
                next_day += timedelta(days=1)
            return next_day.replace(hour=8, minute=0, second=0, microsecond=0)
        if is_us_market(market_code):
            return MarketCalendar._next_us_open(dt)
        return MarketCalendar.next_krx_open(dt)

    @staticmethod
    def get_holiday_name(dt: datetime | None = None, market: str | None = None) -> str | None:
        """공휴일이면 휴일명 반환"""
        market_code = normalize_market(market or settings.primary_market_code)
        current = MarketCalendar._to_kst(dt)
        if is_domestic_market(market_code):
            return _KR_HOLIDAYS.get(current.date())
        ny_dt = current.astimezone(_NY_TZ)
        return _US_HOLIDAYS.get(ny_dt.date())


market_calendar = MarketCalendar()
