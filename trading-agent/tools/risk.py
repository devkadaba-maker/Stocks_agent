"""Hard gate + ATR-based position sizing."""

import logging

from config import is_crypto, settings

logger = logging.getLogger(__name__)


def check_risk(
    action: str,
    symbol: str,
    qty: float,
    price: float,
    portfolio: dict,
    trades_today: list,
) -> tuple[bool, str]:
    """Validate a proposed order against risk rules.

    Rules are checked in order; the first failure short-circuits with a
    reason. Returns (True, "ok") only if every rule passes.
    """
    action = action.upper()

    def _held(sym: str) -> dict | None:
        """Look up an existing holding whether positions is a list or a dict."""
        positions = portfolio["positions"]
        if isinstance(positions, dict):
            return positions.get(sym)
        return next((p for p in positions if p.get("symbol") == sym), None)

    if action == "BUY":
        order_value = qty * price

        # a. Enough cash (with a 2% buffer for fees/slippage).
        if portfolio["cash"] < order_value * 1.02:
            return False, "Insufficient cash"

        # b. Daily trade limit.
        if len(trades_today) >= settings.MAX_DAILY_TRADES:
            return False, "Daily trade limit reached"

        # c. Single-order position size cap.
        max_position_frac = settings.MAX_POSITION_PCT / 100
        if order_value / portfolio["net_liquidation"] > max_position_frac:
            return False, "Exceeds max position size"

        # d. Combined size cap including any existing holding.
        existing = _held(symbol)
        if existing and existing.get("qty", 0) > 0:
            existing_value = existing["qty"] * price
            total_frac = (existing_value + order_value) / portfolio["net_liquidation"]
            if total_frac > max_position_frac:
                return (
                    False,
                    "Would exceed max position size including existing holding",
                )

        return True, "ok"

    if action == "SELL":
        # a. Must actually hold the position.
        existing = _held(symbol)
        if not existing or existing.get("qty", 0) <= 0:
            return False, "Position not held or already flat"

        return True, "ok"

    return False, f"Unknown action: {action}"


def calculate_position_size(
    atr: float, price: float, portfolio_value: float, symbol: str = ""
) -> float:
    """Size a position so that an ATR-based stop risks ~1% of the portfolio,
    capped by the max position size.

    Crypto (e.g. BTC-USD) is bought fractionally, so the raw quantity is
    returned as-is; equities/ETFs are floored to whole shares (minimum 1)."""
    risk_amount = portfolio_value * 0.01
    stop_distance = atr * settings.ATR_MULTIPLIER

    if stop_distance <= 0 or price <= 0:
        return 0

    raw_qty = risk_amount / stop_distance
    max_qty = (portfolio_value * settings.MAX_POSITION_PCT / 100) / price
    qty = min(raw_qty, max_qty)

    if is_crypto(symbol):
        return qty if qty > 0 else 0.0
    return max(1, int(qty))


def calculate_concentrated_position_size(
    price: float, portfolio_value: float, symbol: str = ""
) -> float:
    """Size a position as an equal slice of deployable capital.

    Deploys ~90% of portfolio_value across SHORTLIST_SIZE positions.
    The remaining ~10% is kept as cash reserve.
    """
    if price <= 0 or settings.SHORTLIST_SIZE <= 0:
        return 0

    slice_dollars = (portfolio_value * 0.90) / settings.SHORTLIST_SIZE
    if is_crypto(symbol):
        return (slice_dollars / price) if slice_dollars >= 1.0 else 0.0
    return int(slice_dollars / price)


def calculate_normal_position_size(
    price: float, portfolio_value: float, max_positions: int, symbol: str = ""
) -> float:
    """Size a position equal-weight across max_positions, deploying ~90%."""
    if price <= 0 or max_positions <= 0:
        return 0
    slice_dollars = (portfolio_value * 0.90) / max_positions
    if is_crypto(symbol):
        return (slice_dollars / price) if slice_dollars >= 1.0 else 0.0
    return int(slice_dollars / price)


def check_stoploss(position: dict, current_price: float) -> bool:
    """Return True if price has dropped below the stop-loss threshold."""
    return current_price < position["avg_cost"] * (1 - settings.STOP_LOSS_PCT / 100)
