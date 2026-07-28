"""
brain/_dspy_shared.py — Shared DSPy Infrastructure
==================================================
Eliminates prompt template and _load_programs duplication between:
  - brain/dspy_service.py
  - hledac_hypothesis/hypothesisgenerator.py
  - brain/dspy_programs.py

Single source of truth for:
  - DSPy enablement flag (HLEDAC_ENABLE_DSPY)
  - Cache path (~/.hledac/dspy_cache.json)
  - Program lazy-loader (load_programs)
  - Batch scoring concurrency constants (M1 8GB bounded)

Usage:
    from hledac.universal.brain._dspy_shared import load_programs, ENABLED, CACHE_PATH
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DSPy enablement (single source of truth)
# ---------------------------------------------------------------------------
ENABLED = os.getenv('HLEDAC_ENABLE_DSPY', '0') == '1'

# ---------------------------------------------------------------------------
# Cache path (single source of truth)
# ---------------------------------------------------------------------------
CACHE_PATH = Path.home() / '.hledac' / 'dspy_cache.json'

# ---------------------------------------------------------------------------
# Batch scoring concurrency (M1 8GB bounded — DSPy is CPU-light, memory-light)
# ---------------------------------------------------------------------------
_SCORING_CONCURRENCY = 5  # max concurrent DSPy scoring calls
_SORING_BATCH_SIZE = 20   # findings per DSPy call (prompt token budget)

# Expose for external consumers
SCORING_CONCURRENCY = _SCORING_CONCURRENCY
SCORING_BATCH_SIZE = _SORING_BATCH_SIZE

# ---------------------------------------------------------------------------
# Program cache (lazy-loaded, process-global)
# ---------------------------------------------------------------------------
_programs: dict[str, str] = {}
_programs_loaded: bool = False


def load_programs() -> dict[str, str]:
    """
    Lazy-load compiled DSPy programs from cache.

    Single implementation used by:
      - dspy_service.py (expand_query, score_findings, suggest_pivots)
      - hypothesisgenerator.py (_load_dspy_program → via brain.dspy_programs)

    Returns dict of {task_key: prompt_template_string}.
    """
    global _programs, _programs_loaded
    if _programs_loaded:
        return _programs
    _programs_loaded = True

    if not CACHE_PATH.exists():
        logger.warning('dspy_shared: cache not found at %s', CACHE_PATH)
        return {}

    try:
        try:
            import orjson
            with open(CACHE_PATH, 'rb') as f:
                data = orjson.loads(f.read())
        except Exception:
            import json as _stdlib_json
            with open(CACHE_PATH) as f:
                data = _stdlib_json.load(f)

        prompts = data.get('prompts', {})
        _programs = {k: v for k, v in prompts.items() if v and isinstance(v, str)}
        logger.info('dspy_shared: loaded %d compiled programs from cache', len(_programs))
    except Exception as e:
        logger.warning('dspy_shared: failed to load cache: %s', e)
        _programs = {}

    return _programs


def is_dspy_available() -> bool:
    """Check if DSPy is available and enabled."""
    if not ENABLED:
        return False
    try:
        import dspy as _dspy
        return _dspy is not None
    except ImportError:
        return False
