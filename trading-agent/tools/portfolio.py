"""IBKR REST, fetches account + all open positions."""

import logging

import requests
from config import settings

logger = logging.getLogger(__name__)

# IBKR's self-signed gateway cert means SSL verification is disabled throughout.
_VERIFY = False
_TIMEOUT = 30


def _url(path: str) -> str:
    return f"{settings.IBKR_BASE_URL}{path}"


def _amount(field) -> float:
    """IBKR summary values come as {"amount": x, "currency": ...} or bare numbers."""
    if isinstance(field, dict):
        return float(field.get("amount", 0) or 0)
    try:
        return float(field)
    except (TypeError, ValueError):
        return 0.0


def tickle() -> None:
    """Keep the brokerage session alive (no-op on failure)."""
    try:
        requests.post(_url("/tickle"), verify=_VERIFY, timeout=_TIMEOUT)
    except Exception:
        logger.warning("tickle failed", exc_info=True)


def _resolve_account_id() -> str:
    """Return the configured account id, or discover the first one from IBKR."""
    if settings.IBKR_ACCOUNT_ID:
        return settings.IBKR_ACCOUNT_ID
    try:
        resp = requests.get(_url("/portfolio/accounts"), verify=_VERIFY, timeout=_TIMEOUT)
        accounts = resp.json()
        if accounts:
            first = accounts[0]
            return first.get("accountId") or first.get("id") or ""
    except Exception:
        logger.exception("Failed to resolve account id")
    return ""


def get_account_summary() -> dict:
    """Fetch account summary (net liquidation, cash) from IBKR."""
    account_id = _resolve_account_id()
    if not account_id:
        return {}
    try:
        resp = requests.get(
            _url(f"/portfolio/{account_id}/summary"), verify=_VERIFY, timeout=_TIMEOUT
        )
        data = resp.json()
        return {
            "account_id": account_id,
            "net_liquidation": _amount(data.get("netliquidation")),
            "cash": _amount(data.get("totalcashvalue")),
        }
    except Exception:
        logger.exception("Failed to fetch account summary")
        return {}


def get_open_positions() -> list[dict]:
    """Fetch all open positions as a list of flat dicts."""
    account_id = _resolve_account_id()
    if not account_id:
        return []

    positions: list[dict] = []
    page = 0
    try:
        # IBKR paginates positions in chunks of 30; walk until a short/empty page.
        while True:
            resp = requests.get(
                _url(f"/portfolio/{account_id}/positions/{page}"),
                verify=_VERIFY,
                timeout=_TIMEOUT,
            )
            chunk = resp.json()
            if not chunk:
                break
            for p in chunk:
                positions.append(
                    {
                        "symbol": p.get("contractDesc") or p.get("ticker") or "",
                        "conid": int(p.get("conid", 0) or 0),
                        "qty": p.get("position", 0) or 0,
                        "avg_cost": float(p.get("avgCost", 0) or 0),
                    }
                )
            if len(chunk) < 30:
                break
            page += 1
    except Exception:
        logger.exception("Failed to fetch open positions")
    return positions


def get_portfolio_state() -> dict:
    """Compose a full portfolio snapshot used by the trading cycle.

    Returns {} if the account summary can't be fetched. On success:
    {account_id, net_liquidation, cash, positions: [ {symbol, conid, qty, avg_cost} ]}
    """
    summary = get_account_summary()
    if not summary:
        return {}
    summary["positions"] = get_open_positions()
    return summary


def get_conid(symbol: str) -> int:
    """Resolve a stock symbol to its IBKR contract id, or 0 if not found."""
    try:
        resp = requests.post(
            _url("/iserver/secdef/search"),
            json={"symbol": symbol, "name": False, "secType": "STK"},
            verify=_VERIFY,
            timeout=_TIMEOUT,
        )
        results = resp.json()
        if results:
            return int(results[0].get("conid", 0) or 0)
    except Exception:
        logger.exception("Failed to resolve conid for %s", symbol)
    return 0
