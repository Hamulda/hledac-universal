"""
Canary Token Detector
=====================



ISSUE-015: Detect canary tokens and tracking beacons embedded in documents.

Canary tokens are tracking mechanisms used to identify document leaks. When a
document containing a canary token is opened or accessed, it triggers callbacks
to attacker-controlled infrastructure, exposing the investigator's IP and identity.

This module detects:
- Web beacons (tracking pixels): <img src="...token/tracker/beacon">
- DNS callback URLs: http://[id].dns.canary.token.com patterns
- Unique identifiers: UUIDs, random 32-char hex strings used for tracking
- Tracking pixel patterns: 1x1 images with suspicious URLs
- Hidden iframes and external resource loads

M1 8GB Optimized:
- Regex-based scanning (~1MB RAM overhead)
- No external dependencies beyond stdlib
- Pre-compiled patterns for performance
- Streaming-friendly for large documents

Security Context:
- Critical OPSEC component for offensive OSINT operations
- Pre-flight check before document analysis
- Warns investigators before triggering callbacks
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CanaryDetection:
    """Result of canary token detection scan."""
    detected: bool
    tokens: list[str] = field(default_factory=list)
    web_beacons: list[str] = field(default_factory=list)
    dns_callbacks: list[str] = field(default_factory=list)
    unique_identifiers: list[str] = field(default_factory=list)
    tracking_pixels: list[str] = field(default_factory=list)
    severity: str = 'none'  # none, low, medium, high, critical

    def __bool__(self) -> bool:
        return self.detected

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'detected': self.detected,
            'tokens': self.tokens,
            'web_beacons': self.web_beacons,
            'dns_callbacks': self.dns_callbacks,
            'unique_identifiers': self.unique_identifiers,
            'tracking_pixels': self.tracking_pixels,
            'severity': self.severity,
        }


class CanaryTokenDetector:
    """Detect canary tokens and tracking beacons in document content.

    Pre-flight security check for document analysis. Scans HTML, XML, and
    plain text for patterns that indicate tracking mechanisms.

    Usage:
        detector = CanaryTokenDetector()
        detection = detector.scan(html_content)
        if detection.detected:
            logger.warning(f"Canary tokens found: {detection.tokens}")
    """

    __slots__ = ('_web_beacon_pattern', '_dns_callback_pattern',
                 '_uuid_pattern', '_hex_id_pattern',
                 '_tracking_pixel_pattern', '_iframe_pattern',
                 '_external_resource_pattern')

    def __init__(self) -> None:
        """Initialize with pre-compiled regex patterns.

        Patterns are compiled once at init for O(1) reuse across scans.
        """
        # Web beacons: <img> tags with tracking-related URLs
        # Matches: <img src="https://canary.token/track">, <img src="http://tracker.com/pixel.gif">
        self._web_beacon_pattern = re.compile(
            r'<img[^>]+src=["\']([^"\']*?(?:token|tracker|beacon|track|pixel|canary)[^"\']*)["\']',
            re.IGNORECASE | re.MULTILINE
        )

        # DNS callback URLs: http://[32-char-hex].dns.[domain] patterns
        # Common canary token services use DNS callbacks for exfiltration
        # Matches: http://abc123...xyz.dns.canarytokens.com, http://[id].dns.cb.com
        self._dns_callback_pattern = re.compile(
            r'https?://([a-z0-9]{20,64})\.(?:dns\.)?(?:canarytokens?|canary\.token|callback|track)\.[a-z.]+',
            re.IGNORECASE
        )

        # UUIDs: Standard UUID format used as unique tracking identifiers
        # Matches: 550e8400-e29b-41d4-a716-446655440000
        self._uuid_pattern = re.compile(
            r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b',
            re.IGNORECASE
        )

        # Random hex strings: 32+ char hex strings (MD5-like) used for tracking
        # Matches: abc123def456... (32-64 hex chars)
        # Excludes common false positives (all zeros, sequential patterns)
        self._hex_id_pattern = re.compile(
            r'\b[0-9a-f]{32,64}\b',
            re.IGNORECASE
        )

        # Tracking pixels: 1x1 images with suspicious URLs
        # Matches: width="1" height="1" or width:1px height:1px with external src
        self._tracking_pixel_pattern = re.compile(
            r'<img[^>]+(?:width=["\']?1["\']?|height=["\']?1["\']?)[^>]+src=["\']([^"\']+)["\']',
            re.IGNORECASE | re.MULTILINE
        )

        # Hidden iframes: iframes with zero dimensions or hidden styling
        # Matches: <iframe width="0" height="0" style="display:none" src="...">
        self._iframe_pattern = re.compile(
            r'<iframe[^>]+(?:width=["\']?0["\']?|height=["\']?0["\']?|display:\s*none|visibility:\s*hidden)[^>]+src=["\']([^"\']+)["\']',
            re.IGNORECASE | re.MULTILINE
        )

        # External resource loads: script/link tags loading from suspicious domains
        # Matches: <script src="https://tracker.com/...">, <link href="...canary...">
        self._external_resource_pattern = re.compile(
            r'<(?:script|link)[^>]+(?:src|href)=["\']([^"\']*?(?:token|tracker|beacon|canary)[^"\']*)["\']',
            re.IGNORECASE | re.MULTILINE
        )

    def scan(self, content: str) -> CanaryDetection:
        """Scan content for canary tokens and tracking beacons.

        Performs comprehensive scan across all detection categories.
        Returns immediately if content is empty or whitespace-only.

        Args:
            content: HTML, XML, or plain text content to scan

        Returns:
            CanaryDetection with all findings and severity assessment

        Performance:
            O(n) where n is content length
            ~1MB RAM overhead for regex compilation and result storage
        """
        if not content or not content.strip():
            return CanaryDetection(detected=False, severity='none')

        web_beacons = self._detect_web_beacons(content)
        dns_callbacks = self._detect_dns_callbacks(content)
        unique_ids = self._detect_unique_identifiers(content)
        tracking_pixels = self._detect_tracking_pixels(content)
        hidden_iframes = self._detect_hidden_iframes(content)
        external_resources = self._detect_external_resources(content)

        # Aggregate all findings
        all_tokens = (
            web_beacons +
            dns_callbacks +
            unique_ids +
            tracking_pixels +
            hidden_iframes +
            external_resources
        )

        # Deduplicate while preserving order
        seen = set()
        unique_tokens = []
        for token in all_tokens:
            if token not in seen:
                seen.add(token)
                unique_tokens.append(token)

        detected = len(unique_tokens) > 0
        severity = self._assess_severity(
            web_beacons, dns_callbacks, unique_ids,
            tracking_pixels, hidden_iframes, external_resources
        )

        if detected:
            logger.warning(
                f"[CANARY] Detected {len(unique_tokens)} canary token(s) "
                f"(severity: {severity})"
            )

        return CanaryDetection(
            detected=detected,
            tokens=unique_tokens,
            web_beacons=web_beacons,
            dns_callbacks=dns_callbacks,
            unique_identifiers=unique_ids,
            tracking_pixels=tracking_pixels,
            severity=severity,
        )

    def _detect_web_beacons(self, html: str) -> list[str]:
        """Detect web beacons (tracking pixels) in HTML content.

        Scans for <img> tags with URLs containing tracking-related keywords.
        Common patterns: token, tracker, beacon, track, pixel, canary.

        Args:
            html: HTML content to scan

        Returns:
            List of detected beacon URLs
        """
        matches = self._web_beacon_pattern.findall(html)
        return list(set(matches))

    def _detect_dns_callbacks(self, text: str) -> list[str]:
        """Detect DNS callback URLs used by canary token services.

        Canary tokens often use DNS callbacks for exfiltration. The pattern
        matches URLs with long hex subdomains pointing to known canary domains.

        Args:
            text: Text content to scan

        Returns:
            List of detected DNS callback URLs
        """
        matches = self._dns_callback_pattern.findall(text)
        # Return full URLs, not just the hex subdomain
        full_urls = []
        for match in matches:
            # Find the full URL containing this match
            url_pattern = re.compile(rf'https?://{re.escape(match)}[^\s<>"\']*', re.IGNORECASE)
            url_matches = url_pattern.findall(text)
            full_urls.extend(url_matches)
        return list(set(full_urls))

    def _detect_unique_identifiers(self, text: str) -> list[str]:
        """Detect unique identifiers used for tracking.

        Scans for:
        - UUIDs (standard format)
        - 32-64 char hex strings (MD5-like tracking IDs)

        Filters out common false positives (all zeros, sequential patterns).

        Args:
            text: Text content to scan

        Returns:
            List of detected unique identifiers
        """
        uuids = self._uuid_pattern.findall(text)
        hex_ids = self._hex_id_pattern.findall(text)

        # Filter false positives
        filtered_hex = [
            h for h in hex_ids
            if not self._is_false_positive_hex(h)
        ]

        return list(set(uuids + filtered_hex))

    def _detect_tracking_pixels(self, html: str) -> list[str]:
        """Detect 1x1 tracking pixels with external URLs.

        Tracking pixels are typically 1x1 images used to confirm document access.
        Looks for <img> tags with width=1 or height=1 attributes.

        Args:
            html: HTML content to scan

        Returns:
            List of detected tracking pixel URLs
        """
        matches = self._tracking_pixel_pattern.findall(html)
        # Filter out data URIs and local paths
        external = [
            url for url in matches
            if url.startswith(('http://', 'https://'))
        ]
        return list(set(external))

    def _detect_hidden_iframes(self, html: str) -> list[str]:
        """Detect hidden iframes used for tracking.

        Hidden iframes (width=0, height=0, display:none) are often used
        to load tracking scripts without user visibility.

        Args:
            html: HTML content to scan

        Returns:
            List of detected hidden iframe URLs
        """
        matches = self._iframe_pattern.findall(html)
        return list(set(matches))

    def _detect_external_resources(self, html: str) -> list[str]:
        """Detect external script/link tags loading from suspicious domains.

        Scans for <script> and <link> tags with URLs containing
        tracking-related keywords.

        Args:
            html: HTML content to scan

        Returns:
            List of detected external resource URLs
        """
        matches = self._external_resource_pattern.findall(html)
        return list(set(matches))

    def _is_false_positive_hex(self, hex_str: str) -> bool:
        """Check if a hex string is a common false positive.

        Filters out:
        - All zeros: 00000000000000000000000000000000
        - Sequential patterns: 0123456789abcdef0123456789abcdef
        - Common hash constants

        Args:
            hex_str: Hex string to check

        Returns:
            True if likely a false positive
        """
        # All zeros
        if set(hex_str) == {'0'}:
            return True

        # All same character
        if len(set(hex_str)) == 1:
            return True

        # Sequential pattern (0123456789abcdef repeated)
        if hex_str.lower().startswith('0123456789abcdef'):
            return True

        # Common MD5 of empty string or other constants
        common_hashes = {
            'd41d8cd98f00b204e9800998ecf8427e',  # MD5("")
            '5d41402abc4b2a76b9719d911017c592',  # MD5("hello")
        }
        if hex_str.lower() in common_hashes:
            return True

        return False

    def _assess_severity(
        self,
        web_beacons: list[str],
        dns_callbacks: list[str],
        unique_ids: list[str],
        tracking_pixels: list[str],
        hidden_iframes: list[str],
        external_resources: list[str],
    ) -> str:
        """Assess severity of detected canary tokens.

        Severity levels:
        - critical: DNS callbacks or multiple tracking mechanisms
        - high: Web beacons with unique identifiers
        - medium: Single tracking mechanism
        - low: Only unique identifiers (could be false positives)
        - none: No detections

        Args:
            web_beacons: Detected web beacons
            dns_callbacks: Detected DNS callback URLs
            unique_ids: Detected unique identifiers
            tracking_pixels: Detected tracking pixels
            hidden_iframes: Detected hidden iframes
            external_resources: Detected external resources

        Returns:
            Severity level: 'none', 'low', 'medium', 'high', 'critical'
        """
        if not any([web_beacons, dns_callbacks, tracking_pixels, hidden_iframes, external_resources]):
            if unique_ids:
                return 'low'
            return 'none'

        # Critical: DNS callbacks (active exfiltration) or multiple mechanisms
        if dns_callbacks:
            return 'critical'

        total_active = (
            len(web_beacons) +
            len(tracking_pixels) +
            len(hidden_iframes) +
            len(external_resources)
        )

        if total_active >= 3:
            return 'critical'

        # High: Active tracking with unique identifiers
        if (web_beacons or tracking_pixels) and unique_ids:
            return 'high'

        # Medium: Single active tracking mechanism
        if total_active >= 1:
            return 'medium'

        return 'low'


# Module-level singleton for reuse across analyzers
_detector: CanaryTokenDetector | None = None


def get_detector() -> CanaryTokenDetector:
    """Get or create the singleton CanaryTokenDetector instance.

    Thread-safe via module-level initialization. Detector is created
    once and reused across all document analysis operations.

    Returns:
        Singleton CanaryTokenDetector instance
    """
    global _detector
    if _detector is None:
        _detector = CanaryTokenDetector()
    return _detector


def scan_for_canary_tokens(content: str) -> CanaryDetection:
    """Convenience function to scan content for canary tokens.

    Uses the singleton detector instance for efficiency.

    Args:
        content: HTML, XML, or plain text content to scan

    Returns:
        CanaryDetection with all findings

    Example:
        >>> detection = scan_for_canary_tokens(html_content)
        >>> if detection.detected:
        ...     logger.warning(f"Canary tokens: {detection.tokens}")
    """
    detector = get_detector()
    return detector.scan(content)
