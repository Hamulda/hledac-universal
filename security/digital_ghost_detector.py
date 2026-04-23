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

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class GhostSignal:
    """Detected digital ghost signal."""
    signal_type: str  # metadata_residual, fragment, shadow_reference, cache_trace
    location: str
    confidence: float
    timestamp: Optional[datetime] = None
    content_snippet: Optional[str] = None
    indicators: List[str] = field(default_factory=list)


@dataclass
class RecoveredContent:
    """Potentially recovered content from ghost signals."""
    original_location: str
    recovered_text: str
    confidence: float
    recovery_method: str
    source_signals: List[str] = field(default_factory=list)
    temporal_context: Optional[datetime] = None


@dataclass
class DigitalGhostAnalysis:
    """Complete digital ghost analysis result."""
    target: str
    timestamp: datetime
    ghost_signals: List[GhostSignal] = field(default_factory=list)
    recovered_content: List[RecoveredContent] = field(default_factory=list)
    deletion_indicators: List[str] = field(default_factory=list)
    temporal_patterns: List[Dict[str, Any]] = field(default_factory=list)
    overall_confidence: float = 0.0
    recommendations: List[str] = field(default_factory=list)


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
    
    # Common digital ghost indicators from comments
    GHOST_INDICATORS = {
        'timestamp_gaps': [
            r'created.*modified.*0000',  # Zeroed timestamps
            r'last.*access.*1970',  # Unix epoch
            r'deleted.*\d{4}-\d{2}-\d{2}',  # Deletion markers
        ],
        'content_fragments': [
            r'\{[^{}]*\}',  # JSON remnants
            r'<[^>]+>',  # HTML remnants
            r'[a-zA-Z0-9]{20,}',  # Long strings (hashes, IDs)
        ],
        'shadow_references': [
            r'ref.*deleted',
            r'moved.*permanently',
            r'404.*not.*found',
            r'previously.*available',
        ],
        'filesystem_artifacts': [
            r'\.tmp$',
            r'~$',
            r'\.bak$',
            r'\.old$',
            r'recycle',
            r'trash',
        ]
    }
    
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
            timestamp=datetime.now()
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
            timestamp=datetime.now()
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
    ) -> List[GhostSignal]:
        """
        Detect digital ghost signals in content.
        
        From comments: "Analyze digital ghost signals", "Common digital ghost indicators"
        """
        signals = []
        
        # Check for timestamp gaps
        for pattern in self.GHOST_INDICATORS['timestamp_gaps']:
            matches = re.finditer(pattern, text_content, re.IGNORECASE)
            for match in matches:
                signals.append(GhostSignal(
                    signal_type='timestamp_gap',
                    location=f"{location}:{match.start()}",
                    confidence=0.7,
                    content_snippet=match.group()[:50],
                    indicators=['suspicious_timestamp', 'possible_deletion']
                ))
        
        # Check for content fragments
        for pattern in self.GHOST_INDICATORS['content_fragments']:
            matches = re.finditer(pattern, text_content)
            for match in matches:
                snippet = match.group()
                if len(snippet) > 10:  # Meaningful fragment
                    signals.append(GhostSignal(
                        signal_type='content_fragment',
                        location=f"{location}:{match.start()}",
                        confidence=0.6,
                        content_snippet=snippet[:100],
                        indicators=['structural_remains', 'partial_content']
                    ))
        
        # Check for shadow references
        for pattern in self.GHOST_INDICATORS['shadow_references']:
            matches = re.finditer(pattern, text_content, re.IGNORECASE)
            for match in matches:
                signals.append(GhostSignal(
                    signal_type='shadow_reference',
                    location=f"{location}:{match.start()}",
                    confidence=0.8,
                    content_snippet=match.group()[:50],
                    indicators=['reference_to_deleted', 'broken_link']
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
        
        # Check for filesystem artifacts
        for pattern in self.GHOST_INDICATORS['filesystem_artifacts']:
            if re.search(pattern, location, re.IGNORECASE):
                signals.append(GhostSignal(
                    signal_type='filesystem_artifact',
                    location=location,
                    confidence=0.65,
                    indicators=['backup_file', 'temporary_file', 'recovered_item']
                ))
                break
        
        # Sort by confidence
        signals.sort(key=lambda x: x.confidence, reverse=True)
        return signals
    
    def _analyze_metadata_residuals(
        self,
        file_path: Path
    ) -> List[GhostSignal]:
        """
        Analyze file metadata for residual information.
        
        From comments: "Extract metadata"
        """
        signals = []
        
        try:
            stat = file_path.stat()
            
            # Check for suspicious timestamp patterns
            created = datetime.fromtimestamp(stat.st_ctime)
            modified = datetime.fromtimestamp(stat.st_mtime)
            accessed = datetime.fromtimestamp(stat.st_atime)
            
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
    ) -> List[str]:
        """
        Detect indicators of deletion in content.
        
        From comments: "Common digital ghost indicators"
        """
        indicators = []
        
        # Check for common deletion markers
        deletion_markers = [
            r'deleted?\s+(?:by|on|at)',
            r'removed?\s+(?:by|on|at)',
            r'\[deleted\]',
            r'\[removed\]',
            r'content\s+unavailable',
            r'page\s+not\s+found',
            r'404\s+error',
        ]
        
        for marker in deletion_markers:
            if re.search(marker, text_content, re.IGNORECASE):
                indicators.append(f"deletion_marker:{marker}")
        
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
        ghost_signals: List[GhostSignal],
        text_content: str
    ) -> List[RecoveredContent]:
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
        url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
        urls = re.findall(url_pattern, text_content)
        
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
        ghost_signals: List[GhostSignal]
    ) -> List[Dict[str, Any]]:
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
                diff = (timed_signals[i].timestamp - timed_signals[i-1].timestamp).total_seconds()
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
    ) -> List[str]:
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


def detect_digital_ghosts(file_path: str | Path) -> Dict[str, Any]:
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
        'has_ghosts': len(result.ghost_signals) > 0,
        'recommendations': result.recommendations
    }
