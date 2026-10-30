# -*- coding: utf-8 -*-
"""
Usage Tracker for VERITRACE
Tracks token usage and search API calls for cost monitoring.
"""

import threading
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Dict, Optional
import time


@dataclass
class UsageStats:
    """Statistics for a single session."""

    # Token counts
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    # Search API calls
    search_calls: int = 0
    search_results_count: int = 0

    # LLM calls
    llm_calls: int = 0

    # Timing
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None

    # Details
    search_queries: list = field(default_factory=list)
    llm_models_used: list = field(default_factory=list)

    # Per-model breakdown: {model_name: {input: int, output: int, total: int, calls: int}}
    per_model: Dict[str, Dict[str, int]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        duration = (self.end_time or time.time()) - self.start_time
        return {
            "tokens": {
                "input": self.input_tokens,
                "output": self.output_tokens,
                "total": self.total_tokens,
            },
            "search": {
                "api_calls": self.search_calls,
                "results_count": self.search_results_count,
                "queries": self.search_queries[-10:],  # Last 10 queries
            },
            "llm": {
                "calls": self.llm_calls,
                "models": list(set(self.llm_models_used)),
                "per_model": self.per_model,
            },
            "timing": {
                "duration_seconds": round(duration, 2),
            },
            "summary": self._generate_summary(duration),
        }

    def _generate_summary(self, duration: float) -> str:
        """Generate human-readable summary."""
        lines = [
            f"Token Usage: {self.total_tokens:,} total ({self.input_tokens:,} in / {self.output_tokens:,} out)",
            f"Search Calls: {self.search_calls} ({self.search_results_count} results)",
            f"LLM Calls: {self.llm_calls}",
            f"Duration: {duration:.1f}s",
        ]
        return " | ".join(lines)


class UsageTracker:
    """Thread-safe token and search accounting for one research session."""

    def __init__(self):
        self._stats = UsageStats()
        self._session_lock = threading.Lock()

    def reset(self):
        """Reset stats for a new session."""
        with self._session_lock:
            self._stats = UsageStats()

    def add_llm_usage(self, input_tokens: int = 0, output_tokens: int = 0, model: str = ""):
        """Record LLM API usage."""
        with self._session_lock:
            self._stats.input_tokens += input_tokens
            self._stats.output_tokens += output_tokens
            self._stats.total_tokens += input_tokens + output_tokens
            self._stats.llm_calls += 1
            if model:
                self._stats.llm_models_used.append(model)
                # Per-model breakdown
                if model not in self._stats.per_model:
                    self._stats.per_model[model] = {"input": 0, "output": 0, "total": 0, "calls": 0}
                self._stats.per_model[model]["input"] += input_tokens
                self._stats.per_model[model]["output"] += output_tokens
                self._stats.per_model[model]["total"] += input_tokens + output_tokens
                self._stats.per_model[model]["calls"] += 1

    def add_search_call(self, query: str = "", results_count: int = 0):
        """Record search API call."""
        with self._session_lock:
            self._stats.search_calls += 1
            self._stats.search_results_count += results_count
            if query:
                self._stats.search_queries.append(query)

    def finish(self):
        """Mark session as finished."""
        with self._session_lock:
            self._stats.end_time = time.time()

    def get_stats(self) -> UsageStats:
        """Get current stats."""
        with self._session_lock:
            return deepcopy(self._stats)

    def get_stats_dict(self) -> dict:
        """Get stats as dictionary."""
        return self.get_stats().to_dict()


_current_tracker = ContextVar("veritrace_usage_tracker", default=None)


def get_tracker() -> UsageTracker:
    tracker = _current_tracker.get()
    if tracker is None:
        tracker = UsageTracker()
        _current_tracker.set(tracker)
    return tracker


def reset_tracker():
    """Start independent accounting in the current request context."""
    _current_tracker.set(UsageTracker())


def track_llm_usage(input_tokens: int = 0, output_tokens: int = 0, model: str = ""):
    get_tracker().add_llm_usage(input_tokens, output_tokens, model)


def track_search_call(query: str = "", results_count: int = 0):
    get_tracker().add_search_call(query, results_count)


def get_usage_summary() -> dict:
    return get_tracker().get_stats_dict()


def finish_session():
    get_tracker().finish()
