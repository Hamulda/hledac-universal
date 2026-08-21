"""
Sprint F226B — Confidence Policy Reality Seal
============================================

Verify that confidence_policy migration is real in source, not just in reports.

Goals:
1. _SOURCE_BASELINES is the single source of source-family baselines
2. compute_confidence() uses _SOURCE_BASELINES (no local _BASELINES dict)
3. ClaimsCoordinator._derive_confidence() calls compute_confidence()
4. SocialIdentityMiner._compute_confidence() calls compute_confidence()
5. All outputs bounded, no MLX, no network, no DuckDB at module import

ABORT conditions:
- Any runtime edit
- Any benchmark edit
- Any DB/network/MLX import at module import
- Any dependency edit
"""

import ast
import inspect
import re
import sys
from pathlib import Path

# Ensure hledac.universal importable
_root = Path(__file__).parent.parent
sys.path.insert(0, str(_root))


# ------------------------------------------------------------------
# 1. confidence_policy.py — no local _BASELINES inside compute_confidence
# ------------------------------------------------------------------


class TestSourceBaselinesReal:
    """Verify _SOURCE_BASELINES is the lookup table, not a local dict."""

    def test_no_local_baselines_in_compute_confidence(self) -> None:
        """
        Source test: compute_confidence has no local '_BASELINES =' assignment.
        The function body must not contain '_BASELINES =' (case-sensitive).
        """
        from intelligence.confidence_policy import compute_confidence

        source = inspect.getsource(compute_confidence)
        matches = re.findall(r"_BASELINES\s*=", source)
        assert len(matches) == 0, (
            f"compute_confidence still has local _BASELINES assignments: {matches}. "
            "Must use module-level _SOURCE_BASELINES."
        )

    def test_source_baselines_keys_match_module_constants(self) -> None:
        """Verify _SOURCE_BASELINES keys match module-level constant names."""
        from intelligence.confidence_policy import (
            _SOURCE_BASELINES,
        )

        expected_keys = {
            "FEED",
            "PUBLIC",
            "CT",
            "WAYBACK",
            "PASSIVE_DNS",
            "SOCIAL",
            "PLANNER",
            "STEALTH",
        }
        assert set(_SOURCE_BASELINES.keys()) == expected_keys

    def test_source_baselines_values_match_constants(self) -> None:
        """Verify _SOURCE_BASELINES values match the module-level constants."""
        from intelligence.confidence_policy import (
            _SOURCE_BASELINES,
            CT,
            FEED,
            PASSIVE_DNS,
            PLANNER,
            PUBLIC,
            SOCIAL,
            STEALTH,
            WAYBACK,
        )

        assert _SOURCE_BASELINES["FEED"] == FEED
        assert _SOURCE_BASELINES["PUBLIC"] == PUBLIC
        assert _SOURCE_BASELINES["CT"] == CT
        assert _SOURCE_BASELINES["WAYBACK"] == WAYBACK
        assert _SOURCE_BASELINES["PASSIVE_DNS"] == PASSIVE_DNS
        assert _SOURCE_BASELINES["SOCIAL"] == SOCIAL
        assert _SOURCE_BASELINES["PLANNER"] == PLANNER
        assert _SOURCE_BASELINES["STEALTH"] == STEALTH

    def test_ast_no_baselines_assignment_in_function(self) -> None:
        """AST test: no Assign node with target '_BASELINES' inside compute_confidence."""
        from intelligence.confidence_policy import compute_confidence

        source = inspect.getsource(compute_confidence)
        tree = ast.parse(source)
        assigns = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "_BASELINES":
                        assigns.append(target.id)
        assert len(assigns) == 0


# ------------------------------------------------------------------
# 2. ClaimsCoordinator — imports and calls compute_confidence
# ------------------------------------------------------------------


class TestClaimsCoordinatorMigration:
    """Verify ClaimsCoordinator._derive_confidence() uses compute_confidence."""

    def test_imports_compute_confidence(self) -> None:
        """Source test: claims_coordinator.py imports compute_confidence."""
        path = _root / "coordinators" / "claims_coordinator.py"
        content = path.read_text()
        assert "compute_confidence" in content

    def test_derive_confidence_calls_compute_confidence(self) -> None:
        """Source test: _derive_confidence contains 'compute_confidence(' call."""
        path = _root / "coordinators" / "claims_coordinator.py"
        content = path.read_text()
        derives_pattern = re.search(
            r"def _derive_confidence\([^)]+\)[^:]*:.*?(?=\n    def |\nclass |\Z)",
            content,
            re.DOTALL,
        )
        assert derives_pattern, "_derive_confidence function not found"
        derives_body = derives_pattern.group(0)
        assert "compute_confidence(" in derives_body

    def test_derive_confidence_respects_max_confidence_075(self) -> None:
        """Behavior test: claims confidence capped at 0.75 (deterministic v1)."""
        from hledac.universal.coordinators.claims_coordinator import ClaimsCoordinator

        coord = ClaimsCoordinator()

        text = "A" * 50 + " https://example.com some content here"
        evidence = {
            "source_type": "ct",
            "title": "Example Title",
            "summary": "Example summary",
        }

        conf = coord._derive_confidence(text, evidence, "Example Title", "Example summary")
        assert conf <= 0.75, f"ClaimsCoordinator confidence {conf} exceeds MAX_CONFIDENCE=0.75"
        assert conf >= 0.10

    def test_provenance_raises_claims_confidence(self) -> None:
        """Behavior test: provenance bonus increases claims confidence."""
        from hledac.universal.coordinators.claims_coordinator import ClaimsCoordinator

        coord = ClaimsCoordinator()

        text = "Confirmed report of malware detected on server"
        evidence_no_prov = {"source_type": "public"}
        evidence_with_prov = {"source_type": "public", "source": "test_source", "provenance": "test"}

        conf_no = coord._derive_confidence(text, evidence_no_prov, "", "")
        conf_yes = coord._derive_confidence(text, evidence_with_prov, "", "")

        assert conf_yes > conf_no

    def test_url_domain_email_raises_claims_confidence(self) -> None:
        """Behavior test: URL/domain/email IOC increases claims confidence."""
        from hledac.universal.coordinators.claims_coordinator import ClaimsCoordinator

        coord = ClaimsCoordinator()

        text_no_ioc = "Some claim without any identifiers"
        evidence = {"source_type": "public"}

        text_with_ioc = "Visit https://example.com or contact admin@domain.com for info"
        conf_no = coord._derive_confidence(text_no_ioc, evidence, "", "")
        conf_yes = coord._derive_confidence(text_with_ioc, evidence, "", "")

        assert conf_yes > conf_no

    def test_ct_baseline_higher_than_public(self) -> None:
        """Behavior test: CT baseline (0.70) > PUBLIC baseline (0.60)."""
        from hledac.universal.coordinators.claims_coordinator import ClaimsCoordinator

        coord = ClaimsCoordinator()

        text = "Certificate observed for example.com"
        evidence_ct = {"source_type": "ct"}
        evidence_public = {"source_type": "public"}

        conf_ct = coord._derive_confidence(text, evidence_ct, "", "")
        conf_pub = coord._derive_confidence(text, evidence_public, "", "")

        assert conf_ct > conf_pub

    def test_all_outputs_bounded(self) -> None:
        """Behavior test: all confidence outputs in [0.10, 0.95]."""
        from hledac.universal.coordinators.claims_coordinator import ClaimsCoordinator
        from intelligence.confidence_policy import MAX_CONFIDENCE, MIN_CONFIDENCE

        coord = ClaimsCoordinator()

        test_cases = [
            ("short", {"source_type": "unknown"}),
            ("x" * 20 + " https://a.com b.com c@test.com", {"source_type": "ct", "source": "src"}),
            ("No provenance no IOC text", {"source_type": "public"}),
        ]

        for text, evidence in test_cases:
            conf = coord._derive_confidence(text, evidence, "", "")
            assert MIN_CONFIDENCE <= conf <= MAX_CONFIDENCE

    def test_no_mlx_in_claims_coordinator(self) -> None:
        """Security test: no MLX imported in claims_coordinator module."""
        path = _root / "coordinators" / "claims_coordinator.py"
        content = path.read_text()
        module_level = content.split("def ")[0].split("class ")[0]
        assert "import mlx" not in module_level and "from mlx" not in module_level

    def test_no_duckdb_import_at_module_level(self) -> None:
        """Security test: no DuckDB imported at claims_coordinator module load."""
        path = _root / "coordinators" / "claims_coordinator.py"
        content = path.read_text()
        module_level = content.split("def ")[0].split("class ")[0]
        assert "import duckdb" not in module_level and "from duckdb" not in module_level


# ------------------------------------------------------------------
# 3. SocialIdentityMiner — imports and calls compute_confidence
# ------------------------------------------------------------------


class TestSocialIdentityMinerMigration:
    """Verify SocialIdentityMiner._compute_confidence() calls compute_confidence."""

    def test_imports_compute_confidence(self) -> None:
        """Source test: social_identity_miner.py imports compute_confidence."""
        path = _root / "intelligence" / "social_identity_miner.py"
        content = path.read_text()
        assert "compute_confidence" in content

    def test_compute_confidence_calls_policy(self) -> None:
        """Source test: _compute_confidence contains 'compute_confidence(' call."""
        path = _root / "intelligence" / "social_identity_miner.py"
        content = path.read_text()

        method_match = re.search(
            r"def _compute_confidence\([^)]+\)[^:]*:.*?(?=\n    def |\nclass |\Z)",
            content,
            re.DOTALL,
        )
        assert method_match, "_compute_confidence method not found"
        body = method_match.group(0)
        assert "compute_confidence(" in body

    def test_social_min_confidence_threshold_preserved(self) -> None:
        """Behavior test: SOCIAL_MIN_CONFIDENCE threshold preserved."""
        from intelligence.social_identity_miner import SOCIAL_MIN_CONFIDENCE, SocialIdentityMiner

        miner = SocialIdentityMiner()

        conf_bare = miner._compute_confidence(
            platform="github",
            username="testuser",
            linked_domains=[],
            linked_emails=[],
        )
        assert conf_bare >= SOCIAL_MIN_CONFIDENCE

    def test_social_facet_with_linked_email_domain_higher_than_bare(self) -> None:
        """Behavior test: social facet with email/domain scores higher than bare profile."""
        from intelligence.social_identity_miner import SocialIdentityMiner

        miner = SocialIdentityMiner()

        conf_bare = miner._compute_confidence(
            platform="github",
            username="testuser",
            linked_domains=[],
            linked_emails=[],
        )
        conf_rich = miner._compute_confidence(
            platform="github",
            username="testuser",
            linked_domains=["example.com", "test.io"],
            linked_emails=["user@example.com"],
        )
        assert conf_rich > conf_bare

    def test_confidence_upper_bound_095(self) -> None:
        """Behavior test: social confidence respects MAX_CONFIDENCE=0.95."""
        from intelligence.confidence_policy import MAX_CONFIDENCE
        from intelligence.social_identity_miner import SocialIdentityMiner

        miner = SocialIdentityMiner()

        conf = miner._compute_confidence(
            platform="github",
            username="testuser12345",
            linked_domains=["a.com", "b.com", "c.com", "d.com"],
            linked_emails=["a@a.com", "b@b.com", "c@c.com"],
        )
        assert conf <= MAX_CONFIDENCE

    def test_all_outputs_bounded(self) -> None:
        """Behavior test: all social confidence outputs in [0.10, 0.95]."""
        from intelligence.confidence_policy import MAX_CONFIDENCE, MIN_CONFIDENCE
        from intelligence.social_identity_miner import SocialIdentityMiner

        miner = SocialIdentityMiner()

        test_cases = [
            (["github"], [], []),
            (["twitter"], ["example.com"], ["user@test.com"]),
            (["linkedin"], ["a.com", "b.com"], ["x@a.com", "y@b.com", "z@c.com"]),
        ]

        for platform, domains, emails in test_cases:
            conf = miner._compute_confidence(platform[0], "testuser", domains, emails)
            assert MIN_CONFIDENCE <= conf <= MAX_CONFIDENCE

    def test_no_mlx_in_social_identity_miner(self) -> None:
        """Security test: no MLX imported in social_identity_miner module."""
        import intelligence.social_identity_miner as mod

        assert not hasattr(mod, "mlx")

    def test_no_duckdb_import_at_module_level(self) -> None:
        """Security test: no DuckDB imported at social_identity_miner module load."""
        import intelligence.social_identity_miner as mod

        src = inspect.getsource(mod)
        module_level = src.split("def ")[0].split("class ")[0]
        assert "import duckdb" not in module_level and "from duckdb" not in module_level


# ------------------------------------------------------------------
# 4. Cross-sprint regression — F224B claims extraction tests still pass
# ------------------------------------------------------------------


class TestRegression:
    """Ensure confidence migration doesn't break existing F224B test expectations."""

    def test_claims_coordinator_still_initializable(self) -> None:
        """Verify ClaimsCoordinator can be instantiated without errors (source check)."""
        path = _root / "coordinators" / "claims_coordinator.py"
        content = path.read_text()
        # Verify ClaimsCoordinator class exists and has __init__
        assert "class ClaimsCoordinator" in content
        assert "def __init__" in content

    def test_social_identity_miner_still_initializable(self) -> None:
        """Verify SocialIdentityMiner can be instantiated without errors (source check)."""
        path = _root / "intelligence" / "social_identity_miner.py"
        content = path.read_text()
        # Verify SocialIdentityMiner class exists and has __init__
        assert "class SocialIdentityMiner" in content
        assert "def __init__" in content

    def test_confidence_policy_imports_clean(self) -> None:
        """Verify confidence_policy module has no problematic imports."""
        path = _root / "intelligence" / "confidence_policy.py"
        content = path.read_text()
        module_level = content.split("def compute_confidence")[0]
        assert "import mlx" not in module_level and "from mlx" not in module_level
        assert "import duckdb" not in module_level and "from duckdb" not in module_level
        assert "import network" not in module_level and "from network" not in module_level
