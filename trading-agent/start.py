#!/usr/bin/env python3
"""Schedules the daily dry-run — interactive mode.

Running this script prompts you through the configuration instead of
requiring CLI flags. It then starts a scheduler that fires dry_run.py's
full pipeline every weekday at 11:00 AM Eastern.
"""

import logging
import sys

# Ensure print() output appears immediately (not just on buffer flush/exit) —
# matters when running under a scheduler with output redirected to a log file.
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

from apscheduler.schedulers.blocking import BlockingScheduler
from config import settings
from dry_run import run_dry_run
from tools.term import _box, _c

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("start")


def _ask(prompt: str, default: str = "") -> str:
    """Prompt the user and return the trimmed input."""
    if default:
        full = f"{prompt} [{default}]: "
    else:
        full = f"{prompt}: "
    try:
        return input(full).strip() or default
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)


def _ask_yesno(prompt: str, default: bool = False) -> bool:
    """Ask a yes/no question and return True for yes, False for no."""
    hint = "y/N" if not default else "Y/n"
    raw = _ask(f"{prompt} ({hint})", "y" if default else "n")
    return raw.lower().startswith("y")


def _ask_float(prompt: str, default: float, minimum: float = 0) -> float:
    """Prompt for a numeric value, retrying on invalid input."""
    raw = _ask(prompt, str(default))
    try:
        val = float(raw)
        if val >= minimum:
            return val
        print(_c(f"  Value must be at least {minimum:,.0f}.", "yellow"))
    except ValueError:
        print(_c(f"  Invalid number, using default {default:,.0f}.", "yellow"))
    return default


def _ask_int(prompt: str, default: int, minimum: int = 1) -> int:
    """Prompt for an integer, retrying on invalid input."""
    raw = _ask(prompt, str(default))
    try:
        val = int(raw)
        if val >= minimum:
            return val
        print(_c(f"  Value must be at least {minimum}.", "yellow"))
    except ValueError:
        print(_c(f"  Invalid integer, using default {default}.", "yellow"))
    return default


def main() -> None:
    _box(
        [
            "Configure the trading agent below. Press Ctrl+C at any",
            "prompt to abort. Defaults are shown in brackets — hit",
            "Enter to accept them.",
        ],
        title=_c("TRADING AGENT — STARTUP", "bold", "white"),
    )
    print()

    # ── Capital ──────────────────────────────────────────────────────
    capital = _ask_float("Starting capital ($)", 100_000.0, minimum=1)
    print()

    # ── Portfolio name ───────────────────────────────────────────────
    # Each name gets its own persisted state file (data/virtual_portfolio_<name>.json),
    # so e.g. a "competition" book and a "personal" book don't collide.
    portfolio_name = _ask("Portfolio name (separate state per name)", "default")
    print()

    # ── Mode selection ───────────────────────────────────────────────
    _box(
        [
            "  1 — Diversified (default) — spread capital across many",
            "       positions with balanced sizing.",
            "  2 — Risk / concentrated — fewer positions, larger bets.",
            f"       Implies --risk-positions={_c('3', 'bold', 'cyan')}.",
            "  3 — Mad Max — high-conviction concentrated mode.",
            "       Implies risk mode + crypto + leveraged ETFs +",
            f"       --risk-positions={_c('3', 'bold', 'cyan')}.",
        ],
        title=_c("MODE", "bold", "white"),
    )
    mode_raw = _ask("Select mode (1 / 2 / 3)", "1")
    print()

    madmax = mode_raw.strip() == "3"
    risk = madmax or mode_raw.strip() == "2"

    # ── Risk positions ───────────────────────────────────────────────
    risk_positions = 3
    if risk:
        risk_positions = _ask_int(
            "Concurrent positions in risk/madmax mode",
            3,
            minimum=1,
        )
        print()

    # ── Crypto / ETFs (only asked when not already implied by madmax) ─
    include_crypto = madmax
    include_etfs = madmax
    if not madmax:
        include_crypto = _ask_yesno("Surface crypto as tradeable candidates", False)
        include_etfs = _ask_yesno(
            "Surface leveraged ETFs as tradeable candidates", False
        )
        print()

    # ── Max positions (diversified mode only) ────────────────────────
    max_positions: int | None = None
    if not risk:
        max_positions = _ask_int(
            "Max concurrent positions (0 = no limit)", 0, minimum=0
        )
        max_positions = max_positions if max_positions > 0 else None
        print()

    # ── Run immediately? ─────────────────────────────────────────────
    run_now = _ask_yesno("Run the pipeline once immediately, then schedule daily", True)

    # ── Summary & confirm ────────────────────────────────────────────
    mode_label = "MAD MAX" if madmax else "RISK" if risk else "DIVERSIFIED"
    summary_lines = [
        f"Mode:       {_c(mode_label, 'bold', 'white')}",
        f"Portfolio:  {portfolio_name}",
        f"Capital:    ${capital:,.0f}",
        f"Run now:    {'yes' if run_now else 'no'}",
    ]
    if risk:
        summary_lines.append(f"Risk pos:   {risk_positions}")
    if include_crypto:
        summary_lines.append(f"Crypto:     yes")
    if include_etfs:
        summary_lines.append(f"ETFs:       yes")
    if max_positions is not None:
        summary_lines.append(f"Max pos:    {max_positions}")

    _box(summary_lines, title=_c("CONFIRMATION", "bold", "white"))
    if not _ask_yesno("Proceed with these settings", True):
        print(_c("  Aborted.", "yellow"))
        sys.exit(0)
    print()

    # ── Apply settings ───────────────────────────────────────────────
    if risk:
        settings.SHORTLIST_SIZE = risk_positions
        settings.MAX_POSITION_PCT = max(settings.MAX_POSITION_PCT, 100 / risk_positions)

    if max_positions is not None and not risk:
        settings.MAX_POSITIONS = max_positions

    # ── Initial run ──────────────────────────────────────────────────
    if run_now:
        logger.info("Running dry run immediately")
        run_dry_run(
            capital=capital,
            risk_mode=risk,
            madmax_mode=madmax,
            include_crypto=include_crypto,
            include_etfs=include_etfs,
            portfolio_name=portfolio_name,
        )

    # ── Schedule ─────────────────────────────────────────────────────
    scheduler = BlockingScheduler(timezone="America/New_York")
    scheduler.add_job(
        run_dry_run,
        "cron",
        hour=11,
        minute=0,
        day_of_week="mon-fri",
        id="daily_dry_run",
        kwargs={
            "capital": capital,
            "risk_mode": risk,
            "madmax_mode": madmax,
            "include_crypto": include_crypto,
            "include_etfs": include_etfs,
            "portfolio_name": portfolio_name,
        },
    )

    _box(
        [
            f"Mode: {_c(mode_label, 'bold', 'white')}   Capital: ${capital:,.0f}",
            "Scheduled daily at 11:00 AM ET, Mon-Fri.",
            "Copy the printed BUY/SELL/HOLD/ADD decisions into the",
            "competition manually — no live orders are placed.",
            "Press Ctrl+C to stop.",
        ],
        title=_c("TRADING AGENT — SCHEDULER", "bold", "white"),
    )

    logger.info("Scheduler started — next dry run at 11:00 AM ET on a weekday")
    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
        scheduler.shutdown()


if __name__ == "__main__":
    main()
