"""
VERITRACE Agent Configuration

All tunable parameters for experimentation.
Adjust these values to optimize performance for different use cases.

Priority: ENV > config defaults
- Set in .env for deployment-specific values
- Modify DEFAULT_CONFIG for experiment parameters
"""

import os
from dataclasses import dataclass, field


def _env_int(key: str, default: int) -> int:
    """Get int from env or use default."""
    val = os.getenv(key)
    return int(val) if val else default


def _env_bool(key: str, default: bool) -> bool:
    """Get bool from env or use default."""
    val = os.getenv(key)
    if val is None:
        return default
    return val.lower() in ("true", "1", "yes")


def _env_str(key: str, default: str) -> str:
    """Get string from env or use default."""
    return os.getenv(key, default)


@dataclass
class PreFilterConfig:
    """PreFilter (Stage 1) configuration."""

    # Batch processing
    batch_size: int = 12  # Results per batch (set high like 100 for single batch)
    # Default to four workers, matching _create_default_config.
    max_workers: int = 4  # Parallel LLM calls

    # Model recommendation: Non-thinking model for speed
    # e.g., "Qwen/Qwen2.5-32B-Instruct" (accurate) or "Qwen/Qwen2.5-7B-Instruct" (fast)


@dataclass
class ReaderConfig:
    """Reader (Stage 3) configuration."""

    # Parallel execution
    max_workers: int = 15  # Parallel page readers (actual concurrency limited by LLM_MAX_CONCURRENCY)

    # Content limits
    max_content_length: int = 15000  # Max chars per page content
    snippet_length: int = 300  # Snippet length for PreFilter display

    # Model recommendation: Thinking model for deep analysis
    # e.g., "Qwen/Qwen3-Next-80B-A3B-Thinking"


@dataclass
class SearcherConfig:
    """SearcherAgent configuration."""

    # Search settings
    search_engine: str = "google"  # google, serper, duckduckgo, bing
    max_results: int = 10  # Results per search query
    max_turn: int = 6  # Max search iterations

    # Three-stage cognition
    enable_three_stage: bool = True  # Enable PreFilter; the Searcher analyzes snippets itself.

    # Compatibility metadata; quality_checker.py defines the active quality rules.
    quality_threshold: float = 0.6
    high_quality_ratio: float = 0.7


@dataclass
class AgentConfig:
    """Combined agent configuration."""

    prefilter: PreFilterConfig = field(default_factory=PreFilterConfig)
    reader: ReaderConfig = field(default_factory=ReaderConfig)
    searcher: SearcherConfig = field(default_factory=SearcherConfig)


def _create_default_config() -> AgentConfig:
    """Create config with ENV overrides."""
    return AgentConfig(
        prefilter=PreFilterConfig(
            batch_size=_env_int("PREFILTER_BATCH_SIZE", 12),
            max_workers=_env_int("PREFILTER_MAX_WORKERS", 4),
        ),
        reader=ReaderConfig(
            max_workers=_env_int("READER_MAX_WORKERS", 15),
            max_content_length=_env_int("READER_MAX_CONTENT_LENGTH", 15000),
            snippet_length=_env_int("READER_SNIPPET_LENGTH", 300),
        ),
        searcher=SearcherConfig(
            search_engine=_env_str("SEARCH_ENGINE", "google"),
            max_results=_env_int("SEARCH_MAX_RESULTS", 10),
            max_turn=_env_int("SEARCH_MAX_TURN", 6),
            enable_three_stage=_env_bool("ENABLE_THREE_STAGE", True),
            quality_threshold=float(_env_str("SEARCH_QUALITY_THRESHOLD", "0.6")),
            high_quality_ratio=float(_env_str("SEARCH_HIGH_QUALITY_RATIO", "0.7")),
        ),
    )


# Default configuration instance (reads from ENV at import time)
DEFAULT_CONFIG = _create_default_config()


def get_config() -> AgentConfig:
    """Get the default configuration."""
    return DEFAULT_CONFIG


def reload_config() -> AgentConfig:
    """Reload config from ENV (useful after changing .env at runtime)."""
    global DEFAULT_CONFIG
    DEFAULT_CONFIG = _create_default_config()
    return DEFAULT_CONFIG


# Quick access to sub-configs
def get_prefilter_config() -> PreFilterConfig:
    return DEFAULT_CONFIG.prefilter


def get_reader_config() -> ReaderConfig:
    return DEFAULT_CONFIG.reader


def get_searcher_config() -> SearcherConfig:
    return DEFAULT_CONFIG.searcher


# Three-stage report writer configuration (reads from ENV)
OUTLINE_MODEL = os.getenv("OUTLINE_MODEL")  # None = use WRITER_MODEL
SECTION_WRITER_MODEL = os.getenv("SECTION_WRITER_MODEL")  # None = use WRITER_MODEL
SECTION_PLANNER_MODEL = os.getenv("SECTION_PLANNER_MODEL")  # None = use OUTLINE_MODEL
SECTION_PLANNER_MAX_WORKERS = _env_int("SECTION_PLANNER_MAX_WORKERS", 4)
PREVIOUS_TEXT_MAX_CHARS = _env_int("PREVIOUS_TEXT_MAX_CHARS", 15000)

# Soft deadline: gracefully stop exploration and write report with collected evidence
# Set to 0 to disable. Normal queries (~55min median) are not affected.
# Default 70min leaves ~30min for writing before the 100min hard timeout.
SOFT_DEADLINE_MINUTES = float(_env_str("SOFT_DEADLINE_MINUTES", "70"))
