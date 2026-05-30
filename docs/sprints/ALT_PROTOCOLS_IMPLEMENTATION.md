# Alternative Protocol Stack — F230

## Overview

This implementation provides access to non-indexed content via IPFS, Gopher, Gemini, and I2P protocols.

**Context**: Standard search engines (Google, Bing) cannot index content on these networks, making them valuable OSINT sources for:
- Censorship-resistant datasets
- Academic papers hosted on IPFS
- Historical/niche Gopherspace content
- Privacy-focused Gemini capsules
- I2P eepsites (hidden services)

## Protocol Specifications

### 1. IPFS (InterPlanetary File System)

**Canonical File**: `network/ipfs_client.py`

**Technology**: Content-addressed P2P storage with DHT discovery

**Discovery Methods**:
| Method | Endpoint | Notes |
|--------|----------|-------|
| Local gateway | `localhost:8080` | Requires local IPFS daemon |
| Cloudflare | `cloudflare-ipfs.com` | CDN-backed, fast |
| IPFS.io | `ipfs.io` | Main public gateway |
| IPNS resolution | `/api/v0/name/resolve` | Mutable pointers → CID |
| IPFS Search | `ipfs-search.com/api/v1/search` | Free REST API search |
| Estuary | `api.estuary.tech/public/search` | Pinned content index |

**New Functions**:
```python
resolve_ipns(name: str) -> str | None  # IPNS → CID
fetch_directory_recursive(cid: str, max_depth: int = 3) -> list[dict]
find_via_ipfs_search(query: str) -> list[str]  # CIDs
search_via_estuary(query: str) -> list[str]  # CIDs
ipfs_directory_as_findings(cid, query, max_depth) -> list[CanonicalFinding]
```

**Source Type**: `ipfs_content`, `ipfs_directory`, `ipfs_search`

---

### 2. Gopher Protocol (RFC 1436)

**Canonical File**: `transport/gopher_transport.py` (MERGED — base + crawling)

**Technology**: ASCII-based distributed document retrieval (1991)

**Features** (merged from both implementations):
- Class `GopherTransport` with circuit breaker integration (base)
- Bounded crawling: `crawl_gopherspace()` method (from network/)
- `search_as_findings()` for CanonicalFinding output
- Veronica-2 search via floodgap

**Key Methods**:
```python
class GopherTransport:
    async def fetch(url) -> GopherResponse
    async def search(query) -> GopherResponse  # Veronica-2
    async def crawl_gopherspace(...) -> list[GopherFinding]
    async def search_as_findings(query) -> list[CanonicalFinding]
    def item_to_finding(item) -> dict
```

**Constants**:
- `MAX_CRAWL_HOPS = 5`
- `MAX_CRAWL_ITEMS = 100`
- `VERONICA_HOST = "gopher.floodgap.com"`

**Source Type**: `gopher_content`

---

### 3. Gemini Protocol

**Canonical File**: `network/gemini_transport.py`

**Technology**: Modern privacy-focused protocol (TLS 1.3 required, port 1965)

**Features**:
- Pure TLS via Python `ssl` module
- Bootstrap: `gemini.circumlunar.space`, `kennedy.gemi.dev`
- Search via Kennedy search engine
- Capsule crawling with bounded depth

**Key Functions**:
```python
async def search_geminispace(query) -> list[GeminiFinding]
async def crawl_capsule(url, max_pages=20) -> list[GeminiFinding]
async def geminispace_to_findings(query) -> list[CanonicalFinding]
```

**Source Type**: `gemini_content`

---

### 4. I2P (Invisible Internet Project)

**Canonical File**: `network/i2p_client.py` (MERGED — HTTP + SOCKS5)

**Technology**: Anonymizing network with dual proxy support

**Features** (merged from fetch_coordinator + our implementation):
- HTTP proxy (port 4444) — simple, browser-like
- SOCKS5 proxy (port 7654) — lower-level anonymity
- Health check with 60s TTL cache
- Known eepsites discovery

**Key Functions**:
```python
async def is_i2p_available(proxy_type="http") -> bool  # "http" or "socks5"
async def fetch_eepsite(url, proxy_type="http") -> str | None
async def fetch_eepsite_socks5(url) -> str | None
async def discover_eepsites() -> list[dict]
async def i2p_to_findings(query) -> list[CanonicalFinding]
```

**Source Type**: `i2p_content`

---

## Orchestrator

**File**: `fetching/alternative_protocol_fetcher.py`

**Gating**:
```bash
HLEDAC_ENABLE_ALT_PROTOCOLS=1  # Enable all alt protocols
```

**Constraints**:
- Max 2 concurrent protocol requests (M1 8GB memory)
- Individual timeouts per protocol
- Fail-soft: one protocol failure doesn't block others

**Main Function**:
```python
async def fetch_all_alt_protocols(
    query: str,
    max_concurrent: int = 2,
) -> tuple[list[CanonicalFinding], list[AltProtocolResult]]:
    """Fetch from all protocols in parallel."""
```

---

## Testing

**File**: `tests/test_alt_protocols.py`

```bash
uv run pytest tests/test_alt_protocols.py -v -m "not slow"
```

**Result**: 28/28 tests passed

---

## Invariants

| # | Invariant | Location |
|---|-----------|----------|
| 1 | IPFS: Max file size 10MB | `ipfs_client.MAX_FILE_SIZE_BYTES` |
| 2 | Gopher: Circuit breaker | `transport/gopher_transport` |
| 3 | Gopher: Max crawl hops 5 | `MAX_CRAWL_HOPS` |
| 4 | Gemini: Max response 1MB | `GEMINI_MAX_RESPONSE_SIZE` |
| 5 | I2P: Max response 2MB | `I2P_MAX_SIZE` |
| 6 | All: Max 2 concurrent | `MAX_CONCURRENT_ALT` |
| 7 | Gate via env var | `ALT_PROTOCOLS_ENABLED` |

---

## File Summary

| File | Status | Notes |
|------|--------|-------|
| `network/ipfs_client.py` | Extended | IPNS, Estuary, directory crawl |
| `transport/gopher_transport.py` | Merged base | Class + circuit breaker + crawling |
| `network/gemini_transport.py` | New | TLS 1.3 Gemini protocol |
| `network/i2p_client.py` | Merged base | HTTP (4444) + SOCKS5 (7654) |
| `fetching/alternative_protocol_fetcher.py` | New | Orchestrator |
| `tests/test_alt_protocols.py` | New | 28 tests |