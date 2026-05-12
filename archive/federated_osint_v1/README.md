# Federated OSINT v1 — Archived

**Status**: DISABLED — `enable_federated_osint=False`

These modules were the v1 federated learning substrate for distributed OSINT
node coordination. They are preserved for potential future multi-node clustering
when `enable_federated_osint=True` is re-enabled.

## Archived Modules

| File | Purpose |
|------|---------|
| `model_store.py` | Federated model checkpoint store |
| `peer_registry.py` | Node peer registry and discovery |
| `secure_aggregator.py` | Secure aggregation protocol |
| `evidence_log.py` | Federated evidence audit log |
| `post_quantum.py` | PQC provider (liboqs + Ed25519/X25519 fallback) |

## Post-Quantum Note

`post_quantum.py` is a standalone PQC provider using liboqs for ML-DSA-44/65/87
and Kyber512/768/1024. It is **NOT** the same as `security/pq_export_encryption.py`
which uses Apple's CryptoKit HPKE X-Wing for export bundle encryption.

Archived here to avoid confusion — if federated comms are re-enabled, consider
whether this liboqs-based provider or the CryptoKit path is preferred.

## Re-enabling

If `enable_federated_osint=True`:
1. Move modules back from `archive/federated_osint_v1/` to `federated/`
2. Remove archived flag in `federated/__init__.py`
3. Wire `FederatedEngine` in `brain/` pipeline
4. Run full probe suite before production activation