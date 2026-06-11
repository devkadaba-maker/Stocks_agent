"""Orchestrates one full trading cycle."""

import json
import logging
import re
import time
from datetime import datetime
from datetime import time as dt_time

import pytz
from config import settings
from openai import OpenAI
from tools.execution import get_todays_trades, log_trade, place_order
from tools.indicators import compute_indicators
from tools.market_data import fetch_bars
from tools.portfolio import get_conid, get_portfolio_state, tickle
from tools.portfolio_analysis import analyse_portfolio, score_candidate_fit
from tools.risk import calculate_position_size, check_risk, check_stoploss
from tools.web_search import WEB_SEARCH_TOOL_SCHEMA, web_search

logger = logging.getLogger(__name__)

# Minimum deployable cash (after reserve) worth opening new positions for.
MIN_DEPLOYABLE = 500
# Shortlist is considered stale once it is older than this many seconds.
SHORTLIST_MAX_AGE = 24 * 60 * 60
# Maximum number of web-search tool round trips per LLM call.
MAX_TOOL_ITERATIONS = 5


def _call_llm(system_prompt: str, user_content: str) -> list:
    """Call the LLM via OpenRouter, letting it use web_search, and parse its
    final JSON-array reply.

    Returns the parsed list on success, or [] on any error (the raw text
    is logged so a malformed reply can be debugged).
    """
    text = ""
    try:
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=settings.OPENROUTER_API_KEY,
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        for _ in range(MAX_TOOL_ITERATIONS):
            response = client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=messages,
                tools=[WEB_SEARCH_TOOL_SCHEMA],
                temperature=0.2,
            )
            message = response.choices[0].message

            if message.tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": message.content,
                        "tool_calls": [
                            tc.model_dump() for tc in message.tool_calls
                        ],
                    }
                )
                for tool_call in message.tool_calls:
                    args = json.loads(tool_call.function.arguments or "{}")
                    query = args.get("query", "")
                    results = web_search(query)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps(results),
                        }
                    )
                continue

            text = message.content or ""
            break

        # Strip a ```json ... ``` (or plain ``` ... ```) markdown fence if present.
        cleaned = text.strip()
        fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
        if fence:
            cleaned = fence.group(1)

        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        logger.exception("LLM call/parse failed. Raw response: %s", text)
        return []


def _execute_trade(
    cycle_id: str,
    account_id: str,
    symbol: str,
    conid: int,
    side: str,
    qty: int,
    price: float,
    reasoning: str,
    trades_today: list,
) -> None:
    """Place an order, log it, and record it in today's trade list."""
    order_id = place_order(symbol, conid, account_id, side, qty)
    log_trade(cycle_id, symbol, conid, side, qty, price, order_id, reasoning)
    trades_today.append(
        {"symbol": symbol, "action": side, "qty": qty, "order_id": order_id}
    )
    logger.info(
        "Executed %s %d %s @ ~%.2f (order_id=%s)", side, qty, symbol, price, order_id
    )


def _load_fresh_shortlist() -> list | None:
    """Return the shortlist if it exists and is fresh, else None (logs why)."""
    path = settings.SHORTLIST_PATH
    if not path.exists():
        logger.warning("Shortlist not found at %s — skipping Phase 2", path)
        return None

    age = time.time() - path.stat().st_mtime
    if age > SHORTLIST_MAX_AGE:
        logger.warning("Shortlist is %.1fh old (stale) — skipping Phase 2", age / 3600)
        return None

    try:
        data = json.loads(path.read_text())
        logger.info("Loaded shortlist with %d candidates", len(data))
        return data
    except Exception:
        logger.exception("Failed to read shortlist — skipping Phase 2")
        return None


def run_cycle(cycle_id: str | None = None) -> None:
    """Run one full trading cycle: review positions, then deploy new capital.

    The entire body is guarded so a failure in any phase is logged but never
    crashes the scheduler.
    """
    if cycle_id is None:
        cycle_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    logger.info("=== Starting trading cycle %s ===", cycle_id)

    # --- MARKET-HOURS GATE (before any API calls) ---
    et = pytz.timezone("America/New_York")
    now_et = datetime.now(et)
    if now_et.weekday() >= 5:
        logger.info("Skipping cycle — weekend")
        return
    if not (dt_time(9, 30) <= now_et.time() <= dt_time(16, 0)):
        logger.info(
            "Skipping cycle — outside market hours (%s ET)",
            now_et.strftime("%H:%M"),
        )
        return

    try:
        # --- SETUP ---
        tickle()
        portfolio = get_portfolio_state()
        if not portfolio:
            logger.error(
                "Could not fetch portfolio state — aborting cycle %s", cycle_id
            )
            return

        trades_today = get_todays_trades()
        account_id = portfolio["account_id"]
        net_liq = portfolio["net_liquidation"]
        trades_at_start = len(trades_today)

        # --- DEPLOYMENT STATE ---
        invested = net_liq - portfolio["cash"]
        deployment_pct = (invested / net_liq) * 100
        reserve_floor = net_liq * (settings.CASH_RESERVE_PCT / 100)
        deployable = max(0, portfolio["cash"] - reserve_floor)
        at_capacity = deployable < MIN_DEPLOYABLE
        cautious_mode = deployment_pct > settings.DEPLOYMENT_CAUTION_PCT

        logger.info(
            "Deployment %.1f%% | cash $%.0f | deployable $%.0f | cautious=%s",
            deployment_pct,
            portfolio["cash"],
            deployable,
            cautious_mode,
        )

        # --- PHASE 1: review existing positions ---
        enriched_positions = []
        pos_lookup = {}  # symbol -> {"position": ..., "signals": ...}
        for position in portfolio["positions"]:
            symbol = position["symbol"]
            bars = fetch_bars([symbol], period="3mo")
            if symbol not in bars:
                logger.warning("No bars for held %s — skipping", symbol)
                continue
            signals = compute_indicators(bars[symbol])
            if "error" in signals:
                logger.warning("Indicators failed for %s: %s", symbol, signals["error"])
                continue

            stop_loss_hit = check_stoploss(position, signals["price"])
            enriched = dict(position)
            enriched.update(signals)
            enriched["stop_loss_triggered"] = stop_loss_hit
            enriched_positions.append(enriched)
            pos_lookup[symbol] = {"position": position, "signals": signals}

        # --- PORTFOLIO INTELLIGENCE ---
        portfolio_intel = analyse_portfolio(portfolio, enriched_positions)
        logger.info(
            "Portfolio health: %d/100 | %d risk tier(s) | dominant: %s %.1f%%",
            portfolio_intel["health_score"],
            portfolio_intel["tier_count"],
            portfolio_intel["dominant_tier"],
            portfolio_intel["dominant_tier_pct"],
        )

        if enriched_positions:
            phase1_context = {
                "portfolio_value": net_liq,
                "cash": portfolio["cash"],
                "deployment_pct": round(deployment_pct, 1),
                "portfolio_intelligence": portfolio_intel,
                "positions": enriched_positions,
            }
            phase1_prompt = settings.PHASE1_PROMPT_PATH.read_text()
            decisions = _call_llm(phase1_prompt, json.dumps(phase1_context))

            for decision in decisions:
                symbol = decision.get("symbol")
                action = (decision.get("action") or "").upper()
                reasoning = decision.get("reasoning", "")
                entry = pos_lookup.get(symbol)
                if not entry:
                    continue
                position = entry["position"]
                signals = entry["signals"]
                conid = position.get("conid", 0)

                if action == "SELL":
                    qty = int(position["qty"])
                    ok, reason = check_risk(
                        "SELL", symbol, qty, signals["price"], portfolio, trades_today
                    )
                    if ok:
                        _execute_trade(
                            cycle_id,
                            account_id,
                            symbol,
                            conid,
                            "SELL",
                            qty,
                            signals["price"],
                            reasoning,
                            trades_today,
                        )
                    else:
                        logger.info("Skipping SELL %s: %s", symbol, reason)

                elif action == "ADD":
                    qty = calculate_position_size(
                        signals["atr"], signals["price"], net_liq
                    )
                    ok, reason = check_risk(
                        "BUY", symbol, qty, signals["price"], portfolio, trades_today
                    )
                    if ok:
                        _execute_trade(
                            cycle_id,
                            account_id,
                            symbol,
                            conid,
                            "BUY",
                            qty,
                            signals["price"],
                            reasoning,
                            trades_today,
                        )
                    else:
                        logger.info("Skipping ADD %s: %s", symbol, reason)
        else:
            logger.info("No reviewable positions for Phase 1")

        # --- PHASE 2: new candidates ---
        if at_capacity:
            logger.info(
                "Skipping Phase 2 — portfolio at capacity (%.1f%% deployed, "
                "$%.0f deployable after reserve)",
                deployment_pct,
                deployable,
            )
        else:
            shortlist = _load_fresh_shortlist()
            if shortlist is not None:
                # Consume the shortlist: delete it so subsequent cycles
                # don't re-use stale candidates from this morning's screen.
                # The next morning screen will write a fresh one.
                try:
                    settings.SHORTLIST_PATH.unlink(missing_ok=True)
                except Exception:
                    pass
                held_symbols = {p["symbol"] for p in portfolio["positions"]}
                candidates = [s for s in shortlist if s["ticker"] not in held_symbols]

                enriched_candidates = []
                cand_lookup = {}  # symbol -> signals
                for candidate in candidates:
                    ticker = candidate["ticker"]
                    bars = fetch_bars([ticker], period="3mo")
                    if ticker not in bars:
                        continue
                    signals = compute_indicators(bars[ticker])
                    if "error" in signals:
                        continue
                    enriched = dict(candidate)
                    enriched.update(signals)
                    enriched["symbol"] = ticker
                    fit = score_candidate_fit(ticker, signals, portfolio_intel)
                    enriched["risk_tier"] = fit["risk_tier"]
                    enriched["portfolio_fit_score"] = fit["fit_score"]
                    enriched["portfolio_fit_note"] = fit["fit_note"]
                    enriched["adds_diversification"] = fit["adds_diversification"]
                    enriched_candidates.append(enriched)
                    cand_lookup[ticker] = signals

                phase2_context = {
                    "deployable_cash": round(deployable, 2),
                    "portfolio_value": net_liq,
                    "deployment_pct": round(deployment_pct, 1),
                    "cautious_mode": cautious_mode,
                    "portfolio_intelligence": portfolio_intel,
                    "candidates": enriched_candidates,
                }
                phase2_prompt = settings.PHASE2_PROMPT_PATH.read_text()
                decisions = _call_llm(phase2_prompt, json.dumps(phase2_context))

                for decision in decisions:
                    if (decision.get("action") or "").upper() != "BUY":
                        continue
                    symbol = decision.get("symbol")
                    reasoning = decision.get("reasoning", "")
                    signals = cand_lookup.get(symbol)
                    if not signals:
                        continue

                    conid = get_conid(symbol)
                    if conid == 0:
                        logger.warning("No conid for %s — skipping BUY", symbol)
                        continue

                    qty = calculate_position_size(
                        signals["atr"], signals["price"], net_liq
                    )
                    ok, reason = check_risk(
                        "BUY", symbol, qty, signals["price"], portfolio, trades_today
                    )
                    if ok:
                        _execute_trade(
                            cycle_id,
                            account_id,
                            symbol,
                            conid,
                            "BUY",
                            qty,
                            signals["price"],
                            reasoning,
                            trades_today,
                        )
                    else:
                        logger.info("Skipping BUY %s: %s", symbol, reason)

        executed = len(trades_today) - trades_at_start
        logger.info(
            "=== Cycle %s complete — %d trade(s) executed ===", cycle_id, executed
        )
    except Exception:
        logger.exception("Cycle %s crashed", cycle_id)


def run_summary() -> None:
    """Generate and print the end-of-day summary report."""
    from tools.summary import generate_report

    print(generate_report())
