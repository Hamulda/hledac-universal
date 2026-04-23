"""
DeepResearchSecurity - Komplexní bezpečnostní vrstva pro Ultra Deep Research

Integruje všechny bezpečnostní komponenty pro výzkum v tajných archivech:
- Quantum-safe crypto (ML-KEM, ML-DSA)
- Steganography (DCT, LSB, Neural)
- Obfuscation (masking, chaff)
- Secure destruction (DoD 5220.22-M)
- Audit forensics
- Zero-knowledge patterns

Pro bezpečný výzkum v:
- Tajných databázích
- Klasifikovaných archivech
- Deep web resources
- Restricted networks
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional

from .quantum_safe import QuantumSafeVault, StealthCommunicator, SecurityLevel
from .obfuscation import ResearchObfuscator, ObfuscationConfig
from .destruction import SecureDestructor, DestructionConfig
from .audit import AuditLogger, AuditConfig, AuditEventType, AuditLevel

logger = logging.getLogger(__name__)


@dataclass
class DeepSecurityConfig:
    """Konfigurace pro deep research security"""
    
    # Úroveň zabezpečení
    security_level: SecurityLevel = SecurityLevel.HIGH
    privacy_level: str = "maximum"  # 'low', 'medium', 'high', 'maximum'
    
    # Komponenty
    enable_quantum_safe: bool = True
    enable_steganography: bool = True
    enable_obfuscation: bool = True
    enable_destruction: bool = True
    enable_audit: bool = True
    
    # Chaff generation
    chaff_enabled: bool = True
    chaff_ratio: float = 0.4  # 40% falešného provozu
    
    # Auto-cleanup
    auto_cleanup: bool = True
    cleanup_interval: int = 300  # 5 minut
    
    # Configy pro komponenty
    audit_config: AuditConfig = field(default_factory=AuditConfig)
    obfuscation_config: ObfuscationConfig = field(default_factory=ObfuscationConfig)
    destruction_config: DestructionConfig = field(default_factory=DestructionConfig)


class DeepResearchSecurity:
    """
    Komplexní bezpečnostní systém pro ultra deep research.
    
    Poskytuje kompletní ochranu pro výzkum v tajných archivech:
    - Quantum-safe šifrování citlivých dat
    - Steganografie pro skryté ukládání
    - Obfuskace pro maskování aktivit
    - Bezpečné mazání stop
    - Audit trail
    
    Example:
        >>> security = DeepResearchSecurity()
        >>> 
        >>> async with security.protected_session() as session:
        ...     # Všechny operace jsou chráněné
        ...     encrypted = await session.encrypt_sensitive(data)
        ...     stego = await session.hide_in_image(encrypted, cover_image)
        ...     
        ...     # Obfuskovaný dotaz
        ...     masked = session.mask_query("classified documents")
        ...     result = await search(masked)
        ...     
        ...     # Bezpečné uložení
        ...     await session.secure_store(result)
        ...     
        ...     # Auto-cleanup po skončení session
    """
    
    def __init__(self, config: DeepSecurityConfig = None):
        self.config = config or DeepSecurityConfig()
        
        # Inicializace komponent
        self.vault: Optional[QuantumSafeVault] = None
        self.communicator: Optional[StealthCommunicator] = None
        self.obfuscator: Optional[ResearchObfuscator] = None
        self.destructor: Optional[SecureDestructor] = None
        self.audit: Optional[AuditLogger] = None
        
        # Apply privacy level
        self._apply_privacy_level()
        
        # Session tracking
        self._active_sessions = []
        self._stats = {
            "sessions_created": 0,
            "data_encrypted": 0,
            "data_obfuscated": 0,
            "files_destroyed": 0,
        }
    
    def _apply_privacy_level(self):
        """Aplikovat úroveň soukromí"""
        level = self.config.privacy_level
        
        if level == 'maximum':
            # Maximum privacy - vše zapnout
            self.config.enable_quantum_safe = True
            self.config.enable_steganography = True
            self.config.enable_obfuscation = True
            self.config.chaff_enabled = True
            self.config.chaff_ratio = 0.5
            
            # Destruction config
            self.config.destruction_config.passes = 7
            self.config.destruction_config.compliance_standard = 'dod'
            
            # Audit všech operací
            self.config.audit_config.min_level = AuditLevel.DEBUG
            
        elif level == 'high':
            # High privacy - default
            pass
            
        elif level == 'medium':
            # Medium - redukovat chaff
            self.config.chaff_ratio = 0.2
            self.config.destruction_config.passes = 3
            
        elif level == 'low':
            # Low - audit only
            self.config.enable_obfuscation = False
            self.config.chaff_enabled = False
    
    async def initialize(self) -> None:
        """Inicializovat všechny komponenty"""
        logger.info(f"Initializing DeepResearchSecurity ({self.config.privacy_level})")
        
        # Quantum-safe vault
        if self.config.enable_quantum_safe:
            self.vault = QuantumSafeVault(self.config.security_level)
            await self.vault.initialize()
            logger.info("✓ QuantumSafeVault initialized")
        
        # Stealth communicator
        if self.config.enable_steganography:
            self.communicator = StealthCommunicator()
            logger.info("✓ StealthCommunicator initialized")
        
        # Obfuscator
        if self.config.enable_obfuscation:
            self.obfuscator = ResearchObfuscator(self.config.obfuscation_config)
            logger.info("✓ ResearchObfuscator initialized")
        
        # Destructor
        if self.config.enable_destruction:
            self.destructor = SecureDestructor(self.config.destruction_config)
            logger.info("✓ SecureDestructor initialized")
        
        # Audit
        if self.config.enable_audit:
            self.audit = AuditLogger(self.config.audit_config)
            await self.audit.initialize()
            logger.info("✓ AuditLogger initialized")
        
        logger.info("✓ DeepResearchSecurity fully initialized")
    
    @asynccontextmanager
    async def protected_session(
        self,
        session_name: str = "research_session"
    ) -> AsyncGenerator["SecureSession", None]:
        """
        Vytvořit chráněnou relaci pro výzkum.
        
        Args:
            session_name: Název relace
            
        Yields:
            SecureSession objekt
        """
        session = SecureSession(self, session_name)
        self._active_sessions.append(session)
        self._stats["sessions_created"] += 1
        
        # Log session start
        if self.audit:
            await self.audit.log(
                event_type=AuditEventType.SYSTEM_EVENT,
                action="session_start",
                resource=session_name,
                level=AuditLevel.INFO,
            )
        
        try:
            yield session
        finally:
            # Cleanup
            if self.config.auto_cleanup:
                await session.cleanup()
            
            self._active_sessions.remove(session)
            
            # Log session end
            if self.audit:
                await self.audit.log(
                    event_type=AuditEventType.SYSTEM_EVENT,
                    action="session_end",
                    resource=session_name,
                    level=AuditLevel.INFO,
                )
    
    async def emergency_purge(self) -> Dict[str, Any]:
        """
        Nouzové vyčištění všech stop.
        
        Použít v případě detekce ohrožení.
        
        Returns:
            Statistiky vyčištění
        """
        logger.critical("EMERGENCY PURGE initiated!")
        
        results = {
            "sessions_terminated": len(self._active_sessions),
            "files_destroyed": 0,
            "memory_wiped": False,
        }
        
        # Ukončit všechny sessions
        for session in self._active_sessions[:]:
            await session.emergency_cleanup()
        
        # Smazat audit log pokud je to bezpečné
        # (v reálném nasazení by toto mělo být konfigurovatelné)
        
        logger.critical("EMERGENCY PURGE complete")
        
        return results
    
    def get_stats(self) -> Dict[str, Any]:
        """Získat statistiky"""
        return {
            "config": {
                "security_level": self.config.security_level.value,
                "privacy_level": self.config.privacy_level,
            },
            "sessions": {
                "active": len(self._active_sessions),
                "total": self._stats["sessions_created"],
            },
            "components": {
                "quantum_safe": self.vault is not None,
                "steganography": self.communicator is not None,
                "obfuscation": self.obfuscator is not None,
                "destruction": self.destructor is not None,
                "audit": self.audit is not None,
            },
        }


class SecureSession:
    """
    Bezpečná relace pro výzkum.
    
    Poskytuje metody pro bezpečné operace:
    - Šifrování dat
    - Steganografie
    - Obfuskaci
    - Bezpečné mazání
    """
    
    def __init__(self, security: DeepResearchSecurity, name: str):
        self.security = security
        self.name = name
        self.session_id = f"{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self._temp_files = []
        self._encrypted_data = []
    
    async def encrypt_sensitive(
        self,
        data: bytes,
        metadata: Dict[str, Any] = None
    ) -> bytes:
        """
        Zašifrovat citlivá data.
        
        Args:
            data: Data k zašifrování
            metadata: Volitelná metadata
            
        Returns:
            Serializovaný EncryptedContainer
        """
        if not self.security.vault:
            raise RuntimeError("QuantumSafeVault not available")
        
        import json
        
        container = await self.security.vault.encrypt(
            data,
            associated_data=json.dumps(metadata).encode() if metadata else None
        )
        
        self._encrypted_data.append(container)
        self.security._stats["data_encrypted"] += 1
        
        # Log
        if self.security.audit:
            await self.security.audit.log(
                event_type=AuditEventType.DATA_STORE,
                action="encrypt",
                resource=f"session:{self.name}",
                details={"size": len(data)},
                level=AuditLevel.INFO,
                session_id=self.session_id,
            )
        
        return json.dumps(container.to_dict()).encode()
    
    async def decrypt_sensitive(self, encrypted_data: bytes) -> bytes:
        """Dešifrovat data"""
        if not self.security.vault:
            raise RuntimeError("QuantumSafeVault not available")
        
        import json
        container_data = json.loads(encrypted_data)
        
        from .quantum_safe import EncryptedContainer
        container = EncryptedContainer.from_dict(container_data)
        
        return await self.security.vault.decrypt(container)
    
    async def hide_in_image(
        self,
        data: bytes,
        cover_image: bytes,
        password: str = None
    ) -> bytes:
        """
        Schovat data v obrázku pomocí steganografie.
        
        Args:
            data: Data k schování
            cover_image: Cover image
            password: Volitelné heslo
            
        Returns:
            Stego image
        """
        if not self.security.communicator:
            raise RuntimeError("StealthCommunicator not available")
        
        return await self.security.communicator.hide_message(
            data, cover_image, password
        )
    
    async def extract_from_image(
        self,
        stego_image: bytes,
        password: str = None
    ) -> bytes:
        """Extrahovat data z obrázku"""
        if not self.security.communicator:
            raise RuntimeError("StealthCommunicator not available")
        
        return await self.security.communicator.extract_message(
            stego_image, password
        )
    
    def mask_query(self, query: str, strength: str = "high") -> str:
        """
        Maskovat výzkumný dotaz.
        
        Args:
            query: Původní dotaz
            strength: Síla maskování
            
        Returns:
            Maskovaný dotaz
        """
        if not self.security.obfuscator:
            return query
        
        masked = self.security.obfuscator.mask_query(query, strength)
        self.security._stats["data_obfuscated"] += 1
        
        return masked
    
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
            execute_func: Funkce pro vykonání
            chaff_count: Počet falešných dotazů
            
        Returns:
            Výsledek skutečného dotazu
        """
        if not self.security.obfuscator:
            return await execute_func(real_query)
        
        return await self.security.obfuscator.execute_with_chaff(
            real_query, execute_func, chaff_count
        )
    
    async def secure_store(
        self,
        data: bytes,
        path: str,
        encrypt: bool = True
    ) -> None:
        """
        Bezpečně uložit data.
        
        Args:
            data: Data k uložení
            path: Cesta
            encrypt: Zašifrovat před uložením
        """
        if encrypt and self.security.vault:
            data = await self.encrypt_sensitive(data)
        
        with open(path, 'wb') as f:
            f.write(data)
        
        self._temp_files.append(path)
        
        # Log
        if self.security.audit:
            await self.security.audit.log(
                event_type=AuditEventType.DATA_STORE,
                action="secure_store",
                resource=path,
                details={"encrypted": encrypt, "size": len(data)},
                session_id=self.session_id,
            )
    
    async def secure_destroy(self, path: str) -> bool:
        """
        Bezpečně zničit soubor.
        
        Args:
            path: Cesta k souboru
            
        Returns:
            True pokud úspěšné
        """
        if not self.security.destructor:
            import os
            os.remove(path)
            return True
        
        result = await self.security.destructor.destroy_file(path)
        
        if result.get("success"):
            self.security._stats["files_destroyed"] += 1
            if path in self._temp_files:
                self._temp_files.remove(path)
        
        return result.get("success", False)
    
    async def cleanup(self) -> None:
        """Vyčistit relaci"""
        logger.info(f"Cleaning up session: {self.name}")
        
        # Smazat temp files
        for path in self._temp_files[:]:
            try:
                await self.secure_destroy(path)
            except Exception as e:
                logger.warning(f"Failed to destroy {path}: {e}")
        
        self._temp_files = []
        self._encrypted_data = []
    
    async def emergency_cleanup(self) -> None:
        """Nouzové vyčištění"""
        logger.warning(f"Emergency cleanup for session: {self.name}")
        
        # Agresivní mazání
        for path in self._temp_files[:]:
            try:
                import os
                os.remove(path)
            except Exception:
                pass
        
        self._temp_files = []
        self._encrypted_data = []
