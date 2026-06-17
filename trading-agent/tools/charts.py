"""Daily equity log + PNG chart for the live agent."""

import csv
import logging
from datetime import date

from config import settings

logger = logging.getLogger(__name__)


def log_daily_equity(net_liquidation: float, cash: float) -> None:
    """Append today's equity snapshot to EQUITY_LOG_PATH (one row per day)."""
    path = settings.EQUITY_LOG_PATH
    today = date.today().isoformat()
    invested = net_liquidation - cash

    rows = []
    if path.exists():
        with open(path, newline="") as f:
            rows = list(csv.DictReader(f))

    rows = [r for r in rows if r.get("date") != today]
    rows.append(
        {
            "date": today,
            "net_liquidation": f"{net_liquidation:.2f}",
            "cash": f"{cash:.2f}",
            "invested": f"{invested:.2f}",
        }
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "net_liquidation", "cash", "invested"])
        writer.writeheader()
        writer.writerows(rows)


def save_equity_chart() -> None:
    """Render EQUITY_LOG_PATH as a PNG equity curve (overwrites each call)."""
    path = settings.EQUITY_LOG_PATH
    if not path.exists():
        return

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.dates as mdates
        import matplotlib.pyplot as plt
        import pandas as pd
    except ImportError:
        logger.warning("matplotlib/pandas not installed — skipping chart save")
        return

    df = pd.read_csv(path)
    if df.empty:
        return
    df["date"] = pd.to_datetime(df["date"])

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(df["date"], df["net_liquidation"], label="Net liquidation", color="#2563eb", linewidth=1.8)
    ax.plot(df["date"], df["cash"], label="Cash", color="#94a3b8", linewidth=1.2, linestyle="--")
    ax.set_ylabel("USD")
    ax.set_title("Live Agent — Equity Curve")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    fig.autofmt_xdate()

    plt.tight_layout()
    settings.EQUITY_CHART_PATH.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(settings.EQUITY_CHART_PATH, dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info("Equity curve chart saved to %s", settings.EQUITY_CHART_PATH)
