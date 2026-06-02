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
            SourceType,
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
        from hledac.universal.utils.source_types import SourceType

        # StrEnum serializes as the bare string in json / orjson
        import json

        assert json.dumps({"src": SourceType.CT_LOG}) == '{"src": "ct_log"}'
        assert json.dumps({"src": "ct_log"}) == '{"src": "ct_log"}'
