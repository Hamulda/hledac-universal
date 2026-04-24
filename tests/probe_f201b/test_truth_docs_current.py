"""
Sprint F201B: Truth Docs Current — Probe Tests
==============================================

Tests verify that documentation files are consistent with actual code.

Invariant table:
  F201B-1 | GHOST_INVARIANTS.md links _check_gathered to network.session_runtime
  F201B-2 | GHOST_INVARIANTS.md documents tuple return (ok, errors) contract
  F201B-3 | STORAGE_LAYER_DOCUMENTATION.md lists async_ingest_findings_batch in API
  F201B-4 | STORAGE_LAYER_DOCUMENTATION.md example uses async_ingest_findings_batch
  F201B-5 | STORAGE_LAYER_DOCUMENTATION.md has NO banned single-finding write example
  F201B-6 | REAL_ARCHITECTURE.md marks stealth/ as ACTIVE (was incorrectly dormant)
  F201B-7 | REAL_ARCHITECTURE.md marks prefetch/ as partially active (was dormant)
"""
from __future__ import annotations

import re
from pathlib import Path

# Paths: tests/probe_f201b/test_truth_docs_current.py → hledac/universal/
UNIVERSAL_ROOT = Path(__file__).parent.parent.parent  # hledac/universal/
REPO_ROOT = UNIVERSAL_ROOT.parent  # project root
GHOST_INVARIANTS = UNIVERSAL_ROOT / "GHOST_INVARIANTS.md"
STORAGE_DOC = UNIVERSAL_ROOT / "STORAGE_LAYER_DOCUMENTATION.md"
REAL_ARCH = UNIVERSAL_ROOT / "REAL_ARCHITECTURE.md"


class TestF201BGhostInvariantsCheckGathered:
    """F201B-1/2: _check_gathered authority in GHOST_INVARIANTS.md."""

    def _read_ghost(self) -> str:
        text = GHOST_INVARIANTS.read_text()
        assert text, f"GHOST_INVARIANTS.md is empty at {GHOST_INVARIANTS}"
        return text

    def test_check_gathered_links_to_session_runtime(self):
        """F201B-1: _check_gathered section links to network.session_runtime."""
        text = self._read_ghost()
        # The heading uses backticks: ### `_check_gathered` is called...
        # Look for the section starting from the heading
        section_match = re.search(
            r"###\s+`?_check_gathered`?.*?(?=###\s|\Z)",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        assert section_match, "_check_gathered section not found in GHOST_INVARIANTS.md"
        section = section_match.group(0)
        assert (
            "network.session_runtime" in section
        ), f"_check_gathered section does not reference network.session_runtime. Got:\n{section[:500]}"
        # Must NOT claim canonical authority is utils.async_helpers as primary
        # (it is legacy, documented as such, not canonical)
        assert "utils.async_helpers" not in section or "legacy" in section.lower(), (
            "GHOST_INVARIANTS.md still claims _check_gathered is from "
            "utils.async_helpers as primary canonical authority"
        )

    def test_check_gathered_documents_tuple_contract(self):
        """F201B-2: _check_gathered section documents Tuple return contract."""
        text = self._read_ghost()
        section_match = re.search(
            r"###\s+`?_check_gathered`?.*?(?=###\s|\Z)",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        assert section_match, "_check_gathered section not found"
        section = section_match.group(0)
        # Must document the tuple contract (ok_results, error_results)
        assert (
            "Tuple[List" in section or "tuple" in section.lower()
        ) and ("ok_results" in section or "error_results" in section), (
            f"_check_gathered section does not document Tuple[ok, errors] contract. "
            f"Got:\n{section[:500]}"
        )
        # Must document re-raise of CancelledError/BaseException
        assert (
            "re-raised" in section.lower()
            or "re-raise" in section.lower()
            or "raise" in section
        ) and (
            "CancelledError" in section or "BaseException" in section
        ), f"_check_gathered section missing re-raise contract. Got:\n{section[:500]}"

    def test_check_gathered_documents_legacy_variant(self):
        """F201B-2b: _check_gathered section notes utils.async_helpers is legacy."""
        text = self._read_ghost()
        section_match = re.search(
            r"###\s+`?_check_gathered`?.*?(?=###\s|\Z)",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        assert section_match, "_check_gathered section not found"
        section = section_match.group(0)
        # Should mention the legacy variant
        assert (
            "legacy" in section.lower() or "variant" in section.lower()
        ), f"_check_gathered section should document legacy variant. Got:\n{section[:500]}"


class TestF201BStorageDocCanonicalWritePath:
    """F201B-3/4/5: STORAGE_LAYER_DOCUMENTATION.md canonical write path."""

    def _read_storage(self) -> str:
        text = STORAGE_DOC.read_text()
        assert text, f"STORAGE_LAYER_DOCUMENTATION.md is empty at {STORAGE_DOC}"
        return text

    def test_lists_async_ingest_findings_batch_in_api(self):
        """F201B-3: DuckDBShadowStore API section lists async_ingest_findings_batch."""
        text = self._read_storage()
        # Find the DuckDBShadowStore API section
        duckdb_match = re.search(
            r"DuckDBShadowStore.*?(?=#+\s|\Z)",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        assert duckdb_match, "DuckDBShadowStore section not found"
        duckdb_section = duckdb_match.group(0)
        assert (
            "async_ingest_findings_batch" in duckdb_section
        ), f"async_ingest_findings_batch not listed in DuckDBShadowStore API. Got:\n{duckdb_section[:300]}"

    def test_usage_example_uses_async_ingest_findings_batch(self):
        """F201B-4: Usage guide example uses async_ingest_findings_batch."""
        text = self._read_storage()
        # Find the usage decision guide section
        usage_match = re.search(
            r"Which storage should I use.*?(?=#+\s|\Z)",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        assert usage_match, "Usage decision guide not found"
        usage_section = usage_match.group(0)
        # The example for DuckDBShadowStore/sprint analytics must use ingest
        assert (
            "async_ingest_findings_batch" in usage_section
        ), f"Usage example does not use async_ingest_findings_batch. Got:\n{usage_section[:300]}"

    def test_no_banned_single_finding_write_example(self):
        """F201B-5: No example suggests async_record_shadow_finding(finding)."""
        text = self._read_storage()
        # The banned pattern: a single-finding write that suggests bypassing batch
        banned_pattern = re.search(
            r"async_record_shadow_finding\s*\(\s*finding\s*\)",
            text,
        )
        assert not banned_pattern, (
            f"Banned pattern found: async_record_shadow_finding(finding) "
            f"in STORAGE_LAYER_DOCUMENTATION.md — this suggests single-finding "
            f"write outside canonical batch path"
        )


class TestF201BRealArchitectureActiveDormant:
    """F201B-6/7: REAL_ARCHITECTURE.md active/dormant verdicts."""

    def _read_real_arch(self) -> str:
        text = REAL_ARCH.read_text()
        assert text, f"REAL_ARCHITECTURE.md is empty at {REAL_ARCH}"
        return text

    def test_stealth_marked_active(self):
        """F201B-6: stealth/ is marked ACTIVE in REAL_ARCHITECTURE.md."""
        text = self._read_real_arch()
        # Match the full row (entire line) — markdown tables are single-line
        stealth_match = re.search(
            r"^\|\s*`stealth/`\s*\|.*$",
            text,
            re.MULTILINE,
        )
        assert stealth_match, "stealth/ row not found in REAL_ARCHITECTURE.md dormant table"
        stealth_row = stealth_match.group(0)
        assert (
            "**ACTIVE**" in stealth_row or "ACTIVE" in stealth_row
        ) and "canonical" in stealth_row.lower(), (
            f"stealth/ is not marked ACTIVE with canonical wiring. Got:\n{stealth_row}"
        )

    def test_prefetch_marked_partially_active(self):
        """F201B-7: prefetch/ is marked partially active (was fully dormant)."""
        text = self._read_real_arch()
        # Match the full row
        prefetch_match = re.search(
            r"^\|\s*`prefetch/`\s*\|.*$",
            text,
            re.MULTILINE,
        )
        assert prefetch_match, "prefetch/ row not found in REAL_ARCHITECTURE.md"
        prefetch_row = prefetch_match.group(0)
        # Must mention prefetch_oracle_integration is wired
        assert (
            "prefetch_oracle_integration" in prefetch_row
            or "částečně aktivní" in prefetch_row.lower()
            or "partially active" in prefetch_row.lower()
        ), f"prefetch/ does not mention prefetch_oracle_integration as active. Got:\n{prefetch_row}"

    def test_forensics_marked_active(self):
        """F201B-6b: forensics/ is marked ACTIVE (wired F195C, enriched F198B)."""
        text = self._read_real_arch()
        forensics_match = re.search(
            r"^\|\s*`forensics/`\s*\|.*$",
            text,
            re.MULTILINE,
        )
        assert forensics_match, "forensics/ row not found"
        forensics_row = forensics_match.group(0)
        assert "**active" in forensics_row.lower() or "ACTIVE" in forensics_row, (
            f"forensics/ is not marked ACTIVE. Got:\n{forensics_row}"
        )

    def test_multimodal_marked_active(self):
        """F201B-6c: multimodal/ is marked ACTIVE (wired F195C, extended F198C)."""
        text = self._read_real_arch()
        multimodal_match = re.search(
            r"^\|\s*`multimodal/`\s*\|.*$",
            text,
            re.MULTILINE,
        )
        assert multimodal_match, "multimodal/ row not found"
        multimodal_row = multimodal_match.group(0)
        assert "**active" in multimodal_row.lower() or "ACTIVE" in multimodal_row, (
            f"multimodal/ is not marked ACTIVE. Got:\n{multimodal_row}"
        )


class TestF201BDocCrossConsistency:
    """Cross-doc consistency checks."""

    def test_ghost_invariants_last_updated_f201b(self):
        """Last updated line reflects F201B."""
        text = GHOST_INVARIANTS.read_text()
        assert re.search(r"F201B", text), (
            "GHOST_INVARIANTS.md last-updated line does not reference F201B"
        )

    def test_storage_doc_notes_canonical_ingest(self):
        """STORAGE_LAYER_DOCUMENTATION.md notes async_ingest_findings_batch as canonical."""
        text = STORAGE_DOC.read_text()
        assert "canonical" in text.lower() and "async_ingest_findings_batch" in text, (
            "STORAGE_LAYER_DOCUMENTATION.md should note async_ingest_findings_batch as canonical"
        )

    def test_real_arch_date_updated(self):
        """REAL_ARCHITECTURE.md header mentions F201B or 2026-04-24."""
        text = REAL_ARCH.read_text()
        first_line = text.split("\n")[0]
        assert (
            "F201B" in first_line or "2026-04-24" in first_line
        ), f"REAL_ARCHITECTURE.md not updated to F201B. Header: {first_line}"
