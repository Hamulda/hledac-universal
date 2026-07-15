"""
secrets/ — Canonical secrets management module (F350M-R)

Provides:
    - SecretVault: Password/credential store with AES-256-GCM encryption
    - Batch encryption/decryption via Rust crypto_accelerate

Canonical import path: from secrets.vault import SecretVault
Legacy alias: from security.vault_manager import LootManager (deprecated)

Architecture:
    secrets/vault.py    — Password vault with PBKDF2-KDF, AES-256-GCM, batch ops
    secrets/           — Facade / re-exports for backward compatibility
"""

from secrets.vault import SecretVault

__all__ = ["SecretVault"]
