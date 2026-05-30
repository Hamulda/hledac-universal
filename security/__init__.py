"""
Universal Security - PII Detection, Encryption, and Vault Management

Security components optimized for M1 8GB RAM with MLX acceleration.
Includes steganography detection from deep_research integration.
"""

from .pii_gate import PIICategory, PIIMatch, SanitizationResult, SecurityGate, create_security_gate, quick_sanitize
from .ram_vault import RamDiskVault
from .vault_manager import (
    LootManager,
    VaultManager,  # Alias: canonical name for secure export authority
)

# Encryption and Key Management (Sprint 61)
try:
    from .encryption import decrypt_aes_gcm, encrypt_aes_gcm
    CRYPTO_AVAILABLE = True
except ImportError:
    encrypt_aes_gcm = None
    decrypt_aes_gcm = None
    CRYPTO_AVAILABLE = False
try:
    from .key_manager import KeyManager
    KEY_MANAGER_AVAILABLE = True
except ImportError:
    KeyManager = None
    KEY_MANAGER_AVAILABLE = False

# Steganography Detector (from deep_research/steganography_watermark_detector.py)
try:
    from .stego_detector import (
        ChiSquareResult,
        DCTResult,
        RSResult,
        StatisticalStegoDetector,
        StegoAnalysisResult,
        StegoConfig,
        StegoDetector,
        StegoResult,
        create_stego_detector,
        quick_stego_check,
    )
    STEGO_AVAILABLE = True
except ImportError:
    STEGO_AVAILABLE = False

# Digital Ghost Detector (from deep_research/next_gen_enhancements.py)
try:
    from .digital_ghost_detector import (
        DigitalGhostAnalysis,
        DigitalGhostDetector,
        GhostSignal,
        RecoveredContent,
        detect_digital_ghosts,
    )
    GHOST_AVAILABLE = True
except ImportError:
    GHOST_AVAILABLE = False

# Secure Enclave (Sprint F206X)
try:
    from .secure_enclave import (
        BatchManifest,
        EnclaveAvailability,
        EnclaveStatus,
        NullSecureEnclaveBackend,
        SecureEnclaveBackend,
        SecureEnclaveError,
        SignedDigest,
        build_batch_manifest,
        create_secure_enclave_backend,
    )
    ENCLAVE_AVAILABLE = True
except ImportError:
    ENCLAVE_AVAILABLE = False

# Post-Quantum (Sprint F206Z)
try:
    from .pq_crypto import (
        HybridSignatureSet,
        NullPostQuantumBackend,
        PostQuantumBackend,
        PostQuantumError,
        PQAvailability,
        PQSignature,
        PQStatus,
        create_post_quantum_backend,
    )
    PQ_AVAILABLE = True
except ImportError:
    PQ_AVAILABLE = False

# Vault Manager (Sprint F260)
try:
    from .vault_manager import (
        CRYPTO_AVAILABLE,
        CRYPTOKIT_AVAILABLE,
        LootManager,
        PYZIPPER_AVAILABLE,
        VaultManager,
    )
    VAULT_AVAILABLE = True
except ImportError:
    VAULT_AVAILABLE = False

# PQ Export Encryption (HPKE X-Wing for export bundles)
try:
    from .pq_export_encryption import (
        ExportEncryptionEnvelope,
        ExportPolicy,
        HPKEAvailability,
        encrypt_export_bundle,
        decrypt_export_bundle,
    )
    HPKE_AVAILABLE = True
except ImportError:
    ExportEncryptionEnvelope = None
    ExportPolicy = None
    HPKEAvailability = None
    encrypt_export_bundle = None
    decrypt_export_bundle = None
    HPKE_AVAILABLE = False

# Audit Trail (HMAC-protected SQLite)
try:
    from .audit import (
        AuditEvent,
        AuditEventType,
        AuditLevel,
        AuditLogger,
    )
    AUDIT_AVAILABLE = True
except ImportError:
    AuditEvent = None
    AuditEventType = None
    AuditLevel = None
    AuditLogger = None
    AUDIT_AVAILABLE = False

# Research Obfuscation (chaff traffic, timing jitter)
try:
    from .obfuscation import (
        ObfuscationConfig,
        ResearchObfuscator,
    )
    OBFUSCATION_AVAILABLE = True
except ImportError:
    ObfuscationConfig = None
    ResearchObfuscator = None
    OBFUSCATION_AVAILABLE = False

# Secure Destruction (DoD 5220.22-M / NIST 800-88)
try:
    from .destruction import (
        DestructionConfig,
        SecureDestructor,
    )
    DESTRUCTION_AVAILABLE = True
except ImportError:
    DestructionConfig = None
    SecureDestructor = None
    DESTRUCTION_AVAILABLE = False

# CAPTCHA Detection (F202X)
try:
    from .captcha_detector import CaptchaDetector
    CAPTCHA_AVAILABLE = True
except ImportError:
    CaptchaDetector = None
    CAPTCHA_AVAILABLE = False

__all__ = [
    # PII Gate
    'SecurityGate',
    'PIICategory',
    'PIIMatch',
    'SanitizationResult',
    'create_security_gate',
    'quick_sanitize',
    # Vault
    'LootManager',
    'VaultManager',  # Alias: canonical name for secure export authority
    'RamDiskVault',
    # Encryption & Key Management
    'encrypt_aes_gcm',
    'decrypt_aes_gcm',
    'KeyManager',
    # Stego
    'StegoDetector',
    'StatisticalStegoDetector',
    'StegoAnalysisResult',
    'StegoResult',
    'StegoConfig',
    'ChiSquareResult',
    'RSResult',
    'DCTResult',
    'create_stego_detector',
    'quick_stego_check',
    'STEGO_AVAILABLE',
    # Ghost
    'DigitalGhostDetector',
    'DigitalGhostAnalysis',
    'GhostSignal',
    'RecoveredContent',
    'detect_digital_ghosts',
    'GHOST_AVAILABLE',
    # Secure Enclave
    'EnclaveAvailability',
    'EnclaveStatus',
    'SignedDigest',
    'BatchManifest',
    'SecureEnclaveBackend',
    'SecureEnclaveError',
    'NullSecureEnclaveBackend',
    'build_batch_manifest',
    'create_secure_enclave_backend',
    'ENCLAVE_AVAILABLE',
    # Post-Quantum
    'PQAvailability',
    'PQStatus',
    'PQSignature',
    'HybridSignatureSet',
    'PostQuantumBackend',
    'PostQuantumError',
    'NullPostQuantumBackend',
    'create_post_quantum_backend',
    'PQ_AVAILABLE',
    # Vault Manager
    'LootManager',
    'VaultManager',
    'CRYPTO_AVAILABLE',
    'CRYPTOKIT_AVAILABLE',
    'PYZIPPER_AVAILABLE',
    'VAULT_AVAILABLE',
    # PQ Export Encryption
    'ExportEncryptionEnvelope',
    'ExportPolicy',
    'HPKEAvailability',
    'encrypt_export_bundle',
    'decrypt_export_bundle',
    'HPKE_AVAILABLE',
    # Audit Trail
    'AuditEvent',
    'AuditEventType',
    'AuditLevel',
    'AuditLogger',
    'AUDIT_AVAILABLE',
    # Research Obfuscation
    'ObfuscationConfig',
    'ResearchObfuscator',
    'OBFUSCATION_AVAILABLE',
    # Secure Destruction
    'DestructionConfig',
    'SecureDestructor',
    'DESTRUCTION_AVAILABLE',
    # CAPTCHA Detection
    'CaptchaDetector',
    'CAPTCHA_AVAILABLE',
]
