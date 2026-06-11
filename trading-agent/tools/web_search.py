"""Web search tool backed by the Tavily API.

Gives the LLM a way to look up recent news, earnings, and company context
for held positions and candidates via OpenAI-style tool/function calling.
"""

import logging

import requests
from config import settings

logger = logging.getLogger(__name__)

_TAVILY_URL = "https://api.tavily.com/search"
_TIMEOUT = 15

# OpenAI-compatible tool schema. Pass this in the `tools` list of a chat
# completion request to let the model call web_search.
WEB_SEARCH_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web for recent news, earnings, guidance, product "
            "launches, or other context about a company or stock. Use this "
            "to research held positions and candidates before deciding."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The search query, e.g. 'AAPL Q3 earnings results' "
                        "or 'Acme Corp recent news'."
                    ),
                },
            },
            "required": ["query"],
        },
    },
}


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
