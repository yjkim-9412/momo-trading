"""공유 타입: MarketState, 상수, 전략 팩토리"""
import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime

from strategy.aggressive_short import AggressiveShortStrategy
from strategy.stable_short import StableShortStrategy

_DATA_CONSISTENCY_MAX_GAP_PCT = 0.25
_TIER2_PRICE_KRW_HINT_RATIO = 10.0
_TIER2_PRICE_CONVERSION_MAX_GAP_PCT = 0.25
_ENTRY_MODE_NEW = "NEW"
_ENTRY_MODE_PYRAMID = "ADD_ON_PYRAMID"
_ENTRY_MODE_AVERAGE_DOWN = "ADD_ON_AVERAGE_DOWN"
_ENTRY_MODE_HOLD = "HOLD"
_ENTRY_MODES = {
    _ENTRY_MODE_NEW,
    _ENTRY_MODE_PYRAMID,
    _ENTRY_MODE_AVERAGE_DOWN,
    _ENTRY_MODE_HOLD,
}
_AVERAGE_DOWN_DAILY_LIMIT = 1


def _default_strategies() -> dict[str, object]:
    return {
        "STABLE_SHORT": StableShortStrategy(),
        "AGGRESSIVE_SHORT": AggressiveShortStrategy(),
    }


@dataclass
class MarketState:
    """시장별 격리 상태 (KRX/US 동시 운영 지원)"""
    scope: str = "KRX"
    market_context: str = ""
    market_regime: str = ""
    trading_context: str = ""
    daily_start_balance: float = 0.0
    available_cash: float = 0.0
    trading_date: date | None = None
    session_ids: dict[str, str | None] = field(default_factory=dict)
    active_trading_rules: dict = field(default_factory=dict)
    rr_floor_overrides: dict[str, float] = field(default_factory=dict)
    cycle_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    after_hours_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    settlement_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    cash_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    strategies: dict[str, object] = field(default_factory=_default_strategies)
    last_completed_review_date: date | None = None
    last_schedule_hint: dict = field(default_factory=dict)
    last_selected_watchlist: list[dict[str, object]] = field(default_factory=list)
    _pipeline_snapshot: dict = field(default_factory=dict)
    last_cycle_attempt_at: datetime | None = None
    last_cycle_status: str | None = None
    last_cycle_error: str | None = None
