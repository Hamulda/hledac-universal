"""
KnowledgeGraphBuilder - Memory-Safe Graph Builder for M1 Silicon
================================================================

Lightweight knowledge graph builder optimized for M1 MacBook Air (8GB RAM).
Uses regex patterns and heuristics instead of heavy NLP models.

Key Features:
    - Regex-based fact extraction (no spacy/transformers overhead)
    - Metadata-driven fact generation from crawled content
    - Direct KuzuDB integration for disk-based storage
"""

import hashlib
import logging
import re
from typing import Any, Dict, List

from hledac.universal.legacy.persistent_layer import (
    EdgeType,
    KnowledgeEdge,
    KnowledgeNode,
    NodeType,
)

logger = logging.getLogger(__name__)


class KnowledgeGraphBuilder:
    """
    Memory-safe knowledge graph builder for M1 Silicon.

    Uses regex patterns and heuristics for fact extraction,
    avoiding heavy NLP model loading (spacy ~500MB+).
    """

    def __init__(self):
        """Initialize the builder with regex patterns."""
        self._patterns = self._init_patterns()
        logger.info("KnowledgeGraphBuilder initialized with regex-based extraction")

    def _init_patterns(self) -> Dict[str, List[re.Pattern]]:
        """
        Initialize regex patterns for fact extraction.

        Returns:
            Dictionary mapping relation types to compiled regex patterns
        """
        return {
            'is_a': [
                re.compile(r'(\w+(?:\s+\w+)*)\s+is\s+(?:a|an)\s+(\w+(?:\s+\w+)*)', re.IGNORECASE),
                re.compile(r'(\w+(?:\s+\w+)*)\s+are\s+(\w+(?:\s+\w+)*)', re.IGNORECASE),
            ],
            'causes': [
                re.compile(r'(\w+(?:\s+\w+)*)\s+causes?\s+(\w+(?:\s+\w+)*)', re.IGNORECASE),
                re.compile(r'(\w+(?:\s+\w+)*)\s+lead(?:s)?\s+to\s+(\w+(?:\s+\w+)*)', re.IGNORECASE),
            ],
            'located_in': [
                re.compile(r'(\w+(?:\s+\w+)*)\s+is\s+located\s+in\s+(\w+(?:\s+\w+)*)', re.IGNORECASE),
                re.compile(r'(\w+(?:\s+\w+)*)\s+is\s+situated\s+in\s+(\w+(?:\s+\w+)*)', re.IGNORECASE),
            ],
            'part_of': [
                re.compile(r'(\w+(?:\s+\w+)*)\s+is\s+part\s+of\s+(\w+(?:\s+\w+)*)', re.IGNORECASE),
                re.compile(r'(\w+(?:\s+\w+)*)\s+belongs\s+to\s+(\w+(?:\s+\w+)*)', re.IGNORECASE),
            ],
            'contains': [
                re.compile(r'(\w+(?:\s+\w+)*)\s+contains?\s+(\w+(?:\s+\w+)*)', re.IGNORECASE),
                re.compile(r'(\w+(?:\s+\w+)*)\s+includes?\s+(\w+(?:\s+\w+)*)', re.IGNORECASE),
            ],
        }

    def extract_facts(self, text: str) -> List[Dict[str, Any]]:
        """
        Extract facts from text using regex patterns.

        Memory-safe extraction without loading NLP models.

        Args:
            text: Input text to extract facts from

        Returns:
            List of extracted facts with source, target, and relation
        """
        facts = []
        sentences = re.split(r'[.!?]+', text)

        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) < 10:
                continue

            for relation_type, patterns in self._patterns.items():
                for pattern in patterns:
                    matches = pattern.findall(sentence)
                    for match in matches:
                        if len(match) == 2:
                            source, target = match
                            facts.append({
                                'source': source.strip(),
                                'target': target.strip(),
                                'relation': relation_type,
                                'context': sentence
                            })

        logger.debug(f"Extracted {len(facts)} facts from text")
        return facts

    def _generate_id(self, content: str) -> str:
        """Generate a consistent ID from content."""
        return hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]

    def process_and_store(
        self,
        content: str,
        metadata: Dict[str, Any],
        knowledge_layer
    ) -> List[str]:
        """
        Process content and store extracted facts in the knowledge graph.

        Args:
            content: Text content to process
            metadata: Metadata from crawler (author, url, etc.)
            knowledge_layer: PersistentKnowledgeLayer instance

        Returns:
            List of created node IDs
        """
        node_ids = []

        facts = self.extract_facts(content)

        for fact in facts:
            source_id = self._generate_id(fact['source'])
            target_id = self._generate_id(fact['target'])

            source_node = knowledge_layer.add_knowledge(
                content=fact['source'],
                node_type=NodeType.ENTITY,
                metadata={'extracted_from': metadata.get('url', 'unknown')}
            )
            node_ids.append(source_node)

            target_node = knowledge_layer.add_knowledge(
                content=fact['target'],
                node_type=NodeType.ENTITY,
                metadata={'extracted_from': metadata.get('url', 'unknown')}
            )
            node_ids.append(target_node)

            edge_type_map = {
                'is_a': EdgeType.RELATED,
                'causes': EdgeType.CAUSES,
                'located_in': EdgeType.RELATED,
                'part_of': EdgeType.PART_OF,
                'contains': EdgeType.CONTAINS,
            }

            edge_type = edge_type_map.get(fact['relation'], EdgeType.RELATED)

            knowledge_layer.add_relation(
                source_id=source_id,
                target_id=target_id,
                edge_type=edge_type,
                metadata={
                    'context': fact['context'],
                    'extracted_from': metadata.get('url', 'unknown')
                }
            )

        if 'author' in metadata and 'url' in metadata:
            author_id = self._generate_id(metadata['author'])
            url_id = self._generate_id(metadata['url'])

            author_node = knowledge_layer.add_knowledge(
                content=metadata['author'],
                node_type=NodeType.ENTITY,
                metadata={'type': 'author'}
            )
            node_ids.append(author_node)

            url_node = knowledge_layer.add_knowledge(
                content=metadata['url'],
                node_type=NodeType.URL,
                metadata={'type': 'source'}
            )
            node_ids.append(url_node)

            knowledge_layer.add_relation(
                source_id=author_id,
                target_id=url_id,
                edge_type=EdgeType.MENTIONS,
                metadata={'relation': 'wrote'}
            )

        logger.info(f"Processed and stored {len(facts)} facts, {len(node_ids)} nodes")
        return node_ids

    def process_document(
        self,
        document: str,
        url: str,
        author: str = None,
        knowledge_layer=None
    ) -> List[str]:
        """
        Process a document and store its facts.

        Convenience method for processing crawled documents.

        Args:
            document: Document content
            url: Document URL
            author: Optional author name
            knowledge_layer: PersistentKnowledgeLayer instance

        Returns:
            List of created node IDs
        """
        metadata = {
            'url': url,
            'type': 'document'
        }

        if author:
            metadata['author'] = author

        return self.process_and_store(document, metadata, knowledge_layer)
