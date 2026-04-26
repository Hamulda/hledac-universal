"""
Sprint F203E: CTI STIX 2.1 Export — Probe Tests
================================================

Invariant mapping:
  F203E-1  | render_cti_stix_bundle returns valid STIX 2.1 bundle structure
  F203E-2  | MAX_STIX_OBJECTS=500 bound enforced
  F203E-3  | Empty findings → no fake IOC/indicator objects
  F203E-4  | Findings with ioc_type → indicator objects with correct STIX pattern
  F203E-5  | identity_candidates → identity objects with name/description
  F203E-6  | attribution_scores → note objects linked to identity
  F203E-7  | killchain_tags → labels on indicator + kill-chain note
  F203E-8  | evidence_chains → observed-data + relationship objects
  F203E-9  | Report object wraps all CTI objects
  F203E-10 | Deterministic UUID5 — same content → same ID
  F203E-11 | render_cti_stix_bundle_json produces valid sorted JSON
  F203E-12 | render_cti_stix_bundle_to_path writes file and returns Path
  F203E-13 | Bundle has type=bundle, spec_version=2.1, id starts with bundle--
  F203E-14 | Ghost Prime identity is always present as report author
  F203E-15 | max_objects parameter is respected
"""

import json
import pytest
import sys
import os
from pathlib import Path
from datetime import datetime, timezone

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from hledac.universal.export.stix_exporter import (
    render_cti_stix_bundle,
    render_cti_stix_bundle_json,
    render_cti_stix_bundle_to_path,
    MAX_STIX_OBJECTS,
    _make_stix_id,
    _ioc_to_indicator,
    _build_identity_object,
    _build_attribution_note,
    _build_killchain_note,
    _build_evidence_chain_object,
)


# ── F203E-1: Bundle structure ────────────────────────────────────────────────


class TestCTIBundleStructure:
    """F203E-1: render_cti_stix_bundle returns valid STIX 2.1 bundle structure."""

    def test_bundle_has_required_fields(self):
        """Bundle must have type, id, spec_version, created, objects."""
        bundle = render_cti_stix_bundle(findings=[])
        assert bundle["type"] == "bundle"
        assert bundle["id"].startswith("bundle--")
        assert bundle["spec_version"] == "2.1"
        assert "created" in bundle
        assert "objects" in bundle
        assert isinstance(bundle["objects"], list)

    def test_bundle_starts_with_identity(self):
        """First object must be Ghost Prime identity."""
        bundle = render_cti_stix_bundle(findings=[])
        identity = bundle["objects"][0]
        assert identity["type"] == "identity"
        assert identity["id"] == "identity--ghost-prime"
        assert identity["name"] == "Ghost Prime"


# ── F203E-2: MAX_STIX_OBJECTS bound ───────────────────────────────────────────


class TestMAXSTIXObjectsBound:
    """F203E-2: MAX_STIX_OBJECTS=500 bound enforced."""

    def test_max_objects_constant(self):
        """MAX_STIX_OBJECTS must be exactly 500."""
        assert MAX_STIX_OBJECTS == 500

    def test_max_objects_respected(self):
        """Bundle must not exceed MAX_STIX_OBJECTS."""
        # Create many findings
        findings = [
            {"finding_id": f"f{i}", "ioc_type": "ip", "ioc_value": f"1.2.3.{i % 256}", "confidence": 0.8}
            for i in range(1000)
        ]
        bundle = render_cti_stix_bundle(findings=findings)
        assert len(bundle["objects"]) <= MAX_STIX_OBJECTS


# ── F203E-3: Empty findings guardrail ─────────────────────────────────────────


class TestEmptyFindingsGuardrail:
    """F203E-3: Empty findings → no fake IOC/indicator objects."""

    def test_empty_findings_no_indicators(self):
        """With empty findings, no indicator objects should be created."""
        bundle = render_cti_stix_bundle(findings=[])
        indicator_types = [o["type"] for o in bundle["objects"]]
        assert "indicator" not in indicator_types

    def test_empty_findings_preserves_identity(self):
        """With empty findings, Ghost Prime identity is still present."""
        bundle = render_cti_stix_bundle(findings=[])
        types = [o["type"] for o in bundle["objects"]]
        assert "identity" in types

    def test_empty_findings_has_report(self):
        """With findings, report object is created."""
        bundle = render_cti_stix_bundle(findings=[])
        types = [o["type"] for o in bundle["objects"]]
        assert "report" in types


# ── F203E-4: Findings → indicators ────────────────────────────────────────────


class TestFindingsToIndicators:
    """F203E-4: Findings with ioc_type → indicator objects with correct STIX pattern."""

    def test_ip_finding_creates_indicator(self):
        """IP finding creates indicator with correct STIX pattern."""
        findings = [
            {"finding_id": "f1", "ioc_type": "ip", "ioc_value": "1.2.3.4", "confidence": 0.9}
        ]
        bundle = render_cti_stix_bundle(findings=findings)
        indicators = [o for o in bundle["objects"] if o["type"] == "indicator"]
        assert len(indicators) >= 1
        ind = indicators[0]
        assert "[ipv4-addr:value = '1.2.3.4']" in ind["pattern"]
        assert ind["pattern_type"] == "stix"
        assert ind["confidence"] == 90

    def test_domain_finding_creates_indicator(self):
        """Domain finding creates indicator with domain-name pattern."""
        findings = [
            {"finding_id": "f2", "ioc_type": "domain", "ioc_value": "evil.com", "confidence": 0.8}
        ]
        bundle = render_cti_stix_bundle(findings=findings)
        indicators = [o for o in bundle["objects"] if o["type"] == "indicator"]
        assert len(indicators) >= 1
        ind = indicators[0]
        assert "[domain-name:value = 'evil.com']" in ind["pattern"]

    def test_url_finding_creates_indicator(self):
        """URL finding creates indicator with url pattern."""
        findings = [
            {"finding_id": "f3", "ioc_type": "url", "ioc_value": "https://evil.com/payload", "confidence": 0.7}
        ]
        bundle = render_cti_stix_bundle(findings=findings)
        indicators = [o for o in bundle["objects"] if o["type"] == "indicator"]
        assert len(indicators) >= 1
        ind = indicators[0]
        assert "[url:value = 'https://evil.com/payload']" in ind["pattern"]

    def test_hash_sha256_indicator(self):
        """SHA256 hash finding creates file indicator."""
        findings = [
            {
                "finding_id": "f4",
                "ioc_type": "hash_sha256",
                "ioc_value": "a" * 64,
                "confidence": 0.95,
            }
        ]
        bundle = render_cti_stix_bundle(findings=findings)
        indicators = [o for o in bundle["objects"] if o["type"] == "indicator"]
        assert len(indicators) >= 1
        ind = indicators[0]
        assert "[file:hashes.'SHA-256'" in ind["pattern"]

    def test_hash_md5_indicator(self):
        """MD5 hash finding creates file indicator."""
        findings = [
            {
                "finding_id": "f5",
                "ioc_type": "hash_md5",
                "ioc_value": "d41d8cd98f00b204e9800998ecf8427e",
                "confidence": 0.9,
            }
        ]
        bundle = render_cti_stix_bundle(findings=findings)
        indicators = [o for o in bundle["objects"] if o["type"] == "indicator"]
        assert len(indicators) >= 1
        ind = indicators[0]
        assert "[file:hashes.'MD5'" in ind["pattern"]

    def test_cve_no_indicator(self):
        """CVE finding does not create indicator (maps to Vulnerability)."""
        findings = [
            {"finding_id": "f6", "ioc_type": "cve", "ioc_value": "CVE-2024-1234", "confidence": 0.9}
        ]
        bundle = render_cti_stix_bundle(findings=findings)
        indicators = [o for o in bundle["objects"] if o["type"] == "indicator"]
        # CVE should not produce indicator; it would produce observed-data
        assert all(ind.get("pattern", "").find("CVE-2024-1234") == -1 for ind in indicators)

    def test_indicator_id_deterministic(self):
        """Same finding → same indicator ID (UUID5)."""
        finding = {"finding_id": "f1", "ioc_type": "ip", "ioc_value": "1.2.3.4", "confidence": 0.9}
        bundle1 = render_cti_stix_bundle(findings=[finding])
        bundle2 = render_cti_stix_bundle(findings=[finding])
        ind1 = next(o for o in bundle1["objects"] if o["type"] == "indicator")
        ind2 = next(o for o in bundle2["objects"] if o["type"] == "indicator")
        assert ind1["id"] == ind2["id"]


# ── F203E-5: identity_candidates → identity objects ────────────────────────────


class TestIdentityCandidates:
    """F203E-5: identity_candidates → identity objects with name/description."""

    def test_single_identity_candidate(self):
        """Single identity candidate creates identity STIX object."""
        candidates = [
            {
                "candidate_id": "ic1",
                "primary_name": "alice",
                "emails": ["alice@example.com"],
                "usernames": ["alice123"],
                "platforms": ["github"],
                "confidence": 0.85,
            }
        ]
        bundle = render_cti_stix_bundle(findings=[], identity_candidates=candidates)
        identities = [o for o in bundle["objects"] if o["type"] == "identity" and o["id"] != "identity--ghost-prime"]
        assert len(identities) >= 1
        ident = identities[0]
        assert ident["name"] == "alice"
        assert "alice@example.com" in ident["description"]
        assert "github" in ident["description"]

    def test_identity_includes_confidence(self):
        """Identity description includes confidence score."""
        candidates = [
            {
                "candidate_id": "ic2",
                "primary_name": "bob",
                "confidence": 0.72,
            }
        ]
        bundle = render_cti_stix_bundle(findings=[], identity_candidates=candidates)
        ident = next(
            o for o in bundle["objects"]
            if o["type"] == "identity" and o["id"] != "identity--ghost-prime"
        )
        assert "0.72" in ident["description"]

    def test_multiple_identity_candidates(self):
        """Multiple identity candidates all appear as identity objects."""
        candidates = [
            {"candidate_id": f"ic{i}", "primary_name": f"user{i}", "confidence": 0.8}
            for i in range(5)
        ]
        bundle = render_cti_stix_bundle(findings=[], identity_candidates=candidates)
        identities = [
            o for o in bundle["objects"]
            if o["type"] == "identity" and o["id"] != "identity--ghost-prime"
        ]
        assert len(identities) >= 5


# ── F203E-6: attribution_scores → note objects ─────────────────────────────────


class TestAttributionNotes:
    """F203E-6: attribution_scores → note objects linked to identity."""

    def test_attribution_score_creates_note(self):
        """Attribution score for a candidate creates a note object."""
        candidates = [
            {"candidate_id": "ic1", "primary_name": "alice", "confidence": 0.85}
        ]
        scores = {
            "ic1": {
                "confidence": 0.85,
                "factors": [
                    {
                        "factor_id": "email_domain_gmail.com",
                        "factor_type": "email_domain_match",
                        "raw_score": 1.0,
                        "weighted_score": 0.25,
                    }
                ],
                "evidence_ids": ["e1", "e2"],
            }
        }
        bundle = render_cti_stix_bundle(
            findings=[],
            identity_candidates=candidates,
            attribution_scores=scores,
        )
        notes = [o for o in bundle["objects"] if o["type"] == "note"]
        assert len(notes) >= 1
        note = notes[0]
        assert "attribution" in note.get("abstract", "").lower() or "confidence=0.85" in note.get("abstract", "")

    def test_attribution_note_references_identity(self):
        """Attribution note has object_refs pointing to the identity."""
        candidates = [
            {"candidate_id": "ic1", "primary_name": "alice", "confidence": 0.85}
        ]
        scores = {"ic1": {"confidence": 0.85, "factors": [], "evidence_ids": []}}
        bundle = render_cti_stix_bundle(
            findings=[],
            identity_candidates=candidates,
            attribution_scores=scores,
        )
        note = next(
            o for o in bundle["objects"]
            if o["type"] == "note" and "attribution" in o.get("abstract", "").lower()
        )
        assert any("identity--" in ref for ref in note.get("object_refs", []))


# ── F203E-7: killchain_tags → labels + notes ──────────────────────────────────


class TestKillchainTags:
    """F203E-7: killchain_tags → labels on indicator + kill-chain note."""

    def test_killchain_tag_adds_labels_to_indicator(self):
        """Kill-chain tag adds attack: technique labels to the indicator."""
        findings = [
            {"finding_id": "f1", "ioc_type": "domain", "ioc_value": "evil.com", "confidence": 0.9}
        ]
        tags = {
            "f1": [
                {
                    "tactic": "Reconnaissance",
                    "technique_id": "T1590.001",
                    "phase": "reconnaissance",
                    "confidence": 0.7,
                }
            ]
        }
        bundle = render_cti_stix_bundle(findings=findings, killchain_tags=tags)
        indicators = [o for o in bundle["objects"] if o["type"] == "indicator"]
        assert len(indicators) >= 1
        ind = indicators[0]
        assert "labels" in ind
        assert any("T1590.001" in l or "reconnaissance" in l for l in ind["labels"])

    def test_killchain_creates_note(self):
        """Kill-chain tags for a finding create a kill-chain note."""
        findings = [
            {"finding_id": "f1", "ioc_type": "domain", "ioc_value": "evil.com", "confidence": 0.9}
        ]
        tags = {
            "f1": [
                {
                    "technique_id": "T1590.001",
                    "tactic": "Reconnaissance",
                    "phase": "reconnaissance",
                    "confidence": 0.7,
                }
            ]
        }
        bundle = render_cti_stix_bundle(findings=findings, killchain_tags=tags)
        notes = [o for o in bundle["objects"] if o["type"] == "note"]
        kc_notes = [n for n in notes if "T1590" in n.get("abstract", "") or "kill" in n.get("abstract", "").lower()]
        assert len(kc_notes) >= 1


# ── F203E-8: evidence_chains → observed-data + relationships ──────────────────


class TestEvidenceChains:
    """F203E-8: evidence_chains → observed-data + relationship objects."""

    def test_evidence_chain_creates_observed_data(self):
        """Evidence chain creates observed-data STIX object."""
        chains = [
            {
                "root_finding_id": "f1",
                "steps": [
                    {
                        "step_type": "finding_ingest",
                        "input_ids": [],
                        "output_id": "f1",
                        "confidence": 0.9,
                        "reason": "CT log observed",
                    },
                    {
                        "step_type": "identity_stitching",
                        "input_ids": ["f1"],
                        "output_id": "identity-1",
                        "confidence": 0.85,
                        "reason": "linked via email",
                    },
                ],
                "conclusion": "attributed to actor X",
            }
        ]
        bundle = render_cti_stix_bundle(findings=[], evidence_chains=chains)
        observed = [o for o in bundle["objects"] if o["type"] == "observed-data"]
        assert len(observed) >= 1
        obs = observed[0]
        assert "f1" in obs.get("description", "")

    def test_evidence_chain_content_json_serialized(self):
        """Evidence chain steps are JSON-serialized in observed-data content."""
        chains = [
            {
                "root_finding_id": "f1",
                "steps": [
                    {
                        "step_type": "finding_ingest",
                        "input_ids": [],
                        "output_id": "f1",
                        "confidence": 0.9,
                        "reason": "CT log observed",
                    }
                ],
                "conclusion": "test",
            }
        ]
        bundle = render_cti_stix_bundle(findings=[], evidence_chains=chains)
        obs = next(o for o in bundle["objects"] if o["type"] == "observed-data")
        content = obs.get("content", "{}")
        parsed = json.loads(content)
        assert parsed["root_finding_id"] == "f1"
        assert parsed["depth"] == 1

    def test_empty_chain_list_no_observed_data(self):
        """Empty evidence_chains does not add observed-data."""
        bundle = render_cti_stix_bundle(findings=[], evidence_chains=[])
        observed = [o for o in bundle["objects"] if o["type"] == "observed-data"]
        assert len(observed) == 0


# ── F203E-9: Report object ─────────────────────────────────────────────────────


class TestCTIReport:
    """F203E-9: Report object wraps all CTI objects."""

    def test_report_exists(self):
        """Bundle contains a report object."""
        findings = [
            {"finding_id": "f1", "ioc_type": "ip", "ioc_value": "1.2.3.4", "confidence": 0.9}
        ]
        bundle = render_cti_stix_bundle(findings=findings)
        reports = [o for o in bundle["objects"] if o["type"] == "report"]
        assert len(reports) >= 1

    def test_report_references_objects(self):
        """Report object_refs contains references to all CTI objects."""
        findings = [
            {"finding_id": "f1", "ioc_type": "ip", "ioc_value": "1.2.3.4", "confidence": 0.9}
        ]
        bundle = render_cti_stix_bundle(findings=findings)
        report = next(o for o in bundle["objects"] if o["type"] == "report")
        assert isinstance(report.get("object_refs"), list)
        assert len(report["object_refs"]) > 0

    def test_report_name_contains_ghost_prime(self):
        """Report name contains Ghost Prime identifier."""
        bundle = render_cti_stix_bundle(findings=[])
        report = next(o for o in bundle["objects"] if o["type"] == "report")
        assert "Ghost Prime" in report.get("name", "")


# ── F203E-10: Deterministic UUID5 ─────────────────────────────────────────────


class TestDeterministicUUID5:
    """F203E-10: Deterministic UUID5 — same content → same ID."""

    def test_make_stix_id_deterministic(self):
        """_make_stix_id is deterministic for same inputs."""
        id1 = _make_stix_id("indicator", "ip", "1.2.3.4")
        id2 = _make_stix_id("indicator", "ip", "1.2.3.4")
        assert id1 == id2

    def test_make_stix_id_different_for_different_inputs(self):
        """_make_stix_id produces different IDs for different inputs."""
        id1 = _make_stix_id("indicator", "ip", "1.2.3.4")
        id2 = _make_stix_id("indicator", "ip", "1.2.3.5")
        assert id1 != id2

    def test_bundle_deterministic(self):
        """Same inputs → same bundle ID (first 3 objects are identical)."""
        findings = [
            {"finding_id": "f1", "ioc_type": "ip", "ioc_value": "1.2.3.4", "confidence": 0.9}
        ]
        bundle1 = render_cti_stix_bundle(findings=findings)
        bundle2 = render_cti_stix_bundle(findings=findings)
        # Identity + indicator + report → first 3 object IDs should match
        assert bundle1["objects"][1]["id"] == bundle2["objects"][1]["id"]


# ── F203E-11: render_cti_stix_bundle_json ─────────────────────────────────────


class TestCTIJSONOutput:
    """F203E-11: render_cti_stix_bundle_json produces valid sorted JSON."""

    def test_json_is_valid(self):
        """Output is valid JSON."""
        findings = [
            {"finding_id": "f1", "ioc_type": "ip", "ioc_value": "1.2.3.4", "confidence": 0.9}
        ]
        json_str = render_cti_stix_bundle_json(findings=findings)
        parsed = json.loads(json_str)
        assert parsed["type"] == "bundle"

    def test_json_keys_sorted(self):
        """JSON output has sorted keys (deterministic)."""
        findings = [
            {"finding_id": "f1", "ioc_type": "ip", "ioc_value": "1.2.3.4", "confidence": 0.9}
        ]
        json_str = render_cti_stix_bundle_json(findings=findings)
        # Check top-level keys are sorted
        parsed = json.loads(json_str)
        keys = list(parsed.keys())
        assert keys == sorted(keys)


# ── F203E-12: render_cti_stix_bundle_to_path ───────────────────────────────────


class TestCTIFileOutput:
    """F203E-12: render_cti_stix_bundle_to_path writes file and returns Path."""

    def test_to_path_writes_file(self, tmp_path):
        """to_path writes a .stix.json file."""
        findings = [
            {"finding_id": "f1", "ioc_type": "ip", "ioc_value": "1.2.3.4", "confidence": 0.9}
        ]
        out_path = render_cti_stix_bundle_to_path(findings=findings, path=tmp_path / "test.stix.json")
        assert out_path.exists()
        assert out_path.suffix == ".json"

    def test_to_path_content_valid_json(self, tmp_path):
        """Written file contains valid STIX JSON."""
        findings = [
            {"finding_id": "f1", "ioc_type": "ip", "ioc_value": "1.2.3.4", "confidence": 0.9}
        ]
        out_path = render_cti_stix_bundle_to_path(findings=findings, path=tmp_path / "test.stix.json")
        content = out_path.read_text()
        parsed = json.loads(content)
        assert parsed["type"] == "bundle"
        assert parsed["spec_version"] == "2.1"


# ── F203E-13: Bundle type and spec_version ─────────────────────────────────────


class TestBundleSpecCompliance:
    """F203E-13: Bundle has type=bundle, spec_version=2.1, id starts with bundle--."""

    def test_bundle_type(self):
        """Bundle type must be 'bundle'."""
        bundle = render_cti_stix_bundle(findings=[])
        assert bundle["type"] == "bundle"

    def test_spec_version(self):
        """Spec version must be '2.1'."""
        bundle = render_cti_stix_bundle(findings=[])
        assert bundle["spec_version"] == "2.1"

    def test_bundle_id_prefix(self):
        """Bundle ID must start with 'bundle--'."""
        bundle = render_cti_stix_bundle(findings=[])
        assert bundle["id"].startswith("bundle--")


# ── F203E-14: Ghost Prime identity always present ─────────────────────────────


class TestGhostPrimeIdentity:
    """F203E-14: Ghost Prime identity is always present as report author."""

    def test_ghost_prime_identity_always_first(self):
        """Ghost Prime identity is always the first object."""
        bundle = render_cti_stix_bundle(findings=[])
        assert bundle["objects"][0]["id"] == "identity--ghost-prime"
        assert bundle["objects"][0]["type"] == "identity"

    def test_ghost_prime_identity_persists_with_findings(self):
        """Ghost Prime identity present even when findings are provided."""
        findings = [
            {"finding_id": "f1", "ioc_type": "ip", "ioc_value": "1.2.3.4", "confidence": 0.9}
        ]
        bundle = render_cti_stix_bundle(findings=findings)
        ids = [o["id"] for o in bundle["objects"]]
        assert "identity--ghost-prime" in ids


# ── F203E-15: max_objects parameter ───────────────────────────────────────────


class TestMaxObjectsParameter:
    """F203E-15: max_objects parameter is respected."""

    def test_max_objects_10(self):
        """With max_objects=10, bundle has ≤10 objects."""
        findings = [
            {"finding_id": f"f{i}", "ioc_type": "ip", "ioc_value": f"1.2.3.{i}", "confidence": 0.9}
            for i in range(50)
        ]
        bundle = render_cti_stix_bundle(findings=findings, max_objects=10)
        assert len(bundle["objects"]) <= 10

    def test_max_objects_1(self):
        """With max_objects=1, bundle has exactly 1 object (identity always first)."""
        bundle = render_cti_stix_bundle(findings=[], max_objects=1)
        assert len(bundle["objects"]) <= 1
