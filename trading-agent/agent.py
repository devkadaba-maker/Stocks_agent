"""Orchestrates one trading cycle for REAL trading.

Flow:
  1. Load morning screen shortlist (or run one if stale).
  2. Compute indicators + research for each candidate.
  3. LLM picks the best N stocks (N = max concurrent positions).
  4. Show picks with reasoning; user confirms each BUY.
  5. Place real IBKR orders.
"""

import json
import logging
import re
import sys
import time
from datetime import datetime
from datetime import time as dt_time

import pytz
from config import extra_universe_tickers, settings
from openai import OpenAI
from tools import position_tracker
from tools.execution import (
    get_todays_trades,
    log_decision,
    log_trade,
    place_order,
)
from tools.indicators import compute_indicators
from tools.market_data import fetch_bars
from tools.portfolio import get_conid, get_portfolio_state, tickle
from tools.portfolio_analysis import score_candidate_fit
from tools.risk import (
    calculate_concentrated_position_size,
    calculate_position_size,
    check_risk,
)
from tools.llm_prompts import select_llm_prompt
from tools.screener import compute_momentum_score, run_morning_screen
from tools.term import _box, _c, _fmt_qty, _pct, _rjust, _section
from tools.web_search import research_ticker

logger = logging.getLogger(__name__)

SHORTLIST_MAX_AGE = 24 * 60 * 60  # 24 hours

# ─────────────────────────────────────────────────────────────────────
# LLM helpers
# ─────────────────────────────────────────────────────────────────────


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
        logger.exception("LLM call/parse failed. Raw response: %s", text)
        return []


def _confirm_trade(symbol: str, qty: float, price: float) -> bool:
    """Always prompt for confirmation on real trades."""
    if not sys.stdin.isatty():
        return True
    answer = input(
        f"  {_c('BUY', 'bold', 'green')} {_fmt_qty(qty)} × {symbol} @ ~${price:.2f}  "
        f"{_c('[Y/n]', 'dim')}: "
    ).strip().lower()
    return answer in ("", "y", "yes")


# ─────────────────────────────────────────────────────────────────────
# Shortlist loading
# ─────────────────────────────────────────────────────────────────────


def _load_or_run_shortlist() -> list | None:
    """Return the shortlist if fresh and non-empty, otherwise run a morning screen."""
    path = settings.SHORTLIST_PATH
    if path.exists():
        age = time.time() - path.stat().st_mtime
        if age < SHORTLIST_MAX_AGE:
            try:
                data = json.loads(path.read_text())
                if data:
                    logger.info("Loaded existing shortlist (%d candidates)", len(data))
                    return data
            except Exception:
                pass
    print(_c("\n  ▶ Running morning screen …", "cyan"))
    shortlist = run_morning_screen()
    if not shortlist:
        print(_c("  ✖ No candidates found. Nothing to trade.", "red"))
        return None
    return shortlist


# ─────────────────────────────────────────────────────────────────────
# Sizing
# ─────────────────────────────────────────────────────────────────────


def _size_position(signals: dict, net_liq: float, risk_mode: bool, symbol: str = "") -> float:
    """Size a new position. Crypto returns fractional; equities/ETFs return whole shares."""
    symbol = symbol or signals.get("symbol", "")
    if risk_mode:
        return calculate_concentrated_position_size(signals["price"], net_liq, symbol)
    return calculate_position_size(signals["atr"], signals["price"], net_liq, symbol)


# ─────────────────────────────────────────────────────────────────────
# LLM Prompt
# ─────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────
# MAIN CYCLE
# ─────────────────────────────────────────────────────────────────────


def run_cycle(
    cycle_id: str | None = None,
    risk_mode: bool = False,
    madmax_mode: bool = False,
    include_crypto: bool = False,
    include_etfs: bool = False,
) -> None:
    """Run one trading cycle.

    Steps:
      1. Fetch live portfolio from IBKR.
      2. Load or generate shortlist.
      3. Fetch bars + compute indicators for all candidates.
      4. Enrich positions and candidates with technicals and news.
      5. Call LLM for SELL/ADD/BUY decisions.
      6. Execute SELLs and ADDs automatically; confirm each BUY.
      7. Print the final portfolio state.
    """
    if cycle_id is None:
        cycle_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    if madmax_mode:
        risk_mode = True
        include_crypto = True
        include_etfs = True

    extra_tickers = extra_universe_tickers(include_crypto, include_etfs)
    max_positions = settings.SHORTLIST_SIZE if risk_mode else settings.MAX_POSITIONS

    logger.info(
        "=== Starting trading cycle %s (risk_mode=%s, madmax_mode=%s) ===",
        cycle_id, risk_mode, madmax_mode,
    )

    # ---- MARKET-HOURS GATE ----
    et = pytz.timezone("America/New_York")
    now_et = datetime.now(et)
    if now_et.weekday() >= 5:
        print(_c("\n  ⛔ Weekend — market closed.", "yellow"))
        return
    if not (dt_time(9, 30) <= now_et.time() <= dt_time(16, 0)):
        print(f"\n  ⏰ Outside market hours ({now_et.strftime('%H:%M')} ET). Skipping.\n")
        return

    try:
        tickle()
        portfolio = get_portfolio_state()
        if not portfolio:
            print(_c("\n  ✖ Could not reach IBKR. Check your connection.", "red"))
            return

        trades_today = get_todays_trades()
        account_id = portfolio["account_id"]
        net_liq = portfolio["net_liquidation"]
        cash = portfolio["cash"]
        held_positions = portfolio["positions"]
        held_symbols = {p["symbol"] for p in held_positions}
        deployable = max(0, cash - net_liq * settings.CASH_RESERVE_PCT / 100)

        # Sync position tracker
        position_tracker.sync_held_symbols(held_symbols)

        # ---- BANNER ----
        if madmax_mode:
            risk_label = _c("🔥 MAD MAX", "magenta", "bold")
        elif risk_mode:
            risk_label = _c("⚡ CONCENTRATED", "red", "bold")
        else:
            risk_label = _c("🌱 DIVERSIFIED", "green")

        _box(
            [
                f"Time            {now_et.strftime('%a %b %d  %H:%M ET')}",
                f"Net liq         ${net_liq:,.2f}",
                f"Cash            ${cash:,.2f}",
                f"Deployable      ${deployable:,.2f}",
                f"Held            {len(held_positions)} / {max_positions}",
                f"Mode            {risk_label}",
            ],
            title=_c("TRADING CYCLE", "bold", "white"),
        )
        print()

        # ---- LOAD SHORTLIST ----
        shortlist = _load_or_run_shortlist()
        if not shortlist:
            # Still run Phase 1 (review existing positions) even with no shortlist
            pass

        # ---- FETCH BARS + INDICATORS FOR HELD POSITIONS ----
        enriched_positions = []
        if held_positions:
            held_bars = fetch_bars(list(held_symbols), period="1y")
            for p in held_positions:
                sym = p["symbol"]
                df = held_bars.get(sym)
                if df is None:
                    continue
                signals = compute_indicators(df)
                if "error" in signals:
                    continue
                entry = dict(p)
                entry.update(signals)
                entry["symbol"] = sym
                entry["pnl_pct"] = (
                    ((signals["price"] - p["avg_cost"]) / p["avg_cost"] * 100)
                    if p["avg_cost"]
                    else 0.0
                )
                entry["hold_days"] = position_tracker.get_hold_days(sym) or 0
                entry["stop_loss_triggered"] = signals["price"] < p["avg_cost"] * (1 - settings.STOP_LOSS_PCT / 100)
                news = research_ticker(sym)
                entry["recent_news"] = news
                enriched_positions.append(entry)

        # ---- FETCH BARS + INDICATORS FOR CANDIDATES ----
        enriched_candidates = []
        if shortlist:
            candidate_tickers = [s["ticker"] for s in shortlist if s["ticker"] not in held_symbols]
            if candidate_tickers:
                all_candidate_tickers = candidate_tickers + extra_tickers
                bars = fetch_bars(all_candidate_tickers, period="1y")
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
                    news = research_ticker(ticker)
                    entry["recent_news"] = news
                    enriched_candidates.append(entry)

                # Add extra-universe (crypto / leveraged ETFs) candidates
                for ticker in extra_tickers:
                    if ticker in held_symbols:
                        continue
                    df = bars.get(ticker)
                    if df is None:
                        continue
                    signals = compute_indicators(df)
                    if "error" in signals:
                        continue
                    enriched_candidates.append({
                        "ticker": ticker,
                        "symbol": ticker,
                        "price": signals["price"],
                        "score": compute_momentum_score(signals),
                        **{k: v for k, v in signals.items() if k != "error"},
                    })

        # ---- LLM CONTEXT ----
        open_slots = max(0, max_positions - len(held_positions))
        slot_budget = round(deployable / max(open_slots, 1), 2)

        context = {
            "cash": round(cash, 2),
            "net_liquidation": round(net_liq, 2),
            "deployable_cash": round(deployable, 2),
            "max_positions": max_positions,
            "held_count": len(held_positions),
            "open_slots": open_slots,
            "slot_budget": slot_budget,
            "positions": enriched_positions,
            "candidates": sorted(enriched_candidates, key=lambda c: c.get("score", 0), reverse=True),
        }

        llm_prompt = select_llm_prompt(risk_mode, madmax_mode)
        decisions = _call_llm(llm_prompt, json.dumps(context))

        # ---- PHASE 1: Process SELL/ADD decisions ----
        sells_executed = 0
        adds_executed = 0
        buys_planned = []

        print()
        _section("DECISIONS", width=64)
        print()
        for d in decisions:
            sym = d.get("symbol", "")
            action = (d.get("action") or "").upper()
            reasoning = d.get("reasoning", "")
            log_decision(cycle_id, "phase1", sym, action, reasoning)
            signal_data = next((e for e in enriched_positions + enriched_candidates if e.get("symbol") == sym), None)

            if action == "SELL":
                pos = next((p for p in held_positions if p["symbol"] == sym), None)
                if not pos:
                    print(f"  {_c('SKIP', 'yellow')} SELL {sym} — not held")
                    continue
                ok, reason = check_risk("SELL", sym, pos["qty"], signal_data["price"] if signal_data else pos["avg_cost"], portfolio, trades_today)
                if ok:
                    order_id = place_order(sym, pos.get("conid", 0), account_id, "SELL", pos["qty"])
                    log_trade(cycle_id, sym, pos.get("conid", 0), "SELL", pos["qty"], signal_data["price"] if signal_data else pos["avg_cost"], order_id, reasoning)
                    position_tracker.record_exit(sym)
                    pnl = ((signal_data["price"] - pos["avg_cost"]) / pos["avg_cost"] * 100) if signal_data and pos["avg_cost"] else 0
                    print(f"  {_c('SELL', 'red', 'bold'):>6} {sym:<8} {_rjust(_pct(pnl), 8)}  {reasoning[:55]}")
                    sells_executed += 1
                    held_symbols.discard(sym)

            elif action == "ADD":
                pos = next((p for p in held_positions if p["symbol"] == sym), None)
                if not pos:
                    continue
                qty = _size_position(signal_data, net_liq, risk_mode, sym) if signal_data else 0
                if qty <= 0:
                    continue
                ok, reason = check_risk("BUY", sym, qty, signal_data["price"], portfolio, trades_today)
                if ok:
                    order_id = place_order(sym, pos.get("conid", 0), account_id, "BUY", qty)
                    log_trade(cycle_id, sym, pos.get("conid", 0), "BUY", qty, signal_data["price"], order_id, reasoning)
                    position_tracker.record_entry(sym)
                    print(f"  {_c('ADD', 'cyan', 'bold'):>6} {sym:<8} {_fmt_qty(qty):>6} @ ${signal_data['price']:,.2f}  {reasoning[:55]}")
                    adds_executed += 1

            elif action == "BUY":
                buys_planned.append(d)

        # ---- PHASE 2: Confirm BUY decisions ----
        buys_executed = 0
        if buys_planned:
            print()
            _section("BUY RECOMMENDATIONS", width=64)
            print()
            for d in buys_planned:
                sym = d.get("symbol", "")
                reasoning = d.get("reasoning", "")
                cand = next((c for c in enriched_candidates if c.get("symbol") == sym), None)
                if cand is None:
                    print(f"  {_c('SKIP', 'yellow')} BUY {sym} — no data available")
                    continue

                price = cand["price"]
                qty = _size_position(cand, net_liq, risk_mode, sym)
                if qty <= 0:
                    print(f"  {_c('SKIP', 'yellow')} BUY {sym} — position too small (1 sh = ${price:,.2f})")
                    continue

                # Show the pick
                rsi = cand.get("rsi", 0)
                adx = cand.get("adx", 0)
                macd_tag = "▲" if cand.get("macd_bullish") else "▽"
                print(f"  {_c('PICK', 'green', 'bold'):>6} {sym:<8} {_fmt_qty(qty):>6} @ ${price:,.2f}  RSI {rsi:.0f}  ADX {adx:.0f}  {macd_tag}")
                print(f"         {reasoning}")

                # Confirm
                if not _confirm_trade(sym, qty, price):
                    print(f"         {_c('↪ skipped', 'dim')}")
                    continue

                # Resolve conid, check risk, place order
                conid = get_conid(sym)
                if conid == 0:
                    print(f"         {_c('✖ could not resolve conid', 'red')}")
                    continue
                ok, reason = check_risk("BUY", sym, qty, price, portfolio, trades_today)
                if not ok:
                    print(f"         {_c(f'✖ risk check failed: {reason}', 'red')}")
                    continue
                order_id = place_order(sym, conid, account_id, "BUY", qty)
                if order_id:
                    log_trade(cycle_id, sym, conid, "BUY", qty, price, order_id, reasoning)
                    position_tracker.record_entry(sym)
                    print(f"         {_c('✓ order placed', 'green')}  id={order_id}")
                    buys_executed += 1
                    trades_today.append({"symbol": sym, "action": "BUY", "qty": qty, "order_id": order_id})

        # ---- SUMMARY ----
        total_changes = sells_executed + adds_executed + buys_executed
        if total_changes > 0:
            print()
            _section("CHANGES MADE", width=64)
            print(f"  Sells: {sells_executed}   Adds: {adds_executed}   Buys: {buys_executed}")
        else:
            print()
            print(_c("  No changes needed this cycle.", "dim"))

        # ---- FINAL PORTFOLIO SNAPSHOT ----
        print()
        final_portfolio = get_portfolio_state()
        if final_portfolio and final_portfolio["positions"]:
            final_held = final_portfolio["positions"]
            final_bars = fetch_bars(list({p["symbol"] for p in final_held}), period="1mo")
            final_positions = []
            for p in final_held:
                sym = p["symbol"]
                sig = final_bars.get(sym)
                price = sig["Close"].iloc[-1] if sig is not None and not sig.empty else p["avg_cost"]
                final_positions.append({
                    "symbol": sym,
                    "qty": p["qty"],
                    "price": float(price),
                    "avg_cost": p["avg_cost"],
                })
            _print_portfolio(final_portfolio["cash"], final_positions, title="PORTFOLIO STATUS")
        else:
            print(_c("  Portfolio is empty — all cash.", "bold"))
            print(f"  Cash: ${final_portfolio['cash']:,.2f}" if final_portfolio else "  Could not fetch portfolio.")

        logger.info(
            "=== Cycle %s complete — %d trade(s) executed ===",
            cycle_id, total_changes,
        )

    except Exception:
        logger.exception("Cycle %s crashed", cycle_id)


def run_summary() -> None:
    """Generate and print the end-of-day summary report."""
    from tools.summary import generate_report

    print(generate_report())


# ─────────────────────────────────────────────────────────────────────
# Helper: print portfolio (inline, no cycle needed)
# ─────────────────────────────────────────────────────────────────────


def _print_portfolio(cash: float, positions: list[dict], title: str = "PORTFOLIO") -> None:
    """Render a clean table of current holdings + cash + total."""
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
        pnl = ((p["price"] - p["avg_cost"]) / p["avg_cost"] * 100) if p["avg_cost"] else 0
        rows.append((p["symbol"], p["qty"], p["price"], val, pnl))

    rows.sort(key=lambda r: r[3], reverse=True)
    for sym, qty, price, val, pnl in rows:
        wt = (val / total_value * 100) if total_value else 0
        qty_s = _fmt_qty(qty)
        pnl_s = _pct(pnl)
        print(
            f"  {sym:<8} {qty_s:>8} {'$' + format(price, ',.2f'):>10} "
            f"{'$' + format(val, ',.2f'):>12} {wt:>7.1f}%  {_rjust(pnl_s, 8)}"
        )

    print(_c("  " + "─" * 58, "dim"))
    cw = (cash / total_value * 100) if total_value else 0
    print(
        f"  {'CASH':<8} {'':>8} {'':>10} "
        f"{'$' + format(cash, ',.2f'):>12} {cw:>7.1f}%"
    )
    print(
        _c(
            f"  {'TOTAL':<8} {'':>8} {'':>10} {'$' + format(total_value, ',.2f'):>12}",
            "bold",
        )
    )
