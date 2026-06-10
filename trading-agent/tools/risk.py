"""Hard gate + ATR-based position sizing."""

import logging

logger = logging.getLogger(__name__)


def check_risk_gate(
    account_value: float, daily_pnl: float, max_daily_loss_pct: float
) -> bool:
    """Return False if daily loss exceeds threshold (hard gate)."""
    # TODO: implement
    return True


def calculate_position_size(
    account_value: float,
    atr: float,
    entry_price: float,
    risk_pct: float,
    atr_multiplier: float,
) -> int:
    """Calculate number of shares based on ATR stop distance."""
    # TODO: implement
    return 0
