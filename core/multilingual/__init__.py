"""
Multilingual embedding support for cross-lingual threat intelligence.

This module provides:
- Language detection (FastText, langdetect, script-based)
- BGE-M3 multilingual embeddings (100+ languages)
- Matryoshka Representation Learning (MRL) for dimension truncation
- Dual-index architecture (English 256d + Multilingual 256d)

Author: Hledac Team
Issue: [SWARM]-002
"""
from __future__ import annotations

from .lang_detector import (
    LangDetector,
    LanguageDetectionResult,
    ScriptType,
    detect_language,
    get_lang_detector,
)
from .mrl import (
    MRLTruncator,
    MRL_DIMENSIONS,
    truncate_embedding,
    truncate_batch,
)
from .bge_m3_embedder import (
    BGEM3Embedder,
    BGEBackend,
    BGEConfig,
    get_bge_m3_embedder,
    NATIVE_DIM,
    MRL_TARGET_DIM,
)

__all__ = [
    # Language detection
    'LangDetector',
    'LanguageDetectionResult',
    'ScriptType',
    'detect_language',
    'get_lang_detector',
    # MRL truncation
    'MRLTruncator',
    'MRL_DIMENSIONS',
    'truncate_embedding',
    'truncate_batch',
    # BGE-M3
    'BGEM3Embedder',
    'BGEBackend',
    'BGEConfig',
    'get_bge_m3_embedder',
    'NATIVE_DIM',
    'MRL_TARGET_DIM',
]
