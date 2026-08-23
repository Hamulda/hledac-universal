# security-vault-manager

**Type:** Security Layer  
**Path:** `security/vault_manager.py`  
**Status:** current

## Purpose

Encrypted secrets vault for API keys, credentials, and sensitive configuration.

## Key Functions

| Function | Purpose |
|----------|---------|
| `VaultManager` | Main class |
| `store(key, value)` | Store encrypted secret |
| `retrieve(key)` | Retrieve decrypted secret |
| `delete(key)` | Securely delete secret |

## Invariants

- [SVM-1] Encryption: AES-256-GCM
- [SVM-2] Key derivation: Argon2id
- [SVM-3] Master password from environment or keychain
- [SVM-4] Never log decrypted values

## Storage

Backed by encrypted LMDB database at `~/.hledac/vault.lmdb`
