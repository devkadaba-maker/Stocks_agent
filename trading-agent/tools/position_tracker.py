"""Tracks the entry date of each currently-held position.

IBKR's position endpoint doesn't report when a position was opened, so we
keep a small local record (symbol -> ISO entry date) to support hold-days
based logic (e.g. MAX_HOLD_DAYS).
"""

import json
import logging
from datetime import date

from config import settings

logger = logging.getLogger(__name__)


def _load() -> dict:
    try:
        return json.loads(settings.POSITION_ENTRIES_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save(entries: dict) -> None:
    try:
        settings.POSITION_ENTRIES_PATH.parent.mkdir(parents=True, exist_ok=True)
        settings.POSITION_ENTRIES_PATH.write_text(json.dumps(entries, indent=2))
    except OSError:
        logger.warning("Failed to write %s", settings.POSITION_ENTRIES_PATH, exc_info=True)


def record_entry(symbol: str, entry_date: date | None = None) -> None:
    """Record (or refresh) the entry date for a newly-opened position."""
    entries = _load()
    entries[symbol] = (entry_date or date.today()).isoformat()
    _save(entries)


def record_exit(symbol: str) -> None:
    """Remove the entry-date record once a position is fully closed."""
    entries = _load()
    if symbol in entries:
        del entries[symbol]
        _save(entries)


def get_hold_days(symbol: str) -> int | None:
    """Return how many days *symbol* has been held, or None if unknown."""
    entries = _load()
    entry_str = entries.get(symbol)
    if not entry_str:
        return None
    try:
        entry_date = date.fromisoformat(entry_str)
    except ValueError:
        return None
    return (date.today() - entry_date).days


def sync_held_symbols(held_symbols: set[str]) -> None:
    """Drop entries for symbols no longer held, and backfill missing entries
    for symbols held but not tracked (e.g. pre-existing positions) using
    today's date as a conservative entry date."""
    entries = _load()
    changed = False

    for symbol in list(entries.keys()):
        if symbol not in held_symbols:
            del entries[symbol]
            changed = True

    for symbol in held_symbols:
        if symbol not in entries:
            entries[symbol] = date.today().isoformat()
            changed = True

    if changed:
        _save(entries)
