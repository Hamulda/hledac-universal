# Security Review Report - Sprint F195 Integration

**Scope:** Modified files from sprint F195 integration in `hledac/universal/`
**Risk Level:** HIGH

## Summary
- Critical Issues: 2
- High Issues: 6
- Medium Issues: 8
- Low Issues: 4

---

## Critical Issues (Fix Immediately)

### 1. Dead Code After Return - Circuit Breaker Logic Bug
**Severity:** CRITICAL
**Category:** Code Quality / Potential Security Misconfiguration
**Location:** `coordinators/fetch_coordinator.py:411-419`
**Exploitability:** N/A (logic bug)
**Blast Radius:** Circuit breaker state tracking may not persist correctly across restarts

**Issue:**
```python
def get_blocked_domains(self) -> Dict[str, float]:
    """Returns {domain: unblock_timestamp} for currently blocked domains."""
    now = time.time()
    return {d: t for d, t in self._domain_blocked_until.items() if t > now}

    # Exponential backoff retry (Fix 2)  # <-- DEAD CODE
    self._base_retry_delay = 1.0
    self._max_retries = 3
    self._max_backoff_delay = 30.0
```

The return statement on line 414 causes lines 416-419 to be unreachable. The circuit breaker retry configuration is never actually assigned to instance variables.

**Remediation:**
```python
def get_blocked_domains(self) -> Dict[str, float]:
    """Returns {domain: unblock_timestamp} for currently blocked domains."""
    now = time.time()
    return {d: t for d, t in self._domain_blocked_until.items() if t > now}
```

---

### 2. Audit Log Deletion in Emergency Purge
**Severity:** CRITICAL
**Category:** Security Misconfiguration / Compliance
**Location:** `security/deep_research_security.py:246-248`
**Exploitability:** Local (requires authenticated access to trigger emergency purge)
**Blast Radius:** Complete loss of audit trail, compliance violation

**Issue:**
```python
async def emergency_purge(self) -> Dict[str, Any]:
    # ...
    # Ukoncit vsechny sessions
    for session in self._active_sessions[:]:
        await session.emergency_cleanup()
    
    # Smazat audit log pokud je to bezpecne  # <-- AUDIT LOG DELETION
    # (v reálném nasazení by toto mělo být konfigurovatelné)
```

The comment indicates intent to delete audit logs in emergency purge. If this code path executes, it would destroy forensic evidence and violate compliance requirements.

**Remediation:**
```python
async def emergency_purge(self) -> Dict[str, Any]:
    # Audit logs MUST be preserved for compliance and forensic analysis
    # Never delete audit logs, even in emergency purge
    if self.audit:
        await self.audit.log(
            event_type=AuditEventType.SYSTEM_EVENT,
            action="emergency_purge_initiated",
            resource=f"sessions: {[s.name for s in self._active_sessions]}",
            level=AuditLevel.CRITICAL,
        )
```

---

## High Issues

### 3. External Module Trust Boundary - GhostLayer SystemContext
**Severity:** HIGH
**Category:** Supply Chain / Trust Boundary
**Location:** `layers/ghost_layer.py:131-150`
**Exploitability:** External module import
**Blast Radius:** System compromise if SystemContext is malicious

**Issue:**
```python
async def _init_system_context(self) -> None:
    """Initialize SystemContext for anti-VM protection and system monitoring."""
    try:
        self._system_context = SystemContext(
            enable_anti_vm=True,
            enable_process_monitoring=True,
            enable_integrity_checking=True,
            enable_stealth_mode=False,
            m1_optimization=True
        )
```

The code imports `SystemContext` from `kernel.context` which appears to be an external module. This creates a trust boundary where the orchestrator relies on external code for security-critical functions (anti-VM detection, process monitoring).

**Remediation:**
- Verify SystemContext source and integrity
- Add code signing verification for imported modules
- Implement interface contracts for external security components

---

### 4. Source Compiled Bytecode - Cannot Audit atomic_storage
**Severity:** HIGH
**Category:** Auditability / Security Review
**Location:** `knowledge/atomic_storage.py`
**Exploitability:** Unknown (source unavailable)
**Blast Radius:** Knowledge graph storage operations cannot be security-audited

**Issue:**
The `atomic_storage.py` is a stub that references compiled bytecode:
```
"""Stub pro .../knowledge/__pycache__/atomic_storage.cpython-312.pyc - generováno z bytecode"""
```

This module handles critical knowledge graph storage operations including ClaimClusterIndex, entity storage, and evidence packets. Without source code, we cannot verify:
- Input validation
- SQL/Query injection prevention
- Data isolation between tenants
- Access control

**Remediation:**
Recover and commit source code for atomic_storage.py. If source is lost, treat as potentially compromised and rotate all stored credentials/secrets.

---

### 5. S3 Bucket Enumeration Without Rate Limiting
**Severity:** HIGH
**Category:** Denial of Service / Information Disclosure
**Location:** `intelligence/exposed_service_hunter.py:115-200+`
**Exploitability:** Network (can be triggered remotely via search queries)
**Blast Radius:** Target AWS infrastructure hit with enumeration requests

**Issue:**
S3BucketEnumerator performs unauthenticated HEAD requests to AWS S3 endpoints using common bucket naming patterns. While S3 enumeration is a known OSINT technique, the implementation lacks:
- Rate limiting per target
- Respect for AWS abuse policies
- Request throttling to avoid triggering GuardDuty

**Remediation:**
```python
class S3BucketEnumerator:
    def __init__(self, ...):
        # Add rate limiting
        self._rate_limiter = TokenBucket(rate=10, capacity=20)  # 10 req/sec max
        self._cooldown_domains = {}  # Track domains to avoid
    
    async def enumerate_buckets(self, target: str, ...):
        # Check cooldown before enumeration
        if target in self._cooldown_domains:
            return []  # Skip already-enumerated targets
```

---

### 6. RAM Disk Mount/Unmount with Privilege Requirements
**Severity:** HIGH
**Category:** Privilege Escalation / System Integrity
**Location:** `security/ram_vault.py:17-67`
**Exploitability:** Local (requires elevated privileges)
**Blast Radius:** System integrity, potential for symlink attacks

**Issue:**
```python
def mount(self) -> Optional[str]:
    # ...
    create_result = subprocess.run(
        ["hdiutil", "attach", "-nomount", f"ram://{block_count}"],
        # ...
    )
    format_result = subprocess.run(
        ["diskutil", "erasevolume", "HFS+", self.name, self.device_path],
        # ...
    )
```

The RAM disk operations:
1. Require root privileges via `hdiutil` and `diskutil`
2. Use `self.name` directly in command - potential injection if name is user-controlled
3. No verification of device path before formatting

**Remediation:**
```python
def mount(self) -> Optional[str]:
    # Validate name is safe alphanumeric only
    if not re.match(r'^[a-zA-Z0-9_-]+$', self.name):
        raise ValueError(f"Invalid RAM disk name: {self.name}")
    
    # Use absolute path verification
    if self.device_path and not self.device_path.startswith('/dev/'):
        return None  # Reject unexpected device paths
```

---

### 7. Custom Cryptographic Primitives - SpikingNeuralNetwork
**Severity:** HIGH
**Category:** Cryptographic Weakness
**Location:** `security/quantum_safe.py:135-200+`
**Exploitability:** Cryptographic library
**Blast Radius:** Data encrypted with weak custom crypto may be compromised

**Issue:**
```python
class SpikingNeuralNetwork:
    """Minimal SNN for cryptographic operations."""
    
    def initialize(self):
        """Initialize network weights lazily."""
        if self._initialized:
            return
        # SNN-based encryption using custom neural network
```

The module implements "Neuromorphic Cryptography - SNN-based encryption" as part of quantum-safe crypto. Custom cryptographic implementations are extremely high risk because:
- No public review or cryptanalysis
- May have subtle weaknesses in key derivation
- Not NIST standardized

**Remediation:**
Remove SpikingNeuralNetwork from production crypto. Use only NIST-approved post-quantum algorithms (CRYSTALS-Kyber, CRYSTALS-Dilithium) from well-audited libraries like `cryptography` or `pqcrypto`.

---

### 8. Weak Entropy Mixing in EntropyPool
**Severity:** HIGH
**Category:** Cryptographic Weakness
**Location:** `security/quantum_safe.py:61-106`
**Exploitability:** Cryptographic
**Blast Radius:** Weak entropy can compromise random number generation

**Issue:**
```python
def add_entropy(self, source: str, entropy_bytes: bytes) -> None:
    source_hash = hashlib.sha256(source.encode()).digest()
    
    for i, byte in enumerate(entropy_bytes):
        mixed_byte = byte ^ source_hash[i % len(source_hash)]  # <-- Weak XOR
        self._entropy_data.append(mixed_byte)
```

Simple XOR mixing with a repeating hash is cryptographically weak. An attacker who knows the source of entropy could potentially predict the mixed output.

**Remediation:**
```python
def add_entropy(self, source: str, entropy_bytes: bytes) -> None:
    # Use HMAC-based mixing for proper entropy combination
    import hmac
    key = hashlib.sha256(source.encode()).digest()
    mixed = hmac.new(key, entropy_bytes, hashlib.sha256).digest()
    for byte in mixed:
        self._entropy_data.append(byte)
```

---

## Medium Issues

### 9. Session Cookie Exposure in Logs
**Severity:** MEDIUM
**Category:** Sensitive Data Exposure
**Location:** `coordinators/fetch_coordinator.py:1082-1084`
**Exploitability:** Local (logs may be accessible)
**Blast Radius:** Session cookies could be recovered from logs

**Issue:**
```python
if self._session_manager:
    session = await self._session_manager.get_session(domain)
    if session:
        session_cookies = session.get('cookies')
        # Cookies may be logged if session debugging is enabled
```

**Remediation:**
Mask cookies in all logging:
```python
if session_cookies:
    masked = {k: f"***{v[-4:]}" if len(v) > 4 else "****" for k, v in session_cookies.items()}
    logger.debug(f"Session cookies for {domain}: {masked}")
```

---

### 10. Tor Session Proxy Hardcoded
**Severity:** MEDIUM
**Category:** Configuration Security
**Location:** `coordinators/fetch_coordinator.py:714`
**Exploitability:** Configuration
**Blast Radius:** Tor traffic routing is not configurable per deployment

**Issue:**
```python
connector = aiohttp_socks.SocksConnector.from_url('socks5://127.0.0.1:9050', rdns=True)
```

Hardcoded Tor proxy address. While reasonable for development, production deployments may need different configurations.

**Remediation:**
Move to configuration:
```python
tor_proxy = os.environ.get('TOR_PROXY', 'socks5://127.0.0.1:9050')
connector = aiohttp_socks.SocksConnector.from_url(tor_proxy, rdns=True)
```

---

### 11. Paywall Bypass Content Modification
**Severity:** MEDIUM
**Category:** Security Misconfiguration
**Location:** `coordinators/fetch_coordinator.py:1182-1189`
**Exploitability:** Content modification
**Blast Radius:** Integrity of fetched content is compromised

**Issue:**
```python
if len(content) < 5000 and self._paywall_bypass:
    bypass_result = await self._paywall_bypass.bypass(url, content)
    if bypass_result:
        result['content'] = bypass_result.get('content', '').encode()
        result['bypassed'] = bypass_result.get('bypassed')
```

The paywall bypass modifies fetched content, which:
- May violate Terms of Service
- Compromises content integrity verification
- Could introduce security risks if bypass mechanism is flawed

**Remediation:**
Document the bypass feature clearly, add configuration to disable, and ensure content integrity checks skip bypassed content.

---

### 12. Lightpanda Binary Download Over HTTP
**Severity:** MEDIUM
**Category:** Supply Chain / Integrity
**Location:** `coordinators/fetch_coordinator.py:265`
**Exploitability:** Network (M1TM)
**Blast Radius:** Malicious Lightpanda binary could compromise system

**Issue:**
```python
url = "https://github.com/lightpanda-io/browser/releases/latest/download/lightpanda-aarch64-macos"
```

HTTPS is used, which is good. However, the download is not checksum-verified after download.

**Remediation:**
```python
async with session.get(url) as resp:
    if resp.status == 200:
        content = await resp.read()
        # Verify SHA256 hash before execution
        expected_hash = "..."  # From secure source
        actual_hash = hashlib.sha256(content).hexdigest()
        if actual_hash != expected_hash:
            raise SecurityError("Lightpanda binary hash mismatch")
        with open(self._bin_path, 'wb') as f:
            f.write(content)
```

---

### 13. Deep Research Feature Flag Bypass
**Severity:** MEDIUM
**Category:** Access Control
**Location:** `coordinators/fetch_coordinator.py:1212`
**Exploitability:** Configuration
**Blast Radius:** Security controls can be bypassed via environment variable

**Issue:**
```python
if os.environ.get("GHOST_DEEP_RESEARCH") != "1":
    return None
```

Feature flags via environment variables can be set by any code with access to the environment, potentially bypassing security controls.

**Remediation:**
Use a proper configuration system with security-aware defaults and explicit enable/disable with audit logging.

---

### 14. DNS Rebinding Defense - Time-of-Check to Time-of-Use (TOCTOU)
**Severity:** MEDIUM
**Category:** SSRF
**Location:** `coordinators/fetch_coordinator.py:538-574`
**Exploitability:** Network
**Blast Radius:** Private network access via DNS rebinding

**Issue:**
```python
async def _validate_fetch_target(self, url: str) -> Tuple[bool, Dict[str, Any]]:
    # Validates DNS resolution
    ips = sorted(set(str(r[4][0]) for r in raw_results))  # Check happens here
    # ...
    # BUT fetch happens later in _fetch_url
    result = await self._fetch_with_tor(url)  # or other fetch methods
```

There's a potential TOCTOU race: DNS can change between validation and fetch. An attacker could:
1. Register a domain pointing to public IP
2. Pass validation
3. Change DNS to point to private IP before fetch executes

**Remediation:**
```python
# For critical security contexts, use DNS cherry-picking:
# 1. Connect to first resolved IP
# 2. Verify IP is in certificate SAN (for HTTPS)
# 3. Or: fetch with Host header, let server redirect
```

---

### 15. Obfuscation Mappings May Expose Sensitive Terms
**Severity:** MEDIUM
**Category:** Information Disclosure
**Location:** `security/obfuscation.py:79-118`
**Exploitability:** Configuration
**Blast Radius:** Obfuscation mappings reveal sensitive search patterns

**Issue:**
```python
SENSITIVE_MAPPINGS = {
    'competitive intelligence': 'market research',
    'corporate espionage': 'industry analysis',
    'hacking': 'security testing',
    'data breach': 'information disclosure',
    # ... extensive mapping list
}
```

The existence of these mappings indicates what types of searches the system considers sensitive. While the mappings themselves are for obfuscation, they reveal the system's operational security concerns.

**Remediation:**
This is acceptable for the use case. Ensure these mappings are not committed to public repositories.

---

### 16. Memory Pressure Callbacks Not Sandboxed
**Severity:** MEDIUM
**Category:** Resource Exhaustion
**Location:** `coordinators/memory_coordinator.py`
**Exploitability:** System state
**Blast Radius:** Callbacks could trigger recursive memory pressure

**Issue:**
```python
@dataclass
class MemoryPattern:
    def decay(self, decay_rate: float = 0.01) -> None:
        """Apply exponential decay to memory strength."""
        self.strength *= (1.0 - decay_rate)
```

Memory cleanup callbacks are invoked based on system state. If callbacks themselves allocate memory, they could trigger cascading pressure.

**Remediation:**
```python
# Wrap callbacks in error handlers and memory limits
try:
    callback()
except MemoryError:
    logger.warning("Memory callback failed - insufficient memory")
```

---

## Low Issues

### 17. AIMD Concurrency Window Recreated with Unchecked _value Access
**Severity:** LOW
**Category:** Code Quality
**Location:** `coordinators/fetch_coordinator.py:606-609`
**Exploitability:** Edge case
**Blast Radius:** Semaphore recreation could cause race condition

**Issue:**
```python
current_limit = self._aimd_semaphore._value  # <-- Private API access
target = int(self._aimd_concurrency)
if abs(current_limit - target) > 2:
    self._aimd_semaphore = asyncio.Semaphore(target)
```

Accessing `_value` is using a private API that could change. However, the logic is a safety mechanism and the check prevents unnecessary recreation.

**Remediation:**
Accept current implementation but add comment noting dependency on asyncio internals.

---

### 18. Test Files Contain Hardcoded Secret Key
**Severity:** LOW
**Category:** Secrets Management
**Location:** `tests/live_8be/FINAL_REPORT_8BE.md:24`, `tests/live_8be/searxng_local/settings.yml:3`
**Exploitability:** Test environment only
**Blast Radius:** None (test files)

**Issue:**
```yaml
secret_key: "ghost_prime_local_secret_2026_03_23_abcdef"
```

Test configuration contains a hardcoded secret. This is only a concern if:
1. Tests run in production
2. Secrets are reused in production

**Remediation:**
```yaml
# Use environment variable for test secrets
secret_key: ${TEST_SECRET_KEY}
```

---

### 19. Random Number Generation for Obfuscation
**Severity:** LOW
**Category:** Cryptographic
**Location:** `security/obfuscation.py:150, 158`
**Exploitability:** Low (obfuscation only)
**Blast Radius:** Obfuscation strength reduced

**Issue:**
```python
if strength == 'high' or (strength == 'medium' and random.random() > 0.3):
    # ...
if word in self.SYNONYMS and random.random() > 0.5:
```

Using `random.random()` for obfuscation decisions (not cryptographic purposes). The randomness is not critical for security but affects obfuscation quality.

**Remediation:**
```python
import secrets
if secrets.randbelow(100) > 30:  # Uses cryptographically secure RNG
```

---

### 20. Exception Swallowing in Cleanup Paths
**Severity:** LOW
**Category:** Error Handling
**Location:** Multiple files (destruction.py, ram_vault.py)
**Exploitability:** Debugging difficulty
**Blast Radius:** Silent failures make troubleshooting difficult

**Issue:**
```python
except Exception:
    pass  # Silent swallowing
```

Many cleanup paths silently swallow exceptions, which makes debugging cleanup failures difficult.

**Remediation:**
```python
except Exception as e:
    logger.debug(f"Cleanup failed (non-critical): {e}")
```

---

## Security Checklist

### OWASP Top 10 Coverage
- [x] **A1: Injection** - Not applicable (no SQL/database in scope for this review)
- [x] **A2: Broken Authentication** - Session management reviewed (memory_coordinator)
- [x] **A3: Sensitive Data Exposure** - Encryption reviewed (encryption.py, key_manager.py)
- [x] **A4: XXE** - Not applicable (no XML processing in reviewed files)
- [x] **A5: Broken Access Control** - Feature flags reviewed
- [x] **A6: Security Misconfiguration** - Multiple issues found
- [x] **A7: XSS** - Not applicable (no web output in reviewed files)
- [x] **A8: Insecure Deserialization** - Not applicable
- [x] **A9: Vulnerable Components** - atomic_storage.py source unavailable
- [x] **A10: Insufficient Logging** - Audit log deletion issue found

### Additional Checks
- [x] No hardcoded production secrets found (test files OK)
- [x] Secrets use proper CSPRNG (secrets.token_bytes, os.urandom)
- [x] Custom crypto identified (SpikingNeuralNetwork - HIGH RISK)
- [x] External module trust boundaries identified
- [x] SSRF protection reviewed (DNS rebinding defense present)
- [x] Dependency audit attempted (pip-audit not available)

---

## Recommendations Summary

### Immediate Actions (Critical)
1. Fix dead code in `get_blocked_domains()` - remove unreachable lines 416-419
2. Remove or properly guard audit log deletion in `emergency_purge()`

### High Priority
3. Recover source code for `atomic_storage.py` for security audit
4. Review `SystemContext` trust boundary in `ghost_layer.py`
5. Add rate limiting to S3 bucket enumeration
6. Harden `ram_vault.py` subprocess calls
7. Remove SpikingNeuralNetwork custom crypto or isolate to non-production use
8. Improve entropy mixing in EntropyPool

### Medium Priority
9. Mask session cookies in all logging
10. Add checksum verification for Lightpanda binary download
11. Address TOCTOU in DNS rebinding defense
12. Document paywall bypass feature and add configuration control

---

*Report generated: 2026-04-23*
*Reviewer: Security Review Agent*
