#!/usr/bin/env python3
"""Full dry-run of the LIVE trading pipeline.

This mirrors agent.py exactly but with:
  - A self-tracked virtual portfolio (no IBKR dependency)
  - place_order() monkey-patched to log instead of executing
  - No simulated positions from thin air — portfolio starts empty.

Flow:
  1. Morning screen → shortlist
  2. LLM picks the best stocks (up to max positions)
  3. Shows picks; user confirms each "buy"
  4. Virtual portfolio is updated
  5. Prints final portfolio state
"""

import json
import logging
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Monkey-patch place_order BEFORE anything else
import tools.execution as exec_mod


def _dry_place_order(symbol, conid, account_id, side, qty):
    logger = logging.getLogger("dry_run")
    logger.info(
        "[DRY-RUN] place_order(symbol=%s, conid=%s, side=%s, qty=%s)",
        symbol,
        conid,
        side,
        qty,
    )
    return f"dry_{side}_{symbol}_{int(time.time())}"


exec_mod.place_order = _dry_place_order

# Now safe to import everything
import pandas_market_calendars as mcal
import pytz
from config import extra_universe_tickers, is_crypto, settings
from tools.execution import init_db, log_decision, log_trade
from tools.indicators import compute_indicators
from tools.llm_prompts import select_llm_prompt
from tools.market_data import fetch_bars
from tools.risk import (
    calculate_concentrated_position_size,
    calculate_position_size,
)
from tools.screener import compute_momentum_score, run_morning_screen
from tools.term import _box, _c, _fmt_qty, _pct, _rjust, _section
from tools.web_search import research_ticker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("dry_run")
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("yfinance").setLevel(logging.WARNING)


# ─────────────────────────────────────────────────────────────────────
# LLM call (same as agent.py)
# ─────────────────────────────────────────────────────────────────────

import re

from openai import OpenAI


def _call_llm(system_prompt: str, user_content: str) -> list:
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
        logger.exception("LLM call/parse failed. Raw response: %s", text)
        return []


# ─────────────────────────────────────────────────────────────────────
# Virtual portfolio persistence
# ─────────────────────────────────────────────────────────────────────


def _portfolio_path(portfolio_name: str) -> "Path":
    if portfolio_name == "default":
        return settings.VIRTUAL_PORTFOLIO_PATH
    return settings.VIRTUAL_PORTFOLIO_PATH.with_name(
        f"virtual_portfolio_{portfolio_name}.json"
    )


def _load_virtual_portfolio(default_cash: float, portfolio_name: str) -> tuple[float, dict]:
    """Load the persisted virtual portfolio, or start fresh with default_cash."""
    try:
        data = json.loads(_portfolio_path(portfolio_name).read_text())
        return float(data["cash"]), dict(data["positions"])
    except (FileNotFoundError, json.JSONDecodeError, OSError, KeyError, TypeError):
        return default_cash, {}


def _save_virtual_portfolio(cash: float, positions: dict, portfolio_name: str) -> None:
    path = _portfolio_path(portfolio_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cash": cash, "positions": positions}, indent=2))


def _size_position(signals: dict, net_liq: float, risk_mode: bool, symbol: str, max_positions: int) -> float:
    """Size a new position. Crypto returns fractional; equities/ETFs return whole shares."""
    if risk_mode:
        slot = (net_liq * 0.90) / max(max_positions, 1)
        if is_crypto(symbol):
            return slot / signals["price"]
        return max(1, int(slot / signals["price"]))
    risk_amt = net_liq * 0.01
    stop_dist = signals.get("atr", 1) * 2
    raw_qty = risk_amt / stop_dist if stop_dist > 0 else 1
    max_qty = int((net_liq * 0.20) / signals["price"])
    return max(1, min(int(raw_qty), max_qty))


# ─────────────────────────────────────────────────────────────────────
# Portfolio display (same as agent.py)
# ─────────────────────────────────────────────────────────────────────


def _print_portfolio(
    cash: float, positions: list[dict], title: str = "PORTFOLIO"
) -> None:
    total_value = cash
    for p in positions:
        total_value += p["qty"] * p["price"]

    if not positions and total_value <= cash:
        print()
        print(_c(f"  ╔══ {title} ═══╗", "bold", "cyan"))
        print(f"  Cash: ${cash:,.2f}")
        return

    _section(title, width=64)
    hdr = f"  {'TICKER':<8} {'QTY':>8} {'PRICE':>10} {'VALUE':>12} {'WEIGHT':>8}  {'P&L':>8}"
    print(_c(hdr, "bold"))
    print(_c("  " + "─" * 58, "dim"))

    rows = []
    for p in positions:
        val = p["qty"] * p["price"]
        pnl = (
            ((p["price"] - p["avg_cost"]) / p["avg_cost"] * 100) if p["avg_cost"] else 0
        )
        rows.append((p["symbol"], p["qty"], p["price"], val, pnl))

    rows.sort(key=lambda r: r[3], reverse=True)
    for sym, qty, price, val, pnl in rows:
        wt = (val / total_value * 100) if total_value else 0
        print(
            f"  {sym:<8} {_fmt_qty(qty):>8} {'$' + format(price, ',.2f'):>10} "
            f"{'$' + format(val, ',.2f'):>12} {wt:>7.1f}%  {_rjust(_pct(pnl), 8)}"
        )

    print(_c("  " + "─" * 58, "dim"))
    cw = (cash / total_value * 100) if total_value else 0
    print(
        f"  {'CASH':<8} {'':>8} {'':>10} {'$' + format(cash, ',.2f'):>12} {cw:>7.1f}%"
    )
    print(
        _c(
            f"  {'TOTAL':<8} {'':>8} {'':>10} {'$' + format(total_value, ',.2f'):>12}",
            "bold",
        )
    )


# ─────────────────────────────────────────────────────────────────────
# Main dry-run
# ─────────────────────────────────────────────────────────────────────


def run_dry_run(
    capital: float = 100_000.0,
    risk_mode: bool = False,
    madmax_mode: bool = False,
    include_crypto: bool = False,
    include_etfs: bool = False,
    portfolio_name: str = "default",
):
    # ── NYSE holiday / weekend gate ──────────────────────────────────────────
    _nyse = mcal.get_calendar("NYSE")
    _today = datetime.now(pytz.timezone("America/New_York")).strftime("%Y-%m-%d")
    _schedule = _nyse.schedule(start_date=_today, end_date=_today)
    if _schedule.empty:
        print(_c(f"\n  ⛔ NYSE closed today ({_today}) — skipping dry run.\n", "yellow"))
        logger.info("NYSE closed today (%s) — dry run skipped.", _today)
        return

    max_positions = settings.SHORTLIST_SIZE if risk_mode else settings.MAX_POSITIONS

    print()
    _box(
        [
            "Pipeline runs exactly as live, but:",
            "  • No IBKR connection needed",
            f"  • Starting capital: ${capital:,.0f}",
            "  • Orders are LOGGED, not placed",
            "  • Virtual portfolio is self-tracked",
        ],
        title=_c("DRY RUN", "bold", "white"),
        width=58,
    )
    print()

    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    settings.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    init_db()

    # ─────────────────────────────────────────────────────────────
    # Step 1 & 2: Load / run morning screen
    # ─────────────────────────────────────────────────────────────
    print(_c("  ▶ Running morning screen …", "cyan"))
    shortlist = run_morning_screen()
    if not shortlist:
        print(_c("  ✖ No candidates found.", "red"))
        return
    print(f"  ✅ Shortlist: {len(shortlist)} candidates")

    # ─────────────────────────────────────────────────────────────
    # Step 3: Load persisted virtual portfolio
    # ─────────────────────────────────────────────────────────────
    virtual_cash, virtual_positions = _load_virtual_portfolio(capital, portfolio_name)
    held_symbols = set(virtual_positions)
    if virtual_positions:
        print(f"  ✅ Loaded existing virtual portfolio: {len(virtual_positions)} position(s), ${virtual_cash:,.2f} cash")

    # ─────────────────────────────────────────────────────────────
    # Step 4: Fetch bars + indicators for candidates + held positions
    # ─────────────────────────────────────────────────────────────
    extra_tickers = extra_universe_tickers(include_crypto, include_etfs)
    candidate_tickers = [s["ticker"] for s in shortlist if s["ticker"] not in held_symbols]
    all_tickers = candidate_tickers + extra_tickers + list(held_symbols)
    bars = fetch_bars(all_tickers, period="1y")

    enriched_candidates = []
    for s in shortlist:
        ticker = s["ticker"]
        if ticker in held_symbols:
            continue
        df = bars.get(ticker)
        if df is None:
            continue
        signals = compute_indicators(df)
        if "error" in signals:
            continue
        entry = dict(s)
        entry.update(signals)
        entry["symbol"] = ticker
        entry["recent_news"] = research_ticker(ticker)
        enriched_candidates.append(entry)

    for ticker in extra_tickers:
        if ticker in held_symbols:
            continue
        df = bars.get(ticker)
        if df is None:
            continue
        signals = compute_indicators(df)
        if "error" in signals:
            continue
        enriched_candidates.append(
            {
                "ticker": ticker,
                "symbol": ticker,
                "price": signals["price"],
                "score": compute_momentum_score(signals),
                **{k: v for k, v in signals.items() if k != "error"},
            }
        )

    if not enriched_candidates and not virtual_positions:
        print(_c("  ✖ No candidates with valid data.", "red"))
        return

    # ---- Enrich held positions with fresh prices/indicators ----
    enriched_positions = []
    for sym, pos in virtual_positions.items():
        df = bars.get(sym)
        if df is None:
            enriched_positions.append({**pos, "symbol": sym})
            continue
        signals = compute_indicators(df)
        if "error" in signals:
            enriched_positions.append({**pos, "symbol": sym})
            continue
        entry = dict(pos)
        entry.update(signals)
        entry["symbol"] = sym
        entry["pnl_pct"] = (
            ((signals["price"] - pos["avg_cost"]) / pos["avg_cost"] * 100)
            if pos["avg_cost"]
            else 0.0
        )
        entry["recent_news"] = research_ticker(sym)
        enriched_positions.append(entry)
        pos["price"] = signals["price"]

    net_liq = virtual_cash + sum(p["qty"] * p["price"] for p in virtual_positions.values())

    # ─────────────────────────────────────────────────────────────
    # Step 5: Print candidates summary
    # ─────────────────────────────────────────────────────────────
    _section("SCREENED CANDIDATES", width=64)
    sorted_cands = sorted(
        enriched_candidates, key=lambda c: c.get("score", 0), reverse=True
    )
    hdr = f"  {'#':>3}  {'TICKER':<8} {'PRICE':>8} {'SCORE':>7} {'RSI':>5} {'ADX':>5} {'VOL':>5}"
    print(_c(hdr, "bold"))
    print(_c("  " + "─" * 48, "dim"))
    for i, c in enumerate(sorted_cands, 1):
        print(
            f"  {i:>3}  {c['ticker']:<8} ${c['price']:>7.2f} "
            f"{c.get('score', 0):>7.1f} {c.get('rsi', 0):>5.0f} "
            f"{c.get('adx', 0):>5.0f} {c.get('vol_ratio', 0):>5.1f}"
        )

    # ─────────────────────────────────────────────────────────────
    # Step 6: Build LLM context and call LLM
    # ─────────────────────────────────────────────────────────────
    open_slots = max(0, max_positions - len(virtual_positions))
    slot_budget = round((virtual_cash * 0.90) / max(open_slots, 1), 2)

    context = {
        "cash": round(virtual_cash, 2),
        "net_liquidation": round(net_liq, 2),
        "deployable_cash": round(
            virtual_cash * (1 - settings.CASH_RESERVE_PCT / 100), 2
        ),
        "max_positions": max_positions,
        "held_count": len(virtual_positions),
        "open_slots": open_slots,
        "slot_budget": slot_budget,
        "positions": enriched_positions,
        "candidates": sorted(enriched_candidates, key=lambda c: c.get("score", 0), reverse=True),
    }

    # DEBUG: print first 3 candidates key fields
    print()
    print(_c("  ▶ Diagnostic — checking why LLM may reject candidates …", "dim"))
    for c in enriched_candidates[:5]:
        sym = c.get("symbol", "?")
        rsi = c.get("rsi", 0)
        adx = c.get("adx", 0)
        macd = c.get("macd_bullish", "?")
        sma200 = c.get("price_above_sma_200", "?")
        ema9 = c.get("ema_9", 0)
        ema21 = c.get("ema_21", 0)
        ema_ok = ema9 > ema21
        print(
            f"    {sym:<6}  RSI={rsi:.0f}  ADX={adx:.0f}  MACD={'✓' if macd else '✖'}  SMA200={'✓' if sma200 else '✖'}  EMA9>{'21=✓' if ema_ok else '21=✖'}"
        )
    print()

    print()
    print(_c("  ▶ Asking LLM to pick the best stocks …", "cyan"))
    llm_prompt = select_llm_prompt(risk_mode, madmax_mode)
    decisions = _call_llm(llm_prompt, json.dumps(context))

    # ─────────────────────────────────────────────────────────────
    # Step 7: Process decisions — SELL/ADD/HOLD on held positions first,
    # then BUY for new candidates.
    # ─────────────────────────────────────────────────────────────
    buys_planned = []
    sells_executed = 0
    adds_executed = 0

    if enriched_positions:
        print()
        _section("DECISIONS", width=64)
        print()

    for d in decisions:
        sym = d.get("symbol", "")
        action = (d.get("action") or "").upper()
        reasoning = d.get("reasoning", "")

        if action == "SELL":
            pos = virtual_positions.pop(sym, None)
            if pos is None:
                print(f"  {_c('SKIP', 'yellow')} SELL {sym} — not held")
                continue
            price = pos["price"]
            pnl = ((price - pos["avg_cost"]) / pos["avg_cost"] * 100) if pos["avg_cost"] else 0
            virtual_cash += pos["qty"] * price
            sells_executed += 1
            print(f"  {_c('SELL', 'red', 'bold'):>6} {sym:<8} {_rjust(_pct(pnl), 8)}  {reasoning[:55]}")

        elif action == "ADD":
            pos = virtual_positions.get(sym)
            signal_data = next((e for e in enriched_positions if e["symbol"] == sym), None)
            if pos is None or signal_data is None:
                continue
            qty = _size_position(signal_data, net_liq, risk_mode, sym, max_positions)
            if qty <= 0:
                continue
            price = signal_data["price"]
            cost = qty * price
            if cost > virtual_cash:
                print(f"  {_c('SKIP', 'yellow')} ADD {sym} — ${cost:,.0f} needed, only ${virtual_cash:,.0f} cash")
                continue
            new_qty = pos["qty"] + qty
            pos["avg_cost"] = (pos["avg_cost"] * pos["qty"] + price * qty) / new_qty
            pos["qty"] = new_qty
            pos["price"] = price
            virtual_cash -= cost
            adds_executed += 1
            print(f"  {_c('ADD', 'cyan', 'bold'):>6} {sym:<8} {_fmt_qty(qty):>6} @ ${price:,.2f}  {reasoning[:55]}")

        elif action == "BUY":
            buys_planned.append(d)

        elif action == "HOLD":
            pos = virtual_positions.get(sym)
            if pos is not None:
                print(f"  {_c('HOLD', 'dim'):>6} {sym:<8} {reasoning[:55]}")

    # Show buy recommendations
    buys_executed = 0
    if buys_planned:
        print()
        _section("AI PICKS", width=64)
        print()

    remaining_buys = len(buys_planned)
    for d in buys_planned:
        sym = d["symbol"]
        reasoning = d.get("reasoning", "")
        remaining_buys -= 1
        cand = next((c for c in enriched_candidates if c["symbol"] == sym), None)
        if cand is None:
            print(f"  {_c('SKIP', 'yellow')} {sym} — no data available")
            continue

        price = cand["price"]
        qty = _size_position(cand, net_liq, risk_mode, sym, max_positions)

        if qty <= 0:
            print(f"  {_c('SKIP', 'yellow')} {sym} — position too small")
            continue

        cost = qty * price
        if cost > virtual_cash:
            print(
                f"  {_c('SKIP', 'yellow')} {sym} — ${cost:,.0f} needed, only ${virtual_cash:,.0f} cash"
            )
            continue

        # Skip if this buy would leave insufficient cash for remaining planned picks.
        # Each remaining pick needs at least half a slot to be meaningful.
        cash_needed_for_rest = remaining_buys * (slot_budget * 0.5)
        if cost + cash_needed_for_rest > virtual_cash:
            print(
                f"  {_c('SKIP', 'yellow')} {sym} — ${cost:,.0f} would crowd out "
                f"{remaining_buys} remaining pick(s) (slot budget ${slot_budget:,.0f})"
            )
            continue

        rsi = cand.get("rsi", 0)
        adx = cand.get("adx", 0)
        macd_tag = "▲" if cand.get("macd_bullish") else "▽"
        print(
            f"  {_c('BUY', 'green', 'bold'):>6} {sym:<8} {_fmt_qty(qty):>6} @ ${price:,.2f}  RSI {rsi:.0f}  ADX {adx:.0f}  {macd_tag}"
        )
        print(f"         {reasoning}")

        # "Confirm" (auto-yes in dry run)
        virtual_positions[sym] = {
            "symbol": sym,
            "qty": qty,
            "avg_cost": price,
            "price": price,
        }
        virtual_cash -= cost
        buys_executed += 1
        print()

    # ─────────────────────────────────────────────────────────────
    # Top up: deploy leftover cash down to the 10% reserve by adding
    # extra whole shares to the cheapest newly-bought positions first.
    # ─────────────────────────────────────────────────────────────
    if buys_planned and virtual_positions:
        cash_reserve = net_liq * 0.10
        bought = sorted(
            (virtual_positions[d["symbol"]] for d in buys_planned if d["symbol"] in virtual_positions),
            key=lambda p: p["avg_cost"],
        )
        progress = True
        while virtual_cash - cash_reserve >= 0 and progress:
            progress = False
            for pos in bought:
                price = pos["price"]
                if price <= 0 or is_crypto(pos["symbol"]):
                    # Crypto already got an exact fractional allocation —
                    # no rounding loss to top up.
                    continue
                if virtual_cash - price < cash_reserve:
                    continue
                pos["qty"] += 1
                virtual_cash -= price
                progress = True

    # ─────────────────────────────────────────────────────────────
    # Step 8: Persist + print final portfolio
    # ─────────────────────────────────────────────────────────────
    _save_virtual_portfolio(virtual_cash, virtual_positions, portfolio_name)

    _section("DRY RUN COMPLETE", width=64)
    print(f"  Sells executed: {sells_executed}")
    print(f"  Adds executed:  {adds_executed}")
    print(f"  Buys executed:  {buys_executed}")
    print(f"  Cash remaining: ${virtual_cash:,.2f}")
    print()

    pos_list = list(virtual_positions.values())
    _print_portfolio(virtual_cash, pos_list, "VIRTUAL PORTFOLIO")

    print()
    print(_c("  To run this for real:", "bold"))
    print("    python main.py --once")
    print()
    print(_c("  To run this for real with risk mode:", "bold"))
    print("    python main.py --once --risk --risk-positions 4")


if __name__ == "__main__":
    capital = 100_000.0
    risk_mode = "--risk" in sys.argv
    madmax_mode = "--madmax" in sys.argv
    if madmax_mode:
        risk_mode = True
    if risk_mode:
        try:
            idx = sys.argv.index("--risk-positions")
            settings.SHORTLIST_SIZE = int(sys.argv[idx + 1])
        except ValueError:
            settings.SHORTLIST_SIZE = 3
        settings.MAX_POSITION_PCT = max(
            settings.MAX_POSITION_PCT, 100 / settings.SHORTLIST_SIZE
        )
    try:
        idx = sys.argv.index("--capital")
        capital = float(sys.argv[idx + 1])
    except ValueError:
        pass
    include_crypto = madmax_mode or "--include-crypto" in sys.argv
    include_etfs = madmax_mode or "--include-etfs" in sys.argv

    portfolio_name = "default"
    try:
        idx = sys.argv.index("--portfolio")
        portfolio_name = sys.argv[idx + 1]
    except ValueError:
        pass

    run_dry_run(
        capital=capital,
        risk_mode=risk_mode,
        madmax_mode=madmax_mode,
        include_crypto=include_crypto,
        include_etfs=include_etfs,
        portfolio_name=portfolio_name,
    )
