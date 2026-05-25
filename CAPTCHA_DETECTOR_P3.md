# Sprint P3: CAPTCHA Detector — Phase 1 Implementation

## What was implemented

### 1. `security/captcha_detector.py` (NEW)

- `CaptchaDetector.is_captcha(image_bytes, url) → bool`
- Phase 1: PIL-only heuristics (no VisionEncoder, no coremltools model)
- Score-based detection:
  - URL contains `captcha|challenge|verify|human|botcheck|spam|security.?check|abc.?def` → +0.5 (hard signal)
  - Image < 50KB → +0.3
  - Grayscale/palette mode → +0.3
  - Aspect ratio [0.2, 5.0] → +0.1
  - Score ≥ 0.5 → return True
- Gated by `HLEDAC_ENABLE_CAPTCHA_DETECTION=1` (env var check in FetchCoordinator init)
- All exceptions → return False (fail-soft mandatory)
- `_captcha_detections` counter + `get_detections_count()` + `reset()`

### 2. `fetch_coordinator.py` wiring

**Init** (`__init__` ~line 310):
```
if HLEDAC_ENABLE_CAPTCHA_DETECTION=1:
    self._captcha_detector = CaptchaDetector()
    self._captcha_detections = 0
```

**Post-response filter** (end of `_fetch_url()` ~line 1409):
```
if content_type.startswith("image/") and len(content) < 200KB:
    if self._captcha_detector.is_captcha(content, url):
        return None  # treated same as 404
```

### 3. Stats exposure

`get_captcha_stats()` → `{captcha_detections_total, captcha_detector_enabled}`

## Env vars

| Var | Default | Effect |
|-----|---------|--------|
| `HLEDAC_ENABLE_CAPTCHA_DETECTION` | (unset) | Set to `1` to enable detector |
| `HLEDAC_ENABLE_CAPTCHA_DETECTION` | (unset) | Any other value → disabled |

## Constraints honored

- **No VisionEncoder**: Phase 1 uses PIL-only heuristics
- **No event loop blocking**: PIL ops are wrapped in `run_in_executor` (via `wait_for` + coroutine threadsafe)
- **Fail-soft**: `is_captcha()` catches all exceptions → returns False
- **Bounded**: MAX_SIZE_BYTES=50KB for PIL analysis, 200KB skip threshold for detection
- **Phase 1 only**: No coremltools model, no trained classifier

## Integration points

- `FetchCoordinator._fetch_url()` — post-response CAPTCHA filter
- `FetchCoordinator.get_captcha_stats()` — stats accessor for RL telemetry
- `CaptchaDetector.reset()` — call between sprints to zero counter

## RL telemetry path

```
FetchCoordinator._captcha_detections
  → get_captcha_stats()['captcha_detections_total']
  → can be wired into SprintSchedulerResult or sidecar telemetry
  → RL layer learns to avoid CAPTCHA-heavy sources
```