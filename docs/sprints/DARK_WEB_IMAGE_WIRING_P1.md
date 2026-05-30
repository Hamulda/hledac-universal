# DARK_WEB_IMAGE_WIRING_P1.md

## Status: DORMANT — Method Added, NOT Wired to Scheduler

`extract_and_encode_images()` added to `DarkWebCrawler` in `intelligence/dark_web_intelligence.py:477`.
Not wired to `sprint_scheduler.py` — per task constraint for dormant modules.

---

## What Was Changed

### `intelligence/dark_web_intelligence.py`

**Added imports (lines ~52-63):**
- `import os`
- `PIL_AVAILABLE`, `NP_AVAILABLE` optional-dep guards
- `PIL (Pillow)` and `numpy` optional imports at module level

**Added method `DarkWebCrawler.extract_and_encode_images()` (lines 477-596):**
```
async def extract_and_encode_images(
    self,
    html: str,
    page_url: str,
    sprint_id: str,
    fetch_coordinator,
    vision_encoder,
    vector_store,
) -> List[dict]
```

### `runtime/sprint_scheduler.py`
No changes — module is DORMANT, wiring deferred.

---

## Module Analysis

### Q1: Active HTML crawl loop?
YES — `DarkWebCrawler.crawl_onion()` (line 309) is an `AsyncIterator[DarkWebContent]`.
Raw HTML enters via `aiohttp.ClientSession` in `self._fetch_page()` (line 352), then is
parsed by `_parse_content()` (line 420) which builds a `DarkWebContent` object.

**Key flow:**
```
crawl_onion(seed)
  → _fetch_page(url)          # aiohttp GET, JA3 stealth via Tor session
    → _parse_content(url, html)  # BeautifulSoup, extracts text/links/metadata
      → DarkWebContent object   # yielded per page
```

### Q2: CanonicalFinding produced?
YES — `darkweb_content_to_canonical()` (line 700) converts `DarkWebContent → CanonicalFinding`.
Sourcetype: `"onion_discovery"`.

### Q3: HTTP client?
Own `aiohttp.ClientSession` — NOT FetchCoordinator.
- `aiohttp.ClientSession` (line 163) with Tor proxy chain via `aiohttp_socks`.
- `DarkWebCrawler` does NOT use `FetchCoordinator`.

### Q4: Dormant vs Active?
**DORMANT** — `DarkWebCrawler` is NOT called from `sprint_scheduler.py` in normal sprint runs.
It is only called from `_run_onion_discovery_sidecar()` (line 7789 in sprint_scheduler),
which is itself gated by `HLEDAC_ENABLE_TOR=1`. The sidecar is an opt-in advisory sidecar.

---

## Wiring Decision: Why NOT Wired

1. **FetchCoordinator not available in DarkWebCrawler** — The crawler uses its own
   `aiohttp.ClientSession` with Tor proxy. Using FetchCoordinator inside the already
   active onion sidecar would require wiring FetchCoordinator into DarkWebCrawler,
   which is out of scope for this task.

2. **SprintScheduler lacks FetchCoordinator/VisionEncoder refs in onion sidecar** —
   The `_run_onion_discovery_sidecar()` method (line 7789) does not have direct access
   to `self._fetch_coordinator` or `self._vision_encoder`. Wiring image extraction
   would require either: (a) threading these deps into DarkWebCrawler, or (b) duplicating
   the VisionEncoder call in the sidecar after DarkWebCrawler yields content.

3. **Preferred future wiring path:**
   - Option A: Add `extract_and_encode_images()` as a post-processing step in
     `_run_onion_discovery_sidecar()` after `darkweb_content_to_canonical()`, using
     `self._fetch_coordinator` (already imported elsewhere in scheduler) and
     `self._multimodal_enricher._vision_encoder` + `self._multimodal_lmdb_env`.
   - Option B: Keep DarkWebCrawler method dormant, wire into a future
     `ImageOnionDiscoverySidecar` that runs after the HTML crawl sidecar.

---

## Implementation Notes

| Aspect | Detail |
|--------|--------|
| Gate | `HLEDAC_ENABLE_IMAGE_OSINT=1` — checked first, returns `[]` if off |
| Max images/page | 3 (hard cap on candidates list) |
| Max image size | 512KB (checked on body length before PIL open) |
| Timeout per image | 8s via `fetch_coordinator.fetch(url, timeout=8.0)` |
| Parser | BeautifulSoup `html.parser` (as specified in task) |
| Skip rules | data: URIs, `#`-prefixed src, tracking pixels (w<20 && h<20) |
| PIL validation | `Image.open(io.BytesIO(...)).convert("RGB")` — skip if fails |
| VisionEncoder | `encode_batch([image_bytes])` — returns `List[np.ndarray]` |
| Vector store | `add_vectors(ids, vectors, index_type="image")` — table=`image` |
| Fail-soft | Any exception → log warning → continue; never raise |
| Sourcetype in vector metadata | `"onion_discovery"` (matches darkweb_content_to_canonical) |
| Dependencies | PIL, numpy, BeautifulSoup (`bs4`) all optional-guarded |

---

## Next Steps (Future Sprint)

1. Wire into `_run_onion_discovery_sidecar()` by calling `extract_and_encode_images()`
   on the raw HTML after `darkweb_content_to_canonical()` succeeds.
2. Pass `self._fetch_coordinator` and `self._multimodal_enricher._vision_encoder` to it.
3. Add `HLEDAC_ENABLE_IMAGE_OSINT=1` gate alongside `HLEDAC_ENABLE_TOR=1`.
4. Run probe tests for image extraction: verify 3-image cap, 512KB skip, tracking pixel skip.