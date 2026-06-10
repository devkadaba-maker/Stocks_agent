"""IBKR REST orders + SQLite trade logger."""

import logging

logger = logging.getLogger(__name__)


def place_market_order(ticker: str, quantity: int, action: str) -> dict:
    """Place a market order (BUY or SELL) via IBKR REST API."""
    # TODO: implement
    return {}


def log_trade(ticker: str, action: str, quantity: int, price: float) -> None:
    """Log a completed trade to SQLite trades.db."""

    pass
