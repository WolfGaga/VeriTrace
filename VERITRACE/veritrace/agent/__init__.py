"""
VERITRACE Agent Module.

VERITRACE: Verification-Enhanced Retrieval with Iterative Triangulation
and CRAAP-based Evidence Assessment.

Research agents, components, and configuration.
Supports multiple LLM providers: siliconflow, openai, anthropic, qwen, etc.
"""

import os
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

from .veritrace_agent import VeritraceAgent, AsyncVeritraceAgent
from .searcher import SearcherAgent
from .runtime import ResearchEvent, ResearchResult, TaskResult
from .runtime.scheduler import TaskScheduler
from .core import (
    create_adapter,
    WebSearchTool,
)

from .prefilter import PreFilter, PreFilterResult
from .reader import (
    ReaderAgent,
    ParallelReaderExecutor,
    ReaderInput,
    ReaderOutput,
    MergedReaderResults,
)

from .config import (
    AgentConfig,
    PreFilterConfig,
    ReaderConfig,
    SearcherConfig,
    get_config,
    get_prefilter_config,
    get_reader_config,
    get_searcher_config,
    reload_config,
    DEFAULT_CONFIG,
)


def init_agent(
    lang: str = "en",
    writer_lang: Optional[str] = None,
    llm_provider: str = None,
    llm_model: str = "Qwen/Qwen3-Next-80B-A3B-Thinking",
    searcher_model: Optional[str] = None,
    reader_model: Optional[str] = None,
    prefilter_model: Optional[str] = None,
    quality_model: Optional[str] = None,
    cognitive_model: Optional[str] = None,
    summarizer_model: Optional[str] = None,
    llm_api_key: Optional[str] = None,
    search_engine: str = "google",
    max_results: Optional[int] = None,
    use_async: bool = False,
    include_search_refs: bool = True,
    writer_mode: Optional[str] = None,
    *,
    writer_model: Optional[str] = None,
) -> VeritraceAgent:
    """Initialize VERITRACE Agent."""

    import logging

    logging.info(f"🔧 [init_agent] 配置搜索引擎: {search_engine}")

    if search_engine == "serper":
        serper_key = os.getenv("SERPER_API_KEY")
        if serper_key:
            WebSearchTool.configure_engine("serper", api_key=serper_key)
            logging.info("✅ Serper API 已配置")
        else:
            logging.warning("⚠️ SERPER_API_KEY 未设置")

    elif search_engine == "google":
        google_api_key = os.getenv("GOOGLE_SEARCH_API_KEY")
        google_cx = os.getenv("GOOGLE_CX")
        if google_api_key and google_cx:
            WebSearchTool.configure_engine("google", api_key=google_api_key, cx=google_cx)
            logging.info("✅ Google Custom Search API 已配置")
        else:
            missing = []
            if not google_api_key:
                missing.append("GOOGLE_SEARCH_API_KEY")
            if not google_cx:
                missing.append("GOOGLE_CX")
            logging.warning(f"⚠️ Google API 缺少环境变量: {', '.join(missing)}")

    elif search_engine == "bing":
        bing_key = os.getenv("BING_API_KEY")
        if bing_key:
            WebSearchTool.configure_engine("bing", api_key=bing_key)
            logging.info("✅ Bing API 已配置")
        else:
            logging.warning("⚠️ BING_API_KEY 未设置")

    searcher_cfg = {
        "search_engine": search_engine,
        "max_results": max_results if max_results is not None else get_searcher_config().max_results,
    }

    llm_provider = llm_provider or os.environ.get("LLM_PROVIDER", "siliconflow")
    agent_cls = AsyncVeritraceAgent if use_async else VeritraceAgent
    agent = agent_cls(
        llm_provider=llm_provider,
        llm_model=llm_model,
        searcher_model=searcher_model,
        reader_model=reader_model,
        prefilter_model=prefilter_model,
        quality_model=quality_model,
        cognitive_model=cognitive_model,
        writer_model=writer_model,
        summarizer_model=summarizer_model,
        llm_api_key=llm_api_key,
        searcher_cfg=searcher_cfg,
        max_turn=int(os.environ.get("PLANNER_MAX_TURN", "20")),
        language=lang,
        writer_language=writer_lang,
        include_search_refs=include_search_refs,
        writer_mode=writer_mode,
    )

    return agent


__all__ = [
    "init_agent",
    "VeritraceAgent",
    "AsyncVeritraceAgent",
    "TaskScheduler",
    "SearcherAgent",
    "create_adapter",
    "WebSearchTool",
    "ResearchEvent",
    "ResearchResult",
    "TaskResult",
    "PreFilter",
    "PreFilterResult",
    "ReaderAgent",
    "ParallelReaderExecutor",
    "ReaderInput",
    "ReaderOutput",
    "MergedReaderResults",
    "AgentConfig",
    "PreFilterConfig",
    "ReaderConfig",
    "SearcherConfig",
    "get_config",
    "get_prefilter_config",
    "get_reader_config",
    "get_searcher_config",
    "reload_config",
    "DEFAULT_CONFIG",
]
