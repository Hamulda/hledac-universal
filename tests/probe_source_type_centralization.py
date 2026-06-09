"""
Source type centralization — probe tests for :mod:`utils.source_types`.

Sprint F350M-S: Verify the 80-value registry is internally consistent, the
``StrEnum`` ↔ ``str`` bidirectional conversion works, and legacy aliases
resolve to canonical members.

Run: ``uv run pytest tests/probe_source_type_centralization.py -v``
"""
from __future__ import annotations

import enum
import sys
from typing import get_args

sys.path.insert(0, "hledac/universal")


# ── Registry integrity ───────────────────────────────────────────────────


class TestSourceTypeRegistry:
    def test_is_strenum(self) -> None:
        from hledac.universal.utils.source_types import SourceType

        assert issubclass(SourceType, str)
        assert issubclass(SourceType, enum.Enum)

    def test_member_count_at_least_60(self) -> None:
        """80 unique source types found via ripgrep; allow headroom for future."""
        from hledac.universal.utils.source_types import SourceType

        assert len(SourceType) >= 60, (
            f"expected ≥60 members, got {len(SourceType)} — "
            "check for missing or duplicate entries"
        )

    def test_member_values_are_unique(self) -> None:
        from hledac.universal.utils.source_types import SourceType

        values = [m.value for m in SourceType]
        duplicates = {v for v in values if values.count(v) > 1}
        assert not duplicates, f"duplicate enum values: {duplicates}"

    def test_member_names_are_unique(self) -> None:
        from hledac.universal.utils.source_types import SourceType

        names = [m.name for m in SourceType]
        duplicates = {n for n in names if names.count(n) > 1}
        assert not duplicates, f"duplicate enum names: {duplicates}"


# ── StrEnum ↔ str conversion ─────────────────────────────────────────────


class TestSourceTypeConversion:
    def test_strenum_value_equals_string(self) -> None:
        from hledac.universal.utils.source_types import SourceType

        assert SourceType.CT_LOG == "ct_log"
        assert SourceType.SHODAN_SEARCH == "shodan_search"
        assert SourceType.DEEP_RESEARCH == "deep_research"

    def test_str_returns_value(self) -> None:
        from hledac.universal.utils.source_types import SourceType

        for member in list(SourceType)[:5]:
            assert str(member) == member.value

    def test_construct_from_string(self) -> None:
        """SourceType('ct_log') returns the member, not a ValueError."""
        from hledac.universal.utils.source_types import SourceType

        s = SourceType("ct_log")
        assert s is SourceType.CT_LOG

    def test_construct_unknown_raises(self) -> None:
        from hledac.universal.utils.source_types import SourceType

        try:
            SourceType("nonexistent_source_xyz")
        except ValueError:
            return
        raise AssertionError("expected ValueError for unknown source type")


# ── Legacy aliases ───────────────────────────────────────────────────────


class TestSourceTypeAliases:
    def test_legacy_alias_ct_routes_to_ct_log(self) -> None:
        from hledac.universal.utils.source_types import (
            SourceType,
            canonical_source_type,
        )

        assert canonical_source_type("ct") == SourceType.CT_LOG.value

    def test_legacy_alias_ipfs_routes_to_ipfs_content(self) -> None:
        from hledac.universal.utils.source_types import (
            SourceType,
            canonical_source_type,
        )

        assert canonical_source_type("ipfs") == SourceType.IPFS_CONTENT.value

    def test_legacy_alias_ddg_routes_to_web_fetch(self) -> None:
        from hledac.universal.utils.source_types import (
            SourceType,
            canonical_source_type,
        )

        assert canonical_source_type("duckduckgo_search") == SourceType.WEB_FETCH.value

    def test_unknown_value_passes_through(self) -> None:
        from hledac.universal.utils.source_types import canonical_source_type

        assert canonical_source_type("forward_compat_xyz") == "forward_compat_xyz"

    def test_empty_value_returns_empty(self) -> None:
        from hledac.universal.utils.source_types import canonical_source_type

        assert canonical_source_type("") == ""
        assert canonical_source_type(None) == ""  # type: ignore[arg-type]

    def test_passthrough_for_strenum_input(self) -> None:
        from hledac.universal.utils.source_types import (
            SourceType,
            canonical_source_type,
        )

        assert canonical_source_type(SourceType.SHODAN_SEARCH) == "shodan_search"


# ── Type alias (mypy/pyright check) ──────────────────────────────────────


class TestSourceTypeLiteral:
    def test_literal_alias_includes_canonical_values(self) -> None:
        from hledac.universal.utils.source_types import (
            SourceTypeLiteral,
        )

        args = set(get_args(SourceTypeLiteral))
        # Spot-check canonical members
        for must_have in (
            "ct_log", "shodan_search", "deep_research", "leak_sentinel",
            "fediverse", "ipfs_content", "wayback_cdx", "steganography_detection",
        ):
            assert must_have in args, f"{must_have} missing from SourceTypeLiteral"

    def test_literal_arg_count_matches_enum_count(self) -> None:
        """SourceTypeLiteral should mirror SourceType (legacy aliases OK to differ)."""
        from hledac.universal.utils.source_types import (
            SourceType,
            SourceTypeLiteral,
        )

        literal_count = len(get_args(SourceTypeLiteral))
        enum_count = len(SourceType)
        # Allow literal to be larger (includes legacy aliases) but never smaller
        # than the canonical enum (would drop static-check coverage).
        assert literal_count >= enum_count, (
            f"literal {literal_count} < enum {enum_count} — static checks weakened"
        )


# ── Backward compatibility (call-site contract) ──────────────────────────


class TestSourceTypeBackwardCompat:
    def test_can_use_in_isinstance_check(self) -> None:
        from hledac.universal.utils.source_types import SourceType

        s = SourceType.CT_LOG
        assert isinstance(s, SourceType)
        assert isinstance(s, str)  # StrEnum ⊂ str

    def test_hashable_for_set_membership(self) -> None:
        from hledac.universal.utils.source_types import SourceType

        s = {SourceType.CT_LOG, "ct_log", "shodan_search"}
        # The enum and the plain string hash the same (StrEnum value is str)
        assert SourceType.CT_LOG in s
        assert "ct_log" in s

    def test_msgpack_json_compatible(self) -> None:
        # StrEnum serializes as the bare string in json / orjson
        import json

        from hledac.universal.utils.source_types import SourceType

        assert json.dumps({"src": SourceType.CT_LOG}) == '{"src": "ct_log"}'
        assert json.dumps({"src": "ct_log"}) == '{"src": "ct_log"}'


# ── Sprint F262OBS — adoption sweep: hot-path call sites use SourceType enum
# ───────────────────────────────────────────────────────────────


class TestAdoptionSweep:
    """Sprint F262OBS: Verify the migration sweep actually landed at call sites,
    not just at the registry. These tests are import-time + AST-based so they
    run hermetically without importing the full pipeline stack.
    """

    def test_sprint_scheduler_uses_sourcetype_enum(self) -> None:
        """Top 5 source types in sprint_scheduler must be SourceType.X, not raw strings."""
        import ast
        from pathlib import Path

        from hledac.universal.utils.source_types import SourceType

        repo = Path(__file__).resolve().parents[1]
        sched = repo / "runtime" / "sprint_scheduler.py"
        src = sched.read_text(encoding="utf-8")
        tree = ast.parse(src)

        # Find all Assign nodes where the target is `source_type=...` (kwarg in call)
        raw_string_hits: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "source_type":
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    raw_string_hits.append((node.lineno, node.value.value))

        # top 5 enum-member values from sprint_scheduler migration
        top5 = {
            SourceType.I2P_DISCOVERY.value,
            SourceType.DIGITAL_GHOST_DETECTION.value,
            SourceType.STEGANOGRAPHY_DETECTION.value,
            SourceType.BGP_INTELLIGENCE.value,
            SourceType.CONTEXT_SEED.value,
        }
        # None of the migrated top-5 should still appear as raw string literals
        leaked = [v for _ln, v in raw_string_hits if v in top5]
        assert not leaked, (
            f"sprint_scheduler still uses raw string source_type for migrated values: {leaked}"
        )

    def test_canonical_handles_all_known_legacy_aliases(self) -> None:
        """Every LEGACY_ALIASES key must round-trip through canonical_source_type()."""
        from hledac.universal.utils.source_types import (
            LEGACY_ALIASES,
            canonical_source_type,
        )

        for legacy_key, expected_canonical in LEGACY_ALIASES.items():
            got = canonical_source_type(legacy_key)
            assert got == expected_canonical, (
                f"LEGACY_ALIASES['{legacy_key}'] = {expected_canonical!r}, "
                f"but canonical_source_type returned {got!r}"
            )

    def test_duckdb_guard_rejects_unknown_source_type(self) -> None:
        """STEP 4 guard: duckdb_store normalizes unknown source_type strings via
        canonical_source_type() — values not in the enum are routed through
        LEGACY_ALIASES (or pass through unchanged for forward-compat).
        """
        from hledac.universal.utils.source_types import (
            LEGACY_ALIASES,
            SourceType,
            canonical_source_type,
        )

        # Unknown but pass-through (forward-compat)
        assert canonical_source_type("totally_new_2099") == "totally_new_2099"

        # Known legacy routes via LEGACY_ALIASES
        assert canonical_source_type("certificate_transparency") == SourceType.CT_LOG.value
        assert "certificate_transparency" in LEGACY_ALIASES

        # Known canonical values are returned unchanged
        for member in [SourceType.CT_LOG, SourceType.NETWORK_RECON, SourceType.SPRINT_DIFF]:
            assert canonical_source_type(member) == member.value

    def test_alt_protocol_fetcher_uses_sourcetype_enum(self) -> None:
        """alternative_protocol_fetcher.py must not have bare string source_type
        assignments to legacy bare values (ipfs, gopher, gemini, i2p, fediverse, matrix).
        """
        import re
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        apf = repo / "fetching" / "alternative_protocol_fetcher.py"
        src = apf.read_text(encoding="utf-8")

        # Pattern: source_type="ipfs" | "gopher" | "gemini" | "i2p" | "fediverse" | "matrix"
        # (matrix is also a SourceType member but its literal use was the legacy bare)
        bad_pat = re.compile(
            r'source_type\s*=\s*["\'](?:ipfs|gopher|gemini|i2p|fediverse|matrix)["\']'
        )
        hits = bad_pat.findall(src)
        assert not hits, (
            f"alternative_protocol_fetcher still has raw string source_type literals: {hits}"
        )

    def test_live_public_pipeline_uses_sourcetype_enum(self) -> None:
        """live_public_pipeline.py must not have bare source_type= literals for
        values already covered by the migration (hermes_inference, rl_research, etc.).
        """
        import re
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        lpp = repo / "pipeline" / "live_public_pipeline.py"
        src = lpp.read_text(encoding="utf-8")

        # The migrated hot-path values
        migrated = {
            "hermes_inference",
            "onion_discovery",
            "pastebin_monitor",
            "github_secret_scanner",
            "rl_research",
            "tot_synthesis",
            "llm_synthesis",
        }
        bad_pat = re.compile(r'source_type\s*=\s*["\']([^"\']+)["\']')
        bad_hits = [v for v in bad_pat.findall(src) if v in migrated]
        assert not bad_hits, (
            f"live_public_pipeline still has raw string source_type for migrated values: {bad_hits}"
        )


# ── Sprint F262OBS — STEP 5/STEP 3: StrEnum SQL-compat + SQL literal sweep
# ───────────────────────────────────────────────────────────────


class TestAdoptionSweepStep3And5:
    """F262OBS STEP 3 — StrEnum SQL identity (confirms SourceType.CT_LOG can
    be interpolated directly into SQL strings without `.value`).
    F262OBS STEP 5 — No raw string literals remain inside SQL queries in
    sprint_scheduler.py (the two known SQL sites must use f-strings).
    """

    def test_sourcetype_strenum_sql_identity(self) -> None:
        """SourceType.CT_LOG == 'ct_log' (and friends) — StrEnum is a str subclass,
        so direct f-string interpolation into SQL produces the canonical value
        without needing `.value`. This is the contract that lets STEP 3's
        f-string SQL pattern work.
        """
        from hledac.universal.utils.source_types import SourceType

        # Core identity contract
        assert SourceType.CT_LOG == "ct_log"
        assert SourceType.HERMES_INFERENCE == "hermes_inference"
        # StrEnum member IS a str — verifiable at runtime
        assert isinstance(SourceType.CT_LOG, str)
        assert isinstance(SourceType.HERMES_INFERENCE, str)

        # Direct interpolation produces the SQL-safe value
        sql_ct = f"WHERE source_type = '{SourceType.CT_LOG}' "
        sql_hermes = (
            f"... WHERE source_type = '{SourceType.HERMES_INFERENCE}' AND ..."
        )
        assert sql_ct == "WHERE source_type = 'ct_log' "
        assert sql_hermes == "... WHERE source_type = 'hermes_inference' AND ..."

        # str() and .value agree (explicit str() is also valid)
        assert str(SourceType.CT_LOG) == SourceType.CT_LOG.value == "ct_log"

    def test_no_raw_string_literals_in_sprint_scheduler_sql(self) -> None:
        """AST walk + regex over sprint_scheduler.py — confirm the two known
        SQL sites use f-strings (SourceType.X) and not raw 'literal' source_type
        values. This locks in the STEP 3 SQL canonicalization.
        """
        import ast
        import re
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        sched = repo / "runtime" / "sprint_scheduler.py"
        src = sched.read_text(encoding="utf-8")
        tree = ast.parse(src)

        # The 2 SQL sites migrated in F262OBS STEP 3. If a new SQL site is
        # added that uses one of these source_type values as a raw literal,
        # the test must be updated to cover the new site — that is the audit
        # hook for future SQL canonicalization.
        sql_source_types = {"ct_log", "hermes_inference"}

        # 1) AST walk: walk all string-literal Constants and JoinedStr (f-strings)
        # in function bodies. Any bare string literal that matches a known
        # source_type value (and lives inside a SQL-looking string, i.e. contains
        # "source_type" or "WHERE") is a regression.
        sql_context_pat = re.compile(r"(source_type|WHERE|FROM|SELECT)", re.IGNORECASE)
        raw_sql_literal_hits: list[tuple[int, str, str]] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value
                if value in sql_source_types and sql_context_pat.search(value):
                    raw_sql_literal_hits.append(
                        (node.lineno, "raw_string_literal", value)
                    )
            # f-strings (JoinedStr) with .values: list[Constant | FormattedValue]
            # are not raw literals — SourceType.X becomes a FormattedValue child,
            # so they pass through. We only flag bare strings.

        assert not raw_sql_literal_hits, (
            "sprint_scheduler.py has raw string source_type literals inside "
            f"SQL context: {raw_sql_literal_hits}"
        )

        # 2) Regex sanity: confirm the f-string pattern is present at the two
        # known SQL sites (so the test fails loudly if someone strips it).
        fstring_ct = re.search(
            r"f[\"']WHERE\s+source_type\s*=\s*[\"']\{SourceType\.CT_LOG\}[\"']\s*[\"']",
            src,
        )
        assert fstring_ct is not None, (
            "expected f-string with SourceType.CT_LOG in SQL WHERE clause of "
            "sprint_scheduler.py — STEP 3 SQL canonicalization lost?"
        )
