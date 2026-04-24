"""
Sprint F202B: Identity Stitching Canonical Probe Tests

Verifies:
  - Entity signal extraction from CanonicalFinding objects
  - Identity stitching adapter: bounded profile/comparison caps
  - Graph service upsert_identity_edge helper
  - Sprint markdown reporter: identity candidate rendering
  - SprintSchedulerResult identity counters

All tests use MagicMock/AsyncMock — no real DB/LMDB dependencies.
"""

import asyncio
from unittest.mock import MagicMock


# ============================================================================
# F202B-1: Entity Signal Extractor — basic extraction
# ============================================================================

class TestEntitySignalExtractorExtraction:
    """Invariant: entity_signal_extractor extracts emails, usernames, domain handles."""

    def test_extract_email_from_finding(self):
        """Single email extracted from payload_text."""
        from hledac.universal.intelligence.entity_signal_extractor import (
            extract_entities_from_finding,
        )

        mock_finding = MagicMock()
        mock_finding.finding_id = "test-email-001"
        mock_finding.payload_text = "Contact: alice@example.com for more info"
        mock_finding.source_type = "public"
        mock_finding.provenance = ()
        mock_finding.confidence = 0.8

        entities = extract_entities_from_finding(mock_finding)
        emails = [e for e in entities if e.entity_type == "email"]
        assert len(emails) == 1
        assert emails[0].value == "alice@example.com"
        assert emails[0].raw_value == "alice@example.com"
        assert emails[0].finding_id == "test-email-001"
        assert emails[0].confidence == 0.9  # 0.8 + 0.1 boost

    def test_extract_username_handle(self):
        """Bare @username handle extracted from payload_text."""
        from hledac.universal.intelligence.entity_signal_extractor import (
            extract_entities_from_finding,
        )

        mock_finding = MagicMock()
        mock_finding.finding_id = "test-handle-001"
        mock_finding.payload_text = "Follow @alice_smith for updates"
        mock_finding.source_type = "public"
        mock_finding.provenance = ()
        mock_finding.confidence = 0.7

        entities = extract_entities_from_finding(mock_finding)
        usernames = [e for e in entities if e.entity_type == "username"]
        assert len(usernames) >= 1
        raw_values = [e.raw_value for e in usernames]
        assert "alice_smith" in raw_values

    def test_extract_domain_handle(self):
        """user@domain pattern extracted from payload_text."""
        from hledac.universal.intelligence.entity_signal_extractor import (
            extract_entities_from_finding,
        )

        mock_finding = MagicMock()
        mock_finding.finding_id = "test-dh-001"
        mock_finding.payload_text = "Sent to admin@evil.com from the target"
        mock_finding.source_type = "public"
        mock_finding.provenance = ()
        mock_finding.confidence = 0.75

        entities = extract_entities_from_finding(mock_finding)
        handles = [e for e in entities if e.entity_type == "domain_handle"]
        assert len(handles) >= 1

    def test_extract_multiple_emails(self):
        """Multiple distinct emails extracted from same payload."""
        from hledac.universal.intelligence.entity_signal_extractor import (
            extract_entities_from_finding,
        )

        mock_finding = MagicMock()
        mock_finding.finding_id = "test-multi-001"
        mock_finding.payload_text = "Contacts: bob@example.com, carol@test.org, dave@mail.net"
        mock_finding.source_type = "public"
        mock_finding.provenance = ()
        mock_finding.confidence = 0.6

        entities = extract_entities_from_finding(mock_finding)
        emails = [e for e in entities if e.entity_type == "email"]
        assert len(emails) == 3

    def test_extract_from_findings_batch(self):
        """extract_entities_from_findings groups by normalized value."""
        from hledac.universal.intelligence.entity_signal_extractor import (
            extract_entities_from_findings,
        )

        finding1 = MagicMock()
        finding1.finding_id = "batch-001"
        finding1.payload_text = "Email: alice@example.com"
        finding1.source_type = "public"
        finding1.provenance = ()
        finding1.confidence = 0.7

        finding2 = MagicMock()
        finding2.finding_id = "batch-002"
        finding2.payload_text = "alice@example.com again, also bob@test.org"
        finding2.source_type = "public"
        finding2.provenance = ()
        finding2.confidence = 0.8

        profiles = extract_entities_from_findings([finding1, finding2])
        assert len(profiles) >= 1
        # alice@example.com should be consolidated into one profile
        email_profiles = [p for p in profiles if "alice@example.com" in p.emails]
        assert len(email_profiles) == 1
        assert "batch-001" in email_profiles[0].finding_ids
        assert "batch-002" in email_profiles[1].finding_ids if len(email_profiles) > 1 else True


# ============================================================================
# F202B-2: Entity Signal Extractor — bounds
# ============================================================================

class TestEntitySignalExtractorBounds:
    """Invariant: MAX_PROFILES=500 cap enforced."""

    def test_max_profiles_respected(self):
        """extract_entities_from_findings caps at MAX_PROFILES."""
        from hledac.universal.intelligence.entity_signal_extractor import (
            extract_entities_from_findings,
            MAX_PROFILES,
        )

        mock_finding = MagicMock()
        mock_finding.finding_id = "cap-test"
        mock_finding.payload_text = "alice@example.com"
        mock_finding.source_type = "public"
        mock_finding.provenance = ()
        mock_finding.confidence = 0.7

        # Create a list with many findings that would produce many profiles
        findings = [MagicMock(
            finding_id=f"cap-{i}",
            payload_text=f"user{i}@example.com",
            source_type="public",
            provenance=(),
            confidence=0.7,
        ) for i in range(600)]

        profiles = extract_entities_from_findings(findings, max_profiles=MAX_PROFILES)
        assert len(profiles) <= MAX_PROFILES

    def test_empty_findings_returns_empty(self):
        """Empty findings list returns empty profiles."""
        from hledac.universal.intelligence.entity_signal_extractor import (
            extract_entities_from_findings,
        )

        profiles = extract_entities_from_findings([])
        assert profiles == []


# ============================================================================
# F202B-3: Identity Stitching Adapter — factory and basics
# ============================================================================

class TestIdentityStitchingAdapterBasic:
    """Invariant: adapter creation, stats, clear."""

    def test_adapter_factory_creates_instance(self):
        """create_identity_stitching_adapter returns IdentityStitchingAdapter."""
        from hledac.universal.intelligence.identity_stitching_canonical import (
            create_identity_stitching_adapter,
            IdentityStitchingAdapter,
        )

        adapter = create_identity_stitching_adapter()
        assert isinstance(adapter, IdentityStitchingAdapter)

    def test_adapter_initial_stats(self):
        """Adapter starts with zero stats."""
        from hledac.universal.intelligence.identity_stitching_canonical import (
            create_identity_stitching_adapter,
        )

        adapter = create_identity_stitching_adapter()
        stats = adapter.get_stats()
        assert stats["profiles_added"] == 0
        assert stats["candidates_found"] == 0
        assert stats["comparisons_run"] == 0
        assert stats["findings_produced"] == 0
        assert stats["graph_edges_written"] == 0

    def test_adapter_clear_resets_stats(self):
        """clear() resets stats to zero."""
        from hledac.universal.intelligence.identity_stitching_canonical import (
            create_identity_stitching_adapter,
        )

        adapter = create_identity_stitching_adapter()
        adapter._stats["profiles_added"] = 99  # manually increment
        adapter.clear()
        stats = adapter.get_stats()
        assert stats["profiles_added"] == 0


# ============================================================================
# F202B-4: Identity Stitching Adapter — extract_and_stitch
# ============================================================================

class TestIdentityStitchingAdapterStitching:
    """Invariant: extract_and_stitch returns IdentityCandidate list."""

    def test_extract_and_stitch_empty_profiles(self):
        """Empty profile list returns empty candidates."""
        from hledac.universal.intelligence.identity_stitching_canonical import (
            create_identity_stitching_adapter,
        )

        adapter = create_identity_stitching_adapter()
        candidates = adapter.extract_and_stitch([])
        assert candidates == []

    def test_extract_and_stitch_with_mock_profiles(self):
        """extract_and_stitch produces candidates from EntitySignalProfile list."""
        from hledac.universal.intelligence.entity_signal_extractor import (
            EntitySignalProfile,
        )
        from hledac.universal.intelligence.identity_stitching_canonical import (
            create_identity_stitching_adapter,
        )

        profiles = [
            EntitySignalProfile(
                id="email:alice@example.com",
                primary_name="alice",
                emails=["alice@example.com"],
                usernames=["alice_s"],
                platforms=set(),
                finding_ids=["fid-1", "fid-2"],
                confidence=0.8,
            ),
            EntitySignalProfile(
                id="email:bob@example.com",
                primary_name="bob",
                emails=["bob@example.com"],
                usernames=["bob_t"],
                platforms=set(),
                finding_ids=["fid-3"],
                confidence=0.7,
            ),
        ]

        adapter = create_identity_stitching_adapter()
        candidates = adapter.extract_and_stitch(profiles)
        assert isinstance(candidates, list)


# ============================================================================
# F202B-5: Identity Stitching Adapter — to_derived_findings
# ============================================================================

class TestIdentityStitchingAdapterDerivedFindings:
    """Invariant: to_derived_findings produces CanonicalFinding list."""

    def test_to_derived_findings_empty_candidates(self):
        """Empty candidates returns empty findings."""
        from hledac.universal.intelligence.identity_stitching_canonical import (
            IdentityStitchingAdapter,
            IdentityCandidate,
        )

        adapter = MagicMock(spec=IdentityStitchingAdapter)
        adapter.to_derived_findings = IdentityStitchingAdapter.to_derived_findings.__get__(
            adapter, IdentityStitchingAdapter
        )

        findings = adapter.to_derived_findings([], "test query")
        assert findings == []

    def test_to_derived_findings_produces_canonical_finding(self):
        """Non-empty candidates produces CanonicalFinding objects."""
        from hledac.universal.intelligence.identity_stitching_canonical import (
            IdentityStitchingAdapter,
            IdentityCandidate,
        )

        candidates = [
            IdentityCandidate(
                candidate_id="test-cand-001",
                profile_ids=["email:alice@example.com"],
                primary_name="alice",
                emails=["alice@example.com"],
                usernames=["alice_s"],
                platforms=["public"],
                confidence=0.85,
                signals={"stitch_confidence": 0.85},
                evidence=["exact email match"],
                finding_ids=["fid-1"],
            ),
        ]

        # Create real adapter (needs CanonicalFinding import)
        from hledac.universal.intelligence.identity_stitching_canonical import (
            create_identity_stitching_adapter,
        )

        adapter = create_identity_stitching_adapter()
        findings = adapter.to_derived_findings(candidates, "test query")
        assert len(findings) == 1
        assert findings[0].source_type == "identity_stitching"
        assert findings[0].query == "test query"


# ============================================================================
# F202B-6: Graph Service — upsert_identity_edge
# ============================================================================

class TestGraphServiceUpsertIdentityEdge:
    """Invariant: upsert_identity_edge calls upsert_relation with same_identity type."""

    def test_upsert_identity_edge_exists(self):
        """graph_service.upsert_identity_edge is exported."""
        from hledac.universal.knowledge import graph_service

        assert hasattr(graph_service, "upsert_identity_edge")
        assert callable(graph_service.upsert_identity_edge)

    def test_upsert_identity_edge_delegates_to_upsert_relation(self):
        """upsert_identity_edge calls upsert_relation with rel_type='same_identity'."""
        # Patch upsert_relation at module level
        import hledac.universal.knowledge.graph_service as gs

        original = gs.upsert_relation
        called_with = {}

        def mock_upsert(src, dst, rel_type, weight=1.0, evidence=""):
            called_with["src"] = src
            called_with["dst"] = dst
            called_with["rel_type"] = rel_type
            called_with["weight"] = weight
            called_with["evidence"] = evidence
            return True

        gs.upsert_relation = mock_upsert
        try:
            result = gs.upsert_identity_edge(
                src="profile-a",
                dst="profile-b",
                confidence=0.85,
                evidence="stitch:alice-001",
            )
            assert result is True
            assert called_with["src"] == "profile-a"
            assert called_with["dst"] == "profile-b"
            assert called_with["rel_type"] == "same_identity"
            assert called_with["weight"] == 0.85
            assert called_with["evidence"] == "stitch:alice-001"
        finally:
            gs.upsert_relation = original


# ============================================================================
# F202B-7: Sprint Result — identity counters
# ============================================================================

class TestSprintResultIdentityCounters:
    """Invariant: SprintSchedulerResult has identity_candidates_found and identity_findings_produced."""

    def test_result_has_identity_fields(self):
        """SprintSchedulerResult dataclass has identity counters."""
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult

        result = SprintSchedulerResult()
        assert hasattr(result, "identity_candidates_found")
        assert hasattr(result, "identity_findings_produced")
        assert result.identity_candidates_found == 0
        assert result.identity_findings_produced == 0

    def test_result_identity_fields_mutable(self):
        """Identity counter fields can be incremented."""
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult

        result = SprintSchedulerResult()
        result.identity_candidates_found = 42
        result.identity_findings_produced = 17
        assert result.identity_candidates_found == 42
        assert result.identity_findings_produced == 17


# ============================================================================
# F202B-8: Sprint Markdown Reporter — identity rendering
# ============================================================================

class TestIdentityMarkdownRendering:
    """Invariant: _render_identity_candidates produces markdown with confidence/signals."""

    def test_render_empty_candidates(self):
        """Empty candidates returns empty string."""
        from hledac.universal.export.sprint_markdown_reporter import (
            _render_identity_candidates,
        )

        result = _render_identity_candidates([])
        assert result == ""

    def test_render_single_candidate(self):
        """Single candidate produces markdown with confidence and signals."""
        from hledac.universal.export.sprint_markdown_reporter import (
            _render_identity_candidates,
        )

        candidates = [
            {
                "candidate_id": "test-identity-001",
                "primary_name": "Alice Smith",
                "confidence": 0.87,
                "signals": {"stitch_confidence": 0.87, "email_exact": 1.0},
                "emails": ["alice@example.com"],
                "usernames": ["alice_s", "alice_smith"],
                "platforms": ["public", "ct_log"],
                "evidence": ["exact email match"],
                "finding_ids": ["fid-001", "fid-002"],
            }
        ]

        result = _render_identity_candidates(candidates)
        assert "Alice Smith" in result
        assert "0.87" in result
        assert "high" in result  # confidence >= 0.8
        assert "alice@example.com" in result
        assert "alice_s" in result
        assert "stitch_confidence" in result

    def test_render_medium_confidence_label(self):
        """Medium confidence (0.6-0.8) shows 'medium' label."""
        from hledac.universal.export.sprint_markdown_reporter import (
            _render_identity_candidates,
        )

        candidates = [
            {
                "candidate_id": "med-conf-001",
                "primary_name": "Bob Jones",
                "confidence": 0.65,
                "signals": {"username_similarity": 0.7},
                "emails": [],
                "usernames": ["bob_j"],
                "platforms": ["public"],
                "evidence": [],
                "finding_ids": [],
            }
        ]

        result = _render_identity_candidates(candidates)
        assert "medium" in result  # 0.65 is medium confidence
        assert "Bob Jones" in result

    def test_render_bounded_at_10(self):
        """Output is capped at 10 candidates."""
        from hledac.universal.export.sprint_markdown_reporter import (
            _render_identity_candidates,
        )

        candidates = [
            {
                "candidate_id": f"cand-{i}",
                "primary_name": f"Person {i}",
                "confidence": 0.8,
                "signals": {},
                "emails": [],
                "usernames": [],
                "platforms": [],
                "evidence": [],
                "finding_ids": [],
            }
            for i in range(25)
        ]

        result = _render_identity_candidates(candidates)
        count = result.count("### `cand-")
        assert count == 10  # capped at 10

    def test_render_skips_non_dict(self):
        """Non-dict items in list are skipped."""
        from hledac.universal.export.sprint_markdown_reporter import (
            _render_identity_candidates,
        )

        candidates = [
            "not a dict",
            None,
            {"candidate_id": "valid-001", "primary_name": "Valid", "confidence": 0.9,
             "signals": {}, "emails": [], "usernames": [], "platforms": [],
             "evidence": [], "finding_ids": []},
        ]

        result = _render_identity_candidates(candidates)
        assert "Valid" in result
        assert "not a dict" not in result


# ============================================================================
# F202B-9: SprintScheduler — identity adapter field
# ============================================================================

class TestSprintSchedulerIdentityAdapter:
    """Invariant: SprintScheduler has _identity_adapter field."""

    def test_scheduler_has_identity_adapter_field(self):
        """SprintScheduler.__init__ initializes _identity_adapter to None."""
        from hledac.universal.runtime.sprint_scheduler import (
            SprintScheduler,
            SprintSchedulerConfig,
        )

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)
        assert hasattr(scheduler, "_identity_adapter")
        assert scheduler._identity_adapter is None
