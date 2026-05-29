"""
Tool Executor — Async Execution Patterns.

This module contains the CANONICAL execution logic for tools.
Extracted from tool_registry.py to enable isolated testing of async patterns.

Execution flow:
1. execute_with_limits() — main entry point with rate limiting and capability enforcement
2. _execute_handler() — runs tool handler (async or sync in thread pool)
"""

from __future__ import annotations

import asyncio
import inspect
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .registry import Tool, ToolRegistry


# ============================================================================
# Tool Executor
# ============================================================================


class ToolExecutor:
    """
    Canonical async tool executor.

    Separated from registry for testability — async patterns can be
    tested in isolation without full registry initialization.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def execute_with_limits(
        self,
        tool_name: str,
        args: dict[str, Any],
        timeout_ms: int | None = None,
        available_capabilities: set[str] | None = None,
        exec_logger: Any | None = None,
        correlation: dict[str, str | None] | None = None,
    ) -> Any:
        """Execute tool with rate limiting and capability enforcement.

        Args:
            tool_name: Name of the tool to execute
            args: Validated arguments
            timeout_ms: Optional timeout override
            available_capabilities: Set of available capability names for enforcement.
                                  If None, capability check is skipped.
            exec_logger: Optional ToolExecLog for audit logging.
            correlation: Optional correlation dict (run_id, branch_id, etc.)

        Returns:
            Tool return value
        """
        registry = self._registry
        tool = registry.get_tool(tool_name)

        # Capability enforcement (backward compatible - skips if None)
        if available_capabilities is not None:
            satisfied, reason = registry.check_capabilities(tool_name, available_capabilities)
            if not satisfied:
                raise RuntimeError(f"Capability check failed: {reason}")
        else:
            import warnings
            warnings.warn(
                f"[TOOL EXECUTOR] execute_with_limits(tool_name={tool_name!r}, "
                f"available_capabilities=None) — capability check SKIPPED.",
                DeprecationWarning,
                stacklevel=2,
            )

        # Validate arguments
        validated = tool.validate_args(args)

        # Check rate limit
        allowed, reason = registry.validate_call(tool_name)
        if not allowed:
            raise RuntimeError(reason)

        # Increment counter
        registry._increment_call_count(tool_name)

        # Serialize inputs for audit (hashed, not stored raw)
        try:
            import orjson
            input_bytes = orjson.dumps(args, option=orjson.OPT_SORT_KEYS)
        except Exception:
            input_bytes = str(args).encode("utf-8")

        # Execute with semaphore for parallelism control
        semaphore = registry._get_semaphore(tool_name)
        timeout = timeout_ms or tool.cost_model.time_ms_est * 2

        async with semaphore:
            result = None
            error: Exception | None = None
            status = "success"
            output_bytes: bytes = b""

            try:
                result = await asyncio.wait_for(
                    self._execute_handler(tool, validated),
                    timeout=timeout / 1000,
                )
                try:
                    import orjson
                    output_bytes = orjson.dumps(result)
                except Exception:
                    output_bytes = str(result).encode("utf-8") if result is not None else b""
            except TimeoutError:
                error = TimeoutError(f"Tool '{tool_name}' timed out after {timeout}ms")
                status = "error"
                output_bytes = b""
                raise
            except Exception as e:
                error = e
                status = "error"
                try:
                    import orjson
                    output_bytes = orjson.dumps({"error": str(e)})
                except Exception:
                    output_bytes = str(e).encode("utf-8")
                raise
            finally:
                if exec_logger is not None:
                    try:
                        from .tool_exec_log import normalize_correlation
                        normalized_corr = normalize_correlation(correlation)
                        exec_logger.log(
                            tool_name=tool_name,
                            input_data=input_bytes,
                            output_data=output_bytes,
                            status=status,
                            error=error,
                            correlation=normalized_corr,
                        )
                    except Exception as logger_error:
                        import logging
                        logging.getLogger(__name__).warning(
                            f"[TOOL EXECUTOR] exec_logger.log() failed: {logger_error}"
                        )

        return result

    async def _execute_handler(self, tool: Tool, validated_args: Any) -> Any:
        """Execute tool handler with validated arguments."""
        handler = tool.handler

        if inspect.iscoroutinefunction(handler):
            return await handler(**validated_args.model_dump())
        else:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, lambda: handler(**validated_args.model_dump())
            )


# ============================================================================
# DNS Tunnel Executor (M1-Safe)
# ============================================================================
# F196A: M1-safe dedicated single-thread executor for async DNS tunnel calls.
# Using a persistent worker thread avoids Metal context conflicts from nested
# event loops that occur when asyncio.run() is called in run_in_executor.


_DNS_TUNNEL_EXECUTOR: asyncio.AbstractEventLoop | None = None


def _get_dns_tunnel_executor() -> asyncio.AbstractEventLoop:
    """Get or create DNS tunnel dedicated event loop."""
    global _DNS_TUNNEL_EXECUTOR
    if _DNS_TUNNEL_EXECUTOR is None or _DNS_TUNNEL_EXECUTOR.is_closed():
        _DNS_TUNNEL_EXECUTOR = asyncio.new_event_loop()
    return _DNS_TUNNEL_EXECUTOR


async def execute_dns_tunnel_async(args: dict) -> dict:
    """Async execution of DNS tunnel check in M1-safe manner."""

    if not isinstance(args, dict):
        return {"error": "args must be a dict", "findings": []}

    mode = args.get("mode", "analyze_queries")
    if not isinstance(mode, str) or mode != "analyze_queries":
        return {"error": f"unknown mode: {mode}", "findings": []}

    queries_raw = args.get("queries", [])
    if not isinstance(queries_raw, (list, tuple)):
        return {"error": "queries must be a list", "findings": []}

    queries: list[str] = []
    for q in queries_raw:
        if not isinstance(q, str) or not q.strip():
            continue
        if len(q) > 253:
            continue
        queries.append(q.strip())
    queries = queries[:500]
    if not queries:
        return {"findings": [], "error": "no queries provided"}

    # Lazy import for DNS tunnel detector
    try:
        from .network.dns_tunnel_detector import DNSTunnelConfig, create_dns_tunnel_detector
    except ImportError:
        return {"error": "dns_tunnel_detector not available", "findings": []}

    detector = create_dns_tunnel_detector(DNSTunnelConfig(
        enable_lstm=True,
        entropy_threshold=4.2,
        max_queries_per_batch=500
    ))

    await detector.initialize()
    findings = await detector.analyze_queries(queries)
    return {
        "findings": [
            {
                "query": f.query,
                "verdict": f.verdict.value,
                "confidence": f.confidence,
                "entropy": f.entropy,
                "encoding": f.encoding_type
            }
            for f in findings
            if f.verdict.value in ("suspicious", "malicious")
        ],
        "stats": detector.get_stats()
    }


def execute_dns_tunnel_sync(args: dict) -> dict:
    """Synchronous wrapper — runs in ThreadPoolExecutor for M1 safety."""
    try:
        loop = _get_dns_tunnel_executor()
        return loop.run_until_complete(execute_dns_tunnel_async(args))
    except Exception as e:
        return {"error": str(e), "findings": []}


# ============================================================================
# Registry Factory
# ============================================================================


def create_default_registry() -> ToolRegistry:
    """Create ToolRegistry with all built-in tools registered."""
    from tools.registry import CostModel, RateLimits, RiskLevel, Tool, ToolRegistry

    registry = ToolRegistry()

    # Web Search
    registry.register(Tool(
        name="web_search",
        description="Search the web for information.",
        args_schema=WebSearchArgs,
        returns_schema=WebSearchResult,
        cost_model=CostModel(ram_mb_est=50, time_ms_est=2000, network=True, risk_level=RiskLevel.MEDIUM),
        rate_limits=RateLimits(max_calls_per_run=50, max_parallel=5),
        handler=_web_search_handler,
    ))

    # Entity Extraction
    registry.register(Tool(
        name="entity_extraction",
        description="Extract named entities from text.",
        args_schema=EntityExtractionArgs,
        returns_schema=EntityExtractionResult,
        cost_model=CostModel(ram_mb_est=100, time_ms_est=500, network=False, risk_level=RiskLevel.LOW),
        rate_limits=RateLimits(max_calls_per_run=1000, max_parallel=10),
        handler=_entity_extraction_handler,
    ))

    # Academic Search
    registry.register(Tool(
        name="academic_search",
        description="Search academic databases.",
        args_schema=AcademicSearchArgs,
        returns_schema=AcademicSearchResult,
        cost_model=CostModel(ram_mb_est=50, time_ms_est=3000, network=True, risk_level=RiskLevel.MEDIUM),
        rate_limits=RateLimits(max_calls_per_run=30, max_parallel=3),
        handler=_academic_search_handler,
    ))

    # File Read
    registry.register(Tool(
        name="file_read",
        description="Read file contents.",
        args_schema=FileReadArgs,
        returns_schema=FileReadResult,
        cost_model=CostModel(ram_mb_est=10, time_ms_est=100, network=False, risk_level=RiskLevel.LOW),
        rate_limits=RateLimits(max_calls_per_run=1000, max_parallel=20),
        handler=_file_read_handler,
    ))

    # File Write
    registry.register(Tool(
        name="file_write",
        description="Write content to file.",
        args_schema=FileWriteArgs,
        returns_schema=FileWriteResult,
        cost_model=CostModel(ram_mb_est=10, time_ms_est=100, network=False, risk_level=RiskLevel.MEDIUM),
        rate_limits=RateLimits(max_calls_per_run=100, max_parallel=5),
        handler=_file_write_handler,
    ))

    # Python Execute
    registry.register(Tool(
        name="python_execute",
        description="Execute Python code in restricted sandbox.",
        args_schema=PythonExecuteArgs,
        returns_schema=PythonExecuteResult,
        cost_model=CostModel(ram_mb_est=50, time_ms_est=1000, network=False, risk_level=RiskLevel.HIGH),
        rate_limits=RateLimits(max_calls_per_run=20, max_parallel=1),
        handler=_python_execute_handler,
    ))

    # Capability assignments (Sprint 8SE)
    registry.get_tool("web_search").required_capabilities = {"reranking"}
    registry.get_tool("academic_search").required_capabilities = {"reranking", "entity_linking"}
    registry.get_tool("entity_extraction").required_capabilities = {"entity_linking"}

    return registry


# ============================================================================
# Built-in Tool Handlers (placeholders until moved to handlers.py)
# ============================================================================


async def _web_search_handler(
    query: str, max_results: int = 10, recency_days: int | None = None
) -> dict[str, Any]:
    """Web search - staged gap."""
    return {
        "staged": True,
        "backend_ready": False,
        "query_ready": True,
        "contract_ready": False,
        "capability_blockers": ["web_search_backend"],
        "staged_reason": "web_search backend not implemented",
        "results": [],
        "total_found": 0,
        "query": query,
    }


async def _entity_extraction_handler(
    text: str, entity_types: list[str] | None = None
) -> dict[str, Any]:
    """Entity extraction - placeholder."""
    import re
    entities = []
    entity_types = entity_types or ["person", "organization", "location"]

    if "person" in entity_types:
        for match in re.finditer(r"\b[A-Z][a-z]+\s[A-Z][a-z]+\b", text):
            entities.append({
                "text": match.group(),
                "type": "person",
                "start": match.start(),
                "end": match.end(),
            })

    return {"entities": entities, "entity_count": len(entities)}


async def _academic_search_handler(
    query: str,
    sources: list[str] | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    max_results: int = 10,
) -> dict[str, Any]:
    """Academic search - thin adapter."""
    try:
        from .intelligence.academic_search import AcademicSearchEngine
        known_sources = ["arxiv", "crossref", "semantic_scholar"]
        active_sources = [s for s in (sources or known_sources) if s in known_sources]
        if not active_sources:
            active_sources = known_sources

        engine = AcademicSearchEngine(enable_expansion=True)
        try:
            result = await engine.search(query, max_results=max_results, sources=active_sources)
            return {
                "papers": [
                    {
                        "title": r.title,
                        "url": r.url,
                        "snippet": r.snippet,
                        "source": r.source,
                        "metadata": r.metadata,
                        "relevance_score": r.relevance_score,
                    }
                    for r in result.deduplicated_results[:max_results]
                ],
                "total_found": len(result.deduplicated_results),
                "sources_searched": result.sources_used,
            }
        finally:
            await engine.cleanup()
    except ImportError:
        return {"papers": [], "total_found": 0, "sources_searched": [], "error": "module not available"}
    except Exception as e:
        return {"papers": [], "total_found": 0, "sources_searched": [], "error": str(e)}


async def _file_read_handler(
    path: str, encoding: str = "utf-8", max_bytes: int | None = None
) -> dict[str, Any]:
    """File read handler."""
    import os

    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")

    size = os.path.getsize(path)
    min(size, max_bytes) if max_bytes else size

    with open(path, encoding=encoding) as f:
        content = f.read(max_bytes) if max_bytes else f.read()

    return {"content": content, "path": path, "size_bytes": size, "encoding": encoding}


async def _file_write_handler(
    path: str, content: str, encoding: str = "utf-8", append: bool = False
) -> dict[str, Any]:
    """File write handler."""
    mode = "a" if append else "w"
    with open(path, mode, encoding=encoding) as f:
        f.write(content)

    return {"path": path, "bytes_written": len(content.encode(encoding)), "success": True}


async def _python_execute_handler(
    code: str,
    timeout_seconds: int = 30,
    allowed_modules: list[str] | None = None,
) -> dict[str, Any]:
    """Restricted Python execution."""
    import builtins
    import io
    import json
    import math
    import re
    import signal
    import sys
    import time
    import traceback

    start_time = time.time()

    class _TimeoutError(Exception):
        pass

    def _timeout_handler(signum, frame):
        raise _TimeoutError(f"Execution timed out after {timeout_seconds}s")

    _alarm_registered = False
    if timeout_seconds and 1 <= timeout_seconds <= 300:
        try:
            signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(timeout_seconds)
            _alarm_registered = True
        except (ValueError, OSError):
            pass

    safe_builtins = {
        "abs": builtins.abs, "all": builtins.all, "any": builtins.any,
        "bin": builtins.bin, "bool": builtins.bool, "bytearray": builtins.bytearray,
        "bytes": builtins.bytes, "chr": builtins.chr, "complex": builtins.complex,
        "dict": builtins.dict, "divmod": builtins.divmod, "enumerate": builtins.enumerate,
        "filter": builtins.filter, "float": builtins.float, "format": builtins.format,
        "frozenset": builtins.frozenset, "hasattr": builtins.hasattr, "hash": builtins.hash,
        "hex": builtins.hex, "int": builtins.int, "isinstance": builtins.isinstance,
        "issubclass": builtins.issubclass, "iter": builtins.iter, "len": builtins.len,
        "list": builtins.list, "map": builtins.map, "max": builtins.max,
        "min": builtins.min, "next": builtins.next, "oct": builtins.oct,
        "ord": builtins.ord, "pow": builtins.pow, "print": builtins.print,
        "range": builtins.range, "repr": builtins.repr, "reversed": builtins.reversed,
        "round": builtins.round, "set": builtins.set, "slice": builtins.slice,
        "sorted": builtins.sorted, "str": builtins.str, "sum": builtins.sum,
        "tuple": builtins.tuple, "type": builtins.type, "zip": builtins.zip,
        "math": math, "json": json, "re": re,
    }

    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()
    old_stdout, old_stderr = sys.stdout, sys.stderr
    result = None
    success = False

    try:
        sys.stdout = stdout_capture
        sys.stderr = stderr_capture
        compiled = compile(code, "<restricted>", "exec")
        exec(compiled, {"__builtins__": safe_builtins})
        if "result" in locals() or "result" in globals():
            result = locals().get("result") or globals().get("result")
        success = True
    except _TimeoutError:
        stderr_capture.write(f"TimeoutError: Execution timed out after {timeout_seconds}s\n")
        success = False
    except Exception as e:
        stderr_capture.write(f"{type(e).__name__}: {e}\n")
        stderr_capture.write(traceback.format_exc())
    finally:
        if _alarm_registered:
            signal.alarm(0)
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    execution_time = (time.time() - start_time) * 1000
    return {
        "stdout": stdout_capture.getvalue(),
        "stderr": stderr_capture.getvalue(),
        "result": result,
        "execution_time_ms": execution_time,
        "success": success,
    }


# ============================================================================
# Schemas (for backward compatibility, moving to registry.py)
# ============================================================================


class WebSearchArgs(BaseModel):
    query: str
    max_results: int = Field(default=10, ge=1, le=50)
    recency_days: int | None = Field(default=None, ge=1)


class WebSearchResult(BaseModel):
    staged: bool = False
    backend_ready: bool = False
    query_ready: bool = True
    contract_ready: bool = False
    capability_blockers: list[str] = Field(default_factory=list)
    staged_reason: str = ""
    results: list[dict[str, Any]] = Field(default_factory=list)
    total_found: int = 0
    query: str = ""


class EntityExtractionArgs(BaseModel):
    text: str
    entity_types: list[str] = Field(default=["person", "organization", "location"])


class EntityExtractionResult(BaseModel):
    entities: list[dict[str, Any]]
    entity_count: int


class AcademicSearchArgs(BaseModel):
    query: str
    sources: list[str] = Field(default=["arxiv", "semantic_scholar"])
    year_from: int | None = None
    year_to: int | None = None
    max_results: int = Field(default=10, ge=1, le=100)


class AcademicSearchResult(BaseModel):
    papers: list[dict[str, Any]]
    total_found: int
    sources_searched: list[str]


class FileReadArgs(BaseModel):
    path: str
    encoding: str = "utf-8"
    max_bytes: int | None = Field(default=None, ge=1)


class FileReadResult(BaseModel):
    content: str
    path: str
    size_bytes: int
    encoding: str


class FileWriteArgs(BaseModel):
    path: str
    content: str
    encoding: str = "utf-8"
    append: bool = False


class FileWriteResult(BaseModel):
    path: str
    bytes_written: int
    success: bool


class PythonExecuteArgs(BaseModel):
    code: str
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    allowed_modules: list[str] = Field(default_factory=list)


class PythonExecuteResult(BaseModel):
    stdout: str
    stderr: str
    result: Any
    execution_time_ms: float
    success: bool


class DNSTunnelCheckArgs(BaseModel):
    mode: str = "analyze_queries"
    queries: list[str] = Field(default_factory=list)


class DNSTunnelCheckResult(BaseModel):
    findings: list[dict[str, Any]] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


__all__ = [
    "ToolExecutor",
    "execute_dns_tunnel_sync",
    "execute_dns_tunnel_async",
    "create_default_registry",
]
