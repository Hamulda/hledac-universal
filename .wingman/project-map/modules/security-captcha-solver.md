# security-captcha-solver

**Type:** Security Layer  
**Path:** `security/captcha_solver.py`  
**Status:** current

## Purpose

CAPTCHA solving via multiple backends: 2Captcha, Anti-Captcha, or ML-based solver.

## Key Functions

| Function | Purpose |
|----------|---------|
| `CaptchaSolver` | Main class |
| `solve_reCaptcha(site_key, url)` | Solve reCAPTCHA |
| `solve_hCaptcha(site_key, url)` | Solve hCaptcha |
| `solve_image_captcha(image)` | Solve image CAPTCHA |

## Invariants

- [SCS-1] API key from environment: `CAPTCHA_API_KEY`
- [SCS-2] Timeout: 120s per solve
- [SCS-3] Fallback: mark as unsolvable if all backends fail

## Dependencies

- `2captcha-python` or `anticaptchaofficial`
