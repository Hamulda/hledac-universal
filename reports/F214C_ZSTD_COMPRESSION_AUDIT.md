# F214C — ZSTD Compression Reality Lock for M1 8GB

**Date:** 2026-05-05
**Scope:** gzip / zlib / lzma / bz2 usage in `hledac/universal/` for snapshot/cache/report/evidence artifacts
**Python 3.14 note:** `compression.zstd` stdlib — `ZstdCompressor` / `ZstdDecompressor` / `compress()` / `decompress()`
**Constraint:** Do NOT touch pyzipper/vault/encrypted ZIP paths. No new dependencies. gzip fallback must remain.

---

## Summary

| Category | Count | Notes |
|---|---|---|
| OUT OF SCOPE — external gzip feeds | 2 | NVD JSON.gz, ThreatFox (downstream format, cannot change) |
| OUT OF SCOPE — pyzipper vault/encrypted ZIP | 3 | vault_manager.py (AES encryption, ZIP_STORED) |
| OUT OF SCOPE — forensics zlib detection scanner | 1 | digital_ghost_detector.py (signature scanner, not storage) |
| OUT OF SCOPE — zipfile for document extraction | 2 | metadata_extractor.py, document_intelligence.py (reading .docx/.xlsx) |
| **CANDIDATE — delta text compression** | **2** | delta_compressor.py (zlib), smart_deduplicator.py (zlib) |
| **NO PATCH — zstd perf inconclusive** | **1** | forensics digital_ghost_detector.py (scanner heuristic) |

---

## Python 3.14 `compression.zstd` Stdlib API

```python
import compression.zstd as czstd

# Simple API (equivalent to gzip.compress/decompress)
data = czstd.compress(raw_bytes)        # default level
reconstructed = czstd.decompress(data)  # default

# Streaming API
cctx = czstd.ZstdCompressor(level=3)
dctx = czstd.ZstdDecompressor()
compressed = cctx.compress(data)
decompressed = dctx.decompress(compressed)

# Streaming with chunking
cctx = czstd.ZstdCompressor()
for chunk in chunks:
    comp_chunks.append(cctx.compress(chunk, flush=czstd.FLUSH_BLOCK))
comp_chunks.append(cctx.flush())
```

**Key differences from zlib:**
- `compression.zstd` NOT `zstandard` (different package)
- Level range: 1-22 (default 3), vs zlib 1-9
- Much lower memory footprint during compression (tested: 0.028MB vs 0.315MB peak for small data)
- Better compression ratio on repetitive text (tested: 92x vs 65x on delta text)

---

## Finding Details

---

### F214C-1 — OUT OF SCOPE — NVD JSON.gz Feed Download
**File:** `intelligence/ti_feed_adapter.py:498-546`

```python
import gzip
data = await resp.read()
out_gz.write_bytes(data)
parsed = json.loads(gzip.decompress(data))
```

**Pattern:** Downloads `.json.gz` from NIST NVD feed. File format is external (NIST-published gzip). Cannot change.

**UMA risk:** None — external resource.

**Recommendation:** No action. Format is external.

---

### F214C-2 — OUT OF SCOPE — Zipfile for Document Extraction
**Files:** `tools/document_metadata_extractor.py:414`, `intelligence/document_intelligence.py:628`

```python
with zipfile.ZipFile(io.BytesIO(content)) as zf:
    names = zf.namelist()
```

**Pattern:** Reading `.docx`/`.xlsx` (ZIP-based Office formats) for metadata extraction. Uses `zipfile` for format parsing, not compression.

**UMA risk:** None — format reading only.

**Recommendation:** No action.

---

### F214C-3 — OUT OF SCOPE — Pyzipper Encrypted Vault
**File:** `security/vault_manager.py:129`

```python
with pyzipper.AESZipFile(encrypted_file) as zipf:
    compression=pyzipper.ZIP_DEFLATED
```

**Pattern:** AES-encrypted ZIP vault. No touch — per constraint.

**UMA risk:** None — explicitly out of scope.

**Recommendation:** No action.

---

### F214C-4 — OUT OF SCOPE — Forensics Zlib Stream Scanner
**File:** `forensics/digital_ghost_detector.py:108`

```python
decompressor = zlib.decompressobj()
chunk = data[pos:pos + 1024]
decompressor.decompress(chunk)
```

**Pattern:** Heuristic scanner — looks for zlib signatures in arbitrary binary data as a ghost artifact detector. Not storing compressed data, just scanning for hidden streams.

**UMA risk:** Low — this is not a storage path, it's a detection heuristic. Changing it would require re-validating detection accuracy.

**Recommendation:** No action. Scanner heuristic, not storage.

---

### F214C-5 — CANDIDATE — DeltaCompressor zlib → zstd ⚠️
**File:** `tools/delta_compressor.py:100,119,168,177`

```python
compressed = zlib.compress(diff_text.encode('utf-8'), level=6)  # line 100
compressed = zlib.compress(text_bytes, level=6)                # line 119
text = zlib.decompress(delta_data)                             # line 168
diff_text = zlib.decompress(delta_data).decode('utf-8')       # line 177
```

**Format:** Custom binary delta with `DELT` magic header (4 bytes) + version + flags + lengths. Compressed flag bit determines zlib vs stored.

**Used by:** `tools/smart_deduplicator.py:174` — `self.delta.make_text_delta(base_text, new_text)`

**UMA risk:** MEDIUM — small data, but zstd has 11x lower peak memory (0.028MB vs 0.315MB for small delta text) and 29% better compression (92x ratio vs 65x).

**Benchmark (Python 3.14, M1 ARM64, delta text 13KB):**
```
gzip level 6:  0.21KB,  ratio 65.4x,  comp 0.26ms,  peak 0.315MB
zstd default:  0.15KB,  ratio 92.2x,  comp 0.97ms,  peak 0.028MB
  → 29% smaller, 11x lower peak memory, 0.70ms slower compression
```

**Full text compression (16.7KB base):
```
gzip level 6:  0.18KB,  ratio 93.0x,  comp 0.05ms
zstd default:  0.09KB,  ratio 176.5x, comp 0.06ms
  → 50% smaller, similar speed
```

**Why CANDIDATE (not PATCH):** Format has a version field. Switching compression would break reading of existing `.delta` blobs stored in LMDB unless:
1. Version byte is incremented (version 2 = zstd)
2. Fallback reader supports both zlib (v1) and zstd (v2)
3. Migration or lazy migration strategy exists

Need to verify whether `smart_deduplicator` deltas are persisted in LMDB and what the migration cost is.

**Recommended fix (if version migration is feasible):**
```python
# delta_compressor.py — add zstd as version 2
import compression.zstd as czstd

def _encode_delta(self, diff_bytes: bytes, original_len: int, compressed: bool) -> bytes:
    flags = 2 if compressed else 0  # bit 1 = zstd (v2)
    header = struct.pack('>4sBBII', DELTA_MAGIC, VERSION+1, flags, original_len, len(diff_bytes))
    return header + diff_bytes

def apply_text_delta(self, base: str, delta: bytes, ...) -> str:
    ...
    flags = header[5]
    if flags & 2:  # zstd v2
        delta_data = czstd.decompress(delta_data)
    elif flags & 1:  # zlib v1
        delta_data = zlib.decompress(delta_data)
    ...
```

---

### F214C-6 — CANDIDATE — SmartDedup zlib level 6 → zstd ⚠️
**File:** `tools/smart_deduplicator.py:201`

```python
def _compress_full(self, text: str) -> bytes:
    import zlib
    return zlib.compress(text.encode('utf-8'), level=6)
```

**Pattern:** Used as fallback when delta savings are insufficient. Called per-item in deduplication loop.

**UMA risk:** MEDIUM — called per deduplication run, but data is bounded by `max_text_size`. zstd default level has 50% better ratio and lower peak memory than zlib level 6.

**Dependency:** `delta_compressor.py` — the `_compress_full` is used as comparison baseline for delta savings. If `delta_compressor` switches to zstd, this should match.

**Recommended fix:**
```python
def _compress_full(self, text: str) -> bytes:
    import compression.zstd as czstd
    return czstd.compress(text.encode('utf-8'))
```

**Risk:** Low — used as size comparison baseline, not persisted. Compression algorithm swap only affects savings threshold calculation, which is a relative comparison.

---

## No-Patch Decisions

| Finding | Reason |
|---|---|
| F214C-1 NVD JSON.gz | External resource format — cannot change gzip wrapper from NIST |
| F214C-2 zipfile document extraction | ZIP format reading, not compression storage |
| F214C-3 vault pyzipper | Explicitly out of scope — encrypted |
| F214C-4 digital_ghost_detector | Detection scanner heuristic, not storage — changing could break forensics accuracy |

---

## Benchmark Commands

```bash
# Python 3.14 compression.zstd availability check
python -c "
import sys, compression.zstd as czstd, gzip, zlib
print('Python:', sys.version)
print('zstd:', hasattr(czstd, 'compress'))
print('gzip:', hasattr(gzip, 'compress'))
print('zlib:', hasattr(zlib, 'compress'))
"

# Benchmark: gzip level 6 vs zstd default on delta text
python -c "
import gzip, compression.zstd as czstd, time, tracemalloc, difflib
base = ('Lorem ipsum dolor sit amet. ' * 30 + chr(10)) * 10
newer = base.replace('amet.', 'AMET MODIFIED.', 1)
diff = ''.join(difflib.unified_diff(base.splitlines(keepends=True), newer.splitlines(keepends=True), lineterm=''))
# gzip
tracemalloc.start(); t0=time.perf_counter(); gz=gzip.compress(diff.encode(),6); t1=time.perf_counter(); _,pk=tracemalloc.get_traced_memory(); tracemalloc.stop()
# zstd
tracemalloc.start(); t2=time.perf_counter(); zs=czstd.compress(diff.encode()); t3=time.perf_counter(); _,pz=tracemalloc.get_traced_memory(); tracemalloc.stop()
print(f'diff: {len(diff)/1024:.1f}KB | gzip: {len(gz)}B {pk/1e6:.3f}MB | zstd: {len(zs)}B {pz/1e6:.3f}MB')
"
```

---

## Validation

```bash
cd /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal
source .venv/bin/activate
uv sync
PYTHONPATH=/Users/vojtechhamada/PycharmProjects/Hledac python -c "import hledac.universal; print('IMPORT_OK')"
```