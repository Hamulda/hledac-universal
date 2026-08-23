# Deprecation Wrapper Pattern

## Metadata

| Field | Value |
| --- | --- |
| Kind | pattern |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `patterns/deprecation-wrapper.md` |

## Summary

Deprecated modules re-export from canonical location with warnings.warn(), kept for backward compatibility. Enables migration without breaking existing imports.

## Examples

- `forensics/ioc_extractor.py` → import from `knowledge.ioc_processor`
- `layers/stealth_layer.py` → import from `layers.stealth`
- `layers/security_layer.py` → import from `layers.security`
- `layers/layer_manager.py` → import from `layers.core.LayerRegistry`
- `network/passive_fingerprint.py` → import from `recon.passive_fingerprint`

## Evidence

- warnings.warn() with DeprecationWarning and stacklevel=2
- Re-exports all symbols from canonical module
- Stub files kept in deprecated location

## Use When

- Migrating module location without breaking existing code

## Do Not Use When

- New code (always import from canonical location)
