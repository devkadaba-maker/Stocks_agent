"""Entry point with APScheduler and CLI flags.

Usage:
    python main.py                         # interactive wizard
    python main.py --once                  # single cycle now
    python main.py --screen                # run morning screen only
    python main.py --risk                  # concentrated mode
    python main.py --madmax                # max aggression (crypto + leveraged ETFs)
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta

# Ensure print() output appears immediately (not just on buffer flush/exit) —
# matters when running under a scheduler with output redirected to a log file.
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

from agent import run_cycle, run_summary
from apscheduler.schedulers.blocking import BlockingScheduler
from config import settings
from tools.execution import init_db
from tools.screener import run_morning_screen
from tools.term import _c

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Live Trading Agent — connects to IBKR and trades automatically."
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=None,
        help="Budget cap — never deploy more than this (default: use full account)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single trading cycle now and exit",
    )
    parser.add_argument(
        "--screen",
        action="store_true",
        help="Run the morning screen only and exit",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Generate the end-of-day summary and exit",
    )
    parser.add_argument(
        "--risk",
        action="store_true",
        help="Concentrated mode: hold fewer, larger positions",
    )
    parser.add_argument(
        "--risk-positions",
        type=int,
        default=3,
        help="Number of concurrent positions in --risk mode (default: 3, range 2-4)",
    )
    parser.add_argument(
        "--madmax",
        action="store_true",
        help="Maximum aggression: crypto + leveraged ETFs allowed",
    )
    parser.add_argument(
        "--include-crypto",
        action="store_true",
        help="Include BTC-USD, ETH-USD, SOL-USD etc. as tradeable candidates",
    )
    parser.add_argument(
        "--include-etfs",
        action="store_true",
        help="Include TQQQ, SOXL, UPRO etc. as tradeable candidates",
    )
    parser.add_argument(
        "--max-positions",
        type=int,
        default=None,
        help="Max concurrent positions in non-risk mode (default: MAX_POSITIONS from config, usually 7)",
    )
    parser.add_argument(
        "--duration-days",
        type=int,
        default=None,
        help="Run the scheduler for this many days, then stop (default: forever)",
    )

    args = parser.parse_args()

    # Ensure dirs + DB
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    settings.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    init_db()

    # Apply risk/madmax config
    if args.risk or args.madmax:
        settings.SHORTLIST_SIZE = args.risk_positions
        settings.MAX_POSITION_PCT = max(
            settings.MAX_POSITION_PCT, 100 / args.risk_positions
        )
        logger.info(
            "Mode: %s, %d position(s), MAX_POSITION_PCT=%.1f",
            "MADMAX" if args.madmax else "RISK",
            args.risk_positions,
            settings.MAX_POSITION_PCT,
        )

    if args.max_positions and not (args.risk or args.madmax):
        settings.MAX_POSITIONS = args.max_positions
        logger.info("MAX_POSITIONS set to %d", args.max_positions)

    include_crypto = args.include_crypto or args.madmax
    include_etfs = args.include_etfs or args.madmax

    # ---- ONE-SHOT COMMANDS ----
    if args.once:
        logger.info("Running single cycle")
        run_cycle(
            risk_mode=args.risk or args.madmax,
            madmax_mode=args.madmax,
            include_crypto=include_crypto,
            include_etfs=include_etfs,
        )
        return

    if args.screen:
        logger.info("Running morning screen")
        run_morning_screen()
        return

    if args.summary:
        logger.info("Generating EOD summary")
        run_summary()
        return

    # ---- SCHEDULER MODE (default) ----
    duration_days = args.duration_days
    logger.info(
        "Starting APScheduler (risk=%s, madmax=%s, duration=%s)",
        args.risk,
        args.madmax,
        f"{duration_days}d" if duration_days else "infinite",
    )

    scheduler = BlockingScheduler(timezone="America/New_York")

    if duration_days:
        stop_at = datetime.now() + timedelta(days=duration_days)

        def _shutdown():
            logger.info("Duration limit reached — shutting down")
            scheduler.shutdown(wait=False)

        scheduler.add_job(_shutdown, "date", run_date=stop_at, id="shutdown")
        logger.info(
            "Will auto-shutdown at %s (%d day(s))",
            stop_at.strftime("%Y-%m-%d %H:%M"),
            duration_days,
        )

    # Morning screen @ 9:30 ET
    scheduler.add_job(
        run_morning_screen,
        "cron",
        hour=9,
        minute=30,
        day_of_week="mon-fri",
        id="morning_screen",
    )

    # Trading cycle @ 11:00 ET (once daily, after market opens)
    scheduler.add_job(
        run_cycle,
        "cron",
        hour=11,
        minute=0,
        day_of_week="mon-fri",
        id="trading_cycle",
        kwargs={
            "risk_mode": args.risk or args.madmax,
            "madmax_mode": args.madmax,
            "include_crypto": include_crypto,
            "include_etfs": include_etfs,
        },
    )

    # EOD summary @ 15:55 ET
    scheduler.add_job(
        run_summary,
        "cron",
        hour=15,
        minute=55,
        day_of_week="mon-fri",
        id="eod_summary",
    )

    print(_c("\n  Trading agent started. Schedule:", "bold", "green"))
    print(f"    {_c('09:30 ET', 'cyan')}  Morning screen (refresh shortlist)")
    print(f"    {_c('11:00 ET', 'cyan')}  Trading cycle (review + execute)")
    print(f"    {_c('15:55 ET', 'cyan')}  EOD summary report")
    print()

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
        scheduler.shutdown()


if __name__ == "__main__":
    main()
