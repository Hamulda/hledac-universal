# LAYERS INTEGRATION — COMPLEX ANALYSIS & IMPLEMENTATION PLAN

**Date**: 2026-05-24
**Project**: hledac/new-hledac — layers sprint pipeline integration
**Hardware**: MacBook Air M1 8GB UMA

---

## PHASE 0 — FULL SITUATION ANALYSIS

### 0.1 Layer Inventory (15 layers, 14,626 lines)

| Layer | Lines | Public API | Status | Integration Point |
|-------|-------|-----------|--------|-------------------|
| `coordination_layer` | 2159 | EventDrivenProcessor, CoordinationLayer | **DEPRECATED** | Broken — `_check_universal_coordinators()` undefined |
| `stealth_layer` | 2752 | rotate_fingerprint(), get_fingerprint_protection() | ACTIVE | `rotate_fingerprint()` ~13211 ✅ F250 |
| `memory_layer` | 1527 | ThermalSnapshot, _MemoryStateManager | **MISLABELED** | Thermal/UMA mgmt, NOT context storage |
| `temporal_signal_layer` | 689 | observe(), get_edge_candidates(), event_from_finding_like() | ACTIVE | `temporal.observe()` ~15629 ✅ F250 |
| `security_layer` | 1195 | validate_finding(), log_action(), get_merkle_root() | ACTIVE | `security.validate_finding()` ~15569 ✅ F250 |
| `ghost_layer` | 867 | activate_stealth_mode(), is_vm_environment() | ACTIVE | GhostDirector via LayerManager ~235 ✅ |
| `privacy_layer` | 547 | detect_pii(), has_pii(), anonymize_text(), create_privacy_context() | **STANDALONE** | No sprint hook |
| `research_layer` | 444 | deep_explore(), hunt(), harvest(), create_mission() | **STANDALONE** | No sprint hook |
| `content_layer` | 759 | clean_html(), clean_html_batch(), parse_duckduckgo_results() | **STANDALONE** | No sprint hook |
| `communication_layer` | 852 | route_semantically(), encode_message(), subscribe_to_channel() | **STANDALONE** | No sprint hook |
| `smart_coordination` | 560 | process_task_with_smart_coordination() | **STANDALONE** | No sprint hook |
| `layer_manager` | 926 | LayerManager, get_layer(), initialize_all(), shutdown_all() | ACTIVE | SprintScheduler.__init__ ~5079 ✅ |
| `hive_coordination` | 725 | (internal — used by smart_coordination) | INTERNAL | — |
| `temporal_signal_runtime` | 288 | (internal — runtime for temporal layer) | INTERNAL | — |
| `temporal_signal_store` | 148 | TemporalSignalStore | INTERNAL | — |

### 0.2 Root Causes of Standalone Layers

**Problem is NOT technical debt** — the layers are well-implemented.
**Problem is MISSING INTEGRATION WIRES** — no one connected them to sprint lifecycle.

The canonical sprint path:
```
core.__main__.run_sprint()
  → SprintScheduler.run()          [28688L]
      → _run_one_cycle()
          → fetch phase            [coordinator/fetch]
          → hypothesis phase      [brain/hypothesis_engine]
          → enrichment phase      [enrichment_services]
          → storage phase        [duckdb_store]
          → export phase         [export/sprint_exporter]
```

5 layers have clear sprint-phase affinities but no insertion points wired.

---

## PHASE 1 — CRITICAL PROBLEMS TO UNDERSTAND FIRST

### Problem 1: `coordination_layer` is a Fossil, Not a Candidate

```
Status: LEGACY — Not on canonical sprint runtime path
Role: None — dead coordinator delegation seam
```

- `_check_universal_coordinators()` called at lines 637, 1150, 1451 but **never defined**
- Preserved for: `legacy/autonomous_orchestrator.py`, tests, scripts, docs only
- **Do NOT integrate** — it's a quarantined broken artifact

### Problem 2: `memory_layer` is M1 Thermal Management, Not Context Memory

This was a naming/design confusion in the original sprint F250:
- Exports: `ThermalSnapshot`, `_MemoryStateManager`, `_StorageCoordinator`, `_StealthMemoryManager`
- Purpose: M1 8GB UMA memory pressure monitoring, RAM disk, thermal throttling
- **Canonical Uma owner**: `core/resource_governor.py` — memory_layer is a secondary consumer
- `store_sprint_context()` / `recall_relevant_context()` do NOT exist and should NOT be added here
- **Correct context storage**: `knowledge/graph_service.py` + `knowledge/duckdb_store.py`

### Problem 3: F250 Already Did the Hard Work

Sprint F250 (2026-04) already integrated:
- StealthLayer (`rotate_fingerprint()` before each cycle)
- SecurityLayer (`validate_finding()` in security gate)
- TemporalSignalLayer (`temporal.observe()` for temporal events)
- GhostLayer (GhostDirector singleton via LayerManager)
- LayerManager initialization in SprintScheduler with `HLEDAC_ENABLE_LAYERS=1` gate

**The foundation is laid.** Only 5 layers remain disconnected.

---

## PHASE 2 — CUTTING-EDGE ARCHITECTURE FOR M1 8GB

### Design Principles

1. **Fail-soft everywhere** — layer failure must never block sprint
2. **Bounded memory** — each layer ≤100MB RAM, no heavy init at startup
3. **Async-native** — no blocking ops in async contexts (M1 crash risk)
4. **Lazy loading** — layers load on first access, not at process start
5. **Zero-copy** — pass data by reference, not copy
6. **Observable** — each layer call logs at INFO level with timing

### LayerManager Architecture (already correct)

```python
class LayerManager:
    # Lazy property pattern — layer loaded only on first access
    @property
    def stealth(self) -> StealthLayer: ...
    @property
    def security(self) -> SecurityLayer: ...
    @property
    def temporal(self) -> TemporalSignalLayer: ...
    @property
    def ghost(self) -> GhostLayer: ...
    @property
    def privacy(self) -> PrivacyLayer: ...      # NOT YET WIRED
    @property
    def research(self) -> ResearchLayer: ...    # NOT YET WIRED
    @property
    def content(self) -> ContentCleaner: ...  # NOT YET WIRED
    @property
    def communication(self) -> CommunicationLayer: ...  # NOT YET WIRED
```

This is the correct pattern — LayerManager lazily instantiates layers on demand.
**No layer should import MLX at module level** — all heavy deps must be lazy.

---

## PHASE 3 — INTEGRATION MAPPING (5 LAYERS)

### Layer A: `content_layer` → Sprint Fetch Phase

**Public API**:
```python
clean_html(raw_html, output_format) → CleaningResult
clean_html_batch(html_list) → List[CleaningResult]
parse_duckduckgo_results(html, num_results=10) → List[SearchResultItem]
parse_google_results(html, num_results=10) → List[SearchResultItem]
clean_search_result_url(url, src="auto") → str
extract_url_from_duckduckgo_redirect(url) → str
extract_url_from_google_redirect(url) → str
```

**Sprint insertion point**: `_run_public_discovery_in_cycle()` or `fetching/fetch_coordinator.py`

**What it does**: Cleans HTML, parses search engine results, extracts clean URLs from redirect chains

**Wire location**: After `public_fetcher.fetch()` returns raw HTML, before storing result
- Call `content.clean_html(raw_html)` to get cleaned text
- Pass cleaned text to hypothesis engine instead of raw HTML
- Parse `SearchResultItem` from search engine HTML before candidate extraction

**M1 note**: `ContentCleaner` uses MLX if `use_mlx=True` (default). Has `fallback_to_bs4=True`. Safe.

---

### Layer B: `privacy_layer` → Sprint Security Gate (augment existing)

**Public API**:
```python
detect_pii(text) → Dict[str, List[str]]
has_pii(text) → bool
anonymize_text(text, level=AnonymizationLevel.FULL) → str
create_privacy_context(level=PrivacyLevel.STANDARD) → str
close_privacy_context(context_id) → bool
validate_finding(finding) → (bool, str)  # existing security_layer already has this
```

**Sprint insertion point**: Extend existing `security_gate` around ~15543-15604

**What it does**: Detects and redacts PII from findings before storage/export

**Wire location**: After SecurityLayer.validate_finding(), before LMDB storage
- `privacy.has_pii(finding.payload_text)` — quick check
- `privacy.anonymize_text()` if PII detected
- PrivacyContext lifecycle tied to sprint session

**M1 note**: Lightweight — regex +NLP, no MLX, <10MB RAM

---

### Layer C: `research_layer` → Sprint Hypothesis Phase

**Public API**:
```python
create_mission(goal) → GhostMission
execute_mission(mission, max_steps=None) → Dict[str, Any]
deep_explore(start_url, strategy=None, max_depth=None) → List[ExplorationNode]
hunt(query, dorks=None) → List[Dict[str, Any]]
harvest(url, depth=0) → Dict[str, Any]
get_statistics() → Dict[str, Any]
```

**Sprint insertion point**: `brain/hypothesis_engine.py` — `generate_dark_surface_queries()`

**What it does**: Deep research capability — DORK-based hunting, depth-first exploration, mission-based research

**Wire location**: Before hypothesis generation:
- `research.hunt(query, dorks)` for additional IOC candidates
- `research.deep_explore()` on high-confidence domains found in sprint
- Results feed into `hypothesis_engine.generate_hypotheses()`

**M1 note**: Heavy — uses `hledac.cortex.director`, `hledac.research.depth_maximizer`, `hledac.cortex.hunter`
- Gate with `HLEDAC_ENABLE_RESEARCH_LAYER=1`
- Bounded: `max_steps=20`, `max_depth=5`
- Should run in ThreadPoolExecutor to avoid blocking M1 event loop

---

### Layer D: `communication_layer` → Sprint Export/Alert Phase

**Public API**:
```python
route_semantically(msg, sender_id) → RoutingDecision
encode_message(msg) → Dict[str, Any]
decode_message(encoded) → str
subscribe_to_channel(agent_id, channel) → bool
unsubscribe_from_channel(agent_id, channel) → bool
create_a2a_task(msg, session_id=None) → str
get_a2a_task(task_id) → Dict[str, Any]
get_stats() → Dict[str, Any]
```

**Sprint insertion point**: Post-sprint export phase — `export/sprint_exporter.py`

**What it does**: Agent-to-agent messaging, channel subscriptions, semantic routing

**Wire location**: After findings are accepted, before export:
- Notify subscribed channels of sprint completion
- Route findings to designated output channels
- A2A task creation for downstream agents

**M1 note**: Lightweight, async-native, no MLX. <5MB RAM.

---

### Layer E: `smart_coordination` → Sprint Task Distribution

**Public API**:
```python
process_task_with_smart_coordination(task_description, priority="medium") → Dict[str, Any]
get_smart_coordination_status() → Dict[str, Any]
demo_smart_spawned_integration()
```

**What it does**: Multi-agent task distribution with smart spawned agents (coder, tester, analyst roles)

**Sprint insertion point**: SprintScheduler lifecycle — NOT a per-cycle layer

**Problem**: This layer assumes it can spawn sub-agents and coordinate them.
On M1 8GB, spawning sub-agents is memory-intensive.

**Wire location**: Use for sprint-level task coordination only:
- `smart_coordination.process_task_with_smart_coordination()` for complex multi-step investigations
- NOT in hot path (`_run_one_cycle`) — too expensive
- Could replace `legacy/autonomous_orchestrator.py` coordination patterns

**M1 note**: **HIGH RISK** — spawns multiple agent processes
- Requires `HLEDAC_ENABLE_SMART_COORDINATION=1`
- Bounded: max 3 spawned agents, 5min timeout per agent
- Should be last layer to integrate, after others are stable

---

## PHASE 4 — IMPLEMENTATION ORDER (RECOMMENDED)

### Sprint 1: content_layer + privacy_layer (lowest risk, highest value)

**Rationale**: Both are fail-safe wrappers around existing data flow.
- No new behavior introduced — augmentation only
- No MLX, no agent spawning, no blocking ops
- Immediate sprint quality improvement (cleaner findings, PII protection)

**Files to modify**:
1. `fetching/fetch_coordinator.py` — insert `content.clean_html()` after fetch
2. `runtime/sprint_scheduler.py` ~15543 — extend security gate with privacy PII check

**Tests**: Add probe tests in `tests/probes/` for content_clean_html and privacy_pii

---

### Sprint 2: research_layer (medium risk, high discovery value)

**Rationale**: Adds deep research capability to hypothesis phase.
- Uses external modules (cortex.director, depth_maximizer, hunter) — verify they exist
- Requires async wrapper (ThreadPoolExecutor) to avoid blocking M1 event loop
- DORK-based hunting could significantly expand IOC discovery

**Files to modify**:
1. `brain/hypothesis_engine.py` — insert `research.hunt()` / `research.deep_explore()` before `generate_hypotheses()`
2. Add `HLEDAC_ENABLE_RESEARCH_LAYER=1` gate with memory guard

**Tests**: Probe tests for research_layer with mock cortex modules

---

### Sprint 3: communication_layer (low risk, notify/export)

**Rationale**: Post-sprint notification and channel routing.
- Lightweight, async-native
- Could replace some export notification logic

**Files to modify**:
1. `export/sprint_exporter.py` — insert communication notifications post-export
2. Or: `runtime/sprint_scheduler.py` — notify channels on sprint completion

**Tests**: Probe tests with mock A2A messaging

---

### Sprint 4: smart_coordination (highest risk, defer or scope-reduce)

**Rationale**: Multi-agent spawning on M1 8GB is dangerous.
- Without bounds, could exhaust RAM
- Pattern is unlike other layers — coordination vs. processing

**Recommendation**: Either:
- **Defer** to future sprint with explicit M1 memory modeling
- **Scope-reduce** to single-threaded coordination (no sub-agent spawning)
- **Replace** with a bounded task queue pattern instead of smart spawned agents

---

## PHASE 5 — TECHNICAL IMPLEMENTATION DETAILS

### A. LayerManager: Add Missing Property Accessors

Current `layer_manager.py` has lazy properties for 4 layers only.
Need to add 5 more:

```python
@property
def privacy(self) -> PrivacyLayer:
    if self._privacy is None:
        from .privacy_layer import PrivacyLayer, PrivacyConfig
        self._privacy = PrivacyLayer(PrivacyConfig())
    return self._privacy

@property
def research(self) -> ResearchLayer:
    if self._research is None:
        from .research_layer import ResearchLayer, DeepResearchConfig
        self._research = ResearchLayer(DeepResearchConfig())
    return self._research

@property
def content(self) -> ContentCleaner:
    if self._content is None:
        from .content_layer import ContentCleaner
        self._content = ContentCleaner()
    return self._content

@property
def communication(self) -> CommunicationLayer:
    if self._communication is None:
        from .communication_layer import CommunicationLayer, CommunicationConfig
        self._communication = CommunicationLayer(CommunicationConfig())
    return self._communication

# smart_coordination: highest risk — defer
```

Initialize all `_xxx = None` in `__init__`.

---

### B. SprintScheduler: Add Layer Wires

**Current pattern** (F250, lines ~13203-13215 for stealth):
```python
if os.environ.get("HLEDAC_ENABLE_LAYERS") == "1":
    try:
        stealth = getattr(self._layer_manager, "stealth", None)
        if stealth is not None and hasattr(stealth, "rotate_fingerprint"):
            stealth.rotate_fingerprint()
    except Exception as _e:
        log.W("layers StealthLayer.rotate_fingerprint failed: %s", _e)
```

Use same pattern for each new layer — fail-soft, gate per-layer env var.

---

### C. M1 Memory Safety Requirements

Each layer integration MUST respect:
- `<100MB per layer additional RAM`
- No `asyncio.run()` in thread pools (M1 crash vector)
- Heavy layers (research, smart_coordination) must use `HLEDAC_ENABLE_XXX=1` gate
- Memory pressure check before loading heavy layers:
  ```python
  from hledac.universal.utils.uma_budget import get_uma_snapshot
  uma = get_uma_snapshot()
  if uma.is_critical or uma.is_emergency:
      log.W("Skipping research_layer — M1 memory pressure")
      return
  ```

---

### D. Observability

Each layer call site should log:
```python
import time
start = time.monotonic()
# layer call
elapsed = time.monotonic() - start
log.info("[%s] layer call → %s (%.3fs)", layer_name, status, elapsed)
```

Add to `SprintSchedulerResult`:
```python
content_layer_ms: float = 0.0
privacy_layer_active: bool = False
research_layer_active: bool = False
communication_layer_active: bool = False
```

---

## PHASE 6 — CUTTING-EDGE METHODS & TECHNOLOGIES

### For content_layer: MLX-Powered HTML Cleaning

```python
# content_layer already supports MLX-accelerated cleaning
cleaner = ContentCleaner(use_mlx=True, fallback_to_bs4=True)
# MLX path: faster on M1 GPU cores
# BS4 fallback: CPU fallback when GPU unavailable
```

### For research_layer: Async Mission Execution

```python
# Wrap cortex modules in ThreadPoolExecutor to avoid blocking M1 event loop
executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="research")

async def research_deep_explore(url: str):
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        executor,
        lambda: research.deep_explore(url, max_depth=5)
    )
    return result
```

### For privacy_layer: Regex + Small Model PII Detection

```python
# privacy_layer uses pattern matching + optional small model
# For M1: use coreml or mlx for on-device inference
# Current impl: regex-based, no heavy model dependency
```

### For communication_layer: A2A Protocol

```python
# Standard A2A (Agent-to-Agent) protocol over async channels
# Already implemented in communication_layer
# Can integrate with existing notification system
```

---

## PHASE 7 — TESTING STRATEGY

### Probe Tests (per layer)

```python
tests/probes/
├── test_content_layer_wiring.py     # 5 tests
├── test_privacy_layer_wiring.py     # 5 tests
├── test_research_layer_wiring.py    # 5 tests
├── test_communication_layer_wiring.py  # 5 tests
└── test_smart_coordination_wiring.py   # 3 tests (deferred)
```

Each probe test:
1. Sets `HLEDAC_ENABLE_XXX=1`
2. Initializes LayerManager
3. Calls layer via SprintScheduler integration point
4. Verifies fail-soft on error
5. Verifies layer result is used correctly

### Regression: All F250 tests must still pass

```bash
pytest tests/probes/probe_f250*.py -q
```

---

## PHASE 8 — EXECUTION PLAN (SUMMARY)

```
Priority  Layer           Sprint Phase       Risk    M1 Safety  Effort
---------|--------------|-----------------|-------|----------|-------
P0       content_layer  Sprint 1 (Fetch)  LOW    ✅        2h
P0       privacy_layer  Sprint 1 (Security) LOW    ✅        2h
P1       research_layer Sprint 2 (Hypo)    MEDIUM  ⚠️        4h
P2       communication Sprint 3 (Export)   LOW    ✅        2h
P3       smart_coord   DEFERRED           HIGH   ❌        N/A
```

**Each sprint**:
1. Add property accessor to `layer_manager.py`
2. Add layer wire to `sprint_scheduler.py` with fail-soft try/except
3. Add observability (timing + result stats)
4. Add probe tests
5. Run full probe suite, verify no regression

**Estimated total**: 10h across 4 sprints
**M1 risk**: Only research_layer is elevated (heavy cortex deps)
**Coordination layer**: Completely off the table — broken artifact

---

## APPENDIX A — KEY FILES & LINE REFERENCES

| File | Lines | Key Lines |
|------|-------|-----------|
| `runtime/sprint_scheduler.py` | 28688 | 5079 (LM init), 13203 (stealth), 15543 (security), 15629 (temporal) |
| `layers/layer_manager.py` | 926 | 452 (get_layer), 533 (shutdown_all), 344 (initialize_all) |
| `layers/content_layer.py` | 759 | clean_html(), clean_html_batch() |
| `layers/privacy_layer.py` | 547 | detect_pii(), anonymize_text() |
| `layers/research_layer.py` | 444 | deep_explore(), hunt(), harvest() |
| `layers/communication_layer.py` | 852 | route_semantically(), create_a2a_task() |
| `layers/smart_coordination.py` | 560 | process_task_with_smart_coordination() |

---

## APPENDIX B — THINGS NOT TO TOUCH

1. `coordination_layer.py` — dead artifact, no production use
2. `memory_layer.py` — thermal/UMA management, not context storage
3. Already-integrated layers (stealth, security, temporal, ghost) — F250 work is done
4. `legacy/autonomous_orchestrator.py` — preserved for backward compat only