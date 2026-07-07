# Issue #11: Rust Extensions Batch I/O pro PyO3 Calls

## Analýza a Implementace

### ✅ Změny Implementované

#### 1. Oprava `core/rust_backend/ioc.py`

**`_PythonIocDomain.batch_extract_iocs_simd`** (původně vracelo `[[] for _ in texts]`):
```python
@staticmethod
def batch_extract_iocs_simd(texts: list[str]) -> list[list[tuple[str, str]]]:
    """Batch extraction — uses serial Python extraction per text."""
    return [_python_extract_iocs_flat(t) for t in texts]
```

**`_PythonIocDomain.batch_extract_iocs_simd_indexed`** (původně vracelo `[]`):
```python
@staticmethod
def batch_extract_iocs_simd_indexed(
    texts: list[str]
) -> list[tuple[int, str, str]]:
    """Batch extraction with index — uses serial Python extraction per text."""
    result: list[tuple[int, str, str]] = []
    for idx, t in enumerate(texts):
        for ioc_type, value in _python_extract_iocs_flat(t):
            result.append((idx, value, ioc_type))
    return result
```

#### 2. Oprava `core/rust_backend.py` (Rust path)

**`_RustIocDomain.batch_extract_iocs_simd_indexed`** fallback:
```python
except Exception:
    # SIMD batch not registered — fall back to batch_ioc_extract_unified
    # Uses rayon parallel processing with single GIL acquire/release (not per-item)
    batch_results = self._ext.batch_ioc_extract_unified(texts)
    result: list[tuple[int, str, str]] = []
    for idx, iocs in enumerate(batch_results):
        for value, ioc_type in iocs:
            result.append((idx, value, ioc_type))
    return result
```

### ❌ Nenalezené Problémy

Původní Issue #11 uváděl, že `#[pyfunction]` volá `Python::with_gil()` per-call. Toto je **standardní PyO3 chování** - každé volání Rust funkce z Pythonu vyžaduje GIL acquire/release.

**Klíčový poznatek:** Většina batch API už existuje:
- `batch_classify` v url_ops.rs ✓
- `batch_ioc_extract_unified` v ioc_extract_fast.rs ✓
- `batch_blake3_64` v content_hasher.rs ✓
- `bloom_check_batch` v bloom.rs ✓

### ❌ Chybějící Batch API

| Funkce | Status | Poznámka |
|--------|--------|----------|
| `bloom_add_batch` | **Chybí** | Pouze `bloom_check_batch` existuje. `bloom.add()` je volán per-item v Pythonu. |

### Ověření

```bash
# Test batch_extract_iocs_simd
batch_extract_iocs_simd:
  text[0]: [('ipv4s', '1.2.3.4')]
  text[1]: [('domains', 'example.com'), ('emails', 'user@example.com')]
  text[2]: [('urls', 'https://example.com'), ('domains', 'example.com')]

# Test batch_extract_iocs_simd_indexed
batch_extract_iocs_simd_indexed:
  (0, '1.2.3.4', 'ipv4s')
  (1, 'example.com', 'domains')
  (1, 'user@example.com', 'emails')
```

### Předexistující Problém (nesouvisí s Issue #11)

Test `test_extract_iocs` v `tests/test_rust_backend.py` selhává:
- **Příčina:** Python fallback vrací `'ipv4s'` (plural) ale test očekává `'ipv4'` (singular)
- **Toto je existující bug v `core/rust_backend/ioc.py`** - key normalization je nedokonalá

---

*Issue #11 Analysis - 2026-07-07*
