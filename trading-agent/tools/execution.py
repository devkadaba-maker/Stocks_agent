"""IBKR REST orders + SQLite trade logger."""

import logging
import sqlite3
from datetime import datetime

import requests
from config import settings

logger = logging.getLogger(__name__)


def init_db() -> None:
    """Create the data directory and trades table if they don't exist."""
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(settings.TRADES_DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                cycle_id TEXT,
                symbol TEXT NOT NULL,
                conid INTEGER,
                action TEXT NOT NULL,
                qty INTEGER NOT NULL,
                fill_price REAL,
                order_id TEXT,
                reasoning TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                cycle_id TEXT,
                phase TEXT NOT NULL,
                symbol TEXT,
                action TEXT,
                reasoning TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def place_order(
    symbol: str, conid: int, account_id: str, side: str, qty: float
) -> str:
    """Place a market order via IBKR REST. Returns the order id, or "" on failure."""
    url = f"{settings.IBKR_BASE_URL}/iserver/account/{account_id}/orders"
    body = {
        "orders": [
            {
                "conid": conid,
                "orderType": "MKT",
                "side": side,
                "quantity": qty,
                "tif": "DAY",
            }
        ]
    }

    try:
        resp = requests.post(url, json=body, verify=False, timeout=30)
        data = resp.json()
        logger.info("place_order response for %s: %s", symbol, data)

        # IBKR responds in a few shapes depending on confirmation flow.
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    if item.get("order_id"):
                        return str(item["order_id"])
                    if item.get("id"):
                        return str(item["id"])
        elif isinstance(data, dict):
            if data.get("id"):
                return str(data["id"])
            if data.get("order_id"):
                return str(data["order_id"])

        logger.error("place_order: no order id found in response for %s", symbol)
        return ""
    except Exception:
        logger.exception("place_order failed for %s", symbol)
        return ""


def log_trade(
    cycle_id: str,
    symbol: str,
    conid: int,
    action: str,
    qty: float,
    fill_price: float,
    order_id: str,
    reasoning: str,
) -> None:
    """Insert a completed trade row into the trades table."""
    conn = sqlite3.connect(settings.TRADES_DB_PATH)
    try:
        conn.execute(
            """
            INSERT INTO trades
                (timestamp, cycle_id, symbol, conid, action, qty,
                 fill_price, order_id, reasoning)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.utcnow().isoformat(),
                cycle_id,
                symbol,
                conid,
                action,
                qty,
                fill_price,
                order_id,
                reasoning,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def log_decision(
    cycle_id: str, phase: str, symbol: str, action: str, reasoning: str
) -> None:
    """Insert a record of an LLM decision (including HOLD/blocked), for
    feeding back into future cycles as context."""
    conn = sqlite3.connect(settings.TRADES_DB_PATH)
    try:
        conn.execute(
            """
            INSERT INTO decisions (timestamp, cycle_id, phase, symbol, action, reasoning)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (datetime.utcnow().isoformat(), cycle_id, phase, symbol, action, reasoning),
        )
        conn.commit()
    finally:
        conn.close()


def get_recent_decisions(days: int = 5) -> list[dict]:
    """Return LLM decisions from the last *days* days, most recent first."""
    conn = sqlite3.connect(settings.TRADES_DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT timestamp, phase, symbol, action, reasoning FROM decisions
            WHERE datetime(timestamp) >= datetime('now', ?)
            ORDER BY timestamp DESC
            """,
            (f"-{days} days",),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_todays_trades() -> list[dict]:
    """Return all trades logged today as a list of dicts."""
    conn = sqlite3.connect(settings.TRADES_DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trades WHERE date(timestamp) = date('now')"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def close_all_positions(portfolio: dict, account_id: str) -> None:
    """Liquidate all open positions at end of day, if enabled."""
    if not settings.CLOSE_POSITIONS_EOD:
        return

    positions = portfolio.get("positions", {})
    items = positions.values() if isinstance(positions, dict) else positions

    for position in items:
        qty = position.get("qty", 0)
        if qty > 0:
            place_order(
                position["symbol"],
                position["conid"],
                account_id,
                "SELL",
                qty,  # full size (fractional for crypto)
            )
