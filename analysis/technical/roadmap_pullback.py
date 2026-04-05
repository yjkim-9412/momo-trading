"""20/60일선 눌림형 로드맵 판정."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from core.config import settings


@dataclass
class RoadmapPullbackSnapshot:
    """20/60일 눌림형 판정 결과."""

    qualified: bool = False
    stage: str = ""
    reason: str = ""
    current_price: float = 0.0
    sma_20: float | None = None
    sma_60: float | None = None
    sma_60_prev: float | None = None
    sma_60_slope_pct: float | None = None
    roadmap_anchor_price: float | None = None
    roadmap_invalid_price: float | None = None
    roadmap_take_profit_price: float | None = None
    recent_swing_low: float | None = None
    recent_high: float | None = None
    sma20_entry_low: float | None = None
    sma20_entry_high: float | None = None
    sma60_entry_low: float | None = None
    sma60_entry_high: float | None = None
    roadmap_second_tranche_allowed: bool = False

    def to_metadata(self) -> dict[str, float | str | bool | None]:
        """분석/주문 경로 전달용 metadata."""
        return {
            "roadmap_qualified": self.qualified,
            "roadmap_stage": self.stage,
            "roadmap_reason": self.reason,
            "roadmap_anchor_price": self.roadmap_anchor_price,
            "roadmap_invalid_price": self.roadmap_invalid_price,
            "roadmap_take_profit_price": self.roadmap_take_profit_price,
            "roadmap_second_tranche_allowed": self.roadmap_second_tranche_allowed,
            "roadmap_sma20": self.sma_20,
            "roadmap_sma60": self.sma_60,
            "roadmap_sma60_prev": self.sma_60_prev,
            "roadmap_sma60_slope_pct": self.sma_60_slope_pct,
            "roadmap_recent_swing_low": self.recent_swing_low,
            "roadmap_recent_high": self.recent_high,
            "roadmap_sma20_entry_low": self.sma20_entry_low,
            "roadmap_sma20_entry_high": self.sma20_entry_high,
            "roadmap_sma60_entry_low": self.sma60_entry_low,
            "roadmap_sma60_entry_high": self.sma60_entry_high,
        }

    def to_monitoring(self) -> dict[str, float | str | bool]:
        """실시간 감시용 payload."""
        payload: dict[str, float | str | bool] = {
            "roadmap_enabled": self.qualified,
            "roadmap_strategy_type": "ROADMAP_PULLBACK",
            "roadmap_stage": self.stage,
        }
        for key, value in {
            "roadmap_sma20_entry_low": self.sma20_entry_low,
            "roadmap_sma20_entry_high": self.sma20_entry_high,
            "roadmap_sma60_entry_low": self.sma60_entry_low,
            "roadmap_sma60_entry_high": self.sma60_entry_high,
            "roadmap_invalid_price": self.roadmap_invalid_price,
            "roadmap_take_profit_price": self.roadmap_take_profit_price,
        }.items():
            if isinstance(value, (int, float)) and float(value) > 0:
                payload[key] = round(float(value), 4)
        return payload


class RoadmapPullbackAnalyzer:
    """20/60일선 눌림형 판정기."""

    def evaluate(
        self,
        daily_df: pd.DataFrame,
        *,
        current_price: float,
    ) -> RoadmapPullbackSnapshot:
        """일봉 기준 진입 가능 여부를 평가한다."""
        snapshot = RoadmapPullbackSnapshot(current_price=float(current_price or 0.0))
        if daily_df.empty or current_price <= 0:
            snapshot.reason = "일봉 또는 현재가 부족"
            return snapshot
        if "close" not in daily_df.columns or "high" not in daily_df.columns or "low" not in daily_df.columns:
            snapshot.reason = "OHLC 일봉 컬럼 부족"
            return snapshot

        close_series = pd.to_numeric(daily_df.get("close"), errors="coerce")
        high_series = pd.to_numeric(daily_df.get("high"), errors="coerce")
        low_series = pd.to_numeric(daily_df.get("low"), errors="coerce")
        if close_series.isna().all() or len(close_series.dropna()) < 60:
            snapshot.reason = "SMA60 계산에 필요한 일봉 부족"
            return snapshot

        sma20 = close_series.rolling(20).mean()
        sma60 = close_series.rolling(60).mean()
        current_sma20 = _last_valid_value(sma20)
        current_sma60 = _last_valid_value(sma60)
        previous_sma60 = _previous_valid_value(sma60, lookback=5)
        if current_sma20 is None or current_sma60 is None or previous_sma60 is None:
            snapshot.reason = "SMA20/SMA60 계산 실패"
            return snapshot

        snapshot.sma_20 = round(current_sma20, 4)
        snapshot.sma_60 = round(current_sma60, 4)
        snapshot.sma_60_prev = round(previous_sma60, 4)
        snapshot.sma_60_slope_pct = _slope_pct(current_sma60, previous_sma60)

        thresholds = settings.roadmap_pullback_thresholds
        snapshot.sma20_entry_low = round(
            current_sma20 * (1 + thresholds["sma20_lower_pct"] / 100),
            4,
        )
        snapshot.sma20_entry_high = round(
            current_sma20 * (1 + thresholds["sma20_upper_pct"] / 100),
            4,
        )
        snapshot.sma60_entry_low = round(
            current_sma60 * (1 + thresholds["sma60_lower_pct"] / 100),
            4,
        )
        snapshot.sma60_entry_high = round(
            current_sma60 * (1 + thresholds["sma60_upper_pct"] / 100),
            4,
        )
        snapshot.recent_swing_low = _last_valid_value(low_series.tail(10))
        snapshot.recent_high = _last_valid_value(high_series.tail(20).max(skipna=True))

        if current_sma20 < current_sma60:
            snapshot.reason = "SMA20이 SMA60 아래"
            return snapshot

        if (snapshot.sma_60_slope_pct or 0.0) < thresholds["sma60_min_slope_pct"]:
            snapshot.reason = "SMA60 기울기 하락"
            return snapshot

        chase_limit = current_sma20 * (1 + thresholds["chase_max_above_sma20_pct"] / 100)
        if current_price > chase_limit:
            snapshot.reason = "SMA20 대비 추격 구간"
            return snapshot

        in_sma60_band = (
            snapshot.sma60_entry_low <= current_price <= snapshot.sma60_entry_high
            if snapshot.sma60_entry_low and snapshot.sma60_entry_high
            else False
        )
        in_sma20_band = (
            snapshot.sma20_entry_low <= current_price <= snapshot.sma20_entry_high
            if snapshot.sma20_entry_low and snapshot.sma20_entry_high
            else False
        )
        if in_sma60_band:
            snapshot.stage = "SMA60_PULLBACK"
            snapshot.roadmap_anchor_price = round(current_sma60, 4)
            snapshot.roadmap_second_tranche_allowed = True
        elif in_sma20_band:
            snapshot.stage = "SMA20_PULLBACK"
            snapshot.roadmap_anchor_price = round(current_sma20, 4)
        else:
            snapshot.reason = "20/60일 눌림 밴드 밖"
            return snapshot

        invalid_candidates = [
            candidate
            for candidate in (current_sma60, snapshot.recent_swing_low)
            if isinstance(candidate, (int, float)) and float(candidate) > 0
        ]
        if invalid_candidates:
            snapshot.roadmap_invalid_price = round(max(invalid_candidates), 4)

        take_profit_candidates = [
            candidate
            for candidate in (snapshot.recent_high, close_series.tail(20).max(skipna=True))
            if isinstance(candidate, (int, float)) and float(candidate) > current_price
        ]
        if take_profit_candidates:
            snapshot.roadmap_take_profit_price = round(max(take_profit_candidates), 4)

        snapshot.qualified = True
        snapshot.reason = f"{snapshot.stage} 진입 구간"
        return snapshot


def _last_valid_value(value) -> float | None:
    """마지막 유효 숫자를 반환한다."""
    if isinstance(value, pd.Series):
        cleaned = value.dropna()
        if cleaned.empty:
            return None
        return float(cleaned.iloc[-1])
    if value is None or pd.isna(value):
        return None
    return float(value)


def _previous_valid_value(series: pd.Series, *, lookback: int) -> float | None:
    """N일 전 유효 값을 반환한다."""
    cleaned = series.dropna()
    if len(cleaned) <= lookback:
        return None
    return float(cleaned.iloc[-(lookback + 1)])


def _slope_pct(current: float, previous: float) -> float:
    """기준선 기울기를 퍼센트로 환산한다."""
    if previous == 0:
        return 0.0
    return round(((current - previous) / previous) * 100, 4)


roadmap_pullback_analyzer = RoadmapPullbackAnalyzer()
