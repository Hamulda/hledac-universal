**Key Points:**
- Dual strategy architecture: curl_cffi_opportunistic (default, no extra deps) and aioquic_stealth (opt-in QUIC via [http3] extra)
- LRU cache bounded to 512 entries; concurrency capped at 3; memory guard at 5.5 GiB RSS
- Dark web TLDs (.onion, .i2p, .b32.i2p) always excluded from HTTP/3 lane
- Alt-Svc probing with speculative F265B capability, 16 max concurrent probe tasks
- Fail-soft design: any error returns None, caller continues execution

**Structure:**
- Reason, Raw Concept, Narrative (structure, dependencies, highlights, rules, examples), Facts
- 5 explicit rules governing error handling, fallback behavior, dark web skipping, and non-blocking waits

**Notable Patterns/Decisions:**
- Pattern: `^h3[= "']` for Alt-Svc header detection
- Fallback chain: aioquic missing → curl_cffi → error
- Semaphore wait uses non-blocking timeout (2.0s) with immediate continuation
- H3 per-request timeout: 8.0s; probe timeout: 4.0s
- Environment gate: HLEDAC_ENABLE_HTTPX_H3=1 enables both strategies