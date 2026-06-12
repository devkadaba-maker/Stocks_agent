"""yfinance bars, parallel batch fetch."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


def fetch_bars(
    tickers: list[str], period: str = "8mo", interval: str = "1d"
) -> dict[str, pd.DataFrame]:
    """Fetch historical OHLCV bars for a list of tickers in parallel.

    Each ticker gets its own yfinance download via a thread pool.
    Tickers that return no data (delisted, invalid symbol, etc.)
    are silently skipped — they never crash the pipeline.

    Returns:
        dict mapping ticker -> pandas DataFrame with columns:
        Open, High, Low, Close, Volume
    """
    COLUMN_NAMES = ["Open", "High", "Low", "Close", "Volume"]
    results: dict[str, pd.DataFrame] = {}

    def _fetch_one(ticker: str) -> tuple[str, pd.DataFrame] | None:
        try:
            df = yf.download(ticker, period=period, interval=interval, progress=False)
            if df is not None and not df.empty:
                # yfinance v1.4+ names columns after the ticker symbol (e.g.
                # all 5 columns are "AAPL"). Replace by standard position-based names.
                df.columns = COLUMN_NAMES[: len(df.columns)]
                # The most recent bar can be a NaN-OHLC placeholder for the
                # still-in-progress session — drop incomplete trailing rows.
                df = df.dropna(subset=["Open", "High", "Low", "Close"])
                if df.empty:
                    logger.debug("No valid bars for %s after dropna", ticker)
                    return None
                return ticker, df
            else:
                logger.debug("No data for %s (empty DataFrame)", ticker)
                return None
        except Exception:
            logger.warning("Failed to fetch bars for %s", ticker, exc_info=True)
            return None

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_fetch_one, t): t for t in tickers}
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                ticker, df = result
                results[ticker] = df

    logger.info("Fetched bars for %d/%d tickers", len(results), len(tickers))
    return results


def fetch_current_price(ticker: str) -> float:
    """Fetch the latest price for a single ticker.

    Uses yfinance's fast_info to avoid downloading full history.
    Returns 0.0 if the price cannot be determined.
    """
    try:
        t = yf.Ticker(ticker)
        info = t.fast_info
        price = getattr(info, "last_price", None)
        if price is not None:
            return float(price)
        # fallback: use the most recent close from short history
        df = yf.download(ticker, period="5d", interval="1d", progress=False)
        if df is not None and not df.empty:
            df.columns = ["Open", "High", "Low", "Close", "Volume"][: len(df.columns)]
            return float(df["Close"].iloc[-1])
        logger.warning("Could not determine price for %s", ticker)
        return 0.0
    except Exception:
        logger.warning("Failed to fetch current price for %s", ticker, exc_info=True)
        return 0.0
