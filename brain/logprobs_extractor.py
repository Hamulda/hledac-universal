"""
Token-level uncertainty extraction from MLX inference via logits_processors.

ISSUE APEX-1008: DeepHermes3Engine uses mlx_lm.generate() but doesn't extract

NOTE (MODERN-35): Callers MUST set P-core affinity before mlx_lm.generate() when
using logits_processors from this module. Import utils.cpu_affinity and call:
    from hledac.universal.utils.cpu_affinity import set_mlx_affinity, is_apple_silicon
    if is_apple_silicon():
        set_mlx_affinity()
    output = mlx_lm.generate(..., logits_processors=[processor])
See brain/deephermes3_engine.py for example implementation.




logprobs. This module captures logits during generation and computes per-token
entropy for downstream entity-level uncertainty aggregation.

Architecture:
    LogitsCaptureProcessor (logits_processor for mlx_lm)
        - Captures top-k logits per token (zero-copy on GPU)
        - Computes per-token entropy: H = -sum(p * log2(p))

    TokenUncertaintyCollector
        - Aggregates token entropies across generation
        - Maps token positions to character offsets for entity extraction
        - Provides entity-level uncertainty: avg entropy over entity token span

M1 8GB invariants:
    - Top-k=5 logits only (not full vocab) - ~20 bytes per token vs 128KB
    - mx.log_softmax() on GPU - zero-copy, no CPU transfer
    - Sliding window of 512 tokens max - bounded memory
    - Lazy import of mlx.core - no module-level GPU allocation

Usage:
    collector = TokenUncertaintyCollector(top_k=5)
    logits_processor = collector.create_logits_processor()

    # Pass to mlx_lm.generate()
    output = mlx_lm.generate(
        model, tokenizer, prompt=prompt,
        logits_processors=[logits_processor]
    )

    # After generation, get entity uncertainty
    uncertainty = collector.get_entity_uncertainty(
        entity_text="192.168.1.1",
        generated_text=output
    )
    # Returns: {"avg_entropy": 1.23, "confidence": "high", "uncertainty_flag": "normal"}
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any
from _core import aclose

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Entropy threshold (bits) above which an entity is flagged as low-confidence.
# H=1.5 bits ~ top-2 tokens are close (e.g. 0.65/0.35 split).
# For reference: H=0 -> deterministic, H=log2(5)~2.32 -> uniform over top-5.
ENTROPY_HIGH_THRESHOLD_BITS: float = 1.5
ENTROPY_MEDIUM_THRESHOLD_BITS: float = 0.8

# Maximum tokens to track in the sliding window (M1 8GB bounded).
MAX_TOKEN_WINDOW: int = 512

# Default top-k for logit capture.
DEFAULT_TOP_K: int = 5


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class TokenEntropy:
    """Per-token entropy record."""
    token_id: int
    text: str
    entropy_bits: float
    top_k_logprobs: list[tuple[int, float]]  # (token_id, log_prob) pairs
    position: int  # token position in generation (0-indexed)


@dataclass(slots=True)
class EntityUncertainty:
    """Entity-level uncertainty aggregation result."""
    entity_text: str
    avg_entropy_bits: float
    max_entropy_bits: float
    token_count: int
    confidence: str  # "high", "medium", "low"
    uncertainty_flag: str  # "normal", "elevated", "high_entropy"
    token_entropies: list[TokenEntropy] = field(default_factory=list)


# ---------------------------------------------------------------------------
# LogitsCaptureProcessor - mlx_lm logits_processor callback
# ---------------------------------------------------------------------------

class LogitsCaptureProcessor:
    """
    __slots__ = (
        '_max_window',
        '_position',
        '_tokenizer',
        '_top_k',
    )

    mlx_lm-compatible logits processor that captures top-k logits per token.

    Implements the logits_processor protocol: __call__(token_ids, logits) -> logits.
    Computes entropy on GPU via mx.log_softmax, then stores top-k on CPU.

    Thread-safety: NOT thread-safe. One instance per generation call.
    """

    __slots__ = (
        '_top_k',
        '_max_window',
    )

    def __init__(self, top_k: int = DEFAULT_TOP_K, max_window: int = MAX_TOKEN_WINDOW) -> None:
        self._top_k = top_k
        self._max_window = max_window
        self._token_entropies: list[TokenEntropy] = []
        self._generated_texts: list[str] = []
        self._position: int = 0
        self._tokenizer: Any = None  # set externally for text decoding

    @property
    def token_entropies(self) -> list[TokenEntropy]:
        return self._token_entropies

    @property
    def generated_texts(self) -> list[str]:
        return self._generated_texts

    def set_tokenizer(self, tokenizer: Any) -> None:
        """Attach tokenizer for token->text decoding."""
        self._tokenizer = tokenizer

    def __call__(self, token_ids: list[int], logits: Any) -> Any:
        """
        mlx_lm logits_processor callback.

        Args:
            token_ids: All token IDs generated so far (including prompt).
            logits: MLX array of shape [vocab_size] - raw logits for next token.

        Returns:
            logits unchanged (pass-through - we observe, don't modify).
        """
        try:
            import mlx.core as mx

            # GPU-native: log_softmax over full vocab, then top-k
            log_probs = mx.log_softmax(logits)

            # Top-k by log_prob (highest = most likely)
            # mx.topk returns (values, indices) - both on GPU
            top_k_vals, top_k_idx = mx.topk(log_probs, k=self._top_k)

            # Transfer only top-k to CPU (tiny: 5 floats + 5 ints)
            top_k_logprobs_cpu = top_k_vals.tolist()
            top_k_indices_cpu = top_k_idx.tolist()

            # Compute entropy: H = -sum(p * log2(p)) where p = exp(log_prob)
            # Use log2 change of base: log2(p) = log_prob / ln(2)
            entropy_bits = 0.0
            for lp in top_k_logprobs_cpu:
                p = math.exp(lp)
                if p > 1e-10:
                    entropy_bits -= p * (lp / math.ln(2))

            # Decode the selected token to text
            # The selected token is the one with highest log_prob (index 0 after topk)
            selected_token_id = top_k_indices_cpu[0] if top_k_indices_cpu else 0
            text = ""
            if self._tokenizer is not None:
                try:
                    text = self._tokenizer.decode([selected_token_id])
                except Exception:
                    text = ""

            # Store record (sliding window)
            record = TokenEntropy(
                token_id=selected_token_id,
                text=text,
                entropy_bits=round(entropy_bits, 4),
                top_k_logprobs=list(zip(top_k_indices_cpu, [round(lp, 4) for lp in top_k_logprobs_cpu])),
                position=self._position,
    )
            self._token_entropies.append(record)
            self._generated_texts.append(text)
            self._position += 1

            # Enforce sliding window (M1 8GB bounded memory)
            if len(self._token_entropies) > self._max_window:
                self._token_entropies = self._token_entropies[-self._max_window:]
                self._generated_texts = self._generated_texts[-self._max_window:]

        except Exception as e:
            # Non-fatal: logprobs are optional enhancement
            logger.debug("[LOGPROBS] capture skipped: %s", e)

        # Pass-through: return logits unchanged
        return logits

    def reset(self) -> None:
        """Clear collected data for reuse."""
        self._token_entropies.clear()
        self._generated_texts.clear()
        self._position = 0


# ---------------------------------------------------------------------------
# TokenUncertaintyCollector - high-level API
# ---------------------------------------------------------------------------

class TokenUncertaintyCollector:
    """
    __slots__ = (
        '_max_window',
        '_processor',
        '_top_k',
    )

    High-level API for token-level uncertainty collection and entity aggregation.

    Wraps LogitsCaptureProcessor and provides entity-level uncertainty lookup.

    Usage:
        collector = TokenUncertaintyCollector()
        processor = collector.create_logits_processor(tokenizer)

        output = mlx_lm.generate(
            model, tokenizer, prompt=prompt,
            logits_processors=[processor]
    )

        # Entity uncertainty
        result = collector.get_entity_uncertainty("192.168.1.1", output)
    """

    __slots__ = (
        '_top_k',
        '_max_window',
    )

    def __init__(self, top_k: int = DEFAULT_TOP_K, max_window: int = MAX_TOKEN_WINDOW) -> None:
        self._top_k = top_k
        self._max_window = max_window
        self._processor: LogitsCaptureProcessor | None = None

    def create_logits_processor(self, tokenizer: Any = None) -> LogitsCaptureProcessor:
        """Create and return a fresh LogitsCaptureProcessor."""
        self._processor = LogitsCaptureProcessor(
            top_k=self._top_k,
            max_window=self._max_window,
    )
        if tokenizer is not None:
            self._processor.set_tokenizer(tokenizer)
        return self._processor

    @property
    def processor(self) -> LogitsCaptureProcessor | None:
        return self._processor

    def get_entity_uncertainty(
        self,
        entity_text: str,
        generated_text: str,
    ) -> EntityUncertainty | None:
        """
        Compute entity-level uncertainty by averaging token entropies
        over the character span where entity_text appears in generated_text.

        Args:
            entity_text: The IOC entity string to look up (e.g. "192.168.1.1").
            generated_text: The full generated text (to locate entity position).

        Returns:
            EntityUncertainty with avg/max entropy, confidence label, and flag.
            None if entity not found or no token data available.
        """
        if self._processor is None or not self._processor.token_entropies:
            return None

        # Find entity position in generated text
        concat_text = "".join(self._processor.generated_texts)
        entity_start = concat_text.find(entity_text)
        if entity_start == -1:
            # Try in the provided generated_text as fallback
            entity_start = generated_text.find(entity_text)
            if entity_start == -1:
                return None

        entity_end = entity_start + len(entity_text)

        # Map character offsets to token positions
        char_offset = 0
        matching_entropies: list[TokenEntropy] = []
        for te in self._processor.token_entropies:
            token_start = char_offset
            token_end = char_offset + len(te.text)
            char_offset = token_end

            # Check if this token overlaps with entity span
            if token_end <= entity_start:
                continue
            if token_start >= entity_end:
                break
            matching_entropies.append(te)

        if not matching_entropies:
            return None

        # Aggregate
        entropies = [te.entropy_bits for te in matching_entropies]
        avg_entropy = sum(entropies) / len(entropies)
        max_entropy = max(entropies)

        # Classify
        if avg_entropy > ENTROPY_HIGH_THRESHOLD_BITS:
            confidence = "low"
            uncertainty_flag = "high_entropy"
        elif avg_entropy > ENTROPY_MEDIUM_THRESHOLD_BITS:
            confidence = "medium"
            uncertainty_flag = "elevated"
        else:
            confidence = "high"
            uncertainty_flag = "normal"

        return EntityUncertainty(
            entity_text=entity_text,
            avg_entropy_bits=round(avg_entropy, 4),
            max_entropy_bits=round(max_entropy, 4),
            token_count=len(matching_entropies),
            confidence=confidence,
            uncertainty_flag=uncertainty_flag,
            token_entropies=matching_entropies,
    )

    def get_all_entity_uncertainties(
        self,
        entity_texts: list[str],
        generated_text: str = "",
    ) -> dict[str, EntityUncertainty]:
        """
        Batch uncertainty lookup for multiple entities.

        Returns:
            Dict mapping entity_text -> EntityUncertainty.
            Entities not found in token stream are omitted.
        """
        results: dict[str, EntityUncertainty] = {}
        for entity in entity_texts:
            result = self.get_entity_uncertainty(entity, generated_text)
            if result is not None:
                results[entity] = result
        return results
