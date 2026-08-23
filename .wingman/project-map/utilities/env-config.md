# Environment Config

## Metadata

| Field | Value |
| --- | --- |
| Kind | utility |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `utilities/env-config.md` |
| Source Path | `_core/env_config.py` |

## Summary

Centralized environment variable access. ENV singleton for all configuration.

## Evidence

- Centralizes all HLEDAC_* env vars
- Used throughout codebase for configuration
- Typed accessors for env values

## Use When

- Accessing environment configuration
- Adding new env var configuration

## Do Not Use When

- Hardcoding configuration values
