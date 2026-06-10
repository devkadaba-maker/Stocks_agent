"""Entry point with APScheduler and CLI flags (--once, --summary)."""

import argparse
import logging
import sys

from agent import run_cycle, run_summary
from apscheduler.schedulers.blocking import BlockingScheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("logs/agent.log"),
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
    args = parser.parse_args()

    if args.once:
        logger.info("CLI: --once flag set, running single cycle")
        run_cycle()
        return

    if args.summary:
        logger.info("CLI: --summary flag set, generating EOD report")
        run_summary()
        return

    logger.info("Starting APScheduler trading agent")
    scheduler = BlockingScheduler()

    # TODO: schedule run_cycle based on config.CYCLE_INTERVAL_MINUTES
    # scheduler.add_job(run_cycle, 'interval', minutes=settings.CYCLE_INTERVAL_MINUTES)

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
        scheduler.shutdown()


if __name__ == "__main__":
    main()
