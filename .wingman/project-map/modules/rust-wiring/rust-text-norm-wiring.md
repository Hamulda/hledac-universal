# rust-text-norm-wiring

**Type:** Rust FFI Wiring  
**Path:** `rust_extensions/wiring/text_norm_wiring.py`  
**Status:** current

## Purpose

Rust-native text normalization for IOC extraction. Unicode normalization, whitespace handling, encoding normalization.

## Key Functions

| Function | Purpose |
|----------|---------|
| `normalize_text(text)` | Full normalization pipeline |
| `unicode_normalize(text, form)` | Unicode NFC/NFD/NFKC/NFKD |
| `strip_whitespace(text)` | Whitespace normalization |

## Invariants

- [RTN-1] Default form: NFC (composed)
- [RTN-2] Whitespace: collapse to single space
- [RTN-3] IDNA domain normalization for URLs

## M1 Memory Notes

Inline processing, no allocation for small texts.
