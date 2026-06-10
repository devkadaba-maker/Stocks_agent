"""Orchestrates one full trading cycle."""

import logging

logger = logging.getLogger(__name__)


def run_cycle() -> None:
    """Run one full trading cycle: review positions, screen, evaluate, execute."""
    logger.info("=== Starting trading cycle ===")
    # TODO: implement cycle orchestration
    # Phase 1 — review existing positions
    # Phase 2 — screen for new candidates
    # Phase 3 — evaluate candidates with LLM
    # Phase 4 — risk gate & sizing
    # Phase 5 — execute orders
    logger.info("=== Cycle complete ===")


def run_summary() -> None:
    """Generate end-of-day summary report."""
    logger.info("Generating EOD summary")
    # TODO: delegate to summary module
