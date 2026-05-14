"""
Sprint F217B: LLM Candidate Registry + Fallback Config
======================================================

Safe, explicit LLM candidate registry and fallback configuration layer.
Does NOT swap the production primary reasoner — only prepares plumbing.

Registry roles:
- PRIMARY_REASONER: main research/synthesis model
- STRUCTURED_JSON: structured generation (JSON schema)
- FAST_ROUTER: fast routing decisions
- FALLBACK_REASONER: fallback when primary fails

No model loading occurs — only config resolution.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Role Constants ───────────────────────────────────────────────────────────

PRIMARY_REASONER = "primary_reasoner"
STRUCTURED_JSON = "structured_json"
FAST_ROUTER = "fast_router"
FALLBACK_REASONER = "fallback_reasoner"

# ─── Candidate Metadata ────────────────────────────────────────────────────────

LLM_CANDIDATES: dict[str, dict] = {
    # ── Primary Reasoner Candidates ──────────────────────────────────────────
    "deephermes": {
        "model_key": "deephermes",
        "model_id": "mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit",
        "role": PRIMARY_REASONER,
        "heavy": True,
        "m1_8gb_risk": "medium",
        "is_current_default": True,
        "is_preview": True,  # Preview — HuggingFace preview tag
        "requires_benchmark": False,  # now production default per F217C
        "allowed_as_default": True,
        "mutex_group": "heavy_llm",
        "notes": "DeepHermes 3 3B 4bit — default primary reasoning model (F217C swap)",
    },
    "hermes": {
        "model_key": "hermes",
        "model_id": "mlx-community/Hermes-3-Llama-3.2-3B-4bit",
        "role": PRIMARY_REASONER,
        "heavy": True,
        "m1_8gb_risk": "medium",
        "is_current_default": False,
        "is_preview": False,
        "requires_benchmark": False,  # already production baseline
        "allowed_as_default": True,
        "mutex_group": "heavy_llm",
        "notes": "Hermes-3 3B 4bit — rollback/fallback candidate",
    },
    "nanbeige": {
        "model_key": "nanbeige",
        "model_id": "mlx-community/Nanbeige4.1-3B-4bit",
        "role": PRIMARY_REASONER,
        "heavy": True,
        "m1_8gb_risk": "medium",
        "is_current_default": False,
        "is_preview": False,
        "requires_benchmark": True,
        "allowed_as_default": True,  # non-preview, can be default after benchmark
        "mutex_group": "heavy_llm",
        "notes": "Strong instruction-following, smaller community model",
    },
    # ── Fast Router ──────────────────────────────────────────────────────────
    "smollm3": {
        "model_key": "smollm3",
        "model_id": "mlx-community/SmolLM3-3B-4bit",
        "role": FAST_ROUTER,
        "heavy": True,
        "m1_8gb_risk": "medium",
        "is_current_default": False,
        "is_preview": False,
        "requires_benchmark": True,
        "allowed_as_default": False,  # fast router is a role, not primary reasoner default
        "mutex_group": "heavy_llm",
        "notes": "Apple-native, memory-efficient, fast for routing decisions",
    },
    # ── Structured JSON Candidates ────────────────────────────────────────────
    "qwen3_0_6b": {
        "model_key": "qwen3_0_6b",
        "model_id": "mlx-community/Qwen3-0.6B-4bit",
        "role": STRUCTURED_JSON,
        "heavy": False,
        "m1_8gb_risk": "low",
        "is_current_default": False,
        "is_preview": False,
        "requires_benchmark": True,
        "allowed_as_default": True,  # small model, low RAM risk
        "mutex_group": "light_llm",
        "notes": "Small, fast structured JSON generation — M1 8GB safe",
    },
    "qwen3_1_7b": {
        "model_key": "qwen3_1_7b",
        "model_id": "mlx-community/Qwen3-1.7B-4bit",
        "role": STRUCTURED_JSON,
        "heavy": False,
        "m1_8gb_risk": "low_to_medium",
        "is_current_default": False,
        "is_preview": False,
        "requires_benchmark": True,
        "allowed_as_default": True,
        "mutex_group": "light_llm",
        "notes": "Better JSON quality than 0.6B, still M1 8GB safe",
    },
}


# ─── Dataclasses ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CandidateInfo:
    """Immutable view of an LLM candidate."""
    model_key: str
    model_id: str
    role: str
    heavy: bool
    m1_8gb_risk: str
    is_current_default: bool
    is_preview: bool
    requires_benchmark: bool
    allowed_as_default: bool
    mutex_group: Optional[str]
    notes: str

    @classmethod
    def from_dict(cls, data: dict) -> "CandidateInfo":
        return cls(
            model_key=data["model_key"],
            model_id=data["model_id"],
            role=data["role"],
            heavy=data["heavy"],
            m1_8gb_risk=data["m1_8gb_risk"],
            is_current_default=data["is_current_default"],
            is_preview=data["is_preview"],
            requires_benchmark=data["requires_benchmark"],
            allowed_as_default=data["allowed_as_default"],
            mutex_group=data.get("mutex_group"),
            notes=data.get("notes", ""),
        )


# ─── Env Var Names ─────────────────────────────────────────────────────────────

ENV_PRIMARY_REASONER = "HLEDAC_PRIMARY_REASONER"
ENV_FALLBACK_REASONER = "HLEDAC_FALLBACK_REASONER"
ENV_STRUCTURED_JSON_MODEL = "HLEDAC_STRUCTURED_JSON_MODEL"


# ─── Resolved Default (production baseline) ────────────────────────────────────
# Mirrors current HermesConfig.model_path default.
# DO NOT change — this sprint does NOT swap production models.


# ─── Validation Helpers ─────────────────────────────────────────────────────────

def _is_valid_key(key: str) -> bool:
    return key in LLM_CANDIDATES


def _warn_invalid(key: str, env_var: str, default: str) -> None:
    logger.warning(
        f"[F217B] Invalid {env_var}='{key}' — falling back to '{default}'. "
        f"Valid keys: {list(LLM_CANDIDATES.keys())}"
    )


# ─── Config Resolvers ─────────────────────────────────────────────────────────

def get_primary_reasoner_candidate(env_override: Optional[str] = None) -> CandidateInfo:
    """
    Resolve the primary reasoner candidate.

    Env var HLEDAC_PRIMARY_REASONER can override.
    Preview candidates (is_preview=True) require explicit env override to be allowed.
    Invalid env values fall back to current production baseline (hermes).
    """
    env_key = env_override or os.environ.get(ENV_PRIMARY_REASONER)
    fallback_key = "deephermes"  # production default (F217C swap)

    if env_key is not None:
        if not _is_valid_key(env_key):
            _warn_invalid(env_key, ENV_PRIMARY_REASONER, fallback_key)
        else:
            candidate = LLM_CANDIDATES[env_key]
            # Preview models need explicit override to be allowed as default
            if candidate.get("is_preview") and not candidate.get("allowed_as_default"):
                logger.warning(
                    f"[F217B] {ENV_PRIMARY_REASONER}='{env_key}' is a preview model "
                    f"and not allowed as default. Falling back to '{fallback_key}'. "
                    f"Set allowed_as_default=True in registry to override."
                )
            else:
                return CandidateInfo.from_dict(candidate)

    return CandidateInfo.from_dict(LLM_CANDIDATES[fallback_key])


def get_fallback_reasoner_candidate(env_override: Optional[str] = None) -> CandidateInfo:
    """
    Resolve the fallback reasoner candidate.

    Env var HLEDAC_FALLBACK_REASONER can override.
    Defaults to hermes (same as primary — same model, no exotic fallback).
    """
    env_key = env_override or os.environ.get(ENV_FALLBACK_REASONER)
    fallback_key = "hermes"  # fallback mirrors primary baseline

    if env_key is not None:
        if not _is_valid_key(env_key):
            _warn_invalid(env_key, ENV_FALLBACK_REASONER, fallback_key)
        else:
            return CandidateInfo.from_dict(LLM_CANDIDATES[env_key])

    return CandidateInfo.from_dict(LLM_CANDIDATES[fallback_key])


def get_structured_json_candidate(env_override: Optional[str] = None) -> CandidateInfo:
    """
    Resolve the structured JSON generation candidate.

    Env var HLEDAC_STRUCTURED_JSON_MODEL can override.
    Defaults to Qwen3-0.6B (current windup-local discovery behavior).
    """
    env_key = env_override or os.environ.get(ENV_STRUCTURED_JSON_MODEL)
    fallback_key = "qwen3_0_6b"  # mirrors current windup-local default

    if env_key is not None:
        if not _is_valid_key(env_key):
            _warn_invalid(env_key, ENV_STRUCTURED_JSON_MODEL, fallback_key)
        else:
            candidate = LLM_CANDIDATES[env_key]
            # Only allow candidates with role=STRUCTURED_JSON
            if candidate["role"] != STRUCTURED_JSON:
                logger.warning(
                    f"[F217B] {ENV_STRUCTURED_JSON_MODEL}='{env_key}' has role "
                    f"'{candidate['role']}', not 'structured_json'. Falling back to "
                    f"'{fallback_key}'."
                )
            else:
                return CandidateInfo.from_dict(candidate)

    return CandidateInfo.from_dict(LLM_CANDIDATES[fallback_key])


def resolve_llm_candidate(candidate_key: str) -> CandidateInfo:
    """
    Resolve any candidate by key.

    Returns CandidateInfo or raises ValueError if key is unknown.
    No env var lookups — explicit key resolution.
    """
    if not _is_valid_key(candidate_key):
        raise ValueError(
            f"Unknown LLM candidate key: '{candidate_key}'. "
            f"Valid keys: {list(LLM_CANDIDATES.keys())}"
        )
    return CandidateInfo.from_dict(LLM_CANDIDATES[candidate_key])


def list_llm_candidates(role: Optional[str] = None) -> list[CandidateInfo]:
    """
    List all candidates, optionally filtered by role.

    Args:
        role: If provided, only return candidates with this role.
              One of: PRIMARY_REASONER, STRUCTURED_JSON, FAST_ROUTER, FALLBACK_REASONER

    Returns:
        List of CandidateInfo objects.
    """
    candidates = list(LLM_CANDIDATES.values())
    if role is not None:
        candidates = [c for c in candidates if c["role"] == role]
    return [CandidateInfo.from_dict(c) for c in candidates]


# ─── Convenience Getters ────────────────────────────────────────────────────────

def get_current_production_default() -> CandidateInfo:
    """Return the current production default (deephermes)."""
    return CandidateInfo.from_dict(LLM_CANDIDATES["deephermes"])


def get_default_for_role(role: str) -> CandidateInfo:
    """
    Return the default candidate for a given role.

    Raises ValueError if role is unknown.
    """
    candidates = list_llm_candidates(role=role)
    if not candidates:
        raise ValueError(f"No candidates found for role: {role}")
    # Return the candidate that is_current_default for this role
    defaults = [c for c in candidates if c.is_current_default]
    if defaults:
        return defaults[0]
    # Fallback to first candidate for this role
    return candidates[0]