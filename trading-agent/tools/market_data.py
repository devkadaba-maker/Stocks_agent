"""Market data fetching with multi-source fallback.

Primary:  yfinance batched downloads (with curl_cffi session if available,
          which bypasses Yahoo's bot detection / "Invalid Crumb" errors).
Fallback: Stooq free CSV endpoint (no API key, decades of daily history).

The public interface is unchanged:
    fetch_bars(tickers, period, interval) -> dict[ticker, DataFrame]
    fetch_current_price(ticker) -> float
"""

import io
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests as _requests
import yfinance as yf

from config import settings

logger = logging.getLogger(__name__)

COLUMN_NAMES = ["Open", "High", "Low", "Close", "Volume"]
BATCH_SIZE = 100  # tickers per yfinance batch call
BATCH_PAUSE_SEC = 0.3  # brief pause between batches
STOOQ_WORKERS = 8  # parallel Stooq fallback fetches
CACHE_TTL_SECONDS = 23 * 60 * 60  # 23 hours — daily bars change once per day; safe to reuse all day

# ---------------------------------------------------------------------------
# Optional curl_cffi session — strongly recommended. Yahoo blocks plain
# requests-based clients with 401 "Invalid Crumb"; curl_cffi impersonates a
# real Chrome TLS fingerprint which restores access.
# ---------------------------------------------------------------------------
_session = None
try:
    from curl_cffi import requests as _curl_requests

    _session = _curl_requests.Session(impersonate="chrome")
    logger.info("curl_cffi available — using Chrome-impersonated session for yfinance")
except ImportError:
    logger.warning(
        "curl_cffi not installed — yfinance may hit 'Invalid Crumb' errors. "
        "Run: pip install curl_cffi"
    )


def _period_to_days(period: str) -> int:
    """Convert a yfinance period string like '3mo'/'60d'/'1y' to days."""
    period = period.strip().lower()
    try:
        if period.endswith("mo"):
            return int(period[:-2]) * 31
        if period.endswith("d"):
            return int(period[:-1])
        if period.endswith("y"):
            return int(period[:-1]) * 366
    except ValueError:
        pass
    return 92  # default ~3 months


def _normalise_single(df: pd.DataFrame) -> pd.DataFrame | None:
    """Normalise a single-ticker frame to standard OHLCV columns."""
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    # Keep only the standard columns we know, in order, dropping extras (Adj Close etc.)
    cols = [c for c in COLUMN_NAMES if c in df.columns]
    if len(cols) < 4:
        # Possibly ticker-named columns (yfinance >= 1.4) — rename by position
        df = df.copy()
        df.columns = COLUMN_NAMES[: len(df.columns)]
        cols = [c for c in COLUMN_NAMES if c in df.columns]
    df = df[cols].dropna(subset=["Open", "High", "Low", "Close"])
    return df if not df.empty else None


def _fetch_batch_yfinance(
    batch: list[str], period: str, interval: str
) -> dict[str, pd.DataFrame]:
    """Fetch one batch of tickers in a single yfinance call."""
    out: dict[str, pd.DataFrame] = {}
    try:
        kwargs = dict(
            period=period,
            interval=interval,
            progress=False,
            group_by="ticker",
            threads=False,
        )
        if _session is not None:
            kwargs["session"] = _session
        data = yf.download(batch, **kwargs)
        if data is None or data.empty:
            return out

        if len(batch) == 1:
            df = _normalise_single(data)
            if df is not None:
                out[batch[0]] = df
            return out

        # Multi-ticker frame: columns are (ticker, field)
        for ticker in batch:
            try:
                if ticker not in data.columns.get_level_values(0):
                    continue
                df = _normalise_single(data[ticker].copy())
                if df is not None:
                    out[ticker] = df
            except Exception:
                continue
    except Exception as exc:
        logger.warning("yfinance batch failed (%d tickers): %s", len(batch), exc)
    return out


def _fetch_one_stooq(ticker: str, days: int) -> tuple[str, pd.DataFrame] | None:
    """Fetch daily bars for one ticker from Stooq (free, no key).

    Stooq uses lowercase ticker + '.us' suffix for US equities and
    returns a CSV: Date,Open,High,Low,Close,Volume.
    """
    symbol = ticker.lower().replace("-", "").replace(".", "") + ".us"
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    try:
        resp = _requests.get(url, timeout=15)
        if resp.status_code != 200 or not resp.text or resp.text.startswith("No data"):
            return None
        df = pd.read_csv(io.StringIO(resp.text))
        if df.empty or "Close" not in df.columns:
            return None
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date").sort_index()
        df = df.tail(days)
        df = df[[c for c in COLUMN_NAMES if c in df.columns]]
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        if df.empty:
            return None
        return ticker, df
    except Exception:
        return None


def fetch_bars(
    tickers: list[str], period: str = "3mo", interval: str = "1d"
) -> dict[str, pd.DataFrame]:
    """Fetch historical OHLCV bars for tickers, with automatic fallback and caching.

    Uses a pickle cache keyed on the sorted ticker list + period, so repeated
    calls (e.g. morning screen then trading cycle) reuse downloaded data.
    """
    tickers = list(dict.fromkeys(tickers))  # dedupe, keep order

    # ---- Check pickle cache ----
    cache_key = "_".join(sorted(tickers[:5])) + f"_{len(tickers)}tickers_{period}"
    import hashlib

    cache_path = (
        settings.DATA_DIR
        / f"fetch_cache_{hashlib.md5(cache_key.encode()).hexdigest()[:12]}.pkl"
    )
    if cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        if age > CACHE_TTL_SECONDS:
            logger.info(
                "Fetch cache expired (%.0f min old, TTL %d min) — refreshing",
                age / 60,
                CACHE_TTL_SECONDS / 60,
            )
            cache_path.unlink(missing_ok=True)
        else:
            try:
                cached = pd.read_pickle(cache_path)
                if isinstance(cached, dict):
                    logger.info(
                        "Loaded %d tickers from fetch cache (%s, %.0f min old)",
                        len(cached),
                        cache_path.name,
                        age / 60,
                    )
                    return cached
            except Exception:
                pass

    results: dict[str, pd.DataFrame] = {}

    # --- Primary: batched yfinance ----
    total_batches = (len(tickers) + BATCH_SIZE - 1) // BATCH_SIZE
    for i in range(0, len(tickers), BATCH_SIZE):
        batch_num = i // BATCH_SIZE + 1
        batch = tickers[i : i + BATCH_SIZE]
        results.update(_fetch_batch_yfinance(batch, period, interval))
        logger.info(
            "yfinance batch %d/%d done — %d/%d tickers fetched so far",
            batch_num,
            total_batches,
            len(results),
            len(tickers),
        )
        if i + BATCH_SIZE < len(tickers):
            time.sleep(BATCH_PAUSE_SEC)

    missing = [t for t in tickers if t not in results]
    yf_count = len(results)

    # --- Fallback: Stooq (daily bars only) ----
    if missing and interval == "1d":
        days = _period_to_days(period)
        logger.info(
            "yfinance returned %d/%d — trying Stooq fallback for %d tickers",
            yf_count,
            len(tickers),
            len(missing),
        )
        with ThreadPoolExecutor(max_workers=STOOQ_WORKERS) as pool:
            futures = {pool.submit(_fetch_one_stooq, t, days): t for t in missing}
            for future in as_completed(futures):
                result = future.result()
                if result is not None:
                    ticker, df = result
                    results[ticker] = df

    # ---- Save cache ----
    try:
        settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
        pd.to_pickle(results, cache_path)
        logger.info("Saved fetch cache to %s", cache_path.name)
    except Exception:
        pass

    logger.info(
        "Fetched bars for %d/%d tickers (%d yfinance, %d stooq)",
        len(results),
        len(tickers),
        yf_count,
        len(results) - yf_count,
    )
    return results


def fetch_current_price(ticker: str) -> float:
    """Fetch the latest price for a single ticker, with Stooq fallback."""
    # Try yfinance fast_info first
    try:
        t = yf.Ticker(ticker, session=_session) if _session else yf.Ticker(ticker)
        price = getattr(t.fast_info, "last_price", None)
        if price:
            return float(price)
    except Exception:
        pass
    # Fall back to most recent daily close (yfinance batch then Stooq)
    bars = fetch_bars([ticker], period="5d", interval="1d")
    if ticker in bars and not bars[ticker].empty:
        return float(bars[ticker]["Close"].iloc[-1])
    logger.warning("Could not determine price for %s", ticker)
    return 0.0
