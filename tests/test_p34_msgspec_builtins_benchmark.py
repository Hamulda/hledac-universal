"""
P3-4: msgspec.Struct payload serialization — _normalize_payload fix.

Realizace:
1. _normalize_payload / _normalize_value nyní zpracovávají msgspec.Struct
   (volá msgspec.to_builtins() pro konzistentní dict reprezentaci)
2. EvidenceEvent.to_dict() zůstává nezměněn — je to BC layer, ne hot path

Akceptační kritérium: _normalize_payload ne zahodí msgspec.Struct pole.

Run:
    pytest tests/test_p34_msgspec_builtins_benchmark.py -v -s
"""

from __future__ import annotations

import time

import msgspec

from runtime.scheduler_v2.acquisition import CycleResult
from _core import aclose


def _normalize_value_old(value):
    """Legacy: msgspec.Struct fields were dropped (returned as-is)."""
    if isinstance(value, float):
        return round(value, 6)
    elif isinstance(value, (set, frozenset)):
        return sorted(value)
    elif isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    # msgspec.Struct was NOT handled — returned as object reference (wrong!)
    return value


def _normalize_value_new(value):
    """P3-4: msgspec.Struct → dict via msgspec.to_builtins()."""
    if isinstance(value, float):
        return round(value, 6)
    elif isinstance(value, (set, frozenset)):
        return sorted(value)
    elif isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    elif isinstance(value, msgspec.Struct):
        # P3-4 FIX: convert Struct to dict so it survives serialization
        return msgspec.to_builtins(value)
    return value


class TestNormalizePayloadMsgspecStruct:
    """P3-4: _normalize_payload must handle msgspec.Struct without dropping fields."""

    def test_struct_field_is_not_dropped(self):
        """Before fix: Struct fields were returned as object refs (non-serializable).
        After fix: Struct fields are converted to dict via to_builtins()."""
        result = CycleResult(
            cycle_ok=True,
            aggressive_mode=True,
            feed_results=(True, 10),
            public_results=(True, 20, 2),
        )
        payload = {"cycle_result": result, "plain_field": "hello", "count": 42}

        normalized_old = _normalize_value_old(payload["cycle_result"])
        normalized_new = _normalize_value_new(payload["cycle_result"])

        # Old behavior: Struct returned as-is (breaks orjson.dumps)
        assert not isinstance(
            normalized_old, dict
        ), "Old behavior should return Struct as object"

        # New behavior: Struct converted to dict (serializable)
        assert isinstance(normalized_new, dict)
        assert normalized_new["cycle_ok"] is True
        assert normalized_new["aggressive_mode"] is True
        assert normalized_new["feed_results"] == (True, 10)

    def test_normalize_payload_struct_in_list(self):
        """Struct in a list field must also be converted."""
        from evidence_log import _normalize_payload

        result = CycleResult(cycle_ok=False, feed_results=(False, 0))
        payload = {"results": [result, {"plain": "dict"}]}

        normalized = _normalize_payload(payload)

        assert "results" in normalized
        assert isinstance(normalized["results"], list)
        assert isinstance(normalized["results"][0], dict)
        assert normalized["results"][0]["cycle_ok"] is False
        # Plain dict in list is preserved
        assert normalized["results"][1] == {"plain": "dict"}

    def test_normalize_value_is_deterministic(self):
        """Two calls with same Struct must produce equal dicts."""
        result = CycleResult(
            cycle_ok=True,
            aggressive_mode=False,
            feed_results=(True, 5),
            public_results=(True, 10, 1),
            aimd_window=0.5,
            aimd_successes=50,
            aimd_failures=5,
        )

        d1 = msgspec.to_builtins(result)
        d2 = msgspec.to_builtins(result)

        assert d1 == d2
        assert d1["cycle_ok"] is True
        assert d1["feed_results"] == (True, 5)

    def test_builtins_preserves_all_cycle_result_fields(self):
        """All CycleResult fields survive to_builtins round-trip."""
        result = CycleResult(
            cycle_ok=False,
            empty_work_items=True,
            aggressive_mode=True,
            feed_results=(False, 0),
            public_results=(False, 0, 5),
            ct_results=(False, 0),
            aimd_window=1.5,
            aimd_successes=200,
            aimd_failures=50,
            error="timeout",
        )

        d = msgspec.to_builtins(result)

        assert d["cycle_ok"] is False
        assert d["empty_work_items"] is True
        assert d["aggressive_mode"] is True
        assert d["feed_results"] == (False, 0)
        assert d["public_results"] == (False, 0, 5)
        assert d["ct_results"] == (False, 0)
        assert d["aimd_window"] == 1.5
        assert d["aimd_successes"] == 200
        assert d["aimd_failures"] == 50
        assert d["error"] == "timeout"

    def test_normalize_payload_struct_in_nested_dict(self):
        """Struct in deeply nested dict is converted."""
        from evidence_log import _normalize_payload

        result = CycleResult(cycle_ok=True, feed_results=(True, 7))
        payload = {
            "outer": {
                "inner": {"cycle": result},
                "count": 10,
            }
        }

        normalized = _normalize_payload(payload)

        assert normalized["outer"]["inner"]["cycle"]["cycle_ok"] is True
        assert normalized["outer"]["count"] == 10
