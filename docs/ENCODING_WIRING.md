# F261 — Encoding Wiring: `decode_response_bytes` do fetch pipeline

**Datum:** 2026-06-03
**Sprint:** F261 (Storage Fix follow-up)
**Status:** ✅ Hotovo — 14/14 nových testů PASS, 0 regrese v `probe_storage_fixes.py`

---

## Kontext

`utils/encoding.py` (STORAGE-FIX-4) byl vytvořen v dřívějším sprintu, ale zůstal
**importovaný jen v testech** (`tests/probe_storage_fixes.py`). Produkční fetch
pipeline stále používal starší `_try_decode()` (UTF-8 → windows-1252 → latin-1 →
UTF-8 replace) bez charset hint z HTTP headeru, bez `charset_normalizer` /
`chardet` řetězce.

Důsledek: ne-UTF-8 OSINT zdroje (Windows-1252, KOI8-R, GB2312…) produkovaly
U+FFFD replacement chars ("â€™" typu) v `payload_text` → DuckDB kontaminace.

Audit follow-up: zapojit `decode_response_bytes` do všech tří fetch cest
s fail-soft fallbackem na `_try_decode`.

---

## Cíle

1. ✅ Wiring v `fetching/public_fetcher.py` (2 místa: aiohttp path + curl_cffi escalation)
2. ✅ Wiring v `fetching/alternative_protocol_fetcher.py` (fediverse + matrix)
3. ✅ Wiring v `transport/curl_cffi_fetch.py` (charset hint + decode helper)
4. ✅ Test: latin-1 mock → žádné U+FFFD v `payload_text`
5. ✅ Fail-soft: jakýkoliv výjimka → fallback na `_try_decode` (původní chování)
6. ✅ Invarianty: bounded, hermetický, M1-safe (žádné těžké ML)

---

## Architektura

### Decode chain (`utils/encoding.py`)

```
HTTP response body (bytes)
    │
    ▼
0. http_charset hint (Content-Type → charset=xxx)        [pokud k dispozici]
    │
    ▼
1. charset_normalizer.from_bytes() — best accuracy        [transitive dep]
    │
    ▼
2. chardet.detect() — legacy fallback
    │
    ▼
3. UTF-8 strict
    │
    ▼
4. UTF-8 with surrogateescape
    │
    ▼
5. latin-1 (always succeeds)                              [last resort]
```

- **Bounded:** 5 MB cap (`_MAX_DECODE_BYTES`), candidate attempts capped
- **Fail-soft:** nikdy nevyhazuje výjimku, vždy vrací `str`
- **M1-safe:** pure-Python, žádné ML modely, žádný streaming

### Wiring přehled

| Soubor | Call site | http_charset zdroj | Fallback |
|--------|-----------|---------------------|----------|
| `fetching/public_fetcher.py` L2468 | curl_cffi escalation path | `_esc_result["content_type"]` | `_try_decode(_esc_bytes)` |
| `fetching/public_fetcher.py` L2639 | hlavní aiohttp body | `resp.headers["Content-Type"]` | `_try_decode(body_bytes)` |
| `fetching/alternative_protocol_fetcher.py` `_fetch_from_fediverse` | `status["content"]` | (žádný — str pass-through) | raw `content` |
| `fetching/alternative_protocol_fetcher.py` `_fetch_from_matrix` | `msg["content"]["body"]` | (žádný — str pass-through) | raw `content` |
| `transport/curl_cffi_fetch.py` `fetch_via_curl_cffi` | result dict: `http_charset_hint` | `response.headers["content-type"]` | n/a (jen hint) |
| `transport/curl_cffi_fetch.py` `decode_curl_cffi_result` | standalone helper | `result["http_charset_hint"]` | `None` (no content) |

### Helper functions (nové)

| Funkce | Místo | Účel |
|--------|-------|------|
| `parse_charset_from_content_type(ct)` | `utils/encoding.py` | Extrahuje `charset=xxx` z Content-Type headeru |
| `_try_decode_with_charset(body, *, http_charset, max_bytes)` | `public_fetcher.py` | Wrapper: chain + fail-soft na `_try_decode` |
| `decode_curl_cffi_result(result, *, max_bytes)` | `curl_cffi_fetch.py` | Standalone helper pro curl_cffi dicts |

---

## Implementace

### `utils/encoding.py` — `parse_charset_from_content_type()`

```python
def parse_charset_from_content_type(content_type: str | None) -> str | None:
    """F261: Extract charset= value from a Content-Type header."""
    if not content_type or not isinstance(content_type, str):
        return None
    try:
        for part in content_type.split(";"):
            token = part.strip()
            if token.lower().startswith("charset="):
                value = token[len("charset="):].strip().strip('"').strip("'")
                return value or None
    except Exception:
        return None
    return None
```

### `public_fetcher.py` — `_try_decode_with_charset()`

```python
def _try_decode_with_charset(
    body: bytes,
    *,
    http_charset: str | None = None,
    max_bytes: int = 5 * 1024 * 1024,
) -> tuple[str, bool, int]:
    """STORAGE-FIX-4 wiring: charset_normalizer chain with fail-soft fallback."""
    try:
        text = decode_response_bytes(
            body,
            http_charset=http_charset,
            max_bytes=max_bytes,
        )
        replacement_count = text.count("�")
        return (text, replacement_count > 0, replacement_count)
    except Exception as e:
        logger.debug("decode_response_bytes failed, falling back to _try_decode: %s", e)
        return _try_decode(body)
```

### `curl_cffi_fetch.py` — `http_charset_hint` + `decode_curl_cffi_result()`

```python
# In fetch_via_curl_cffi:
http_charset_hint = parse_charset_from_content_type(content_type)
return {
    ...,
    "http_charset_hint": http_charset_hint,  # F261
    ...,
}

# Standalone helper:
def decode_curl_cffi_result(result: dict, *, max_bytes: int = 5 * 1024 * 1024) -> str | None:
    """Decode raw bytes from a curl_cffi result dict to str. Never raises."""
    if not isinstance(result, dict) or not result.get("success"):
        return None
    content = result.get("content", b"")
    if not content:
        return None
    try:
        return decode_response_bytes(
            content, http_charset=result.get("http_charset_hint"), max_bytes=max_bytes
        )
    except Exception as e:
        logger.debug("decode_curl_cffi_result failed (fail-soft): %s", e)
        return None
```

---

## Test pokrytí

`tests/probe_encoding_wiring.py` — 14 testů:

| # | Test | Cíl |
|---|------|-----|
| 1 | `parse_charset_basic` | 9 edge cases Content-Type parsování |
| 2 | `decode_response_bytes_latin1_no_replacement` | Windows-1252 → žádné U+FFFD |
| 3 | `decode_response_bytes_no_hint_chain` | UTF-8 roundtrip bez hintu |
| 4 | `decode_response_bytes_str_passthrough` | str / empty / None → no exception |
| 5 | `decode_response_bytes_truncation` | 6 MB → 100 B cap respected |
| 6 | `try_decode_with_charset_clean_utf8` | Public_fetcher helper, UTF-8 |
| 7 | `try_decode_with_charset_latin1_no_replacement` | Public_fetcher helper, latin-1 |
| 8 | `try_decode_with_charset_fallback_on_exception` | Monkey-patch → fallback na `_try_decode` |
| 9 | `decode_curl_cffi_result_latin1` | curl_cffi dict → decoded str |
| 10 | `decode_curl_cffi_result_error_dict` | error / empty / wrong-type → None |
| 11 | `end_to_end_latin1_payload_text` | E2E: latin-1 → `payload_text` bez mojibake |
| 12 | `static_wiring_in_public_fetcher` | ≥ 2 call sites `_try_decode_with_charset` |
| 13 | `static_wiring_in_curl_cffi_fetch` | `http_charset_hint` + decoder present |
| 14 | `static_wiring_in_alternative_protocol_fetcher` | ≥ 2 call sites `decode_response_bytes` |

### Výsledky

```
tests/probe_encoding_wiring.py::test_f261_parse_charset_basic ............................. PASSED
tests/probe_encoding_wiring.py::test_f261_decode_response_bytes_latin1_no_replacement .... PASSED
tests/probe_encoding_wiring.py::test_f261_decode_response_bytes_no_hint_chain ............ PASSED
tests/probe_encoding_wiring.py::test_f261_decode_response_bytes_str_passthrough .......... PASSED
tests/probe_encoding_wiring.py::test_f261_decode_response_bytes_truncation ............... PASSED
tests/probe_encoding_wiring.py::test_f261_try_decode_with_charset_clean_utf8 ............. PASSED
tests/probe_encoding_wiring.py::test_f261_try_decode_with_charset_latin1_no_replacement .. PASSED
tests/probe_encoding_wiring.py::test_f261_try_decode_with_charset_fallback_on_exception .. PASSED
tests/probe_encoding_wiring.py::test_f261_decode_curl_cffi_result_latin1 ................. PASSED
tests/probe_encoding_wiring.py::test_f261_decode_curl_cffi_result_error_dict ............. PASSED
tests/probe_encoding_wiring.py::test_f261_end_to_end_latin1_payload_text ................. PASSED
tests/probe_encoding_wiring.py::test_f261_static_wiring_in_public_fetcher ................ PASSED
tests/probe_encoding_wiring.py::test_f261_static_wiring_in_curl_cffi_fetch .............. PASSED
tests/probe_encoding_wiring.py::test_f261_static_wiring_in_alternative_protocol_fetcher . PASSED
============================ 14 passed in 1.37s ============================
```

### Regrese check

```
uv run pytest tests/probe_storage_fixes.py tests/probe_encoding_wiring.py -q
27 passed, 2 warnings in 2.16s
```

Původních 13 `probe_storage_fixes` testů stále PASS + 14 nových.

---

## Invarianty

| Invariant | Jak ověřen |
|-----------|-----------|
| **Fail-soft** — žádný výjimka nesmí crashnout pipeline | `try/except` na všech 4 call sites + monkey-patch test |
| **Bounded** — 5 MB cap, kandidáti capped | `decode_response_bytes` interně; `test_f261_decode_response_bytes_truncation` |
| **Hermetic** — žádná síť, žádný MLX, žádný model load | Všechny testy in-process, mockované bytes |
| **M1-safe** — pure Python, žádné těžké ML | `decode_response_bytes` chain: charset_normalizer / chardet / stdlib only |
| **Backward compat** — původní `_try_decode` API zachováno | `decode_response_bytes` přijímá `str` → pass-through; `_try_decode` fallback |

---

## Dopad

### Před (původní stav)

```
HTTP response (Windows-1252)
  → aiohttp body bytes
  → _try_decode (UTF-8 strict → windows-1252 → latin-1 → UTF-8 replace)
  → payload_text: "Itâ€™s a test â€œquotedâ€�"  ← MOJIBAKE
```

### Po (F261 wiring)

```
HTTP response (Windows-1252)
  → aiohttp body bytes
  → parse_charset_from_content_type("text/html; charset=windows-1252") → "windows-1252"
  → _try_decode_with_charset(body, http_charset="windows-1252")
  → decode_response_bytes(body, http_charset="windows-1252")
  → chain: 0) http_charset → raw_b.decode("windows-1252", errors="strict") ✓
  → payload_text: "It's a test "quoted""  ← CLEAN
```

### Fail-soft scénáře

| Vstup | Co se stane | Fallback |
|-------|-------------|----------|
| `decode_response_bytes` import selže | `decode_response_bytes = None` | `content = raw_content` (str pass-through) |
| `decode_response_bytes` vyhodí | `except Exception` | `_try_decode(body)` |
| Neplatný charset hint | LookupError → přeskočí | chain step 1-5 |
| Binární content (obrázek) | `latin-1` 1:1 mapping | vrátí str s high chars, necrashne |
| 6 MB response | truncated to `max_bytes` | posledních N bytes decoded |
| Prázdný response | `if not raw_b: return ""` | `text = ""` |

---

## Co se NEZMĚNILO

- `_try_decode()` v `public_fetcher.py` — ponechán jako fallback (dědictví)
- `FetchResult` dataclass — beze změn
- ContentLayer / JS rendering path — beze změn (text je už dekódovaný)
- `CanonicalFinding` API — beze změn
- Test signature v `probe_storage_fixes.py` — beze změn

---

## Soubory změněny

| Soubor | Změna | Řádky |
|--------|-------|-------|
| `utils/encoding.py` | + `parse_charset_from_content_type()` | +26 |
| `fetching/public_fetcher.py` | + import, + `_try_decode_with_charset()`, 2× wiring | +35 / -3 |
| `fetching/alternative_protocol_fetcher.py` | + import, 2× wiring (fediverse, matrix) | +24 |
| `transport/curl_cffi_fetch.py` | + import, + `decode_curl_cffi_result()`, + `http_charset_hint` v result dictu | +40 / -1 |
| `tests/probe_encoding_wiring.py` | + 14 testů (nový soubor) | +287 |

**Net changes:** ~340 LOC přidaných, ~4 LOC odebraných.

---

## Budoucí práce (mimo scope)

- Adaptéry `discovery/fediverse_adapter.py` a `discovery/matrix_adapter.py` mají
  vlastní HTTP request cestu — tam by se dekódování mělo dít co nejdříve.
  Aktuálně adaptéry vrací `dict` (parsovaný JSON), takže `decode_response_bytes`
  na `alt_protocol_fetcher` straně funguje jen jako safety net.
- `pages_rendered.html` z JS rendererů (Camoufox, nodriver) — tamtéž latence
  by mohla těžit z `decode_response_bytes` pokud server vrací non-UTF-8 HTML.
  Aktuálně se vrací `str` z `page.content()` (webkit) — bez wireingu.
- `httpx` v transport fallback path — podobný pattern jako curl_cffi.

Tyto jsou **mimo scope F261** a mohou být follow-up sprinty.
