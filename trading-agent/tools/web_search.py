"""Web search backed by the Tavily API.

Pre-fetches recent news, earnings, and company context for held positions and
candidates so the LLM sees research as plain context rather than having to
call a tool itself.
"""

import logging

import requests
from config import settings

logger = logging.getLogger(__name__)

_TAVILY_URL = "https://api.tavily.com/search"
_TIMEOUT = 15


def web_search(query: str, max_results: int = 5) -> list[dict]:
    """Run a web search via Tavily and return a list of {title, url, content}.

    Returns an empty list if TAVILY_API_KEY is not configured or the request
    fails for any reason.
    """
    if not settings.TAVILY_API_KEY:
        logger.warning("TAVILY_API_KEY not set — web search disabled")
        return []

    try:
        resp = requests.post(
            _TAVILY_URL,
            json={
                "api_key": settings.TAVILY_API_KEY,
                "query": query,
                "search_depth": "basic",
                "max_results": max_results,
                "include_answer": False,
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", ""),
            }
            for r in data.get("results", [])
        ]
    except Exception:
        logger.exception("Tavily search failed for query: %s", query)
        return []


def research_ticker(symbol: str, name_hint: str = "") -> str:
    """Pre-fetch a plain-text summary of recent news for a ticker.

    Sends a Tavily query asking about recent news, earnings, and business
    developments, then concatenates the most useful result content into a
    readable summary. Returns an empty string silently if Tavily is not
    configured or the search fails.
    """
    subject = f"{symbol} ({name_hint})" if name_hint else symbol
    query = (
        f"recent news, earnings, and business developments for {subject} stock"
    )
    results = web_search(query)
    if not results:
        return ""

    parts: list[str] = []
    for r in results:
        title = r.get("title", "").strip()
        content = r.get("content", "").strip()
        if not content:
            continue
        parts.append(f"{title}: {content}" if title else content)

    return "\n\n".join(parts)
