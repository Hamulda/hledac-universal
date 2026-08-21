"""
Security Layer - Cryptography, Privacy, and Secure Destruction
===========================================================

Consolidated from:
- security_layer.py: SecurityLayer + MissionAudit + AuditEntry
- privacy_layer.py: PrivacyLayer

Features:
- StringObfuscator: Multi-stage encoding (XOR, Base64, Zlib)
- ResearchObfuscator: Query masking, chaff traffic
- SecureDestructor: DoD 5220.22-M, NIST 800-88 wiping
- MissionAudit: Merkle Tree audit chain
- Privacy management: VPN/Tor/DNS, PGP, GDPR compliance
- PII anonymization

M1 8GB: Uses __slots__ for memory efficiency.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import time
from pathlib import Path
from typing import Any

import msgspec.json as _json

from compat.msgspec_gc_compat import Struct
from hledac.universal.project_types import (
    DestructionResult,
    ObfuscationLevel,
    ObfuscationResult,
    SecurityConfig,
    WipeStandard,
)

logger = logging.getLogger(__name__)

__all__ = [
    "SecurityLayer",
    "MissionAudit",
    "AuditEntry",
    "StringObfuscator",
    "ResearchObfuscator",
    "SecureDestructor",
]

# Crypto-safe RNG — F350M-R
_RNG = secrets.SystemRandom()


class AuditEntry(Struct, gc=False):
    """Immutable audit entry with SHA-256 hashing."""

    timestamp: float
    action_type: str
    data_hash: str
    previous_hash: str
    entry_hash: str = ""
    metadata: dict[str, Any] = {}

    def __post_init__(self) -> None:
        if not self.entry_hash:
            object.__setattr__(self, "entry_hash", self._calculate_hash())

    def _calculate_hash(self) -> str:
        data = f"{self.timestamp}:{self.action_type}:{self.data_hash}:{self.previous_hash}"
        return hashlib.sha256(data.encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "action_type": self.action_type,
            "data_hash": self.data_hash,
            "previous_hash": self.previous_hash,
            "entry_hash": self.entry_hash,
            "metadata": self.metadata,
        }

    def verify_integrity(self) -> bool:
        return self.entry_hash == self._calculate_hash()


class MissionAudit:
    """
    Cryptographic audit chain using Merkle Trees for legally bulletproof evidence.

    Features:
    - Merkle Tree based logging with SHA-256 hashing
    - Blockchain-style previous_hash linkage
    - Cryptographic proofs for verification
    - Tamper-evident logging

    M1 8GB: Uses __slots__ for memory efficiency.
    """

    __slots__ = ("_audit_file", "audit_chain")

    def __init__(self) -> None:
        self.audit_chain: list[AuditEntry] = []
        self._audit_file: Path | None = None
        logger.info("MissionAudit initialized")

    def log_action(self, action_type: str, data: bytes, metadata: dict) -> str:
        """Log an action to the audit chain."""
        data_hash = hashlib.sha256(data).hexdigest()
        previous_hash = self.audit_chain[-1].entry_hash if self.audit_chain else "0" * 64
        entry = AuditEntry(
            timestamp=time.time(),
            action_type=action_type,
            data_hash=data_hash,
            previous_hash=previous_hash,
            metadata=metadata,
        )
        self.audit_chain.append(entry)
        logger.debug(f"MissionAudit: Logged '{action_type}' -> {entry.entry_hash[:16]}...")
        return entry.entry_hash

    def get_merkle_root(self) -> str | None:
        """Calculate the Merkle root of the current audit chain."""
        if not self.audit_chain:
            return None
        hashes = [entry.entry_hash for entry in self.audit_chain]
        return self._calculate_merkle_root(hashes)

    def _calculate_merkle_root(self, hashes: list[str]) -> str:
        """Calculate Merkle root from list of hashes."""
        if not hashes:
            return "0" * 64
        if len(hashes) == 1:
            return hashes[0]
        current_level = hashes
        while len(current_level) > 1:
            next_level = []
            for i in range(0, len(current_level), 2):
                left = current_level[i]
                right = current_level[i + 1] if i + 1 < len(current_level) else left
                combined = hashlib.sha256((left + right).encode()).hexdigest()
                next_level.append(combined)
            current_level = next_level
        return current_level[0]

    def verify_chain(self) -> bool:
        """Verify the integrity of the entire audit chain."""
        if not self.audit_chain:
            return True
        for i, entry in enumerate(self.audit_chain):
            if not entry.verify_integrity():
                logger.error(f"MissionAudit: Entry {i} hash mismatch")
                return False
            if i == 0:
                if entry.previous_hash != "0" * 64:
                    logger.error("MissionAudit: First entry previous_hash should be zeros")
                    return False
            else:
                expected_previous = self.audit_chain[i - 1].entry_hash
                if entry.previous_hash != expected_previous:
                    logger.error(f"MissionAudit: Entry {i} previous_hash mismatch")
                    return False
        logger.debug("MissionAudit: Chain verification passed")
        return True

    def get_entry(self, entry_hash: str) -> AuditEntry | None:
        """Get an entry by its hash."""
        for entry in self.audit_chain:
            if entry.entry_hash == entry_hash:
                return entry
        return None

    def get_entries_by_type(self, action_type: str) -> list[AuditEntry]:
        """Get all entries of a specific action type."""
        return [e for e in self.audit_chain if e.action_type == action_type]

    def export_chain(self, output_path: str) -> bool:
        """Export audit chain to file."""
        try:
            data = {
                "merkle_root": self.get_merkle_root(),
                "entry_count": len(self.audit_chain),
                "entries": [entry.to_dict() for entry in self.audit_chain],
            }
            with open(output_path, "w") as f:
                f.write(_json.encode(data, indent=2).decode("utf-8"))
            logger.info(f"MissionAudit: Exported chain to {output_path}")
            return True
        except Exception as e:
            logger.error(f"MissionAudit: Export failed: {e}")
            return False

    def import_chain(self, input_path: str) -> bool:
        """Import audit chain from file."""
        try:
            with open(input_path) as f:
                data = _json.decode(f.read())
            self.audit_chain = []
            for entry_data in data.get("entries", []):
                entry = AuditEntry(
                    timestamp=entry_data["timestamp"],
                    action_type=entry_data["action_type"],
                    data_hash=entry_data["data_hash"],
                    previous_hash=entry_data["previous_hash"],
                    entry_hash=entry_data["entry_hash"],
                    metadata=entry_data.get("metadata", {}),
                )
                self.audit_chain.append(entry)
            if not self.verify_chain():
                logger.error("MissionAudit: Imported chain verification failed")
                return False
            logger.info(f"MissionAudit: Imported chain from {input_path}")
            return True
        except Exception as e:
            logger.error(f"MissionAudit: Import failed: {e}")
            return False

    def get_chain_stats(self) -> dict[str, Any]:
        """Get statistics about the audit chain."""
        return {
            "entry_count": len(self.audit_chain),
            "merkle_root": self.get_merkle_root(),
            "verified": self.verify_chain(),
            "action_types": list({e.action_type for e in self.audit_chain}),
            "first_entry_time": self.audit_chain[0].timestamp if self.audit_chain else None,
            "last_entry_time": self.audit_chain[-1].timestamp if self.audit_chain else None,
        }

    def cleanup(self) -> None:
        """Clear the audit chain."""
        entry_count = len(self.audit_chain)
        self.audit_chain.clear()
        logger.debug(f"MissionAudit: Cleaned up {entry_count} entries")


class SecurityLayer:
    """
    Security layer for cryptography, obfuscation, and secure destruction.

    Features:
    - FORENSIC mode: Merkle Tree chain for legally bulletproof evidence
    - COMPLIANCE mode: GDPR/CCPA compliant logging with PII anonymization

    M1 8GB: Uses __slots__ for memory efficiency.
    """

    layer_name: str = "security"
    _priority: int = 90  # High priority

    __slots__ = (
        "_audit_mode",
        "_chaff_generated",
        "_destruction_count",
        "_mission_audit",
        "_obfuscation_count",
        "_privacy_audit",
        "_research_obfuscator",
        "_secure_destructor",
        "_string_obfuscator",
        "config",
    )

    def __init__(self, config: SecurityConfig | None = None) -> None:
        self.config = config or SecurityConfig()
        self._string_obfuscator = None
        self._research_obfuscator = None
        self._secure_destructor = None
        self._mission_audit: MissionAudit | None = None
        self._privacy_audit: Any | None = None
        self._audit_mode: str = "forensic"
        self._obfuscation_count = 0
        self._destruction_count = 0
        self._chaff_generated = 0
        logger.info("SecurityLayer initialized")

    async def mount(self, ctx: Any) -> None:
        """Mount the security layer."""
        await self.initialize()
        ctx.set("security", self)

    async def unmount(self, ctx: Any) -> None:
        """Unmount the security layer."""
        await self.cleanup()

    async def process(self, ctx: Any, data: Any) -> Any:
        """Process data through security layer (passthrough)."""
        return data

    async def rollback(self, ctx: Any, error: Exception) -> None:
        """Rollback on error."""
        logger.warning(f"SecurityLayer rollback: {error}")

    async def initialize(self) -> bool:
        """Initialize SecurityLayer components."""
        try:
            logger.info("🚀 Initializing SecurityLayer...")
            if self.config.obfuscation_level != "none":
                await self._init_string_obfuscator()
            if self.config.enable_query_masking or self.config.enable_chaff_traffic:
                await self._init_research_obfuscator()
            await self._init_secure_destructor()
            await self._init_mission_audit()
            logger.info("✅ SecurityLayer initialized successfully")
            return True
        except Exception as e:
            logger.error(f"❌ SecurityLayer initialization failed: {e}")
            return False

    async def _init_string_obfuscator(self) -> None:
        """Lazy initialization of StringObfuscator."""
        if self._string_obfuscator is None:
            try:
                from hledac.universal.crypto.string_obfuscator import StringObfuscator

                self._string_obfuscator = StringObfuscator()
                logger.info("✅ StringObfuscator initialized")
            except ImportError as e:
                logger.warning(f"⚠️ StringObfuscator not available: {e}")
                self._string_obfuscator = None

    async def _init_research_obfuscator(self) -> None:
        """Lazy initialization of ResearchObfuscator."""
        if self._research_obfuscator is None:
            try:
                from hledac.universal.research_security.research_obfuscation import (
                    ResearchObfuscator,
                )

                self._research_obfuscator = ResearchObfuscator()
                logger.info("✅ ResearchObfuscator initialized")
            except ImportError as e:
                logger.warning(f"⚠️ ResearchObfuscator not available: {e}")
                self._research_obfuscator = None

    async def _init_secure_destructor(self) -> None:
        """Lazy initialization of SecureDestructor."""
        if self._secure_destructor is None:
            try:
                from hledac.universal.research_security.secure_destruction import (
                    DestructionConfig,
                    SecureDestructor,
                )

                destructor_config = DestructionConfig(
                    standard=self.config.wipe_standard,
                    verify=self.config.verification_enabled,
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
                    AnonymizationLevel,
                    PrivacyAuditLog,
                )

                self._privacy_audit = await PrivacyAuditLog.create(
                    retention_days=90,
                    anonymization_level=AnonymizationLevel.FULL,
                )
                logger.info("✅ PrivacyAuditLog initialized (compliance mode)")
            except Exception as e:
                logger.warning(f"⚠️ PrivacyAuditLog not available: {e}")
                self._privacy_audit = None

    def set_audit_mode(self, mode: str) -> None:
        """Set audit mode: 'forensic', 'compliance', or 'both'."""
        if mode not in ("forensic", "compliance", "both"):
            raise ValueError(f"Invalid audit mode: {mode}")
        self._audit_mode = mode
        logger.info(f"🔒 Audit mode set to: {mode}")

    async def log_privacy_event(
        self,
        action: str,
        subject_id: str,
        resource: str,
        details: dict | None = None,
        category: str = "DATA_ACCESS",
    ) -> str | None:
        """Log privacy event for GDPR/CCPA compliance."""
        if self._audit_mode not in ("compliance", "both"):
            logger.debug(f"Privacy audit disabled (mode: {self._audit_mode})")
            return None
        if self._privacy_audit is None:
            await self._init_privacy_audit()
        if self._privacy_audit is None:
            if self._mission_audit:
                return self._mission_audit.log_action(
                    f"privacy:{action}",
                    f"{subject_id}:{resource}".encode(),
                    details or {},
                )
            return None
        try:
            from ...privacy_protection.privacy_audit_log import (
                PrivacyEventCategory,
                Severity,
            )

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
                severity=Severity.INFO,
            )
            return entry.entry_id if entry else None
        except Exception as e:
            logger.warning(f"⚠️ Privacy audit log failed: {e}")
            return None

    def anonymize_text(self, text: str, level: str = "full") -> str:
        """Anonymize PII in text."""
        if self._privacy_audit and hasattr(self._privacy_audit, "anonymizer"):
            try:
                from ...privacy_protection.privacy_audit_log import AnonymizationLevel

                level_map = {
                    "none": AnonymizationLevel.NONE,
                    "partial": AnonymizationLevel.PARTIAL,
                    "full": AnonymizationLevel.FULL,
                }
                return self._privacy_audit.anonymizer.anonymize(text, level_map.get(level, AnonymizationLevel.FULL))
            except Exception as e:
                logger.warning(f"⚠️ Anonymization failed: {e}")
        import re

        text = re.sub(r"\S+@\S+\.\S+", "[EMAIL_REDACTED]", text)
        text = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "[SSN_REDACTED]", text)
        return text

    BLOCKLISTED_DOMAINS = frozenset(
        [
            "honeypot.example.com",
            "sinkhole.example.net",
            "known-false-positive.osint.local",
        ]
    )
    ENTROPY_THRESHOLD = 1.5
    _PII_EMAIL_RE = __import__("re").compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
    _PII_PHONE_RE = __import__("re").compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b")
    _PII_SSN_RE = __import__("re").compile(r"\b\d{3}-\d{2}-\d{4}\b")
    _PII_PATTERNS = (_PII_EMAIL_RE, _PII_PHONE_RE, _PII_SSN_RE)

    def validate_finding(self, finding: dict) -> tuple[bool, str]:
        """Lightweight security gate for findings — runs synchronously, <1ms."""
        try:
            payload = finding.get("payload_text", "") or ""
            provenance = finding.get("provenance", "") or ""

            if provenance:
                import urllib.parse

                try:
                    parsed = urllib.parse.urlparse(provenance)
                    host = parsed.netloc.lower()
                    if any(bad in host for bad in self.BLOCKLISTED_DOMAINS):
                        return (False, "blocklisted_domain")
                except Exception:
                    pass

            has_pii = any(p.search(payload) for p in self._PII_PATTERNS)
            if has_pii:
                redacted = self.anonymize_text(payload)
                finding["payload_text"] = redacted
                return (True, "pii_redacted")

            if payload:
                entropy = self._shannon_entropy(payload)
                if entropy < self.ENTROPY_THRESHOLD:
                    return (False, "low_entropy_payload")

            return (True, "ok")
        except Exception:
            return (True, "ok")

    @staticmethod
    def _shannon_entropy(text: str) -> float:
        """Compute Shannon entropy of text."""
        if not text:
            return 0.0
        import math

        freq: dict[str, float] = {}
        for ch in text:
            freq[ch] = freq.get(ch, 0) + 1
        entropy = 0.0
        length = len(text)
        for count in freq.values():
            prob = count / length
            if prob > 0:
                entropy -= prob * math.log2(prob)
        return entropy

    def log_action(
        self,
        action_type: str,
        data: bytes,
        metadata: dict | None = None,
    ) -> str | None:
        """Log an action to the cryptographic audit chain."""
        if self._mission_audit:
            try:
                return self._mission_audit.log_action(action_type, data, metadata or {})
            except Exception as e:
                logger.warning(f"⚠️ MissionAudit log failed: {e}")
        return None

    def get_audit_chain(self) -> list[dict]:
        """Get the full audit chain."""
        if self._mission_audit:
            return [entry.to_dict() for entry in self._mission_audit.audit_chain]
        return []

    def get_merkle_root(self) -> str | None:
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
        level: ObfuscationLevel | None = None,
    ) -> ObfuscationResult:
        """Obfuscate a string with multi-stage encoding."""
        level = level or ObfuscationLevel(self.config.obfuscation_level)
        self._obfuscation_count += 1
        logger.debug(f"🔐 Obfuscating string (level: {level.value})")

        try:
            if self._string_obfuscator:
                original_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
                stages = {
                    ObfuscationLevel.LIGHT: ["xor", "base64"],
                    ObfuscationLevel.MEDIUM: ["xor", "base64", "zlib"],
                    ObfuscationLevel.HEAVY: ["xor", "base64", "zlib", "xor"],
                    ObfuscationLevel.MAXIMUM: ["xor", "base64", "zlib", "xor"],
                }.get(level, [])

                obfuscated = self._string_obfuscator.multi_stage_encode(content)

                if level in (ObfuscationLevel.HEAVY, ObfuscationLevel.MAXIMUM):
                    if self.config.generate_decoys:
                        self._string_obfuscator.generate_decoy_strings(count=self.config.decoy_count)

                self.log_action("obfuscation", content.encode(), {"level": level.value, "stages": stages})
                return ObfuscationResult(
                    original_hash=original_hash,
                    obfuscated_data=obfuscated,
                    encoding_chain=stages,
                    decoy_count=self.config.decoy_count
                    if level in (ObfuscationLevel.HEAVY, ObfuscationLevel.MAXIMUM)
                    else 0,
                    success=True,
                )
            else:
                import base64

                return ObfuscationResult(
                    original_hash=hashlib.sha256(content.encode()).hexdigest()[:16],
                    obfuscated_data=base64.b64encode(content.encode()).decode(),
                    encoding_chain=["base64"],
                    decoy_count=0,
                    success=True,
                )
        except Exception as e:
            logger.error(f"❌ String obfuscation failed: {e}")
            return ObfuscationResult(
                original_hash="",
                obfuscated_data=content,
                encoding_chain=[],
                decoy_count=0,
                success=False,
            )

    def mask_query(self, query: str) -> str:
        """Mask a research query to hide intent."""
        if not self.config.enable_query_masking:
            return query
        if self._research_obfuscator:
            try:
                return self._research_obfuscator.mask_query(query)
            except Exception as e:
                logger.warning(f"⚠️ Query masking failed: {e}")
                return query
        else:
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

    def generate_chaff(self, count: int | None = None) -> list[str]:
        """Generate chaff queries to mask real research."""
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
        chaff = _RNG.sample(fallback_chaff, min(count, len(fallback_chaff)))
        self._chaff_generated += len(chaff)
        return chaff

    async def destroy_file(
        self,
        file_path: str,
        standard: WipeStandard | None = None,
    ) -> DestructionResult:
        """Securely destroy a file."""
        import os

        standard = standard or WipeStandard(self.config.wipe_standard)
        self._destruction_count += 1
        logger.info(f"🗑️ Securely destroying file: {file_path} (standard: {standard.value})")

        try:
            if self._secure_destructor:
                result = await self._secure_destructor.destroy_file(file_path)
                self.log_action(
                    "file_destruction",
                    file_path.encode(),
                    {"standard": standard.value, "passes": getattr(result, "passes", 1)},
                )
                return DestructionResult(
                    file_path=file_path,
                    standard=standard,
                    passes_completed=getattr(result, "passes", 1),
                    bytes_overwritten=getattr(result, "bytes", 0),
                    verification_passed=getattr(result, "verified", True),
                    timestamp=time.time(),
                )
            else:
                # Fallback: simple overwrite
                p = Path(file_path)
                if p.exists():
                    size = p.stat().st_size
                    with open(p, "wb") as f:
                        f.write(os.urandom(size))
                    p.unlink()
                    self.log_action(
                        "file_destruction",
                        file_path.encode(),
                        {"standard": standard.value, "fallback": True},
                    )
                    return DestructionResult(
                        file_path=file_path,
                        standard=standard,
                        passes_completed=1,
                        bytes_overwritten=size,
                        verification_passed=not p.exists(),
                        timestamp=time.time(),
                    )
                return DestructionResult(
                    file_path=file_path,
                    standard=standard,
                    passes_completed=0,
                    bytes_overwritten=0,
                    verification_passed=False,
                    timestamp=time.time(),
                )
        except Exception as e:
            logger.error(f"❌ File destruction failed: {e}")
            return DestructionResult(
                file_path=file_path,
                standard=standard,
                passes_completed=0,
                bytes_overwritten=0,
                verification_passed=False,
                timestamp=time.time(),
            )

    async def destroy_directory(
        self,
        dir_path: str,
        recursive: bool = True,
    ) -> list[DestructionResult]:
        """Securely destroy a directory."""
        import os

        logger.info(f"🗑️ Securely destroying directory: {dir_path}")
        results = []
        try:
            if recursive:

                def walk_sync():
                    file_paths = []
                    for root, _dirs, files in os.walk(dir_path, topdown=False):
                        root_p = Path(root)
                        for file in files:
                            file_paths.append(str(root_p / file))
                    return file_paths

                all_files = await asyncio.to_thread(walk_sync)
                for file_path in all_files:
                    result = await self.destroy_file(file_path)
                    results.append(result)
            else:

                def glob_sync():
                    import glob

                    return [f for f in glob.glob(str(Path(dir_path) / "*")) if Path(f).is_file()]

                files = await asyncio.to_thread(glob_sync)
                for file_path in files:
                    result = await self.destroy_file(file_path)
                    results.append(result)
        except Exception as e:
            logger.error(f"❌ Directory destruction failed: {e}")
        return results

    def get_statistics(self) -> dict[str, Any]:
        """Get security layer statistics."""
        return {
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
            },
        }

    async def cleanup(self) -> None:
        """Cleanup resources."""
        from hledac.universal.utils._patterns import safe_cleanup_component

        logger.info("🧹 Cleaning up SecurityLayer...")
        await safe_cleanup_component(self._secure_destructor, "SecureDestructor", logger, _type="async")
        await safe_cleanup_component(self._mission_audit, "MissionAudit", logger, _type="sync")
        logger.info("✅ SecurityLayer cleanup complete")


__all__ = ["SecurityLayer", "MissionAudit", "AuditEntry"]
