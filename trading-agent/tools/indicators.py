"""pandas-based technical indicators — returns flat signal dict per stock."""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _ema(series: pd.Series, length: int) -> pd.Series:
    """Exponential moving average."""
    result = series.ewm(span=length, adjust=False).mean()
    return result if isinstance(result, pd.Series) else result.squeeze()  # type: ignore[return-value]


def _sma(series: pd.Series, length: int) -> pd.Series:
    """Simple moving average."""
    result = series.rolling(window=length).mean()
    return result if isinstance(result, pd.Series) else result.squeeze()  # type: ignore[return-value]


def _rsi(series: pd.Series, length: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = _ema(gain, length)
    avg_loss = _ema(loss, length)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = pd.Series(100 - (100 / (1 + rs)), index=series.index, dtype=float)
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
    plus_di: pd.Series = 100 * _ema(plus_dm, length) / atr.replace(0, np.nan)
    minus_di: pd.Series = 100 * _ema(minus_dm, length) / atr.replace(0, np.nan)
    dx: pd.Series = 100 * (
        (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    )
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
    """Compute technical indicators filtered to find high-volatility companies
    resting in a low-momentum baseline state before an explosive trend occurs.
    """
    close: pd.Series = df["Close"].astype(float)  # type: ignore[assignment]
    high: pd.Series = df["High"].astype(float)  # type: ignore[assignment]
    low: pd.Series = df["Low"].astype(float)  # type: ignore[assignment]
    volume: pd.Series = df["Volume"].astype(float)  # type: ignore[assignment]

    if len(close) < 45:
        logger.warning(
            "Not enough data for indicators (need >= 45 bars, got %d)", len(close)
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

    # Volume and Trend metrics
    vol_sma_20 = _sma(volume, 20)

    latest_idx = -1
    prev_idx = (
        -5
    )  # Lookback window (approx 1 trading week ago) to calculate trend direction

    effective_price = (
        live_price if live_price is not None else float(close.iloc[latest_idx])
    )

    # --- BEHAVIORAL CALCULATIONS FOR SMALL CAP PRE-BOOM DISCOVERY ---

    # 1. Daily Dollar Volume (Filters out mega-cap footprints cleanly)
    avg_daily_volume = float(vol_sma_20.iloc[latest_idx])
    daily_dollar_volume = effective_price * avg_daily_volume

    # 2. ATR Percentage (Identifies assets with explosive capacity)
    atr_val = float(atr_series.iloc[latest_idx])
    atr_pct = (atr_val / float(close.iloc[latest_idx])) * 100

    # 3. Tethers to core baseline structure
    sma_50_val = float(sma_50.iloc[latest_idx])
    pct_above_sma_50 = ((effective_price / sma_50_val) - 1) * 100

    # 4. Momentum tracking parameters
    rsi_val = float(rsi.iloc[latest_idx])
    adx_val = float(adx_series.iloc[latest_idx])
    adx_prev_val = float(adx_series.iloc[prev_idx])
    adx_is_turning_up = adx_val > adx_prev_val

    # 6. Volume ratio — guard against a zero/NaN 20-day average (thinly traded
    #    or freshly listed tickers) to avoid divide-by-zero RuntimeWarnings.
    latest_volume = float(volume.iloc[latest_idx])
    vol_sma_20_val = float(vol_sma_20.iloc[latest_idx])
    if not np.isfinite(vol_sma_20_val) or vol_sma_20_val <= 0:
        vol_sma_20_val = 0.0
        vol_ratio = 0.0
    else:
        vol_ratio = latest_volume / vol_sma_20_val

    # 5. Pre-Boom Setup Logic Flag
    # Must be smaller asset ($1M-$25M daily flow), High capacity (ATR > 4%),
    # resting baseline near 50 SMA, completely cooled down neutral momentum.
    is_pre_boom_setup = bool(
        (1_000_000 <= daily_dollar_volume <= 25_000_000)
        and (atr_pct >= 4.0)
        and (0.0 <= pct_above_sma_50 <= 4.5)
        and (45.0 <= rsi_val <= 55.0)
        and (15.0 <= adx_val <= 23.0)
    )

    return {
        "price": round(effective_price, 2),
        "daily_dollar_volume": round(daily_dollar_volume, 2),
        "is_pre_boom_setup": is_pre_boom_setup,
        # Moving averages & baselines
        "price_above_sma_20": bool(effective_price > sma_20.iloc[latest_idx]),
        "price_above_sma_50": bool(effective_price > sma_50.iloc[latest_idx]),
        "pct_above_sma_50": round(float(pct_above_sma_50), 2),
        "sma_20": round(float(sma_20.iloc[latest_idx]), 2),
        "sma_50": round(sma_50_val, 2),
        "ema_9": round(float(ema_9.iloc[latest_idx]), 2),
        "ema_21": round(float(ema_21.iloc[latest_idx]), 2),
        # Cooled-down Momentum Variables
        "rsi": round(rsi_val, 2),
        "rsi_in_launch_pad_zone": bool(45.0 <= rsi_val <= 55.0),
        # MACD Baseline tracking
        "macd": round(float(macd_df["macd"].iloc[latest_idx]), 4),
        "macd_signal": round(float(macd_df["signal"].iloc[latest_idx]), 4),
        "macd_histogram": round(float(macd_df["histogram"].iloc[latest_idx]), 4),
        "macd_bullish": bool(
            macd_df["macd"].iloc[latest_idx] > macd_df["signal"].iloc[latest_idx]
        ),
        # Volatility Capacity
        "atr": round(atr_val, 4),
        "atr_pct": round(float(atr_pct), 2),
        # Bollinger Bands Squeeze
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
        # Trend Birth Tracking (ADX)
        "adx": round(adx_val, 2),
        "adx_is_turning_up": adx_is_turning_up,
        "adx_low_trend_consolidation": bool(15.0 <= adx_val <= 23.0),
        # Volume
        "volume": int(latest_volume),
        "vol_sma_20": int(vol_sma_20_val),
        "vol_ratio": round(vol_ratio, 2),
    }
