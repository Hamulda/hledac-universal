# DEFAULT_DEPENDENCY_THINNING_AUDIT

**Date:** 2026-05-18
**Author:** Claude Code
**Status:** DRAFT — first-safe move TBD

## Context

`pyproject.toml` has a "Lean default: no torch, no chromium, no browser binary" comment but default dependencies still include lane-specific and heavy packages. Goal: move lane-specific deps to optional extras, keeping default capable of core CLI + MLX inference.

---

## Audit Table

| Package | Import name | Callers | Lazy? | Fail-soft? | Current extra | Candidate extra | Verdict |
|---------|-------------|---------|-------|------------|---------------|-----------------|---------|
| `coremltools==8.2` | `coremltools` | `coreml_ane_capability.py:90`, `model_manager.py:460,482`, `ner_engine.py:105`, `captcha_solver.py:28` | ✅ lazy in all | ⚠️ returns None/False on fail | — | `coreml` | **MOVE** — all lazy, fail-soft, lane-specific |
| `pyobjc-framework-coreml>=12.1` | `pyobjc.framework.CoreML` | `coreml_ane_capability.py:193` | ✅ lazy | ⚠️ checked via inspect | — | `coreml` | **MOVE** — platform-specific, only for Apple |
| `transformers>=5.8.0` | `transformers`, `sentence_transformers` | `ane_embedder.py:156,44`, `modernbert_engine.py:44` | ✅ lazy | ❌ no fallback — fails hard if not present | — (transitive of rerank?) | `hf` | **MOVE** — only used by sentence-transformers fallback |
| `flashrank>=0.2.10` | `flashrank.Ranker` | `ane_embedder.py:549`, `synthesis_runner.py:316` | ✅ lazy | ✅ fallback to cosine sim | `rerank` ✅ | `rerank` | KEEP in rerank extra |
| `pytesseract>=0.3.10` | `pytesseract` | `captcha_solver.py:303` | ✅ lazy | ✅ returns None on ImportError | — | `ocr` | **FIRST SAFE MOVE** — already fail-soft |
| `beautifulsoup4>=4.12.0` | `bs4.BeautifulSoup` | `validation_coordinator.py:392` | ✅ lazy | ✅ selectolax fallback | — | `legacy-html` | **MOVE** — selectolax is primary, BS4 fallback only |
| `stem>=1.8.0` | `stem.control`, `stem.Signal` | `tor_manager.py:12`, `stealth_manager.py:1038`, `public_fetcher.py:746` | ✅ lazy in tor_manager | ⚠️ returns False/None on fail | — | `tor` | **MOVE** — tor-specific lane |
| `duckduckgo-search>=8.0.0` | `duckduckgo_search.DDGS` | `duckduckgo_adapter.py:39,221` | ✅ lazy | ✅ ddgs → duckduckgo_search fallback | — | `search` | **MOVE** — search-specific lane |
| `aiohttp-socks>=0.8.0` | `aiohttp_socks` | transport only (httpx SOCKS) | lazy? | ✅ | `transport` ✅ | `transport` | KEEP in transport |
| `dnspython>=2.4.0` | `dns.resolver` | multiple | lazy? | ✅ | — | — | KEEP in default — core networking |
| `selectolax>=0.3.21` | `selectolax` | validation_coordinator.py | lazy? | ✅ | `osint-html` ✅ | `osint-html` | KEEP in osint-html |

---

## Notes on Existing Extras

| Extra | Contents | Notes |
|-------|----------|-------|
| `rerank` | `flashrank>=0.2.0` | flashrank imports `transformers` internally — moving transformers to `hf` extra would break rerank. **rerank must include transformers as transitive or stay default** |
| `transport` | `h2`, `aiohttp-socks` | ✅ already separates tor/socks from default |

---

## Proposed Extra Structure

```toml
# New extras
coreml = ["coremltools==8.2", "pyobjc-framework-coreml>=12.1"]
hf = ["transformers>=5.8.0", "sentence-transformers>=4.0.0"]
ocr = ["pytesseract>=0.3.10"]
legacy-html = ["beautifulsoup4>=4.12.0"]
tor = ["stem>=1.8.0"]
search = ["duckduckgo-search>=8.0.0"]
```

---

## First Safe Move: `pytesseract` → `ocr` extra

**Why pytesseract first:**
- `captcha_solver.py:302-307` already has `try/except ImportError` — fails soft, returns `None`
- `pillow` is used but not declared as dep (likely transitive via other deps)
- No other caller would break if pytesseract is missing
- Captcha solving is an optional lane (off by default)

**Risk:** Low. Fail-soft already implemented.

**Next moves (in order):**
1. `coremltools` + `pyobjc-framework-coreml` → `coreml` extra
2. `duckduckgo-search` → `search` extra
3. `stem` → `tor` extra
4. `beautifulsoup4` → `legacy-html` extra
5. `transformers` → `hf` extra (complex — transitive of rerank)

---

## Verification Commands

```bash
# Default env smoke test
uv run python -c "import hledac.universal; print('default import OK')"

# After first move
uv lock
uv lock --check
```