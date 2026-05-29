"""
Research Obfuscation - Maskování výzkumných aktivit

Pro ultra-deep research v tajných databázích:
- Query masking (transformace citlivých termínů)
- Chaff traffic generation (falešné dotazy)
- Timing obfuscation
- Research pattern disruption
- Plausible deniability
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ObfuscationConfig:
    """Konfigurace obfuskace"""
    # Úrovně obfuskace
    mask_queries: bool = True
    generate_chaff: bool = True
    disrupt_patterns: bool = True
    timing_jitter: bool = True

    # Chaff generace
    chaff_ratio: float = 0.3  # 30% falešného provozu
    chaff_topics: list[str] = field(default_factory=list)

    # Timing
    jitter_range: float = 0.5  # +/- 50%
    min_delay: float = 1.0
    max_delay: float = 5.0

    # Plausible deniability
    cover_topics: list[str] = field(default_factory=lambda: [
        "weather forecast",
        "sports news",
        "recipe ideas",
        "movie reviews",
        "travel destinations",
        "technology news",
        "stock market",
        "health tips",
    ])

    # Semantic masking
    use_synonyms: bool = True
    use_generalization: bool = True


class ResearchObfuscator:
    """
    Obfuskátor výzkumných aktivit.

    Skrývá skutečný předmět výzkumu před:
    - ISP monitoring
    - Search engine profiling
    - Network analysis
    - Metadata collection

    Example:
        >>> obf = ResearchObfuscator()
        >>> masked = obf.mask_query("competitive intelligence Acme Corp")
        'market research technology company'
        >>> chaff = obf.generate_chaff_queries("secret government project", count=5)
    """

    # Mapování citlivých termínů
    SENSITIVE_MAPPINGS = {
        # Espionage/Intelligence
        'competitive intelligence': 'market research',
        'corporate espionage': 'industry analysis',
        'trade secret': 'proprietary method',
        'industrial spy': 'competitor analyst',

        # Government/Military
        'classified': 'restricted access',
        'top secret': 'confidential',
        'intelligence agency': 'government organization',
        'surveillance': 'monitoring',
        'covert operation': 'special project',

        # Financial Crimes
        'money laundering': 'transaction analysis',
        'financial fraud': 'accounting irregularities',
        'insider trading': 'market activity',
        'tax evasion': 'tax optimization',

        # Cyber
        'hacking': 'security testing',
        'data breach': 'information disclosure',
        'exploit': 'vulnerability',
        'backdoor': 'access mechanism',
        'zero-day': 'security flaw',

        # Legal/Ethical
        'illegal': 'unauthorized',
        'criminal': 'suspicious',
        'underground': 'alternative',
        'black market': 'informal economy',

        # Sensitive Topics
        'banned': 'restricted',
        'censored': 'filtered',
        'suppressed': 'limited access',
        'conspiracy': 'alternative theory',
        'whistleblower': 'informant',
    }

    # Synonyma pro obfuskaci
    SYNONYMS = {
        'research': ['study', 'analysis', 'investigation', 'review', 'survey'],
        'data': ['information', 'records', 'files', 'documents', 'content'],
        'find': ['locate', 'identify', 'discover', 'obtain', 'access'],
        'secret': ['private', 'confidential', 'restricted', 'classified', 'hidden'],
        'steal': ['acquire', 'obtain', 'access', 'extract', 'copy'],
    }

    def __init__(self, config: ObfuscationConfig = None):
        self.config = config or ObfuscationConfig()
        self._query_history = []
        self._chaff_queries_generated = 0

    def mask_query(self, query: str, strength: str = 'medium') -> str:
        """
        Maskovat citlivý dotaz.

        Args:
            query: Původní dotaz
            strength: Síla maskování ('low', 'medium', 'high')

        Returns:
            Maskovaný dotaz
        """
        masked = query.lower()

        # Nahradit citlivé termíny
        if self.config.mask_queries:
            for sensitive, replacement in self.SENSITIVE_MAPPINGS.items():
                if strength == 'high' or (strength == 'medium' and random.random() > 0.3):
                    masked = masked.replace(sensitive.lower(), replacement)

        # Použít synonyma
        if self.config.use_synonyms:
            words = masked.split()
            new_words = []
            for word in words:
                if word in self.SYNONYMS and random.random() > 0.5:
                    new_words.append(random.choice(self.SYNONYMS[word]))
                else:
                    new_words.append(word)
            masked = ' '.join(new_words)

        # Generalizace
        if self.config.use_generalization and strength == 'high':
            masked = self._generalize(masked)

        return masked

    def _generalize(self, query: str) -> str:
        """Generalizovat specifické termíny"""
        # Odstranit specifická jména
        import re

        # Nahradit konkrétní názvy obecnými
        query = re.sub(r'\b[A-Z][a-z]+ (Corp|Inc|Ltd|Company)\b', 'company', query)
        query = re.sub(r'\b[A-Z][a-zA-Z]+ (Agency|Bureau|Department)\b', 'organization', query)

        return query

    def generate_chaff_queries(
        self,
        original_query: str,
        count: int = 5
    ) -> list[str]:
        """
        Generovat falešné dotazy pro zamaskování skutečného výzkumu.

        Args:
            original_query: Skutečný dotaz (pro generování souvisejících chaff)
            count: Počet falešných dotazů

        Returns:
            Seznam falešných dotazů
        """
        chaff = []

        # 1. Náhodné obecné dotazy
        general_chaff = [
            "weather today",
            "news headlines",
            "recipe pasta",
            "movie ratings",
            "sports scores",
            "stock prices",
            "travel destinations",
            "health tips",
            "technology news",
            "book reviews",
        ]

        # 2. Související chaff (na základě původního dotazu)
        related_chaff = self._generate_related_chaff(original_query)

        # 3. Cover topics
        cover_chaff = self.config.cover_topics

        # Kombinovat a vybrat
        all_chaff = general_chaff + related_chaff + cover_chaff

        for _ in range(count):
            if all_chaff:
                query = random.choice(all_chaff)
                # Přidat timestamp pro unikátnost
                query = f"{query} {datetime.now().strftime('%H:%M')}"
                chaff.append(query)
                self._chaff_queries_generated += 1

        return chaff

    def _generate_related_chaff(self, original_query: str) -> list[str]:
        """Generovat související chaff na základě původního dotazu"""
        # Extrahovat klíčová slova
        words = original_query.lower().split()

        # Generovat varianty
        chaff = []

        # Přidat obecné prefixy/sufixy
        prefixes = ['about', 'information on', 'news about', 'updates on']
        for word in words[:3]:  # První 3 slova
            for prefix in prefixes:
                chaff.append(f"{prefix} {word}")

        return chaff

    async def execute_with_chaff(
        self,
        real_query: str,
        execute_func,
        chaff_count: int = None
    ) -> Any:
        """
        Vykonat dotaz s chaff provozem.

        Args:
            real_query: Skutečný dotaz
            execute_func: Funkce pro vykonání dotazu
            chaff_count: Počet chaff dotazů (default z config)

        Returns:
            Výsledek skutečného dotazu
        """
        if not self.config.generate_chaff:
            return await execute_func(real_query)

        count = chaff_count or int(self.config.chaff_ratio * 10)

        # Generovat chaff
        chaff_queries = self.generate_chaff_queries(real_query, count)

        # Vykonat všechny dotazy (chaff + real)
        all_queries = chaff_queries + [real_query]

        # Zamíchat
        random.shuffle(all_queries)

        results = []
        real_result = None

        for query in all_queries:
            # Timing jitter
            if self.config.timing_jitter:
                delay = self.config.min_delay + random.uniform(
                    0, self.config.max_delay - self.config.min_delay
                )
                await asyncio.sleep(delay)

            # Vykonat
            result = await execute_func(query)

            if query == real_query:
                real_result = result
            else:
                results.append(result)

        logger.info(f"Executed {len(chaff_queries)} chaff queries + 1 real query")

        return real_result

    def disrupt_timing(self, base_delay: float) -> float:
        """
        Narušit timing pattern.

        Args:
            base_delay: Základní delay

        Returns:
            Modifikovaný delay
        """
        if not self.config.timing_jitter:
            return base_delay

        jitter = base_delay * self.config.jitter_range
        return base_delay + random.uniform(-jitter, jitter)

    def get_stats(self) -> dict[str, Any]:
        """Získat statistiky obfuskace"""
        return {
            "queries_masked": len(self._query_history),
            "chaff_generated": self._chaff_queries_generated,
            "config": {
                "mask_queries": self.config.mask_queries,
                "generate_chaff": self.config.generate_chaff,
                "timing_jitter": self.config.timing_jitter,
                "chaff_ratio": self.config.chaff_ratio,
            },
        }
