"""EOD report generator."""

import json
import logging
from datetime import date

from openai import OpenAI

from config import settings
from tools.execution import get_todays_trades
from tools.portfolio import get_portfolio_state

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = "You are a trading agent assistant generating an end-of-day report."

_INSTRUCTION = (
    "Write a ~150 word plain English summary of today's trading activity and "
    "reasoning.\n"
    "Then produce a block formatted exactly like this:\n"
    "=== INVESTOPEDIA ENTRY ===\n"
    "BUY AAPL 10 @ $192.50\n"
    "SELL TSLA 5 @ $248.00\n"
    "=== END ===\n"
    "If no trades were made today, say so in the summary and leave the entry "
    "block empty."
)


def generate_report() -> str:
    """Build an end-of-day report via the LLM, save it, print it, and return it."""
    trades = get_todays_trades()
    portfolio = get_portfolio_state() or {}
    today = date.today().isoformat()

    context = {
        "date": today,
        "trades": trades,
        "portfolio": {
            "account_id": portfolio.get("account_id"),
            "net_liquidation": portfolio.get("net_liquidation"),
            "cash": portfolio.get("cash"),
            "num_positions": len(portfolio.get("positions", [])),
        },
    }

    user_content = json.dumps(context, default=str) + "\n\n" + _INSTRUCTION

    try:
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=settings.OPENROUTER_API_KEY,
        )
        response = client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.3,
        )
        report = (response.choices[0].message.content or "").strip()
    except Exception:
        logger.exception("Failed to generate EOD report")
        report = f"EOD report generation failed for {today}. {len(trades)} trade(s) logged."

    # Persist the report alongside the logs.
    settings.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = settings.LOGS_DIR / f"summary_{today}.txt"
    try:
        report_path.write_text(report)
    except Exception:
        logger.exception("Failed to write report to %s", report_path)

    print(report)
    return report
