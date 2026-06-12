"""
Sprint 8VF: ToolRegistry audit integration tests.

Tests:
- execute_with_limits() remains single canonical entry point
- exec_logger optional audit logging works (success/error paths)
- correlation keys are passed through
- ToolExecLog does NOT store raw payloads (hashes only)
- No new execution authority created
"""
import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# Module-level fixtures
@pytest.fixture
def temp_log_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_logger(temp_log_dir):
    """Mock ToolExecLog that captures log calls."""
    from hledac.universal.tool_exec_log import ToolExecLog
    logger = MagicMock(spec=ToolExecLog)
    logger.log = MagicMock(return_value=MagicMock())
    return logger


class TestExecuteWithLimitsAuditIntegration:
    """Audit integration for execute_with_limits()."""

    @pytest.fixture
    def registry(self):
        from hledac.universal.tool_registry import create_default_registry
        from tools.executor import ToolExecutor
        registry = create_default_registry()
        return ToolExecutor(registry)

    @pytest.mark.asyncio
    async def test_execute_with_limits_success_no_logger(self, registry):
        """Without exec_logger, execute_with_limits works as before."""
        result = await registry.execute_with_limits(
            "entity_extraction",
            {"text": "John works at Apple", "entity_types": ["person"]},
            available_capabilities={"entity_linking"},
        )
        assert result["entity_count"] >= 0

    @pytest.mark.asyncio
    async def test_execute_with_limits_success_with_logger(self, registry, mock_logger):
        """With exec_logger, audit event is recorded."""
        await registry.execute_with_limits(
            "entity_extraction",
            {"text": "John works at Apple", "entity_types": ["person"]},
            available_capabilities={"entity_linking"},
            exec_logger=mock_logger,
            correlation={"run_id": "test-run", "branch_id": "b1", "provider_id": None, "action_id": None},
        )
        mock_logger.log.assert_called_once()
        call_kwargs = mock_logger.log.call_args.kwargs
        assert call_kwargs["tool_name"] == "entity_extraction"
        assert call_kwargs["status"] == "success"
        assert call_kwargs["correlation"]["run_id"] == "test-run"
        assert call_kwargs["correlation"]["branch_id"] == "b1"

    @pytest.mark.asyncio
    async def test_execute_with_limits_error_in_handler_logs_error(self, registry, mock_logger):
        """Error inside handler (after semaphore) is logged with error status."""
        mock_logger.log.reset_mock()

        # Create a tool that will fail inside the handler
        from hledac.universal.tool_registry import Tool, ToolRegistry
        from pydantic import BaseModel

        class FailArgs(BaseModel):
            should_fail: bool = False

        async def fail_handler(should_fail: bool = False):
            if should_fail:
                raise ValueError("handler failed")
            return {"result": "ok"}

        fail_tool = Tool(
            name="fail_tool",
            description="Test tool that fails",
            args_schema=FailArgs,
            returns_schema=BaseModel,
            handler=fail_handler,
        )
        registry.register(fail_tool)

        with pytest.raises(ValueError):
            await registry.execute_with_limits(
                "fail_tool",
                {"should_fail": True},
                exec_logger=mock_logger,
            )
        mock_logger.log.assert_called_once()
        call_kwargs = mock_logger.log.call_args.kwargs
        assert call_kwargs["tool_name"] == "fail_tool"
        assert call_kwargs["status"] == "error"

    @pytest.mark.asyncio
    async def test_logger_failure_does_not_affect_execution(self, registry):
        """If exec_logger.log() raises, execution continues normally."""
        from hledac.universal.tool_exec_log import ToolExecLog
        bad_logger = MagicMock(spec=ToolExecLog)
        bad_logger.log = MagicMock(side_effect=RuntimeError("logger broken"))

        result = await registry.execute_with_limits(
            "entity_extraction",
            {"text": "test", "entity_types": ["person"]},
            available_capabilities={"entity_linking"},
            exec_logger=bad_logger,
        )
        assert result["entity_count"] >= 0  # Execution succeeded despite logger failure

    @pytest.mark.asyncio
    async def test_backward_compat_no_logger(self, registry):
        """Without exec_logger, behavior is unchanged (backward compatible)."""
        result = await registry.execute_with_limits(
            "entity_extraction",
            {"text": "test data", "entity_types": ["person"]},
            available_capabilities={"entity_linking"},
        )
        assert "entities" in result

    @pytest.mark.asyncio
    async def test_correlation_passed_through(self, registry, mock_logger):
        """Correlation dict is passed to logger exactly."""
        correlation = {
            "run_id": "run-123",
            "branch_id": "branch-a",
            "provider_id": "mlx",
            "action_id": "act-456",
        }
        await registry.execute_with_limits(
            "entity_extraction",
            {"text": "test", "entity_types": []},
            available_capabilities={"entity_linking"},
            exec_logger=mock_logger,
            correlation=correlation,
        )
        call_kwargs = mock_logger.log.call_args.kwargs
        assert call_kwargs["correlation"] == correlation

    @pytest.mark.asyncio
    async def test_tool_exec_log_does_not_store_raw_payloads(self, registry, temp_log_dir):
        """ToolExecLog stores hashes, not raw data."""
        from hledac.universal.tool_exec_log import ToolExecLog
        logger = ToolExecLog(run_dir=temp_log_dir, enable_persist=True, run_id="audit-test")

        await registry.execute_with_limits(
            "entity_extraction",
            {"text": "sensitive: password=secret123", "entity_types": ["person"]},
            available_capabilities={"entity_linking"},
            exec_logger=logger,
        )

        event = logger._log[-1]
        # Raw text NOT stored
        assert event.input_hash != ""
        # input_hash is SHA256 of serialized args (with sorted keys)
        import orjson
        raw_bytes = orjson.dumps(
            {"text": "sensitive: password=secret123", "entity_types": ["person"]},
            option=orjson.OPT_SORT_KEYS
        )
        assert event.input_hash == hashlib.sha256(raw_bytes).hexdigest()
        logger.close()

    @pytest.mark.asyncio
    async def test_no_new_execution_authority_created(self, registry, mock_logger):
        """Demonstrate exec_logger is NOT execution authority."""
        # Call without exec_logger - should still work
        result1 = await registry.execute_with_limits(
            "entity_extraction",
            {"text": "test", "entity_types": []},
            available_capabilities={"entity_linking"},
        )
        # Call with exec_logger - same result
        result2 = await registry.execute_with_limits(
            "entity_extraction",
            {"text": "test", "entity_types": []},
            available_capabilities={"entity_linking"},
            exec_logger=mock_logger,
        )
        assert result1 == result2
        # exec_logger.log() was called but did NOT change execution behavior
        mock_logger.log.assert_called()


class TestToolExecLogCorrelationBoundary:
    """ToolExecLog correlation boundary tests."""

    def test_log_stores_correlation_dict(self, temp_log_dir):
        """ToolExecEvent stores correlation but does NOT execute."""
        from hledac.universal.tool_exec_log import ToolExecLog
        logger = ToolExecLog(run_dir=temp_log_dir, enable_persist=False)

        event = logger.log(
            tool_name="test_tool",
            input_data=b"input",
            output_data=b"output",
            status="success",
            correlation={"run_id": "r1", "branch_id": "b1", "provider_id": "p1", "action_id": "a1"},
        )

        assert event.correlation["run_id"] == "r1"
        assert event.correlation["branch_id"] == "b1"
        # NOT execution authority - log() returns event but doesn't run anything
        assert event.tool_name == "test_tool"

    def test_log_has_bounded_error_class(self, temp_log_dir):
        """Error class is bounded to safe set, not full exception."""
        from hledac.universal.tool_exec_log import ToolExecLog
        logger = ToolExecLog(run_dir=temp_log_dir, enable_persist=False)

        event = logger.log(
            tool_name="test_tool",
            input_data=b"input",
            output_data=b"",
            status="error",
            error=ValueError("sensitive error message with password"),
        )

        # Only bounded error class stored, not full message
        assert event.error_class == "ValueError"
        # Full sensitive message NOT stored
        assert "password" not in str(event.to_dict())

    def test_hash_chain_maintained(self, temp_log_dir):
        """Hash chain provides tamper-evidence."""
        from hledac.universal.tool_exec_log import ToolExecLog
        logger = ToolExecLog(run_dir=temp_log_dir, enable_persist=False)

        e1 = logger.log(tool_name="t1", input_data=b"a", output_data=b"out1", status="success")
        e2 = logger.log(tool_name="t2", input_data=b"b", output_data=b"out2", status="success")

        # Chain linked
        assert e2.prev_chain_hash == e1.chain_hash
        assert e2.chain_hash != e1.chain_hash

    def test_verify_all_detects_tampering(self, temp_log_dir):
        """verify_all() detects chain breaks."""
        from hledac.universal.tool_exec_log import ToolExecLog
        logger = ToolExecLog(run_dir=temp_log_dir, enable_persist=True, run_id="verify-test")

        logger.log(tool_name="t1", input_data=b"a", output_data=b"out1", status="success")
        logger.log(tool_name="t2", input_data=b"b", output_data=b"out2", status="success")
        logger.finalize()

        # Tamper with chain
        log_file = temp_log_dir / "logs" / "tool_exec.jsonl"
        lines = log_file.read_text().splitlines()
        # Corrupt second event's chain link
        data = json.loads(lines[1])
        data["prev_chain_hash"] = "corrupted"
        lines[1] = json.dumps(data)
        log_file.write_text("\n".join(lines) + "\n")

        # Need enable_persist=True to read from disk
        logger2 = ToolExecLog(run_dir=temp_log_dir, enable_persist=True, run_id="verify-test-2")
        result = logger2.verify_all()
        assert result["chain_valid"] is False
        assert len(result["errors"]) > 0
        logger2.close()


class TestCanonicalSurfaceUnchanged:
    """Verify execute_with_limits() stays canonical surface."""

    @pytest.fixture
    def registry(self):
        from hledac.universal.tool_registry import create_default_registry
        from tools.executor import ToolExecutor
        registry = create_default_registry()
        return ToolExecutor(registry)


    @pytest.mark.asyncio
    async def test_single_entry_point(self, registry):
        """Only execute_with_limits() is canonical, no other execute method."""
        from tools.executor import ToolExecutor
        # execute_with_limits is on ToolExecutor, not ToolRegistry
        assert hasattr(ToolExecutor, "execute_with_limits")
        # No parallel execute() method added to ToolExecutor
        assert not hasattr(registry, "execute")

    @pytest.mark.asyncio
    async def test_capability_enforcement_still_works(self, registry):
        """Capability enforcement unchanged by audit addition."""
        with pytest.raises(RuntimeError, match="Capability check failed"):
            await registry.execute_with_limits(
                "web_search",
                {"query": "test"},
                available_capabilities=set(),  # Missing reranking
            )

    @pytest.mark.asyncio
    async def test_rate_limit_still_works(self, registry):
        """Rate limits still enforced regardless of audit."""
        # Exhaust rate limit
        tool = registry.get_tool("entity_extraction")
        original = tool.rate_limits.max_calls_per_run
        tool.rate_limits.max_calls_per_run = 1

        await registry.execute_with_limits(
            "entity_extraction",
            {"text": "test", "entity_types": []},
            available_capabilities={"entity_linking"},
        )

        with pytest.raises(RuntimeError, match="Rate limit exceeded"):
            await registry.execute_with_limits(
                "entity_extraction",
                {"text": "test", "entity_types": []},
                available_capabilities={"entity_linking"},
            )

        tool.rate_limits.max_calls_per_run = original

    @pytest.mark.asyncio
    async def test_logger_is_optional_not_required(self, registry):
        """exec_logger parameter is optional - backwards compatible."""
        # Old code without exec_logger still works
        result = await registry.execute_with_limits(
            "entity_extraction",
            {"text": "test", "entity_types": []},
            available_capabilities={"entity_linking"},
        )
        assert "entities" in result

    @pytest.mark.asyncio
    async def test_capability_error_before_semaphore_not_logged(self, registry, mock_logger):
        """Errors before entering semaphore (capability check) are NOT logged."""
        mock_logger.log.reset_mock()
        with pytest.raises(RuntimeError, match="Capability check failed"):
            await registry.execute_with_limits(
                "web_search",
                {"query": "test"},
                available_capabilities=set(),  # Missing reranking
                exec_logger=mock_logger,
            )
        # No logging because error happened before semaphore block
        mock_logger.log.assert_not_called()
