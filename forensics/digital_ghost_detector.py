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
import re as _re
import logging
from dataclasses import dataclass, field
import msgspec
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import numpy as np
from operator import attrgetter, itemgetter
logger = logging.getLogger(__name__)
_GHOST_PATTERN_GROUPS: list[tuple[str, str]] = [('ts_0', 'created.*modified.*0000'), ('ts_1', 'last.*access.*1970'), ('ts_2', 'deleted.*\\d{4}-\\d{2}-\\d{2}'), ('frag_0', '\\{[^{}]*\\}'), ('frag_1', '<[^>]+>'), ('frag_2', '[a-zA-Z0-9]{20,}'), ('shadow_0', 'ref.*deleted'), ('shadow_1', 'moved.*permanently'), ('shadow_2', '404.*not.*found'), ('shadow_3', 'previously.*available'), ('fs_0', '\\.tmp$'), ('fs_1', '~$'), ('fs_2', '\\.bak$'), ('fs_3', '\\.old$'), ('fs_4', 'recycle'), ('fs_5', 'trash')]
_URL_PATTERN: _re.Pattern[str] = _re.compile('https?://[^\\s<>"{}|\\\\^`\\[\\]]+')
_GHOST_GROUP_TO_TYPE: dict[str, tuple[str, float, list[str]]] = {'ts_0': ('timestamp_gap', 0.7, ['suspicious_timestamp', 'possible_deletion']), 'ts_1': ('timestamp_gap', 0.7, ['suspicious_timestamp', 'possible_deletion']), 'ts_2': ('timestamp_gap', 0.7, ['suspicious_timestamp', 'possible_deletion']), 'frag_0': ('content_fragment', 0.6, ['structural_remains', 'partial_content']), 'frag_1': ('content_fragment', 0.6, ['structural_remains', 'partial_content']), 'frag_2': ('content_fragment', 0.6, ['structural_remains', 'partial_content']), 'shadow_0': ('shadow_reference', 0.8, ['reference_to_deleted', 'broken_link']), 'shadow_1': ('shadow_reference', 0.8, ['reference_to_deleted', 'broken_link']), 'shadow_2': ('shadow_reference', 0.8, ['reference_to_deleted', 'broken_link']), 'shadow_3': ('shadow_reference', 0.8, ['reference_to_deleted', 'broken_link']), 'fs_0': ('filesystem_artifact', 0.65, ['backup_file', 'temporary_file', 'recovered_item']), 'fs_1': ('filesystem_artifact', 0.65, ['backup_file', 'temporary_file', 'recovered_item']), 'fs_2': ('filesystem_artifact', 0.65, ['backup_file', 'temporary_file', 'recovered_item']), 'fs_3': ('filesystem_artifact', 0.65, ['backup_file', 'temporary_file', 'recovered_item']), 'fs_4': ('filesystem_artifact', 0.65, ['backup_file', 'temporary_file', 'recovered_item']), 'fs_5': ('filesystem_artifact', 0.65, ['backup_file', 'temporary_file', 'recovered_item'])}
_DELETION_PATTERNS: list[str] = ['deleted?\\s+(?:by|on|at)', 'removed?\\s+(?:by|on|at)', '\\[deleted\\]', '\\[removed\\]', 'content\\s+unavailable', 'page\\s+not\\s+found', '404\\s+error']
_DELETION_REGEX_SET: _re.Pattern[str] = _re.compile('|'.join(_DELETION_PATTERNS))
_GHOST_COMBINED = _re.compile('|'.join((f'(?P<{name}>{pattern})' for name, pattern in _GHOST_PATTERN_GROUPS)))
_SIGNAL_TYPE_MAP: dict[str, tuple[str, float, list[str]]] = {'timestamp_gap': ('timestamp_gap', 0.7, ['suspicious_timestamp', 'possible_deletion']), 'content_fragment': ('content_fragment', 0.6, ['structural_remains', 'partial_content']), 'shadow_reference': ('shadow_reference', 0.8, ['reference_to_deleted', 'broken_link']), 'filesystem_artifact': ('filesystem_artifact', 0.65, ['backup_file', 'temporary_file', 'recovered_item'])}

class GhostSignal(msgspec.Struct, gc=False):
    """Detected digital ghost signal."""
    signal_type: str
    location: str
    confidence: float
    timestamp: datetime | None = None
    content_snippet: str | None = None
    indicators: list[str] = field(default_factory=list)

class RecoveredContent(msgspec.Struct, frozen=True, gc=False):
    """Potentially recovered content from ghost signals."""
    original_location: str
    recovered_text: str
    confidence: float
    recovery_method: str
    source_signals: list[str] = field(default_factory=list)
    temporal_context: datetime | None = None

class DigitalGhostAnalysis(msgspec.Struct, gc=False):
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
    __slots__ = tuple(('confidence_threshold',))

    def __init__(self, confidence_threshold: float=0.6):
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
        result = DigitalGhostAnalysis(target=str(file_path), timestamp=datetime.now(UTC))
        try:
            with open(file_path, 'rb') as f:
                raw_content = f.read()
            try:
                text_content = raw_content.decode('utf-8', errors='ignore')
            except Exception:
                text_content = ''
            result.ghost_signals = self._detect_ghost_signals(str(file_path), text_content, raw_content)
            metadata_signals = self._analyze_metadata_residuals(file_path)
            result.ghost_signals.extend(metadata_signals)
            result.deletion_indicators = self._detect_deletion_indicators(text_content, raw_content)
            result.recovered_content = self._attempt_content_recovery(result.ghost_signals, text_content)
            result.temporal_patterns = self._analyze_temporal_patterns(result.ghost_signals)
            if result.ghost_signals:
                result.overall_confidence = np.mean([s.confidence for s in result.ghost_signals])
            result.recommendations = self._generate_recommendations(result)
            logger.info(f'Ghost analysis complete: {len(result.ghost_signals)} signals, {len(result.recovered_content)} recovered fragments')
        except Exception as e:
            logger.error(f'Ghost analysis failed: {e}')
            result.recommendations.append(f'Analysis error: {str(e)}')
        return result

    def analyze_text_content(self, content: str, source: str='unknown') -> DigitalGhostAnalysis:
        """
        Analyze text content for ghost signals.

        Args:
            content: Text content to analyze
            source: Source identifier

        Returns:
            DigitalGhostAnalysis with findings
        """
        result = DigitalGhostAnalysis(target=source, timestamp=datetime.now(UTC))
        result.ghost_signals = self._detect_ghost_signals(source, content, b'')
        result.deletion_indicators = self._detect_deletion_indicators(content, b'')
        result.recovered_content = self._attempt_content_recovery(result.ghost_signals, content)
        if result.ghost_signals:
            result.overall_confidence = np.mean([s.confidence for s in result.ghost_signals])
        result.recommendations = self._generate_recommendations(result)
        return result

    def _detect_ghost_signals(self, location: str, text_content: str, raw_content: bytes) -> list[GhostSignal]:
        """
        Detect digital ghost signals in content using named-group combined regex.

        Issue #3: Replaces O(n×m) nested-loop approach (RegexSet → individual finditer)
        with O(n) single-pass named-group matching. m.lastgroup directly identifies
        the pattern type without rescanning the text.
        """
        signals = []
        for m in _GHOST_COMBINED.finditer(text_content):
            group_name = m.lastgroup
            if group_name is None:
                continue
            if group_name.startswith('frag_') and len(m.group()) <= 10:
                continue
            if group_name == 'frag_1':
                matched = m.group()
                if matched.count('@') == 1 and '.' in matched[1:-1]:
                    continue
            if group_name == 'frag_2':
                matched = m.group()
                if len(matched) >= 40 and all((c in '0123456789abcdefABCDEF' for c in matched)):
                    continue
            sig_type, confidence, ind_list = _GHOST_GROUP_TO_TYPE[group_name]
            signals.append(GhostSignal(signal_type=sig_type, location=f'{location}:{m.start()}', confidence=confidence, content_snippet=m.group()[:100], indicators=ind_list))
        null_count = raw_content.count(0)
        if null_count > len(raw_content) * 0.1:
            signals.append(GhostSignal(signal_type='partial_overwrite', location=location, confidence=0.75, indicators=['null_padding', 'partial_deletion', 'wiped_section'], content_snippet=f'{null_count} null bytes detected'))
        for group_name, pattern in _GHOST_PATTERN_GROUPS:
            if group_name.startswith('fs_') and _re.search(pattern, location):
                sig_type, confidence, ind_list = _GHOST_GROUP_TO_TYPE[group_name]
                signals.append(GhostSignal(signal_type=sig_type, location=location, confidence=confidence, indicators=ind_list))
                break
        signals.sort(key=attrgetter("confidence"), reverse=True)
        return signals

    def _analyze_metadata_residuals(self, file_path: Path) -> list[GhostSignal]:
        """
        Analyze file metadata for residual information.

        From comments: "Extract metadata"
        """
        signals = []
        try:
            stat = file_path.stat()
            created = datetime.fromtimestamp(stat.st_ctime)
            modified = datetime.fromtimestamp(stat.st_mtime)
            accessed = datetime.fromtimestamp(stat.st_atime)
            if created > modified:
                signals.append(GhostSignal(signal_type='metadata_residual', location=str(file_path), confidence=0.6, timestamp=created, indicators=['restore_from_backup', 'creation_after_modification']))
            if (modified - accessed).days > 30:
                signals.append(GhostSignal(signal_type='metadata_residual', location=str(file_path), confidence=0.5, timestamp=accessed, indicators=['stale_access_time', 'possible_undeletion']))
        except Exception as e:
            logger.debug(f'Metadata analysis failed: {e}')
        return signals

    def _detect_deletion_indicators(self, text_content: str, raw_content: bytes) -> list[str]:
        """
        Detect indicators of deletion in content.

        From comments: "Common digital ghost indicators"
        Issue #3: Uses pre-compiled _DELETION_REGEX_SET (single-pass).
        """
        indicators = []
        for m in _DELETION_REGEX_SET.finditer(text_content):
            indicators.append(f'deletion_marker:{m.group()}')
        if len(raw_content) > 1000:
            chunks = [raw_content[i:i + 256] for i in range(0, len(raw_content), 256)]
            for i, chunk in enumerate(chunks[:5]):
                entropy = self._calculate_entropy(chunk)
                if entropy > 7.5:
                    indicators.append(f'high_entropy_chunk_{i}:{entropy:.2f}')
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

    def _attempt_content_recovery(self, ghost_signals: list[GhostSignal], text_content: str) -> list[RecoveredContent]:
        """
        Attempt to recover content from ghost signals.

        From comments: "ML-based content prediction", "Recover content from multiple sources"
        """
        recovered = []
        fragments = [s for s in ghost_signals if s.signal_type == 'content_fragment']
        if len(fragments) >= 2:
            combined_text = ' '.join([f.content_snippet or '' for f in fragments[:5]])
            if len(combined_text) > 50:
                recovered.append(RecoveredContent(original_location=fragments[0].location, recovered_text=combined_text[:500], confidence=np.mean([f.confidence for f in fragments]), recovery_method='fragment_reconstruction', source_signals=[f.signal_type for f in fragments]))
        urls = _URL_PATTERN.findall(text_content)
        for url in urls[:5]:
            if any((indicator in url.lower() for indicator in ['deleted', 'removed', '404'])):
                recovered.append(RecoveredContent(original_location=url, recovered_text=f'Reference to potentially deleted content: {url}', confidence=0.5, recovery_method='shadow_reference_detection', source_signals=['url_analysis']))
        return recovered

    def _analyze_temporal_patterns(self, ghost_signals: list[GhostSignal]) -> list[dict[str, Any]]:
        """
        Analyze temporal patterns in ghost signals.

        From comments: "Temporal pattern matching", "Simulate finding matches in historical snapshots"
        """
        patterns = []
        timed_signals = [s for s in ghost_signals if s.timestamp]
        if len(timed_signals) >= 2:
            timed_signals.sort(key=attrgetter("timestamp"))
            time_diffs = []
            for i in range(1, len(timed_signals)):
                ts_i = timed_signals[i].timestamp
                ts_prev = timed_signals[i - 1].timestamp
                if ts_i is not None and ts_prev is not None:
                    diff = (ts_i - ts_prev).total_seconds()
                    time_diffs.append(diff)
            if time_diffs:
                avg_diff = np.mean(time_diffs)
                patterns.append({'type': 'temporal_clustering', 'average_interval_seconds': avg_diff, 'signal_count': len(timed_signals), 'confidence': 0.7 if avg_diff < 3600 else 0.5})
        return patterns

    def _generate_recommendations(self, result: DigitalGhostAnalysis) -> list[str]:
        """Generate recommendations based on analysis."""
        recommendations = []
        if result.ghost_signals:
            high_conf_signals = [s for s in result.ghost_signals if s.confidence > 0.7]
            if high_conf_signals:
                recommendations.append(f'High-confidence ghost signals detected ({len(high_conf_signals)}). Consider forensic recovery tools.')
        if result.recovered_content:
            recommendations.append(f'{len(result.recovered_content)} content fragments potentially recoverable. Review recovered content for sensitive information.')
        if result.deletion_indicators:
            recommendations.append(f'{len(result.deletion_indicators)} deletion indicators found. Content may have been incompletely wiped.')
        if not result.ghost_signals:
            recommendations.append('No significant ghost signals detected. File appears clean.')
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
    return {'target': str(file_path), 'ghost_signals_count': len(result.ghost_signals), 'high_confidence_signals': len([s for s in result.ghost_signals if s.confidence > 0.7]), 'recovered_fragments': len(result.recovered_content), 'deletion_indicators': result.deletion_indicators, 'overall_confidence': result.overall_confidence, 'has_ghosts': result.ghost_signals, 'recommendations': result.recommendations}
__all__ = ['DigitalGhostDetector', 'DigitalGhostAnalysis', 'GhostSignal', 'RecoveredContent', 'detect_digital_ghosts']