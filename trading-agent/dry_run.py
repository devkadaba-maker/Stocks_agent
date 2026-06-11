#!/usr/bin/env python3
"""Full dry-run of the trading pipeline.

Runs every step EXCEPT placing real IBKR orders:
  1. Morning screen (downloads listings, fetches bars, writes shortlist.json)
  2. Trading cycle (loads portfolio, computes indicators, calls LLM)
  3. Prints what WOULD be traded — but does NOT call place_order()

The place_order() call in tools/execution is monkey-patched to log instead.
"""

import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

# Ensure venv packages are found
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ============================================================
# Monkey-patch place_order BEFORE any other imports
# ============================================================
import tools.execution as exec_mod


def _dry_place_order(symbol, conid, account_id, side, qty):
    logger = logging.getLogger("dry_run")
    msg = f"[DRY-RUN] WOULD place_order(symbol={symbol}, conid={conid}, side={side}, qty={qty})"
    logger.info(msg)
    return f"dry_{side}_{symbol}_{int(time.time())}"


exec_mod.place_order = _dry_place_order

# ============================================================
# Now import the rest
# ============================================================
from config import settings
from tools.execution import get_todays_trades, init_db
from tools.screener import run_morning_screen

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("dry_run")


def print_separator(title):
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


# ============================================================
# Build a simulated portfolio
# ============================================================
def build_simulated_portfolio():
    """Create a realistic simulated portfolio from shortlist data."""
    from tools.indicators import compute_indicators
    from tools.market_data import fetch_bars

    shortlist_path = settings.SHORTLIST_PATH
    if not shortlist_path.exists():
        return None

    shortlist = json.loads(shortlist_path.read_text())
    top_picks = shortlist[: min(3, len(shortlist))]
    tickers = [s["ticker"] for s in top_picks]
    bars = fetch_bars(tickers, period="3mo")

    positions = []
    for s in top_picks:
        ticker = s["ticker"]
        signals = compute_indicators(bars[ticker]) if ticker in bars else {}
        positions.append(
            {
                "symbol": ticker,
                "conid": 12345,
                "qty": 100,
                "avg_cost": signals.get("price", s["price"]),
            }
        )

    return {
        "account_id": "DRY_RUN_ACCOUNT",
        "net_liquidation": 100_000.00,
        "cash": 50_000.00,
        "positions": positions,
    }


def run_dry_run():
    print()
    print("  ╔══════════════════════════════════════════════════════════╗")
    print("  ║       TRADING AGENT — FULL DRY RUN                      ║")
    print("  ║       (pipeline runs, orders are LOGGED not PLACED)     ║")
    print("  ╚══════════════════════════════════════════════════════════╝")
    print()

    # Ensure dirs exist
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    settings.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    init_db()

    # Clear today's trades so we start clean
    print_separator("STEP 0: Reset trades.db for clean dry-run")
    conn = sqlite3.connect(settings.TRADES_DB_PATH)
    conn.execute("DELETE FROM trades")
    conn.commit()
    conn.close()
    print("  ✅ Cleared all trades from DB")

    # ─────────────────────────────────────────────────────────────────
    # STEP 1: Morning Screen (or use existing shortlist)
    # ─────────────────────────────────────────────────────────────────
    shortlist_path = settings.SHORTLIST_PATH
    if shortlist_path.exists():
        shortlist = json.loads(shortlist_path.read_text())
        print(f"  ✅ Using existing shortlist.json with {len(shortlist)} candidates")
    else:
        print_separator("STEP 1: Morning Screen (Pass 1 + Pass 2 → shortlist.json)")
        print("\n  ▶  Downloading NASDAQ & AMEX listings...")
        shortlist = run_morning_screen()
        print(f"\n  ✅ Morning screen produced {len(shortlist)} candidates")

    if not shortlist:
        print("  ❌ No candidates — nothing to do. Exiting.")
        return

    print("\n  Shortlist summary:")
    sorted_by_score = sorted(shortlist, key=lambda s: s["score"], reverse=True)
    header = (
        f"  {'#':>3s}  {'Ticker':<7s}  {'Price':>8s}  {'Score':>7s}  "
        f"{'RSI':>5s}  {'ADX':>5s}  {'ATR%':>5s}  {'VolR':>5s}  {'%>50SMA':>8s}"
    )
    print(header)
    print("  " + "-" * 66)
    for i, s in enumerate(sorted_by_score, 1):
        print(
            f"  {i:3d}  {s['ticker']:<7s}  ${s['price']:<7.2f}  "
            f"{s['score']:>7.2f}  {s['rsi']:>5.1f}  {s['adx']:>5.1f}  "
            f"{s['atr_pct']:>5.2f}  {s['vol_ratio']:>5.2f}  "
            f"{s['pct_above_sma_50']:>+7.2f}%"
        )

    # ─────────────────────────────────────────────────────────────────
    # STEP 2: Build simulated portfolio
    # ─────────────────────────────────────────────────────────────────
    print_separator("STEP 2: Build simulated portfolio")

    portfolio = build_simulated_portfolio()
    if portfolio is None:
        print("  ❌ No shortlist available to build portfolio.")
        return

    print(f"\n     Simulated account with ${portfolio['net_liquidation']:,.0f}")
    print(f"     Cash: ${portfolio['cash']:,.0f}")
    print(f"     Holding {len(portfolio['positions'])} positions:")
    for p in portfolio["positions"]:
        print(
            f"       {p['symbol']:<6s}  {p['qty']:>4d} shares @ ${p['avg_cost']:<8.2f}"
        )

    from tools.portfolio_analysis import analyse_portfolio

    portfolio_intel = analyse_portfolio(portfolio)
    print(f"\n  Portfolio health score: {portfolio_intel['health_score']}/100")
    print(f"  Risk tiers represented: {portfolio_intel['tier_count']}")
    for note in portfolio_intel['health_notes']:
        print(f"    • {note}")

    # ─────────────────────────────────────────────────────────────────
    # STEP 3: Monkey-patch agent module references
    # ─────────────────────────────────────────────────────────────────
    print_separator("STEP 3: Apply all monkey-patches")

    # Because agent.py does `from tools.portfolio import get_portfolio_state, tickle`,
    # we need to override the names on the *agent module itself*.
    import agent as agent_mod

    # Override tickle (no-op)
    agent_mod.tickle = lambda: None

    # Override get_portfolio_state (returns our simulated portfolio)
    agent_mod.get_portfolio_state = lambda: portfolio

    # Override get_conid (returns fake conid)
    agent_mod.get_conid = lambda sym: 12345

    # Override the market-hours gate: replace datetime.now with a fake
    # that always returns 9:31 AM ET on a weekday.
    import pytz

    _orig_datetime = agent_mod.datetime

    class _FakeDatetime:
        """Pretend it's 9:31 AM ET on a weekday."""

        @classmethod
        def now(cls, tz=None):
            et = pytz.timezone("America/New_York")
            return datetime(2026, 6, 10, 9, 31, 0, tzinfo=et)

        @classmethod
        def strftime(cls, fmt, dt=None):
            return (dt or datetime.now()).strftime(fmt)

        # Proxy other classmethods to real datetime
        @classmethod
        def fromisoformat(cls, s):
            return datetime.fromisoformat(s)

        @classmethod
        def fromtimestamp(cls, ts, tz=None):
            return datetime.fromtimestamp(ts, tz=tz)

    agent_mod.datetime = _FakeDatetime

    # Also patch tools.portfolio.get_portfolio_state so that if any
    # other module calls it, they get our data
    import tools.portfolio as port_mod

    port_mod.get_portfolio_state = lambda: portfolio
    port_mod.tickle = lambda: None
    port_mod.get_conid = lambda sym: 12345

    print("  ✅ tickle()     → no-op")
    print("  ✅ get_portfolio_state() → simulated portfolio")
    print("  ✅ get_conid()  → fake 12345")
    print("  ✅ market-hours gate → forced to 9:31 AM ET")
    print("  ✅ place_order() → dry-run (logged, not placed)")

    # ─────────────────────────────────────────────────────────────────
    # STEP 4: Run the trading cycle
    # ─────────────────────────────────────────────────────────────────
    print_separator("STEP 4: Trading Cycle (Phase 1: review, Phase 2: new buys)")

    cycle_id = f"DRY_RUN_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    print(f"\n  Cycle ID: {cycle_id}\n")

    # Run the cycle — all patches are in place
    agent_mod.run_cycle(cycle_id)

    # ─────────────────────────────────────────────────────────────────
    # STEP 5: Summary of what happened
    # ─────────────────────────────────────────────────────────────────
    print_separator("STEP 5: Summary of Dry-Run")

    trades = get_todays_trades()
    if trades:
        print(f"\n  📊 {len(trades)} trade(s) would have been executed:")
        print()
        print(
            f"  {'Action':>6s}  {'Symbol':<7s}  {'Qty':>5s}  {'Price':>8s}  "
            f"{'Order ID':<30s}  Reasoning"
        )
        print("  " + "-" * 110)
        for t in trades:
            reasoning = (t.get("reasoning") or "")[:55]
            print(
                f"  {t['action']:>6s}  {t['symbol']:<7s}  {t['qty']:>5d}  "
                f"${t.get('fill_price', 0):<7.2f}  {t.get('order_id', ''):<30s}  "
                f"{reasoning}"
            )
    else:
        print("\n  📭 No trades would have been executed this cycle.")
        print("     (Conditions not met, or LLM chose to wait.)")

    # ─────────────────────────────────────────────────────────────────
    # STEP 6: LLM decision context
    # ─────────────────────────────────────────────────────────────────
    print_separator("STEP 6: Decision Context")

    net_liq = portfolio["net_liquidation"]
    cash = portfolio["cash"]
    invested = net_liq - cash
    deployment_pct = (invested / net_liq) * 100
    reserve_floor = net_liq * (settings.CASH_RESERVE_PCT / 100)
    deployable = max(0, cash - reserve_floor)
    cautious_mode = deployment_pct > settings.DEPLOYMENT_CAUTION_PCT

    print(f"\n  Portfolio deployment:  {deployment_pct:.1f}%")
    print(f"  Cash:                  ${cash:,.2f}")
    print(f"  Reserve floor ({settings.CASH_RESERVE_PCT}%):  ${reserve_floor:,.2f}")
    print(f"  Deployable cash:       ${deployable:,.2f}")
    print(f"  Cautious mode:         {'YES 🟡' if cautious_mode else 'No 🟢'}")
    print(f"  At capacity:           {'YES 🔴' if deployable < 500 else 'No 🟢'}")
    print()
    print(f"  ── Held positions (Phase 1 input):")
    for p in portfolio["positions"]:
        print(f"     {p['symbol']:<7s}  {p['qty']:>4d} shares @ ${p['avg_cost']:<8.2f}")
    print()
    print(f"  ── Shortlist candidates (Phase 2 input):")
    for s in shortlist:
        print(f"     {s['ticker']:<7s}  ${s['price']:<8.2f}  score={s['score']:<7.2f}")

    # ─────────────────────────────────────────────────────────────────
    # Final
    # ─────────────────────────────────────────────────────────────────
    print_separator("DRY RUN COMPLETE")
    print()
    if trades:
        print(f"  🟢 Pipeline would execute {len(trades)} trade(s). See above.")
    else:
        print("  🟢 Pipeline ran successfully — no trades triggered (normal).")
    print()
    print(f"  Shortlist state: {settings.SHORTLIST_PATH}")
    if settings.SHORTLIST_PATH.exists():
        print(f"    ✅ Still present (will be deleted next time a cycle consumes it)")
    else:
        print(f"    ✅ Was consumed and deleted")
    print()
    print("  To place REAL orders:")
    print("    python main.py --once")


if __name__ == "__main__":
    run_dry_run()
