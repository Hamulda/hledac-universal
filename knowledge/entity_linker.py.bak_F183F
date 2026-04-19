"""
EntityLinker - Wikidata-based Entity Linking and Disambiguation
===============================================================

Lightweight entity linking system optimized for M1 MacBook Air (8GB RAM).
Links extracted entities to Wikidata using SPARQL queries with context-aware
disambiguation and caching.

Key Features:
    - Async HTTP requests to Wikidata SPARQL endpoint
    - Context-aware candidate ranking using semantic similarity
    - Response caching for repeated queries
    - Batch processing support
    - Integration with GLiNER for NER (if available) or regex fallback
    - M1 8GB RAM optimized (no heavy ML models)

Dependencies:
    - aiohttp (async HTTP requests)
    - rapidfuzz (fuzzy string matching)
    - Optional: SPARQLWrapper

Example:
    linker = EntityLinker()
    entities = await linker.link_entities(
        text="Apple was founded by Steve Jobs in California.",
        context="Technology companies and their founders"
    )
    for entity in entities:
        print(f"{entity.original_text} -> {entity.canonical_label} ({entity.canonical_id})")
"""

import asyncio
import hashlib
import importlib.util
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from urllib.parse import quote

logger = logging.getLogger(__name__)

# Optional imports with fallback
aiohttp = None
try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    logger.warning("aiohttp not available. Install with: pip install aiohttp")

rapidfuzz = None
try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    logger.warning("rapidfuzz not available. Install with: pip install rapidfuzz")

# GLiNER optional import - lazy to avoid circular imports
GLINER_AVAILABLE = False
GLiNER = None
try:
    # Test if gliner can be imported without triggering circular imports
    import importlib
    spec = importlib.util.find_spec("gliner")
    if spec is not None:
        # Mark as potentially available, actual import happens lazily
        GLINER_AVAILABLE = True
    else:
        GLINER_AVAILABLE = False
        logger.debug("GLiNER not found")
except Exception:
    GLINER_AVAILABLE = False
    logger.debug("GLiNER check failed, using fallback NER")


@dataclass
class EntityCandidate:
    """
    Represents a candidate entity from Wikidata.

    Attributes:
        entity_text: The original text that was matched
        wikidata_id: Wikidata Q-ID (e.g., "Q312" for Apple Inc.)
        label: Canonical label from Wikidata
        description: Entity description from Wikidata
        types: List of entity types (P31 instance of)
        context_score: Semantic similarity to context (0-1)
        popularity_score: Popularity based on sitelinks (0-1)
        final_score: Combined ranking score (0-1)
    """
    entity_text: str
    wikidata_id: str
    label: str
    description: str
    types: List[str] = field(default_factory=list)
    context_score: float = 0.0
    popularity_score: float = 0.0
    final_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'entity_text': self.entity_text,
            'wikidata_id': self.wikidata_id,
            'label': self.label,
            'description': self.description,
            'types': self.types,
            'context_score': self.context_score,
            'popularity_score': self.popularity_score,
            'final_score': self.final_score
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'EntityCandidate':
        """Create from dictionary."""
        return cls(
            entity_text=data['entity_text'],
            wikidata_id=data['wikidata_id'],
            label=data['label'],
            description=data['description'],
            types=data.get('types', []),
            context_score=data.get('context_score', 0.0),
            popularity_score=data.get('popularity_score', 0.0),
            final_score=data.get('final_score', 0.0)
        )


@dataclass
class LinkedEntity:
    """
    Represents a successfully linked entity.

    Attributes:
        original_text: Text as it appeared in the input
        start_pos: Start position in the original text
        end_pos: End position in the original text
        canonical_id: Wikidata Q-ID
        canonical_label: Canonical label from Wikidata
        entity_type: Entity type/category
        confidence: Linking confidence score (0-1)
        candidates_considered: Number of candidates evaluated
    """
    original_text: str
    start_pos: int
    end_pos: int
    canonical_id: str
    canonical_label: str
    entity_type: str
    confidence: float
    candidates_considered: int

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'original_text': self.original_text,
            'start_pos': self.start_pos,
            'end_pos': self.end_pos,
            'canonical_id': self.canonical_id,
            'canonical_label': self.canonical_label,
            'entity_type': self.entity_type,
            'confidence': self.confidence,
            'candidates_considered': self.candidates_considered
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'LinkedEntity':
        """Create from dictionary."""
        return cls(
            original_text=data['original_text'],
            start_pos=data['start_pos'],
            end_pos=data['end_pos'],
            canonical_id=data['canonical_id'],
            canonical_label=data['canonical_label'],
            entity_type=data['entity_type'],
            confidence=data['confidence'],
            candidates_considered=data['candidates_considered']
        )


class SimpleCache:
    """
    Simple in-memory cache with TTL for Wikidata responses.
    M1 8GB optimized - limited size with LRU eviction.
    """

    def __init__(self, max_size: int = 1000, ttl_seconds: int = 3600):
        """
        Initialize cache.

        Args:
            max_size: Maximum number of entries
            ttl_seconds: Time-to-live in seconds
        """
        self.max_size = max_size
        self.ttl = timedelta(seconds=ttl_seconds)
        self._cache: Dict[str, Tuple[Any, datetime]] = {}
        self._access_order: List[str] = []

    def _generate_key(self, query: str) -> str:
        """Generate cache key from query."""
        return hashlib.sha256(query.encode('utf-8')).hexdigest()[:16]

    def get(self, query: str) -> Optional[Any]:
        """Get cached value if not expired."""
        key = self._generate_key(query)

        if key not in self._cache:
            return None

        value, timestamp = self._cache[key]

        # Check expiration
        if datetime.utcnow() - timestamp > self.ttl:
            del self._cache[key]
            if key in self._access_order:
                self._access_order.remove(key)
            return None

        # Update access order (LRU)
        if key in self._access_order:
            self._access_order.remove(key)
        self._access_order.append(key)

        return value

    def set(self, query: str, value: Any):
        """Cache value with timestamp."""
        key = self._generate_key(query)

        # Evict oldest if at capacity
        if len(self._cache) >= self.max_size and key not in self._cache:
            oldest_key = self._access_order.pop(0)
            if oldest_key in self._cache:
                del self._cache[oldest_key]

        self._cache[key] = (value, datetime.utcnow())

        # Update access order
        if key in self._access_order:
            self._access_order.remove(key)
        self._access_order.append(key)

    def clear(self):
        """Clear all cached entries."""
        self._cache.clear()
        self._access_order.clear()

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return {
            'size': len(self._cache),
            'max_size': self.max_size,
            'ttl_seconds': self.ttl.total_seconds()
        }


class EntityLinker:
    """
    Wikidata-based entity linker with context-aware disambiguation.

    Optimized for M1 8GB RAM:
        - Async HTTP requests (non-blocking)
        - Response caching (reduces API calls)
        - Batch processing support
        - No heavy ML models (uses lightweight similarity)

    Usage:
        linker = EntityLinker()
        entities = await linker.link_entities("Apple was founded by Steve Jobs")
    """

    DEFAULT_WIKIDATA_ENDPOINT = "https://query.wikidata.org/sparql"
    DEFAULT_USER_AGENT = "HledacEntityLinker/1.0 (M1-Optimized; research tool)"

    # Entity type mapping from Wikidata P31
    TYPE_MAPPING = {
        'Q5': 'PERSON',           # human
        'Q43229': 'ORGANIZATION', # organization
        'Q4830453': 'BUSINESS',   # business
        'Q515': 'CITY',           # city
        'Q6256': 'COUNTRY',       # country
        'Q1656682': 'EVENT',      # event
        'Q571': 'BOOK',           # book
        'Q11424': 'FILM',         # film
        'Q7397': 'SOFTWARE',      # software
        'Q811165': 'PRODUCT',     # product
        'Q5': 'PERSON',
        'Q95074': 'CHARACTER',    # fictional character
        'Q488383': 'LOCATION',    # location
        'Q618123': 'LOCATION',    # geographical feature
        'Q15401930': 'PRODUCT',   # product
        'Q12737077': 'OCCUPATION', # occupation
        'Q4164871': 'POSITION',   # position
        'Q18616576': 'AWARD',     # award
        'Q5': 'PERSON',
    }

    def __init__(
        self,
        wikidata_endpoint: str = DEFAULT_WIKIDATA_ENDPOINT,
        cache_size: int = 1000,
        cache_ttl: int = 3600,
        max_candidates: int = 10,
        confidence_threshold: float = 0.5,
        request_timeout: int = 30,
        use_gliner: bool = True
    ):
        """
        Initialize EntityLinker.

        Args:
            wikidata_endpoint: SPARQL endpoint URL
            cache_size: Maximum cache entries
            cache_ttl: Cache TTL in seconds
            max_candidates: Maximum candidates to fetch per entity
            confidence_threshold: Minimum confidence for linking
            request_timeout: HTTP request timeout in seconds
            use_gliner: Whether to use GLiNER for NER if available
        """
        self.wikidata_endpoint = wikidata_endpoint
        self.max_candidates = max_candidates
        self.confidence_threshold = confidence_threshold
        self.request_timeout = request_timeout
        self.use_gliner = use_gliner and GLINER_AVAILABLE

        # Initialize cache
        self._cache = SimpleCache(max_size=cache_size, ttl_seconds=cache_ttl)

        # Initialize HTTP session (lazy)
        self._session: Optional[Any] = None

        # Initialize GLiNER (lazy, if available)
        self._gliner_model: Optional[Any] = None

        # Fallback NER patterns
        self._init_ner_patterns()

        logger.info(f"EntityLinker initialized (GLiNER: {self.use_gliner})")

    def _init_ner_patterns(self):
        """Initialize regex patterns for fallback NER."""
        self._ner_patterns = {
            'PERSON': [
                re.compile(r'\b[A-Z][a-z]+\s+[A-Z][a-z]+\b'),  # First Last
                re.compile(r'\b(?:Mr\.?|Mrs\.?|Ms\.?|Dr\.?)\s+[A-Z][a-z]+\b', re.IGNORECASE),
            ],
            'ORGANIZATION': [
                re.compile(r'\b[A-Z][a-z]*\s+(?:Inc\.?|Corp\.?|Ltd\.?|LLC|Company|Co\.)\b'),
                re.compile(r'\b(?:Apple|Google|Microsoft|Amazon|Facebook|Meta|Twitter|X)\b'),
            ],
            'LOCATION': [
                re.compile(r'\b(?:in|at|from)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b'),
            ],
        }

    async def _get_session(self) -> Optional[Any]:
        """Get or create aiohttp session."""
        if not AIOHTTP_AVAILABLE:
            return None

        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.request_timeout)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers={'User-Agent': self.DEFAULT_USER_AGENT}
            )
        return self._session

    def _load_gliner(self):
        """Lazy load GLiNER model."""
        if self.use_gliner and self._gliner_model is None:
            try:
                # Lazy import to avoid circular imports
                from gliner import GLiNER as GLiNERClass
                self._gliner_model = GLiNERClass.from_pretrained("urchade/gliner_medium-v2.1")
                logger.info("GLiNER model loaded")
            except Exception as e:
                logger.warning(f"Failed to load GLiNER: {e}")
                self.use_gliner = False

    def _extract_entities_fallback(self, text: str) -> List[Tuple[str, int, int, str]]:
        """
        Extract entities using regex patterns (fallback when GLiNER unavailable).

        Returns:
            List of (entity_text, start, end, entity_type) tuples
        """
        entities = []
        seen_spans: Set[Tuple[int, int]] = set()

        for entity_type, patterns in self._ner_patterns.items():
            for pattern in patterns:
                for match in pattern.finditer(text):
                    start, end = match.span()

                    # Skip if overlapping with existing entity
                    if any(start < e and end > s for s, e in seen_spans):
                        continue

                    entity_text = match.group(1) if match.groups() else match.group()
                    entities.append((entity_text, start, end, entity_type))
                    seen_spans.add((start, end))

        # Sort by position
        entities.sort(key=lambda x: x[1])
        return entities

    def _extract_entities_gliner(self, text: str) -> List[Tuple[str, int, int, str]]:
        """
        Extract entities using GLiNER.

        Returns:
            List of (entity_text, start, end, entity_type) tuples
        """
        self._load_gliner()

        if self._gliner_model is None:
            return self._extract_entities_fallback(text)

        try:
            labels = ["PERSON", "ORGANIZATION", "LOCATION", "EVENT", "PRODUCT"]
            entities = self._gliner_model.predict_entities(text, labels, threshold=0.5)

            return [
                (e['text'], e['start'], e['end'], e['label'])
                for e in entities
            ]
        except Exception as e:
            logger.warning(f"GLiNER extraction failed: {e}")
            return self._extract_entities_fallback(text)

    def _build_sparql_query(self, entity_text: str, limit: int = 10) -> str:
        """
        Build SPARQL query for entity search.

        Args:
            entity_text: Text to search for
            limit: Maximum results

        Returns:
            SPARQL query string
        """
        # Escape special characters
        escaped = entity_text.replace('"', '\\"')

        query = f"""
        SELECT DISTINCT ?item ?itemLabel ?itemDescription ?sitelinks
               (GROUP_CONCAT(DISTINCT ?typeLabel; separator=", ") AS ?types)
        WHERE {{
          ?item rdfs:label ?label .
          FILTER(LANG(?label) = "en")
          FILTER(CONTAINS(LCASE(STR(?label)), LCASE("{escaped}")))

          OPTIONAL {{ ?item wdt:P31 ?type . ?type rdfs:label ?typeLabel . FILTER(LANG(?typeLabel) = "en") }}
          OPTIONAL {{ ?item wikibase:sitelinks ?sitelinks }}

          SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" }}
        }}
        GROUP BY ?item ?itemLabel ?itemDescription ?sitelinks
        ORDER BY DESC(?sitelinks) DESC(STRLEN(?itemLabel))
        LIMIT {limit}
        """
        return query

    async def query_wikidata(self, entity_text: str) -> List[EntityCandidate]:
        """
        Query Wikidata for entity candidates.

        Args:
            entity_text: Entity text to search

        Returns:
            List of EntityCandidate objects
        """
        # Check cache first
        cached = self._cache.get(entity_text)
        if cached is not None:
            logger.debug(f"Cache hit for: {entity_text}")
            return [EntityCandidate.from_dict(c) for c in cached]

        if not AIOHTTP_AVAILABLE:
            logger.warning("aiohttp not available, cannot query Wikidata")
            return []

        session = await self._get_session()
        if session is None:
            return []

        query = self._build_sparql_query(entity_text, self.max_candidates)

        try:
            params = {'query': query, 'format': 'json'}

            async with session.get(self.wikidata_endpoint, params=params) as response:
                if response.status != 200:
                    logger.warning(f"Wikidata query failed: {response.status}")
                    return []

                data = await response.json()
                candidates = self._parse_sparql_results(entity_text, data)

                # Cache results
                self._cache.set(entity_text, [c.to_dict() for c in candidates])

                return candidates

        except asyncio.TimeoutError:
            logger.warning(f"Timeout querying Wikidata for: {entity_text}")
            return []
        except Exception as e:
            logger.warning(f"Error querying Wikidata: {e}")
            return []

    def _parse_sparql_results(
        self,
        entity_text: str,
        data: Dict[str, Any]
    ) -> List[EntityCandidate]:
        """
        Parse SPARQL results into EntityCandidate objects.

        Args:
            entity_text: Original entity text
            data: SPARQL JSON response

        Returns:
            List of EntityCandidate objects
        """
        candidates = []
        bindings = data.get('results', {}).get('bindings', [])

        # Calculate max sitelinks for normalization
        max_sitelinks = 1
        for binding in bindings:
            sitelinks_str = binding.get('sitelinks', {}).get('value', '0')
            try:
                sitelinks = int(sitelinks_str) if sitelinks_str else 0
                max_sitelinks = max(max_sitelinks, sitelinks)
            except ValueError:
                pass

        for binding in bindings:
            try:
                item_uri = binding.get('item', {}).get('value', '')
                wikidata_id = item_uri.split('/')[-1] if item_uri else ''

                label = binding.get('itemLabel', {}).get('value', '')
                description = binding.get('itemDescription', {}).get('value', '')

                types_str = binding.get('types', {}).get('value', '')
                types = [t.strip() for t in types_str.split(',') if t.strip()]

                sitelinks_str = binding.get('sitelinks', {}).get('value', '0')
                try:
                    sitelinks = int(sitelinks_str) if sitelinks_str else 0
                except ValueError:
                    sitelinks = 0

                # Calculate popularity score (normalized sitelinks)
                popularity_score = min(1.0, sitelinks / max_sitelinks) if max_sitelinks > 0 else 0.0

                candidate = EntityCandidate(
                    entity_text=entity_text,
                    wikidata_id=wikidata_id,
                    label=label,
                    description=description,
                    types=types,
                    popularity_score=popularity_score
                )
                candidates.append(candidate)

            except Exception as e:
                logger.debug(f"Error parsing candidate: {e}")
                continue

        return candidates

    def compute_context_similarity(self, entity_desc: str, context: str) -> float:
        """
        Compute semantic similarity between entity description and context.

        Uses rapidfuzz for fuzzy matching (lightweight, no ML models).

        Args:
            entity_desc: Entity description
            context: Context text

        Returns:
            Similarity score (0-1)
        """
        if not entity_desc or not context:
            return 0.0

        if RAPIDFUZZ_AVAILABLE:
            # Use token_set_ratio for partial matching
            score = fuzz.token_set_ratio(entity_desc.lower(), context.lower())
            return score / 100.0
        else:
            # Simple word overlap fallback
            desc_words = set(entity_desc.lower().split())
            context_words = set(context.lower().split())

            if not desc_words:
                return 0.0

            overlap = len(desc_words & context_words)
            return overlap / len(desc_words)

    async def disambiguate(
        self,
        entity_text: str,
        candidates: List[EntityCandidate],
        context: str
    ) -> Optional[EntityCandidate]:
        """
        Disambiguate entity candidates using context.

        Args:
            entity_text: Original entity text
            candidates: List of candidate entities
            context: Context for disambiguation

        Returns:
            Best matching candidate or None
        """
        if not candidates:
            return None

        if len(candidates) == 1:
            candidate = candidates[0]
            candidate.context_score = self.compute_context_similarity(
                candidate.description, context
            )
            candidate.final_score = 0.5 + (candidate.popularity_score * 0.5)
            return candidate

        # Score all candidates
        scored_candidates = []
        for candidate in candidates:
            # Context similarity
            context_score = self.compute_context_similarity(
                candidate.description, context
            )

            # Combine scores (context 60%, popularity 40%)
            final_score = (context_score * 0.6) + (candidate.popularity_score * 0.4)

            candidate.context_score = context_score
            candidate.final_score = final_score
            scored_candidates.append(candidate)

        # Sort by final score
        scored_candidates.sort(key=lambda x: x.final_score, reverse=True)

        # Return best candidate if above threshold
        best = scored_candidates[0]
        if best.final_score >= self.confidence_threshold:
            return best

        # Return most popular as fallback
        scored_candidates.sort(key=lambda x: x.popularity_score, reverse=True)
        return scored_candidates[0]

    async def link_entities(
        self,
        text: str,
        context: str = ""
    ) -> List[LinkedEntity]:
        """
        Link entities in text to Wikidata.

        Args:
            text: Input text to extract and link entities from
            context: Optional context for disambiguation

        Returns:
            List of LinkedEntity objects
        """
        # Extract entities
        if self.use_gliner:
            extracted = self._extract_entities_gliner(text)
        else:
            extracted = self._extract_entities_fallback(text)

        if not extracted:
            return []

        # Query Wikidata for each entity (with concurrency limit)
        linked_entities = []
        semaphore = asyncio.Semaphore(5)  # Limit concurrent requests

        async def process_entity(entity_data: Tuple[str, int, int, str]) -> Optional[LinkedEntity]:
            async with semaphore:
                entity_text, start, end, entity_type = entity_data

                # Query Wikidata
                candidates = await self.query_wikidata(entity_text)

                if not candidates:
                    return None

                # Disambiguate
                use_context = context or text[max(0, start-100):min(len(text), end+100)]
                best_candidate = await self.disambiguate(
                    entity_text, candidates, use_context
                )

                if best_candidate is None:
                    return None

                return LinkedEntity(
                    original_text=entity_text,
                    start_pos=start,
                    end_pos=end,
                    canonical_id=best_candidate.wikidata_id,
                    canonical_label=best_candidate.label,
                    entity_type=entity_type,
                    confidence=best_candidate.final_score,
                    candidates_considered=len(candidates)
                )

        # Process all entities concurrently
        tasks = [process_entity(e) for e in extracted]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, LinkedEntity):
                linked_entities.append(result)
            elif isinstance(result, Exception):
                logger.debug(f"Entity linking failed: {result}")

        return linked_entities

    async def resolve_aliases(self, entities: List[str]) -> Dict[str, str]:
        """
        Resolve entity aliases to canonical Wikidata labels.

        Args:
            entities: List of entity texts to resolve

        Returns:
            Dictionary mapping original text to canonical label
        """
        resolved = {}
        semaphore = asyncio.Semaphore(5)

        async def resolve_one(entity: str) -> Tuple[str, Optional[str]]:
            async with semaphore:
                candidates = await self.query_wikidata(entity)
                if candidates:
                    # Return most popular
                    best = max(candidates, key=lambda x: x.popularity_score)
                    return entity, best.label
                return entity, None

        tasks = [resolve_one(e) for e in entities]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, tuple):
                entity, canonical = result
                if canonical:
                    resolved[entity] = canonical

        return resolved

    def canonicalize_entity(self, entity_text: str, entity_type: str) -> str:
        """
        Canonicalize entity text to a standard form.

        Args:
            entity_text: Original entity text
            entity_type: Entity type

        Returns:
            Canonicalized entity text
        """
        # Remove common prefixes/suffixes
        canonical = entity_text.strip()

        # Type-specific canonicalization
        if entity_type == 'PERSON':
            # "John Smith" -> "Smith, John" for sorting
            parts = canonical.split()
            if len(parts) == 2:
                canonical = f"{parts[1]}, {parts[0]}"
        elif entity_type in ('ORGANIZATION', 'BUSINESS'):
            # Remove common suffixes
            suffixes = [' Inc.', ' Corp.', ' Ltd.', ' LLC', ' Company', ' Co.']
            for suffix in suffixes:
                if canonical.endswith(suffix):
                    canonical = canonical[:-len(suffix)].strip()
                    break

        return canonical.lower()

    async def batch_link(
        self,
        texts: List[str],
        contexts: Optional[List[str]] = None
    ) -> List[List[LinkedEntity]]:
        """
        Link entities in multiple texts (batch processing).

        Args:
            texts: List of texts to process
            contexts: Optional list of contexts (one per text)

        Returns:
            List of LinkedEntity lists (one per input text)
        """
        if contexts is None:
            contexts = [""] * len(texts)

        if len(texts) != len(contexts):
            raise ValueError("texts and contexts must have same length")

        tasks = [
            self.link_entities(text, context)
            for text, context in zip(texts, contexts)
        ]

        return await asyncio.gather(*tasks)

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return self._cache.get_stats()

    def clear_cache(self):
        """Clear the query cache."""
        self._cache.clear()
        logger.info("EntityLinker cache cleared")

    async def close(self):
        """Close HTTP session and cleanup resources."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

        # Unload GLiNER
        if self._gliner_model is not None:
            self._gliner_model = None
            import gc
            gc.collect()

        logger.info("EntityLinker closed")

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()


# Convenience functions for simple usage
_linker: Optional[EntityLinker] = None


def get_linker() -> EntityLinker:
    """Get singleton EntityLinker instance."""
    global _linker
    if _linker is None:
        _linker = EntityLinker()
    return _linker


async def link_entities(text: str, context: str = "") -> List[LinkedEntity]:
    """
    Link entities in text (convenience function).

    Args:
        text: Input text
        context: Optional context

    Returns:
        List of LinkedEntity objects
    """
    linker = get_linker()
    return await linker.link_entities(text, context)


async def resolve_entity(entity_text: str) -> Optional[EntityCandidate]:
    """
    Resolve single entity to Wikidata (convenience function).

    Args:
        entity_text: Entity text to resolve

    Returns:
        Best matching EntityCandidate or None
    """
    linker = get_linker()
    candidates = await linker.query_wikidata(entity_text)

    if not candidates:
        return None

    return await linker.disambiguate(entity_text, candidates, "")


if __name__ == "__main__":
    # Test the entity linker
    logging.basicConfig(level=logging.INFO)

    async def test():
        linker = EntityLinker()

        test_texts = [
            "Apple was founded by Steve Jobs in California.",
            "The Eiffel Tower is located in Paris, France.",
            "Python is a programming language created by Guido van Rossum.",
        ]

        for text in test_texts:
            print(f"\nText: {text}")
            entities = await linker.link_entities(text)

            for entity in entities:
                print(f"  '{entity.original_text}' -> {entity.canonical_label} "
                      f"({entity.canonical_id}, confidence: {entity.confidence:.2f})")

        print(f"\nCache stats: {linker.get_cache_stats()}")
        await linker.close()

    asyncio.run(test())
