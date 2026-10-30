"""
Built-in Tools for VERITRACE.

This module provides ready-to-use tools:
- WebSearchTool: Search the web using various engines
- PageSelectTool: Select and read web pages from search results
"""

import asyncio
import logging
import threading
from ..concurrency import check_cancelled
from typing import Any, Dict, List, Optional

from pydantic import Field

from .base_tool import BaseTool, ToolResult, register_tool

# Suppress pdfminer font warnings (harmless but noisy)
logging.getLogger("pdfminer").setLevel(logging.ERROR)

# Module-level storage for search configuration and cache
# (Pydantic models can't have mutable class attributes)
_ENGINE_CONFIGS: Dict[str, Dict[str, Any]] = {}

# Serper API key rotation state (module-level, shared across all instances)
_serper_keys: Optional[List[str]] = None
_serper_key_idx: int = 0
_serper_dead_keys: set = set()
_serper_lock = threading.RLock()


def _load_serper_keys() -> List[str]:
    """Load primary + backup Serper API keys from env. Cached after first call."""
    with _serper_lock:
        global _serper_keys
        if _serper_keys is not None:
            return _serper_keys

        import os

        keys: List[str] = []
        primary = os.environ.get("SERPER_API_KEY", "").strip()
        if primary:
            keys.append(primary)
        backup = os.environ.get("SERPER_BACKUP_API_KEYS", "").strip()
        if backup:
            for k in backup.split(","):
                k = k.strip()
                if k and k not in keys:
                    keys.append(k)
        _serper_keys = keys
        return keys


def _get_active_serper_key() -> Optional[str]:
    """Return the currently active Serper key, skipping dead ones."""
    with _serper_lock:
        global _serper_key_idx
        keys = _load_serper_keys()
        if not keys:
            return None
        n = len(keys)
        for _ in range(n):
            idx = _serper_key_idx % n
            key = keys[idx]
            if key not in _serper_dead_keys:
                return key
            _serper_key_idx += 1
        return None  # All keys dead


def _rotate_serper_key(failed_key: str, mark_dead: bool = True) -> Optional[str]:
    """Mark current key as dead and advance to next. Returns next key or None."""
    with _serper_lock:
        global _serper_key_idx
        if mark_dead:
            _serper_dead_keys.add(failed_key)
            logging.warning(
                f"[Serper] Key ...{failed_key[-6:]} marked dead. Total dead: {len(_serper_dead_keys)}/{len(_load_serper_keys())}"
            )
        _serper_key_idx += 1
        return _get_active_serper_key()


# Bound cache growth with an LRU cache.
from collections import OrderedDict


class LRUCache:
    """A bounded least-recently-used cache."""

    def __init__(self, maxsize: int = 500):
        self._cache: OrderedDict = OrderedDict()
        self._maxsize = maxsize
        self._lock = threading.RLock()

    def get(self, key: str, default=None):
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            return default

    def set(self, key: str, value):
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            elif len(self._cache) >= self._maxsize:
                self._cache.popitem(last=False)
            self._cache[key] = value

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._cache

    def __getitem__(self, key: str):
        return self.get(key)

    def __setitem__(self, key: str, value):
        self.set(key, value)

    def __bool__(self) -> bool:
        with self._lock:
            return len(self._cache) > 0

    def keys(self):
        with self._lock:
            return list(self._cache)


_SEARCH_RESULTS_CACHE = LRUCache(maxsize=1000)  # Cache at most 1,000 queries.


# Retry temporary network errors.
import time
from functools import wraps


def retry_on_temporary_error(max_retries: int = 3, base_delay: float = 1.0):
    """Retry temporary network failures with exponential backoff: 429, 500, 502, 503, 504, connection errors, and timeouts. Propagate permanent 400, 401, 403, and 404 errors."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e

                    # Identify temporary errors.
                    is_temporary = False
                    error_str = str(e).lower()

                    # requests exception types.
                    if "timeout" in error_str or "connection" in error_str:
                        is_temporary = True
                    # HTTP status errors.
                    elif hasattr(e, "response") and e.response is not None:
                        status = e.response.status_code
                        is_temporary = status in (429, 500, 502, 503, 504)
                    # Other retryable errors.
                    elif "rate" in error_str or "limit" in error_str:
                        is_temporary = True

                    if not is_temporary:
                        # Propagate permanent errors immediately.
                        raise

                    if attempt < max_retries - 1:
                        delay = base_delay * (2**attempt)  # Exponential backoff: 1s, 2s, 4s.
                        logging.warning(f"   ⚠️ 请求失败 (尝试 {attempt + 1}/{max_retries}): {e}, {delay}s 后重试...")
                        time.sleep(delay)

            # All retry attempts failed.
            raise last_exception

        return wrapper

    return decorator


# Thread-local storage for Playwright instances (sync_api is not thread-safe)
_playwright_local = threading.local()


def _get_playwright_browser():
    """Get or create a thread-local Playwright browser instance.

    Playwright sync_api is not thread-safe, so each thread needs its own instance.
    Browser startup is slow (~3s), but we reuse within the same thread.
    """
    if not hasattr(_playwright_local, "browser") or _playwright_local.browser is None:
        from playwright.sync_api import sync_playwright

        _playwright_local.playwright = sync_playwright().start()
        _playwright_local.browser = _playwright_local.playwright.chromium.launch(headless=True)
        logging.info("   🎭 Playwright browser started (thread-local)")

    return _playwright_local.browser


def _cleanup_thread_playwright():
    """Clean up Playwright browser for current thread."""
    if hasattr(_playwright_local, "browser") and _playwright_local.browser:
        try:
            _playwright_local.browser.close()
        except Exception:
            pass
        _playwright_local.browser = None

    if hasattr(_playwright_local, "playwright") and _playwright_local.playwright:
        try:
            _playwright_local.playwright.stop()
        except Exception:
            pass
        _playwright_local.playwright = None


def _extract_domain(url: str) -> str:
    """Extract domain from URL for source quality evaluation."""
    from urllib.parse import urlparse

    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


@register_tool
class WebSearchTool(BaseTool):
    """
    Search the web for information.

    Supports multiple search engines (DuckDuckGo, Bing, Google, etc.).
    """

    query: str = Field(description="The search query to execute. Be specific and include relevant keywords.")
    max_results: int = Field(
        default=10,
        description="Maximum number of search results to return (1-20).",
        ge=1,
        le=20,
    )
    search_engine: str = Field(
        default="google",
        description="Search engine to use: 'google' (Google Custom Search API), 'serper', 'duckduckgo', 'bing', 'brave'",
    )

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "Search the web for information using various search engines. Returns a list of search results with titles, URLs, and snippets."

    def execute(self) -> ToolResult:
        """Execute web search."""
        try:
            results = self._perform_search()

            # Cache results for later page selection
            cache_key = self._get_cache_key()
            _SEARCH_RESULTS_CACHE[cache_key] = results

            # Format results for LLM
            formatted = self._format_results(results)

            return ToolResult(
                success=True,
                output={
                    "query": self.query,
                    "num_results": len(results),
                    "results": formatted,
                    "cache_key": cache_key,
                },
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    async def aexecute(self) -> ToolResult:
        """Async web search execution."""
        try:
            results = await self._async_perform_search()

            cache_key = self._get_cache_key()
            _SEARCH_RESULTS_CACHE[cache_key] = results

            formatted = self._format_results(results)

            return ToolResult(
                success=True,
                output={
                    "query": self.query,
                    "num_results": len(results),
                    "results": formatted,
                    "cache_key": cache_key,
                },
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    def _get_cache_key(self) -> str:
        """Generate cache key for search results."""
        return f"{self.search_engine}:{self.query}"

    def _perform_search(self) -> List[Dict]:
        """Perform the actual search."""
        engine = self.search_engine.lower()

        if engine == "serper":
            results = self._search_serper()
        elif engine == "duckduckgo":
            results = self._search_duckduckgo()
        elif engine == "bing":
            results = self._search_bing()
        elif engine == "google":
            results = self._search_google()
        else:
            # Fallback to Serper (best quality)
            results = self._search_serper()

        # Track search usage
        try:
            from ..usage_tracker import track_search_call

            track_search_call(query=self.query, results_count=len(results))
        except Exception:
            pass  # Don't fail if tracker not available

        return results

    async def _async_perform_search(self) -> List[Dict]:
        """Async search (wraps sync for now)."""
        return await asyncio.to_thread(self._perform_search)

    @retry_on_temporary_error(max_retries=3, base_delay=1.0)
    def _search_serper(self) -> List[Dict]:
        """
        Search using Serper API (Google Search).

        Auto-rotates through SERPER_API_KEY + SERPER_BACKUP_API_KEYS on
        4xx errors (400/402/429 = bad/exhausted/rate-limited keys).
        """
        import os
        import requests

        # Build candidate key list: prefer rotation pool, fall back to config
        config = _ENGINE_CONFIGS.get("serper", {})
        config_key = config.get("api_key")
        rotated_key = _get_active_serper_key()
        api_key = rotated_key or config_key or os.environ.get("SERPER_API_KEY")

        if not api_key:
            raise ValueError(
                "Serper API key not configured. "
                "Set SERPER_API_KEY env var or use WebSearchTool.configure_engine('serper', api_key='...')"
            )

        payload = {"q": self.query, "num": self.max_results}

        # Try keys in order; rotate on 400/402/429
        max_attempts = max(1, len(_load_serper_keys()))
        last_exc = None
        for attempt in range(max_attempts):
            headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
            try:
                check_cancelled()
                response = requests.post(
                    "https://google.serper.dev/search",
                    headers=headers,
                    json=payload,
                    timeout=30,
                )
                if response.status_code in (400, 402, 429):
                    # Bad key / out of credits / rate-limited → rotate
                    next_key = _rotate_serper_key(api_key, mark_dead=True)
                    if next_key and next_key != api_key:
                        api_key = next_key
                        last_exc = requests.HTTPError(f"Serper {response.status_code}: {response.text[:200]}")
                        continue
                    # No more keys to try
                    response.raise_for_status()
                response.raise_for_status()
                break  # success
            except requests.HTTPError as e:
                last_exc = e
                if attempt == max_attempts - 1:
                    raise
        else:
            if last_exc:
                raise last_exc

        data = response.json()
        organic_results = data.get("organic", [])

        return [
            {
                "index": i,
                "title": r.get("title", ""),
                "url": r.get("link", ""),
                "snippet": r.get("snippet", ""),
                # Metadata for quality evaluation
                "domain": _extract_domain(r.get("link", "")),
                "date_published": r.get("date"),  # Serper provides date field
                "author": None,  # Serper doesn't provide author
                "site_name": None,  # Serper doesn't provide site_name
            }
            for i, r in enumerate(organic_results)
        ]

    @retry_on_temporary_error(max_retries=3, base_delay=1.0)
    def _search_duckduckgo(self) -> List[Dict]:
        """Search using DuckDuckGo."""
        try:
            from duckduckgo_search import DDGS

            with DDGS() as ddgs:
                results = list(ddgs.text(self.query, max_results=self.max_results))

            return [
                {
                    "index": i,
                    "title": r.get("title", ""),
                    "url": r.get("href", r.get("link", "")),
                    "snippet": r.get("body", r.get("snippet", "")),
                    # Metadata for quality evaluation
                    "domain": _extract_domain(r.get("href", r.get("link", ""))),
                    "date_published": None,  # DuckDuckGo doesn't provide date
                    "author": None,
                    "site_name": None,
                }
                for i, r in enumerate(results)
            ]
        except ImportError:
            raise ImportError("duckduckgo_search not installed. Run: pip install duckduckgo-search")

    @retry_on_temporary_error(max_retries=3, base_delay=1.0)
    def _search_bing(self) -> List[Dict]:
        """Search using Bing (requires API key)."""
        config = _ENGINE_CONFIGS.get("bing", {})
        api_key = config.get("api_key")

        if not api_key:
            raise ValueError("Bing API key not configured. Use WebSearchTool.configure_engine('bing', api_key='...')")

        import requests

        headers = {"Ocp-Apim-Subscription-Key": api_key}
        params = {"q": self.query, "count": self.max_results}

        check_cancelled()
        response = requests.get(
            "https://api.bing.microsoft.com/v7.0/search",
            headers=headers,
            params=params,
            timeout=30,  # Set a request timeout.
        )
        response.raise_for_status()

        data = response.json()
        web_pages = data.get("webPages", {}).get("value", [])

        return [
            {
                "index": i,
                "title": r.get("name", ""),
                "url": r.get("url", ""),
                "snippet": r.get("snippet", ""),
                # Metadata for quality evaluation
                "domain": _extract_domain(r.get("url", "")),
                "date_published": r.get("dateLastCrawled"),  # Bing provides crawl date
                "author": None,
                "site_name": r.get("siteName"),  # Bing may provide site name
            }
            for i, r in enumerate(web_pages)
        ]

    @retry_on_temporary_error(max_retries=3, base_delay=1.0)
    def _search_google(self) -> List[Dict]:
        """Search using Google Custom Search (requires API key)."""
        import os

        config = _ENGINE_CONFIGS.get("google", {})
        api_key = config.get("api_key") or os.environ.get("GOOGLE_SEARCH_API_KEY")
        cx = config.get("cx") or os.environ.get("GOOGLE_CX")

        if not api_key or not cx:
            raise ValueError(
                "Google API key and CX not configured. "
                "Set GOOGLE_SEARCH_API_KEY and GOOGLE_CX env vars or use WebSearchTool.configure_engine('google', api_key='...', cx='...')"
            )

        import requests

        params = {
            "key": api_key,
            "cx": cx,
            "q": self.query,
            "num": min(self.max_results, 10),  # Google limits to 10
        }

        check_cancelled()
        response = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params=params,
            timeout=30,  # Set a request timeout.
        )
        response.raise_for_status()

        data = response.json()
        items = data.get("items", [])

        results = []
        for i, r in enumerate(items):
            # Extract pagemap metadata for quality evaluation
            pagemap = r.get("pagemap", {})
            metatags_list = pagemap.get("metatags", [])
            metatags = metatags_list[0] if metatags_list else {}

            results.append(
                {
                    "index": i,
                    "title": r.get("title", ""),
                    "url": r.get("link", ""),
                    "snippet": r.get("snippet", ""),
                    # Metadata for quality evaluation
                    "domain": _extract_domain(r.get("link", "")),
                    "date_published": (
                        metatags.get("article:published_time")
                        or metatags.get("datepublished")
                        or metatags.get("og:article:published_time")
                    ),
                    "author": (
                        metatags.get("author") or metatags.get("article:author") or metatags.get("og:article:author")
                    ),
                    "site_name": metatags.get("og:site_name"),
                }
            )
        return results

    def _format_results(self, results: List[Dict]) -> str:
        """Format search results for LLM consumption."""
        if not results:
            return "No results found."

        lines = []
        for r in results:
            lines.append(f"[{r['index']}] {r['title']}")
            lines.append(f"    URL: {r['url']}")
            lines.append(f"    {r['snippet']}")
            lines.append("")

        return "\n".join(lines)

    @classmethod
    def configure_engine(cls, engine: str, **kwargs) -> None:
        """
        Configure a search engine with API keys and options.

        Args:
            engine: Engine name ('bing', 'google', etc.)
            **kwargs: Engine-specific configuration (api_key, cx, etc.)
        """
        _ENGINE_CONFIGS[engine.lower()] = kwargs

    @classmethod
    def get_cached_results(cls, cache_key: str) -> Optional[List[Dict]]:
        """Get cached search results by key."""
        return _SEARCH_RESULTS_CACHE.get(cache_key)


from .page_reader import PageSelectTool as PageSelectTool
