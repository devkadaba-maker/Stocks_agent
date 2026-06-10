"""Entry point with APScheduler and CLI flags (--once, --summary)."""

import argparse
import logging
import os
import sys
from pathlib import Path

from agent import run_cycle, run_summary
from apscheduler.schedulers.blocking import BlockingScheduler
from config import settings
from tools.execution import init_db
from tools.screener import run_morning_screen

# Ensure the log directory exists before the FileHandler opens the log file.
settings.LOGS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(settings.LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Trading Agent")
    parser.add_argument(
        "--once", action="store_true", help="Run a single cycle and exit"
    )
    parser.add_argument(
        "--summary", action="store_true", help="Generate EOD summary report"
    )
    parser.add_argument(
        "--screen", action="store_true", help="Run the morning screen and exit"
    )
    args = parser.parse_args()

    # Ensure data/log dirs and the trades DB exist before anything runs.
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    settings.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    init_db()

    if args.once:
        logger.info("CLI: --once flag set, running single cycle")
        run_cycle()
        return

    if args.summary:
        logger.info("CLI: --summary flag set, generating EOD report")
        run_summary()
        return

    if args.screen:
        logger.info("CLI: --screen flag set, running morning screen")
        run_morning_screen()
        return

    logger.info("Starting APScheduler trading agent")
    scheduler = BlockingScheduler(timezone="America/New_York")

    scheduler.add_job(
        run_morning_screen,
        "cron",
        hour=9,
        minute=30,
        day_of_week="mon-fri",
        id="morning_screen",
    )
    scheduler.add_job(
        run_cycle,
        "interval",
        minutes=settings.CYCLE_INTERVAL_MINUTES,
        jitter=60,
        id="trading_cycle",
    )
    scheduler.add_job(
        run_summary,
        "cron",
        hour=15,
        minute=55,
        day_of_week="mon-fri",
        id="eod_summary",
    )

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
        scheduler.shutdown()


if __name__ == "__main__":
    main()
