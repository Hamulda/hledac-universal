**Key Points:**
- 410 HLEDAC_* env vars identified with 97 ENABLE flags, but only 36 checked at runtime (61+ unused)
- Root cause: each sprint added HLEDAC_ENABLE_X instead of using existing registries
- Three-layer solution model: Capabilities (keep), SprintProfile/LaneRegistry (fix), RuntimeConfig/FlagSpec (validate)
- CapabilityRegistry underutilized (5 files) — target growth to 30+ files
- Emergency overrides (HLEDAC_FORCE_PYTHON, HLEDAC_FORCE_RUST, HLEDAC_FORCE_LANE, memory ceilings) should remain as env vars

**Structure:**
- Reason: Documents feature flag sprawl analysis
- Raw Concept: Task, Changes, Files, Flow, Timestamp, Author
- Narrative: Structure (three layers), Dependencies (Phase 1→2→3), Highlights (emergency overrides, flag presets), Rules (4 rules)
- Facts: Quantitative metrics on env vars, flags, usage

**Notable Entities:**
- CapabilityRegistry (core/capabilities.py)
- LaneRegistry, SidecarRegistry (runtime/acquisition/profile.py)
- AcquisitionProfile (mission mode)
- FlagSpec, RuntimeConfig (config/settings.py)
- flag_presets.py (5 presets: MINIMAL/OSINT/RECON/RESEARCH/FULL)
- SprintFlags msgspec (7 fields)

**Key Decisions:**
- Rule 1: Replace os.environ.get("HLEDAC_ENABLE_X") → LaneRegistry.is_enabled("x")
- Rule 2: Keep CapabilityRegistry for dependency gates
- Rule 3: Use SidecarRegistry.sidecar_id for lane composition
- Rule 4: Use AcquisitionProfile for mission mode
- Implementation order: Phase 1 (LaneRegistry) → Phase 2 (CapabilityRegistry growth) → Phase 3 (FlagSpec validation)