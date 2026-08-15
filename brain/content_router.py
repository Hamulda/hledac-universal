"""
ContentRouter — Pure Functions for Content-Based Task Classification

Extracted from micro_model_swarm.py for better separation of concerns.

This module provides stateless, pure functions for content classification
using regex + keyword matching. No ML model required — <1ms latency.

Usage:
    from hledac.universal.brain.content_router import classify_content, get_preferred_model
    
    task_type = classify_content("Write a SQL query to join...")
    model_id = get_preferred_model(task_type)
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Optional

from .micro_model_pool import MICRO_MODELS, TaskType
from core import aclose


# =============================================================================
# Pre-compiled Patterns (Module-level for efficiency)
# =============================================================================

# Code patterns
_CODE_PATTERNS = (
    r'def\s+\w+\s*\(',
    r'class\s+\w+',
    r'import\s+\w+',
    r'from\s+\w+\s+import',
    r'function\s*\w*\s*\(',
    r'=>\s*\{',
    r'const\s+\w+\s*=',
    r'let\s+\w+\s*=',
    r'var\s+\w+\s*=',
    r'#include',
    r'public\s+class',
    r'private\s+void',
    r'async\s+def',
    r'@\w+\s*\(',
    r'```\w*',
)

# SQL patterns
_SQL_PATTERNS = (
    r'SELECT\s+.+\s+FROM',
    r'INSERT\s+INTO',
    r'UPDATE\s+\w+\s+SET',
    r'DELETE\s+FROM',
    r'CREATE\s+TABLE',
    r'ALTER\s+TABLE',
    r'DROP\s+TABLE',
    r'JOIN\s+\w+\s+ON',
    r'WHERE\s+\w+',
)

# Translation patterns
_TRANSLATION_PATTERNS = (
    r'\b(translate|translation|Übersetzung|traduction|traducción)\b',
    r'\b(from\s+\w+\s+to\s+\w+)\b',
    r'\b(english|german|french|spanish|chinese|japanese|korean)\s+(to|into|ins?)\b',
)

# Embedding keywords
_EMBEDDING_KEYWORDS = (
    "embed", "similarity", "semantic", "vector", "embedding",
    "compare", "rank", "search", "find related", "most similar",
)

# Classification keywords
_CLASSIFICATION_KEYWORDS = (
    r'\b(classify|categorize|osint|threat|indicator|type\s+of|what\s+kind)\b',
)

# Relevance keywords
_RELEVANCE_KEYWORDS = (
    r'\b(relevant|irrelevant|skip|ignore|filter|priorit.y|important)\b',
)

# Synthesis keywords
_SYNTHESIS_KEYWORDS = (
    r'\b(write|summarize|explain|describe|generate|create|compose)\b',
)


# =============================================================================
# Compiled Regex Patterns (Lazy compilation)
# =============================================================================

_compiled_code_re: re.Pattern | None = None
_compiled_sql_re: re.Pattern | None = None
_compiled_trans_re: re.Pattern | None = None
_compiled_embed_re: re.Pattern | None = None
_compiled_classification_re: re.Pattern | None = None
_compiled_relevance_re: re.Pattern | None = None
_compiled_synthesis_re: re.Pattern | None = None


def _get_code_re() -> re.Pattern:
    """Get compiled code regex pattern."""
    global _compiled_code_re
    if _compiled_code_re is None:
        _compiled_code_re = re.compile('|'.join(_CODE_PATTERNS), re.IGNORECASE)
    return _compiled_code_re


def _get_sql_re() -> re.Pattern:
    """Get compiled SQL regex pattern."""
    global _compiled_sql_re
    if _compiled_sql_re is None:
        _compiled_sql_re = re.compile('|'.join(_SQL_PATTERNS), re.IGNORECASE)
    return _compiled_sql_re


def _get_trans_re() -> re.Pattern:
    """Get compiled translation regex pattern."""
    global _compiled_trans_re
    if _compiled_trans_re is None:
        _compiled_trans_re = re.compile('|'.join(_TRANSLATION_PATTERNS), re.IGNORECASE)
    return _compiled_trans_re


def _get_embed_re() -> re.Pattern:
    """Get compiled embedding keyword regex pattern."""
    global _compiled_embed_re
    if _compiled_embed_re is None:
        escaped = [re.escape(k) for k in _EMBEDDING_KEYWORDS]
        _compiled_embed_re = re.compile('|'.join(escaped), re.IGNORECASE)
    return _compiled_embed_re


def _get_classification_re() -> re.Pattern:
    """Get compiled classification regex pattern."""
    global _compiled_classification_re
    if _compiled_classification_re is None:
        _compiled_classification_re = re.compile('|'.join(_CLASSIFICATION_KEYWORDS), re.IGNORECASE)
    return _compiled_classification_re


def _get_relevance_re() -> re.Pattern:
    """Get compiled relevance regex pattern."""
    global _compiled_relevance_re
    if _compiled_relevance_re is None:
        _compiled_relevance_re = re.compile('|'.join(_RELEVANCE_KEYWORDS), re.IGNORECASE)
    return _compiled_relevance_re


def _get_synthesis_re() -> re.Pattern:
    """Get compiled synthesis regex pattern."""
    global _compiled_synthesis_re
    if _compiled_synthesis_re is None:
        _compiled_synthesis_re = re.compile('|'.join(_SYNTHESIS_KEYWORDS), re.IGNORECASE)
    return _compiled_synthesis_re


# =============================================================================
# Pure Functions for Classification
# =============================================================================

def classify_content(text: str) -> TaskType:
    """
    Classify text into TaskType using regex + keyword matching.
    
    Fast path: No ML model required, only regex on raw text.
    <1ms latency.
    
    Args:
        text: Input text to classify
        
    Returns:
        TaskType classification
    """
    text_lower = text.lower()
    
    # Priority 1: SQL detection (most specific)
    if _get_sql_re().search(text):
        return TaskType.CODE  # SQL routed to code specialist
    
    # Priority 2: Code detection
    if _get_code_re().search(text):
        return TaskType.CODE
    
    # Priority 3: Embedding tasks
    if _get_embed_re().search(text):
        return TaskType.EMBEDDINGS
    
    # Priority 4: Translation
    if _get_trans_re().search(text):
        return TaskType.TRANSLATION
    
    # Priority 5: Classification (OSINT)
    if _get_classification_re().search(text):
        return TaskType.CLASSIFICATION
    
    # Priority 6: Binary triage
    if _get_relevance_re().search(text):
        return TaskType.TRIAGE
    
    # Priority 7: Synthesis
    if _get_synthesis_re().search(text):
        return TaskType.SYNTHESIS
    
    # Default: generalist model
    return TaskType.GENERAL


@lru_cache(maxsize=1024)
def get_preferred_model(task_type: TaskType) -> Optional[str]:
    """
    Get the preferred micro-model name for a task type.
    
    Returns None if no micro-model is suitable for this task,
    indicating fallback to main model should be used.
    
    Note: Uses lru_cache for performance — the mapping is static.
    
    Args:
        task_type: TaskType to get preferred model for
        
    Returns:
        Model ID string or None
    """
    mapping = {
        TaskType.CODE: "qwen_coder",
        TaskType.EMBEDDINGS: "nomic_embed",
        TaskType.TRIAGE: "smollm_triage",
        TaskType.SYNTHESIS: "phi35_mini",
        TaskType.TRANSLATION: "phi35_mini",
        TaskType.CLASSIFICATION: None,  # Needs generalist or specialized
        TaskType.GENERAL: None,  # Use main model
    }
    model_id = mapping.get(task_type)
    # Verify model is available in registry
    if model_id and model_id in MICRO_MODELS:
        return model_id
    return None


def route_content(text: str) -> tuple[Optional[str], TaskType]:
    """
    Combined content classification and model routing.
    
    Pure function version combining classify_content and get_preferred_model.
    <1ms latency.
    
    Args:
        text: Input text to classify and route
        
    Returns:
        Tuple of (model_id, task_type)
        model_id is None if routing to main model is recommended
    """
    task_type = classify_content(text)
    model_id = get_preferred_model(task_type)
    return (model_id, task_type)


# =============================================================================
# ContentRouter Class (Thin wrapper for backward compatibility)
# =============================================================================

class ContentRouter:
    """
    Fast content-based task classification using regex + heuristics.
    
    This class is maintained for backward compatibility. The actual logic
    is in the pure functions above.
    
    Usage:
        router = ContentRouter()
        task_type = router.classify("Write a SQL query...")
        model_id = router.get_preferred_model(task_type)
    """
    
    def classify(self, text: str) -> TaskType:
        """Classify text into TaskType."""
        return classify_content(text)
    
    def get_preferred_model(self, task_type: TaskType) -> Optional[str]:
        """Get the preferred micro-model name for a task type."""
        return get_preferred_model(task_type)
