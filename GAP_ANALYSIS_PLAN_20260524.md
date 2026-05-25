# GAP Analysis & Implementation Plan — 2026-05-24

## Section 1: Current Architecture Understanding

### 1.1 OSINT Report Flow

```
findings: list[dict]
    ↓
SynthesisRunner.synthesize_findings(query, findings, ...)
    ↓ (Outlines constrained generation via Hermes3Engine)
OSINTReport (msgspec.Struct)
    ├── query: str
    ├── ioc_entities: list[IOCEntity]  ← Extracted IOCs
    │   ├── val: str
    │   ├── ioc_type: str
    │   ├── severity: str
    │   └── ctx: str
    ├── threat_summary: str
    ├── threat_actors: list[str]
    ├── confidence: float
    ├── sources_count: int
    └── timestamp: float
```

### 1.2 Where Each GAP Fits

| GAP | Component | Location | Gap |
|-----|-----------|----------|-----|
| 8 | Evidence Grounding | SynthesisRunner → output validation | IOC in report not verified against findings |
| 3/1 | Model Circuit Breaker | Hermes3Engine/MDL | No per-model failure tracking |
| 7 | Output Schema Validator | Hermes3Engine generate() | msgspec validates structure, not semantic validity |
| 5 | Prompt Injection Sandbox | Hermes3Engine._sanitize_for_llm | Only text hook, no structured validation |
| 17 | Model Integrity | model_manager.py | No SHA256 hash verification |
| 23 | Benchmark Harness | tests/probe_* | No performance benchmarks |

### 1.3 Key Classes

**OSINTReport** (brain/synthesis_runner.py:273):
- msgspec.Struct with 8 fields
- Validated by Outlines at generation time
- NOT re-validated post-generation

**CircuitBreaker** (transport/circuit_breaker.py:88):
- Tracks domain failures with CLOSED/OPEN/HALF_OPEN states
- `_record_domain_failure()` and `is_open()` methods
- Does NOT track model inference failures

**Hermes3Engine.generate()**:
- Calls `_sanitize_for_llm` as text hook
- Has P1G-A comment but no structured injection validation
- No per-model failure tracking

---

## Section 2: GAP Root Cause Analysis

### GAP-8: Evidence Grounding (CRITICAL)

**Problem**: `synthesize_findings()` extracts IOCs into `OSINTReport.ioc_entities` but there is NO validation that:
1. Each IOC in the report actually appears in the source `findings`
2. `threat_summary` claims are supported by evidence

**Current flow**:
```
findings[0] = {content: "...found C2 at 192.168.1.1..."}
findings[1] = {content: "...malware hash abc123..."}
    ↓ SynthesisRunner
OSINTReport {
    ioc_entities: [
        IOCEntity {val: "192.168.1.1", ...},
        IOCEntity {val: "abc123", ...}  ← Could be fabricated!
    ]
}
    ↓ (No validation)
DuckDB storage → Research output
```

**Impact**: Fabricated IOCs silently pollute research quality. A hallucinated IOC can cause false positives in downstream processing.

**Fix approach**: Add `validate_grounding(report: OSINTReport, findings: list[dict]) -> tuple[bool, list[str]]` that:
1. Builds a set of all IOC values in findings (by scanning content)
2. Checks each IOCEntity.val against that set
3. Returns (is_valid, list of unmatched IOCs)
4. Fail-soft: logs mismatch, returns True with warnings

### GAP-3/1: Model Circuit Breaker + InferenceGuard (P0)

**Problem**: `CircuitBreaker` in transport/ tracks HTTP domain failures ONLY. Model inference failures (OOM, Metal driver errors, timeouts) are NOT tracked per-model.

**Current state**:
- Domain CircuitBreaker: tracks `_failure_count`, trips at threshold
- Model inference: no tracking, failures crash or retry forever

**Impact**: Repeated inference failures cause memory fragmentation and instability on M1 8GB. No early detection before OOM.

**Fix approach**: Extend `CircuitBreaker` class or create `ModelCircuitBreaker`:
```python
@dataclass
class ModelCircuitBreaker:
    model_id: str
    failure_count: int = 0
    failure_threshold: int = 3  # trip after 3 failures
    recovery_timeout_s: float = 30.0
    last_failure: float = 0.0
    state: CBState = CBState.CLOSED
    
    def record_failure(self, kind: str): ...
    def record_success(self): ...
    def is_open(self) -> bool: ...
```

Integrate with `Hermes3Engine.generate()`:
```python
try:
    result = await self._outlines_generate(...)
    self._model_breaker.record_success()
    return result
except OOMError as e:
    self._model_breaker.record_failure("oom")
    raise
except TimeoutError as e:
    self._model_breaker.record_failure("timeout")
    raise
```

### GAP-7: Output Schema Validator (P1)

**Problem**: `OSINTReport` uses msgspec.Struct which validates structure at decode time. But semantic validity is NOT checked:
- `confidence` could be -0.5 or 999.0
- `sources_count` could be -1
- `threat_summary` could be empty string
- `ioc_entities` could be empty list with no findings

**Fix approach**: Add post-generation semantic validator:
```python
def validate_osint_report_semantics(report: OSINTReport) -> tuple[bool, list[str]]:
    errors = []
    if not 0.0 <= report.confidence <= 1.0:
        errors.append(f"confidence {report.confidence} out of range [0,1]")
    if report.sources_count < 0:
        errors.append(f"sources_count {report.sources_count} negative")
    if not report.ioc_entities and report.sources_count > 0:
        errors.append("no IOCs extracted but sources_count > 0")
    return (len(errors) == 0, errors)
```

Actually, checking if msgspec itself validates ranges:
- msgspec only validates types, not value ranges
- Pydantic would need `Field(ge=0, le=1)` for range validation
- Since OSINTReport is msgspec.Struct, need explicit semantic validation

### GAP-5: Prompt Injection Sandbox (P1)

**Problem**: `_sanitize_for_llm` is a text hook that truncates to `MAX_LLM_PROMPT_CHARS`. No structured validation of prompt structure.

**What exists**:
- Comment `# P1G-A: Prompt injection validator` exists in code
- But actual pattern matching code is NOT implemented
- Only `_sanitize_for_llm` callback exists

**Fix approach**: Implement structured prompt injection detection:
```python
# Patterns that indicate instruction injection
INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+(instructions|commands)",
    r"(system|prompt)\s*:\s*you\s+are\s+a",
    r"\bROLE\s*:\s*admin",
    r"```system",
    r"<\|system\|>",
    r"#{3,}\s*system",
    r"(delimiter|injection)\s*:\s*",
]

def detect_prompt_injection(prompt: str) -> tuple[bool, list[str]]:
    matches = []
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, prompt, re.IGNORECASE):
            matches.append(pattern)
    return (len(matches) > 0, matches)
```

Wire into `Hermes3Engine.generate()` before `_sanitize_for_llm`.

---

## Section 3: M1 8GB Compatibility

All implementations MUST respect:
- **Memory budget**: <100MB additional RAM per feature
- **Bounded collections**: No unbounded growth
- **Fail-soft**: Never crash the sprint on validation failure
- **Async-native**: All blocking ops in thread pools

**Pattern for all implementations**:
```python
async def validate_grounding(report, findings) -> tuple[bool, list[str]]:
    """Fail-soft: returns (True, []) on success, (True, warnings) on partial"""
    if not findings:
        return (True, ["no findings to validate against"])
    try:
        # bounded operations only
        evidence_set = set()
        for f in findings[:MAX_VALIDATION_FINDINGS]:  # bounded
            evidence_set.update(extract_iocs(f))
        unmatched = [ioc for ioc in report.ioc_entities if ioc.val not in evidence_set]
        if unmatched:
            logger.warning(f"Unmatched IOCs: {unmatched}")
        return (True, unmatched)  # fail-soft
    except Exception as e:
        logger.debug(f"Grounding validation failed: {e}")
        return (True, [])  # fail-soft
```

---

## Section 4: Implementation Plan

### Sprint 1: GAP-8 Evidence Grounding + GAP-7 Schema Validator

**Files to modify**:
- `brain/synthesis_runner.py` — add validators
- `tests/probe_f250a_sfo_completeness.py` — add tests

**Implementation**:

```python
# brain/synthesis_runner.py

MAX_VALIDATION_FINDINGS = 100  # bounded

def _extract_iocs_from_finding(finding: dict) -> set[str]:
    """Extract all IOC values from a finding dict."""
    iocs = set()
    # Check common IOC fields
    for field in ['ioc_val', 'val', 'value', 'indicator']:
        if field in finding:
            iocs.add(str(finding[field]))
    # Check raw content
    content = finding.get('content', '') or finding.get('raw_content', '')
    if content:
        # Extract IPs, domains, hashes via regex
        import re
        for pattern in [
            r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b',  # IPv4
            r'\b[a-zA-Z0-9][a-zA-Z0-9-]*\.[a-zA-Z]{2,}\b',  # Domain
            r'\b[a-fA-F0-9]{32,}\b',  # MD5/SHA256
        ]:
            iocs.update(re.findall(pattern, content))
    return iocs

def validate_evidence_grounding(
    report: OSINTReport,
    findings: list[dict]
) -> tuple[bool, list[str]]:
    """
    Validate that each IOC in report appears in source findings.
    Returns (is_valid, list of unmatched IOC values).
    Fail-soft: always returns (True, warnings) not crash.
    """
    if not findings:
        return (True, ["no findings to validate against"])
    
    evidence_set = set()
    for f in findings[:MAX_VALIDATION_FINDINGS]:
        evidence_set.update(_extract_iocs_from_finding(f))
    
    unmatched = [
        ioc.val for ioc in report.ioc_entities
        if ioc.val not in evidence_set
    ]
    
    if unmatched:
        logger.warning(f"Evidence grounding mismatch: {len(unmatched)} IOCs not in findings")
    
    return (True, unmatched)  # fail-soft

def validate_report_semantics(report: OSINTReport) -> tuple[bool, list[str]]:
    """Validate OSINTReport semantic constraints."""
    errors = []
    
    if not 0.0 <= report.confidence <= 1.0:
        errors.append(f"confidence {report.confidence} out of [0,1]")
    if report.sources_count < 0:
        errors.append(f"sources_count {report.sources_count} negative")
    if len(report.ioc_entities) == 0 and report.sources_count > 0:
        errors.append("empty ioc_entities with positive sources_count")
    
    return (len(errors) == 0, errors)
```

Wire into `synthesize_findings()` at the end:
```python
# After OSINTReport is produced
is_valid, grounding_warnings = validate_evidence_grounding(report, findings)
if grounding_warnings:
    logger.warning(f"Grounding warnings: {grounding_warnings}")

is_valid, semantic_errors = validate_report_semantics(report)
if not is_valid:
    logger.warning(f"Semantic validation errors: {semantic_errors}")
```

### Sprint 2: GAP-3/1 Model Circuit Breaker

**Files to modify**:
- `transport/circuit_breaker.py` — add ModelCircuitBreaker
- `brain/hermes3_engine.py` — wire in breaker
- `tests/probe_f250a_sfo_completeness.py` — add tests

**Implementation**:

```python
# transport/circuit_breaker.py

@dataclass
class ModelCircuitBreaker:
    """Per-model circuit breaker for inference failures."""
    model_id: str
    failure_threshold: int = 3
    recovery_timeout_s: float = 30.0
    _failure_count: int = field(default=0)
    _last_failure_time: float = field(default=0.0)
    _state: CBState = field(default=CBState.CLOSED)
    _last_failure_kind: str = field(default="")

    def record_failure(self, kind: str):
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        self._last_failure_kind = kind
        if self._failure_count >= self.failure_threshold:
            self._state = CBState.OPEN

    def record_success(self):
        self._failure_count = 0
        self._state = CBState.CLOSED

    def is_open(self) -> bool:
        if self._state == CBState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self.recovery_timeout_s:
                self._state = CBState.HALF_OPEN
            return True
        return False

    def get_snapshot(self) -> dict:
        return {
            "model_id": self.model_id,
            "state": self._state.name,
            "failure_count": self._failure_count,
            "last_failure_kind": self._last_failure_kind,
        }
```

Wire into `Hermes3Engine.generate()`:
```python
# At inference call site
if self._model_breaker and self._model_breaker.is_open():
    raise RuntimeError(f"Model inference blocked: {self._model_breaker.failure_count} failures")

try:
    result = await self._outlines_generate(...)
    if self._model_breaker:
        self._model_breaker.record_success()
    return result
except RuntimeError as e:
    if "memory" in str(e).lower() or "oom" in str(e).lower():
        if self._model_breaker:
            self._model_breaker.record_failure("oom")
    raise
```

### Sprint 3: GAP-5 Prompt Injection Sandbox

**Files to modify**:
- `brain/hermes3_engine.py` — implement P1G-A patterns
- `tests/probe_f250a_sfo_completeness.py` — add tests

**Implementation**:

```python
# brain/hermes3_engine.py

# Sprint 33: P1G-A Prompt Injection Patterns
INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+(instructions|commands)", re.I),
    re.compile(r"(?:system|prompt)\s*:\s*you\s+are\s+a", re.I),
    re.compile(r"#{3,}\s*system\s*[:\s]", re.I),
    re.compile(r"<\|system\|>", re.I),
    re.compile(r"\bROLE\s*:\s*admin", re.I),
    re.compile(r"(?:delimiter|injection)\s*[:\s]", re.I),
]

def detect_prompt_injection(prompt: str) -> tuple[bool, list[str]]:
    """Detect prompt injection patterns. Returns (is_injection, matched_patterns)."""
    matched = []
    for pattern in INJECTION_PATTERNS:
        if pattern.search(prompt):
            matched.append(pattern.pattern)
    return (len(matched) > 0, matched)

# In generate(), before _sanitize_for_llm:
is_injection, patterns = detect_prompt_injection(prompt)
if is_injection:
    logger.warning(f"Prompt injection detected: {patterns}")
    # Log but don't block — fail-soft
```

### Sprint 4: GAP-17 Model Integrity + GAP-23 Benchmark

**GAP-17**: Add SHA256 registry in `model_manager.py`:
```python
MODEL_REGISTRY_PATH = Path("~/.hledac/model_registry.json").expanduser()

def _verify_model_integrity(model_id: str, model_path: Path) -> bool:
    """Verify model hash against registry."""
    if not MODEL_REGISTRY_PATH.exists():
        return True  # No registry = trust
    registry = json.loads(MODEL_REGISTRY_PATH.read_text())
    if model_id not in registry:
        return True  # Not in registry = trust
    expected_hash = registry[model_id]["sha256"]
    actual_hash = hashlib.sha256(model_path.read_bytes()).hexdigest()
    return actual_hash == expected_hash
```

**GAP-23**: Create `benchmark/llm_benchmark.py` (60 lines, not urgent).

---

## Section 5: Testing Strategy

Each GAP needs:
1. Unit tests in `tests/probe_f250a_sfo_completeness.py`
2. Fail-soft verification (validation errors log, don't crash)
3. M1 memory budget check

**Test pattern**:
```python
def test_evidence_grounding_validator():
    report = OSINTReport(query="test", ioc_entities=[
        IOCEntity(val="192.168.1.1", ioc_type="ip", severity="high", ctx="test")
    ], ...)
    findings = [{'content': '...found C2 at 192.168.1.1...'}]
    
    is_valid, unmatched = validate_evidence_grounding(report, findings)
    assert is_valid
    assert "192.168.1.1" not in unmatched

def test_evidence_grounding_fabricated_ioc():
    report = OSINTReport(query="test", ioc_entities=[
        IOCEntity(val="1.2.3.4", ioc_type="ip", severity="high", ctx="test")
    ], ...)
    findings = [{'content': '...no IP here...'}]  # fabricated
    
    _, unmatched = validate_evidence_grounding(report, findings)
    assert "1.2.3.4" in unmatched  # caught
```

---

## Section 6: Risk Assessment

| GAP | Complexity | M1 Risk | Fix LOC | Priority |
|-----|------------|---------|---------|----------|
| 8 | Medium | Low | 35 | P0-CRITICAL |
| 3/1 | High | Medium | 70 | P0 |
| 7 | Low | Low | 25 | P1 |
| 5 | Medium | Low | 40 | P1 |
| 17 | Low | Low | 15 | P2 |
| 23 | Medium | Low | 60 | P2 |

**Highest risk**: GAP-3/1 (Model Circuit Breaker) — modifies critical inference path
**Lowest risk**: GAP-7 (Schema Validator) — additive validation only

---

## Section 7: Implementation Order Recommendation

```
Sprint 1 (GAP-8 + GAP-7): Evidence + Schema validation
  - Low risk, high impact
  - 60 lines total
  - Can be tested in isolation

Sprint 2 (GAP-3/1): Model Circuit Breaker  
  - Medium risk, critical for M1 stability
  - 70 lines
  - Requires careful integration testing

Sprint 3 (GAP-5): Prompt Injection Sandbox
  - Medium complexity
  - 40 lines
  
Sprint 4 (GAP-17 + GAP-23): Integrity + Benchmark
  - Lower priority
  - P2 items
```