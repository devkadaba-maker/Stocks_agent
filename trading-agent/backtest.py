#!/usr/bin/env python3
"""Walk-forward backtester — replays the strategy on historical yfinance data.

Flow:
  1. Runs the screener's Pass 1 to get the NASDAQ/AMEX ticker universe.
  2. Downloads 8 years of daily data for all candidates.
  3. As of the start date, runs the screener's scoring logic (Pass 2 filters)
     to pick the top SHORTLIST_SIZE stocks.
  4. Walk-forwards day-by-day through ~2026, trading only those selected
     stocks using indicator-based entry/exit rules.

No LLM calls, no look-ahead bias, no Tavily.

Usage:
    python backtest.py
    python backtest.py --capital 50000
    python backtest.py --max-hold 20
    python backtest.py --save-charts
    python backtest.py --loose        # relaxed entry rules, more trades
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from openai import OpenAI

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import extra_universe_tickers, is_crypto, settings
from tools.indicators import compute_indicators
from tools.llm_prompts import select_llm_prompt
from tools.market_data import _normalise_single, _session
from tools.portfolio_analysis import analyse_portfolio, classify_risk_tier
from tools.screener import (
    _stratified_selection,
    compute_momentum_score,
    pass1_metadata_filter,
)
from tools.term import (
    USE_COLOR,
    _box,
    _c,
    _pct,
    _print_holdings_decisions,
    _print_portfolio_status,
    _rjust,
    _section,
)

logger = logging.getLogger("backtest")
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)

RISK_FREE_RATE = 4.5  # annualised % used for Sharpe calculation
TRADING_DAYS_PER_YEAR = 252
BATCH_SIZE = 200
BATCH_PAUSE_SEC = 1.5

# Maximum lookback days for 1y return calculation (matches indicators.py)
_1Y_LOOKBACK = 252

# How often (in trading days) the LLM reviews the portfolio and re-screens
# the universe for new candidates.
LLM_REVIEW_INTERVAL = 250

COLUMN_NAMES = ["Open", "High", "Low", "Close", "Volume"]

def _jsonable(obj):
    """Recursively convert numpy/pandas scalar types to plain Python types."""
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    return obj


def _next_exec_bar(df, after_date):
    """First bar strictly after `after_date` in this symbol's OWN calendar.

    The global master-date index is the union of every ticker's dates, so it
    includes weekend dates contributed by crypto. A stock has no bar on those
    days, so executing at a global "next_date" that lands on a weekend would
    drop the trade. Resolving the next bar per-symbol fills a Friday stock
    decision at Monday's open while still filling crypto at Saturday's.
    """
    if df is None or len(df) == 0:
        return None
    i = df.index.searchsorted(after_date, side="right")
    return df.index[i] if i < len(df.index) else None


def _size_entry(symbol, slot_dollars, price, cash):
    """Size a new entry. Returns (qty, cost) on success, or (0.0, reason) if
    nothing can be bought.

    Crypto can be bought fractionally, so we deploy the smaller of the target
    slot or available cash and take whatever fraction that buys. Equities and
    ETFs trade in whole shares, with a one-share floor when the slot rounds
    below a single share (and a cap so we never overspend cash).
    """
    if price <= 0:
        return 0.0, "invalid price"
    if is_crypto(symbol):
        budget = min(slot_dollars, cash)
        if budget < 1.0:
            return 0.0, f"insufficient cash for a fractional unit (${cash:,.2f})"
        qty = budget / price
        return qty, qty * price
    qty = int(slot_dollars / price)
    if qty < 1:
        qty = 1
    qty = min(qty, int(cash / price))
    if qty < 1:
        return 0.0, f"1 share costs ${price:,.2f} > available cash ${cash:,.2f}"
    return qty, qty * price


def _fmt_qty(qty):
    """Whole shares as an integer, fractional crypto with trimmed decimals."""
    if float(qty) == int(qty):
        return f"{int(qty):,d}"
    return f"{qty:,.6f}".rstrip("0").rstrip(".")


def _call_llm(system_prompt: str, user_content: str) -> list:
    """Call the LLM via OpenRouter and parse its JSON-array reply."""
    text = ""
    try:
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=settings.OPENROUTER_API_KEY,
        )
        response = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.2,
        )
        text = response.choices[0].message.content or ""
        cleaned = text.strip()
        fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
        if fence:
            cleaned = fence.group(1)
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        logger.exception("Backtest LLM call/parse failed. Raw response: %s", text)
        return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compute_sharpe(returns: pd.Series, rf: float, periods: int) -> float:
    """Annualised Sharpe ratio."""
    if len(returns) < 2:
        return 0.0
    excess = returns - rf / 100 / periods
    if excess.std() == 0 or excess.std() is None or np.isnan(excess.std()):
        return 0.0
    return float(np.sqrt(periods) * excess.mean() / excess.std())


def _max_drawdown(equity: pd.Series) -> float:
    """Largest peak-to-trough decline as a positive percentage."""
    rolling_max = equity.cummax()
    dd = (equity - rolling_max) / rolling_max
    return float(abs(dd.min()) * 100) if not dd.isna().all() else 0.0


def _cache_path(start: str, end: str) -> Path:
    """Path to the pickle cache file for a given date range."""
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return settings.DATA_DIR / f"backtest_data_{start}_{end}.pkl"


def _load_cached_data(start: str, end: str) -> dict[str, pd.DataFrame]:
    """Load whatever cached data exists on disk (possibly empty/partial).

    Cache files are named backtest_data_{cached_start}_{cached_end}.pkl. A
    cache whose cached_start is on or before the requested start covers the
    requested range as a subset (it just has extra leading history, which is
    harmless — downstream code filters to the requested window anyway). So we
    consider ANY cache file with cached_start <= start as a candidate, not
    just exact start-date matches, and pick the most recently modified one
    among those. This means a long-range download (e.g. starting 2010) is
    reused for later, narrower start dates without re-downloading.
    """
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(
        r"^backtest_data_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})\.pkl$"
    )
    candidates = []
    for path in settings.DATA_DIR.glob("backtest_data_*_*.pkl"):
        m = pattern.match(path.name)
        if not m:
            continue
        cached_start = m.group(1)
        if cached_start <= start:
            candidates.append(path)

    if not candidates:
        return {}

    path = max(candidates, key=lambda p: p.stat().st_mtime)
    try:
        logger.info("Loading cached data from %s", path)
        return pd.read_pickle(path)
    except Exception:
        logger.warning("Failed to load cache, will re-download")
        return {}


def _save_cached_data(data: dict[str, pd.DataFrame], start: str, end: str) -> None:
    """Save downloaded data to pickle cache."""
    path = _cache_path(start, end)
    try:
        pd.to_pickle(data, path)
        logger.info("Saved cached data to %s", path)
    except Exception as exc:
        logger.warning("Failed to cache data: %s", exc)


def _fetch_data_batched(
    tickers: list[str], start: str, end: str
) -> dict[str, pd.DataFrame]:
    """Download daily OHLCV for many tickers in batches using yfinance,
    with pickle caching. Normalised via _normalise_single(). Also fetches SPY.

    Returns dict[ticker -> DataFrame], including "SPY".
    """
    # Check cache first — reuse whatever's already there, only fetch what's missing.
    result = _load_cached_data(start, end)
    missing = [t for t in tickers if t not in result]
    need_spy = "SPY" not in result

    if not missing and not need_spy:
        return result

    today_str = date.today().isoformat()
    logger.info(
        "Downloading data for %d / %d tickers from %s to %s (cache had %d)",
        len(missing) + (1 if need_spy else 0),
        len(tickers) + 1,
        start,
        today_str,
        len(result),
    )
    if result:
        print(
            f"    Using cached data for {len(result)} tickers, "
            f"fetching {len(missing) + (1 if need_spy else 0)} missing ..."
        )

    # Always fetch SPY first if missing — single ticker, no group_by to avoid MultiIndex wrapping
    if need_spy:
        try:
            spy_kwargs: dict = dict(
                start=start,
                end=end,
                interval="1d",
                progress=False,
                auto_adjust=False,
            )
            if _session is not None:
                spy_kwargs["session"] = _session
            raw_spy = yf.download("SPY", **spy_kwargs)
            if raw_spy is not None and not raw_spy.empty:
                df = _normalise_single(raw_spy)
                if df is not None:
                    result["SPY"] = df
        except Exception as exc:
            logger.warning("Failed to fetch SPY: %s", exc)

        if "SPY" not in result:
            logger.error("SPY data not available — aborting.")
            return {}

    if not missing:
        _save_cached_data(result, start, end)
        return result

    # Batch the missing tickers
    batch_kwargs: dict = dict(
        start=start,
        end=end,
        interval="1d",
        progress=False,
        group_by="ticker",
        auto_adjust=False,
        threads=True,
    )
    if _session is not None:
        batch_kwargs["session"] = _session

    total = len(missing)
    for i in range(0, total, BATCH_SIZE):
        batch = missing[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
        pct = min(100, int((i + len(batch)) / total * 100))
        print(
            f"    Downloading batch {batch_num}/{total_batches} "
            f"({len(batch)} tickers, {pct}%) ... ",
            end="",
            flush=True,
        )
        try:
            raw = yf.download(batch, **batch_kwargs)
            if raw is not None and not raw.empty:
                parsed = _extract_multi_yf(raw, batch)
                result.update(parsed)
                print(f"got {len(parsed)}", flush=True)
            else:
                print("no data", flush=True)
        except Exception as exc:
            print(f"error: {exc}", flush=True)

        if i + BATCH_SIZE < total:
            time.sleep(BATCH_PAUSE_SEC)

    logger.info(
        "Downloaded data for %d / %d missing tickers + SPY",
        len(result) - 1,
        len(tickers),
    )

    # Cache to disk for next run
    _save_cached_data(result, start, end)

    return result


def _extract_multi_yf(raw: pd.DataFrame, batch: list[str]) -> dict[str, pd.DataFrame]:
    """Extract individual ticker DataFrames from a multi-ticker yfinance result."""
    out: dict[str, pd.DataFrame] = {}
    if not isinstance(raw.columns, pd.MultiIndex):
        df = _normalise_single(raw)
        if df is not None and batch:
            out[batch[0]] = df
        return out

    level0 = raw.columns.get_level_values(0)
    if "Open" in level0:
        # Shape is (field, ticker)
        col_map: dict[str, list[str]] = {}
        for col in raw.columns:
            field, sym = col
            col_map.setdefault(sym, []).append((field, col))
        for sym in batch:
            if sym not in col_map:
                continue
            sub = pd.DataFrame({f: raw[c] for f, c in col_map[sym]})
            df = _normalise_single(sub)
            if df is not None:
                out[sym] = df
    else:
        # Shape is (ticker, field)
        for sym in batch:
            if sym not in level0:
                continue
            df = _normalise_single(raw[sym].copy())
            if df is not None:
                out[sym] = df
    return out


def _screen_stocks_as_of(
    data: dict[str, pd.DataFrame],
    screen_date: pd.Timestamp,
    spy: pd.DataFrame,
    extra_tickers: list[str] | None = None,
) -> list[str]:
    """Run the screener's Pass 2 logic (price, SMA, ATR, volume filters + scoring)
    on all tickers as of *screen_date*, using only data available up to that date.

    Returns the ticker symbols of the top SHORTLIST_SIZE picks.
    """
    extra_tickers = extra_tickers or []
    survivors: list[dict] = []
    total = len(data)

    n_insufficient = 0
    n_indicator_err = 0
    n_price = 0
    n_above_sma = 0
    n_atr = 0
    n_vol = 0

    for idx, (ticker, df) in enumerate(data.items()):
        if ticker == "SPY":
            continue

        # Slice data up to screen_date
        window = df.loc[:screen_date]
        if len(window) < settings.SCREEN_DAYS:
            n_insufficient += 1
            continue

        try:
            signals = compute_indicators(window)
        except Exception:
            continue

        if "error" in signals:
            n_indicator_err += 1
            continue

        price = signals["price"]
        is_madmax_ticker = ticker in extra_tickers

        if price < settings.MIN_PRICE:
            n_price += 1
            continue
        if not is_madmax_ticker and price > settings.MAX_PRICE:
            n_price += 1
            continue

        # MAD MAX tickers (crypto / leveraged ETFs) bypass the normal
        # trend/volatility/volume filters — they're admitted unconditionally
        # and left for the LLM to evaluate.
        if not is_madmax_ticker:
            if not signals["price_above_sma_50"]:
                n_above_sma += 1
                continue

            atr_pct = signals["atr_pct"]
            if atr_pct < 1.0 or atr_pct > 5.0:
                n_atr += 1
                continue

            vol_sma_20 = signals["vol_sma_20"]
            if vol_sma_20 < settings.MIN_AVG_VOLUME:
                n_vol += 1
                continue

        atr_pct = signals["atr_pct"]
        vol_ratio = signals["vol_ratio"]
        pct_above = signals["pct_above_sma_50"]
        rsi_val = signals["rsi"]
        adx_val = signals["adx"]
        daily_dollar_volume = signals.get("daily_dollar_volume", 0)

        # ---- Dollar-volume ceiling (filter out mega-cap liquid stocks) ----
        if daily_dollar_volume > settings.MAX_DAILY_DOLLAR_VOLUME:
            continue

        survivors.append(
            {
                "ticker": ticker,
                "price": price,
                "pct_above_sma_50": pct_above,
                "vol_ratio": vol_ratio,
                "atr_pct": atr_pct,
                "rsi": rsi_val,
                "adx": adx_val,
                "pct_above_sma_200": signals.get("pct_above_sma_200"),
                "return_1y_pct": signals.get("return_1y_pct"),
                "score": compute_momentum_score(signals),
            }
        )

    print(
        _c("    filter stats:", "dim")
        + f"  insufficient_data={n_insufficient}  indicator_err={n_indicator_err}"
        f"  price_out={n_price}  below_sma50={n_above_sma}  atr_out={n_atr}"
        f"  low_vol={n_vol}  -> " + _c(f"survivors={len(survivors)}", "bold")
    )

    # Stratified selection (same as screener \u2014 3 volatility tiers)
    target = settings.SCREEN_SHORTLIST_SIZE
    selected = _stratified_selection(survivors, target)

    if extra_tickers:
        # Always surface the opted-in extra universe (crypto/leveraged ETFs)
        # as candidates, even if the stratified selection would otherwise
        # drop them in favour of higher-scoring ordinary stocks.
        selected_tickers_set = {s["ticker"] for s in selected}
        extra_survivors = [
            s
            for s in survivors
            if s["ticker"] in extra_tickers and s["ticker"] not in selected_tickers_set
        ]
        selected = selected + extra_survivors

    tickers_out = [s["ticker"] for s in selected]

    print(
        _c(
            f"    -> picked {len(tickers_out)} stocks as of {screen_date.date()}:",
            "dim",
        )
    )
    header = (
        f"      {'#':>2s}  {'TICKER':<6s} {'PRICE':>9s} {'SCORE':>8s} "
        f"{'RSI':>6s} {'ADX':>6s} {'ATR%':>6s}"
    )
    print(_c(header, "dim"))
    for i, s in enumerate(selected, 1):
        print(
            f"      {i:2d}  {s['ticker']:<6s} ${s['price']:>8.2f} {s['score']:>8.2f} "
            f"{s['rsi']:>6.1f} {s['adx']:>6.1f} {s['atr_pct']:>6.2f}"
        )

    return tickers_out


# ---------------------------------------------------------------------------
# Main backtest
# ---------------------------------------------------------------------------


def run_backtest(
    capital: float = 100_000.0,
    max_hold_days: int = 30,
    save_charts: bool = False,
    loose: bool = False,
    use_llm: bool = False,
    llm_interval: int = LLM_REVIEW_INTERVAL,
    start_date: str | None = None,
    end_date: str | None = None,
    risk_mode: bool = False,
    risk_positions: int = 3,
    madmax_mode: bool = False,
    include_crypto: bool = False,
    include_etfs: bool = False,
) -> None:
    start_date = start_date or settings.BACKTEST_START_DATE
    end_date = end_date or date.today().isoformat()

    if madmax_mode:
        include_crypto = True
        include_etfs = True

    extra_tickers = extra_universe_tickers(include_crypto, include_etfs)

    if madmax_mode or risk_mode:
        settings.SHORTLIST_SIZE = risk_positions

    # Max concurrent positions: SCREEN_SHORTLIST_SIZE candidates are screened,
    # but only this many are ever held/sized at once. Risk/madmax modes are
    # concentrated, so their position count is the user-chosen risk_positions
    # (stored in SHORTLIST_SIZE above); diversified mode uses MAX_POSITIONS.
    max_positions = (
        settings.SHORTLIST_SIZE if (risk_mode or madmax_mode) else settings.MAX_POSITIONS
    )
    # Fraction of the book to actually deploy, leaving the rest as cash reserve.
    deploy_frac = 1 - settings.CASH_RESERVE_PCT / 100

    # Need at least SCREEN_DAYS trading days of history before the screen date.
    # 90 trading days ~= 130 calendar days. Use 200 to be safe (handles holidays).
    screen_buffer_days = max(settings.SCREEN_DAYS * 2, 200)
    screen_start = (
        pd.Timestamp(start_date) - timedelta(days=screen_buffer_days)
    ).isoformat()[:10]

    mode_label = (
        _c("LOOSE \u2014 more trades", "yellow")
        if loose
        else _c("STRICT \u2014 original rules", "cyan")
    )
    if madmax_mode:
        risk_label = _c(
            f"MAD MAX \u2014 {max_positions} positions, crypto/leveraged allowed",
            "magenta",
            "bold",
        )
    elif risk_mode:
        risk_label = _c(
            f"RISK \u2014 {max_positions} concentrated picks", "red", "bold"
        )
    else:
        risk_label = _c("NO RISK \u2014 diversified", "green")
    info_lines = [
        f"Period         {start_date}  ->  {end_date}",
        f"Capital        ${capital:,.0f}",
        f"Max hold       {max_hold_days} days",
        f"Max positions  {max_positions}",
        f"Screen days    {settings.SCREEN_DAYS}",
        f"Mode           {mode_label}",
        f"Risk profile   {risk_label}",
    ]
    if use_llm:
        if not settings.OPENROUTER_API_KEY:
            info_lines.append(
                _c("LLM review     DISABLED (no OPENROUTER_API_KEY)", "red")
            )
            use_llm = False
        else:
            info_lines.append(
                f"LLM review     every {llm_interval} trading days "
                + _c(f"({settings.LLM_MODEL})", "magenta")
            )
    else:
        info_lines.append(_c("LLM review     off", "dim"))
    _box(info_lines, title=_c("WALK-FORWARD BACKTESTER", "bold", "white"))

    # ---- 1. Get NASDAQ/AMEX ticker universe via screener's Pass 1 ----
    _section("Step 1 / 4  --  Loading NASDAQ/AMEX ticker universe")
    universe = pass1_metadata_filter()
    if not universe:
        print(_c("  ERROR: Could not load ticker universe.", "red", "bold"))
        return
    if extra_tickers:
        extra = [t for t in extra_tickers if t not in universe]
        universe = universe + extra
        print(
            f"  -> +{len(extra)} extra-universe ticker(s) (crypto/leveraged ETFs) added"
        )
    print(f"  -> {len(universe)} candidates after Pass 1 metadata filter")

    # ---- 2. Download data for all candidates ----
    _section("Step 2 / 4  --  Downloading historical data")
    data = _fetch_data_batched(universe, screen_start, end_date)
    spy = data.get("SPY")
    if spy is None:
        print(_c("  ERROR: SPY data not available.", "red", "bold"))
        return
    if len(data) <= 1:
        print(_c("  ERROR: No ticker data available.", "red", "bold"))
        return
    print(f"  -> Data downloaded for {len(data) - 1} / {len(universe)} tickers + SPY")

    # ---- 3. Run screener once as of start_date ----
    _section("Step 3 / 4  --  Running screener to pick stocks")
    screen_date = pd.Timestamp(start_date)
    selected_tickers = _screen_stocks_as_of(
        data, screen_date, spy, extra_tickers=extra_tickers
    )

    if not selected_tickers:
        print(_c("  ERROR: Screener returned no stocks.", "red", "bold"))
        return

    print(f"  -> Screener selected {len(selected_tickers)} stock(s) to track:")
    print(f"     {', '.join(sorted(selected_tickers))}")

    tickers = selected_tickers

    # ---- 4. Build master date index (from the selected tickers + SPY) ----
    _section("Step 4 / 4  --  Walking forward through time")
    # Use SPY's trading calendar as the master date index so that weekends
    # and holidays (present in crypto tickers like BTC-USD which trade 24/7)
    # don't produce phantom trading days for equities. Position fills and
    # exits still resolve to each symbol's own next available bar via
    # _next_exec_bar, so crypto fills correctly on its own calendar.
    master_dates = sorted(d for d in spy.index if d >= pd.Timestamp(start_date))
    print(f"  -> {len(master_dates)} trading days in period")

    # ---- 5. Initialise state ----
    cash = capital
    positions: dict[str, dict] = {}
    trade_log: list[dict] = []
    daily_equity: list[dict] = []

    signals_cache: dict[str, dict] = {}
    last_bar_count: dict[str, int] = {}
    df_slices: dict[str, pd.DataFrame] = {}

    spy_close = spy["Close"]
    spy_start_idx = spy_close.index.asof(pd.Timestamp(start_date))
    spy_first_close = (
        float(spy_close.loc[spy_start_idx])
        if spy_start_idx is not None
        else (float(spy_close.iloc[0]) if not spy_close.empty else 1.0)
    )
    spy_shares = capital / spy_first_close if spy_first_close > 0 else 0

    ticker_data: dict[str, pd.DataFrame] = {t: data[t] for t in tickers if t in data}

    total_days = len(master_dates)
    progress_interval = 250

    # ---- 6. Walk forward ----
    for idx, current_date in enumerate(master_dates):
        day_num = idx + 1

        # Progress display
        if day_num % progress_interval == 0 or day_num == total_days:
            num_open = len(positions)
            pv = cash + sum(
                p["qty"]
                * float(
                    ticker_data[p["ticker"]].loc[
                        ticker_data[p["ticker"]].index.asof(current_date), "Close"
                    ]
                )
                for p in positions.values()
                if p["ticker"] in ticker_data
                and ticker_data[p["ticker"]].index.asof(current_date) is not None
            )
            pv_color = "green" if pv >= capital else "red"
            msg = (
                f"  Day {_c(f'{day_num:>5d}', 'bold')}/{total_days}   "
                f"{current_date.date()}   "
                f"Portfolio {_c(f'${pv:>11,.0f}', pv_color)}   "
                f"Positions {num_open:>2d}"
            )
            if day_num == total_days:
                print(msg)
            else:
                print(msg + "\033[K", end="\r", flush=True)

        # ---- Update indicator cache for selected tickers ----
        for ticker in tickers:
            if ticker not in ticker_data:
                continue
            df = ticker_data[ticker]
            slice_df = df.loc[:current_date]
            df_slices[ticker] = slice_df
            n = len(slice_df)

            if n < 45:
                signals_cache.pop(ticker, None)
                last_bar_count.pop(ticker, None)
                continue

            prev = last_bar_count.get(ticker, 0)
            if n != prev or ticker not in signals_cache:
                try:
                    signals_cache[ticker] = compute_indicators(slice_df)
                    last_bar_count[ticker] = n
                except Exception:
                    signals_cache.pop(ticker, None)
                    last_bar_count.pop(ticker, None)

        # ---- Next trading date ----
        next_idx = idx + 1
        next_date = master_dates[next_idx] if next_idx < total_days else None

        # ---- LLM portfolio review (every llm_interval trading days, plus
        # always on Day 1 so the LLM picks the initial positions) ----
        if (
            use_llm
            and next_date is not None
            and (day_num == 1 or day_num % llm_interval == 0)
        ):
            print("\033[K")
            _section(
                f"LLM Portfolio Review  --  Day {day_num}, {current_date.date()}",
                width=70,
            )

            # Re-screen the universe as of today for fresh candidates.
            review_tickers = _screen_stocks_as_of(
                data, current_date, spy, extra_tickers=extra_tickers
            )

            # Make sure every held ticker + new candidate has data/signals.
            review_set = set(review_tickers) | set(positions.keys())
            for ticker in review_set:
                if ticker not in ticker_data and ticker in data:
                    ticker_data[ticker] = data[ticker]
                if ticker not in ticker_data:
                    continue
                slice_df = ticker_data[ticker].loc[:current_date]
                df_slices[ticker] = slice_df
                if len(slice_df) < 45:
                    continue
                if ticker not in signals_cache or last_bar_count.get(ticker) != len(
                    slice_df
                ):
                    try:
                        signals_cache[ticker] = compute_indicators(slice_df)
                        last_bar_count[ticker] = len(slice_df)
                    except Exception:
                        signals_cache.pop(ticker, None)

            # Portfolio value as of today.
            open_value_now = 0.0
            position_list = []
            for ticker, pos in positions.items():
                sig = signals_cache.get(ticker)
                if sig is None or "error" in sig:
                    continue
                price = sig["price"]
                market_value = pos["qty"] * price
                open_value_now += market_value
                unrealized_pnl_pct = (
                    (price - pos["entry_price"]) / pos["entry_price"]
                ) * 100
                position_list.append(
                    {
                        "symbol": ticker,
                        "qty": pos["qty"],
                        "avg_cost": round(pos["entry_price"], 2),
                        "market_value": round(market_value, 2),
                        "unrealized_pnl_pct": round(unrealized_pnl_pct, 2),
                        "hold_days": (current_date - pos["entry_date"]).days,
                        **{
                            k: sig[k]
                            for k in (
                                "price",
                                "rsi",
                                "adx",
                                "atr_pct",
                                "macd_bullish",
                                "ema_9",
                                "ema_21",
                                "bb_upper",
                                "price_above_sma_50",
                                "vol_ratio",
                                "price_above_sma_200",
                                "pct_above_sma_200",
                                "return_1y_pct",
                                "pct_off_1y_high",
                            )
                            if k in sig and sig[k] is not None
                        },
                    }
                )

            net_liq = cash + open_value_now
            portfolio_dict = {
                "positions": [
                    {
                        "symbol": p["symbol"],
                        "qty": p["qty"],
                        "avg_cost": p["avg_cost"],
                        "market_value": p["market_value"],
                    }
                    for p in position_list
                ],
                "net_liquidation": net_liq,
                "cash": cash,
            }
            portfolio_intel = analyse_portfolio(portfolio_dict, position_list)

            held_symbols = set(positions.keys())
            candidate_list = []
            for ticker in review_tickers:
                if ticker in held_symbols:
                    continue
                sig = signals_cache.get(ticker)
                if sig is None or "error" in sig:
                    continue
                candidate_list.append(
                    {
                        "symbol": ticker,
                        **{
                            k: sig[k]
                            for k in (
                                "price",
                                "rsi",
                                "adx",
                                "atr_pct",
                                "macd_bullish",
                                "ema_9",
                                "ema_21",
                                "price_above_sma_50",
                                "vol_ratio",
                                "price_above_sma_200",
                                "pct_above_sma_200",
                                "return_1y_pct",
                                "pct_off_1y_high",
                            )
                            if k in sig and sig[k] is not None
                        },
                        "risk_tier": classify_risk_tier(sig),
                    }
                )

            context = _jsonable(
                {
                    "cash": round(cash, 2),
                    "net_liquidation": round(net_liq, 2),
                    "deployable_cash": round(
                        max(0.0, cash - net_liq * settings.CASH_RESERVE_PCT / 100), 2
                    ),
                    "portfolio_intelligence": portfolio_intel,
                    "positions": position_list,
                    "candidates": candidate_list,
                    "held_count": len(positions),
                    "max_positions": max_positions,
                }
            )
            llm_prompt = select_llm_prompt(risk_mode, madmax_mode)
            decisions = _call_llm(llm_prompt, json.dumps(context))
            print(_c(f"  -> LLM returned {len(decisions)} decision(s)", "dim"))
            print()

            held_before = dict(positions)
            new_buys = []  # (symbol, detail, reasoning)
            holdings_summary = {}  # symbol -> (action, pnl_pct, reasoning)
            skipped = []  # (symbol, action, reason) — decisions that didn't execute

            for decision in decisions:
                symbol = decision.get("symbol")
                action = (decision.get("action") or "").upper()
                reasoning = decision.get("reasoning", "")
                exec_date = _next_exec_bar(ticker_data.get(symbol), current_date)
                if not symbol or exec_date is None:
                    if symbol in held_before:
                        pos = held_before[symbol]
                        sig = signals_cache.get(symbol)
                        ref_price = (
                            sig["price"]
                            if sig and "error" not in sig
                            else pos["entry_price"]
                        )
                        pnl_pct = (
                            (ref_price - pos["entry_price"]) / pos["entry_price"]
                        ) * 100
                        holdings_summary[symbol] = (
                            action or "HOLD",
                            pnl_pct,
                            reasoning,
                        )
                    elif symbol:
                        skipped.append(
                            (symbol, action or "?", "no future price data available")
                        )
                    continue
                next_open = float(ticker_data[symbol].loc[exec_date, "Open"])

                if action == "SELL" and symbol in positions:
                    pos = positions[symbol]
                    pnl_pct = (
                        (next_open - pos["entry_price"]) / pos["entry_price"]
                    ) * 100
                    trade_log.append(
                        {
                            "ticker": symbol,
                            "entry_date": pos["entry_date"].date(),
                            "exit_date": exec_date.date(),
                            "entry_price": round(pos["entry_price"], 2),
                            "exit_price": round(next_open, 2),
                            "return_pct": round(pnl_pct, 2),
                            "qty": pos["qty"],
                            "pnl_dollars": round(
                                pos["qty"] * (next_open - pos["entry_price"]), 2
                            ),
                            "hold_days": (exec_date - pos["entry_date"]).days,
                            "exit_reason": "llm_sell",
                            "rsi_at_entry": pos.get("rsi_at_entry"),
                            "adx_at_entry": pos.get("adx_at_entry"),
                            "macd_bullish_at_entry": pos.get("macd_bullish_at_entry"),
                            "vol_ratio_at_entry": pos.get("vol_ratio_at_entry"),
                        }
                    )
                    cash += pos["qty"] * next_open
                    del positions[symbol]
                    holdings_summary[symbol] = ("SELL", pnl_pct, reasoning)

                elif action == "ADD" and symbol in positions:
                    pos = positions[symbol]
                    portfolio_value = cash + open_value_now
                    add_dollars = portfolio_value * deploy_frac / max_positions
                    add_qty, add_cost = _size_entry(
                        symbol, add_dollars, next_open, cash
                    )
                    pnl_pct = (
                        (next_open - pos["entry_price"]) / pos["entry_price"]
                    ) * 100
                    if add_qty > 0:
                        total_qty = pos["qty"] + add_qty
                        pos["entry_price"] = (
                            pos["entry_price"] * pos["qty"] + next_open * add_qty
                        ) / total_qty
                        pos["qty"] = total_qty
                        cash -= add_cost
                    holdings_summary[symbol] = ("ADD", pnl_pct, reasoning)

                elif action == "HOLD":
                    pos = positions.get(symbol)
                    if pos is not None:
                        pnl_pct = (
                            (next_open - pos["entry_price"]) / pos["entry_price"]
                        ) * 100
                        holdings_summary[symbol] = ("HOLD", pnl_pct, reasoning)

                elif action in ("BUY", "ADD") and symbol not in positions:
                    if len(positions) >= max_positions:
                        skipped.append(
                            (
                                symbol,
                                "BUY",
                                f"book already full ({max_positions} positions)",
                            )
                        )
                        continue
                    portfolio_value = cash + open_value_now
                    # Target weight = an equal slice of the deployable book
                    # (portfolio value minus the cash reserve). Crypto fills
                    # fractionally; equities/ETFs round to whole shares with a
                    # one-share floor. _size_entry never overspends cash.
                    slot_dollars = portfolio_value * deploy_frac / max_positions
                    qty, cost = _size_entry(symbol, slot_dollars, next_open, cash)
                    if qty > 0:
                        sig = signals_cache.get(symbol, {})
                        positions[symbol] = {
                            "ticker": symbol,
                            "entry_price": next_open,
                            "qty": qty,
                            "entry_date": exec_date,
                            "rsi_at_entry": round(sig.get("rsi", 0), 2),
                            "adx_at_entry": round(sig.get("adx", 0), 2),
                            "macd_bullish_at_entry": sig.get("macd_bullish"),
                            "vol_ratio_at_entry": round(sig.get("vol_ratio", 0), 2),
                        }
                        cash -= cost
                        unit = "units" if is_crypto(symbol) else "sh"
                        new_buys.append(
                            (
                                symbol,
                                f"{_fmt_qty(qty)} {unit} @ ${next_open:,.2f}",
                                reasoning,
                            )
                        )
                    else:
                        skipped.append((symbol, "BUY", cost))

                else:
                    # Decision matched no executable branch (e.g. SELL/ADD/HOLD
                    # on a symbol not held). Surface it instead of dropping it.
                    skipped.append(
                        (
                            symbol,
                            action or "?",
                            "not actionable (symbol not held / unknown action)",
                        )
                    )

            # Full table of every position held going into this review and
            # its decision, even if the LLM didn't return one for a symbol,
            # plus any new positions opened this review.
            print()
            _section(f"Holdings — decision summary (Day {day_num})", width=70)
            table_rows = []
            for symbol, pos in held_before.items():
                if symbol in holdings_summary:
                    action, pnl_pct, reasoning = holdings_summary[symbol]
                else:
                    sig = signals_cache.get(symbol)
                    ref_price = (
                        sig["price"]
                        if sig and "error" not in sig
                        else pos["entry_price"]
                    )
                    pnl_pct = (
                        (ref_price - pos["entry_price"]) / pos["entry_price"]
                    ) * 100
                    action = "HOLD"
                    reasoning = "No decision returned by LLM — defaulted to HOLD"
                table_rows.append((symbol, action, pnl_pct, reasoning))
            for symbol, detail, reasoning in new_buys:
                table_rows.append((symbol, "BUY", None, f"{detail} — {reasoning}"))
            for symbol, action, reason in skipped:
                table_rows.append(
                    (symbol, "SKIP", None, f"{action} not taken — {reason}")
                )
            _print_holdings_decisions(table_rows)

            # ---- Portfolio status: every position now held, its worth, and
            # the total portfolio value (post-decision snapshot) ----
            status_rows = []
            holdings_value = 0.0
            for symbol, pos in positions.items():
                df = ticker_data.get(symbol)
                exec_date = _next_exec_bar(df, current_date)
                if df is not None and exec_date is not None:
                    price = float(df.loc[exec_date, "Open"])
                else:
                    sig = signals_cache.get(symbol)
                    price = (
                        sig["price"]
                        if sig and "error" not in sig
                        else pos["entry_price"]
                    )
                market_value = pos["qty"] * price
                holdings_value += market_value
                pnl_pct = ((price - pos["entry_price"]) / pos["entry_price"]) * 100
                status_rows.append((symbol, pos["qty"], price, market_value, pnl_pct))
            print()
            _section(f"Portfolio status (Day {day_num})", width=70)
            _print_portfolio_status(status_rows, cash, cash + holdings_value)

            # Prune the working ticker list to just what's now held plus this
            # review's candidates — otherwise it grows unbounded and the daily
            # indicator recompute below (compute_indicators per ticker, with
            # rolling windows up to 200 bars) gets slower every review.
            tickers = list(set(positions.keys()) | review_set)
            stale = set(signals_cache.keys()) - set(tickers)
            for ticker in stale:
                signals_cache.pop(ticker, None)
                last_bar_count.pop(ticker, None)
                df_slices.pop(ticker, None)

        # ---- Process exits ----
        to_exit: list[str] = []
        for ticker in list(positions.keys()):
            pos = positions[ticker]
            sig = signals_cache.get(ticker)
            if sig is None or "error" in sig:
                continue

            entry_price = pos["entry_price"]
            current_close = sig["price"]
            hold_days = (current_date - pos["entry_date"]).days
            rsi_val = sig["rsi"]
            macd_bullish = sig["macd_bullish"]
            ema_9_above_ema_21 = sig["ema_9"] > sig["ema_21"]
            bb_above_upper = current_close > sig["bb_upper"]

            exit_reason = None
            if not loose:
                # Original tight exit rules (no stop-loss — the LLM decides SELLs)
                if rsi_val > 75 and bb_above_upper:
                    exit_reason = "overbought"
                elif not macd_bullish and not ema_9_above_ema_21:
                    exit_reason = "signal_collapse"
                elif hold_days >= max_hold_days:
                    exit_reason = "max_hold"
            else:
                # Loose: no overbought sell, no signal-collapse sell, no stop-loss
                if hold_days >= max_hold_days:
                    exit_reason = "max_hold"

            if exit_reason is not None:
                if (
                    next_date is not None
                    and next_date in df_slices.get(ticker, pd.DataFrame()).index
                ):
                    exit_price = float(ticker_data[ticker].loc[next_date, "Open"])
                else:
                    exit_price = current_close

                pnl_pct = ((exit_price - entry_price) / entry_price) * 100
                trade_log.append(
                    {
                        "ticker": ticker,
                        "entry_date": pos["entry_date"].date(),
                        "exit_date": next_date.date()
                        if next_date is not None
                        else current_date.date(),
                        "entry_price": round(entry_price, 2),
                        "exit_price": round(exit_price, 2),
                        "return_pct": round(pnl_pct, 2),
                        "qty": pos["qty"],
                        "pnl_dollars": round(
                            pos["qty"] * (exit_price - entry_price), 2
                        ),
                        "hold_days": hold_days,
                        "exit_reason": exit_reason,
                        "rsi_at_entry": pos.get("rsi_at_entry"),
                        "adx_at_entry": pos.get("adx_at_entry"),
                        "macd_bullish_at_entry": pos.get("macd_bullish_at_entry"),
                        "vol_ratio_at_entry": pos.get("vol_ratio_at_entry"),
                    }
                )
                cash += pos["qty"] * exit_price
                to_exit.append(ticker)

        for ticker in to_exit:
            del positions[ticker]

        # ---- Process entries ----
        if len(positions) < max_positions and next_date is not None:
            for ticker in tickers:
                if ticker in positions or ticker not in ticker_data:
                    continue
                sig = signals_cache.get(ticker)
                if sig is None or "error" in sig:
                    continue

                macd_bullish = sig["macd_bullish"]
                ema_9_above_ema_21 = sig["ema_9"] > sig["ema_21"]
                rsi_val = sig["rsi"]
                price_above_sma_50 = sig["price_above_sma_50"]
                vol_ratio = sig["vol_ratio"]
                adx_val = sig["adx"]

                if loose:
                    conditions_met = sum(
                        [
                            macd_bullish,
                            ema_9_above_ema_21,
                            30 <= rsi_val <= 75,
                            price_above_sma_50,
                            vol_ratio > 0.7,
                            adx_val > 14,
                        ]
                    )
                    if conditions_met < 3:
                        continue
                else:
                    if not all(
                        [
                            macd_bullish,
                            ema_9_above_ema_21,
                            40 <= rsi_val <= 65,
                            price_above_sma_50,
                            vol_ratio > 1.0,
                            adx_val > 18,
                        ]
                    ):
                        continue

                if next_date not in ticker_data[ticker].index:
                    continue

                # Fixed fractional sizing
                portfolio_value = cash + sum(
                    p["qty"]
                    * float(
                        ticker_data[p["ticker"]].loc[
                            ticker_data[p["ticker"]].index.asof(current_date), "Close"
                        ]
                    )
                    for p in positions.values()
                    if ticker_data[p["ticker"]].index.asof(current_date) is not None
                )
                position_size_dollars = portfolio_value * deploy_frac / max_positions
                next_open = float(ticker_data[ticker].loc[next_date, "Open"])
                # Crypto fills fractionally; equities/ETFs whole-share with a
                # one-share floor. _size_entry caps spend at available cash.
                qty, cost = _size_entry(ticker, position_size_dollars, next_open, cash)
                if qty <= 0:
                    continue

                positions[ticker] = {
                    "ticker": ticker,
                    "entry_price": next_open,
                    "qty": qty,
                    "entry_date": next_date,
                    "rsi_at_entry": round(rsi_val, 2),
                    "adx_at_entry": round(adx_val, 2),
                    "macd_bullish_at_entry": macd_bullish,
                    "vol_ratio_at_entry": round(vol_ratio, 2),
                }
                cash -= cost

        # ---- Record daily portfolio value ----
        open_value = 0.0
        for p in positions.values():
            ticker_df = ticker_data[p["ticker"]]
            asof_idx = ticker_df.index.asof(current_date)
            if asof_idx is not None:
                open_value += p["qty"] * float(ticker_df.loc[asof_idx, "Close"])
        portfolio_value = cash + open_value

        spy_asof = spy_close.index.asof(current_date)
        spy_val = (
            float(spy_close.loc[spy_asof]) * spy_shares
            if spy_asof is not None
            else cash
        )

        daily_equity.append(
            {
                "date": current_date.date(),
                "portfolio_value": round(portfolio_value, 2),
                "cash": round(cash, 2),
                "invested": round(open_value, 2),
                "spy_value": round(spy_val, 2),
            }
        )

    # ---- 6b. Liquidate all remaining positions at the final close ----
    for ticker in list(positions.keys()):
        pos = positions[ticker]
        ticker_df = ticker_data[ticker]
        asof_idx = ticker_df.index.asof(master_dates[-1])
        if asof_idx is None:
            continue
        exit_price = float(ticker_df.loc[asof_idx, "Close"])
        entry_price = pos["entry_price"]
        pnl_pct = ((exit_price - entry_price) / entry_price) * 100
        hold_days = (master_dates[-1] - pos["entry_date"]).days
        trade_log.append(
            {
                "ticker": ticker,
                "entry_date": pos["entry_date"].date(),
                "exit_date": master_dates[-1].date(),
                "entry_price": round(entry_price, 2),
                "exit_price": round(exit_price, 2),
                "return_pct": round(pnl_pct, 2),
                "qty": pos["qty"],
                "pnl_dollars": round(pos["qty"] * (exit_price - entry_price), 2),
                "hold_days": hold_days,
                "exit_reason": "final_liquidation",
                "rsi_at_entry": pos.get("rsi_at_entry"),
                "adx_at_entry": pos.get("adx_at_entry"),
                "macd_bullish_at_entry": pos.get("macd_bullish_at_entry"),
                "vol_ratio_at_entry": pos.get("vol_ratio_at_entry"),
            }
        )
        cash += pos["qty"] * exit_price
    positions.clear()
    if daily_equity:
        daily_equity[-1]["portfolio_value"] = round(cash, 2)
        daily_equity[-1]["cash"] = round(cash, 2)
        daily_equity[-1]["invested"] = 0.0

    # ---- 7. Performance metrics ----
    ending_capital = daily_equity[-1]["portfolio_value"] if daily_equity else cash
    total_return = ((ending_capital - capital) / capital) * 100

    spy_ending = daily_equity[-1]["spy_value"] if daily_equity else capital
    spy_return = ((spy_ending - capital) / capital) * 100
    alpha = total_return - spy_return

    years = (
        (master_dates[-1] - master_dates[0]).days / 365.25
        if len(master_dates) > 1
        else 1.0
    )
    ann_return = (
        ((1 + total_return / 100) ** (1 / years) - 1) * 100 if years > 0 else 0.0
    )

    eq_series = pd.Series([d["portfolio_value"] for d in daily_equity])
    mdd = _max_drawdown(eq_series)

    daily_returns = eq_series.pct_change().dropna()
    sharpe = _compute_sharpe(daily_returns, RISK_FREE_RATE, TRADING_DAYS_PER_YEAR)

    wins = [t for t in trade_log if t["return_pct"] > 0]
    losses = [t for t in trade_log if t["return_pct"] <= 0]
    gross_wins = sum(t["return_pct"] for t in wins)
    gross_losses = abs(sum(t["return_pct"] for t in losses))
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf")
    pf_text = f"{profit_factor:.2f}" if gross_losses > 0 else "Inf"

    _section("BACKTEST RESULTS", width=70)
    result_lines = [
        f"{'Tickers tested':<20s} {len(tickers)}",
        f"{'Starting capital':<20s} ${capital:,.0f}",
        f"{'Ending capital':<20s} ${ending_capital:,.0f}",
        f"{'Total return':<20s} {_pct(total_return)}",
        f"{'SPY return':<20s} {_pct(spy_return)}",
        f"{'Alpha (vs SPY)':<20s} {_pct(alpha)}",
        f"{'Annualised return':<20s} {_pct(ann_return)}",
        f"{'Max drawdown':<20s} {_c(f'{mdd:.2f}%', 'red')}",
        f"{'Sharpe ratio':<20s} {sharpe:.2f}",
        f"{'Profit factor':<20s} {pf_text}",
    ]
    _box(result_lines, width=46)

    print()
    print(_c("  Trade statistics", "bold"))
    win_rate = (len(wins) / len(trade_log) * 100) if trade_log else 0.0
    avg_win = np.mean([t["return_pct"] for t in wins]) if wins else 0.0
    avg_loss = np.mean([t["return_pct"] for t in losses]) if losses else 0.0
    avg_hold = np.mean([t["hold_days"] for t in trade_log]) if trade_log else 0
    trade_lines = [
        f"{'Total trades':<16s} {len(trade_log)}",
        f"{'Win rate':<16s} {win_rate:.1f}%",
        f"{'Avg win':<16s} {_c(f'+{avg_win:.2f}%', 'green')}",
        f"{'Avg loss':<16s} {_c(f'{avg_loss:.2f}%', 'red')}",
        f"{'Avg hold days':<16s} {avg_hold:.1f}",
    ]
    if trade_log:
        best = max(trade_log, key=lambda t: t["return_pct"])
        worst = min(trade_log, key=lambda t: t["return_pct"])
        trade_lines.append(
            f"{'Best trade':<16s} {_c(best['ticker'], 'bold')} "
            f"{_pct(best['return_pct'])}  ({best['entry_date']} -> {best['exit_date']})"
        )
        trade_lines.append(
            f"{'Worst trade':<16s} {_c(worst['ticker'], 'bold')} "
            f"{_pct(worst['return_pct'])}  ({worst['entry_date']} -> {worst['exit_date']})"
        )
    for line in trade_lines:
        print(f"  {line}")

    # Per-ticker breakdown
    _section("Per-ticker breakdown", width=70)
    ticker_stats: dict[str, dict] = {}
    for t in trade_log:
        sym = t["ticker"]
        if sym not in ticker_stats:
            ticker_stats[sym] = {
                "trades": 0,
                "wins": 0,
                "total_return": 0.0,
                "holds": [],
                "best": t,
                "worst": t,
            }
        ts = ticker_stats[sym]
        ts["trades"] += 1
        if t["return_pct"] > 0:
            ts["wins"] += 1
        ts["total_return"] += t["return_pct"]
        ts["holds"].append(t["hold_days"])
        if t["return_pct"] > ts["best"]["return_pct"]:
            ts["best"] = t
        if t["return_pct"] < ts["worst"]["return_pct"]:
            ts["worst"] = t

    header = (
        f"  {'TICKER':<8s} {'Trades':>7s} {'WinRate':>8s} {'Contrib':>9s} "
        f"{'AvgHold':>8s} {'BestTrade':>10s} {'WorstTrade':>10s}"
    )
    print(_c(header, "bold"))
    print(_c("  " + "─" * 64, "dim"))
    for sym, ts in sorted(
        ticker_stats.items(), key=lambda kv: kv[1]["total_return"], reverse=True
    ):
        wr = (ts["wins"] / ts["trades"] * 100) if ts["trades"] else 0
        ah = np.mean(ts["holds"]) if ts["holds"] else 0
        bt = _pct(ts["best"]["return_pct"], decimals=1)
        wt = _pct(ts["worst"]["return_pct"], decimals=1)
        print(
            f"  {sym:<8s} {ts['trades']:>7d} {wr:>7.1f}% "
            f"{_rjust(_pct(ts['total_return']), 9)} {ah:>7.1f} "
            f"{_rjust(bt, 10)} {_rjust(wt, 10)}"
        )

    # Portfolio breakdown — best performers by $ P&L (--risk / --madmax only)
    if risk_mode or madmax_mode:
        _section("Portfolio breakdown — money made per ticker", width=70)
        pnl_by_ticker: dict[str, float] = {}
        for t in trade_log:
            pnl_by_ticker[t["ticker"]] = pnl_by_ticker.get(t["ticker"], 0.0) + t.get(
                "pnl_dollars", 0.0
            )

        header = f"  {'TICKER':<8s} {'TRADES':>7s} {'$ P&L':>16s}"
        print(_c(header, "bold"))
        print(_c("  " + "─" * 32, "dim"))
        for sym, pnl in sorted(
            pnl_by_ticker.items(), key=lambda kv: kv[1], reverse=True
        ):
            trades_n = ticker_stats[sym]["trades"]
            pnl_str = _c(f"${pnl:>14,.2f}", "green" if pnl >= 0 else "red")
            print(f"  {sym:<8s} {trades_n:>7d} {pnl_str}")
        print(_c("  " + "─" * 32, "dim"))
        total_pnl = sum(pnl_by_ticker.values())
        total_pnl_str = _c(
            f"${total_pnl:>14,.2f}", "green" if total_pnl >= 0 else "red"
        )
        print(f"  {'TOTAL':<8s} {'':>7s} {total_pnl_str}")

    # Final liquidation summary -- all positions are sold at the close of the
    # last trading day, so the portfolio ends entirely in cash.
    _section("Final liquidation", width=70)
    profit = cash - capital
    print(f"  {'All positions sold at final close':<30s}")
    print()
    label_start = f"{'Starting capital':<30s}"
    label_end = f"{'Ending cash (fully liquidated)':<30s}"
    label_earned = f"{'FINAL AMOUNT EARNED':<30s}"
    print(f"  {label_start} ${capital:>13,.2f}")
    print(f"  {label_end} ${cash:>13,.2f}")
    profit_str = _c(f"${profit:>13,.2f}", "green" if profit >= 0 else "red")
    print(f"  {_c(label_earned, 'bold')} {profit_str}  ({_pct(total_return)})")

    # ---- 8. Save outputs ----
    data_dir = settings.DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)

    trade_df = pd.DataFrame(trade_log)
    if not trade_df.empty:
        cols = [
            "ticker",
            "entry_date",
            "exit_date",
            "entry_price",
            "exit_price",
            "return_pct",
            "qty",
            "pnl_dollars",
            "hold_days",
            "exit_reason",
            "rsi_at_entry",
            "adx_at_entry",
            "macd_bullish_at_entry",
            "vol_ratio_at_entry",
        ]
        trade_df[cols].to_csv(data_dir / "backtest_trades.csv", index=False)
        print(f"\n  Trade log saved to data/backtest_trades.csv")
    else:
        print(f"\n  No trades executed.")

    equity_df = pd.DataFrame(daily_equity)
    equity_df.to_csv(data_dir / "backtest_equity.csv", index=False)
    print(f"  Equity curve saved to data/backtest_equity.csv")

    if save_charts:
        _save_charts(daily_equity, trade_log, data_dir)


def _save_charts(
    daily_equity: list[dict],
    trade_log: list[dict],
    data_dir: Path,
) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.dates as mdates
        import matplotlib.pyplot as plt
    except ImportError:
        print("  matplotlib not installed — skipping chart save.")
        return

    df = pd.DataFrame(daily_equity)
    df["date"] = pd.to_datetime(df["date"])

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(14, 10), gridspec_kw={"height_ratios": [3, 1]}
    )

    ax1.plot(
        df["date"],
        df["portfolio_value"],
        label="Strategy",
        color="#2563eb",
        linewidth=1.8,
    )
    ax1.plot(
        df["date"],
        df["spy_value"],
        label="SPY (buy & hold)",
        color="#94a3b8",
        linewidth=1.5,
        linestyle="--",
    )
    ax1.set_ylabel("Portfolio Value ($)")
    ax1.legend(loc="upper left")
    ax1.grid(True, alpha=0.3)
    ax1.set_title("Backtest Equity Curve")

    if len(trade_log) < 200 and trade_log:
        date_to_val = dict(zip(df["date"], df["portfolio_value"]))
        entry_dates = []
        entry_values = []
        exit_dates = []
        exit_values = []
        for t in trade_log:
            ed = pd.Timestamp(t["entry_date"])
            xd = pd.Timestamp(t["exit_date"])
            if ed in date_to_val:
                entry_dates.append(ed)
                entry_values.append(date_to_val[ed])
            if xd in date_to_val:
                exit_dates.append(xd)
                exit_values.append(date_to_val[xd])
        if entry_dates:
            ax1.scatter(
                entry_dates,
                entry_values,
                marker="^",
                color="#22c55e",
                s=30,
                zorder=5,
                label="Entry",
            )
        if exit_dates:
            ax1.scatter(
                exit_dates,
                exit_values,
                marker="v",
                color="#ef4444",
                s=30,
                zorder=5,
                label="Exit",
            )

    eq_series = df["portfolio_value"]
    rolling_max = eq_series.cummax()
    drawdown = (eq_series - rolling_max) / rolling_max * 100
    ax2.fill_between(df["date"], drawdown, 0, color="#ef4444", alpha=0.5)
    ax2.set_ylabel("Drawdown (%)")
    ax2.set_xlabel("Date")
    ax2.grid(True, alpha=0.3)

    for ax in [ax1, ax2]:
        ax.xaxis.set_major_locator(mdates.YearLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    plt.tight_layout()
    out_path = data_dir / "backtest_equity_curve.png"
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Equity curve chart saved to {out_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _ask(prompt: str, default: str = "") -> str:
    suffix = _c(f" [{default}]", "dim") if default else ""
    raw = input(f"  {prompt}{suffix}: ").strip()
    return raw or default


def _ask_yes_no(prompt: str, default: bool = False) -> bool:
    default_str = "y/N" if not default else "Y/n"
    raw = input(f"  {prompt} ({default_str}): ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def _run_interactive_wizard() -> dict:
    """Ask the user a short series of questions and return run_backtest kwargs."""
    _box(
        [
            "Answer a few questions to configure this backtest.",
            "Press Enter to accept the default shown in [brackets].",
        ],
        title=_c("INTERACTIVE BACKTEST SETUP", "bold", "white"),
    )

    print()
    print(_c("  1. How far back do you want to go?", "bold"))
    period = _ask(
        "Enter a start date (YYYY-MM-DD) or a number of years back",
        "3",
    )
    if re.match(r"^\d{4}-\d{2}-\d{2}$", period):
        start_date = period
    else:
        try:
            years = float(period)
        except ValueError:
            years = 3.0
        start_date = (date.today() - timedelta(days=int(years * 365.25))).isoformat()
    print(_c(f"     -> start date: {start_date}", "dim"))

    print()
    print(_c("  2. How risky do you want to be?", "bold"))
    print("     [1] Non-risky, no crypto or leveraged ETFs")
    print("     [2] Non-risky, plus crypto")
    print("     [3] Non-risky, plus crypto and leveraged ETFs")
    print("     [4] Risky (concentrated), no crypto or leveraged ETFs")
    print("     [5] Risky (concentrated), plus crypto and leveraged ETFs")
    print("     [6] MAD MAX — most aggressive, crypto and leveraged ETFs built in")
    risk_choice = _ask("Choice", "1")

    risky = risk_choice in ("4", "5", "6")
    madmax = risk_choice == "6"
    include_crypto = risk_choice in ("2", "3", "5")
    include_etfs = risk_choice in ("3", "5")

    risk_positions = 3
    if risky:
        risk_positions = int(_ask("How many concurrent positions (suggested 2-4)", "3"))

    print()
    print(_c("  3. Any other flags/options?", "bold"))
    capital = float(_ask("Starting capital", "100000"))
    use_llm = _ask_yes_no("Enable periodic LLM portfolio reviews", True)
    llm_interval = LLM_REVIEW_INTERVAL
    if use_llm:
        llm_interval = int(
            _ask("Trading days between LLM reviews", str(LLM_REVIEW_INTERVAL))
        )
    loose = _ask_yes_no("Use loose entry/exit rules (more trades)", False)
    save_charts = _ask_yes_no("Save an equity curve chart (PNG)", True)
    end_date_raw = _ask("End date (YYYY-MM-DD, blank = today)", "")
    end_date = end_date_raw or None
    max_hold_raw = _ask("Override max hold days (blank = use default)", "")
    max_hold = int(max_hold_raw) if max_hold_raw else None

    if max_hold is not None:
        resolved_max_hold = max_hold
    elif risky:
        resolved_max_hold = 5 * 365
    else:
        resolved_max_hold = settings.BACKTEST_MAX_HOLD_DAYS

    return {
        "capital": capital,
        "max_hold_days": resolved_max_hold,
        "save_charts": save_charts,
        "use_llm": use_llm,
        "llm_interval": llm_interval,
        "loose": loose,
        "start_date": start_date,
        "end_date": end_date,
        "risk_mode": risky,
        "risk_positions": risk_positions,
        "madmax_mode": madmax,
        "include_crypto": include_crypto,
        "include_etfs": include_etfs,
    }


def main() -> None:
    if len(sys.argv) == 1:
        run_backtest(**_run_interactive_wizard())
        return

    parser = argparse.ArgumentParser(
        description="Walk-forward backtester — daily rule-based trading with "
        "periodic LLM portfolio reviews, no look-ahead bias."
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=100_000.0,
        help="Starting capital (default: 100000)",
    )
    parser.add_argument(
        "--max-hold",
        type=int,
        default=None,
        help="Override max hold days (default: BACKTEST_MAX_HOLD_DAYS from config)",
    )
    parser.add_argument(
        "--save-charts",
        action="store_true",
        help="Save equity curve PNG to data/ (requires matplotlib)",
    )
    parser.add_argument(
        "--loose",
        action="store_true",
        help="Relaxed entry/exit rules — more trades, holds winners longer",
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help="Enable periodic LLM portfolio reviews (re-screens universe and "
        "can SELL/ADD/BUY) every --llm-interval trading days",
    )
    parser.add_argument(
        "--llm-interval",
        type=int,
        default=LLM_REVIEW_INTERVAL,
        help=f"Trading days between LLM reviews (default: {LLM_REVIEW_INTERVAL})",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="Backtest start date YYYY-MM-DD (default: BACKTEST_START_DATE from config)",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        help="Backtest end date YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--risk",
        action="store_true",
        help="Concentrated high-risk mode: only --risk-positions stocks held at "
        "once (the LLM picks what it thinks is best), and max hold defaults to "
        "5 years instead of 1. Default is diversified (--no-risk behaviour).",
    )
    parser.add_argument(
        "--risk-positions",
        type=int,
        default=3,
        help="Number of concurrent positions in --risk mode (default: 3, "
        "suggested range 2-4)",
    )
    parser.add_argument(
        "--madmax",
        action="store_true",
        help="MAD MAX mode: the most aggressive setting. Implies --risk-style "
        "concentration (--risk-positions positions, default 5-year max hold) "
        "but additionally adds crypto (BTC-USD, ETH-USD, SOL-USD, ...) and "
        "leveraged ETFs (TQQQ, SOXL, UPRO, ...) to the tradeable universe and "
        "bypasses the normal trend/volatility filters for them. Overrides "
        "--risk.",
    )
    parser.add_argument(
        "--include-crypto",
        action="store_true",
        help="Surface crypto (BTC-USD, ETH-USD, SOL-USD, ...) as tradeable "
        "candidates in addition to the normal stock universe. Implied by --madmax.",
    )
    parser.add_argument(
        "--include-etfs",
        action="store_true",
        help="Surface leveraged ETFs (TQQQ, SOXL, UPRO, ...) as tradeable "
        "candidates in addition to the normal stock universe. Implied by --madmax.",
    )
    args = parser.parse_args()

    if args.max_hold is not None:
        max_hold = args.max_hold
    elif args.risk or args.madmax:
        max_hold = 5 * 365
    else:
        max_hold = settings.BACKTEST_MAX_HOLD_DAYS

    run_backtest(
        capital=args.capital,
        max_hold_days=max_hold,
        save_charts=args.save_charts,
        use_llm=args.llm,
        llm_interval=args.llm_interval,
        loose=args.loose,
        start_date=args.start_date,
        end_date=args.end_date,
        risk_mode=args.risk or args.madmax,
        risk_positions=args.risk_positions,
        madmax_mode=args.madmax,
        include_crypto=args.include_crypto,
        include_etfs=args.include_etfs,
    )


if __name__ == "__main__":
    main()
