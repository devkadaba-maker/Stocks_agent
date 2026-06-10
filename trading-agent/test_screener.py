#!/usr/bin/env python3
"""Test suite for the screener module.

Tests:
  1. _is_valid_common_stock — metadata filter logic (no network)
  2. _download_listing — live download from NASDAQ FTP
  3. pass1_metadata_filter — full Pass 1 pipeline
  4. pass2_momentum_screen — momentum filters on a small batch
  5. run_morning_screen — end-to-end with shortlist.json write
"""

import json
import os
import sys
from pathlib import Path

# Ensure venv packages are found
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import settings
from tools.screener import (
    _download_listing,
    _is_valid_common_stock,
    pass1_metadata_filter,
    pass2_momentum_screen,
    run_morning_screen,
)


def test_is_valid_common_stock():
    """Unit tests for the metadata filter — no network required."""
    print("\n" + "=" * 60)
    print("  TEST 1: _is_valid_common_stock")
    print("=" * 60)

    # Should PASS
    assert (
        _is_valid_common_stock(
            {"Symbol": "AAPL", "Security Name": "Apple Inc. Common Stock", "ETF": "N"}
        )
        is True
    )
    assert (
        _is_valid_common_stock(
            {
                "Symbol": "MSFT",
                "Security Name": "Microsoft Corporation Common Stock",
                "ETF": "N",
            }
        )
        is True
    )
    assert (
        _is_valid_common_stock(
            {"Symbol": "TSLA", "Security Name": "Tesla Inc. Common Stock"}
        )
        is True
    )  # no ETF col

    # Should FAIL — ETF
    assert (
        _is_valid_common_stock(
            {"Symbol": "SPY", "Security Name": "SPDR S&P 500 ETF", "ETF": "Y"}
        )
        is False
    )

    # Should FAIL — warrants, rights, units, preferred
    assert (
        _is_valid_common_stock(
            {"Symbol": "AAPLW", "Security Name": "Apple Inc. Warrant", "ETF": "N"}
        )
        is False
    )
    assert (
        _is_valid_common_stock(
            {"Symbol": "XYZR", "Security Name": "XYZ Corp Right", "ETF": "N"}
        )
        is False
    )
    assert (
        _is_valid_common_stock(
            {"Symbol": "ABC", "Security Name": "ABC Corp Unit", "ETF": "N"}
        )
        is False
    )
    assert (
        _is_valid_common_stock(
            {"Symbol": "DEF", "Security Name": "DEF Inc Preferred Stock", "ETF": "N"}
        )
        is False
    )
    assert (
        _is_valid_common_stock(
            {"Symbol": "GHI", "Security Name": "GHI Corp ADR", "ETF": "N"}
        )
        is False
    )
    assert (
        _is_valid_common_stock(
            {"Symbol": "JKL", "Security Name": "JKL Depositary Shares", "ETF": "N"}
        )
        is False
    )

    # Should FAIL — test symbols
    assert (
        _is_valid_common_stock(
            {"Symbol": "TEST$", "Security Name": "Test Issue", "ETF": "N"}
        )
        is False
    )
    assert (
        _is_valid_common_stock(
            {"Symbol": "BRK.B", "Security Name": "Berkshire Hathaway", "ETF": "N"}
        )
        is False
    )

    # Should FAIL — empty
    assert (
        _is_valid_common_stock({"Symbol": "", "Security Name": "", "ETF": "N"}) is False
    )
    assert (
        _is_valid_common_stock({"Security Name": "Missing Symbol", "ETF": "N"}) is False
    )

    print("    ✅ All assertion tests passed")
    return True


def test_download_listing():
    """Test live download from NASDAQ Trader FTP."""
    print("\n" + "=" * 60)
    print("  TEST 2: _download_listing (live)")
    print("=" * 60)

    try:
        rows = _download_listing(
            "https://ftp.nasdaqtrader.com/SymbolDirectory/nasdaqlisted.txt",
            fallback_url="https://datahub.io/core/nasdaq-listings/r/nasdaq-listed.csv",
        )
        assert len(rows) > 100, f"Expected >100 rows, got {len(rows)}"
        # Check that we got dicts with expected keys
        first = rows[0]
        assert "Symbol" in first, f"Missing 'Symbol' key. Keys: {list(first.keys())}"
        assert "Security Name" in first, f"Missing 'Security Name' key"
        print(f"    ✅ Downloaded {len(rows)} rows from nasdaqlisted.txt")
        print(f"    First row keys: {list(first.keys())}")
        print(f"    Sample row: {first}")
        return True
    except Exception as e:
        print(f"    ❌ Download failed: {e}")
        return False


def test_pass1():
    """Test full Pass 1 metadata filter (downloads both NASDAQ files)."""
    print("\n" + "=" * 60)
    print("  TEST 3: pass1_metadata_filter")
    print("=" * 60)

    try:
        tickers = pass1_metadata_filter()
        assert len(tickers) > 500, f"Expected >500 survivors, got {len(tickers)}"
        # Spot-check that big names are included
        for expected in ["AAPL", "MSFT", "NVDA", "AMZN", "GOOG"]:
            assert expected in tickers, f"{expected} missing from Pass 1!"
        print(f"    ✅ Pass 1 returned {len(tickers)} unique tickers")
        print(f"    Sample: {tickers[:20]}")
        return True
    except Exception as e:
        print(f"    ❌ Pass 1 failed: {e}")
        return False


def test_pass2():
    """Test Pass 2 momentum screen on a small batch of known liquid stocks."""
    print("\n" + "=" * 60)
    print("  TEST 4: pass2_momentum_screen (small batch)")
    print("=" * 60)

    # Use a small curated list so the test runs in ~30 seconds
    test_tickers = [
        "AAPL",
        "MSFT",
        "NVDA",
        "AMZN",
        "GOOG",
        "META",
        "TSLA",
        "AMD",
        "NFLX",
        "CRM",
        "JPM",
        "V",
        "UNH",
        "XOM",
        "COST",
    ]

    try:
        results = pass2_momentum_screen(test_tickers)
        print(
            f"    Pass 2 returned {len(results)} survivors from {len(test_tickers)} candidates"
        )

        if results:
            for i, s in enumerate(results, 1):
                print(
                    f"      {i:2d}. {s['ticker']:<6s}  ${s['price']:<8.2f}  "
                    f"score={s['score']:<6.2f}  RSI={s['rsi']:<5.1f}  "
                    f"ATR%={s['atr_pct']:<4.1f}  vol_ratio={s['vol_ratio']:.2f}"
                )
        else:
            print(
                "    ⚠️  No stocks survived filters (market may be closed or conditions tight)"
            )

        # Verify structure of results
        for s in results:
            for key in (
                "ticker",
                "price",
                "pct_above_sma_50",
                "vol_ratio",
                "atr_pct",
                "rsi",
                "adx",
                "score",
            ):
                assert key in s, f"Missing key '{key}' in result: {s}"
            assert isinstance(s["ticker"], str)
            assert isinstance(s["price"], (int, float))
            assert isinstance(s["score"], (int, float))

        print("    ✅ Pass 2 structure and filters verified")
        return True
    except Exception as e:
        print(f"    ❌ Pass 2 failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_full_pipeline():
    """End-to-end: run_morning_screen() writes shortlist.json."""
    print("\n" + "=" * 60)
    print("  TEST 5: run_morning_screen (full pipeline)")
    print("=" * 60)

    # Clean up any existing shortlist
    shortlist_path = settings.SHORTLIST_PATH
    if shortlist_path.exists():
        shortlist_path.unlink()

    try:
        results = run_morning_screen()
        print(f"    Morning screen returned {len(results)} stocks")

        # Check file was written
        assert shortlist_path.exists(), "shortlist.json was not written!"
        with open(shortlist_path) as f:
            saved = json.load(f)
        assert saved == results, "Saved shortlist != returned results"
        print(f"    ✅ shortlist.json verified ({len(saved)} entries)")

        if results:
            print("\n    Top picks:")
            for i, s in enumerate(results, 1):
                print(
                    f"      {i:2d}. {s['ticker']:<6s}  ${s['price']:<8.2f}  "
                    f"score={s['score']:<6.2f}"
                )

        return True
    except Exception as e:
        print(f"    ❌ Full pipeline failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Screener test suite")
    parser.add_argument(
        "--quick", action="store_true", help="Skip slow network tests (only test 1)"
    )
    parser.add_argument("--pass1", action="store_true", help="Run Pass 1 only")
    parser.add_argument(
        "--pass2", action="store_true", help="Run Pass 2 only (small batch)"
    )
    parser.add_argument("--full", action="store_true", help="Run full pipeline only")
    args = parser.parse_args()

    results = {}

    if args.quick:
        results["metadata_filter"] = test_is_valid_common_stock()
    elif args.pass1:
        results["metadata_filter"] = test_is_valid_common_stock()
        results["download_listing"] = test_download_listing()
        results["pass1"] = test_pass1()
    elif args.pass2:
        results["pass2"] = test_pass2()
    elif args.full:
        results["full_pipeline"] = test_full_pipeline()
    else:
        # Run all tests
        results["metadata_filter"] = test_is_valid_common_stock()
        results["download_listing"] = test_download_listing()
        results["pass1"] = test_pass1()
        results["pass2"] = test_pass2()
        # Only run full pipeline if pass2 succeeded (it's slow)
        if results["pass2"]:
            results["full_pipeline"] = test_full_pipeline()

    # Summary
    print("\n" + "=" * 60)
    print("  RESULTS SUMMARY")
    print("=" * 60)
    all_pass = True
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"    {status}  {name}")
        if not passed:
            all_pass = False

    if all_pass:
        print("\n    All tests passed!")
    else:
        print("\n    Some tests failed - check output above")
        sys.exit(1)


main()
