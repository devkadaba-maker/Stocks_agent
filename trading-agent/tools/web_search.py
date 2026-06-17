"""Web search backed by Tavily, with DuckDuckGo as automatic fallback.

Pre-fetches recent news, earnings, and company context for held positions and
candidates so the LLM sees research as plain context rather than having to
call a tool itself.

Fallback order:
  1. Tavily (primary) — TAVILY_API_KEY required; best quality, rate-limited monthly.
  2. DuckDuckGo (fallback) — no API key needed; used when Tavily fails or quota is exhausted.
"""

import logging

import requests
from ddgs import DDGS
from config import settings

logger = logging.getLogger(__name__)

_TAVILY_URL = "https://api.tavily.com/search"
_TIMEOUT = 15


def _search_tavily(query: str, max_results: int) -> list[dict]:
    if not settings.TAVILY_API_KEY:
        return []
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


def _search_ddg(query: str, max_results: int) -> list[dict]:
    try:
        with DDGS() as ddgs:
            return [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("body", ""),
                }
                for r in ddgs.news(query, max_results=max_results)
            ]
    except Exception:
        # "No results found" and transient DDG errors are not real failures.
        return []


def web_search(query: str, max_results: int = 5) -> list[dict]:
    """Search via Tavily first; fall back to DuckDuckGo if Tavily fails.

    Returns a list of {title, url, content}. Returns [] if both fail.
    """
    if settings.TAVILY_API_KEY:
        try:
            results = _search_tavily(query, max_results)
            if results:
                return results
        except Exception:
            logger.warning("Tavily failed for '%s' — falling back to DuckDuckGo", query)
    else:
        logger.warning("TAVILY_API_KEY not set — using DuckDuckGo")

    try:
        results = _search_ddg(query, max_results)
        if results:
            return results
    except Exception:
        logger.debug("DuckDuckGo fallback also failed for query: %s", query)

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
