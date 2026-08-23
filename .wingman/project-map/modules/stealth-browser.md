# Stealth Browser

## Metadata

- **Entry Path:** modules/stealth-browser
- **Status:** current
- **Source:** advanced_web/stealth_browser.py
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** module

## Summary

Headless browser with WebDriver fingerprint elimination and stealth capabilities.

## Source Paths

- `advanced_web/stealth_browser.py`

## Key Features

- WebDriver fingerprint elimination via nodriver
- No `--disable-gpu` (M1 GPU=CPU)
- Same-domain link following
- Structured entity extraction

## Dependencies

- nodriver (headless browser without WebDriver)
- ocrmac (OCR for images, inside recognize())

## Browser Lifecycle

```python
from advanced_web.stealth_browser import StealthBrowser

browser = StealthBrowser()
result = await browser.crawl_url(url, depth=2)
await browser.cleanup()
```

## M1 Constraint

**NEVER add `--disable-gpu`** — on M1 GPU is CPU, disabling slows browser.

## Related Entries

- modules/opsec-coordinator
- modules/fetch-coordinator
