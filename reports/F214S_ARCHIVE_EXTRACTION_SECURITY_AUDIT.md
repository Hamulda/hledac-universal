# F214S — Archive Extraction Security Audit

**Python runtime:** uv-managed CPython 3.14.4
**Audited:** 2026-05-05
**Scope:** tarfile, zipfile, shutil.unpack_archive, pyzipper, document ZIP parsing, forensics unpacking

---

## Summary Table

| ID | File | Line(s) | Archive | Operation | To Disk? | User Input? | Risk | Decision |
|----|------|---------|---------|-----------|----------|-------------|------|----------|
| F214S-1 | `forensics/metadata_extractor.py` | 1778 | ZIP | `ZipFile` + `namelist()`/`infolist()` | No | No | None | PASS |
| F214S-2 | `forensics/metadata_extractor.py` | 1830 | TAR | `tarfile.open()` + `getmembers()` | No | No | None | PASS |
| F214S-3 | `intelligence/document_intelligence.py` | 628 | ZIP | `ZipFile(io.BytesIO(content))` + `read()` | No | No | Low | PASS |
| F214S-4 | `tools/document_metadata_extractor.py` | 414 | ZIP | `ZipFile(io.BytesIO(content))` + `namelist()` | No | No | Low | PASS |
| F214S-5 | `legacy/persistent_layer.py` | 3034, 3060 | ZIP | `ZipFile(path)` + `namelist()` | No | No | Low | PASS |
| F214S-6 | `security/vault_manager.py` | 309 | ZIP | `zipfile.ZipFile` + `extractall()` | **Yes** | **Indirect** | **Medium** | **PATCH** |
| F214S-7 | `security/vault_manager.py` | 326 | ZIP | `pyzipper.AESZipFile` + `extractall()` | **Yes** | **Indirect** | **Low** | **PATCH** |
| F214S-8 | `tests/test_autonomous_orchestrator.py` | 7721, 7781 | ZIP | `ZipFile` + `extractall()` (test) | Yes | No (test) | None | N/A |

**Overall: 2 patches needed (F214S-6, F214S-7), 6 PASS**

---

## Detailed Findings

---

### F214S-1 — `forensics/metadata_extractor.py:1778`

**Archive type:** ZIP (metadata read)
**Operation:** `zipfile.ZipFile(file_path, "r") as zf:` + `zf.namelist()` + `zf.infolist()`
**Read-only inspection vs extraction:** Read-only metadata inspection
**User-controlled input?** NO — `file_path` is a local artifact path
**Path traversal risk:** NONE — only `namelist()` and `infolist()` are called; no `read()` or `extract()`
**Zip-slip risk:** NONE
**Symlink/hardlink risk:** NONE — no extraction
**Current safety guard:** N/A (read-only)
**Recommended action:** None

```
with zipfile.ZipFile(file_path, "r") as zf:
    metadata.num_files = len(zf.namelist())
    metadata.comment = zf.comment.decode("utf-8", errors="ignore") if zf.comment else None
    for info in zf.infolist():
        total_uncompressed += info.file_size
        total_compressed += info.compress_size
        files.append({"name": info.filename, ...})
```

**Verdict: PASS** — Only reads archive metadata, no extraction.

---

### F214S-2 — `forensics/metadata_extractor.py:1830`

**Archive type:** TAR (metadata read)
**Operation:** `tarfile.open(file_path, "r:*") as tf:` + `tf.getmembers()`
**Read-only inspection vs extraction:** Read-only metadata inspection
**User-controlled input?** NO — `file_path` is a local artifact path
**Path traversal risk:** NONE — only `getmembers()` enumeration; no `extract()`
**Zip-slip risk:** N/A (no extraction)
**Symlink/hardlink risk:** NONE — no extraction; `member.name` read-only
**Current safety guard:** N/A (read-only)
**Recommended action:** None

```
with tarfile.open(file_path, "r:*") as tf:
    members = tf.getmembers()
    metadata.num_files = len(members)
    for member in members:
        total_size += member.size
        files.append({"name": member.name, "size": member.size, ...})
```

**Verdict: PASS** — Only reads TAR member list, no extraction.

---

### F214S-3 — `intelligence/document_intelligence.py:628`

**Archive type:** ZIP (in-memory, OOXML parsing)
**Operation:** `zipfile.ZipFile(io.BytesIO(content)) as z:` + `z.namelist()` + `z.read()`
**Read-only inspection vs extraction:** Read-only; parses in memory via `BytesIO`
**User-controlled input?** YES — `content: bytes` parameter; `url: str` for extension detection
**Path traversal risk:** NONE — BytesIO wrapper, no filesystem write
**Zip-slip risk:** NONE — no extraction
**Symlink/hardlink risk:** NONE — no extraction
**Current safety guard:** In-memory BytesIO prevents filesystem writes
**Recommended action:** None

```
with zipfile.ZipFile(io.BytesIO(content)) as z:
    metadata = self._extract_ooxml_core_props(z, content)
    if "word/comments.xml" in z.namelist():
        comments_xml = z.read("word/comments.xml").decode("utf-8", errors="ignore")
    for name in z.namelist():
        if name.startswith("word/media/"):
            data = z.read(name)  # Still in-memory BytesIO
```

**Verdict: PASS (low risk)** — In-memory ZIP reading for document metadata. `.docx`/`.xlsx` reading preserved. No filesystem extraction.

---

### F214S-4 — `tools/document_metadata_extractor.py:414`

**Archive type:** ZIP (in-memory, macro detection)
**Operation:** `zipfile.ZipFile(io.BytesIO(content)) as zf:` + `zf.namelist()`
**Read-only inspection vs extraction:** Read-only; checks for VBA macro presence
**User-controlled input?** YES — `content: bytes` from external document fetch
**Path traversal risk:** NONE — BytesIO wrapper, no filesystem write
**Zip-slip risk:** NONE — only `namelist()` scan
**Recommended action:** None

```
with zipfile.ZipFile(io.BytesIO(content)) as zf:
    names = zf.namelist()
    return any('vbaProject.bin' in n for n in names)
```

**Verdict: PASS (low risk)** — In-memory ZIP namelist scan. `.docx`/`.xlsx` reading preserved.

---

### F214S-5 — `legacy/persistent_layer.py:3060, 3127, 3151, 3198, 3209`

**Archive type:** ZIP (WACZ validation, read-only)
**Operation:** `zipfile.ZipFile(path, "r") as zf:` + `zf.namelist()` only
**Read-only inspection vs extraction:** Read-only WACZ structure validation
**User-controlled input?** NO — file path from local archive processing
**Path traversal risk:** NONE — only `namelist()` scan; validation only
**Zip-slip risk:** NONE — no extraction
**Recommended action:** None

```
def _validate_wacz_structure(self, zf: 'zipfile.ZipFile'):
    namelist = zf.namelist()
    if "datapackage.json" not in namelist: ...
    for name in zf.namelist(): ...
```

**Verdict: PASS (low risk)** — Read-only WACZ validation via namelist scan.

---

### F214S-6 — `security/vault_manager.py:309`  ← **PATCH**

**Archive type:** ZIP (Fernet-decrypted vault export)
**Operation:** `zipfile.ZipFile(temp_path, 'r') as zipf:` + `zipf.extractall(extract_path)`
**Read-only inspection vs extraction:** **EXTRACTION TO DISK**
**User-controlled input?:** INDIRECT — `output_dir` parameter to `decrypt_export()` is caller-controlled; the archive bytes are Fernet-decrypted before extraction
**Path traversal risk: YES** — A malicious ZIP entry could contain `../../../../etc/cron.d/payload` and escape `extract_path = output_dir / "decrypted_vault"`
**Zip-slip risk: YES** — `extractall()` resolves `..` components; Python 3.14 still resolves traversal
**Symlink/hardlink risk:** Tar-specific, not applicable here
**Current safety guard:** Archive is from authenticated Fernet vault; threat model assumes non-malicious vault content
**Recommended action:** Add path-traversal guard in `_decrypt_fernet` before `extractall()`. Use membership validation loop before extraction, OR switch to per-entry `extract(member, path)` with explicit `SafeZipPath` guard.

**Specific vulnerable code:**
```python
def _decrypt_fernet(self, encrypted_data: bytes, password: str, output_path: Path) -> Optional[str]:
    # ...
    extract_path = output_path / "decrypted_vault"
    extract_path.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(delete=False, suffix='.zip', dir=_get_tempdir()) as tmp:
        temp_path = Path(tmp.name)
    temp_path.write_bytes(decrypted)
    with zipfile.ZipFile(temp_path, 'r') as zipf:
        zipf.extractall(extract_path)  # ← VULNERABLE
    os.unlink(temp_path)
    return str(extract_path)
```

**Why this is MEDIUM (not HIGH):** The archive source is `decrypt_export()` — the user provides their own encrypted vault file. The attack scenario requires: (1) an attacker can modify or replace the encrypted vault file, AND (2) the victim decrypts it with their own password. This is a degraded-trust scenario within an already-authenticated export flow. However, since extraction IS to disk with a user-controlled `output_dir`, zip-slip CAN write outside the intended `decrypted_vault` subdirectory.

**Patch:** Add pre-extraction zip-slip guard in `_decrypt_fernet`:

```python
def _safe_extractall(self, zf: zipfile.ZipFile, extract_to: Path) -> None:
    """Extract ZIP with zip-slip protection."""
    extract_to = extract_to.resolve()
    for member in zf.namelist():
        member_path = (extract_to / member).resolve()
        if not member_path.is_relative_to(extract_to):
            raise zipfile.BadZipFile(f"Path traversal attempt: {member}")
    zf.extractall(extract_to)
```

**Probe test target:** `tests/test_vault_manager.py` — add `test_zip_slip_protection` after PATCH-7 is applied.

---

### F214S-7 — `security/vault_manager.py:326`  ← **PATCH**

**Archive type:** ZIP (pyzipper AES-decrypted vault export)
**Operation:** `pyzipper.AESZipFile(encrypted_file) as zipf:` + `zipf.extractall(extract_path)`
**Read-only inspection vs extraction:** **EXTRACTION TO DISK**
**User-controlled input?:** INDIRECT — `output_dir` parameter to `decrypt_export()` is caller-controlled; archive is from encrypted vault
**Path traversal risk: YES** — Same zip-slip scenario as F214S-6
**Zip-slip risk: YES** — `extractall()` on AES-encrypted ZIP from authenticated vault
**Symlink/hardlink risk:** N/A for ZIP
**Current safety guard:** Archive is from AES-encrypted vault (password-protected); threat model assumes non-malicious vault content
**Recommended action:** Same pre-extraction path guard as F214S-6. Apply to `_decrypt_pyzipper` as well.

**Specific vulnerable code:**
```python
def _decrypt_pyzipper(self, encrypted_file: Path, password: str, output_path: Path) -> Optional[str]:
    extract_path = output_path / "decrypted_vault"
    extract_path.mkdir(exist_ok=True)
    with pyzipper.AESZipFile(encrypted_file) as zipf:
        zipf.setpassword(password.encode())
        zipf.extractall(extract_path)  # ← VULNERABLE
    return str(extract_path)
```

**Why this is LOW (not MEDIUM):** pyzipper requires correct password to decrypt. Attacker must either: (1) know the vault password AND modify the encrypted file, or (2) trick user into decrypting attacker-created archive. Within the authenticated vault model, this is low-risk but the code pattern matches F214S-6 so both should be hardened together.

**Patch:** Same `_safe_extractall` helper; call it from `_decrypt_pyzipper` too.

**IMPORTANT — DO NOT BROADLY REFACTOR pyzipper:** pyzipper is used here ONLY for vault decryption (trusted source). Do NOT touch `WZ_AES` encryption semantics, `WZ_AES` file creation, or other pyzipper paths. Only add the zip-slip guard at the `extractall()` call site.

---

### F214S-8 — `tests/test_autonomous_orchestrator.py:7721, 7781`

**Archive type:** ZIP (WACZ in tests)
**Operation:** `zipfile.ZipFile(str(wacz_path), 'r') as zf:` + `zf.extractall(extract_dir)`
**Read-only inspection vs extraction:** Test-only extraction to temp dir
**User-controlled input?** NO — test creates its own WACZ archives
**Risk:** None — test artifacts only
**Recommended action:** None

**Verdict: N/A (test-only)**

---

## Constraints Respected

- **No broad refactor** — targeted patches only at `vault_manager.py` extractall sites
- **pyzipper vault encryption semantics unchanged** — only extraction is patched; WZ_AES creation/reading is untouched
- **No document parser changes** — no proof of extraction bugs in document parsers
- **`.docx`/`.xlsx` reading preserved** — BytesIO + zipfile.read() path untouched
- **Package layout unchanged**
- **Python 3.14 compatibility** — `filter='data'` for tarfile noted but tarfile extraction is read-only in this codebase

---

## Python 3.14 Note on `tarfile.extractall()`

Python 3.12+ added `filter=` parameter to `tarfile.extractall()`:

```python
tar.extractall(path, filter='data')  # Python 3.12+ — blocks absolute/unsafe paths
```

This codebase uses `tarfile.open()` + `getmembers()` for read-only metadata only (F214S-2). No `tarfile.extractall()` call exists that writes to disk in the active codebase. The report at `reports/PY314_ADVANCEMENTS_AUDIT.md:738` references this for documentation purposes only.

---

## Validation Command

```bash
cd /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal
source .venv/bin/activate
uv sync
PYTHONPATH=/Users/vojtechhamada/PycharmProjects/Hledac python -c "import hledac.universal; print('IMPORT_OK')"
```

---

## Acceptance

- [x] Complete list of archive handling locations
- [x] PASS / PATCH / NO_PATCH decisions per finding
- [x] No broad refactors
- [x] 2 patches identified (F214S-6, F214S-7), both in `vault_manager.py` with `_safe_extractall` helper
- [x] `.docx`/`.xlsx` reading preserved (BytesIO path)
- [x] pyzipper encryption semantics untouched
- [ ] Probe test for zip-slip path traversal (deferred until PATCH-7 is applied)
