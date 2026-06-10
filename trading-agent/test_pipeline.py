#!/usr/bin/env python3
"""End-to-end smoke test: market_data -> indicators -> formatted output."""

import json
import os
import sys

# Ensure the venv packages are found
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import settings
from tools.indicators import compute_indicators
from tools.market_data import fetch_bars, fetch_current_price


def main():
    print("=" * 60)
    print("  TRADING AGENT — DATA PIPELINE SMOKE TEST")
    print("=" * 60)

    # ── Step 1: Config ──────────────────────────────────────────────
    print(f"\n[1] Config loaded")
    print(f"    Root:       {settings.ROOT_DIR}")
    print(f"    Shortlist:  {settings.SHORTLIST_PATH}")
    print(f"    Max pos:    {settings.MAX_POSITIONS}")
    print(f"    Min price:  ${settings.MIN_PRICE}")

    # ── Step 2: Fetch bars ──────────────────────────────────────────
    tickers = ["AAPL", "MSFT", "GOOG", "AMZN", "NVDA"]
    print(f"\n[2] Fetching bars for {len(tickers)} tickers (period=3mo)...")
    data = fetch_bars(tickers, period="3mo", interval="1d")
    print(f"    Returned: {len(data)} / {len(tickers)} tickers")

    if not data:
        print("    ERROR: No data returned. Cannot continue.")
        sys.exit(1)

    # Show basic info for each
    for ticker, df in data.items():
        print(
            f"    {ticker:>5s}: {len(df):>4d} bars, "
            f"{df['Close'].iloc[-1]:>8.2f} → {df['Close'].iloc[0]:>8.2f}"
        )

    # ── Step 3: Current prices ──────────────────────────────────────
    print(f"\n[3] Fetching live prices via fetch_current_price()...")
    live_prices = {}
    for t in tickers:
        price = fetch_current_price(t)
        live_prices[t] = price
        if price > 0:
            print(f"    {t:>5s}: ${price:>8.2f}")

    # ── Step 4: Compute indicators ──────────────────────────────────
    print(f"\n[4] Computing indicators (injecting live price)...")
    all_signals = {}
    for ticker in sorted(data.keys()):
        df = data[ticker]
        signals = compute_indicators(df, live_price=live_prices.get(ticker))
        all_signals[ticker] = signals
        print(f"\n    ── {ticker} ──")
        if "error" in signals:
            print(f"    ERROR: {signals['error']} ({signals['bars']} bars)")
            continue

        indicators_of_interest = [
            ("Price", f"${signals['price']}"),
            ("SMA 20", f"${signals['sma_20']}"),
            ("SMA 50", f"${signals['sma_50']}"),
            ("% above 50-SMA", f"{signals['pct_above_sma_50']:+.2f}%"),
            ("RSI (14)", str(signals["rsi"])),
            ("MACD", f"{signals['macd']:.4f} / {signals['macd_signal']:.4f}"),
            ("ATR", f"${signals['atr']:.2f} ({signals['atr_pct']}%)"),
            ("BB Width", f"{signals['bb_width']:.1f}%"),
            ("ADX", str(signals["adx"])),
            ("Stoch %K/%D", f"{signals['stoch_k']:.1f} / {signals['stoch_d']:.1f}"),
            ("Volume ratio", f"{signals['vol_ratio']:.2f}x vs 20-day avg"),
        ]
        for label, value in indicators_of_interest:
            print(f"    {label:>18s}: {value}")

    # ── Step 5: LLM-ready verdict ───────────────────────────────────
    print(f"\n[5] LLM-ready overview (live price used for all decisions):")
    print(f"    {'=' * 50}")
    for ticker in sorted(all_signals.keys()):
        s = all_signals[ticker]
        if "error" in s:
            continue

        verdict_parts = []
        if s.get("price_above_sma_50"):
            verdict_parts.append("uptrend")
        else:
            verdict_parts.append("downtrend")
        if s.get("macd_bullish"):
            verdict_parts.append("MACD bullish")
        else:
            verdict_parts.append("MACD bearish")
        if s.get("rsi_overbought"):
            verdict_parts.append("overbought")
        elif s.get("rsi_oversold"):
            verdict_parts.append("oversold")
        if s.get("adx_strong_trend"):
            verdict_parts.append("strong trend")
        if s.get("bb_above_upper"):
            verdict_parts.append("above upper BB")
        elif s.get("bb_below_lower"):
            verdict_parts.append("below lower BB")

        print(f"\n    {ticker} @ ${s['price']}")
        print(f"      {', '.join(verdict_parts)}")
        print(
            f"      RSI={s['rsi']}, ADX={s['adx']}, "
            f"Momentum={s['pct_above_sma_50']:+.1f}%"
        )
        print(f"      Vol ratio: {s['vol_ratio']}x")

    # ── Save full output for inspection ────────────────────────────
    output_path = settings.DATA_DIR / "test_output.json"
    with open(output_path, "w") as f:
        json.dump(all_signals, f, indent=2, default=str)
    print(f"\n[Done] Full signal dict written to {output_path}")


if __name__ == "__main__":
    main()
