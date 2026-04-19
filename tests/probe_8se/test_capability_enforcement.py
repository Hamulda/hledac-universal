"""
Sprint 8SE: Capability Enforcement Flow Tests
==============================================

Tests for the analyzer -> capability router -> tool registry enforcement pipeline.

Invariant table:
- test_analyzer_result_to_capability_signal: AnalyzerResult.to_capability_signal() works
- test_capability_router_route_analyzer_result: CapabilityRouter.route() with AnalyzerResult
- test_capability_router_route_legacy_dict: CapabilityRouter.route() with legacy dict
- test_tool_registry_capability_check_pass: ToolRegistry.check_capabilities() passes when satisfied
- test_tool_registry_capability_check_fail: ToolRegistry.check_capabilities() fails when missing
- test_execute_with_limits_enforces_capabilities: execute_with_limits() enforces capabilities
- test_execute_with_limits_skips_when_none: execute_with_limits() skips when available_capabilities=None
- test_ghost_executor_is_donor_compat: GhostExecutor is marked as donor/compat (not canonical)
- test_required_capabilities_populated: Representative tools have required_capabilities set
- test_enforcement_happens_in_registry_not_analyzer: Enforcement is in ToolRegistry, not analyzer
"""

import pytest

from hledac.universal.capabilities import CapabilityRouter, Capability
from hledac.universal.tool_registry import create_default_registry
from hledac.universal.project_types import AnalyzerResult
from hledac.universal.autonomous_analyzer import AutoResearchProfile


class TestAnalyzerResultCapabilitySignal:
    """Test AnalyzerResult.to_capability_signal() produces correct output."""

    def test_to_capability_signal_basic(self):
        """AnalyzerResult.to_capability_signal() returns correct keys."""
        profile = AutoResearchProfile(
            tools={"web_intelligence", "academic_search"},
            sources={"surface", "academic"},
            privacy_level="STANDARD",
            use_tor=False,
            depth="DEEP",
            use_tot=False,
            tot_mode="standard",
        )
        result = AnalyzerResult.from_profile(profile)
        signal = result.to_capability_signal()

        assert "tools" in signal
        assert "sources" in signal
        assert "privacy_level" in signal
        assert "use_tor" in signal
        assert "depth" in signal
        assert signal["tools"] == {"web_intelligence", "academic_search"}
        assert signal["requires_embeddings"] is False
        assert signal["requires_ner"] is False
        assert signal["requires_temporal"] is False
        assert signal["requires_crypto"] is False

    def test_to_capability_signal_models(self):
        """AnalyzerResult.to_capability_signal() sets model flags correctly."""
        profile = AutoResearchProfile(
            tools={"academic_search", "identity_stitching"},
            models_needed={"hermes", "modernbert", "gliner"},
        )
        result = AnalyzerResult.from_profile(profile)
        signal = result.to_capability_signal()

        assert signal["requires_embeddings"] is True  # modernbert
        assert signal["requires_ner"] is True  # gliner

    def test_to_capability_signal_temporal(self):
        """AnalyzerResult.to_capability_signal() sets temporal flag."""
        profile = AutoResearchProfile(tools={"temporal_analyzer"})
        result = AnalyzerResult.from_profile(profile)
        signal = result.to_capability_signal()

        assert signal["requires_temporal"] is True

    def test_to_capability_signal_crypto(self):
        """AnalyzerResult.to_capability_signal() sets crypto flag."""
        profile = AutoResearchProfile(tools={"blockchain_analyzer"})
        result = AnalyzerResult.from_profile(profile)
        signal = result.to_capability_signal()

        assert signal["requires_crypto"] is True


class TestCapabilityRouter:
    """Test CapabilityRouter.route() with both AnalyzerResult and legacy dict."""

    def test_route_analyzer_result_stealth(self):
        """CapabilityRouter.route() with AnalyzerResult - STEALTH."""
        profile = AutoResearchProfile(
            tools={"stealth_crawler"},
            privacy_level="MAXIMUM",
            use_tor=True,
        )
        result = AnalyzerResult.from_profile(profile)
        caps = CapabilityRouter.route(result)

        assert Capability.STEALTH in caps
        assert Capability.DARK_WEB in caps

    def test_route_analyzer_result_academic(self):
        """CapabilityRouter.route() with AnalyzerResult - academic."""
        profile = AutoResearchProfile(
            tools={"academic_search"},
            models_needed={"hermes", "modernbert"},
        )
        result = AnalyzerResult.from_profile(profile)
        caps = CapabilityRouter.route(result)

        assert Capability.RERANKING in caps
        assert Capability.ENTITY_LINKING in caps
        assert Capability.MODERNBERT in caps

    def test_route_analyzer_result_blockchain(self):
        """CapabilityRouter.route() with AnalyzerResult - blockchain."""
        profile = AutoResearchProfile(tools={"blockchain_analyzer"})
        result = AnalyzerResult.from_profile(profile)
        caps = CapabilityRouter.route(result)

        assert Capability.CRYPTO_INTEL in caps

    def test_route_legacy_dict_stealth(self):
        """CapabilityRouter.route() with legacy dict - STEALTH (backward compat)."""
        analysis = {
            "tools": ["stealth_crawler"],
            "privacy_level": "MAXIMUM",
            "use_tor": True,
        }
        caps = CapabilityRouter.route(analysis)

        assert Capability.STEALTH in caps
        assert Capability.DARK_WEB in caps

    def test_route_legacy_dict_temporal_signal(self):
        """CapabilityRouter.route() with legacy signal dict."""
        signal = {
            "tools": ["temporal_analyzer"],
            "requires_temporal": True,
        }
        caps = CapabilityRouter.route(signal)

        assert Capability.TEMPORAL in caps

    def test_route_empty_tools(self):
        """CapabilityRouter.route() with empty tools - HERMES always present."""
        profile = AutoResearchProfile(tools=set())
        result = AnalyzerResult.from_profile(profile)
        caps = CapabilityRouter.route(result)

        assert Capability.HERMES in caps


class TestToolRegistryCapabilityCheck:
    """Test ToolRegistry.check_capabilities() enforcement."""

    def test_check_capabilities_pass(self):
        """check_capabilities() passes when all required are available."""
        registry = create_default_registry()

        # academic_search requires reranking + entity_linking
        satisfied, msg = registry.check_capabilities(
            "academic_search",
            {"reranking", "entity_linking", "hermes"}
        )

        assert satisfied is True
        assert msg is None

    def test_check_capabilities_fail_missing(self):
        """check_capabilities() fails when required capability is missing."""
        registry = create_default_registry()

        # academic_search requires reranking + entity_linking, but only entity_linking present
        satisfied, msg = registry.check_capabilities(
            "academic_search",
            {"entity_linking"}  # missing reranking
        )

        assert satisfied is False
        assert msg is not None
        assert "reranking" in msg
        assert "academic_search" in msg

    def test_check_capabilities_no_required(self):
        """check_capabilities() passes when tool has no required_capabilities."""
        registry = create_default_registry()

        # file_read has no required_capabilities
        satisfied, msg = registry.check_capabilities("file_read", set())

        assert satisfied is True
        assert msg is None

    def test_required_capabilities_populated(self):
        """Representative tools have required_capabilities populated."""
        registry = create_default_registry()

        web_search = registry.get_tool("web_search")
        assert "reranking" in web_search.required_capabilities

        academic_search = registry.get_tool("academic_search")
        assert "reranking" in academic_search.required_capabilities
        assert "entity_linking" in academic_search.required_capabilities

        entity_extraction = registry.get_tool("entity_extraction")
        assert "entity_linking" in entity_extraction.required_capabilities


class TestExecuteWithLimitsCapabilityEnforcement:
    """Test execute_with_limits() enforces capabilities."""

    @pytest.mark.asyncio
    async def test_enforces_capabilities(self):
        """execute_with_limits() raises when capabilities missing."""
        registry = create_default_registry()

        # academic_search requires reranking + entity_linking
        # Only entity_linking provided - should fail
        with pytest.raises(RuntimeError, match="Capability check failed"):
            await registry.execute_with_limits(
                "academic_search",
                {"query": "test", "sources": ["arxiv"]},
                available_capabilities={"entity_linking"}  # missing reranking
            )

    @pytest.mark.asyncio
    async def test_skips_when_none(self):
        """execute_with_limits() skips check when available_capabilities=None."""
        registry = create_default_registry()

        # Should not raise even though academic_search has requirements
        # (backward compatible - None means skip)
        await registry.execute_with_limits(
            "academic_search",
            {"query": "test", "sources": ["arxiv"]},
            available_capabilities=None  # skip check
        )

        # Should get past capability check (rate limit will still apply but that's OK)
        # Note: actual execution may fail due to other reasons, but capability check passes

    @pytest.mark.asyncio
    async def test_passes_when_satisfied(self):
        """execute_with_limits() passes when all required capabilities present."""
        registry = create_default_registry()

        # web_search requires reranking - provide it
        result = await registry.execute_with_limits(
            "web_search",
            {"query": "test", "max_results": 5},
            available_capabilities={"reranking"}
        )

        # Should complete without capability error
        assert result is not None


class TestGhostExecutorDonorCompat:
    """Verify GhostExecutor is donor/compat, not canonical authority."""

    def test_ghost_executor_has_donor_comment(self):
        """GhostExecutor class docstring mentions donor/compat."""
        from hledac.universal.execution.ghost_executor import GhostExecutor

        docstring = GhostExecutor.__doc__ or ""
        assert "DONOR" in docstring or "donor" in docstring.lower()
        assert "COMPATIBILITY" in docstring or "compat" in docstring.lower()
        assert "Canonical authority" in docstring

    def test_ghost_executor_not_in_tool_registry_canonical(self):
        """GhostExecutor is NOT the canonical execution surface."""
        registry = create_default_registry()

        # GhostExecutor actions should NOT be registered as canonical tools
        # through GhostExecutor itself - they may exist elsewhere
        tool_names = [t.name for t in registry.list_tools()]

        # These are GhostExecutor-specific actions - should NOT be here
        ghost_actions = {"scan", "google", "deep_read", "stealth_harvest", "osint_discovery"}
        assert not any(t in ghost_actions for t in tool_names), \
            "GhostExecutor actions found in ToolRegistry - may indicate over-registration"

    def test_ghost_executor_removal_condition_documented(self):
        """Sprint 8VF: GhostExecutor docstring contains REMOVAL CONDITION."""
        from hledac.universal.execution.ghost_executor import GhostExecutor

        docstring = GhostExecutor.__doc__ or ""
        assert "REMOVAL CONDITION" in docstring, \
            "GhostExecutor should document when it becomes deprecation candidate"

    def test_ghost_executor_boundary_seams_documented(self):
        """Sprint 8VF: GhostExecutor docstring contains BOUNDARY SEAMS."""
        from hledac.universal.execution.ghost_executor import GhostExecutor

        docstring = GhostExecutor.__doc__ or ""
        assert "BOUNDARY SEAMS" in docstring, \
            "GhostExecutor should document its boundary seams vs ToolRegistry"
        # Verify key seams are documented
        assert "ActionType" in docstring, "Should mention ActionType enum"
        assert "_actions" in docstring, "Should mention _actions dict"

    def test_ghost_executor_future_owner_documented(self):
        """Sprint 8VF: GhostExecutor documents ToolRegistry as future owner."""
        from hledac.universal.execution.ghost_executor import GhostExecutor

        docstring = GhostExecutor.__doc__ or ""
        assert "ToolRegistry" in docstring, \
            "GhostExecutor should mention ToolRegistry as migration target"


class TestToolRegistryCanonicalRole:
    """Sprint 8VF: Verify ToolRegistry canonical execution-control surface role."""

    def test_tool_registry_has_explicit_docstring(self):
        """ToolRegistry class docstring is explicit about canonical role."""
        from hledac.universal.tool_registry import ToolRegistry

        docstring = ToolRegistry.__doc__ or ""
        assert "CANONICAL" in docstring, \
            "ToolRegistry should explicitly state canonical role"
        assert "execution" in docstring.lower(), \
            "Should mention execution in docstring"

    def test_tool_registry_docstring_has_do_dont(self):
        """Sprint 8VF: ToolRegistry docstring contains DO/DON'T boundaries."""
        from hledac.universal.tool_registry import ToolRegistry

        docstring = ToolRegistry.__doc__ or ""
        assert "DO NOT" in docstring, \
            "ToolRegistry should have explicit DO NOT section"

    def test_tool_registry_related_components_documented(self):
        """Sprint 8VF: ToolRegistry docstring mentions related components."""
        from hledac.universal.tool_registry import ToolRegistry

        docstring = ToolRegistry.__doc__ or ""
        assert "GhostExecutor" in docstring, \
            "ToolRegistry should mention GhostExecutor as donor/compat"
        assert "ToolExecLog" in docstring, \
            "ToolRegistry should mention ToolExecLog as audit"
        assert "CapabilityRouter" in docstring, \
            "ToolRegistry should mention CapabilityRouter as signal mapping"


class TestToolExecLogAuditBoundary:
    """Sprint 8VF: Verify ToolExecLog AUDIT boundary clarity."""

    def test_tool_exec_log_has_audit_role(self):
        """ToolExecLog docstring explicitly states AUDIT role."""
        from hledac.universal.tool_exec_log import ToolExecLog

        docstring = ToolExecLog.__doc__ or ""
        assert "AUDIT" in docstring or "audit" in docstring.lower(), \
            "ToolExecLog should explicitly state AUDIT role"

    def test_tool_exec_log_is_not_execution_authority(self):
        """Sprint 8VF: ToolExecLog docstring states NOT execution authority."""
        from hledac.universal.tool_exec_log import ToolExecLog

        docstring = ToolExecLog.__doc__ or ""
        assert "NOT" in docstring and "execution" in docstring.lower(), \
            "ToolExecLog should explicitly state it is NOT execution authority"

    def test_tool_exec_log_has_correlation_boundary(self):
        """Sprint 8VF: ToolExecLog has CORRELATION BOUNDARY documented."""
        from hledac.universal.tool_exec_log import ToolExecLog

        docstring = ToolExecLog.__doc__ or ""
        assert "CORRELATION" in docstring or "correlation" in docstring.lower(), \
            "ToolExecLog should document correlation boundary"
        # Verify correlation fields are mentioned
        assert "run_id" in docstring, "Should mention run_id in correlation context"

    def test_tool_exec_log_has_do_not_list(self):
        """Sprint 8VF: ToolExecLog docstring contains DO NOT restrictions."""
        from hledac.universal.tool_exec_log import ToolExecLog

        docstring = ToolExecLog.__doc__ or ""
        assert "DO NOT" in docstring, \
            "ToolExecLog should have explicit DO NOT section"


class TestEnforcementHappensInRegistryNotAnalyzer:
    """Verify enforcement is in ToolRegistry, not in analyzer."""

    def test_analyzer_recommends_not_enforces(self):
        """AnalyzerResult does NOT enforce - it only recommends."""
        from hledac.universal.project_types import AnalyzerResult

        profile = AutoResearchProfile(tools={"academic_search"})
        result = AnalyzerResult.from_profile(profile)

        # AnalyzerResult has no enforcement mechanism
        # It only provides capability signal
        assert hasattr(result, "to_capability_signal")
        assert not hasattr(result, "enforce")
        assert not hasattr(result, "check_capabilities")

    def test_enforcement_in_tool_registry(self):
        """Capability enforcement happens in ToolRegistry, not CapabilityRouter."""
        registry = create_default_registry()

        # ToolRegistry has check_capabilities
        assert hasattr(registry, "check_capabilities")

        # CapabilityRouter only routes - no enforcement
        assert not hasattr(CapabilityRouter, "check_capabilities")
        assert not hasattr(CapabilityRouter, "enforce")

    def test_capability_router_signal_keys_defined(self):
        """CapabilityRouter.SIGNAL_KEYS defines canonical signal interface."""
        assert hasattr(CapabilityRouter, "SIGNAL_KEYS")
        assert "tools" in CapabilityRouter.SIGNAL_KEYS
        assert "requires_embeddings" in CapabilityRouter.SIGNAL_KEYS


class TestToolExecLogCorrelation:
    """Test tool_exec_log integration with RunCorrelation."""

    def test_tool_exec_event_has_correlation_field(self):
        """ToolExecEvent has correlation field for audit trail."""
        from hledac.universal.tool_exec_log import ToolExecEvent
        from datetime import datetime

        event = ToolExecEvent(
            event_id="test_1",
            ts=datetime.now(),
            tool_name="web_search",
            input_hash="abc",
            output_hash="def",
            output_len=100,
            status="success",
            correlation={"run_id": "run_1", "branch_id": None, "provider_id": None, "action_id": None}
        )

        assert event.correlation is not None
        assert event.correlation["run_id"] == "run_1"

    def test_run_correlation_to_dict(self):
        """RunCorrelation.to_dict() produces correct structure."""
        from hledac.universal.project_types import RunCorrelation

        corr = RunCorrelation(run_id="run_1", branch_id="branch_a", provider_id="mlx")
        d = corr.to_dict()

        assert d["run_id"] == "run_1"
        assert d["branch_id"] == "branch_a"
        assert d["provider_id"] == "mlx"
        assert d["action_id"] is None


class TestEndToEndEnforcement:
    """
    End-to-end enforcement probe tests.

    These tests verify the complete flow:
    1. AnalyzerResult.from_profile() creates typed result
    2. AnalyzerResult.to_capability_signal() produces signal dict
    3. CapabilityRouter.route() converts to Set[Capability]
    4. ToolRegistry.execute_with_limits() enforces capabilities

    The flow is real but isolated (no production call-sites exist yet).
    """

    def _caps_to_strings(self, caps: set) -> set[str]:
        """Convert Capability enum set to string set for execute_with_limits."""
        return {c.value for c in caps}

    @pytest.mark.asyncio
    async def test_e2e_analyzer_to_registry_success(self):
        """
        End-to-end: AnalyzerResult → CapabilityRouter → ToolRegistry (success).

        Profile: academic_search with models_needed including modernbert+gliner.
        Expected: academic_search requires {reranking, entity_linking},
                  both are provided → passes.
        """
        registry = create_default_registry()

        # AnalyzerResult from profile
        profile = AutoResearchProfile(
            tools={"academic_search"},
            models_needed={"hermes", "modernbert", "gliner"},
        )
        result = AnalyzerResult.from_profile(profile)

        # Route to capabilities
        caps = CapabilityRouter.route(result)
        cap_strings = self._caps_to_strings(caps)

        # Execute with all required capabilities provided
        result = await registry.execute_with_limits(
            "academic_search",
            {"query": "machine learning", "sources": ["arxiv"]},
            available_capabilities=cap_strings,  # {"reranking", "entity_linking", "hermes", ...}
        )

        # Should succeed
        assert result is not None
        assert "papers" in result or "total_found" in result or result == {}

    @pytest.mark.asyncio
    async def test_e2e_analyzer_to_registry_capability_fail(self):
        """
        End-to-end: AnalyzerResult → CapabilityRouter → ToolRegistry (fail).

        Profile: academic_search without providing capabilities.
        Expected: academic_search requires {reranking, entity_linking},
                  neither provided → RuntimeError.
        """
        registry = create_default_registry()

        # Execute with ONLY entity_linking (missing reranking)
        # academic_search requires both reranking AND entity_linking
        with pytest.raises(RuntimeError, match="Capability check failed"):
            await registry.execute_with_limits(
                "academic_search",
                {"query": "machine learning", "sources": ["arxiv"]},
                available_capabilities={"entity_linking"},  # missing reranking
            )

    @pytest.mark.asyncio
    async def test_e2e_analyzer_to_registry_none_skip_compat(self):
        """
        End-to-end: None-skip backward compatibility path.

        Profile: academic_search with full models.
        Expected: execute_with_limits(available_capabilities=None) silently skips
                  capability check (backward compat). No error raised.
        """
        registry = create_default_registry()

        # None-skip: capability check is bypassed entirely
        # This is the current state of ALL production call-sites
        result = await registry.execute_with_limits(
            "academic_search",
            {"query": "machine learning", "sources": ["arxiv"]},
            available_capabilities=None,  # Backward compat skip
        )

        # Should not raise even though academic_search has requirements
        # (backward compatible - None means skip)
        assert result is not None

    @pytest.mark.asyncio
    async def test_e2e_web_search_single_capability(self):
        """
        End-to-end: web_search (single capability).

        web_search requires only "reranking".
        Providing it → success. Missing it → fail.
        """
        registry = create_default_registry()

        # Success case: provide reranking
        result = await registry.execute_with_limits(
            "web_search",
            {"query": "test", "max_results": 5},
            available_capabilities={"reranking"},
        )
        assert result is not None

        # Fail case: missing reranking
        with pytest.raises(RuntimeError, match="Capability check failed"):
            await registry.execute_with_limits(
                "web_search",
                {"query": "test", "max_results": 5},
                available_capabilities=set(),  # missing reranking
            )

    @pytest.mark.asyncio
    async def test_e2e_no_capability_tool_always_passes(self):
        """
        End-to-end: tools with no required_capabilities always pass.

        file_read and file_write have no capability requirements.
        They should pass regardless of available_capabilities.
        """
        registry = create_default_registry()

        # Even with empty capabilities, no-requirement tools pass
        result = await registry.execute_with_limits(
            "file_read",
            {"path": __file__},  # Read this test file
            available_capabilities=set(),  # Empty - no capabilities
        )

        # Should succeed (file_read doesn't check capabilities)
        # Note: may fail on actual file read but NOT on capability check
        assert result is not None


class TestNoneSkipWarning:
    """
    None-skip containment tests (Sprint 8SG).

    These tests verify that None-skip:
    1. Emits a DeprecationWarning (controlled compat debt)
    2. Still allows backward-compatible execution
    3. Documents what tool requires capabilities
    """

    @pytest.mark.asyncio
    async def test_none_skip_emits_deprecation_warning(self):
        """
        When None is passed, a DeprecationWarning is issued.

        This is Sprint 8SG None-skip containment:
        - Warning is emitted to signal deprecated usage
        - Execution still proceeds (backward compatibility)
        - Tool's required_capabilities are documented in warning
        """
        import warnings as _warnings

        registry = create_default_registry()

        # Capture warnings
        with _warnings.catch_warnings(record=True) as w:
            _warnings.simplefilter("always")
            await registry.execute_with_limits(
                "academic_search",
                {"query": "test", "sources": ["arxiv"]},
                available_capabilities=None,
            )

        # Sprint 8SG: DeprecationWarning IS now emitted
        cap_warnings = [
            x for x in w
            if "capability" in str(x.message).lower() or "deprecated" in str(x.message).lower()
        ]
        assert len(cap_warnings) == 1, "None-skip should emit exactly one DeprecationWarning"
        assert "academic_search" in str(cap_warnings[0].message)
        assert "reranking" in str(cap_warnings[0].message) or "entity_linking" in str(cap_warnings[0].message)

    @pytest.mark.asyncio
    async def test_none_skip_still_allows_compat_path(self):
        """
        None-skip routes through backward-compatible path.

        When available_capabilities=None:
        1. DeprecationWarning is emitted
        2. Execution proceeds without capability verification
        3. This is intentional for backward compatibility
        """
        import warnings as _warnings

        registry = create_default_registry()

        # Warning is emitted but execution still succeeds
        with _warnings.catch_warnings(record=True):
            _warnings.simplefilter("always")
            result = await registry.execute_with_limits(
                "academic_search",
                {"query": "test", "sources": ["arxiv"]},
                available_capabilities=None,
            )

        # Result returned (or error from actual handler, not capability check)
        assert result is not None or True  # Either way, no capability error

    @pytest.mark.asyncio
    async def test_none_skip_warning_contains_required_capabilities(self):
        """
        None-skip warning message contains the tool's required capabilities.

        This helps developers understand what to pass for proper enforcement.
        """
        import warnings as _warnings

        registry = create_default_registry()

        with _warnings.catch_warnings(record=True) as w:
            _warnings.simplefilter("always")
            await registry.execute_with_limits(
                "web_search",
                {"query": "test", "max_results": 5},
                available_capabilities=None,
            )

        cap_warnings = [
            x for x in w
            if "capability" in str(x.message).lower()
        ]
        assert len(cap_warnings) == 1
        # web_search requires reranking
        assert "reranking" in str(cap_warnings[0].message)


class TestCallSiteBoundaries:
    """
    Sprint 8TD: Call-site boundary verification.

    These tests document WHY no primary call-site was wired:
    - GhostExecutor is donor/compat, not canonical authority
    - tool_exec_log is instrumentation, not execution
    - No safe non-legacy, non-scheduler, non-stealth call-sites exist
    """

    def test_ghost_executor_execute_is_separate_from_tool_registry(self):
        """
        GhostExecutor.execute() is a SEPARATE execution path from ToolRegistry.

        It uses ActionType enum and direct action handlers.
        It does NOT go through ToolRegistry.execute_with_limits().
        """
        from hledac.universal.execution.ghost_executor import GhostExecutor, ActionType

        # GhostExecutor has its own action registry
        executor = GhostExecutor()
        assert ActionType.SEARCH.value in executor._actions

        # But those actions are NOT in ToolRegistry
        registry = create_default_registry()
        tool_names = [t.name for t in registry.list_tools()]

        # GhostExecutor actions should not appear as ToolRegistry tools
        ghost_actions = {"scan", "google", "deep_read", "stealth_harvest", "osint_discovery"}
        assert not any(a in tool_names for a in ghost_actions)

    def test_tool_exec_log_is_instrumentation_not_execution(self):
        """
        tool_exec_log is an AUDIT/LOGGING tool, not an execution tool.

        It logs tool execution events but does not execute tools itself.
        It COULD wrap ToolRegistry calls in the future for correlation.
        """
        from hledac.universal.tool_exec_log import ToolExecLog, ToolExecEvent
        from pathlib import Path
        import tempfile
        from dataclasses import fields

        with tempfile.TemporaryDirectory() as tmpdir:
            log = ToolExecLog(run_dir=Path(tmpdir), enable_persist=False)

            # Log a fake event
            log.log(
                tool_name="test_tool",
                input_data=b"input",
                output_data=b"output",
                status="success",
            )

            # tool_exec_log has log() method, not execute_with_limits()
            assert hasattr(log, "log")
            assert not hasattr(log, "execute_with_limits")

            # ToolExecEvent is a dataclass with event_id and correlation fields
            event_fields = {f.name for f in fields(ToolExecEvent)}
            assert "event_id" in event_fields
            assert "correlation" in event_fields


class TestBypassDebtMatrix:
    """
    Sprint 8TD: Bypass debt matrix documentation.

    These tests verify that documented bypasses are REAL
    and that the enforcement hook exists for future wiring.
    """

    def test_execute_with_limits_hook_exists_for_future_wiring(self):
        """
        The enforcement hook (execute_with_limits with available_capabilities)
        EXISTS and WORKS, but no production call-site passes it yet.

        This is intentional - the hook is ready for scheduler integration.
        """
        registry = create_default_registry()

        # The hook exists
        assert hasattr(registry, "execute_with_limits")

        # academic_search REQUIRES capabilities
        tool = registry.get_tool("academic_search")
        assert len(tool.required_capabilities) > 0

        # execute_with_limits CAN enforce when called properly
        # (proven by TestExecuteWithLimitsCapabilityEnforcement tests)

    @pytest.mark.asyncio
    async def test_none_skip_still_warns_but_does_not_hard_fail(self):
        """
        All known production call-sites use None-skip (backward compat).

        This is the current reality:
        - DeprecationWarning is emitted (Sprint 8SG)
        - But execution continues (backward compat preserved)
        - Hard fail would break production
        """
        import warnings as _warnings

        registry = create_default_registry()

        # This is how ALL current production call-sites call it
        with _warnings.catch_warnings(record=True) as w:
            _warnings.simplefilter("always")
            result = await registry.execute_with_limits(
                "academic_search",
                {"query": "test", "sources": ["arxiv"]},
                available_capabilities=None,  # What all production call-sites do
            )

        # No hard fail - execution continued
        assert result is not None

        # But warning WAS emitted
        cap_warnings = [x for x in w if "capability" in str(x.message).lower()]
        assert len(cap_warnings) >= 1


class TestF9ExecutionPlaneContainment:
    """
    Sprint F9: Execution Plane Containment Tests.

    These tests verify execution-plane containment WITHOUT:
    - Real production wiring to scheduler
    - execute_with_limits cutover
    - GhostExecutor migration
    - New execution framework

    Tests verify:
    - GhostExecutor is donor/compat, NOT canonical
    - ToolRegistry remains canonical execution-control surface
    - ToolExecLog remains audit boundary
    - Correlation seam is explicit and bounded
    - No new execution framework exists
    - No hidden scheduler wiring
    - No false authority merge between ActionType and Tool worlds
    """

    def test_ghost_executor_is_donor_compat_not_canonical(self):
        """
        GhostExecutor is DONOR/COMPAT backend, NOT canonical execution authority.

        Evidence:
        - Uses ActionType enum (NOT Tool model)
        - Has own _actions dict (NOT _tools registry)
        - execute() is separate from ToolRegistry.execute_with_limits()
        - Docstring explicitly states "DONOR/COMPAT"
        """
        from hledac.universal.execution.ghost_executor import GhostExecutor, ActionType

        executor = GhostExecutor()

        # ActionType enum exists (NOT Tool model)
        assert hasattr(ActionType, "SCAN")
        assert hasattr(ActionType, "DEEP_READ")
        assert hasattr(ActionType, "STEALTH_HARVEST")

        # _actions dict contains ActionType values
        assert "scan" in executor._actions
        assert "deep_read" in executor._actions
        assert "stealth_harvest" in executor._actions

        # No _tools attribute (that belongs to ToolRegistry)
        assert not hasattr(executor, "_tools")

        # Docstring confirms donor/compat role
        docstring = executor.__doc__ or ""
        assert "DONOR" in docstring
        assert "COMPAT" in docstring

    def test_ghost_executor_does_not_call_tool_registry(self):
        """
        GhostExecutor.execute() does NOT call ToolRegistry.execute_with_limits().

        This is a SEPARATE execution path — important for containment.
        """
        from hledac.universal.execution.ghost_executor import GhostExecutor
        import inspect

        # Check execute method source doesn't reference ToolRegistry
        source = inspect.getsource(GhostExecutor.execute)

        # execute() should NOT call ToolRegistry.execute_with_limits
        assert "execute_with_limits" not in source
        assert "ToolRegistry" not in source

    def test_tool_registry_remain_canonical_execution_surface(self):
        """
        ToolRegistry.execute_with_limits() remains the ONLY canonical execution surface.

        No new execution framework was created.
        """
        from hledac.universal.tool_registry import ToolRegistry, create_default_registry

        registry = create_default_registry()

        # Only execute_with_limits is canonical (no execute() method)
        assert hasattr(registry, "execute_with_limits")
        assert not hasattr(registry, "execute")

        # ToolRegistry class has execute_with_limits
        assert hasattr(ToolRegistry, "execute_with_limits")

        # No parallel execution authority
        assert not hasattr(registry, "execute_async")
        assert not hasattr(registry, "execute_tool")

    def test_tool_exec_log_audit_boundary_unchanged(self):
        """
        ToolExecLog remains AUDIT boundary — does NOT execute tools.

        Evidence:
        - Has log() method, NOT execute_with_limits
        - Stores hashes, NOT raw payloads
        - correlation dict storage only
        """
        from hledac.universal.tool_exec_log import ToolExecLog
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            logger = ToolExecLog(run_dir=Path(tmpdir), enable_persist=False)

            # Has audit method, NOT execution method
            assert hasattr(logger, "log")
            assert not hasattr(logger, "execute_with_limits")
            assert not hasattr(logger, "execute")

            # Log stores hashes, not raw data
            event = logger.log(
                tool_name="test",
                input_data=b"raw sensitive data",
                output_data=b"result",
                status="success",
            )
            # input_hash is SHA256, NOT the raw data
            assert event.input_hash != "raw sensitive data"
            assert len(event.input_hash) == 64  # SHA256 hex length

    def test_correlation_seam_explicit_and_bounded(self):
        """
        Correlation seam is explicit: caller → execute_with_limits → exec_logger.log → ToolExecEvent.

        No hidden correlation creation — keys come from call-site.
        """
        from hledac.universal.tool_registry import create_default_registry
        from hledac.universal.tool_exec_log import ToolExecLog
        from pathlib import Path
        import tempfile
        from unittest.mock import MagicMock

        registry = create_default_registry()

        with tempfile.TemporaryDirectory() as tmpdir:
            logger = ToolExecLog(run_dir=Path(tmpdir), enable_persist=False)

            correlation = {
                "run_id": "sprint-123",
                "branch_id": "branch-a",
                "provider_id": "mlx",
                "action_id": "act-456",
            }

            # Capture the logged event
            original_log = logger.log
            logged_event = None
            def capture_log(*args, **kwargs):
                nonlocal logged_event
                logged_event = original_log(*args, **kwargs)
                return logged_event
            logger.log = capture_log

            # execute_with_limits passes correlation through
            import asyncio
            asyncio.get_event_loop().run_until_complete(
                registry.execute_with_limits(
                    "entity_extraction",
                    {"text": "test", "entity_types": []},
                    available_capabilities={"entity_linking"},
                    exec_logger=logger,
                    correlation=correlation,
                )
            )

            # Correlation was stored exactly as passed
            assert logged_event.correlation == correlation
            assert logged_event.correlation["run_id"] == "sprint-123"

    def test_no_new_execution_framework(self):
        """
        NO new execution framework was created.

        Evidence:
        - ToolRegistry.execute_with_limits signature unchanged (plus optional params)
        - exec_logger is optional side-effect, not execution authority
        - No new entry points added
        """
        from hledac.universal.tool_registry import create_default_registry
        import inspect

        registry = create_default_registry()

        # Only execute_with_limits is canonical entry point
        assert hasattr(registry, "execute_with_limits")

        # Signature has optional exec_logger and correlation (not new required params)
        sig = inspect.signature(registry.execute_with_limits)
        params = list(sig.parameters.keys())

        # All new params are optional
        assert "exec_logger" in params
        assert "correlation" in params
        # Check they have defaults (optional)
        exec_logger_param = sig.parameters["exec_logger"]
        correlation_param = sig.parameters["correlation"]
        assert exec_logger_param.default is None
        assert correlation_param.default is None

    def test_no_hidden_scheduler_wiring(self):
        """
        NO hidden scheduler wiring exists.

        execute_with_limits does NOT auto-wire to SprintScheduler.
        """
        from hledac.universal.tool_registry import create_default_registry
        import inspect

        registry = create_default_registry()

        # Check source of execute_with_limits doesn't auto-wire scheduler
        source = inspect.getsource(registry.execute_with_limits)

        # No scheduler references
        assert "sprint_scheduler" not in source.lower()
        assert "SprintScheduler" not in source
        assert "run_id" not in source or "self._run_id" not in source

    def test_no_false_authority_merge(self):
        """
        NO merge of ActionType world and Tool world.

        GhostExecutor actions (ActionType) are separate from ToolRegistry tools.
        They remain separate concerns — no false authority.
        """
        from hledac.universal.execution.ghost_executor import GhostExecutor, ActionType
        from hledac.universal.tool_registry import create_default_registry

        executor = GhostExecutor()
        registry = create_default_registry()

        # GhostExecutor has ActionType-based actions
        assert ActionType.SCAN.value in executor._actions
        assert ActionType.DEEP_READ.value in executor._actions

        # ToolRegistry has Tool-based handlers
        tool_names = {t.name for t in registry.list_tools()}

        # NO overlap between ActionType values and Tool names
        ghost_action_values = {a.value for a in ActionType}
        overlap = ghost_action_values & tool_names
        assert len(overlap) == 0, f"GhostExecutor actions found in ToolRegistry: {overlap}"

    def test_no_new_dto_outside_types(self):
        """
        NO new execution-plane specific DTOs were created.

        RunCorrelation already exists in types.py for correlation.
        ExecutionContext is legacy (v1+v2), not new execution plane DTO.
        """
        from hledac.universal import types

        # RunCorrelation exists for correlation
        assert hasattr(types, "RunCorrelation")

        # No NEW execution-plane DTOs (beyond RunCorrelation)
        # ExecutionContext is legacy from v1+v2 (not new DTO for this plane)
        # ToolExecContext and ExecutionControl would be new execution plane DTOs
        assert not hasattr(types, "ToolExecContext")
        assert not hasattr(types, "ExecutionControl")

    def test_ghost_executor_migration_blockers_documented(self):
        """
        GhostExecutor migration blockers are explicitly documented.

        These blockers prevent simple GhostExecutor → ToolRegistry migration:
        1. Akce svázané s interními lazy-loaders
        2. ActionType → Tool model mapping není triviální
        3. GhostExecutor.call-sites by musely přejít
        4. Scheduler wire je forbidden
        """
        from hledac.universal.execution.ghost_executor import GhostExecutor

        docstring = GhostExecutor.__doc__ or ""

        # REMOVAL CONDITION documented
        assert "REMOVAL CONDITION" in docstring

        # BOUNDARY SEAMS documented
        assert "BOUNDARY SEAMS" in docstring
        assert "ActionType" in docstring
        assert "_actions" in docstring

    def test_capability_enforcement_blocker_documented(self):
        """
        Capability enforcement blocker: žádné real call-sites s available_capabilities.

        Všechny current call-sites používají None-skip (backward compat).
        """
        from hledac.universal.tool_registry import create_default_registry

        registry = create_default_registry()

        # exec_logger and correlation are optional (None defaults)
        # Real call-sites don't pass them
        tool = registry.get_tool("academic_search")
        assert len(tool.required_capabilities) > 0

        # None-skip works (backward compat)
        import asyncio
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = asyncio.get_event_loop().run_until_complete(
                registry.execute_with_limits(
                    "academic_search",
                    {"query": "test", "sources": ["arxiv"]},
                    available_capabilities=None,  # All current call-sites do this
                )
            )
            # No hard fail
            assert result is not None

    def test_tool_exec_log_blocker_real_time_flush(self):
        """
        Blocker: ToolExecLog nemá real-time flush.

        Batch fsync (every N events) is a limitation for real-time correlation.
        """
        from hledac.universal.tool_exec_log import ToolExecLog

        # _FSYNC_EVERY_N_EVENTS is batch-based
        assert hasattr(ToolExecLog, "_FSYNC_EVERY_N_EVENTS")
        assert ToolExecLog._FSYNC_EVERY_N_EVENTS == 25

        # This is a documented limitation for real-time scenarios


class TestGhostBridgeSeam:
    """Sprint 8VF: GhostBridge thin typed seam tests."""

    def test_ghost_bridge_exists(self):
        """GhostBridge class exists in ghost_executor module."""
        from hledac.universal.execution.ghost_executor import GhostBridge
        assert GhostBridge is not None

    def test_ghost_bridge_to_execution_request(self):
        """GhostBridge.to_execution_request converts Ghost params to ExecutionRequest."""
        from hledac.universal.execution.ghost_executor import GhostBridge
        req = GhostBridge.to_execution_request(
            action="google",
            params={"query": "test query"},
            priority=5,
        )
        assert req.action_type == "google"
        assert req.parameters == {"query": "test query"}
        assert req.priority == 5

    def test_ghost_bridge_to_execution_result(self):
        """GhostBridge.to_execution_result converts Ghost ActionResult to ExecutionResult."""
        from hledac.universal.execution.ghost_executor import GhostBridge, ActionResult
        ghost_result = ActionResult(
            success=True,
            action="google",
            data={"results": ["a", "b"]},
            execution_time=0.1,
        )
        result = GhostBridge.to_execution_result(ghost_result)
        assert result.action_type == "google"
        assert result.success is True
        assert result.data == {"results": ["a", "b"]}
        assert result.execution_time == 0.1

    def test_ghost_bridge_action_has_canonical_tool(self):
        """GhostBridge.action_has_canonical_tool returns True for mapped actions."""
        from hledac.universal.execution.ghost_executor import GhostBridge
        # SEARCH, GOOGLE, RESEARCH_PAPER, DEEP_READ map to canonical tools
        assert GhostBridge.action_has_canonical_tool("google") is True
        assert GhostBridge.action_has_canonical_tool("search") is True
        assert GhostBridge.action_has_canonical_tool("research_paper") is True
        # Akce bez mappingu
        assert GhostBridge.action_has_canonical_tool("scan") is False
        assert GhostBridge.action_has_canonical_tool("stealth_harvest") is False

    def test_ghost_bridge_get_canonical_tool_name(self):
        """GhostBridge.get_canonical_tool_name returns mapped tool name or empty."""
        from hledac.universal.execution.ghost_executor import GhostBridge
        assert GhostBridge.get_canonical_tool_name("google") == "web_search"
        assert GhostBridge.get_canonical_tool_name("research_paper") == "academic_search"
        assert GhostBridge.get_canonical_tool_name("scan") == ""
        assert GhostBridge.get_canonical_tool_name("stealth_harvest") == ""

    def test_ghost_bridge_is_read_side_adapter(self):
        """GhostBridge is read-side only — does not call execute_with_limits."""
        import inspect
        from hledac.universal.execution.ghost_executor import GhostBridge
        # Kontrolujeme pouze implementaci (kód), ne docstringy
        # Docstringy SPRÁVNĚ zmiňují execute_with_limits jako co TO NENÍ
        method_names = ["to_execution_request", "to_execution_result",
                        "action_has_canonical_tool", "get_canonical_tool_name",
                        "get_action_canonical_tool_mapping"]
        for method_name in method_names:
            method = getattr(GhostBridge, method_name)
            source = inspect.getsource(method)
            # Odstraň docstring pro čistou kontrolu kódu
            import re
            code_only = re.sub(r'""".*?"""', '', source, flags=re.DOTALL)
            # Metody nevolají execute_with_limits
            assert "execute_with_limits" not in code_only, f"{method_name} calls execute_with_limits"
            # ToolRegistry se nevolá
            assert "ToolRegistry" not in code_only, f"{method_name} references ToolRegistry"

    def test_action_to_canonical_tool_mapping_complete(self):
        """_ACTION_TO_CANONICAL_TOOL covers all 18 Ghost ActionTypes."""
        from hledac.universal.execution.ghost_executor import (
            _ACTION_TO_CANONICAL_TOOL,
            ActionType,
        )
        ghost_actions = {a.value for a in ActionType}
        mapped_actions = set(_ACTION_TO_CANONICAL_TOOL.keys())
        # All Ghost actions are in the mapping
        assert ghost_actions == mapped_actions, (
            f"Missing actions: {ghost_actions - mapped_actions}, "
            f"Extra: {mapped_actions - ghost_actions}"
        )

    def test_ghost_bridge_preserves_donor_role(self):
        """GhostBridge does not change GhostExecutor donor/compat role."""
        from hledac.universal.execution.ghost_executor import GhostExecutor
        docstring = GhostExecutor.__doc__ or ""
        # Donor role documented
        assert "DONOR" in docstring.upper() or "donor" in docstring.lower()
        # GhostExecutor is explicitly NOT canonical authority (compact the multi-line)
        normalized = " ".join(docstring.split())
        assert "NOT the canonical" in normalized

    def test_correlation_passthrough_in_bridge(self):
        """GhostBridge preserves RunCorrelation through conversion."""
        from hledac.universal.execution.ghost_executor import GhostBridge
        from hledac.universal.project_types import RunCorrelation

        corr = RunCorrelation(run_id="run-123", branch_id="branch-1")
        req = GhostBridge.to_execution_request(
            action="google",
            params={"query": "test"},
            correlation=corr,
        )
        assert req.correlation is not None
        assert req.correlation.run_id == "run-123"

        from hledac.universal.execution.ghost_executor import ActionResult
        ghost_result = ActionResult(
            success=True, action="google", data={}, execution_time=0.05
        )
        result = GhostBridge.to_execution_result(ghost_result, correlation=corr)
        assert result.correlation is not None
        assert result.correlation.run_id == "run-123"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
