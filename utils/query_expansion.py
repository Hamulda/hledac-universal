"""
Query Expansion - Intelligent Query Variations

Generates context-aware search variations using:
- Domain-specific synonyms
- Acronym expansion
- Pattern-based expansion
- Permutation generation

M1-Optimized: Minimal dependencies, efficient generation
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ExpansionConfig:
    """Configuration for query expansion"""
    max_variations: int = 50
    synonym_depth: int = 2
    include_acronyms: bool = True
    include_plurals: bool = True
    include_permutations: bool = True
    domain_context: str | None = None  # 'academic', 'medical', 'tech', etc.


class QueryExpander:
    """
    Generate intelligent search query variations.

    Example:
        >>> expander = QueryExpander()
        >>> variations = expander.expand("machine learning healthcare")
        >>> print(variations)
        ['machine learning healthcare', 'ml healthcare', 'machine learning medicine', ...]
    """

    # Domain-specific synonyms
    DOMAIN_SYNONYMS: dict[str, dict[str, list[str]]] = {
        'academic': {
            'paper': ['article', 'publication', 'research', 'study'],
            'author': ['researcher', 'scientist', 'scholar'],
            'journal': ['periodical', 'publication', 'magazine'],
            'citation': ['reference', 'bibliography'],
            'abstract': ['summary', 'overview'],
        },
        'medical': {
            'patient': ['subject', 'individual', 'case'],
            'treatment': ['therapy', 'intervention', 'care'],
            'disease': ['condition', 'disorder', 'illness', 'syndrome'],
            'symptom': ['sign', 'manifestation', 'indication'],
            'diagnosis': ['identification', 'detection'],
            'medication': ['drug', 'pharmaceutical', 'medicine'],
        },
        'tech': {
            'software': ['program', 'application', 'system'],
            'hardware': ['equipment', 'device', 'machinery'],
            'network': ['connection', 'infrastructure'],
            'algorithm': ['procedure', 'method', 'technique'],
            'database': ['repository', 'data store'],
        },
        'general': {
            'method': ['approach', 'technique', 'strategy', 'way'],
            'analysis': ['examination', 'evaluation', 'study'],
            'development': ['evolution', 'growth', 'progress'],
            'improvement': ['enhancement', 'optimization', 'refinement'],
            'application': ['use', 'implementation', 'deployment'],
        }
    }

    # Common acronyms in research
    ACRONYMS: dict[str, list[str]] = {
        'ml': ['machine learning'],
        'ai': ['artificial intelligence'],
        'dl': ['deep learning'],
        'nlp': ['natural language processing'],
        'cv': ['computer vision'],
        'rl': ['reinforcement learning'],
        'nn': ['neural network', 'neural networks'],
        'cnn': ['convolutional neural network', 'convolutional neural networks'],
        'rnn': ['recurrent neural network', 'recurrent neural networks'],
        'llm': ['large language model', 'large language models'],
        'bert': ['bidirectional encoder representations'],
        'gpu': ['graphics processing unit'],
        'cpu': ['central processing unit'],
        'api': ['application programming interface'],
        'ui': ['user interface'],
        'ux': ['user experience'],
        'db': ['database'],
        'sql': ['structured query language'],
        'json': ['javascript object notation'],
        'xml': ['extensible markup language'],
        'html': ['hypertext markup language'],
        'css': ['cascading style sheets'],
    }

    def __init__(self, config: ExpansionConfig | None = None):
        self.config = config or ExpansionConfig()
        self._synonyms: dict[str, list[str]] = {}
        self._build_synonym_map()

    def _build_synonym_map(self):
        """Build combined synonym map based on domain context"""
        self._synonyms = dict(self.DOMAIN_SYNONYMS.get('general', {}))

        if self.config.domain_context:
            domain_syns = self.DOMAIN_SYNONYMS.get(self.config.domain_context, {})
            for word, syns in domain_syns.items():
                if word in self._synonyms:
                    self._synonyms[word] = list(set(self._synonyms[word] + syns))
                else:
                    self._synonyms[word] = syns

    def _tokenize(self, text: str) -> list[str]:
        """Tokenize query into words"""
        return text.lower().split()

    def _get_synonyms(self, word: str) -> list[str]:
        """Get synonyms for a word"""
        word = word.lower()

        if word in self._synonyms:
            return self._synonyms[word]

        if self.config.include_acronyms and word in self.ACRONYMS:
            return self.ACRONYMS[word]

        if self.config.include_acronyms:
            for acronym, expansions in self.ACRONYMS.items():
                if word in expansions:
                    return [acronym]

        return []

    def _generate_plural(self, word: str) -> str | None:
        """Generate plural form of word"""
        if not self.config.include_plurals:
            return None

        if word.endswith('s') or word.endswith('x') or word.endswith('ch') or word.endswith('sh'):
            return word + 'es'
        elif word.endswith('y') and len(word) > 1 and word[-2] not in 'aeiou':
            return word[:-1] + 'ies'
        else:
            return word + 's'

    def _expand_acronyms(self, query: str) -> list[str]:
        """Expand acronyms in query"""
        if not self.config.include_acronyms:
            return [query]

        tokens = self._tokenize(query)
        expansions = []

        for token in tokens:
            if token in self.ACRONYMS:
                expansions.append((token, self.ACRONYMS[token]))

        if not expansions:
            return [query]

        results = [tokens]

        for acronym, expansions_list in expansions:
            new_results = []
            for result in results:
                for expansion in expansions_list:
                    new_result = result.copy()
                    idx = new_result.index(acronym)
                    new_result[idx] = expansion
                    new_results.append(new_result)
            results = new_results

        return [' '.join(r) for r in results]

    def _generate_synonym_variations(self, query: str) -> list[str]:
        """Generate variations by replacing words with synonyms"""
        tokens = self._tokenize(query)

        token_synonyms = []
        for token in tokens:
            syns = self._get_synonyms(token)
            if syns:
                token_synonyms.append((token, [token] + syns[:self.config.synonym_depth]))
            else:
                token_synonyms.append((token, [token]))

        if not token_synonyms:
            return [query]

        synonym_lists = [ts[1] for ts in token_synonyms]
        combinations = list(itertools.product(*synonym_lists))

        if len(combinations) > self.config.max_variations:
            combinations = combinations[:self.config.max_variations]

        return [' '.join(combo) for combo in combinations]

    def _generate_permutations(self, query: str) -> list[str]:
        """Generate permutations of query terms"""
        tokens = self._tokenize(query)

        if len(tokens) < 2 or len(tokens) > 4:
            return [query]

        permutations = [tokens]

        if len(tokens) == 2:
            permutations.append([tokens[1], tokens[0]])
        elif len(tokens) == 3:
            permutations.append([tokens[1], tokens[0], tokens[2]])
            permutations.append([tokens[0], tokens[2], tokens[1]])

        return [' '.join(p) for p in permutations]

    def expand(self, query: str) -> list[str]:
        """
        Generate query variations.

        Args:
            query: Original search query

        Returns:
            List of query variations
        """
        if not query or not query.strip():
            return []

        query = query.strip().lower()
        variations: set[str] = {query}

        # Expand acronyms
        acronym_expansions = self._expand_acronyms(query)
        variations.update(acronym_expansions)

        # Generate synonym variations
        for expansion in acronym_expansions:
            syn_variations = self._generate_synonym_variations(expansion)
            variations.update(syn_variations)

        # Generate permutations
        if self.config.include_permutations:
            permutations = self._generate_permutations(query)
            variations.update(permutations)

        # Add plural forms
        if self.config.include_plurals:
            tokens = self._tokenize(query)
            pluralized = []
            for token in tokens:
                plural = self._generate_plural(token)
                if plural and plural != token:
                    pluralized.append((token, plural))

            for original, plural in pluralized[:3]:
                new_query = query.replace(original, plural, 1)
                variations.add(new_query)

        results = sorted(variations, key=lambda x: (len(x), x))
        return results[:self.config.max_variations]

    def expand_for_discovery(
        self,
        base_terms: list[str],
        modifiers: list[str] | None = None
    ) -> list[str]:
        """
        Generate discovery-focused query variations.

        Args:
            base_terms: Base search terms
            modifiers: Additional modifiers

        Returns:
            Combined expanded queries
        """
        if modifiers is None:
            modifiers = ['', 'review', 'paper', 'tutorial', 'guide', 'overview']

        all_variations = []

        for term in base_terms:
            term_variations = self.expand(term)

            for variation in term_variations:
                for modifier in modifiers:
                    if modifier:
                        combined = f"{variation} {modifier}".strip()
                    else:
                        combined = variation
                    all_variations.append(combined)

        unique = list(dict.fromkeys(all_variations))
        return unique[:self.config.max_variations * 2]

    def get_statistics(self) -> dict[str, Any]:
        """Get expander statistics"""
        return {
            'domain': self.config.domain_context,
            'synonyms_loaded': len(self._synonyms),
            'acronyms_loaded': len(self.ACRONYMS),
        }


# Convenience function
def expand_query(
    query: str,
    domain: str | None = None,
    max_variations: int = 20
) -> list[str]:
    """
    Quick query expansion.

    Args:
        query: Original query
        domain: Domain context ('academic', 'medical', 'tech')
        max_variations: Maximum variations to generate

    Returns:
        List of query variations
    """
    config = ExpansionConfig(
        domain_context=domain,
        max_variations=max_variations
    )
    expander = QueryExpander(config)
    return expander.expand(query)


# =============================================================================
# ADVANCED EXPANSION STRATEGIES - From MSQES
# =============================================================================

from abc import ABC, abstractmethod


class ExpansionStrategy(ABC):
    """Abstract base class for query expansion strategies (from MSQES)."""

    @abstractmethod
    async def expand(self, query: str, context: dict[str, Any] | None = None) -> list[QueryVariation]:
        """Expand query into multiple variations."""
        pass

    @property
    @abstractmethod
    def strategy_type(self) -> str:
        """Get strategy type identifier."""
        pass


@dataclass
class QueryVariation:
    """A single query variation with metadata."""
    query: str
    strategy: str
    weight: float = 1.0
    confidence: float = 0.8


class SemanticExpansionStrategy(ExpansionStrategy):
    """
    Semantic query expansion using synonyms and related terms.
    From MSQES - optimized for academic research.
    """

    # Domain-specific semantic mappings
    DOMAIN_SYNONYMS: dict[str, dict[str, list[str]]] = {
        "cs": {
            "machine learning": ["ML", "deep learning", "neural networks", "artificial intelligence", "AI"],
            "neural network": ["NN", "deep neural network", "DNN", "artificial neural network"],
            "training": ["learning", "optimization", "fine-tuning", "fitting"],
            "dataset": ["data", "corpus", "benchmark", "training data"],
            "model": ["architecture", "network", "system", "algorithm"],
            "classification": ["categorization", "labeling", "recognition"],
            "prediction": ["forecasting", "regression", "estimation"],
        },
        "physics": {
            "quantum": ["quantum mechanics", "quantum physics", "quantum theory"],
            "relativity": ["general relativity", "special relativity", "GR", "SR"],
            "particle": ["subatomic", "elementary particle", "fundamental particle"],
        },
        "biology": {
            "gene": ["genetic", "DNA", "genomic"],
            "protein": ["peptide", "amino acid sequence"],
            "cell": ["cellular", "tissue", "organism"],
        },
        "medicine": {
            "patient": ["subject", "individual", "case"],
            "treatment": ["therapy", "intervention", "care"],
            "disease": ["condition", "disorder", "illness", "syndrome"],
            "diagnosis": ["identification", "detection"],
            "medication": ["drug", "pharmaceutical", "medicine"],
        }
    }

    # General academic synonyms
    GENERAL_SYNONYMS: dict[str, list[str]] = {
        "research": ["study", "investigation", "analysis", "exploration"],
        "method": ["approach", "technique", "methodology", "procedure"],
        "result": ["finding", "outcome", "conclusion", "discovery"],
        "analysis": ["examination", "evaluation", "assessment", "investigation"],
        "significant": ["important", "substantial", "considerable", "meaningful"],
        "improve": ["enhance", "optimize", "boost", "increase"],
        "new": ["novel", "innovative", "recent", "state-of-the-art"],
        "performance": ["efficiency", "accuracy", "effectiveness", "capability"],
    }

    def __init__(self, max_expansions: int = 5, domain: str | None = None):
        self.max_expansions = max_expansions
        self.domain = domain
        self._synonyms = self._build_synonym_map()

    @property
    def strategy_type(self) -> str:
        return "semantic"

    def _build_synonym_map(self) -> dict[str, list[str]]:
        """Build combined synonym map."""
        synonyms = dict(self.GENERAL_SYNONYMS)
        if self.domain and self.domain in self.DOMAIN_SYNONYMS:
            synonyms.update(self.DOMAIN_SYNONYMS[self.domain])
        return synonyms

    async def expand(self, query: str, context: dict[str, Any] | None = None) -> list[QueryVariation]:
        """Expand query using semantic variations."""
        variations = []
        query_lower = query.lower()

        # Detect domain from context or query
        detected_domain = context.get("domain") if context else None
        if detected_domain and detected_domain in self.DOMAIN_SYNONYMS:
            synonyms = dict(self.GENERAL_SYNONYMS)
            synonyms.update(self.DOMAIN_SYNONYMS[detected_domain])
        else:
            synonyms = self._synonyms

        # Generate expansions by replacing terms
        for term, replacements in synonyms.items():
            if term in query_lower:
                for replacement in replacements[:2]:  # Limit replacements
                    expanded = query_lower.replace(term, replacement, 1)
                    if expanded != query_lower:
                        variations.append(QueryVariation(
                            query=expanded,
                            strategy="semantic",
                            weight=0.8,
                            confidence=0.85
                        ))

        # Add academic modifiers
        key_terms = query_lower.split()
        if key_terms:
            variations.append(QueryVariation(
                query=f"recent {query}",
                strategy="semantic",
                weight=0.7,
                confidence=0.75
            ))
            variations.append(QueryVariation(
                query=f"survey {query}",
                strategy="semantic",
                weight=0.6,
                confidence=0.70
            ))
            variations.append(QueryVariation(
                query=f"review {query}",
                strategy="semantic",
                weight=0.6,
                confidence=0.70
            ))

        # Remove duplicates while preserving order
        seen = set()
        unique_variations = []
        for var in variations:
            if var.query not in seen:
                seen.add(var.query)
                unique_variations.append(var)

        return unique_variations[:self.max_expansions]


class SyntacticExpansionStrategy(ExpansionStrategy):
    """
    Syntactic query expansion - generates different phrasings
    without changing semantic meaning.
    """

    def __init__(self, max_expansions: int = 5):
        self.max_expansions = max_expansions

    @property
    def strategy_type(self) -> str:
        return "syntactic"

    async def expand(self, query: str, context: dict[str, Any] | None = None) -> list[QueryVariation]:
        """Expand query using syntactic variations."""
        variations = []
        words = query.split()

        if len(words) < 2:
            return variations

        # Strategy 1: Reorder key terms
        if len(words) >= 3:
            reordered = words[-2:] + words[:-2]
            variations.append(QueryVariation(
                query=" ".join(reordered),
                strategy="syntactic",
                weight=0.7,
                confidence=0.75
            ))

        # Strategy 2: Add quotes around phrases
        if len(words) >= 2:
            quoted = f'"{" ".join(words[:2])}"' + " " + " ".join(words[2:])
            variations.append(QueryVariation(
                query=quoted,
                strategy="syntactic",
                weight=0.8,
                confidence=0.80
            ))

            quoted_end = " ".join(words[:-2]) + f' "{" ".join(words[-2:])}"'
            variations.append(QueryVariation(
                query=quoted_end,
                strategy="syntactic",
                weight=0.8,
                confidence=0.80
            ))

        # Strategy 3: Boolean variations
        variations.append(QueryVariation(
            query=query.replace(" ", " AND "),
            strategy="syntactic",
            weight=0.6,
            confidence=0.70
        ))

        # Strategy 4: Field-specific variations for academic search
        variations.append(QueryVariation(
            query=f"title:{query}",
            strategy="syntactic",
            weight=0.7,
            confidence=0.75
        ))
        variations.append(QueryVariation(
            query=f"abstract:{query}",
            strategy="syntactic",
            weight=0.7,
            confidence=0.75
        ))

        # Strategy 5: Exact phrase match
        variations.append(QueryVariation(
            query=f'"{query}"',
            strategy="syntactic",
            weight=0.9,
            confidence=0.90
        ))

        # Remove duplicates
        seen = {query.lower()}
        unique_variations = []
        for var in variations:
            normalized = var.query.lower().strip()
            if normalized not in seen:
                seen.add(normalized)
                unique_variations.append(var)

        return unique_variations[:self.max_expansions]


class DomainSpecificExpansionStrategy(ExpansionStrategy):
    """
    Domain-specific query expansion using field knowledge.
    """

    DOMAIN_MODIFIERS: dict[str, list[str]] = {
        "cs": [
            "algorithm", "implementation", "evaluation", "benchmark",
            "dataset", "framework", "open source", "GitHub"
        ],
        "medicine": [
            "clinical trial", "patient outcomes", "treatment",
            "efficacy", "safety", "randomized controlled trial", "meta-analysis"
        ],
        "physics": [
            "theoretical", "experimental", "simulation", "model", "measurement"
        ],
        "biology": [
            "in vivo", "in vitro", "molecular", "cellular", "genetic"
        ],
        "economics": [
            "empirical", "theoretical", "policy", "market analysis", "econometric"
        ],
        "psychology": [
            "behavioral", "cognitive", "experimental", "longitudinal study"
        ]
    }

    PAPER_TYPES = [
        "paper", "article", "study", "research",
        "thesis", "dissertation", "preprint"
    ]

    def __init__(self, max_expansions: int = 5, domain: str | None = None):
        self.max_expansions = max_expansions
        self.domain = domain

    @property
    def strategy_type(self) -> str:
        return "domain"

    def _detect_domain(self, query: str) -> str | None:
        """Detect domain from query terms."""
        query_lower = query.lower()

        domain_indicators = {
            "cs": ["algorithm", "neural", "machine learning", "deep learning",
                   "computer vision", "nlp", "artificial intelligence", "code",
                   "programming", "software", "network", "database"],
            "medicine": ["patient", "clinical", "treatment", "disease",
                        "diagnosis", "therapy", "drug", "medical", "health"],
            "physics": ["quantum", "particle", "relativity", "thermodynamics",
                       "electromagnetic", "optics", "mechanics"],
            "biology": ["gene", "protein", "cell", "organism", "species",
                       "dna", "molecular", "biological", "evolution"],
        }

        for domain, terms in domain_indicators.items():
            if any(term in query_lower for term in terms):
                return domain
        return None

    async def expand(self, query: str, context: dict[str, Any] | None = None) -> list[QueryVariation]:
        """Expand query using domain-specific knowledge."""
        variations = []

        # Detect domain
        domain = self.domain
        if not domain:
            domain = self._detect_domain(query)

        if domain and domain in self.DOMAIN_MODIFIERS:
            modifiers = self.DOMAIN_MODIFIERS[domain]

            # Add domain-specific modifiers
            for modifier in modifiers[:3]:
                variations.append(QueryVariation(
                    query=f"{query} {modifier}",
                    strategy="domain",
                    weight=0.75,
                    confidence=0.80
                ))

            # Add domain-specific paper types
            for paper_type in self.PAPER_TYPES[:3]:
                variations.append(QueryVariation(
                    query=f"{query} {paper_type}",
                    strategy="domain",
                    weight=0.7,
                    confidence=0.75
                ))

        # Add general academic modifiers
        general_modifiers = [
            "research paper", "journal article", "conference proceedings",
            "literature review", "systematic review"
        ]

        for modifier in general_modifiers[:2]:
            variations.append(QueryVariation(
                query=f"{query} {modifier}",
                strategy="domain",
                weight=0.6,
                confidence=0.70
            ))

        # Remove duplicates
        seen = {query.lower()}
        unique_variations = []
        for var in variations:
            normalized = var.query.lower().strip()
            if normalized not in seen:
                seen.add(normalized)
                unique_variations.append(var)

        return unique_variations[:self.max_expansions]


class MultiStrategyExpander:
    """
    Combines multiple expansion strategies for comprehensive query expansion.
    From MSQES - Multi-Source Query Expansion System.
    """

    def __init__(
        self,
        strategies: list[ExpansionStrategy] | None = None,
        max_total_variations: int = 20
    ):
        self.strategies = strategies or [
            SemanticExpansionStrategy(),
            SyntacticExpansionStrategy(),
            DomainSpecificExpansionStrategy()
        ]
        self.max_total_variations = max_total_variations

    async def expand(self, query: str, context: dict[str, Any] | None = None) -> list[QueryVariation]:
        """
        Expand query using all configured strategies.

        Args:
            query: Original query
            context: Optional context (domain hints, etc.)

        Returns:
            List of query variations from all strategies
        """
        all_variations = []

        # Collect variations from all strategies
        for strategy in self.strategies:
            try:
                variations = await strategy.expand(query, context)
                all_variations.extend(variations)
            except Exception as e:
                logger.warning(f"Expansion strategy {strategy.strategy_type} failed: {e}")

        # Sort by weight (confidence * strategy weight)
        all_variations.sort(key=lambda v: v.weight * v.confidence, reverse=True)

        # Remove duplicates
        seen = {query.lower()}
        unique_variations = []
        for var in all_variations:
            if var.query.lower() not in seen:
                seen.add(var.query.lower())
                unique_variations.append(var)

        return unique_variations[:self.max_total_variations]


# =============================================================================
# DORKING ENGINE - Advanced Google Dorking for Deep Research
# Integrated from hledac/scanners/deep_probe.py
# =============================================================================

class DorkingEngine:
    """
    Advanced dorking engine for generating complex search queries.

    Generates sophisticated search queries (Google dorks) for discovering
    hidden content, academic papers, technical documents, and more.

    Categories:
    - academic: Research papers, publications, studies
    - technical: Specifications, documentation, manuals
    - financial: Reports, annual statements, investor docs
    - government: Classified docs, FOIA releases, archives

    Example:
        >>> engine = DorkingEngine()
        >>> queries = engine.generate_complex_queries('ai research', 'academic')
        >>> print(queries[:3])
        ['site:ai.edu filetype:pdf "research"', 'site:ai.gov filetype:pdf "study"', ...]
    """

    def __init__(self):
        self.patterns = {
            'academic': [
                'site:{domain} filetype:pdf "research"',
                'site:{domain} filetype:pdf "study"',
                'site:{domain} filetype:pdf "analysis"',
                'site:{domain} inurl:research filetype:pdf',
                'site:{domain} inurl:publications filetype:pdf',
                'site:{domain} filetype:doc "research"',
                'site:{domain} "research paper" "pdf"',
                'site:{domain} "journal" "article" "pdf"',
            ],
            'technical': [
                'site:{domain} filetype:pdf "specification"',
                'site:{domain} filetype:pdf "documentation"',
                'site:{domain} filetype:pdf "manual"',
                'site:{domain} inurl:docs filetype:pdf',
                'site:{domain} inurl:api filetype:pdf',
                'site:{domain} filetype:txt "readme"',
                'site:{domain} "api documentation" "pdf"',
                'site:{domain} "technical report" "pdf"',
            ],
            'financial': [
                'site:{domain} filetype:pdf "report"',
                'site:{domain} filetype:pdf "annual"',
                'site:{domain} filetype:pdf "quarterly"',
                'site:{domain} inurl:investor filetype:pdf',
                'site:{domain} inurl:financial filetype:pdf',
                'site:{domain} "financial statement" "pdf"',
                'site:{domain} "earnings report" "pdf"',
            ],
            'government': [
                'site:{domain} filetype:pdf "classified"',
                'site:{domain} filetype:pdf "declassified"',
                'site:{domain} filetype:pdf "memo"',
                'site:{domain} inurl:foia filetype:pdf',
                'site:{domain} inurl:archives filetype:pdf',
                'site:{domain} "government report" "pdf"',
                'site:{domain} "official document" "pdf"',
            ],
            'security': [
                'site:{domain} filetype:log',
                'site:{domain} filetype:env',
                'site:{domain} filetype:config',
                'site:{domain} inurl:admin',
                'site:{domain} inurl:backup',
                'site:{domain} "error log"',
                'site:{domain} "access log"',
            ],
            'hidden': [
                'site:{domain} intitle:"index of"',
                'site:{domain} intitle:"directory listing"',
                'site:{domain} "parent directory"',
                'site:{domain} filetype:sql',
                'site:{domain} filetype:backup',
                'site:{domain} filetype:old',
                'site:{domain} filetype:bak',
            ]
        }

    def generate_complex_queries(
        self,
        topic: str,
        query_type: str = 'academic',
        include_variations: bool = True
    ) -> list[str]:
        """
        Generate complex dorking queries for a topic.

        Args:
            topic: Search topic or domain
            query_type: Type of queries ('academic', 'technical', 'financial',
                       'government', 'security', 'hidden')
            include_variations: Whether to include filetype variations

        Returns:
            List of dorking queries
        """
        if query_type not in self.patterns:
            query_type = 'academic'

        base_patterns = self.patterns[query_type]
        queries = []

        # Generate variations for common domains
        domain_variations = [
            f'{topic}.edu',
            f'{topic}.gov',
            f'{topic}.org',
            f'{topic}.com',
            topic,
            f'www.{topic}.com'
        ]

        # Generate queries for each domain variation
        for pattern in base_patterns:
            for domain in domain_variations:
                query = pattern.replace('{domain}', domain)
                queries.append(query)

        # Add filetype variations if requested
        if include_variations:
            filetypes = ['pdf', 'doc', 'docx', 'txt', 'csv', 'xml', 'json', 'xls', 'xlsx']
            base_queries = queries.copy()

            for query in base_queries:
                if 'filetype:pdf' in query:
                    for ft in filetypes[1:]:  # Skip pdf, already included
                        queries.append(query.replace('filetype:pdf', f'filetype:{ft}'))

        # Remove duplicates while preserving order
        seen = set()
        unique_queries = []
        for q in queries:
            if q not in seen:
                seen.add(q)
                unique_queries.append(q)

        return unique_queries

    def generate_all_categories(self, topic: str) -> dict[str, list[str]]:
        """
        Generate queries for all categories.

        Args:
            topic: Search topic

        Returns:
            Dictionary mapping category to list of queries
        """
        return {
            category: self.generate_complex_queries(topic, category)
            for category in self.patterns.keys()
        }

    def add_custom_pattern(self, category: str, pattern: str) -> None:
        """
        Add custom pattern to a category.

        Args:
            category: Category name (creates new if doesn't exist)
            pattern: Pattern string with {domain} placeholder
        """
        if category not in self.patterns:
            self.patterns[category] = []
        self.patterns[category].append(pattern)


# Update exports
__all__ = [
    'ExpansionConfig',
    'QueryExpander',
    'expand_query',
    'DorkingEngine',  # NEW from scanners
    # MSQES Expansion Strategies
    'ExpansionStrategy',
    'QueryVariation',
    'SemanticExpansionStrategy',
    'SyntacticExpansionStrategy',
    'DomainSpecificExpansionStrategy',
    'MultiStrategyExpander',
]
