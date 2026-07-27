---
confidence: 0.75
sources: [transport_layers/_index.md, facts/project/_index.md, knowledge_base/audit/_index.md]
synthesized_at: '2026-07-26T11:44:30.879Z'
type: synthesis
title: CAPS Capability Registry for Feature Gating
summary: CAPS.require() replaces availability checks (is_curl_cffi_available) as the canonical capability gating mechanism
tags: [caps, feature-flags, capability, gating, architecture]
related: [facts/project/issue_0_2_curl_cffi_caps_invariants.md]
keywords: [caps, capability, feature-flags, require, availability, curl_cffi, gating]
createdAt: '2026-07-26T11:44:30.879Z'
updatedAt: '2026-07-26T11:44:30.879Z'
---

# CAPS Capability Registry for Feature Gating

CAPS is emerging as the system-wide capability registry. ISSUE-0.2 migrated from is_curl_cffi_available() to CAPS.require(CURL_CFFI) with FAIL-FAST policy. This pattern should be extended to other optional dependencies.

## Evidence

- **transport_layers**: FetchCoordinator uses CAPS.require(CURL_CFFI) instead of is_curl_cffi_available()
- **facts/project**: 14+ HLEDAC_* environment gates control features
- **knowledge_base/audit**: 50+ feature flags documented in feature_flags_reference
