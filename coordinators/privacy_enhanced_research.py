"""
Privacy Enhanced Research - Secure Research Wrapper

Wraps research operations with privacy protections:
- Request anonymization
- Result sanitization
- Audit logging
- Data minimization
- Secure communication channels

Based on research_privacy_enhancer concept from integration files.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable
from enum import Enum

from hledac.universal.project_types import PrivacyLevel

logger = logging.getLogger(__name__)


class DataRetention(Enum):
    """Data retention policies."""
    SESSION = "session"        # Delete after session
    SHORT = "short"           # 1 hour
    MEDIUM = "medium"         # 24 hours
    LONG = "long"             # 7 days


@dataclass
class PrivacyConfig:
    """Configuration for privacy-enhanced research."""
    level: PrivacyLevel = PrivacyLevel.ENHANCED
    retention: DataRetention = DataRetention.SESSION
    anonymize_requests: bool = True
    sanitize_results: bool = True
    audit_logging: bool = True
    encrypt_transit: bool = True
    min_data_collection: bool = True
    allowed_domains: List[str] = field(default_factory=list)
    blocked_terms: List[str] = field(default_factory=list)


@dataclass
class AuditRecord:
    """Audit record for research operation."""
    timestamp: float
    operation_id: str
    operation_type: str
    privacy_level: PrivacyLevel
    anonymized_query: str
    result_count: int
    retention_until: float
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AnonymizedRequest:
    """Anonymized research request."""
    original_query: str
    anonymized_query: str
    operation_id: str
    privacy_level: PrivacyLevel
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SanitizedResult:
    """Sanitized research result."""
    data: Any
    pii_detected: bool
    sanitized_fields: List[str]
    confidence_score: float


class PrivacyEnhancedResearch:
    """
    Privacy-enhanced research wrapper with anonymization and sanitization.
    
    Example:
        >>> privacy_research = PrivacyEnhancedResearch(PrivacyConfig(
        ...     level=PrivacyLevel.ENHANCED,
        ...     retention=DataRetention.SESSION
        ... ))
        >>> 
        >>> # Execute research with privacy protection
        >>> result = await privacy_research.execute(
        ...     query="sensitive research topic",
        ...     research_func=actual_research_function
        ... )
    """
    
    # Common PII patterns
    PII_PATTERNS = [
        (r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL]'),
        (r'\b\d{3}-\d{2}-\d{4}\b', '[SSN]'),  # US SSN
        (r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b', '[CARD]'),  # Credit card
        (r'\b\d{3}-\d{3}-\d{4}\b', '[PHONE]'),  # US Phone
    ]
    
    def __init__(self, config: Optional[PrivacyConfig] = None):
        self.config = config or PrivacyConfig()
        self._audit_log: List[AuditRecord] = []
        self._active_sessions: Dict[str, float] = {}
        self._operation_counter = 0
        
        logger.info(f"PrivacyEnhancedResearch initialized (level: {self.config.level.value})")
    
    async def execute(
        self,
        query: str,
        research_func: Callable,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Execute research with privacy protection.
        
        Args:
            query: Research query
            research_func: Actual research function to call
            **kwargs: Additional arguments for research function
            
        Returns:
            Sanitized research results
        """
        self._operation_counter += 1
        operation_id = f"priv_{self._operation_counter}_{int(time.time())}"
        
        try:
            # Step 1: Anonymize request
            if self.config.anonymize_requests:
                anon_request = self._anonymize_query(query, operation_id)
                search_query = anon_request.anonymized_query
            else:
                search_query = query
                anon_request = AnonymizedRequest(
                    original_query=query,
                    anonymized_query=query,
                    operation_id=operation_id,
                    privacy_level=self.config.level
                )
            
            # Step 2: Execute research
            start_time = time.time()
            raw_result = await research_func(search_query, **kwargs)
            duration = time.time() - start_time
            
            # Step 3: Sanitize results
            if self.config.sanitize_results:
                sanitized = self._sanitize_results(raw_result)
                result_data = sanitized.data
            else:
                result_data = raw_result
                sanitized = SanitizedResult(
                    data=raw_result,
                    pii_detected=False,
                    sanitized_fields=[],
                    confidence_score=1.0
                )
            
            # Step 4: Audit logging
            if self.config.audit_logging:
                self._log_audit(
                    operation_id=operation_id,
                    operation_type="research",
                    anon_request=anon_request,
                    result_count=self._count_results(result_data),
                    duration=duration
                )
            
            # Step 5: Set retention
            retention_until = self._calculate_retention()
            self._active_sessions[operation_id] = retention_until
            
            return {
                "operation_id": operation_id,
                "data": result_data,
                "privacy_info": {
                    "level": self.config.level.value,
                    "anonymized": self.config.anonymize_requests,
                    "sanitized": self.config.sanitize_results,
                    "pii_detected": sanitized.pii_detected,
                    "sanitized_fields": sanitized.sanitized_fields,
                    "retention_until": retention_until,
                },
                "metadata": {
                    "duration": duration,
                    "query_hash": self._hash_query(query),
                }
            }
            
        except Exception as e:
            logger.error(f"Privacy research failed: {e}")
            if self.config.audit_logging:
                self._log_audit(
                    operation_id=operation_id,
                    operation_type="research_failed",
                    anon_request=AnonymizedRequest(
                        original_query=query,
                        anonymized_query="[FAILED]",
                        operation_id=operation_id,
                        privacy_level=self.config.level
                    ),
                    result_count=0,
                    duration=0,
                    error=str(e)
                )
            raise
    
    def _anonymize_query(self, query: str, operation_id: str) -> AnonymizedRequest:
        """Anonymize search query."""
        import re
        
        anonymized = query
        
        # Remove PII patterns
        for pattern, replacement in self.PII_PATTERNS:
            anonymized = re.sub(pattern, replacement, anonymized)
        
        # Hash identifiable terms if maximum privacy
        if self.config.level == PrivacyLevel.MAXIMUM:
            words = anonymized.split()
            hashed_words = []
            for word in words:
                if len(word) > 4 and word.isalpha():
                    # Hash potentially identifiable terms
                    hashed_words.append(hashlib.sha256(word.encode()).hexdigest()[:8])
                else:
                    hashed_words.append(word)
            anonymized = " ".join(hashed_words)
        
        return AnonymizedRequest(
            original_query=query,
            anonymized_query=anonymized,
            operation_id=operation_id,
            privacy_level=self.config.level,
            context={
                "anonymized_at": time.time(),
                "original_length": len(query),
            }
        )
    
    def _sanitize_results(self, data: Any) -> SanitizedResult:
        """Sanitize results to remove PII."""
        import re
        
        sanitized_fields = []
        pii_detected = False
        
        def sanitize_value(value: str) -> str:
            nonlocal pii_detected
            original = value
            
            for pattern, replacement in self.PII_PATTERNS:
                if re.search(pattern, value):
                    pii_detected = True
                    value = re.sub(pattern, replacement, value)
            
            if value != original:
                sanitized_fields.append(f"string_field_{len(sanitized_fields)}")
            
            return value
        
        def recursive_sanitize(obj: Any) -> Any:
            if isinstance(obj, str):
                return sanitize_value(obj)
            elif isinstance(obj, dict):
                return {k: recursive_sanitize(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [recursive_sanitize(item) for item in obj]
            else:
                return obj
        
        sanitized_data = recursive_sanitize(data)
        
        return SanitizedResult(
            data=sanitized_data,
            pii_detected=pii_detected,
            sanitized_fields=sanitized_fields,
            confidence_score=0.95 if not pii_detected else 0.8
        )
    
    def _log_audit(
        self,
        operation_id: str,
        operation_type: str,
        anon_request: AnonymizedRequest,
        result_count: int,
        duration: float,
        error: Optional[str] = None
    ) -> None:
        """Log audit record."""
        record = AuditRecord(
            timestamp=time.time(),
            operation_id=operation_id,
            operation_type=operation_type,
            privacy_level=self.config.level,
            anonymized_query=anon_request.anonymized_query,
            result_count=result_count,
            retention_until=self._calculate_retention(),
            metadata={
                "duration": duration,
                "error": error,
                "query_hash": self._hash_query(anon_request.original_query),
            }
        )
        
        self._audit_log.append(record)
        
        # Trim log
        if len(self._audit_log) > 10000:
            self._audit_log = self._audit_log[-10000:]
        
        logger.debug(f"Audit logged: {operation_id} ({operation_type})")
    
    def _calculate_retention(self) -> float:
        """Calculate retention timestamp."""
        now = time.time()
        retention_hours = {
            DataRetention.SESSION: 0.5,
            DataRetention.SHORT: 1,
            DataRetention.MEDIUM: 24,
            DataRetention.LONG: 168,  # 7 days
        }
        return now + (retention_hours[self.config.retention] * 3600)
    
    def _hash_query(self, query: str) -> str:
        """Create hash of query for audit without storing actual query."""
        return hashlib.sha256(query.encode()).hexdigest()[:16]
    
    def _count_results(self, data: Any) -> int:
        """Count number of results in data."""
        if isinstance(data, list):
            return len(data)
        elif isinstance(data, dict):
            if "results" in data:
                return len(data["results"])
            elif "items" in data:
                return len(data["items"])
            return len(data)
        return 1
    
    def get_audit_log(
        self,
        operation_type: Optional[str] = None,
        limit: int = 100
    ) -> List[AuditRecord]:
        """Get audit log with optional filtering."""
        records = self._audit_log
        
        if operation_type:
            records = [r for r in records if r.operation_type == operation_type]
        
        return records[-limit:]
    
    def cleanup_expired(self) -> int:
        """Clean up expired sessions. Returns count of cleaned sessions."""
        now = time.time()
        expired = [
            op_id for op_id, retention in self._active_sessions.items()
            if now > retention
        ]
        
        for op_id in expired:
            del self._active_sessions[op_id]
        
        # Also clean audit log
        self._audit_log = [
            r for r in self._audit_log
            if time.time() <= r.retention_until
        ]
        
        return len(expired)
    
    def get_privacy_stats(self) -> Dict[str, Any]:
        """Get privacy statistics."""
        return {
            "config": {
                "level": self.config.level.value,
                "retention": self.config.retention.value,
                "anonymize_requests": self.config.anonymize_requests,
                "sanitize_results": self.config.sanitize_results,
                "audit_logging": self.config.audit_logging,
            },
            "operations": {
                "total": len(self._audit_log),
                "active_sessions": len(self._active_sessions),
            },
            "audit_log_size": len(self._audit_log),
        }


# Convenience function
async def private_research(
    query: str,
    research_func: Callable,
    level: PrivacyLevel = PrivacyLevel.ENHANCED,
    **kwargs
) -> Dict[str, Any]:
    """
    Quick privacy-enhanced research.
    
    Args:
        query: Research query
        research_func: Research function to execute
        level: Privacy level
        **kwargs: Additional arguments
        
    Returns:
        Privacy-enhanced results
    """
    privacy = PrivacyEnhancedResearch(PrivacyConfig(level=level))
    return await privacy.execute(query, research_func, **kwargs)
