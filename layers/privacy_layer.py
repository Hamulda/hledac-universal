#!/usr/bin/env python3
"""
Privacy Layer - Universal Orchestrator Integration

Integrates all privacy protection modules:
- PersonalPrivacyManager (VPN/Tor/DNS/fingerprinting)
- AnonymousCommunication (PGP/secure email/channels)
- PrivacyAuditLog (PII anonymization/GDPR compliance)
- ProtocolCodeGenerator (secure protocol generation)

Provides unified API for privacy operations with automatic
fallbacks and M1 memory optimization.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from hledac.universal.project_types import (
    PrivacyLevel, AnonymizationLevel, PrivacyEventCategory,
    Severity, ProtocolType, SecurityLevel
)
from ..config import PrivacyConfig

logger = logging.getLogger(__name__)

# Lazy imports for privacy modules
try:
    from ...privacy_protection.personal_privacy_manager import (
        PersonalPrivacyManager, PrivacyLevel as PPMLevel,
        VPNConfig, TorConfig, DNSConfig, BrowserFingerprint
    )
    HAS_PPM = True
except ImportError:
    HAS_PPM = False

try:
    from ...privacy_protection.anonymous_communication import (
        AnonymousCommunication, PGPKey, SecureMessage,
        SecureChannel, BurnerIdentity, EmailConfig
    )
    HAS_AC = False
except ImportError:
    HAS_AC = False

try:
    from ...privacy_protection.privacy_audit_log import (
        PrivacyAuditLog, PrivacyLogEntry, PIIAnonymizer,
        AnonymizationLevel as PALLevel
    )
    HAS_PAL = False
except ImportError:
    HAS_PAL = False

try:
    from ...privacy_protection.protocol_code_generator import (
        ProtocolCodeGenerator, GeneratedProtocol, ProtocolSpec
    )
    HAS_PCG = False
except ImportError:
    HAS_PCG = False


@dataclass
class PrivacyContext:
    """Privacy context for operations."""
    level: PrivacyLevel
    identity_id: Optional[str] = None
    channel_id: Optional[str] = None
    audit_session: Optional[str] = None


class PrivacyLayer:
    """
    Privacy Layer integrating all privacy protection features.

    Features:
    - VPN/Tor/DNS privacy management
    - Anonymous communication (PGP/email/channels)
    - Privacy audit logging (delegated to SecurityLayer)
    - Protocol code generation
    - Automatic PII anonymization

    Note: Privacy audit logging is now handled by SecurityLayer for unified audit.
    """

    def __init__(self, config: PrivacyConfig, security_layer: Optional[Any] = None):
        """
        Initialize PrivacyLayer.

        Args:
            config: Privacy configuration
            security_layer: Optional SecurityLayer for unified audit logging
        """
        self.config = config
        self._privacy_manager: Optional[Any] = None
        self._comm: Optional[Any] = None
        self._protocol_gen: Optional[Any] = None

        # Unified audit via SecurityLayer (prevents duplicate audit systems)
        self._security_layer: Optional[Any] = security_layer
        self._audit: Optional[Any] = None  # Legacy fallback

        self._initialized = False
        self._contexts: Dict[str, PrivacyContext] = {}
        
    async def initialize(self) -> bool:
        """Initialize all privacy components."""
        try:
            # Initialize Personal Privacy Manager
            if HAS_PPM and self.config.enable_privacy_manager:
                from ...privacy_protection.personal_privacy_manager import (
                    create_privacy_manager, PrivacyLevel as PPMLevel
                )
                
                level_map = {
                    PrivacyLevel.BASIC: PPMLevel.BASIC,
                    PrivacyLevel.STANDARD: PPMLevel.STANDARD,
                    PrivacyLevel.ENHANCED: PPMLevel.ENHANCED,
                    PrivacyLevel.MAXIMUM: PPMLevel.MAXIMUM
                }
                
                self._privacy_manager = await create_privacy_manager(
                    level=level_map.get(self.config.level, PPMLevel.STANDARD)
                )
                logger.info("PersonalPrivacyManager initialized")
            
            # Initialize Anonymous Communication
            if HAS_AC and self.config.enable_anonymous_comm:
                from ...privacy_protection.anonymous_communication import (
                    create_anonymous_communication
                )
                self._comm = await create_anonymous_communication(
                    use_tor=self.config.use_tor
                )
                logger.info("AnonymousCommunication initialized")
            
            # Privacy Audit Log is now handled by SecurityLayer (unified audit)
            # If SecurityLayer is not provided, use legacy fallback
            if self._security_layer is None and HAS_PAL and self.config.enable_audit_log:
                from ...privacy_protection.privacy_audit_log import (
                    create_privacy_audit_log
                )
                self._audit = await create_privacy_audit_log(
                    retention_days=self.config.audit_retention_days,
                    anonymization_level=PALLevel.FULL
                )
                logger.info("PrivacyAuditLog initialized (legacy mode)")
            
            # Initialize Protocol Generator
            if HAS_PCG and self.config.enable_protocol_gen:
                from ...privacy_protection.protocol_code_generator import (
                    create_protocol_generator
                )
                self._protocol_gen = create_protocol_generator()
                logger.info("ProtocolCodeGenerator initialized")
            
            self._initialized = True
            return True
            
        except Exception as e:
            logger.error(f"Privacy layer initialization failed: {e}")
            return False
    
    async def shutdown(self) -> None:
        """Shutdown all privacy components."""
        if self._privacy_manager:
            await self._privacy_manager.deactivate()
        
        if self._comm:
            await self._comm.shutdown()
        
        if self._audit:
            await self._audit._flush_to_disk()
        
        self._initialized = False
        logger.info("Privacy layer shutdown complete")
    
    # ============== Privacy Manager Methods ==============
    
    async def activate_privacy(self, level: Optional[PrivacyLevel] = None) -> bool:
        """Activate privacy protection."""
        if self._privacy_manager:
            return await self._privacy_manager.activate()
        return False
    
    async def deactivate_privacy(self) -> bool:
        """Deactivate privacy protection."""
        if self._privacy_manager:
            return await self._privacy_manager.deactivate()
        return False
    
    async def rotate_identity(self) -> bool:
        """Rotate to new identity."""
        if self._privacy_manager:
            return await self._privacy_manager.rotate_identity()
        return False
    
    def get_browser_fingerprint(self) -> Optional[Dict[str, Any]]:
        """Get randomized browser fingerprint."""
        if self._privacy_manager:
            fp = self._privacy_manager.get_fingerprint()
            return {
                "user_agent": fp.user_agent,
                "screen_resolution": fp.screen_resolution,
                "timezone": fp.timezone,
                "language": fp.language,
                "platform": fp.platform
            }
        return None
    
    def get_privacy_status(self) -> Dict[str, Any]:
        """Get current privacy status."""
        if self._privacy_manager:
            status = self._privacy_manager.get_status()
            return status.to_dict()
        return {"status": "not_initialized"}
    
    # ============== Anonymous Communication Methods ==============
    
    def generate_pgp_key(self, name: str, email: str) -> Optional[Dict[str, str]]:
        """Generate PGP key pair."""
        if self._comm:
            key = self._comm.generate_pgp_key(name, email)
            if key:
                return {
                    "key_id": key.key_id,
                    "fingerprint": key.fingerprint,
                    "public_key": key.public_key
                }
        return None
    
    def encrypt_message(self, message: str, recipient_key_id: str) -> Optional[str]:
        """Encrypt message with PGP."""
        if self._comm:
            return self._comm.encrypt_message(message, recipient_key_id)
        return None
    
    def decrypt_message(self, encrypted_message: str) -> Optional[str]:
        """Decrypt PGP message."""
        if self._comm:
            return self._comm.decrypt_message(encrypted_message)
        return None
    
    def create_secure_channel(self, participant_ids: List[str]) -> Optional[str]:
        """Create secure messaging channel."""
        if self._comm:
            channel = self._comm.create_channel(participant_ids)
            return channel.channel_id if channel else None
        return None
    
    def send_channel_message(
        self,
        channel_id: str,
        sender_id: str,
        content: str,
        ttl: Optional[int] = None
    ) -> Optional[str]:
        """Send message to secure channel."""
        if self._comm:
            msg = self._comm.send_channel_message(channel_id, sender_id, content, ttl)
            return msg.message_id if msg else None
        return None
    
    def create_burner_identity(
        self,
        display_name: Optional[str] = None,
        lifespan_hours: int = 24
    ) -> Optional[Dict[str, Any]]:
        """Create temporary anonymous identity."""
        if self._comm:
            identity = self._comm.create_burner_identity(display_name, lifespan_hours)
            if identity:
                return {
                    "identity_id": identity.identity_id,
                    "display_name": identity.display_name,
                    "pgp_key_id": identity.pgp_key.key_id,
                    "expires_at": identity.expires_at.isoformat()
                }
        return None
    
    # ============== Audit Log Methods ==============
    
    async def log_event(
        self,
        category: PrivacyEventCategory,
        action: str,
        subject_id: str,
        resource: str,
        details: Optional[Dict[str, Any]] = None,
        severity: Severity = Severity.INFO
    ) -> bool:
        """Log privacy event (delegated to SecurityLayer if available)."""
        # Prefer unified SecurityLayer audit
        if self._security_layer and hasattr(self._security_layer, 'log_privacy_event'):
            cat_map = {
                PrivacyEventCategory.DATA_ACCESS: "DATA_ACCESS",
                PrivacyEventCategory.DATA_MODIFICATION: "DATA_MODIFICATION",
                PrivacyEventCategory.DATA_DELETION: "DATA_DELETION",
                PrivacyEventCategory.CONSENT_GRANTED: "CONSENT_GRANTED",
                PrivacyEventCategory.CONSENT_REVOKED: "CONSENT_REVOKED",
            }
            result = await self._security_layer.log_privacy_event(
                action=action,
                subject_id=subject_id,
                resource=resource,
                details=details,
                category=cat_map.get(category, "DATA_ACCESS")
            )
            return result is not None

        # Legacy fallback
        if self._audit:
            from ...privacy_protection.privacy_audit_log import PrivacyEventCategory as PALCat, Severity as PALSEV

            cat_map = {
                PrivacyEventCategory.DATA_ACCESS: PALCat.DATA_ACCESS,
                PrivacyEventCategory.DATA_MODIFICATION: PALCat.DATA_MODIFICATION,
                PrivacyEventCategory.DATA_DELETION: PALCat.DATA_DELETION,
                PrivacyEventCategory.CONSENT_GRANTED: PALCat.CONSENT_GRANTED,
                PrivacyEventCategory.CONSENT_REVOKED: PALCat.CONSENT_REVOKED,
            }

            sev_map = {
                Severity.DEBUG: PALSEV.DEBUG,
                Severity.INFO: PALSEV.INFO,
                Severity.WARNING: PALSEV.WARNING,
                Severity.ERROR: PALSEV.ERROR,
                Severity.CRITICAL: PALSEV.CRITICAL,
            }

            entry = await self._audit.log_event(
                category=cat_map.get(category, PALCat.DATA_ACCESS),
                action=action,
                subject_id=subject_id,
                resource=resource,
                details=details or {},
                severity=sev_map.get(severity, PALSEV.INFO)
            )
            return entry is not None
        return False
    
    async def search_audit_logs(
        self,
        category: Optional[PrivacyEventCategory] = None,
        severity: Optional[Severity] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Search audit logs."""
        if self._audit:
            entries = await self._audit.search_logs(limit=limit)
            return [
                {
                    "entry_id": e.entry_id,
                    "timestamp": e.timestamp.isoformat(),
                    "category": e.category.value,
                    "severity": e.severity.value,
                    "action": e.action,
                    "subject_id": e.subject_id,
                    "resource": e.resource
                }
                for e in entries
            ]
        return []
    
    async def generate_compliance_report(
        self,
        days: int = 30
    ) -> Optional[Dict[str, Any]]:
        """Generate GDPR/CCPA compliance report."""
        if self._audit:
            from datetime import datetime, timedelta
            return await self._audit.generate_compliance_report(
                start_date=datetime.now() - timedelta(days=days),
                end_date=datetime.now()
            )
        return None
    
    # ============== Protocol Generator Methods ==============
    
    def generate_protocol(
        self,
        name: str,
        protocol_type: ProtocolType,
        **kwargs
    ) -> Optional[Dict[str, Any]]:
        """Generate secure protocol code."""
        if self._protocol_gen:
            from ...privacy_protection.protocol_code_generator import ProtocolType as PCGType
            
            type_map = {
                ProtocolType.MESSAGING: PCGType.MESSAGING,
                ProtocolType.HANDSHAKE: PCGType.HANDSHAKE,
                ProtocolType.ZK_PROOF: PCGType.ZK_PROOF,
            }
            
            protocol = self._protocol_gen.generate_protocol(
                name,
                type_map.get(protocol_type, PCGType.MESSAGING),
                **kwargs
            )
            
            return {
                "protocol_id": protocol.protocol_id,
                "name": protocol.name,
                "type": protocol.protocol_type.name,
                "source_code_length": len(protocol.source_code),
                "has_protobuf": protocol.protobuf_def is not None,
                "security_hints": protocol.security_audit_hints
            }
        return None
    
    async def save_protocol(
        self,
        protocol_id: str,
        output_dir: Optional[Path] = None
    ) -> Optional[Path]:
        """Save generated protocol to disk."""
        if self._protocol_gen and protocol_id in self._protocol_gen.generated:
            protocol = self._protocol_gen.generated[protocol_id]
            return await self._protocol_gen.save_protocol(protocol, output_dir)
        return None
    
    # ============== PII Anonymization Methods ==============
    
    def anonymize_text(
        self,
        text: str,
        level: AnonymizationLevel = AnonymizationLevel.FULL
    ) -> str:
        """Anonymize PII in text."""
        if self._audit:
            from ...privacy_protection.privacy_audit_log import AnonymizationLevel as AL
            level_map = {
                AnonymizationLevel.NONE: AL.NONE,
                AnonymizationLevel.PARTIAL: AL.PARTIAL,
                AnonymizationLevel.FULL: AL.FULL,
            }
            return self._audit.anonymizer.anonymize(
                text,
                level_map.get(level, AL.FULL)
            )
        
        # Fallback: basic redaction
        import re
        text = re.sub(r'\\S+@\\S+\\.\\S+', '[EMAIL_REDACTED]', text)
        text = re.sub(r'\\b\\d{3}-\\d{2}-\\d{4}\\b', '[SSN_REDACTED]', text)
        return text
    
    def detect_pii(self, text: str) -> Dict[str, List[str]]:
        """Detect PII in text."""
        if self._audit:
            return self._audit.anonymizer.detect_pii(text)
        return {}
    
    def has_pii(self, text: str) -> bool:
        """Check if text contains PII."""
        if self._audit:
            return self._audit.anonymizer.has_pii(text)
        return False
    
    # ============== Unified API ==============
    
    async def create_privacy_context(
        self,
        level: PrivacyLevel = PrivacyLevel.STANDARD
    ) -> str:
        """Create new privacy context."""
        import secrets
        context_id = secrets.token_hex(8)
        
        self._contexts[context_id] = PrivacyContext(
            level=level,
            audit_session=secrets.token_hex(8)
        )
        
        # Log context creation
        await self.log_event(
            category=PrivacyEventCategory.DATA_ACCESS,
            action="privacy_context_created",
            subject_id=context_id,
            resource="privacy_layer",
            details={"level": level.value}
        )
        
        return context_id
    
    async def close_privacy_context(self, context_id: str) -> bool:
        """Close privacy context."""
        if context_id in self._contexts:
            # Log context closure
            await self.log_event(
                category=PrivacyEventCategory.DATA_ACCESS,
                action="privacy_context_closed",
                subject_id=context_id,
                resource="privacy_layer"
            )
            
            del self._contexts[context_id]
            return True
        return False
    
    def get_status(self) -> Dict[str, Any]:
        """Get privacy layer status."""
        return {
            "initialized": self._initialized,
            "privacy_manager": HAS_PPM and self._privacy_manager is not None,
            "anonymous_communication": HAS_AC and self._comm is not None,
            "audit_log": HAS_PAL and self._audit is not None,
            "protocol_generator": HAS_PCG and self._protocol_gen is not None,
            "active_contexts": len(self._contexts),
            "config": {
                "level": self.config.level.value if self.config.level else None,
                "use_tor": self.config.use_tor,
                "audit_retention_days": self.config.audit_retention_days
            }
        }
    
    async def health_check(self) -> Tuple[bool, List[str]]:
        """Check privacy layer health."""
        issues = []
        
        if not self._initialized:
            issues.append("Privacy layer not initialized")
        
        if self.config.enable_privacy_manager and not self._privacy_manager:
            issues.append("Privacy manager not available")
        
        if self.config.enable_anonymous_comm and not self._comm:
            issues.append("Anonymous communication not available")
        
        if self.config.enable_audit_log and not self._audit:
            issues.append("Audit log not available")
        
        return len(issues) == 0, issues


# Factory function
async def create_privacy_layer(config: PrivacyConfig) -> PrivacyLayer:
    """Create and initialize privacy layer."""
    layer = PrivacyLayer(config)
    await layer.initialize()
    return layer
