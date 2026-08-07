"""
🔄 ALTERNATIVA - Regex-based Entity Extractor (RegexHunter)
===========================================================



Toto je ALTERNATIVNÍ implementace entity extraction založená na regexech.

Pro plnohodnotné NER použijte CANONICAL verzi:
    from hledac.universal.brain.ner_engine import NEREngine

Tento modul je vhodný pro:
- Rychlou extrakci známých patternů (emaily, crypto adresy, API keys)
- Případy kde není potřeba ML-based NER
- Nízkou latenci (bez načítání ML modelu)

Features:
    - Email address extraction
    - Cryptocurrency address detection (BTC, ETH, XMR)
    - API key heuristic detection (AWS, Google, Stripe)
    - IP address and onion link detection
    - Critical data warnings for private keys and passwords

Example:
    >>> extractor = EntityExtractor()
    >>> entities = extractor.extract_all("Contact: user@example.com")
    >>> print(entities[0].pattern_type)
    PatternType.EMAIL
"""
import logging
import re
from dataclasses import dataclass
import msgspec
from enum import Enum
from typing import Any
from operator import attrgetter, itemgetter
logger = logging.getLogger(__name__)

class PatternType(Enum):
    """Pattern types for entity extraction."""
    EMAIL = 'email'
    BTC_ADDRESS = 'btc_address'
    ETH_ADDRESS = 'eth_address'
    XMR_ADDRESS = 'xmr_address'
    AWS_KEY = 'aws_key'
    GOOGLE_KEY = 'google_key'
    STRIPE_KEY = 'stripe_key'
    IP_ADDRESS = 'ip_address'
    ONION_LINK = 'onion_link'
    PRIVATE_KEY = 'private_key'
    PASSWORD = 'password'
    API_KEY_GENERIC = 'api_key_generic'
    URL = 'url'
    PHONE = 'phone'

class ExtractedEntity(msgspec.Struct, gc=False):
    """Extracted entity with metadata."""
    pattern_type: PatternType
    value: str
    confidence: float
    context: str
    line_number: int | None = None
    start_pos: int | None = None
    end_pos: int | None = None

class EntityExtractor:
    """
    Advanced regex-based entity extraction.

    Extracts emails, crypto addresses, API keys, and other sensitive data
    from text using optimized regex patterns.
    """
    __slots__ = tuple(('_stats', 'patterns'))

    def __init__(self):
        """Initialize regex patterns for entity extraction."""
        self._compile_patterns()
        self._stats: dict[str, Any] = {'total_scans': 0, 'entities_found': 0, 'critical_findings': 0, 'pattern_counts': {pt.value: 0 for pt in PatternType}}

    def _compile_patterns(self) -> None:
        """Compile all regex patterns for efficient matching."""
        self.patterns = {PatternType.EMAIL: re.compile('\\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Z|a-z]{2,}\\b', re.IGNORECASE), PatternType.BTC_ADDRESS: re.compile('\\b(?:1|3)[a-km-zA-HJ-NP-Z1-9]{25,34}\\b|\\bbc1[a-z0-9]{39,59}\\b'), PatternType.ETH_ADDRESS: re.compile('\\b0x[a-fA-F0-9]{40}\\b'), PatternType.XMR_ADDRESS: re.compile('\\b4[0-9AB][1-9A-HJ-NP-Za-km-z]{93}\\b'), PatternType.AWS_KEY: re.compile('\\bAKIA[0-9A-Z]{16}\\b|\\bASIA[0-9A-Z]{16}\\b'), PatternType.GOOGLE_KEY: re.compile('\\bAIza[0-9A-Za-z_-]{35}\\b'), PatternType.STRIPE_KEY: re.compile('\\bsk_live_[0-9a-zA-Z]{24,}\\b|\\bpk_live_[0-9a-zA-Z]{24,}\\b'), PatternType.IP_ADDRESS: re.compile('\\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\\b'), PatternType.ONION_LINK: re.compile('\\b[a-z2-7]{16}\\.onion\\b|\\b[a-z2-7]{56}\\.onion\\b'), PatternType.PRIVATE_KEY: re.compile('-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----'), PatternType.PASSWORD: re.compile('(?i)(?:password|passwd|pwd)\\s*[=:]\\s*["\\\']?([^"\\\'\\s]+)', re.IGNORECASE), PatternType.API_KEY_GENERIC: re.compile('\\b(?:api[_-]?key|apikey)\\s*[=:]\\s*["\\\']?([a-zA-Z0-9_-]{16,})["\\\']?', re.IGNORECASE), PatternType.URL: re.compile('https?://(?:[-\\w.])+(?:[:\\d]+)?(?:/(?:[\\w/_.])*(?:\\?(?:[\\w&=%.])*)?(?:#(?:[\\w.])*)?)?'), PatternType.PHONE: re.compile('\\b(?:\\+?1[-.\\s]?)?\\(?[0-9]{3}\\)?[-.\\s]?[0-9]{3}[-.\\s]?[0-9]{4}\\b')}

    def extract_all(self, text: str, context_chars: int=30) -> list[ExtractedEntity]:
        """
        Extract all entity types from text.

        Args:
            text: Text to analyze
            context_chars: Number of context characters around match

        Returns:
            List of extracted entities
        """
        entities = []
        self._stats['total_scans'] = int(self._stats.get('total_scans', 0)) + 1
        for pattern_type, pattern in self.patterns.items():
            for match in pattern.finditer(text):
                value = match.group(0)
                confidence = self._calculate_confidence(pattern_type, value)
                start = max(0, match.start() - context_chars)
                end = min(len(text), match.end() + context_chars)
                context = text[start:end]
                line_number = text[:match.start()].count('\n') + 1
                entity = ExtractedEntity(pattern_type=pattern_type, value=value, confidence=confidence, context=context, line_number=line_number, start_pos=match.start(), end_pos=match.end())
                entities.append(entity)
                self._stats['entities_found'] = int(self._stats.get('entities_found', 0)) + 1
                pc = self._stats.get('pattern_counts', {})
                if isinstance(pc, dict):
                    pc[pattern_type.value] = int(pc.get(pattern_type.value, 0)) + 1
                self._stats['pattern_counts'] = pc
                if pattern_type in [PatternType.PRIVATE_KEY, PatternType.PASSWORD]:
                    self._stats['critical_findings'] = int(self._stats.get('critical_findings', 0)) + 1
        entities.sort(key=attrgetter("start_pos") or 0)
        return entities

    def extract_by_type(self, text: str, pattern_type: PatternType, context_chars: int=30) -> list[ExtractedEntity]:
        """
        Extract specific entity type from text.

        Args:
            text: Text to analyze
            pattern_type: Type of pattern to extract
            context_chars: Number of context characters around match

        Returns:
            List of extracted entities of specified type
        """
        entities = []
        if pattern_type not in self.patterns:
            return entities
        pattern = self.patterns[pattern_type]
        for match in pattern.finditer(text):
            value = match.group(0)
            confidence = self._calculate_confidence(pattern_type, value)
            start = max(0, match.start() - context_chars)
            end = min(len(text), match.end() + context_chars)
            context = text[start:end]
            line_number = text[:match.start()].count('\n') + 1
            entity = ExtractedEntity(pattern_type=pattern_type, value=value, confidence=confidence, context=context, line_number=line_number, start_pos=match.start(), end_pos=match.end())
            entities.append(entity)
        return entities

    def _calculate_confidence(self, pattern_type: PatternType, value: str) -> float:
        """Calculate confidence score for a match."""
        confidence = 0.8
        if pattern_type == PatternType.EMAIL:
            if '.' in value.split('@')[-1]:
                confidence = 0.95
        elif pattern_type == PatternType.BTC_ADDRESS:
            if value.startswith('bc1'):
                confidence = 0.9
            else:
                confidence = 0.85
        elif pattern_type == PatternType.ETH_ADDRESS:
            if len(value) == 42:
                confidence = 0.9
        elif pattern_type == PatternType.PRIVATE_KEY:
            confidence = 0.99
        return min(confidence, 1.0)

    def has_critical_data(self, text: str) -> tuple[bool, list[ExtractedEntity]]:
        """
        Check if text contains critical/sensitive data.

        Args:
            text: Text to analyze

        Returns:
            Tuple of (has_critical, critical_entities)
        """
        critical_types = {PatternType.PRIVATE_KEY, PatternType.PASSWORD, PatternType.AWS_KEY, PatternType.GOOGLE_KEY, PatternType.STRIPE_KEY, PatternType.API_KEY_GENERIC}
        all_entities = self.extract_all(text)
        critical = [e for e in all_entities if e.pattern_type in critical_types]
        return (bool(critical), critical)

    def get_stats(self) -> dict[str, Any]:
        """Get extraction statistics."""
        return self._stats.copy()

    def reset_stats(self) -> None:
        """Reset extraction statistics."""
        self._stats = {'total_scans': 0, 'entities_found': 0, 'critical_findings': 0, 'pattern_counts': {pt.value: 0 for pt in PatternType}}
__all__ = ['PatternType', 'ExtractedEntity', 'EntityExtractor']