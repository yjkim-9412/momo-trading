"""Small runtime-compatible subset of the ``pandas_ta`` API used by this app."""

from __future__ import annotations

import numpy as np
import pandas as pd


def _to_series(values: pd.Series | list[float] | tuple[float, ...]) -> pd.Series:
    if isinstance(values, pd.Series):
        return values.astype(float)
    return pd.Series(values, dtype=float)


def sma(close: pd.Series, length: int = 30) -> pd.Series:
    close = _to_series(close)
    return close.rolling(length, min_periods=length).mean()


def ema(close: pd.Series, length: int = 10) -> pd.Series:
    close = _to_series(close)
    return close.ewm(span=length, adjust=False, min_periods=length).mean()


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    close = _to_series(close)
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = gain.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()

    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    result = 100.0 - (100.0 / (1.0 + rs))

    result = result.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    result = result.mask((avg_gain == 0) & (avg_loss > 0), 0.0)
    result = result.mask((avg_gain == 0) & (avg_loss == 0), 50.0)
    return result


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    close = _to_series(close)
    ema_fast = ema(close, length=fast)
    ema_slow = ema(close, length=slow)
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    histogram = macd_line - signal_line

    return pd.DataFrame(
        {
            f"MACD_{fast}_{slow}_{signal}": macd_line,
            f"MACDs_{fast}_{slow}_{signal}": signal_line,
            f"MACDh_{fast}_{slow}_{signal}": histogram,
        }
    )


def bbands(close: pd.Series, length: int = 20, std: float = 2.0) -> pd.DataFrame:
    close = _to_series(close)
    middle = sma(close, length=length)
    deviation = close.rolling(length, min_periods=length).std(ddof=0)
    upper = middle + (deviation * std)
    lower = middle - (deviation * std)

    return pd.DataFrame(
        {
            f"BBU_{length}_{std}": upper,
            f"BBM_{length}_{std}": middle,
            f"BBL_{length}_{std}": lower,
        }
    )


def stoch(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    k: int = 14,
    d: int = 3,
    smooth_k: int = 3,
) -> pd.DataFrame:
    high = _to_series(high)
    low = _to_series(low)
    close = _to_series(close)

    lowest_low = low.rolling(k, min_periods=k).min()
    highest_high = high.rolling(k, min_periods=k).max()
    price_range = (highest_high - lowest_low).replace(0.0, np.nan)

    raw_k = 100.0 * (close - lowest_low) / price_range
    smooth_k_series = raw_k.rolling(smooth_k, min_periods=smooth_k).mean()
    d_series = smooth_k_series.rolling(d, min_periods=d).mean()

    return pd.DataFrame(
        {
            f"STOCHk_{k}_{d}_{smooth_k}": smooth_k_series,
            f"STOCHd_{k}_{d}_{smooth_k}": d_series,
        }
    )


def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    high = _to_series(high)
    low = _to_series(low)
    close = _to_series(close)

    prev_close = close.shift(1)
    true_range = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()


def ichimoku(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    tenkan: int = 9,
    kijun: int = 26,
    senkou: int = 52,
    offset: int = 26,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    high = _to_series(high)
    low = _to_series(low)
    close = _to_series(close)

    tenkan_sen = (high.rolling(tenkan, min_periods=tenkan).max() + low.rolling(tenkan, min_periods=tenkan).min()) / 2
    kijun_sen = (high.rolling(kijun, min_periods=kijun).max() + low.rolling(kijun, min_periods=kijun).min()) / 2
    senkou_span_a = (tenkan_sen + kijun_sen) / 2
    senkou_span_b = (high.rolling(senkou, min_periods=senkou).max() + low.rolling(senkou, min_periods=senkou).min()) / 2
    chikou_span = close.shift(-offset)

    visible = pd.DataFrame(
        {
            "tenkan_sen": tenkan_sen,
            "kijun_sen": kijun_sen,
            "senkou_span_a": senkou_span_a,
            "senkou_span_b": senkou_span_b,
            "chikou_span": chikou_span,
        }
    )
    return visible, pd.DataFrame()


def willr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    high = _to_series(high)
    low = _to_series(low)
    close = _to_series(close)

    highest_high = high.rolling(length, min_periods=length).max()
    lowest_low = low.rolling(length, min_periods=length).min()
    price_range = (highest_high - lowest_low).replace(0.0, np.nan)
    return -100.0 * (highest_high - close) / price_range


def cci(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 20) -> pd.Series:
    high = _to_series(high)
    low = _to_series(low)
    close = _to_series(close)

    typical_price = (high + low + close) / 3.0
    mean_price = typical_price.rolling(length, min_periods=length).mean()
    mean_deviation = typical_price.rolling(length, min_periods=length).apply(
        lambda values: np.mean(np.abs(values - np.mean(values))),
        raw=True,
    )

    denominator = (0.015 * mean_deviation).replace(0.0, np.nan)
    return (typical_price - mean_price) / denominator


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    close = _to_series(close)
    volume = _to_series(volume)

    direction = np.sign(close.diff().fillna(0.0))
    return (direction * volume).cumsum()


def adx(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.DataFrame:
    high = _to_series(high)
    low = _to_series(low)
    close = _to_series(close)

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = up_move.where((up_move > down_move) & (up_move > 0.0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0.0), 0.0)

    prev_close = close.shift(1)
    true_range = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr_series = true_range.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()

    plus_di = 100.0 * plus_dm.ewm(alpha=1 / length, adjust=False, min_periods=length).mean() / atr_series.replace(0.0, np.nan)
    minus_di = 100.0 * minus_dm.ewm(alpha=1 / length, adjust=False, min_periods=length).mean() / atr_series.replace(0.0, np.nan)

    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    adx_series = dx.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()

    return pd.DataFrame(
        {
            f"ADX_{length}": adx_series,
            f"DMP_{length}": plus_di,
            f"DMN_{length}": minus_di,
        }
    )


def mfi(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    length: int = 14,
) -> pd.Series:
    high = _to_series(high)
    low = _to_series(low)
    close = _to_series(close)
    volume = _to_series(volume)

    typical_price = (high + low + close) / 3.0
    raw_money_flow = typical_price * volume
    price_change = typical_price.diff()

    positive_flow = raw_money_flow.where(price_change > 0.0, 0.0)
    negative_flow = raw_money_flow.where(price_change < 0.0, 0.0).abs()

    positive_sum = positive_flow.rolling(length, min_periods=length).sum()
    negative_sum = negative_flow.rolling(length, min_periods=length).sum()

    money_ratio = positive_sum / negative_sum.replace(0.0, np.nan)
    result = 100.0 - (100.0 / (1.0 + money_ratio))
    result = result.mask((negative_sum == 0) & (positive_sum > 0), 100.0)
    result = result.mask((positive_sum == 0) & (negative_sum > 0), 0.0)
    result = result.mask((positive_sum == 0) & (negative_sum == 0), 50.0)
    return result
