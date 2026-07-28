---
title: Issue A5 Feature Flag Sprawl Analysis
summary: '410 HLEDAC_* env vars with 97 ENABLE flags, only 36 checked at runtime. Three-layer model: Capabilities (keep), SprintProfile/LaneRegistry (fix), RuntimeConfig/FlagSpec (validate).'
tags: []
related: [facts/project/caps-capability-registry-for-feature-gating.md, architecture/runtime/acquisition_orchestrator_lifecycle.md]
keywords: []
createdAt: '2026-07-27T21:59:43.123Z'
updatedAt: '2026-07-27T21:59:43.123Z'
---
## Reason
Documenting feature flag sprawl analysis and three-layer solution model

## Raw Concept
**Task:**
ISSUE-A5 Feature-Flag Sprawl Analysis

**Changes:**
- Identified 410 HLEDAC_* env vars with 97 ENABLE flags
- Root cause: each sprint added HLEDAC_ENABLE_X instead of using existing registries
- Proposed three-layer model: Capabilities + SprintProfile + RuntimeConfig

**Files:**
- .scratch/A5/ISSUE-A5-FEATURE-FLAG-SPRAWL.md
- core/capabilities.py
- runtime/acquisition/profile.py
- config/settings.py

**Flow:**
Analysis -> Findings -> Root Cause -> Three-Layer Model -> Implementation Phases

**Timestamp:** 2026-07-27

**Author:** ByteRover

## Narrative
### Structure
Three-layer feature flag architecture:
Layer 1 (Capabilities): core/capabilities.py — optional dependency resolution — KEEP as-is, grow adoption to 30+ files
Layer 2 (SprintProfile): runtime/acquisition/profile.py + LaneRegistry — lane composition — FIX: codemod ENABLE flags to profile.lanes frozenset
Layer 3 (RuntimeConfig): config/settings.py + FlagSpec — resource tuning — KEEP env vars, add FlagSpec.resolve_* validation

### Dependencies
Phase 1 requires LaneRegistry creation. Phase 2 requires growing CapabilityRegistry from 5 to 30+ files. Phase 3 requires wiring FlagSpec into settings.py.

### Highlights
Emergency overrides (HLEDAC_FORCE_PYTHON, HLEDAC_FORCE_RUST, HLEDAC_FORCE_LANE, memory ceilings) should remain as env vars. Flag presets work but not shown to operators.

### Rules
Rule 1: Replace os.environ.get("HLEDAC_ENABLE_X") with LaneRegistry.is_enabled("x")
Rule 2: Keep CapabilityRegistry for dependency gates
Rule 3: Use SidecarRegistry.sidecar_id for lane composition
Rule 4: Use AcquisitionProfile for mission mode

## Facts
- **env_var_count**: 410 distinct HLEDAC_* env vars exist across codebase [project]
- **enable_flag_count**: 97 HLEDAC_ENABLE_* lane-gate flags defined (24% of total env vars) [project]
- **checked_enable_flags**: Only 36 of 97 ENABLE flags have actual os.environ.get() checks [project]
- **unused_enable_flags**: 61+ ENABLE flags defined but never checked at runtime [project]
- **capability_registry_usage**: CapabilityRegistry used by only 5 files - underutilized [project]
- **flag_presets**: flag_presets.py has 5 presets: MINIMAL/OSINT/RECON/RESEARCH/FULL [project]
- **sprintflags_fields**: SprintFlags msgspec has 7 fields - minimal design [project]
