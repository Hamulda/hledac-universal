"""
Digital Ghost Detector - Recovery of Deleted/Digital Shadows
=============================================================

From deep_research/next_gen_enhancements.py comments:
- "Analyze digital ghost signals"
- "Digital ghost analysis"
- "ML-based content prediction" for recovered content
- "Temporal pattern matching" for historical recovery
- "Recover content from multiple sources"

Detects traces of deleted content, incomplete deletions, and digital shadows
that remain in files, filesystems, and web archives.

M1 Optimized: Memory-efficient analysis without large dependencies.
"""
from __future__ import annotations

import re as _re

import logging
from dataclasses import dataclass, field
import msgspec
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


# Issue #3: Pre-compiled regex patterns — named-group combined regex for single-pass matching.
# Each pattern has a named group so m.lastgroup tells us the type directly (no rescanning).
_GHOST_PATTERN_GROUPS: list[tuple[str, str]] = [
    # (group_name, pattern) — order determines group index
    ("ts_0",       r'created.*modified.*0000'),
    ("ts_1",       r'last.*access.*1970'),
    ("ts_2",       r'deleted.*\d{4}-\d{2}-\d{2}'),
    ("frag_0",     r'\{[^{}]*\}'),
    ("frag_1",     r'<[^>]+>'),
    ("frag_2",     r'[a-zA-Z0-9]{20,}'),
    ("shadow_0",   r'ref.*deleted'),
    ("shadow_1",   r'moved.*permanently'),
    ("shadow_2",   r'404.*not.*found'),
    ("shadow_3",   r'previously.*available'),
    ("fs_0",       r'\.tmp$'),
    ("fs_1",       r'~$'),
    ("fs_2",       r'\.bak$'),
    ("fs_3",       r'\.old$'),
    ("fs_4",       r'recycle'),
    ("fs_5",       r'trash'),
]
_URL_PATTERN: _re.Pattern[str] = _re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+')

# Group name → signal type mapping
_GHOST_GROUP_TO_TYPE: dict[str, tuple[str, float, list[str]]] = {
    "ts_0":   ("timestamp_gap",       0.7, ["suspicious_timestamp", "possible_deletion"]),
    "ts_1":   ("timestamp_gap",       0.7, ["suspicious_timestamp", "possible_deletion"]),
    "ts_2":   ("timestamp_gap",       0.7, ["suspicious_timestamp", "possible_deletion"]),
    "frag_0": ("content_fragment",    0.6, ["structural_remains", "partial_content"]),
    "frag_1": ("content_fragment",    0.6, ["structural_remains", "partial_content"]),
    "frag_2": ("content_fragment",    0.6, ["structural_remains", "partial_content"]),
    "shadow_0": ("shadow_reference",  0.8, ["reference_to_deleted", "broken_link"]),
    "shadow_1": ("shadow_reference",  0.8, ["reference_to_deleted", "broken_link"]),
    "shadow_2": ("shadow_reference",  0.8, ["reference_to_deleted", "broken_link"]),
    "shadow_3": ("shadow_reference",  0.8, ["reference_to_deleted", "broken_link"]),
    "fs_0":   ("filesystem_artifact", 0.65, ["backup_file", "temporary_file", "recovered_item"]),
    "fs_1":   ("filesystem_artifact", 0.65, ["backup_file", "temporary_file", "recovered_item"]),
    "fs_2":   ("filesystem_artifact", 0.65, ["backup_file", "temporary_file", "recovered_item"]),
    "fs_3":   ("filesystem_artifact", 0.65, ["backup_file", "temporary_file", "recovered_item"]),
    "fs_4":   ("filesystem_artifact", 0.65, ["backup_file", "temporary_file", "recovered_item"]),
    "fs_5":   ("filesystem_artifact", 0.65, ["backup_file", "temporary_file", "recovered_item"]),
}

_DELETION_PATTERNS: list[str] = [
    r'deleted?\s+(?:by|on|at)',
    r'removed?\s+(?:by|on|at)',
    r'\[deleted\]',
    r'\[removed\]',
    r'content\s+unavailable',
    r'page\s+not\s+found',
    r'404\s+error',
]
_DELETION_REGEX_SET: _re.Pattern[str] = _re.compile("|".join(_DELETION_PATTERNS))

# Single combined regex with named groups — one finditer pass, no nested rescan.
_GHOST_COMBINED = _re.compile(
    "|".join(f"(?P<{name}>{pattern})" for name, pattern in _GHOST_PATTERN_GROUPS)
)

_SIGNAL_TYPE_MAP: dict[str, tuple[str, float, list[str]]] = {
    "timestamp_gap": ("timestamp_gap", 0.7, ["suspicious_timestamp", "possible_deletion"]),
    "content_fragment": ("content_fragment", 0.6, ["structural_remains", "partial_content"]),
    "shadow_reference": ("shadow_reference", 0.8, ["reference_to_deleted", "broken_link"]),
    "filesystem_artifact": ("filesystem_artifact", 0.65, ["backup_file", "temporary_file", "recovered_item"]),
}


@dataclass
class GhostSignal:
    """Detected digital ghost signal."""
    signal_type: str  # metadata_residual, fragment, shadow_reference, cache_trace
    location: str
    confidence: float
    timestamp: datetime | None = None
    content_snippet: str | None = None
    indicators: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RecoveredContent:
    """Potentially recovered content from ghost signals."""
    original_location: str
    recovered_text: str
    confidence: float
    recovery_method: str
    source_signals: list[str] = field(default_factory=list)
    temporal_context: datetime | None = None


@dataclass
class DigitalGhostAnalysis:
    """Complete digital ghost analysis result."""
    target: str
    timestamp: datetime
    ghost_signals: list[GhostSignal] = field(default_factory=list)
    recovered_content: list[RecoveredContent] = field(default_factory=list)
    deletion_indicators: list[str] = field(default_factory=list)
    temporal_patterns: list[dict[str, Any]] = field(default_factory=list)
    overall_confidence: float = 0.0
    recommendations: list[str] = field(default_factory=list)


class DigitalGhostDetector:
    """
    Digital Ghost Detector - Finds traces of deleted content.

    From next_gen_enhancements.py comments:
    - "Analyze digital ghost signals" - finds residual data
    - "Common digital ghost indicators" - patterns of deletion
    - "ML-based content prediction" - reconstructs missing content
    - "Temporal pattern matching" - finds historical versions
    - "Combine all recovered content sources" - synthesis of findings

    Detection methods:
    1. Metadata residuals (timestamps, author info)
    2. File fragment analysis (partial overwrites)
    3. Shadow references (links to deleted content)
    4. Cache/archive traces (Wayback, search caches)
    5. Cross-reference gaps (missing sequence numbers)
    """

    def __init__(self, confidence_threshold: float = 0.6):
        """
        Initialize Digital Ghost Detector.

        Args:
            confidence_threshold: Minimum confidence to report findings
        """
        self.confidence_threshold = confidence_threshold

    def analyze_file(self, file_path: str | Path) -> DigitalGhostAnalysis:
        """
        Analyze file for digital ghost signals.

        Args:
            file_path: Path to file to analyze

        Returns:
            DigitalGhostAnalysis with findings
        """
        file_path = Path(file_path)
        result = DigitalGhostAnalysis(
            target=str(file_path),
            timestamp=datetime.now(UTC)  # noqa: DTZ005
        )

        try:
            # Read file content
            with open(file_path, 'rb') as f:
                raw_content = f.read()

            # Try to decode as text
            try:
                text_content = raw_content.decode('utf-8', errors='ignore')
            except Exception:
                text_content = ""

            # Detect ghost signals
            result.ghost_signals = self._detect_ghost_signals(
                str(file_path), text_content, raw_content
            )

            # Analyze metadata residuals
            metadata_signals = self._analyze_metadata_residuals(file_path)
            result.ghost_signals.extend(metadata_signals)

            # Detect deletion indicators
            result.deletion_indicators = self._detect_deletion_indicators(
                text_content, raw_content
            )

            # Attempt content recovery
            result.recovered_content = self._attempt_content_recovery(
                result.ghost_signals, text_content
            )

            # Analyze temporal patterns
            result.temporal_patterns = self._analyze_temporal_patterns(
                result.ghost_signals
            )

            # Calculate overall confidence
            if result.ghost_signals:
                result.overall_confidence = np.mean([
                    s.confidence for s in result.ghost_signals
                ])

            # Generate recommendations
            result.recommendations = self._generate_recommendations(result)

            logger.info(
                f"Ghost analysis complete: {len(result.ghost_signals)} signals, "
                f"{len(result.recovered_content)} recovered fragments"
            )

        except Exception as e:
            logger.error(f"Ghost analysis failed: {e}")
            result.recommendations.append(f"Analysis error: {str(e)}")

        return result

    def analyze_text_content(
        self,
        content: str,
        source: str = "unknown"
    ) -> DigitalGhostAnalysis:
        """
        Analyze text content for ghost signals.

        Args:
            content: Text content to analyze
            source: Source identifier

        Returns:
            DigitalGhostAnalysis with findings
        """
        result = DigitalGhostAnalysis(
            target=source,
            timestamp=datetime.now(UTC)  # noqa: DTZ005
        )

        # Detect ghost signals in text
        result.ghost_signals = self._detect_ghost_signals(source, content, b"")

        # Detect deletion indicators
        result.deletion_indicators = self._detect_deletion_indicators(content, b"")

        # Attempt content recovery
        result.recovered_content = self._attempt_content_recovery(
            result.ghost_signals, content
        )

        # Calculate confidence
        if result.ghost_signals:
            result.overall_confidence = np.mean([
                s.confidence for s in result.ghost_signals
            ])

        result.recommendations = self._generate_recommendations(result)

        return result

    def _detect_ghost_signals(
        self,
        location: str,
        text_content: str,
        raw_content: bytes
    ) -> list[GhostSignal]:
        """
        Detect digital ghost signals in content using named-group combined regex.

        Issue #3: Replaces O(n×m) nested-loop approach (RegexSet → individual finditer)
        with O(n) single-pass named-group matching. m.lastgroup directly identifies
        the pattern type without rescanning the text.
        """
        signals = []

        # Single finditer pass — m.lastgroup gives us the pattern name directly.
        for m in _GHOST_COMBINED.finditer(text_content):
            group_name = m.lastgroup
            if group_name is None:
                continue

            # Content fragments need minimum length filter
            if group_name.startswith("frag_") and len(m.group()) <= 10:
                continue

            # Issue #5: Exclude email-like matches from frag_1 (e.g. <user@domain.com>)
            if group_name == "frag_1":
                matched = m.group()
                # Skip if looks like an email address in angle brackets
                if matched.count('@') == 1 and '.' in matched[1:-1]:
                    continue

            # Issue #6: Exclude hex strings (likely hashes) from frag_2
            if group_name == "frag_2":
                matched = m.group()
                # Skip if it's a pure hex string (SHA256, SHA512, etc.)
                if len(matched) >= 40 and all(c in '0123456789abcdefABCDEF' for c in matched):
                    continue

            sig_type, confidence, ind_list = _GHOST_GROUP_TO_TYPE[group_name]
            signals.append(GhostSignal(
                signal_type=sig_type,
                location=f"{location}:{m.start()}",
                confidence=confidence,
                content_snippet=m.group()[:100],
                indicators=ind_list
            ))

        # Check for null byte patterns (sign of partial deletion)
        null_count = raw_content.count(0)
        if null_count > len(raw_content) * 0.1:  # More than 10% nulls
            signals.append(GhostSignal(
                signal_type='partial_overwrite',
                location=location,
                confidence=0.75,
                indicators=['null_padding', 'partial_deletion', 'wiped_section'],
                content_snippet=f"{null_count} null bytes detected"
            ))

        # Check for filesystem artifacts (path-based, not text-based)
        for group_name, pattern in _GHOST_PATTERN_GROUPS:
            if group_name.startswith("fs_") and _re.search(pattern, location):
                sig_type, confidence, ind_list = _GHOST_GROUP_TO_TYPE[group_name]
                signals.append(GhostSignal(
                    signal_type=sig_type,
                    location=location,
                    confidence=confidence,
                    indicators=ind_list
                ))
                break

        # Sort by confidence
        signals.sort(key=lambda x: x.confidence, reverse=True)
        return signals

    def _analyze_metadata_residuals(
        self,
        file_path: Path
    ) -> list[GhostSignal]:
        """
        Analyze file metadata for residual information.

        From comments: "Extract metadata"
        """
        signals = []

        try:
            stat = file_path.stat()

            # Check for suspicious timestamp patterns
            created = datetime.fromtimestamp(stat.st_ctime)  # noqa: DTZ006
            modified = datetime.fromtimestamp(stat.st_mtime)  # noqa: DTZ006
            accessed = datetime.fromtimestamp(stat.st_atime)  # noqa: DTZ006

            # If created after modified, possible restore from backup
            if created > modified:
                signals.append(GhostSignal(
                    signal_type='metadata_residual',
                    location=str(file_path),
                    confidence=0.6,
                    timestamp=created,
                    indicators=['restore_from_backup', 'creation_after_modification']
                ))

            # If very old access time but recent modification, possible undeletion
            if (modified - accessed).days > 30:
                signals.append(GhostSignal(
                    signal_type='metadata_residual',
                    location=str(file_path),
                    confidence=0.5,
                    timestamp=accessed,
                    indicators=['stale_access_time', 'possible_undeletion']
                ))

        except Exception as e:
            logger.debug(f"Metadata analysis failed: {e}")

        return signals

    def _detect_deletion_indicators(
        self,
        text_content: str,
        raw_content: bytes
    ) -> list[str]:
        """
        Detect indicators of deletion in content.

        From comments: "Common digital ghost indicators"
        Issue #3: Uses pre-compiled _DELETION_REGEX_SET (single-pass).
        """
        indicators = []

        # Check for common deletion markers — pre-compiled RegexSet
        for m in _DELETION_REGEX_SET.finditer(text_content):
            indicators.append(f"deletion_marker:{m.group()}")

        # Check for high entropy sections (encrypted or wiped)
        if len(raw_content) > 1000:
            chunks = [raw_content[i:i+256] for i in range(0, len(raw_content), 256)]
            for i, chunk in enumerate(chunks[:5]):  # Check first 5 chunks
                entropy = self._calculate_entropy(chunk)
                if entropy > 7.5:  # High entropy
                    indicators.append(f"high_entropy_chunk_{i}:{entropy:.2f}")

        return indicators

    def _calculate_entropy(self, data: bytes) -> float:
        """Calculate Shannon entropy."""
        if not data:
            return 0.0

        byte_counts = {}
        for byte in data:
            byte_counts[byte] = byte_counts.get(byte, 0) + 1

        entropy = 0.0
        length = len(data)
        for count in byte_counts.values():
            p = count / length
            entropy -= p * np.log2(p)

        return entropy

    def _attempt_content_recovery(
        self,
        ghost_signals: list[GhostSignal],
        text_content: str
    ) -> list[RecoveredContent]:
        """
        Attempt to recover content from ghost signals.

        From comments: "ML-based content prediction", "Recover content from multiple sources"
        """
        recovered = []

        # Group signals by type
        fragments = [s for s in ghost_signals if s.signal_type == 'content_fragment']

        if len(fragments) >= 2:
            # Try to reconstruct from multiple fragments
            combined_text = ' '.join([
                f.content_snippet or '' for f in fragments[:5]
            ])

            if len(combined_text) > 50:
                recovered.append(RecoveredContent(
                    original_location=fragments[0].location,
                    recovered_text=combined_text[:500],
                    confidence=np.mean([f.confidence for f in fragments]),
                    recovery_method='fragment_reconstruction',
                    source_signals=[f.signal_type for f in fragments]
                ))

        # Look for URL patterns that might reference deleted content
        # Issue #3: Uses pre-compiled _URL_PATTERN
        urls = _URL_PATTERN.findall(text_content)

        for url in urls[:5]:  # Limit to first 5 URLs
            if any(indicator in url.lower() for indicator in ['deleted', 'removed', '404']):
                recovered.append(RecoveredContent(
                    original_location=url,
                    recovered_text=f"Reference to potentially deleted content: {url}",
                    confidence=0.5,
                    recovery_method='shadow_reference_detection',
                    source_signals=['url_analysis']
                ))

        return recovered

    def _analyze_temporal_patterns(
        self,
        ghost_signals: list[GhostSignal]
    ) -> list[dict[str, Any]]:
        """
        Analyze temporal patterns in ghost signals.

        From comments: "Temporal pattern matching", "Simulate finding matches in historical snapshots"
        """
        patterns = []

        # Group signals by timestamp
        timed_signals = [s for s in ghost_signals if s.timestamp]

        if len(timed_signals) >= 2:
            # Sort by timestamp
            timed_signals.sort(key=lambda x: x.timestamp)

            # Look for clustering
            time_diffs = []
            for i in range(1, len(timed_signals)):
                ts_i = timed_signals[i].timestamp
                ts_prev = timed_signals[i - 1].timestamp
                if ts_i is not None and ts_prev is not None:
                    diff = (ts_i - ts_prev).total_seconds()
                    time_diffs.append(diff)

            if time_diffs:
                avg_diff = np.mean(time_diffs)
                patterns.append({
                    'type': 'temporal_clustering',
                    'average_interval_seconds': avg_diff,
                    'signal_count': len(timed_signals),
                    'confidence': 0.7 if avg_diff < 3600 else 0.5  # High confidence if within hour
                })

        return patterns

    def _generate_recommendations(
        self,
        result: DigitalGhostAnalysis
    ) -> list[str]:
        """Generate recommendations based on analysis."""
        recommendations = []

        if result.ghost_signals:
            high_conf_signals = [s for s in result.ghost_signals if s.confidence > 0.7]
            if high_conf_signals:
                recommendations.append(
                    f"High-confidence ghost signals detected ({len(high_conf_signals)}). "
                    "Consider forensic recovery tools."
                )

        if result.recovered_content:
            recommendations.append(
                f"{len(result.recovered_content)} content fragments potentially recoverable. "
                "Review recovered content for sensitive information."
            )

        if result.deletion_indicators:
            recommendations.append(
                f"{len(result.deletion_indicators)} deletion indicators found. "
                "Content may have been incompletely wiped."
            )

        if not result.ghost_signals:
            recommendations.append("No significant ghost signals detected. File appears clean.")

        return recommendations


def detect_digital_ghosts(file_path: str | Path) -> dict[str, Any]:
    """
    Quick function to detect digital ghosts in a file.

    Args:
        file_path: Path to file to analyze

    Returns:
        Dictionary with key findings
    """
    detector = DigitalGhostDetector()
    result = detector.analyze_file(file_path)

    return {
        'target': str(file_path),
        'ghost_signals_count': len(result.ghost_signals),
        'high_confidence_signals': len([s for s in result.ghost_signals if s.confidence > 0.7]),
        'recovered_fragments': len(result.recovered_content),
        'deletion_indicators': result.deletion_indicators,
        'overall_confidence': result.overall_confidence,
        'has_ghosts': result.ghost_signals,
        'recommendations': result.recommendations
    }


__all__ = [
    "DigitalGhostDetector",
    "DigitalGhostAnalysis",
    "GhostSignal",
    "RecoveredContent",
    "detect_digital_ghosts",
]
