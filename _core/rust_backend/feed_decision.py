# feed_decision.py — Feed decision classifiers domain
"""
Rust-backed feed signal classification — pure functions, no I/O.


feed_decision_classify: classify fallback decision outcome (should_fetch, wasted, helpful).
feed_stage_diagnose: diagnose which pipeline stage lost the signal.
feed_branch_hint: sprint hint about feed branch quality.
feed_economics_verdict: condensed economics verdict for the run.
feed_branch_verdict: rich dict verdict for feed branch economics.

Called 1000+ times per sprint — Rust gives ~10x speedup over Python.

M1 8GB: all functions are pure computation, no GIL contention.
"""

from __future__ import annotations

from typing import Any


def get_domain() -> FeedDecisionDomain:
    from hledac.universal.rust_extensions import hledac_rust_extensions as _ext

    _probe = getattr(_ext, "feed_decision_classify", None)
    if _probe is None:
        msg = "hledac_rust_extensions.feed_decision_classify not available"
        raise ImportError(msg)
    return FeedDecisionDomain(_ext)


class FeedDecisionDomain:
    """Rust-backed feed decision classification — pure FSM functions."""

    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
        self._ext = ext

    def classify(
        self,
        assembled_text_len: int,
        pre_fallback_hits_count: int,
        quality_band: str,
        metadata_boost: bool,
        language_mismatch: bool,
        article_fallback_used: bool,
        article_fallback_attempted: bool,
        post_fallback_findings_count: int,
        adapter_source_priority_bias: float,
        adapter_metadata_richness_band: str,
    ) -> tuple[str, bool, bool, bool, bool, str]:
        """Classify fallback decision outcome.

        Returns:
            (reason, should_fetch, forced, wasted, helpful, skip_because)
        """
        return self._ext.feed_decision_classify(
            assembled_text_len,
            pre_fallback_hits_count,
            quality_band,
            metadata_boost,
            language_mismatch,
            article_fallback_used,
            article_fallback_attempted,
            post_fallback_findings_count,
            adapter_source_priority_bias,
            adapter_metadata_richness_band,
        )

    def stage_diagnose(
        self,
        entries_seen: int,
        entries_with_empty_assembled_text: int,
        entries_scanned: int,
        entries_with_hits: int,
        findings_built_pre_store: int,
        patterns_configured: int,
        findings_lost_to_dedup_total: int,
    ) -> str:
        """Diagnose which stage the signal is lost at.

        Returns:
            One of: empty_registry, empty_fetch, content_empty,
            no_pattern_hits, findings_build_loss, no_pattern_hits_with_content,
            prestore_findings_present, unknown
        """
        return self._ext.feed_stage_diagnose(
            entries_seen,
            entries_with_empty_assembled_text,
            entries_scanned,
            entries_with_hits,
            findings_built_pre_store,
            patterns_configured,
            findings_lost_to_dedup_total,
        )

    def branch_hint(
        self,
        feed_signal_present: bool,
        fallback_useful: int,
        fallback_waste: int,
        findings_rich: int,
        findings_fallback: int,
        entries_with_hits: int,
    ) -> str:
        """Compute a hint for next sprint about feed branch quality.

        Returns:
            One of: unknown, feed_strong, feed_weak, fallback_valuable, mixed
        """
        return self._ext.feed_branch_hint(
            feed_signal_present,
            fallback_useful,
            fallback_waste,
            findings_rich,
            findings_fallback,
            entries_with_hits,
        )

    def economics_verdict(
        self,
        feed_signal_present: bool,
        fallback_useful: int,
        fallback_waste: int,
        findings_rich: int,
        findings_fallback: int,
    ) -> tuple[str, int, int, int, int]:
        """Compute condensed economics verdict for the run.

        Returns:
            (verdict_tag, feed_signal_present, fallback_useful, fallback_waste, quality)
        """
        return self._ext.feed_economics_verdict(
            feed_signal_present,
            fallback_useful,
            fallback_waste,
            findings_rich,
            findings_fallback,
        )

    def branch_verdict(
        self,
        feed_signal_present: bool,
        fallback_useful: int,
        fallback_waste: int,
        findings_rich: int,
        findings_fallback: int,
        squandered_high_usefulness: int,
        metadata_strong_but_content_weak: int,
        low_trust_feed_hits: int,
        total_entries_with_hits: int,
        entries_seen: int,
        feed_native_yield_ratio: float,
        fallback_value_ratio: float,
    ) -> dict[str, Any]:
        """Compute rich dict-style verdict for feed branch economics.

        Returns:
            dict with keys: verdict_tag, feed_native_yield, fallback_yield,
            total_yield, squandered_high_usefulness_entries, unnecessary_fallbacks,
            useful_fallbacks, feed_corroborates, feed_burns_budget,
            feed_next_action, feed_confidence_note, feed_confidence_score,
            feed_native_yield_ratio, fallback_value_ratio,
            high_usefulness_waste_rate, metadata_strong_content_weak,
            low_trust_feed_hits, entries_with_hits, entries_seen
        """
        # Rust returns PyDict directly (2026-07-26 optimization).
        # Python fallback still returns dict from Python implementation.
        return self._ext.feed_branch_verdict(
            feed_signal_present,
            fallback_useful,
            fallback_waste,
            findings_rich,
            findings_fallback,
            squandered_high_usefulness,
            metadata_strong_but_content_weak,
            low_trust_feed_hits,
            total_entries_with_hits,
            entries_seen,
            feed_native_yield_ratio,
            fallback_value_ratio,
        )


class PythonFallbackFeedDecisionDomain:
    """Pure-Python fallback for feed decision classification."""

    __slots__ = ()

    MIN_ARTICLE_FALLBACK_CHARS = 150

    def classify(
        self,
        assembled_text_len: int,
        pre_fallback_hits_count: int,
        quality_band: str,
        metadata_boost: bool,
        language_mismatch: bool,
        _article_fallback_used: bool,
        article_fallback_attempted: bool,
        post_fallback_findings_count: int,
        adapter_source_priority_bias: float,
        adapter_metadata_richness_band: str,
    ) -> tuple[str, bool, bool, bool, bool, str]:
        if pre_fallback_hits_count > 0:
            return ("feed_native_had_signal", False, False, True, False, "feed-native already carried hits")

        if not article_fallback_attempted:
            if assembled_text_len >= self.MIN_ARTICLE_FALLBACK_CHARS and quality_band in ("high", "medium"):
                return (
                    "skipped_high_quality",
                    False,
                    False,
                    False,
                    False,
                    f"high quality ({quality_band}), assembled {assembled_text_len} chars",
                )
            if adapter_source_priority_bias >= 0.1 and assembled_text_len >= self.MIN_ARTICLE_FALLBACK_CHARS:
                return (
                    "skipped_adapter_bias",
                    False,
                    False,
                    False,
                    False,
                    f"adapter source_priority_bias={adapter_source_priority_bias:.2f}",
                )
            return (
                "no_fetch_warranted",
                False,
                False,
                False,
                False,
                f"assembled={assembled_text_len}, quality={quality_band}",
            )

        if metadata_boost and not language_mismatch and assembled_text_len < self.MIN_ARTICLE_FALLBACK_CHARS:
            if post_fallback_findings_count > 0:
                return ("forced_metadata_mismatch", True, True, False, True, "")
            return ("forced_no_yield", True, True, True, False, "")

        if assembled_text_len >= self.MIN_ARTICLE_FALLBACK_CHARS and quality_band == "low":
            if post_fallback_findings_count > 0:
                return ("aged_structured_yield", True, True, False, True, "")
            return ("aged_structured_no_yield", True, True, True, False, "")

        if adapter_metadata_richness_band == "high" and assembled_text_len < self.MIN_ARTICLE_FALLBACK_CHARS:
            if post_fallback_findings_count > 0:
                return ("forced_adapter_metadata", True, True, False, True, "")
            return ("forced_adapter_no_yield", True, True, True, False, "")

        if post_fallback_findings_count > 0:
            return ("normal_fallback_yield", True, False, False, True, "")
        return ("normal_fallback_no_yield", True, False, False, False, "")

    def stage_diagnose(
        self,
        entries_seen: int,
        entries_with_empty_assembled_text: int,
        entries_scanned: int,
        entries_with_hits: int,
        findings_built_pre_store: int,
        patterns_configured: int,
        findings_lost_to_dedup_total: int,
    ) -> str:
        if patterns_configured == 0:
            return "empty_registry"
        if entries_seen == 0:
            return "empty_fetch"
        if entries_with_empty_assembled_text > 0 and entries_scanned == 0:
            return "content_empty"
        if entries_scanned == 0:
            return "no_pattern_hits"
        if findings_built_pre_store == 0 and findings_lost_to_dedup_total > 0:
            return "findings_build_loss"
        if entries_with_hits == 0:
            return "no_pattern_hits_with_content"
        if findings_built_pre_store > 0:
            return "prestore_findings_present"
        return "unknown"

    def branch_hint(
        self,
        feed_signal_present: bool,
        fallback_useful: int,
        fallback_waste: int,
        _findings_rich: int,
        _findings_fallback: int,
        entries_with_hits: int,
    ) -> str:
        if entries_with_hits == 0:
            return "unknown"
        if feed_signal_present and fallback_waste == 0:
            return "feed_strong"
        if feed_signal_present and fallback_waste > 0 and fallback_useful == 0:
            return "feed_weak"
        if fallback_useful > 0 and _findings_fallback > 0:
            return "fallback_valuable"
        if feed_signal_present or fallback_useful > 0:
            return "mixed"
        return "unknown"

    def economics_verdict(
        self,
        feed_signal_present: bool,
        fallback_useful: int,
        fallback_waste: int,
        findings_rich: int,
        findings_fallback: int,
    ) -> tuple[str, int, int, int, int]:
        total_findings = findings_rich + findings_fallback
        if total_findings == 0:
            return ("no_signal", int(feed_signal_present), fallback_useful, fallback_waste, 0)

        rich_ratio = findings_rich / total_findings if total_findings > 0 else 0.0
        waste_ratio = (
            fallback_waste / (fallback_useful + fallback_waste) if (fallback_useful + fallback_waste) > 0 else 0.0
        )

        if rich_ratio >= 0.7:
            verdict_tag = "feed_lean"
        elif rich_ratio <= 0.3:
            verdict_tag = "fallback_lean"
        else:
            verdict_tag = "balanced"

        quality = int(rich_ratio * 100.0 * (1.0 - waste_ratio * 0.5))
        return (verdict_tag, int(feed_signal_present), fallback_useful, fallback_waste, quality)

    def branch_verdict(
        self,
        feed_signal_present: bool,
        fallback_useful: int,
        fallback_waste: int,
        findings_rich: int,
        findings_fallback: int,
        squandered_high_usefulness: int,
        metadata_strong_but_content_weak: int,
        low_trust_feed_hits: int,
        total_entries_with_hits: int,
        entries_seen: int,
        feed_native_yield_ratio: float,
        fallback_value_ratio: float,
    ) -> dict[str, Any]:
        total_findings = findings_rich + findings_fallback
        verdict: dict[str, Any] = {
            "verdict_tag": "no_signal",
            "feed_native_yield": findings_rich,
            "fallback_yield": findings_fallback,
            "total_yield": total_findings,
            "squandered_high_usefulness_entries": squandered_high_usefulness,
            "unnecessary_fallbacks": fallback_waste,
            "useful_fallbacks": fallback_useful,
            "feed_corroborates": feed_signal_present and fallback_useful > 0,
            "feed_burns_budget": fallback_waste > 0 and findings_rich == 0,
            "feed_next_action": "unknown",
            "feed_confidence_note": "",
            "feed_confidence_score": 0,
            "feed_native_yield_ratio": feed_native_yield_ratio,
            "fallback_value_ratio": fallback_value_ratio,
            "high_usefulness_waste_rate": 0.0,
            "metadata_strong_content_weak": metadata_strong_but_content_weak,
            "low_trust_feed_hits": low_trust_feed_hits,
            "entries_with_hits": total_entries_with_hits,
            "entries_seen": entries_seen,
        }

        if total_findings == 0:
            verdict["verdict_tag"] = "no_signal"
            verdict["feed_next_action"] = "reassess_feed"
            verdict["feed_confidence_note"] = "no findings in either branch"
            return verdict

        if squandered_high_usefulness + fallback_waste > 0:
            verdict["high_usefulness_waste_rate"] = fallback_waste / (squandered_high_usefulness + fallback_waste)

        rich_ratio = feed_native_yield_ratio
        if rich_ratio >= 0.7:
            verdict["verdict_tag"] = "feed_lean"
        elif rich_ratio <= 0.3:
            verdict["verdict_tag"] = "fallback_lean"
        else:
            verdict["verdict_tag"] = "balanced"

        verdict["feed_corroborates"] = feed_signal_present and fallback_useful > 0
        verdict["feed_burns_budget"] = fallback_waste > 0 and findings_rich == 0

        if total_findings == 0:
            verdict["feed_next_action"] = "reassess_feed"
            verdict["feed_confidence_note"] = "neither branch produced signal"
            verdict["feed_confidence_score"] = 0
        elif verdict["feed_burns_budget"]:
            verdict["feed_next_action"] = "fallback_more"
            verdict["feed_confidence_note"] = "feed burns budget; rely on fallback"
        elif verdict["feed_corroborates"]:
            verdict["feed_next_action"] = "continue_feed"
            verdict["feed_confidence_note"] = "both branches contribute; feed is valuable"
        elif feed_signal_present and fallback_useful == 0:
            verdict["feed_next_action"] = "continue_feed"
            verdict["feed_confidence_note"] = "feed-native only; fallback not needed"
        else:
            verdict["feed_next_action"] = "reassess_feed"
            verdict["feed_confidence_note"] = "mixed signals; review feed quality"

        confidence = int(rich_ratio * 100.0 * (1.0 - verdict["high_usefulness_waste_rate"] * 0.5))
        verdict["feed_confidence_score"] = max(0, confidence)

        return verdict
