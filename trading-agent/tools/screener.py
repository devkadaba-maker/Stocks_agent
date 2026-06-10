"""Morning screen: NASDAQ FTP -> pass 1 -> pass 2 -> shortlist.json.

Two-pass screener:
  Pass 1 (metadata only) -- download NASDAQ FTP CSV, filter out OTC/ADRs/
        micro-cap/no-volume.  ~8 000 -> ~1 500-2 000 candidates.
  Pass 2 (momentum) -- fetch 60-day bars for survivors, apply volume/
        price/SMA/ATR filters, score by (% above 50-SMA + volume trend).
  Stratified selection -- survivors are split into dollar-volume quartiles;
        top picks from each tier ensure a mix of mega-, large-, mid-,
        and small-cap stocks in the shortlist.
"""

import csv
import io
import json
import logging
from pathlib import Path

import requests
from config import settings
from tools.indicators import compute_indicators
from tools.market_data import fetch_bars

logger = logging.getLogger(__name__)

_NASDAQ_URL = "https://ftp.nasdaqtrader.com/SymbolDirectory/nasdaqlisted.txt"
_AMEX_URL = "https://ftp.nasdaqtrader.com/SymbolDirectory/otherlisted.txt"
_NASDAQ_DATAHUB_URL = "https://datahub.io/core/nasdaq-listings/r/nasdaq-listed.csv"
_AMEX_DATAHUB_URL = "https://datahub.io/core/nyse-other-listings/r/other-listed.csv"


# -- helpers -------------------------------------------------------------------


def _download_listing(url: str, fallback_url: str | None = None) -> list[dict]:
    urls_to_try = [fallback_url, url] if fallback_url else [url]
    for attempt_url in urls_to_try:
        if attempt_url is None:
            continue
        logger.info("Downloading listing from %s", attempt_url)
        timeout = 5 if attempt_url == url and fallback_url else 15
        try:
            resp = requests.get(attempt_url, timeout=timeout)
            resp.raise_for_status()
        except requests.RequestException:
            logger.warning("Failed to fetch %s", attempt_url, exc_info=False)
            continue

        text = "\n".join(
            line
            for line in resp.text.splitlines()
            if not line.startswith("File Creation Time")
        )
        first_line = text.split("\n", 1)[0] if text else ""
        delimiter = "|" if "|" in first_line else ","
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        rows = list(reader)
        logger.info("  -> %d rows from %s", len(rows), attempt_url.split("/")[-1])
        return rows

    logger.error("All listing URLs failed")
    return []


def _is_valid_common_stock(row: dict) -> bool:
    symbol = row.get("Symbol", "")
    name = row.get("Security Name", "").upper()

    if not symbol:
        return False
    if "$" in symbol or "." in symbol:
        return False
    if row.get("ETF", "").strip().upper() == "Y":
        return False

    skip_keywords = (
        "WARRANT",
        "RIGHT",
        "UNIT",
        "PREFERRED",
        "PFD",
        "NOTE",
        "DEBENTURE",
        "SUB UNIT",
        "DEPOSITARY",
        "ADR",
        "GDR",
    )
    for kw in skip_keywords:
        if kw in name:
            return False
    return True


# -- Pass 1 --------------------------------------------------------------------


def pass1_metadata_filter() -> list[str]:
    all_rows: list[dict] = []
    for row in _download_listing(_NASDAQ_URL, fallback_url=_NASDAQ_DATAHUB_URL):
        if _is_valid_common_stock(row):
            all_rows.append({"Symbol": row["Symbol"], "Exchange": "NASDAQ"})
    for row in _download_listing(_AMEX_URL, fallback_url=_AMEX_DATAHUB_URL):
        if _is_valid_common_stock(row):
            exch = row.get("Exchange", "OTHER")
            all_rows.append({"Symbol": row["Symbol"], "Exchange": exch})

    seen = set()
    unique: list[str] = []
    for r in all_rows:
        sym = r["Symbol"]
        if sym not in seen:
            seen.add(sym)
            unique.append(sym)

    logger.info("Pass 1 survivors: %d unique tickers", len(unique))
    return unique


# -- Dollar-volume tiering -----------------------------------------------------


def _compute_dollar_volume(df) -> float:
    close = df["Close"].astype(float)
    volume = df["Volume"].astype(float)
    recent = min(20, len(close))
    avg_close = float(close.iloc[-recent:].mean())
    avg_vol = float(volume.iloc[-recent:].mean())
    return avg_close * avg_vol


def _stratified_selection(
    survivors: list[dict], target: int, n_tiers: int = 4
) -> list[dict]:
    """Split survivors into *n_tiers* dollar-volume bins and pick evenly.

    Each bin contributes floor(target / n_tiers) top-scorers.
    Remaining slots fill from the smallest-cap tier upward
    to surface rough diamonds.
    """
    if not survivors:
        return []
    if len(survivors) <= target:
        return survivors

    # Rank by dollar volume descending, then split into quartiles
    ranked = sorted(survivors, key=lambda s: s["dollar_volume"], reverse=True)
    n = len(ranked)

    bins: dict[int, list[dict]] = {}
    for i, stock in enumerate(ranked):
        quartile = min(n_tiers - 1, int(i / max(1, n // n_tiers)))
        bins.setdefault(quartile, []).append(stock)

    # Sort each bin internally by score descending
    for q in bins:
        bins[q].sort(key=lambda s: s["score"], reverse=True)

    per_tier = target // n_tiers
    selected: list[dict] = []

    # Round 1: grab per_tier best scorers from each tier
    for q in range(n_tiers):
        selected.extend(bins[q][:per_tier])

    # Round 2: fill remainder from smallest-cap tiers first
    for q in range(n_tiers - 1, -1, -1):
        if len(selected) >= target:
            break
        extras = [s for s in bins[q] if s not in selected]
        if extras:
            selected.append(extras[0])

    return selected


# -- Pass 2 --------------------------------------------------------------------


def pass2_momentum_screen(
    candidates: list[str],
    exclude: set[str] | None = None,
) -> list[dict]:
    """Fetch bars for *candidates*, apply momentum filters, return stratified shortlist.

    Filters applied:
      - Price between MIN_PRICE and MAX_PRICE
      - Price > 50-day SMA
      - ATR 1-5 % of price
      - Avg daily volume (20-day) > MIN_AVG_VOLUME
    Scoring:
      score = pct_above_sma_50 + vol_ratio * 10
    Selection:
      Survivors are split into 4 tiers by dollar volume, best from each tier
      picked to ensure a diverse mix.

    Args:
        candidates: ticker symbols from Pass 1.
        exclude: tickers to skip (e.g. already-held stocks).
    """
    if exclude is None:
        exclude = set()
    candidates = [c for c in candidates if c not in exclude]
    logger.info(
        "Pass 2: screening %d candidates (excluded %d)",
        len(candidates),
        len(exclude),
    )

    bars = fetch_bars(candidates, period=f"{settings.SCREEN_DAYS}d", interval="1d")
    logger.info("Pass 2: got bars for %d / %d", len(bars), len(candidates))

    survivors: list[dict] = []

    for ticker, df in bars.items():
        try:
            signals = compute_indicators(df)
            if "error" in signals:
                continue

            price = signals["price"]

            if price < settings.MIN_PRICE or price > settings.MAX_PRICE:
                continue
            if not signals["price_above_sma_50"]:
                continue

            atr_pct = signals["atr_pct"]
            if atr_pct < 1.0 or atr_pct > 5.0:
                continue

            vol_sma_20 = signals["vol_sma_20"]
            if vol_sma_20 < settings.MIN_AVG_VOLUME:
                continue

            vol_ratio = signals["vol_ratio"]
            pct_above = signals["pct_above_sma_50"]
            score = pct_above + vol_ratio * 10
            dollar_volume = _compute_dollar_volume(df)

            survivors.append(
                {
                    "ticker": ticker,
                    "price": price,
                    "pct_above_sma_50": pct_above,
                    "vol_ratio": vol_ratio,
                    "atr_pct": atr_pct,
                    "rsi": signals["rsi"],
                    "adx": signals["adx"],
                    "score": round(score, 2),
                    "dollar_volume": round(dollar_volume, 0),
                }
            )
        except Exception:
            logger.warning("Pass 2 error for %s", ticker, exc_info=True)
            continue

    target = settings.SHORTLIST_SIZE
    # 3 tiers: TOP (mega+large merged), MID, SMALL -- tilts toward smaller caps
    top = _stratified_selection(survivors, target, n_tiers=3)

    logger.info(
        "Pass 2 complete: %d survived, stratified down to %d (wanted %d, 3 tiers)",
        len(survivors),
        len(top),
        target,
    )

    return top


# -- main entry point ----------------------------------------------------------


def run_morning_screen(exclude: set[str] | None = None) -> list[dict]:
    """Run the full screening pipeline and write shortlist.json.

    Args:
        exclude: tickers to skip in Pass 2 (e.g. already-held stocks).

    Returns:
        The shortlist (list of dicts), also persisted to disk.
    """
    logger.info("=== Morning screen starting ===")

    candidates = pass1_metadata_filter()
    if not candidates:
        logger.error("Pass 1 returned 0 candidates -- aborting")
        return []

    shortlist = pass2_momentum_screen(candidates, exclude=exclude)
    if not shortlist:
        logger.warning("Pass 2 returned 0 survivors")
        return []

    out_path: Path = settings.SHORTLIST_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(shortlist, f, indent=2)
    logger.info("Shortlist written to %s (%d stocks)", out_path, len(shortlist))

    # Log with tier labels for visibility (3 tiers: TOP, MID, SMALL)
    sorted_by_dv = sorted(shortlist, key=lambda s: s["dollar_volume"], reverse=True)
    n = len(sorted_by_dv)
    n_tiers = 3
    per_tier = max(1, n // n_tiers)
    for i, s in enumerate(sorted_by_dv, 1):
        tier_idx = min(n_tiers - 1, int((i - 1) / per_tier))
        label = ["TOP", "MID", "SMALL"][tier_idx]
        logger.info(
            "  %2d. [%s] %-6s  $%-8.2f  dv=$%-12s  score=%-6.2f  RSI=%-5.1f",
            i,
            label,
            s["ticker"],
            s["price"],
            f"{s['dollar_volume']:,.0f}",
            s["score"],
            s["rsi"],
        )

    logger.info("=== Morning screen complete ===")
    return shortlist
