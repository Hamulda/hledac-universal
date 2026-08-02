### ISSUE-P8-006 — HIGH — No Apple Vision Framework OCR: ANE is embedder-only

**File:** `recon/document_intelligence.py` — grep `VNRecognizeTextRequest|Vision.*Framework` → **0 matches**

**Root Cause:**

`document_intelligence.py` uses:
- **PyMuPDF** (`fitz`): PDF text extraction, image extraction
- **PIL/Pillow** + `ExifTags`: EXIF/GPS metadata
- **python-docx**, **openpyxl**: Office document parsing
- **No Vision Framework**: no `VNRecognizeTextRequest` for hardware-accelerated OCR

`brain/ane_embedder.py` has a sophisticated ANE integration:

```python
# ane_embedder.py:548-614 — ANEEmbedder 3-level fallback
async def load(self):
    # Level 1: MLX ModernBERT (PRIMARY) — ANE via MLX Metal backend ✓
    # Level 2: CoreML MLModel (legacy) — PyObjC → MLModel.predict ✓
    # Level 3: hash fallback ✓
```

This is for **embeddings**, not OCR. Vision Framework's `VNRecognizeTextRequest` is a separate subsystem providing:
- Hardware-accelerated OCR via ANE (zero CPU, zero GPU bandwidth)
- Layout analysis via `VNDetectDocumentSegmentationRequest`
- Barcode/qr detection via `VNDetectBarcodesRequest`

**Impact on M1 8GB / ANE:**
- Scanned PDFs (image-based) → PyMuPDF extracts nothing useful
- Screenshots, photos of documents → not processed via Vision ANE
- ANE is underutilized: it processes MLX embeddings but not OCR

**Cutting-edge Solution:**

```python
# In recon/document_intelligence.py — add Vision Framework OCR
import Vision
import AppKit

async def _ane_ocr(image_path: str | bytes) -> str | None:
    """
    Hardware-accelerated OCR via Apple Vision Framework on ANE.
    Zero CPU usage, zero GPU bandwidth — uses dedicated Neural Engine.
    """
    try:
        if isinstance(image_path, str):
            img = AppKit.NSImage.alloc().initWithContentsOfFile_(image_path)
        else:
            img = AppKit.NSImage.alloc().initWithData_(image_path)

        if not img:
            return None
        cg_img = img.CGImageForProposedRect_context_hints_(None, None)

        result = await asyncio.to_thread(_vision_sync_ocr, cg_img)
        return result
    except Exception:
        return None

def _vision_sync_ocr(cg_img) -> str | None:
    """Synchronous Vision OCR (runs in thread pool to avoid blocking)."""
    handler = _OCRResultHandler.alloc().init()
    request = Vision.VNRecognizeTextRequest.alloc().initWithCompletionHandler_(handler)
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setRecognitionLanguages_(["en-US", "cs-CZ", "de-DE", "fr-FR"])
    request.setUsesLanguageCorrection_(True)

    Vision.VNImageRequestHandler.alloc().initWithCGImageOptions_(
        cg_img, {"VNImageOptionApplyOrientationCorrection": True}
    ).performRequests_error_([request], None)

    texts = [str(obs.revision()) for obs in request.results()]
    return "\n".join(texts)
```

---

### ISSUE-P8-007 — MEDIUM — Fragmented fingerprint extraction: no SSH host key, no MMH3 favicon hash, no GA/GTM

**Files:**
- `recon/passive_fingerprint.py` — JA4 TLS fingerprints ✓
- `recon/cryptographic_intelligence.py` — certificate SHA-256/SHA-1 fingerprints ✓
- `recon/attribution_scorer.py:170-200` — PGP key correlation ✓
- `recon/exposure_correlator.py` — JARM infrastructure clustering ✓

**Missing fingerprints:**

1. **SSH host key fingerprint**: No SSHFP DNS record querying, no `known_hosts` parsing, no SSH server host key extraction. **Impact:** Same SSH host key = same physical server = strong infrastructure pivot independent of domain/IP.

2. **Favicon MMH3 hash**: `passive_fingerprint.py:296-297` extracts the favicon URL but **does not compute the MMH3 hash** of the favicon image. **Impact:** `faviconhash.com` and Shodan cluster sites by favicon similarity — same hash = shared hosting/ownership.

3. **GA/GTM tracking ID extraction**: grep `UA-\d+|GTM-` → **0 matches**. Google Analytics and Tag Manager IDs are strong cross-site tracking identifiers.

**Cutting-edge Solution:**

```python
# In recon/passive_fingerprint.py — add MMH3 favicon hash
import mmh3

async def _extract_favicon_mmh3(
    html: str,
    page_url: str,
    session: httpx.AsyncClient,
) -> str | None:
    """Compute MMH3 hash of favicon for cross-site clustering."""
    favicon_url = _extract_favicon_url(html, page_url)
    if not favicon_url:
        return None
    try:
        resp = await session.get(favicon_url, timeout=5.0)
        h = mmh3.hash(resp.content, signed=False)
        return f"mh3:{h}"
    except Exception:
        return None

# Add GA/GTM extraction
_GA_PATTERN = re.compile(r'UA-\d{6,}-\d+|G-[A-Z0-9]{10,}')
_GTM_PATTERN = re.compile(r'GTM-[A-Z0-9]{4,}')

def _extract_tracking_ids(html: str) -> dict[str, list[str]]:
    return {
        'ga_ids': _GA_PATTERN.findall(html),
        'gtm_ids': _GTM_PATTERN.findall(html),
    }

# In recon/cryptographic_intelligence.py — add SSHFP DNS query
async def _query_sshfp(domain: str) -> list[dict]:
    """Query SSHFP DNS records (RFC 4255) for SSH host key fingerprints."""
    import dns.resolver
    try:
        answers = dns.resolver.resolve(domain, 'SSHFP')
        return [
            {
                'algorithm': rdata.algorithm,
                'fingerprint_type': rdata.fp_type,
                'fingerprint': rdata.fingerprint.hex(),
            }
            for rdata in answers
        ]
    except (dns.resolver.NXDOMAIN, Exception):
        return []
```

---

## AREA 4: TECHNICAL PIVOTING & CROSS-LINKING ENGINE

### ISSUE-P8-008 — HIGH — No centralized PivotEngine: cross-domain linking is ad-hoc

**Files:**
- `recon/identity_stitching.py` — LSH + simhash for social identity
- `recon/exposure_correlator.py` — JARM clustering for infrastructure
- `recon/attribution_scorer.py` — PGP fingerprint correlation
- `recon/cryptographic_intelligence.py` — certificate analysis

**Root Cause:**

Each subsystem has its own pivot logic but there is **no unified PivotEngine**:

```
identity_stitching.py  → pivots on: username, email, LSH simhash (social)
exposure_correlator.py → pivots on: JARM hash, cert fingerprint, DNS (infra)
attribution_scorer.py  → pivots on: PGP key, social profile, bio link (attribution)
```

The `suggested_pivots` field in `CanonicalFinding` is per-finding, not cross-finding. There is no engine that:
1. Collects all technical identifiers from all findings in a sprint
2. Builds a cross-index: identifier → [(sprint_id, finding_id)]
3. Identifies shared identifiers across unrelated queries
4. Triggers automatic pivot discovery

**Impact on M1 8GB / research capability:**

Automatic pivoting is the highest-value OSINT operation:
- Same SSH host key across 3 domains = same physical server
- Same PGP fingerprint in two leak sites = same operator
- Same Bitcoin address in two dark web forums = same entity
- Same JA4 TLS fingerprint = same CDN/proxy provider

Currently: analyst must manually notice and query. An automatic PivotEngine surfaces these automatically.

**Cutting-edge Solution:**

```python
# In recon/pivot_engine.py — centralized cross-domain pivot engine
@dataclass
class PivotMatch:
    finding_id: str
    sprint_id: str
    source_type: str
    id_type: str

class PivotEngine:
    """
    Automatic pivot discovery across all technical identifier types.
    Builds a reverse index: identifier → [(sprint_id, finding_id, signal_type)]
    """
    def __init__(self, duckdb_path: Path):
        self._conn = duckdb.connect(str(duckdb_path), read_only=True)
        self._index: dict[str, list[PivotMatch]] = {}  # Lazy build

    async def build_index(self, sprint_ids: list[str]) -> None:
        """Build pivot index from canonical_findings table."""
        rows = self._conn.execute("""
            SELECT finding_id, sprint_id, source_type, payload_text
            FROM canonical_findings
            WHERE sprint_id = ANY($1)
        """, [sprint_ids]).fetchall()

        for finding_id, sprint_id, source_type, payload_text in rows:
            identifiers = (
                _extract_pgp_fingerprints(payload_text) +
                _extract_ssh_fingerprints(payload_text) +
                _extract_cert_fingerprints(payload_text) +
                _extract_crypto_addresses(payload_text) +
                _extract_jarm_hashes(payload_text)
            )
            for id_type, identifier in identifiers:
                self._index.setdefault(identifier, []).append(
                    PivotMatch(finding_id=finding_id, sprint_id=sprint_id,
                               source_type=source_type, id_type=id_type)
                )

    def find_shared_identifiers(self) -> list[PivotFinding]:
        """Return identifiers shared across multiple sprints/queries."""
        pivots = []
        for identifier, matches in self._index.items():
            unique_sprints = {m.sprint_id for m in matches}
            if len(unique_sprints) > 1:
                pivots.append(PivotFinding(
                    identifier=identifier,
                    id_type=matches[0].id_type,
                    matches=matches,
                    confidence=min(1.0, len(matches) * 0.3),
                    summary=f"Shared {matches[0].id_type} across {len(unique_sprints)} sprints"
                ))
        return pivots
```

---

### ISSUE-P8-009 — MEDIUM — Graph traverse cache: sophisticated but isolated from query engine

**File:** `rust_extensions/src/graph_traverse/cache.rs:458-470` — grep callers → **0 matches**

**Root Cause:**

`cache.rs` provides a thread-local LRU cache with mmap persistence and LZ4 compression:

```
Thread-local LRU(50k) → mmap persistence → LZ4 compression
Max: 50,000 entries, 100MB, M1 8GB safe
Cache key: (root_value, max_hops) → Vec<TraversalResult>
```

The implementation is sophisticated (fail-soft, lazy init, drop flush, `catch_unwind` for FFI safety). However, `get_cached_traversal()` at `cache.rs:458-470` has **zero Python call sites** — the graph query path in `knowledge/` doesn't route through it.

**Impact:** The Rust graph traverse cache is an unused asset. If activated, repeated graph queries within the same sprint would be dramatically faster (LRU hit = zero DB query).

**Cutting-edge Solution:**

```python
# In knowledge/graph_service.py — wire Rust graph traverse cache
from hledac_rust_extensions.graph_traverse import get_cached_traversal

async def traverse_graph(root: str, max_hops: int = 3) -> list[CanonicalFinding]:
    # Check Rust LRU cache first
    cached = get_cached_traversal(
        db_path=str(self._duckdb_path),
        root_value=root,
        max_hops=max_hops,
        cache_dir=Path(self._cache_dir),
    )
    if cached:
        return cached

    # Compute and cache
    results = await self._traverse_sync(root, max_hops)
    return results
```

---

## CROSS-AREA OBSERVATIONS

### ARCH-P8-001: Archive infrastructure is excellent — only WARC raw response storage missing
WaybackCDX, WaybackDiffMiner, CommonCrawl (URL-only), Archive.ph, IPFS, GitHub Historical — all production-grade with bounded concurrency and circuit breakers. The only gap is raw HTTP response persistence (WARC format).

### ARCH-P8-002: ANE is underutilized for OCR
MLX ModernBERT uses ANE for embeddings. Vision Framework (`VNRecognizeTextRequest`) is not integrated. ANE OCR would be zero-CPU, zero-GPU-bandwidth — ideal for M1 8GB.

### ARCH-P8-003: Graph traverse cache is an unused asset
`cache.rs` is sophisticated but has zero Python call sites.

### ARCH-P8-004: Pivot capabilities are excellent but fragmented
Each subsystem (identity, infrastructure, attribution) has its own pivot logic. A centralized PivotEngine would unify them and enable automatic cross-domain discovery.

---

## PRIORITIZED ROADMAP — PHASE 8

### TIER 1 — HIGH IMPACT

| # | Area | Issue | File:Line | Risk |
|---|------|-------|-----------|------|
| **ISSUE-P8-001** | Archive | No WARC format: raw HTTP responses not archived | `evidence_log.py` (absent) | Forensic evidence lost, no offline replay |
| **ISSUE-P8-006** | ANE OCR | No Vision Framework OCR: ANE is embedder-only | `document_intelligence.py` (absent) | Scanned PDFs/images not OCR'd on ANE |
| **ISSUE-P8-008** | Pivot | No centralized PivotEngine: cross-domain linking ad-hoc | `identity_stitching`, `exposure_correlator`, `attribution_scorer` | Automatic pivot discovery not possible |

### TIER 2 — MEDIUM IMPACT

| # | Area | Issue | File:Line |
|---|------|-------|-----------|
| **ISSUE-P8-002** | Archive | CommonCrawl: URL discovery only, no WAT/WARC content fetch | `commoncrawl_adapter.py:27-28` |
| **ISSUE-P8-003** | Archive | WaybackCDX: MAX_CDX_RESULTS=500 truncates historical records | `wayback_cdx.py:41` |
| **ISSUE-P8-007** | Metadata | No SSH host key / MMH3 favicon / GA-GTM ID extraction | `passive_fingerprint.py` (absent) |
| **ISSUE-P8-009** | Graph | Graph traverse cache: sophisticated but isolated from query engine | `cache.rs:458-470` (zero callers) |

### TIER 3 — LOW IMPACT

| # | Area | Issue | File:Line |
|---|------|-------|-----------|
| **ISSUE-P8-004** | Protocol | MAX_CONCURRENT_ALT=2 hardcoded bottleneck | `alternative_protocol_fetcher.py:29` |
| **ISSUE-P8-005** | Protocol | IPFS: gateway-only, no native CID/IPNS DHT resolution | `alternative_protocol_fetcher.py:74-106` |

---

## APPENDIX: Verified References

| Issue | File | Lines | Status |
|-------|------|-------|--------|
| P8-001 | evidence_log.py | grep warc/WARC → 0 matches | ABSENT |
| P8-002 | recon/commoncrawl_adapter.py | 27-28, 34-46 | URL discovery only ✓, content fetch absent |
| P8-002 | recon/wayback_cdx.py | 1-296 | CDX well-implemented ✓ |
| P8-002 | recon/wayback_diff_miner.py | 1-438 | Diff miner well-implemented ✓ |
| P8-003 | recon/wayback_cdx.py | 41, 114-167 | MAX_CDX_RESULTS=500, no pagination |
| P8-004 | fetching/alternative_protocol_fetcher.py | 29, 241-273 | MAX_CONCURRENT_ALT=2 hardcoded |
| P8-005 | fetching/alternative_protocol_fetcher.py | 74-106 | IPFS gateway-only |
| P8-006 | recon/document_intelligence.py | grep VNRecognizeTextRequest → 0 matches | Vision OCR absent |
| P8-006 | brain/ane_embedder.py | 548-614 | ANE for embeddings ✓, not OCR |
| P8-007 | recon/passive_fingerprint.py | 296-297 | Favicon URL extracted, MMH3 not computed |
| P8-007 | recon/cryptographic_intelligence.py | 670-699 | Cert SHA-256/SHA-1 ✓ |
| P8-007 | recon/attribution_scorer.py | 170-200 | PGP correlation ✓ |
| P8-007 | recon/ | grep ssh_host\|mmh3\|GA_ID → 0 matches | SSH, MMH3, GA absent |
| P8-008 | recon/identity_stitching.py | 1-1140 | LSH/simhash pivots ✓, not centralized |
| P8-008 | recon/exposure_correlator.py | 1-582 | JARM clustering ✓, not centralized |
| P8-008 | recon/attribution_scorer.py | 1-500 | PGP correlation ✓, not centralized |
| P8-009 | rust_extensions/src/graph_traverse/cache.rs | 458-470 | get_cached_traversal: 0 callers |

---

**PHASE 8 AUDIT COMPLETE**  
Scope: recon/ (archive, metadata, fingerprints), brain/ (ANE), rust_extensions/ (graph), fetching/ (protocols)  
Key finding: Archive infrastructure is production-grade (CDX, Wayback, CommonCrawl all well-implemented). ANE is embedder-only (Vision OCR absent). Graph traverse cache is an unused asset. No centralized PivotEngine. MAX_CONCURRENT_ALT=2 is a hardcoded bottleneck.
