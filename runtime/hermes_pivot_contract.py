"""
F256: HermesInferenceOutput contract — cross-sprint data transfer object.

Canonical home for Hermes3Engine inference results used by:
- pivot_planner.py    (score_with_hermes_output, _pivot_from_hermes_output)
- sprint_advisory_runner.py (from_dict loading from DuckDB)
- live_public_pipeline.py  (construction + to_dict persistence)
"""


from __future__ import annotations

import msgspec

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
MAX_INFERENCE_ITEMS: int = 50  # cap hermes_outputs list in advisory runner

# --------------------------------------------------------------------------- #
# Struct
# --------------------------------------------------------------------------- #
class HermesInferenceOutput(msgspec.Struct, frozen=True, gc=False):
    """Hermes3Engine structured inference output for pivot planning.

    Migrated from @dataclass(slots=True) to msgspec.Struct(frozen=True) for:
    - ~2-3× faster instantiation on hot path
    - C-level to_builtins() serialization (~50 ns vs 5-10 µs hasattr chain)
    - Zero-GC overhead, Python 3.14 compatible
    """

    output_id: str = ""
    source_finding_id: str = ""
    inference_type: str = ""  # e.g. "report_synthesis"
    timestamp: float = 0.0
    primary_text: str = ""  # unused by pivot_planner but stored
    confidence: float = 0.0  # 0.0–1.0

    # Core pivot extraction targets
    key_iocs: list[str] = msgspec.field(default_factory=list)  # domains, IPs, hashes, emails
    key_entities: list[str] = msgspec.field(default_factory=list)  # extracted entities
    pivot_suggestions: list[str] = msgspec.field(default_factory=list)  # LLM-suggested queries

    # Metadata
    bounded: bool = False
    tokens_used: int = 0
    model_name: str = ""
    source_hints: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        """Convert to dict via msgspec.to_builtins (C-level, ~50 ns)."""
        return msgspec.to_builtins(self)

    @classmethod
    def from_dict(cls, payload: dict) -> HermesInferenceOutput:
        """Reconstruct from dict — compatible with msgspec.to_builtins output."""
        # Explicit tuple conversion: msgspec.convert does not coerce list→tuple
        payload = dict(payload)
        if "source_hints" in payload and isinstance(payload["source_hints"], list):
            payload["source_hints"] = tuple(payload["source_hints"])
        return msgspec.convert(payload, cls)

    # ------------------------------------------------------------------------- #
    # __all__
    # ------------------------------------------------------------------------- #
    __all__ = ["HermesInferenceOutput", "MAX_INFERENCE_ITEMS"]
