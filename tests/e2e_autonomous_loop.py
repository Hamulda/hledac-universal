"""
End-to-end testy pro autonomní loop - bez ReAct závislostí.
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from typing import Dict, Any, List
import asyncio
import tempfile

from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator, DiscoveryDepth
from hledac.universal.evidence_log import EvidenceLog
from hledac.universal.budget_manager import BudgetManager, BudgetConfig
from hledac.universal.project_types import ResearchMode


class TestNoReActReferences:
    """Verify no ReAct references in the code."""

    def test_no_react_imports_in_orchestrator(self):
        """Verify orchestrator doesn't import from react module."""
        import hledac.universal.autonomous_orchestrator as mod
        source = mod.__file__

        with open(source, 'r') as f:
            content = f.read()

        # Should not have ReAct imports
        assert 'from .react' not in content
        assert 'from hledac.universal.react' not in content
        assert 'ReActOrchestrator' not in content

    def test_no_react_in_evidence_log(self):
        """Verify evidence_log doesn't import from react module."""
        import hledac.universal.evidence_log as mod
        source = mod.__file__

        with open(source, 'r') as f:
            content = f.read()

        assert 'from .react' not in content
        assert 'from hledac.universal.react' not in content

    def test_no_react_in_budget_manager(self):
        """Verify budget_manager doesn't import from react module."""
        import hledac.universal.budget_manager as mod
        source = mod.__file__

        with open(source, 'r') as f:
            content = f.read()

        assert 'from .react' not in content
        assert 'from hledac.universal.react' not in content


class TestOrchestratorInstantiation:
    """Test orchestrator can be instantiated without ReAct."""

    def test_orchestrator_can_be_created(self):
        """Test orchestrator creation works."""
        with patch('hledac.universal.autonomous_orchestrator.MLX_AVAILABLE', False):
            orch = FullyAutonomousOrchestrator()

        assert orch is not None

    def test_orchestrator_has_required_attributes(self):
        """Test orchestrator has required attributes after creation."""
        with patch('hledac.universal.autonomous_orchestrator.MLX_AVAILABLE', False):
            orch = FullyAutonomousOrchestrator()

        # Verify key attributes exist
        assert hasattr(orch, 'config')
        assert hasattr(orch, '_execution_history')


class TestEvidenceLogIntegration:
    """Test evidence log integration."""

    def test_evidence_log_creation(self):
        """Test evidence log can be created."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log = EvidenceLog(run_id="test_run", persist_path=tmpdir, enable_persist=False)
            assert log is not None
            assert log.run_id == "test_run"

    def test_create_evidence_packet_event(self):
        """Test creating evidence packet event."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log = EvidenceLog(run_id="test_run", persist_path=tmpdir, enable_persist=False)

            event = log.create_evidence_packet_event(
                evidence_id="ev_001",
                packet_path="/tmp/packet.json",
                summary={"url": "https://example.com", "status": 200},
                confidence=0.9
            )

            assert event is not None
            assert event.event_type == "evidence_packet"
            assert event.payload["evidence_id"] == "ev_001"

    def test_create_decision_event(self):
        """Test creating decision event."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log = EvidenceLog(run_id="test_run", persist_path=tmpdir, enable_persist=False)

            event = log.create_decision_event(
                kind="bandit",
                summary={"action": "explore", "confidence": 0.8},
                reasons=["high uncertainty"],
                refs={"evidence_ids": ["ev_001"]},
                confidence=0.8
            )

            assert event is not None
            assert event.event_type == "decision"
            assert event.payload["kind"] == "bandit"


class TestBudgetManagerIntegration:
    """Test budget manager integration."""

    def test_budget_manager_creation(self):
        """Test budget manager can be created."""
        config = BudgetConfig(max_iterations=10, max_time_sec=60)
        budget = BudgetManager(config=config)
        assert budget is not None

    def test_budget_config_accepts_params(self):
        """Test BudgetConfig accepts iteration and time params."""
        config = BudgetConfig(max_iterations=5, max_time_sec=120)
        assert config.max_iterations == 5
        assert config.max_time_sec == 120


class TestResearchModeConfig:
    """Test research mode configuration."""

    def test_autonomous_mode_config(self):
        """Test autonomous mode config can be created."""
        from hledac.universal.config import UniversalConfig

        config = UniversalConfig.for_mode(ResearchMode.AUTONOMOUS)
        assert config is not None

    def test_config_has_research_settings(self):
        """Test config has research settings."""
        from hledac.universal.config import UniversalConfig

        config = UniversalConfig.for_mode(ResearchMode.AUTONOMOUS)
        assert hasattr(config, 'research')
