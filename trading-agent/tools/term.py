"""Shared ANSI terminal formatting helpers used by backtest.py and the live agent."""

import os
import re
import sys
import textwrap

USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

_C = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "green": "\033[32m",
    "red": "\033[31m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
    "magenta": "\033[35m",
    "blue": "\033[34m",
    "white": "\033[97m",
}


def _c(text: str, *styles: str) -> str:
    """Wrap text in ANSI styles (no-op if not a TTY / NO_COLOR set)."""
    if not USE_COLOR:
        return text
    prefix = "".join(_C[s] for s in styles)
    return f"{prefix}{text}{_C['reset']}"


_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _rjust(s: str, width: int) -> str:
    """Right-justify a string, ignoring ANSI escape codes for width."""
    vis = len(_ANSI_RE.sub("", s))
    return " " * max(0, width - vis) + s


def _pct(value: float, decimals: int = 2, sign: bool = True) -> str:
    """Format a percentage, colored green/red by sign."""
    fmt = f"{{:+.{decimals}f}}%" if sign else f"{{:.{decimals}f}}%"
    text = fmt.format(value)
    return _c(text, "green" if value >= 0 else "red")


def _fmt_qty(qty) -> str:
    """Whole shares as an integer, fractional crypto with trimmed decimals."""
    if float(qty) == int(qty):
        return f"{int(qty):,d}"
    return f"{qty:,.6f}".rstrip("0").rstrip(".")


def _section(title: str, width: int = 70) -> None:
    """Print a bold section header inside a horizontal rule."""
    print()
    print(_c("  " + "─" * width, "dim"))
    print(_c(f"  {title}", "bold", "cyan"))
    print(_c("  " + "─" * width, "dim"))


def _print_decision(action: str, symbol: str, detail: str, reasoning: str) -> None:
    """Print an LLM SELL/ADD/BUY/HOLD decision with a colored tag and wrapped reasoning."""
    colors = {"SELL": "red", "ADD": "cyan", "BUY": "green", "HOLD": "yellow"}
    tag = _c(f"{action:<4s}", "bold", colors.get(action, "white"))
    label = f"  {tag} {_c(f'{symbol:<8s}', 'bold')} {detail}"
    print(label)
    wrapped = textwrap.wrap(reasoning, width=80)
    for line in wrapped:
        print(_c(f"         {line}", "dim"))


def _print_holdings_decisions(rows: list[tuple]) -> None:
    """Print a table covering every currently-held symbol and its decision
    for this review, even if the LLM didn't return one.

    Each row is (symbol, action, pnl_pct_or_None, reasoning).
    """
    colors = {"SELL": "red", "ADD": "cyan", "BUY": "green", "HOLD": "yellow"}
    header = f"  {'TICKER':<8s} {'DECISION':<10s} {'P&L':>8s}  REASONING"
    print(_c(header, "bold"))
    print(_c("  " + "─" * 90, "dim"))
    for symbol, action, pnl_pct, reasoning in rows:
        action_disp = _c(f"{action:<10s}", "bold", colors.get(action, "white"))
        pnl_disp = _pct(pnl_pct) if pnl_pct is not None else f"{'':>8s}"
        reasoning = reasoning or ""
        wrapped = textwrap.wrap(reasoning, width=70) or [""]
        print(f"  {symbol:<8s} {action_disp} {_rjust(pnl_disp, 8)}  {wrapped[0]}")
        for line in wrapped[1:]:
            print(f"  {'':<8s} {'':<10s} {'':>8s}  {line}")


def _print_portfolio_status(rows: list[tuple], cash: float, total_value: float) -> None:
    """Print a snapshot of every position currently held, its market value and
    weight, followed by cash and the total portfolio value.

    Each row is (symbol, qty, price, market_value, pnl_pct_or_None).
    """
    header = (
        f"  {'TICKER':<8s} {'QTY':>8s} {'PRICE':>11s} "
        f"{'VALUE':>13s} {'WEIGHT':>8s} {'P&L':>8s}"
    )
    print(_c(header, "bold"))
    print(_c("  " + "─" * 62, "dim"))
    for symbol, qty, price, market_value, pnl_pct in rows:
        weight = (market_value / total_value * 100) if total_value else 0.0
        pnl_disp = _pct(pnl_pct) if pnl_pct is not None else f"{'':>8s}"
        qty_str = _fmt_qty(qty)
        print(
            f"  {symbol:<8s} {qty_str:>8s} {('$' + format(price, ',.2f')):>11s} "
            f"{('$' + format(market_value, ',.2f')):>13s} {weight:>7.1f}% "
            f"{_rjust(pnl_disp, 8)}"
        )
    print(_c("  " + "─" * 62, "dim"))
    cash_weight = (cash / total_value * 100) if total_value else 0.0
    print(
        f"  {'CASH':<8s} {'':>8s} {'':>11s} "
        f"{('$' + format(cash, ',.2f')):>13s} {cash_weight:>7.1f}%"
    )
    print(
        _c(
            f"  {'TOTAL':<8s} {'':>8s} {'':>11s} "
            f"{('$' + format(total_value, ',.2f')):>13s}",
            "bold",
        )
    )


def _box(lines: list[str], title: str = "", width: int = 56) -> None:
    """Print a list of lines inside a box.

    Lines may contain ANSI codes; padding is computed on the visible
    (escape-stripped) length so columns stay aligned.
    """

    def vislen(s: str) -> int:
        return len(_ANSI_RE.sub("", s))

    top = "╔" + "═" * (width + 2) + "╗"
    bot = "╚" + "═" * (width + 2) + "╝"
    print("  " + _c(top, "cyan"))
    if title:
        pad = width - vislen(title)
        print("  " + _c("║ ", "cyan") + title + " " * (pad + 1) + _c("║", "cyan"))
        print("  " + _c("╟" + "─" * (width + 2) + "╢", "cyan"))
    for line in lines:
        pad = width - vislen(line)
        print("  " + _c("║ ", "cyan") + line + " " * (pad + 1) + _c("║", "cyan"))
    print("  " + _c(bot, "cyan"))
