"""pandas-based technical indicators — returns flat signal dict per stock."""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _ema(series: pd.Series, length: int) -> pd.Series:
    """Exponential moving average."""
    return series.ewm(span=length, adjust=False).mean()


def _sma(series: pd.Series, length: int) -> pd.Series:
    """Simple moving average."""
    return series.rolling(window=length).mean()


def _rsi(series: pd.Series, length: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = _ema(gain, length)
    avg_loss = _ema(loss, length)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def _macd(series: pd.Series) -> pd.DataFrame:
    """MACD line, signal line, and histogram."""
    ema12 = _ema(series, 12)
    ema26 = _ema(series, 26)
    macd_line = ema12 - ema26
    signal = _ema(macd_line, 9)
    histogram = macd_line - signal
    return pd.DataFrame({"macd": macd_line, "signal": signal, "histogram": histogram})


def _atr(
    high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14
) -> pd.Series:
    """Average True Range."""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return _ema(true_range, length)


def _bollinger_bands(
    series: pd.Series, length: int = 20, std_dev: float = 2.0
) -> pd.DataFrame:
    """Bollinger Bands."""
    middle = _sma(series, length)
    std = series.rolling(window=length).std(ddof=0)
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return pd.DataFrame({"upper": upper, "middle": middle, "lower": lower})


def _adx(
    high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14
) -> pd.Series:
    """Average Directional Index (always >= 0)."""
    high_diff = high.diff()
    low_diff = low.diff()
    # +DM: current high minus previous high
    plus_dm = high_diff.where((high_diff > 0) & (high_diff > -low_diff), 0.0)
    # -DM: previous low minus current low
    minus_dm = (-low_diff).where((-low_diff > 0) & (-low_diff > high_diff), 0.0)
    # True Range
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = _ema(tr, length)
    plus_di = 100 * _ema(plus_dm, length) / atr.replace(0, np.nan)
    minus_di = 100 * _ema(minus_dm, length) / atr.replace(0, np.nan)
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx = _ema(dx, length)
    return adx


def _stochastic(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    k_length: int = 14,
    d_length: int = 3,
) -> pd.DataFrame:
    """Stochastic Oscillator %K and %D."""
    lowest_low = low.rolling(window=k_length).min()
    highest_high = high.rolling(window=k_length).max()
    k = 100 * ((close - lowest_low) / (highest_high - lowest_low).replace(0, np.nan))
    d = _sma(k, d_length)
    return pd.DataFrame({"k": k, "d": d})


def compute_indicators(df: pd.DataFrame, live_price: float | None = None) -> dict:
    """Compute all technical indicators and return a flat signal dict.

    Expects a DataFrame with columns: Open, High, Low, Close, Volume.
    The last daily close is used for moving averages / oscillators, but
    if *live_price* is provided it will appear as ``price`` in the output
    dict (so decision-making sees the real-time price, not yesterday's).

    Returns a dict with the latest values of each indicator.
    """
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    volume = df["Volume"].astype(float)

    if len(close) < 50:
        logger.warning(
            "Not enough data for indicators (need >= 50 bars, got %d)", len(close)
        )
        return {"error": "insufficient_data", "bars": len(close)}

    sma_20 = _sma(close, 20)
    sma_50 = _sma(close, 50)
    ema_9 = _ema(close, 9)
    ema_21 = _ema(close, 21)
    rsi = _rsi(close, 14)
    macd_df = _macd(close)
    atr_series = _atr(high, low, close, 14)
    bb = _bollinger_bands(close, 20)
    adx_series = _adx(high, low, close, 14)
    stoch_df = _stochastic(high, low, close)

    # Volume trend: compare recent avg volume to longer-term avg
    vol_sma_20 = _sma(volume, 20)
    vol_sma_50 = _sma(volume, 50)

    latest_idx = -1

    # Use live price if provided, otherwise fall back to last daily close
    effective_price = (
        live_price if live_price is not None else float(close.iloc[latest_idx])
    )

    return {
        # Price (live if available, daily close fallback)
        "price": round(effective_price, 2),
        "price_above_sma_20": bool(effective_price > sma_20.iloc[latest_idx]),
        "price_above_sma_50": bool(effective_price > sma_50.iloc[latest_idx]),
        "pct_above_sma_50": round(
            float((effective_price / sma_50.iloc[latest_idx] - 1) * 100), 2
        ),
        # Moving averages
        "sma_20": round(float(sma_20.iloc[latest_idx]), 2),
        "sma_50": round(float(sma_50.iloc[latest_idx]), 2),
        "ema_9": round(float(ema_9.iloc[latest_idx]), 2),
        "ema_21": round(float(ema_21.iloc[latest_idx]), 2),
        "ema_9_above_ema_21": bool(ema_9.iloc[latest_idx] > ema_21.iloc[latest_idx]),
        # Momentum
        "rsi": round(float(rsi.iloc[latest_idx]), 2),
        "rsi_overbought": bool(rsi.iloc[latest_idx] > 70),
        "rsi_oversold": bool(rsi.iloc[latest_idx] < 30),
        # MACD
        "macd": round(float(macd_df["macd"].iloc[latest_idx]), 4),
        "macd_signal": round(float(macd_df["signal"].iloc[latest_idx]), 4),
        "macd_histogram": round(float(macd_df["histogram"].iloc[latest_idx]), 4),
        "macd_bullish": bool(
            macd_df["macd"].iloc[latest_idx] > macd_df["signal"].iloc[latest_idx]
        ),
        # Volatility
        "atr": round(float(atr_series.iloc[latest_idx]), 4),
        "atr_pct": round(
            float(atr_series.iloc[latest_idx] / close.iloc[latest_idx] * 100), 2
        ),
        # Bollinger
        "bb_upper": round(float(bb["upper"].iloc[latest_idx]), 2),
        "bb_lower": round(float(bb["lower"].iloc[latest_idx]), 2),
        "bb_middle": round(float(bb["middle"].iloc[latest_idx]), 2),
        "bb_width": round(
            float(
                (bb["upper"].iloc[latest_idx] - bb["lower"].iloc[latest_idx])
                / bb["middle"].iloc[latest_idx]
                * 100
            ),
            2,
        ),
        "bb_above_upper": bool(close.iloc[latest_idx] > bb["upper"].iloc[latest_idx]),
        "bb_below_lower": bool(close.iloc[latest_idx] < bb["lower"].iloc[latest_idx]),
        # Trend strength
        "adx": round(float(adx_series.iloc[latest_idx]), 2),
        "adx_strong_trend": bool(adx_series.iloc[latest_idx] > 25),
        # Stochastic
        "stoch_k": round(float(stoch_df["k"].iloc[latest_idx]), 2),
        "stoch_d": round(float(stoch_df["d"].iloc[latest_idx]), 2),
        # Volume
        "volume": int(volume.iloc[latest_idx]),
        "vol_sma_20": int(vol_sma_20.iloc[latest_idx]),
        "vol_ratio": round(
            float(volume.iloc[latest_idx] / vol_sma_20.iloc[latest_idx]), 2
        ),
    }
