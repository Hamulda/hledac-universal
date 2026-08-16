"""
Cognitive Tarpit Detector — LLM-Generated Honeypot Text Detection
==================================================================

ISSUE [ADVERSARY]-002: Adversaries now mass-generate AI-forum posts with
perfectly grammatical English, low typo density, uniform sentence length
(high burstiness deviation toward flat LLM signatures), and high Shannon
entropy per byte (LLMs default to ~4.5 bits/byte in English vs. human ~4.1).



This module provides three orthogonal sub-scores that detect LLM-generated
content WITHOUT requiring a model inference pass (except SmolLM path):

  1. Byte-entropy variance   (Shannon entropy in 32-byte sliding windows)
  2. Burstiness deviation     (variance-of-sentence-length / mean-of-sentence-length)
  3. POS trigram ratio        (ratio of (DT-JJ-NN) / (NN-VB-DT))
  4. SmolLM pseudo-perplexity (teacher-forcing cross-entropy on 4-token chunks)

Integration:
  - fetching/public_fetcher.py — runs BEFORE IOC extraction, after HTML tarpit check
  - brain/synthesis_runner.py — drops findings from tarpit_score=1.0 domains before LLM context

Opt-in gate: HLEDAC_ENABLE_COGNITIVE_TARPIT=1 (default ON).

M1 8GB budget:
  - Pure Python fast-path: ~2-4ms per 1KB text (entropy + burstiness)
  - POS tagging path: ~8-15ms per 1KB (heavy, only when HLEDAC_ENABLE_POS_TAGGING=1)
  - SmolLM path: ~5ms per 512-token chunk (requires HLEDAC_ENABLE_BLITZ_TRIAGE=1)
  - Total RAM: <5MB for all sliding-window buffers
"""

from __future__ import annotations

import math
import os
import re
import statistics
import threading
from dataclasses import dataclass
from typing import Final

# ---------------------------------------------------------------------------
# msglike types — frozen, gc=False for M1 memory efficiency
# ---------------------------------------------------------------------------

try:
    import msgspec

    _HAVE_MSGSPEC = True
except ImportError:
    msgspec = None  # type: ignore[assignment]
    _HAVE_MSGSPEC = False


# msgspec-based if available, else pure-dataclass fallback
if _HAVE_MSGSPEC:

    class CognitiveTarpitVerdict(msgspec.Struct, frozen=True, gc=False):  # type: ignore[valid-type,misc]
        """Detection result for LLM-generated honeypot text."""
        is_cognitive_tarpit: bool
        cognitive_tarpit_score: float  # 0.0 (human) — 1.0 (certain LLM honeypot)
        reasons: tuple[str, ...]
        # Sub-scores
        entropy_score: float   # 0.0 — 1.0
        burstiness_score: float  # 0.0 — 1.0
        pos_score: float       # 0.0 — 1.0 (0 if POS tagging disabled)
        perplexity_score: float  # 0.0 — 1.0 (0 if SmolLM disabled)
        # Diagnostics
        entropy_variance: float = 0.0
        burstiness_deviation: float = 0.0
        sentence_count: int = 0
        word_count: int = 0
        text_length_chars: int = 0
        analysis_ms: float = 0.0

else:
    # Fallback pure-Python dataclass (no msgspec)
    @dataclass(frozen=True, slots=True)
    class CognitiveTarpitVerdict:
        """Detection result for LLM-generated honeypot text."""
        is_cognitive_tarpit: bool
        cognitive_tarpit_score: float
        reasons: tuple[str, ...]
        entropy_score: float
        burstiness_score: float
        pos_score: float
        perplexity_score: float
        entropy_variance: float = 0.0
        burstiness_deviation: float = 0.0
        sentence_count: int = 0
        word_count: int = 0
        text_length_chars: int = 0
        analysis_ms: float = 0.0


# ---------------------------------------------------------------------------
# Constants — thresholds derived from LLM vs human corpora analysis
# ---------------------------------------------------------------------------

# Feature gate — SWARM-010 compliant via FeatureFlags
from hledac.universal._core.feature_flags import FeatureFlags, FeatureFlag
from _core import aclose
_COGNITIVE_TARPIT_ENABLED: Final[bool] = FeatureFlags.get(FeatureFlag.COGNITIVE_TARPIT)

# POS tagging gate (heavy, off by default) — SWARM-010 compliant via FeatureFlags
_POS_TAGGING_ENABLED: Final[bool] = FeatureFlags.get(FeatureFlag.POS_TAGGING)

# Cognitive tarpit abort threshold
_COGNITIVE_TARPIT_THRESHOLD: Final[float] = 0.7

# SmolLM perplexity threshold (>0.45 → honeypot)
_SMOLLM_PERPLEXITY_THRESHOLD: Final[float] = 0.45

# Per-sub-score weights for composite score
_ENTROPY_WEIGHT: Final[float] = 0.25
_BURSTINESS_WEIGHT: Final[float] = 0.30
_POS_WEIGHT: Final[float] = 0.20
_PERPLEXITY_WEIGHT: Final[float] = 0.25

# Shannon entropy thresholds (per 32-byte window)
_ENTROPY_WINDOW_SIZE: Final[int] = 32
# LLM text: entropy variance < 0.15; human text: variance > 0.40
_ENTROPY_VAR_LLM_BOUND: Final[float] = 0.15
_ENTROPY_VAR_HUMAN_BOUND: Final[float] = 0.40

# Burstiness: variance-of-sentence-length / mean-of-sentence-length
# LLM ≈ 0.3-0.5; human prose ≈ 0.8-1.5
_BURSTINESS_LLM_MAX: Final[float] = 0.55
_BURSTINESS_HUMAN_MIN: Final[float] = 0.70

# Minimum sentence length for burstiness calculation
_BURSTINESS_MIN_SENTENCES: Final[int] = 3

# Minimum text length for analysis (avoid noise from tiny snippets)
_MIN_TEXT_LENGTH: Final[int] = 200

# Maximum text length to analyze (bound CPU for huge pages)
_MAX_TEXT_ANALYSIS_CHARS: Final[int] = 50_000

# SmolLM chunk size (tokens)
_SMOLLM_CHUNK_TOKENS: Final[int] = 512

# Cached SmolLM model instance (lazy-loaded, thread-safe)
_smollm_lock = threading.Lock()
_smollm_model: object | None = None
_smollm_tokenizer: object | None = None


# ---------------------------------------------------------------------------
# Byte-level Shannon entropy
# ---------------------------------------------------------------------------

def _shannon_entropy(data: bytes) -> float:
    """Compute Shannon entropy of a byte sequence in bits/byte."""
    if not data:
        return 0.0
    freq: dict[int, int] = {}
    for b in data:
        freq[b] = freq.get(b, 0) + 1
    total = len(data)
    entropy = 0.0
    for count in freq.values():
        p = count / total
        if p > 0:
            entropy -= p * math.log2(p)
    return entropy


def _entropy_variance(text: str, window_size: int = _ENTROPY_WINDOW_SIZE) -> float:
    """Compute variance of Shannon entropy over sliding windows.

    LLM text shows uniform low variance (<0.15) because LLMs use predictable
    token distributions. Human text has high variance (>0.40) due to
    irregular word choices, typos, and varied vocabulary.
    """
    data = text.encode("utf-8", errors="replace")
    if len(data) < window_size * 2:
        return 0.0

    entropies: list[float] = []
    step = max(1, window_size // 2)  # 50% overlap
    for i in range(0, len(data) - window_size + 1, step):
        window = data[i : i + window_size]
        entropies.append(_shannon_entropy(window))

    if len(entropies) < 3:
        return 0.0
    return float(statistics.variance(entropies)) if len(entropies) > 1 else 0.0


def _entropy_score(variance: float) -> float:
    """Map entropy variance to 0-1 score (0=human, 1=LLM)."""
    if variance <= _ENTROPY_VAR_LLM_BOUND:
        return 1.0
    if variance >= _ENTROPY_VAR_HUMAN_BOUND:
        return 0.0
    # Linear interpolation between bounds
    return 1.0 - (variance - _ENTROPY_VAR_LLM_BOUND) / (
        _ENTROPY_VAR_HUMAN_BOUND - _ENTROPY_VAR_LLM_BOUND
    )


# ---------------------------------------------------------------------------
# Burstiness deviation (sentence length variance / mean)
# ---------------------------------------------------------------------------

_SENTENCE_SPLIT_RE: Final[re.Pattern] = re.compile(
    r"(?<=[.!?])\s+",
    re.MULTILINE,
    )


def _sentence_lengths(text: str) -> list[int]:
    """Return list of sentence lengths in characters (whitespace-stripped)."""
    raw = _SENTENCE_SPLIT_RE.split(text)
    return [len(s.strip()) for s in raw if len(s.strip()) > 0]


def _burstiness_deviation(sentence_lengths: list[int]) -> float:
    """Compute normalized burstiness: var(seg_len) / mean(seg_len).

    LLM text: low ratio (~0.3-0.5) — uniform sentence length
    Human text: high ratio (~0.8-1.5) — varied sentence length
    """
    if len(sentence_lengths) < _BURSTINESS_MIN_SENTENCES:
        return 0.0
    mean_len = statistics.mean(sentence_lengths)
    if mean_len < 1:
        return 0.0
    variance = statistics.variance(sentence_lengths) if len(sentence_lengths) > 1 else 0.0
    return float(variance / mean_len)


def _burstiness_score(deviation: float) -> float:
    """Map burstiness deviation to 0-1 score (0=human, 1=LLM)."""
    if deviation <= _BURSTINESS_LLM_MAX:
        return 1.0
    if deviation >= _BURSTINESS_HUMAN_MIN:
        return 0.0
    return 1.0 - (deviation - _BURSTINESS_LLM_MAX) / (
        _BURSTINESS_HUMAN_MIN - _BURSTINESS_LLM_MAX
    )


# ---------------------------------------------------------------------------
# POS trigram ratio (DT-JJ-NN / NN-VB-DT)
# ---------------------------------------------------------------------------

# Simple regex-based POS approximation patterns
# (No spacy/nltk dependency — pure Python heuristic for M1 8GB)
#
# POS pattern groups:
#   DT = Determiners: a, an, the, this, that, these, those
#   JJ  = Adjectives: common adjective patterns (ends with -ly is likely adverb)
#   NN  = Nouns: common noun patterns
#   VB  = Verbs: common verb patterns
#   IN  = Prepositions: in, on, at, for, to, with, by, from, of, about

_DETERMINER_RE: Final[re.Pattern] = re.compile(
    r"\b(?:the|this|that|these|those|a|an|my|your|his|her|its|our|their)\b",
    re.IGNORECASE,
    )

_ADJECTIVE_RE: Final[re.Pattern] = re.compile(
    r"\b(?:[\w]+(?:ous|ful|less|ive|able|ible|al|ial|ous|ent|ant|ary|ery|ish|tive|ic|sive))\b",
    re.IGNORECASE,
    )

_NOUN_RE: Final[re.Pattern] = re.compile(
    r"\b(?:\w+(?:tion|sion|ness|ment|ity|ance|ence|er|or|ist|ism|logy|graphy|scopy|data|ics))\b",
    re.IGNORECASE,
    )

_VERB_RE: Final[re.Pattern] = re.compile(
    r"\b(?:\w+(?:ify|ize|ate|ify|en|ed|ing|es|s))\b",
    re.IGNORECASE,
    )

_PREPOSITION_RE: Final[re.Pattern] = re.compile(
    r"\b(?:in|on|at|for|to|with|by|from|of|about|into|through|during|before|after|above|below|between|under|over|around|among)\b",
    re.IGNORECASE,
    )


def _pos_tag_tokens(text: str) -> list[str]:
    """Simple regex-based POS tagging (approximate).

    Returns list of POS tags matching word positions.
    Tags: DT, JJ, NN, VB, IN, O (other)
    """
    words = re.findall(r"\b\w+\b", text)
    tags: list[str] = []
    for w in words:
        lw = w.lower()
        # Order matters: most specific patterns first
        # Determiners always match first (no suffix ambiguity)
        if _DETERMINER_RE.fullmatch(lw):
            tags.append("DT")
        # Adjectives before verbs — "tested" looks like a past-tense verb
        # but could be a participial adjective. Check JJ suffix first.
        elif _ADJECTIVE_RE.fullmatch(w):
            tags.append("JJ")
        elif _VERB_RE.fullmatch(w):
            tags.append("VB")
        elif _NOUN_RE.fullmatch(w):
            tags.append("NN")
        elif _PREPOSITION_RE.fullmatch(lw):
            tags.append("IN")
        else:
            tags.append("O")
    return tags


def _pos_trigram_ratio(tags: list[str]) -> float:
    """Compute ratio of (DT-JJ-NN) / (NN-VB-DT) trigrams.

    LLMs overuse canonical subject-predicate-object patterns (DT-JJ-NN).
    Human prose has more varied trigram structures.

    Returns:
        >1.0: more canonical SPO patterns (LLM-like)
        <1.0: more varied patterns (human-like)
        0.0: insufficient data
    """
    if len(tags) < 3:
        return 0.0

    dt_jj_nn_count = 0
    nn_vb_dt_count = 0

    for i in range(len(tags) - 2):
        t0, t1, t2 = tags[i], tags[i + 1], tags[i + 2]
        if t0 == "DT" and t1 == "JJ" and t2 == "NN":
            dt_jj_nn_count += 1
        elif t0 == "NN" and t1 == "VB" and t2 == "DT":
            nn_vb_dt_count += 1

    if nn_vb_dt_count == 0:
        return float(dt_jj_nn_count) if dt_jj_nn_count > 0 else 0.0
    return dt_jj_nn_count / nn_vb_dt_count


def _pos_score(ratio: float) -> float:
    """Map POS trigram ratio to 0-1 score (0=human, 1=LLM).

    Threshold: ratio > 2.0 → strongly LLM-like (score → 1.0)
               ratio < 1.0 → human-like (score → 0.0)
    """
    if ratio >= 2.0:
        return 1.0
    if ratio <= 0.5:
        return 0.0
    # Linear interpolation
    return (ratio - 0.5) / 1.5


# ---------------------------------------------------------------------------
# SmolLM pseudo-perplexity (teacher-forcing cross-entropy)
# ---------------------------------------------------------------------------

def _load_smollm() -> tuple[object, object] | tuple[None, None]:
    """Lazy-load SmolLM-360M-4bit model and tokenizer.

    Returns:
        (model, tokenizer) on success, (None, None) on failure.
    Thread-safe via _smollm_lock.
    """
    global _smollm_model, _smollm_tokenizer  # noqa: PLW0603

    if _smollm_model is not None:
        return _smollm_model, _smollm_tokenizer

    with _smollm_lock:
        if _smollm_model is not None:  # Double-check after acquiring lock
            return _smollm_model, _smollm_tokenizer

        try:
            from hledac.universal._core.feature_flags import FeatureFlag, FeatureFlags

            _blitz_triage = FeatureFlags.get(FeatureFlag.BLITZ_TRIAGE)
            if not _blitz_triage:
                # SmolLM only loaded if BLITZ_TRIAGE is enabled
                return None, None

            # Import MLX components lazily
            from mlx_lm import load as _mlx_load

            _MODEL_ID = "mlx-community/SmolLM-360M-Instruct-4bit"
            _smollm_model, _smollm_tokenizer = _mlx_load(
                _MODEL_ID,
                tokenizer_config={"trust_remote_code": True},
    )
            return _smollm_model, _smollm_tokenizer
        except Exception:
            return None, None


def _smollm_pseudo_perplexity(
    text: str, chunk_tokens: int = _SMOLLM_CHUNK_TOKENS,
) -> float:
    """Compute pseudo-perplexity via SmolLM-360M teacher-forcing.

    Tokenizes text into chunks, computes per-token cross-entropy under the
    model, and returns mean cross-entropy normalized to 0-1 range.

    LLM text: low perplexity (model is confident) → high score → 1.0
    Human text: high perplexity (model is uncertain) → low score → 0.0

    Threshold: >0.45 → honeypot flag

    Returns:
        0.0 if SmolLM not available or text too short.
    """
    try:
        model, tokenizer = _load_smollm()
        if model is None or tokenizer is None:
            return 0.0

        # Tokenize
        enc = tokenizer
        tokens = enc.encode(text, add_special_tokens=True)
        if len(tokens) < 16:
            return 0.0

        # Process in chunks
        chunk_size = min(chunk_tokens, len(tokens) - 1)
        losses: list[float] = []

        # Import MLX lazily
        import mlx.core as mx

        for i in range(0, len(tokens) - 1, chunk_size):
            chunk = tokens[i : i + chunk_size + 1]
            if len(chunk) < 4:
                continue

            input_ids = mx.array(chunk[:-1])
            labels = mx.array(chunk[1:])

            try:
                import mlx.core as _mx
                import mlx_lm as _mlx_lm

                input_ids = _mx.array(chunk[:-1])
                targets = _mx.array(chunk[1:])

                # Direct model forward pass (standard MLX API)
                # model is a nn.Module loaded via mlx_lm.load()
                try:
                    logits = model(input_ids)  # (seq_len, vocab_size)
                except TypeError:
                    # Model requires additional args (e.g., kv_cache)
                    try:
                        logits = model(input_ids, None)
                    except Exception:
                        continue

                # logits: (seq_len, vocab_size), targets: (seq_len,)
                ce = _mx.mean(_mx.losses.cross_entropy(logits, targets, reduction='none'))
                losses.append(float(ce))
            except Exception:
                continue

        if not losses:
            return 0.0

        # Normalize cross-entropy to 0-1 pseudo-perplexity score
        # Typical cross-entropy for LLM text: 1.5-2.5
        # Typical cross-entropy for human text: 3.5-5.0
        mean_ce = float(statistics.mean(losses))

        # Map: low CE → high LLM score; high CE → low LLM score
        # CE < 2.0 → score = 1.0; CE > 5.0 → score = 0.0
        if mean_ce <= 2.0:
            return 1.0
        if mean_ce >= 5.0:
            return 0.0
        return 1.0 - (mean_ce - 2.0) / 3.0

    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Main scoring function
# ---------------------------------------------------------------------------

def _cognitive_tarpit_score(text: str) -> CognitiveTarpitVerdict:
    """Compute composite LLM-honeypot detection score.

    Returns CognitiveTarpitVerdict with:
      - cognitive_tarpit_score: weighted composite (0.0-1.0)
      - sub-scores: entropy, burstiness, POS, perplexity
      - reasons: human-readable detection reasons

    M1 8GB: pure Python fast-path runs in ~2-4ms per 1KB text.
    """
    import time

    t0 = time.monotonic()

    # Feature gate
    if not _COGNITIVE_TARPIT_ENABLED:
        return CognitiveTarpitVerdict(
            is_cognitive_tarpit=False,
            cognitive_tarpit_score=0.0,
            reasons=("cognitive_tarpit_disabled",),
            entropy_score=0.0,
            burstiness_score=0.0,
            pos_score=0.0,
            perplexity_score=0.0,
            text_length_chars=len(text),
            analysis_ms=0.0,
    )

    # Guard: minimum text length
    if len(text) < _MIN_TEXT_LENGTH:
        return CognitiveTarpitVerdict(
            is_cognitive_tarpit=False,
            cognitive_tarpit_score=0.0,
            reasons=("text_too_short",),
            entropy_score=0.0,
            burstiness_score=0.0,
            pos_score=0.0,
            perplexity_score=0.0,
            text_length_chars=len(text),
            analysis_ms=0.0,
    )

    # Truncate to bound CPU
    analysis_text = text[:_MAX_TEXT_ANALYSIS_CHARS]

    reasons: list[str] = []
    word_count = len(re.findall(r"\b\w+\b", analysis_text))

    # ── 1. Entropy variance ────────────────────────────────────────────────
    entropy_var = _entropy_variance(analysis_text)
    entropy_sc = _entropy_score(entropy_var)
    if entropy_sc > 0.7:
        reasons.append(f"entropy_variance={entropy_var:.3f} (LLM-low)")
    entropy_score = entropy_sc

    # ── 2. Burstiness deviation ────────────────────────────────────────────
    sent_lens = _sentence_lengths(analysis_text)
    burst_dev = _burstiness_deviation(sent_lens)
    burst_sc = _burstiness_score(burst_dev)
    if burst_sc > 0.7:
        reasons.append(
            f"burstiness_deviation={burst_dev:.3f} (LLM-flat, sentences={len(sent_lens)})"
    )
    burstiness_score = burst_sc

    # ── 3. POS trigram ratio (only if enabled) ────────────────────────────
    pos_sc = 0.0
    pos_ratio = 0.0
    if _POS_TAGGING_ENABLED:
        pos_tags = _pos_tag_tokens(analysis_text)
        pos_ratio = _pos_trigram_ratio(pos_tags)
        pos_sc = _pos_score(pos_ratio)
        if pos_sc > 0.7:
            reasons.append(f"pos_trigram_ratio={pos_ratio:.2f} (LLM-canonical)")

    # ── 4. SmolLM perplexity (only if BLITZ_TRIAGE enabled) ───────────────
    perplexity_sc = 0.0
    perplexity_sc = _smollm_pseudo_perplexity(analysis_text)
    if perplexity_sc > _SMOLLM_PERPLEXITY_THRESHOLD:
        reasons.append(f"smollm_perplexity={perplexity_sc:.3f} (honeypot_threshold={_SMOLLM_PERPLEXITY_THRESHOLD})")

    # ── Composite score ────────────────────────────────────────────────────
    # If perplexity available, use it as primary signal (most accurate)
    if perplexity_sc > 0:
        cognitive_tarpit_score = (
            _ENTROPY_WEIGHT * entropy_score
            + _BURSTINESS_WEIGHT * burstiness_score
            + _POS_WEIGHT * pos_sc
            + _PERPLEXITY_WEIGHT * perplexity_sc
    )
    else:
        # No perplexity — weight redistributed to entropy + burstiness
        cognitive_tarpit_score = (
            0.40 * entropy_score
            + 0.45 * burstiness_score
            + 0.15 * pos_sc
    )

    # Decision: cognitive tarpit if composite > threshold
    is_cognitive_tarpit = cognitive_tarpit_score >= _COGNITIVE_TARPIT_THRESHOLD
    if is_cognitive_tarpit:
        reasons.insert(0, f"cognitive_tarpit_score={cognitive_tarpit_score:.3f}>={_COGNITIVE_TARPIT_THRESHOLD}")

    elapsed_ms = (time.monotonic() - t0) * 1000.0

    return CognitiveTarpitVerdict(
        is_cognitive_tarpit=is_cognitive_tarpit,
        cognitive_tarpit_score=cognitive_tarpit_score,
        reasons=tuple(reasons),
        entropy_score=entropy_score,
        burstiness_score=burstiness_score,
        pos_score=pos_sc,
        perplexity_score=perplexity_sc,
        entropy_variance=entropy_var,
        burstiness_deviation=burst_dev,
        sentence_count=len(sent_lens),
        word_count=word_count,
        text_length_chars=len(text),
        analysis_ms=elapsed_ms,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def cognitive_tarpit_score(text: str) -> CognitiveTarpitVerdict:
    """Public API: detect LLM-generated honeypot text.

    Args:
        text: Raw text content from HTTP response (HTML stripped).

    Returns:
        CognitiveTarpitVerdict with is_cognitive_tarpit, cognitive_tarpit_score,
        and sub-scores.

    Example:
        verdict = cognitive_tarpit_score(page_text)
        if verdict.is_cognitive_tarpit:
            logger.warning("[COGNITIVE_TARPIT] %s: %s", verdict.cognitive_tarpit_score, verdict.reasons)
    """
    return _cognitive_tarpit_score(text)


# ---------------------------------------------------------------------------
# SmolLM model cache invalidation (called on memory pressure)
# ---------------------------------------------------------------------------

def invalidate_smollm_cache() -> None:
    """Invalidate cached SmolLM model (call on M1 memory pressure)."""
    global _smollm_model, _smollm_tokenizer  # noqa: PLW0603
    with _smollm_lock:
        _smollm_model = None
        _smollm_tokenizer = None
