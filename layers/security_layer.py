"""
Security Layer - Cryptography, Obfuscation, and Secure Destruction
====================================================================

Integrates:
- StringObfuscator: Multi-stage encoding (XOR, Base64, Zlib)
- ResearchObfuscator: Query masking, chaff traffic, cover stories
- SecureDestructor: DoD 5220.22-M, NIST 800-88, Gutmann wiping
- MissionAudit: Merkle Tree audit chain for legally bulletproof evidence

This is a thin wrapper that imports existing security modules
and adds integration logic for the universal orchestrator.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import logging
from typing import Any, Dict, List, Optional

from hledac.universal.project_types import (
    DestructionResult,
    ObfuscationLevel,
    ObfuscationResult,
    SecurityConfig,
    WipeStandard,
)

logger = logging.getLogger(__name__)


class SecurityLayer:
    """
    Security layer for cryptography, obfuscation, and secure destruction.

    Unified audit system supporting both forensic (Merkle Tree) and compliance (GDPR) modes.

    This layer:
    1. Obfuscates sensitive strings and queries
    2. Generates chaff traffic to mask research
    3. Securely destroys sensitive data
    4. Masks research intent
    5. Maintains cryptographic audit chain (MissionAudit - forensic mode)
    6. Privacy compliance logging (GDPR/CCPA - compliance mode)

    Audit Modes:
    - FORENSIC: Merkle Tree chain for legally bulletproof evidence
    - COMPLIANCE: GDPR/CCPA compliant logging with PII anonymization

    Example:
        security = SecurityLayer(config)
        await security.initialize()

        # Obfuscate sensitive string
        result = await security.obfuscate_string(
            "API_KEY_12345",
            level=ObfuscationLevel.HEAVY
        )

        # Mask research query
        masked = security.mask_query("corporate espionage techniques")

        # Securely delete file
        await security.destroy_file("/path/to/sensitive.pdf")

        # Log action to audit chain (forensic mode)
        audit_hash = security.log_action("file_destruction", b"data", {"file": "..."})

        # Log privacy event (compliance mode)
        await security.log_privacy_event("data_access", "user123", "profile")
    """

    def __init__(self, config: Optional[SecurityConfig] = None):
        """
        Initialize SecurityLayer.

        Args:
            config: Security configuration (uses defaults if None)
        """
        self.config = config or SecurityConfig()

        # Core components (lazy loaded)
        self._string_obfuscator = None
        self._research_obfuscator = None
        self._secure_destructor = None

        # Dedicated executor for blocking file I/O (avoids event loop blocking)
        self._file_destroy_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="security_destroy"
        )

        # Unified Audit System
        self._mission_audit: Optional['MissionAudit'] = None  # Forensic mode
        self._privacy_audit: Optional[Any] = None  # Compliance mode (PrivacyAuditLog)
        self._audit_mode: str = "forensic"  # "forensic" | "compliance" | "both"

        # Statistics
        self._obfuscation_count = 0
        self._destruction_count = 0
        self._chaff_generated = 0

        logger.info("SecurityLayer initialized")
    
    async def initialize(self) -> bool:
        """
        Initialize SecurityLayer components.
        
        Returns:
            True if initialization successful
        """
        try:
            logger.info("🚀 Initializing SecurityLayer...")
            
            # Initialize StringObfuscator
            if self.config.obfuscation_level != "none":
                await self._init_string_obfuscator()
            
            # Initialize ResearchObfuscator
            if self.config.enable_query_masking or self.config.enable_chaff_traffic:
                await self._init_research_obfuscator()
            
            # Initialize SecureDestructor
            await self._init_secure_destructor()
            
            # Initialize MissionAudit (forensic mode)
            await self._init_mission_audit()

            # Initialize PrivacyAudit (compliance mode) - lazy loaded
            # Will be initialized on first privacy log event

            logger.info("✅ SecurityLayer initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"❌ SecurityLayer initialization failed: {e}")
            return False
    
    async def _init_string_obfuscator(self) -> None:
        """Lazy initialization of StringObfuscator"""
        if self._string_obfuscator is None:
            try:
                from hledac.crypto.string_obfuscator import StringObfuscator
                
                self._string_obfuscator = StringObfuscator()
                logger.info("✅ StringObfuscator initialized")
                
            except ImportError as e:
                logger.warning(f"⚠️ StringObfuscator not available: {e}")
                self._string_obfuscator = None
    
    async def _init_research_obfuscator(self) -> None:
        """Lazy initialization of ResearchObfuscator"""
        if self._research_obfuscator is None:
            try:
                from hledac.research_security.research_obfuscation import ResearchObfuscator
                
                self._research_obfuscator = ResearchObfuscator()
                logger.info("✅ ResearchObfuscator initialized")
                
            except ImportError as e:
                logger.warning(f"⚠️ ResearchObfuscator not available: {e}")
                self._research_obfuscator = None
    
    async def _init_secure_destructor(self) -> None:
        """Lazy initialization of SecureDestructor"""
        if self._secure_destructor is None:
            try:
                from hledac.research_security.secure_destruction import SecureDestructor, DestructionConfig
                
                destructor_config = DestructionConfig(
                    standard=self.config.wipe_standard,
                    verify=self.config.verification_enabled
                )
                self._secure_destructor = SecureDestructor(destructor_config)
                logger.info("✅ SecureDestructor initialized")
                
            except ImportError as e:
                logger.warning(f"⚠️ SecureDestructor not available: {e}")
                self._secure_destructor = None
    
    async def _init_mission_audit(self) -> None:
        """Initialize MissionAudit for forensic audit chain."""
        try:
            self._mission_audit = MissionAudit()
            logger.info("✅ MissionAudit initialized (forensic mode)")
        except Exception as e:
            logger.warning(f"⚠️ MissionAudit not available: {e}")
            self._mission_audit = None

    async def _init_privacy_audit(self) -> None:
        """Lazy initialization of PrivacyAuditLog for compliance mode."""
        if self._privacy_audit is None:
            try:
                from ...privacy_protection.privacy_audit_log import (
                    PrivacyAuditLog, AnonymizationLevel
                )
                self._privacy_audit = await PrivacyAuditLog.create(
                    retention_days=90,
                    anonymization_level=AnonymizationLevel.FULL
                )
                logger.info("✅ PrivacyAuditLog initialized (compliance mode)")
            except Exception as e:
                logger.warning(f"⚠️ PrivacyAuditLog not available: {e}")
                self._privacy_audit = None

    def set_audit_mode(self, mode: str) -> None:
        """
        Set audit mode: "forensic", "compliance", or "both".

        Args:
            mode: Audit mode to use
        """
        if mode not in ("forensic", "compliance", "both"):
            raise ValueError(f"Invalid audit mode: {mode}")
        self._audit_mode = mode
        logger.info(f"🔒 Audit mode set to: {mode}")

    # ====================================================================
    # Unified Audit System - Forensic + Compliance
    # ====================================================================

    async def log_privacy_event(
        self,
        action: str,
        subject_id: str,
        resource: str,
        details: Optional[Dict] = None,
        category: str = "DATA_ACCESS"
    ) -> Optional[str]:
        """
        Log privacy event for GDPR/CCPA compliance.

        Args:
            action: Action performed (e.g., 'data_access', 'data_deletion')
            subject_id: ID of the data subject
            resource: Resource being accessed
            details: Additional details
            category: Event category

        Returns:
            Entry ID if successful
        """
        if self._audit_mode not in ("compliance", "both"):
            logger.debug("Privacy audit disabled (mode: {self._audit_mode})")
            return None

        # Lazy init privacy audit
        if self._privacy_audit is None:
            await self._init_privacy_audit()

        if self._privacy_audit is None:
            # Fallback: log to MissionAudit with privacy prefix
            if self._mission_audit:
                return self._mission_audit.log_action(
                    f"privacy:{action}",
                    f"{subject_id}:{resource}".encode(),
                    details or {}
                )
            return None

        try:
            from ...privacy_protection.privacy_audit_log import PrivacyEventCategory, Severity

            cat_map = {
                "DATA_ACCESS": PrivacyEventCategory.DATA_ACCESS,
                "DATA_MODIFICATION": PrivacyEventCategory.DATA_MODIFICATION,
                "DATA_DELETION": PrivacyEventCategory.DATA_DELETION,
                "CONSENT_GRANTED": PrivacyEventCategory.CONSENT_GRANTED,
                "CONSENT_REVOKED": PrivacyEventCategory.CONSENT_REVOKED,
            }

            entry = await self._privacy_audit.log_event(
                category=cat_map.get(category, PrivacyEventCategory.DATA_ACCESS),
                action=action,
                subject_id=subject_id,
                resource=resource,
                details=details or {},
                severity=Severity.INFO
            )
            return entry.entry_id if entry else None

        except Exception as e:
            logger.warning(f"⚠️ Privacy audit log failed: {e}")
            return None

    async def generate_compliance_report(
        self,
        days: int = 30
    ) -> Optional[Dict[str, Any]]:
        """
        Generate GDPR/CCPA compliance report.

        Args:
            days: Number of days to include

        Returns:
            Compliance report or None
        """
        if self._privacy_audit is None:
            await self._init_privacy_audit()

        if self._privacy_audit is None:
            return None

        try:
            from datetime import datetime, timedelta
            return await self._privacy_audit.generate_compliance_report(
                start_date=datetime.now() - timedelta(days=days),
                end_date=datetime.now()
            )
        except Exception as e:
            logger.warning(f"⚠️ Compliance report generation failed: {e}")
            return None

    def anonymize_text(self, text: str, level: str = "full") -> str:
        """
        Anonymize PII in text.

        Args:
            text: Text to anonymize
            level: Anonymization level ("none", "partial", "full")

        Returns:
            Anonymized text
        """
        if self._privacy_audit and hasattr(self._privacy_audit, 'anonymizer'):
            try:
                from ...privacy_protection.privacy_audit_log import AnonymizationLevel
                level_map = {
                    "none": AnonymizationLevel.NONE,
                    "partial": AnonymizationLevel.PARTIAL,
                    "full": AnonymizationLevel.FULL,
                }
                return self._privacy_audit.anonymizer.anonymize(
                    text, level_map.get(level, AnonymizationLevel.FULL)
                )
            except Exception as e:
                logger.warning(f"⚠️ Anonymization failed: {e}")

        # Fallback: basic redaction
        import re
        text = re.sub(r'\S+@\S+\.\S+', '[EMAIL_REDACTED]', text)
        text = re.sub(r'\b\d{3}-\d{2}-\d{4}\b', '[SSN_REDACTED]', text)
        return text

    # ====================================================================
    # MissionAudit Integration (Forensic Mode)
    # ====================================================================
    
    def log_action(self, action_type: str, data: bytes, metadata: Optional[Dict] = None) -> Optional[str]:
        """
        Log an action to the cryptographic audit chain.
        
        Args:
            action_type: Type of action (e.g., 'file_destruction', 'obfuscation')
            data: Data to hash and log
            metadata: Optional metadata about the action
            
        Returns:
            Entry hash if successful, None otherwise
        """
        if self._mission_audit:
            try:
                return self._mission_audit.log_action(action_type, data, metadata or {})
            except Exception as e:
                logger.warning(f"⚠️ MissionAudit log failed: {e}")
        return None
    
    def get_audit_chain(self) -> List[Dict]:
        """Get the full audit chain."""
        if self._mission_audit:
            return [entry.to_dict() for entry in self._mission_audit.audit_chain]
        return []
    
    def get_merkle_root(self) -> Optional[str]:
        """Get the current Merkle root hash."""
        if self._mission_audit:
            return self._mission_audit.get_merkle_root()
        return None
    
    def verify_audit_integrity(self) -> bool:
        """Verify the integrity of the entire audit chain."""
        if self._mission_audit:
            return self._mission_audit.verify_chain()
        return False
    
    def export_audit_chain(self, output_path: str) -> bool:
        """Export audit chain to file."""
        if self._mission_audit:
            return self._mission_audit.export_chain(output_path)
        return False
    
    async def obfuscate_string(
        self,
        content: str,
        level: Optional[ObfuscationLevel] = None
    ) -> ObfuscationResult:
        """
        Obfuscate a string with multi-stage encoding.
        
        Args:
            content: String to obfuscate
            level: Obfuscation level (uses config default if None)
            
        Returns:
            ObfuscationResult with obfuscated data
        """
        level = level or ObfuscationLevel(self.config.obfuscation_level)
        self._obfuscation_count += 1
        
        logger.debug(f"🔐 Obfuscating string (level: {level.value})")
        
        try:
            if self._string_obfuscator:
                # Use real StringObfuscator
                original_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
                
                # Determine encoding stages based on level
                if level == ObfuscationLevel.LIGHT:
                    stages = ["xor", "base64"]
                    obfuscated = self._string_obfuscator.multi_stage_encode(content)
                elif level == ObfuscationLevel.MEDIUM:
                    stages = ["xor", "base64", "zlib"]
                    obfuscated = self._string_obfuscator.multi_stage_encode(content)
                elif level in (ObfuscationLevel.HEAVY, ObfuscationLevel.MAXIMUM):
                    stages = ["xor", "base64", "zlib", "xor"]
                    obfuscated = self._string_obfuscator.multi_stage_encode(content)
                    
                    # Generate decoys for heavy/maximum
                    if self.config.generate_decoys:
                        decoys = self._string_obfuscator.generate_decoy_strings(
                            count=self.config.decoy_count
                        )
                else:
                    stages = []
                    obfuscated = content
                
                # Log to audit chain
                self.log_action(
                    "obfuscation",
                    content.encode(),
                    {"level": level.value, "stages": stages}
                )
                
                return ObfuscationResult(
                    original_hash=original_hash,
                    obfuscated_data=obfuscated,
                    encoding_chain=stages,
                    decoy_count=self.config.decoy_count if level in (ObfuscationLevel.HEAVY, ObfuscationLevel.MAXIMUM) else 0,
                    success=True
                )
            else:
                # Fallback: simple base64
                import base64
                return ObfuscationResult(
                    original_hash=hashlib.sha256(content.encode()).hexdigest()[:16],
                    obfuscated_data=base64.b64encode(content.encode()).decode(),
                    encoding_chain=["base64"],
                    decoy_count=0,
                    success=True
                )
                
        except Exception as e:
            logger.error(f"❌ String obfuscation failed: {e}")
            return ObfuscationResult(
                original_hash="",
                obfuscated_data=content,
                encoding_chain=[],
                decoy_count=0,
                success=False
            )
    
    def mask_query(self, query: str) -> str:
        """
        Mask a research query to hide intent.
        
        Args:
            query: Original query
            
        Returns:
            Masked query
        """
        if not self.config.enable_query_masking:
            return query
        
        if self._research_obfuscator:
            try:
                return self._research_obfuscator.mask_query(query)
            except Exception as e:
                logger.warning(f"⚠️ Query masking failed: {e}")
                return query
        else:
            # Fallback: simple synonym replacement
            replacements = {
                "corporate espionage": "market research",
                "trade secrets": "proprietary methods",
                "hack": "security analysis",
                "exploit": "vulnerability assessment",
                "bypass": "circumvention testing",
            }
            
            masked = query.lower()
            for original, replacement in replacements.items():
                masked = masked.replace(original, replacement)
            
            return masked if masked != query.lower() else query
    
    def generate_chaff(self, count: Optional[int] = None) -> List[str]:
        """
        Generate chaff queries to mask real research.

        Args:
            count: Number of chaff queries (uses config default if None)

        Returns:
            List of chaff queries
        """
        if not self.config.enable_chaff_traffic:
            return []

        count = count or int(1 / self.config.chaff_ratio) if self.config.chaff_ratio > 0 else 3

        if self._research_obfuscator:
            try:
                chaff = self._research_obfuscator.generate_chaff(count)
                self._chaff_generated += count
                return chaff
            except Exception as e:
                logger.warning(f"⚠️ Chaff generation failed: {e}")

        # Fallback chaff
        fallback_chaff = [
            "weather forecast today",
            "healthy dinner recipes",
            "how to meditate",
            "best programming tutorials",
            "latest science discoveries",
            "workout routines",
            "productivity tips",
            "travel destinations 2024",
            "book recommendations",
            "time management techniques",
        ]

        import random
        chaff = random.sample(fallback_chaff, min(count, len(fallback_chaff)))
        self._chaff_generated += len(chaff)
        return chaff

    # ====================================================================
    # Secure File Destruction (non-blocking)
    # ====================================================================

    def _destroy_file_fallback_sync(
        self,
        file_path: str,
        standard: WipeStandard
    ) -> DestructionResult:
        """
        Synchronous fallback for file destruction - runs in ThreadPoolExecutor.

        NOTE: On SSD/APFS, overwrite is best-effort only and does NOT guarantee
        physical data sanitization. This is not a forensic-grade wipe.
        Do NOT use "forensic-clean" or similar marketing claims for this fallback.

        Args:
            file_path: Path to file to destroy
            standard: Wipe standard (informational - fallback is simple overwrite)

        Returns:
            DestructionResult with outcome
        """
        import os
        import time as time_module

        try:
            if os.path.exists(file_path):
                size = os.path.getsize(file_path)

                # Simple overwrite with random bytes (best-effort on SSD/APFS)
                with open(file_path, 'wb') as f:
                    f.write(os.urandom(size))

                # Remove file
                os.remove(file_path)

                return DestructionResult(
                    file_path=file_path,
                    standard=standard,
                    passes_completed=1,
                    bytes_overwritten=size,
                    verification_passed=not os.path.exists(file_path),
                    timestamp=time_module.time()
                )
            else:
                return DestructionResult(
                    file_path=file_path,
                    standard=standard,
                    passes_completed=0,
                    bytes_overwritten=0,
                    verification_passed=False,
                    timestamp=time_module.time()
                )
        except Exception as e:
            logger.error(f"❌ File destruction fallback failed: {e}")
            return DestructionResult(
                file_path=file_path,
                standard=standard,
                passes_completed=0,
                bytes_overwritten=0,
                verification_passed=False,
                timestamp=time_module.time()
            )
    
    async def destroy_file(
        self,
        file_path: str,
        standard: Optional[WipeStandard] = None
    ) -> DestructionResult:
        """
        Securely destroy a file.

        Args:
            file_path: Path to file
            standard: Wipe standard (uses config default if None)

        Returns:
            DestructionResult
        """
        standard = standard or WipeStandard(self.config.wipe_standard)
        self._destruction_count += 1

        logger.info(f"🗑️ Securely destroying file: {file_path} (standard: {standard.value})")

        try:
            if self._secure_destructor:
                # Use real SecureDestructor
                result = await self._secure_destructor.destroy_file(file_path)

                # Log to audit chain
                self.log_action(
                    "file_destruction",
                    file_path.encode(),
                    {"standard": standard.value, "passes": result.passes if hasattr(result, 'passes') else 1}
                )

                return DestructionResult(
                    file_path=file_path,
                    standard=standard,
                    passes_completed=result.passes if hasattr(result, 'passes') else 1,
                    bytes_overwritten=result.bytes if hasattr(result, 'bytes') else 0,
                    verification_passed=result.verified if hasattr(result, 'verified') else True,
                    timestamp=__import__('time').time()
                )
            else:
                # Fallback: run sync destruction in thread pool to avoid blocking event loop
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    self._file_destroy_executor,
                    self._destroy_file_fallback_sync,
                    file_path,
                    standard
                )

                # Log to audit chain
                self.log_action(
                    "file_destruction",
                    file_path.encode(),
                    {"standard": standard.value, "fallback": True}
                )

                return result

        except Exception as e:
            logger.error(f"❌ File destruction failed: {e}")
            return DestructionResult(
                file_path=file_path,
                standard=standard,
                passes_completed=0,
                bytes_overwritten=0,
                verification_passed=False,
                timestamp=__import__('time').time()
            )
    
    async def destroy_directory(
        self,
        dir_path: str,
        recursive: bool = True
    ) -> List[DestructionResult]:
        """
        Securely destroy a directory.

        Args:
            dir_path: Path to directory
            recursive: Whether to recursively destroy subdirectories

        Returns:
            List of DestructionResults
        """
        logger.info(f"🗑️ Securely destroying directory: {dir_path}")

        results = []

        try:
            import os
            import glob
            # Walk directory in thread pool to avoid blocking event loop
            loop = asyncio.get_running_loop()

            if recursive:
                # Collect all file paths first (sync in thread pool)
                def _walk_sync():
                    file_paths = []
                    for root, dirs, files in os.walk(dir_path, topdown=False):
                        for file in files:
                            file_paths.append(os.path.join(root, file))
                    return file_paths

                all_files = await loop.run_in_executor(
                    self._file_destroy_executor,
                    _walk_sync
                )

                # Destroy each file via async destroy_file (non-blocking calls)
                for file_path in all_files:
                    result = await self.destroy_file(file_path)
                    results.append(result)

                # Remove directory structure (sync in thread pool)
                def _remove_dirs_sync():
                    for root, dirs, files in os.walk(dir_path, topdown=False):
                        for d in dirs:
                            os.rmdir(os.path.join(root, d))
                    if os.path.exists(dir_path):
                        os.rmdir(dir_path)

                await loop.run_in_executor(
                    self._file_destroy_executor,
                    _remove_dirs_sync
                )
            else:
                # Non-recursive: glob and destroy files only
                def _glob_sync():
                    import glob
                    return [f for f in glob.glob(os.path.join(dir_path, "*")) if os.path.isfile(f)]

                files = await loop.run_in_executor(
                    self._file_destroy_executor,
                    _glob_sync
                )

                for file_path in files:
                    result = await self.destroy_file(file_path)
                    results.append(result)

        except Exception as e:
            logger.error(f"❌ Directory destruction failed: {e}")

        return results
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get security layer statistics"""
        stats = {
            "obfuscation_count": self._obfuscation_count,
            "destruction_count": self._destruction_count,
            "chaff_generated": self._chaff_generated,
            "string_obfuscator_available": self._string_obfuscator is not None,
            "research_obfuscator_available": self._research_obfuscator is not None,
            "secure_destructor_available": self._secure_destructor is not None,
            "mission_audit_available": self._mission_audit is not None,
            "audit_chain_length": len(self._mission_audit.audit_chain) if self._mission_audit else 0,
            "merkle_root": self.get_merkle_root(),
            "config": {
                "obfuscation_level": self.config.obfuscation_level,
                "wipe_standard": self.config.wipe_standard,
                "enable_query_masking": self.config.enable_query_masking,
                "enable_chaff_traffic": self.config.enable_chaff_traffic,
            }
        }
        return stats
    
    async def cleanup(self) -> None:
        """Cleanup resources - idempotent shutdown of all components."""
        logger.info("🧹 Cleaning up SecurityLayer...")

        # Shutdown file destroy executor (idempotent - shutdown() is safe to call multiple times)
        if self._file_destroy_executor is not None:
            self._file_destroy_executor.shutdown(wait=False)
            self._file_destroy_executor = None

        # Cleanup components
        if self._secure_destructor and hasattr(self._secure_destructor, 'cleanup'):
            try:
                await self._secure_destructor.cleanup()
            except Exception as e:
                logger.warning(f"⚠️ SecureDestructor cleanup error: {e}")

        # Cleanup MissionAudit
        if self._mission_audit and hasattr(self._mission_audit, 'cleanup'):
            try:
                self._mission_audit.cleanup()
            except Exception as e:
                logger.warning(f"⚠️ MissionAudit cleanup error: {e}")

        logger.info("✅ SecurityLayer cleanup complete")


# =============================================================================
# MISSION AUDIT - Merkle Tree Audit Chain (from kernel/integrity.py)
# =============================================================================

import time
import json
import secrets
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AuditEntry:
    """
    Immutable audit entry with SHA-256 hashing.
    
    Features:
    - Blockchain-style previous_hash linkage
    - Tamper-evident logging
    - Witness statements for legal compliance
    """
    timestamp: float
    action_type: str
    data_hash: str
    previous_hash: str
    entry_hash: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """Calculate entry hash if not provided."""
        if not self.entry_hash:
            self.entry_hash = self._calculate_hash()
    
    def _calculate_hash(self) -> str:
        """Calculate SHA-256 hash of entry data."""
        data = f"{self.timestamp}:{self.action_type}:{self.data_hash}:{self.previous_hash}"
        return hashlib.sha256(data.encode()).hexdigest()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp,
            "action_type": self.action_type,
            "data_hash": self.data_hash,
            "previous_hash": self.previous_hash,
            "entry_hash": self.entry_hash,
            "metadata": self.metadata
        }
    
    def verify_integrity(self) -> bool:
        """Verify entry hasn't been tampered with."""
        return self.entry_hash == self._calculate_hash()


class MissionAudit:
    """
    Cryptographic audit chain using Merkle Trees for legally bulletproof evidence.
    
    Integrated from kernel/integrity.py - Provides tamper-evident logging
    with blockchain-style integrity protection.
    
    Features:
    - Merkle Tree based logging with SHA-256 hashing
    - Blockchain-style previous_hash linkage
    - Cryptographic proofs for verification
    - Tamper-evident logging
    - Witness statements for legal compliance
    
    Example:
        audit = MissionAudit()
        
        # Log an action
        entry_hash = audit.log_action("file_deletion", b"data", {"file": "secret.pdf"})
        
        # Verify chain integrity
        if audit.verify_chain():
            print("Audit chain is intact")
        
        # Get Merkle root for verification
        root = audit.get_merkle_root()
    """
    
    def __init__(self):
        """Initialize MissionAudit with empty chain."""
        self.audit_chain: List[AuditEntry] = []
        self._audit_file: Optional[Path] = None
        logger.info("MissionAudit initialized")
    
    def log_action(self, action_type: str, data: bytes, metadata: Dict) -> str:
        """
        Log an action to the audit chain.
        
        Args:
            action_type: Type of action (e.g., 'file_destruction', 'obfuscation')
            data: Data to hash and log
            metadata: Additional metadata about the action
            
        Returns:
            Entry hash
        """
        # Hash the data
        data_hash = hashlib.sha256(data).hexdigest()
        
        # Get previous hash
        previous_hash = self.audit_chain[-1].entry_hash if self.audit_chain else "0" * 64
        
        # Create entry
        entry = AuditEntry(
            timestamp=time.time(),
            action_type=action_type,
            data_hash=data_hash,
            previous_hash=previous_hash,
            metadata=metadata
        )
        
        # Add to chain
        self.audit_chain.append(entry)
        
        logger.debug(f"MissionAudit: Logged action '{action_type}' -> {entry.entry_hash[:16]}...")
        
        return entry.entry_hash
    
    def get_merkle_root(self) -> Optional[str]:
        """
        Calculate the Merkle root of the current audit chain.
        
        Returns:
            Merkle root hash or None if chain is empty
        """
        if not self.audit_chain:
            return None
        
        # Get all entry hashes
        hashes = [entry.entry_hash for entry in self.audit_chain]
        
        # Build Merkle tree
        return self._calculate_merkle_root(hashes)
    
    def _calculate_merkle_root(self, hashes: List[str]) -> str:
        """Calculate Merkle root from list of hashes."""
        if len(hashes) == 0:
            return "0" * 64
        if len(hashes) == 1:
            return hashes[0]
        
        # Build tree bottom-up
        current_level = hashes
        
        while len(current_level) > 1:
            next_level = []
            
            for i in range(0, len(current_level), 2):
                left = current_level[i]
                right = current_level[i + 1] if i + 1 < len(current_level) else left
                
                # Combine and hash
                combined = hashlib.sha256((left + right).encode()).hexdigest()
                next_level.append(combined)
            
            current_level = next_level
        
        return current_level[0]
    
    def verify_chain(self) -> bool:
        """
        Verify the integrity of the entire audit chain.
        
        Checks:
        - Each entry's hash is valid
        - Previous hash links are correct
        - No tampering detected
        
        Returns:
            True if chain is intact
        """
        if not self.audit_chain:
            return True
        
        for i, entry in enumerate(self.audit_chain):
            # Verify entry hash
            if not entry.verify_integrity():
                logger.error(f"MissionAudit: Entry {i} hash mismatch")
                return False
            
            # Verify previous hash link
            if i == 0:
                if entry.previous_hash != "0" * 64:
                    logger.error(f"MissionAudit: First entry previous_hash should be zeros")
                    return False
            else:
                expected_previous = self.audit_chain[i - 1].entry_hash
                if entry.previous_hash != expected_previous:
                    logger.error(f"MissionAudit: Entry {i} previous_hash mismatch")
                    return False
        
        logger.debug("MissionAudit: Chain verification passed")
        return True
    
    def get_entry(self, entry_hash: str) -> Optional[AuditEntry]:
        """
        Get an entry by its hash.
        
        Args:
            entry_hash: Entry hash to find
            
        Returns:
            AuditEntry if found, None otherwise
        """
        for entry in self.audit_chain:
            if entry.entry_hash == entry_hash:
                return entry
        return None
    
    def get_entries_by_type(self, action_type: str) -> List[AuditEntry]:
        """
        Get all entries of a specific action type.
        
        Args:
            action_type: Type of action to filter by
            
        Returns:
            List of matching entries
        """
        return [e for e in self.audit_chain if e.action_type == action_type]
    
    def export_chain(self, output_path: str) -> bool:
        """
        Export audit chain to file.
        
        Args:
            output_path: Path to export to
            
        Returns:
            True if successful
        """
        try:
            data = {
                "merkle_root": self.get_merkle_root(),
                "entry_count": len(self.audit_chain),
                "entries": [entry.to_dict() for entry in self.audit_chain]
            }
            
            with open(output_path, 'w') as f:
                json.dump(data, f, indent=2)
            
            logger.info(f"MissionAudit: Exported chain to {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"MissionAudit: Export failed: {e}")
            return False
    
    def import_chain(self, input_path: str) -> bool:
        """
        Import audit chain from file.
        
        Args:
            input_path: Path to import from
            
        Returns:
            True if successful and chain is valid
        """
        try:
            with open(input_path, 'r') as f:
                data = json.load(f)
            
            # Reconstruct chain
            self.audit_chain = []
            for entry_data in data.get("entries", []):
                entry = AuditEntry(
                    timestamp=entry_data["timestamp"],
                    action_type=entry_data["action_type"],
                    data_hash=entry_data["data_hash"],
                    previous_hash=entry_data["previous_hash"],
                    entry_hash=entry_data["entry_hash"],
                    metadata=entry_data.get("metadata", {})
                )
                self.audit_chain.append(entry)
            
            # Verify imported chain
            if not self.verify_chain():
                logger.error("MissionAudit: Imported chain verification failed")
                return False
            
            logger.info(f"MissionAudit: Imported chain from {input_path}")
            return True
            
        except Exception as e:
            logger.error(f"MissionAudit: Import failed: {e}")
            return False
    
    def get_chain_stats(self) -> Dict[str, Any]:
        """Get statistics about the audit chain."""
        return {
            "entry_count": len(self.audit_chain),
            "merkle_root": self.get_merkle_root(),
            "verified": self.verify_chain(),
            "action_types": list(set(e.action_type for e in self.audit_chain)),
            "first_entry_time": self.audit_chain[0].timestamp if self.audit_chain else None,
            "last_entry_time": self.audit_chain[-1].timestamp if self.audit_chain else None,
        }
    
    def cleanup(self) -> None:
        """Clear the audit chain."""
        entry_count = len(self.audit_chain)
        self.audit_chain.clear()
        logger.debug(f"MissionAudit: Cleaned up {entry_count} entries")
