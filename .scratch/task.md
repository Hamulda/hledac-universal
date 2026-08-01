================================================================================
CLAUDE CODE TASK: NEXT-GEN OSINT, AGENTIC SAFETY & FORENSIC AUDIT (READ-ONLY)
================================================================================

PROJEKT: ~/PycharmProjects/Hledac/hledac/universal
TARGET ENVIRONMENT: macOS M1 (8GB UMA RAM), Python 3.14.6 Standard GIL, MLX Engine.

MÓD PROVEDENÍ: STRICT READ-ONLY AUDIT.
NEUPRAVUJ, NEMĚŇ ANI NEVYTVÁŘEJ ŽÁDNÉ KÓDOVÉ SOUBORY. VÝSTUPEM JE POUZE STRUKTUROVANÁ ROADMAPA PRO FÁZI 5 (NEXT-GEN CAPABILITIES).

Základní stabilita (Memory leaks, FFI panics, Async TaskGroups, DNS leaks) JE JIŽ VYŘEŠENA. Nyní se soustřeď výhradně na pokročilou autonomii, evasive OSINT a MLX optimalizace.

--------------------------------------------------------------------------------
1. NOISE FILTER (CO BLESKOVĚ IGNOROVAT)
--------------------------------------------------------------------------------
- IGNORUJ: Linter varování, type-hinty, PEP8, docstringy, chybějící testy, základní memory/async leaky (již opraveno).

--------------------------------------------------------------------------------
2. CÍLOVÉ OBLASTI ANALÝZY (ANGLE 6: THE OFENSIVE & AUTONOMOUS EDGE)
--------------------------------------------------------------------------------

[ÚHEL 6.1: ADVANCED ANTI-BOT EVASION & FINGERPRINTING]
- Zkontroluj `fetching/curl_cffi_fetch.py` a headless browser implementace.
- Hledej statické TLS/JA3 otisky (např. hardcoded `impersonate="chrome120"`). Existuje dynamická rotace prohlížečů, verzí a hlaviček při HTTP 403 / 429?
- Ověř, zda systém umí detekovat Cloudflare Turnstile / DataDome challenge a má strategii pro backoff/bypass (nejen tupý retry).

[ÚHEL 6.2: AGENTIC LOOP SAFETY & TOOL CALLING RECOVERY]
- Zkontroluj `brain/` modul (orchestraci LLM, prompt generation, tool calling).
- Hledej "Agentic Circuit Breakers": Co se stane, když LLM neustále vrací špatně formátovaný JSON nebo volá neexistující tool? Existuje hard-limit (max_iterations) pro LLM reasoning loop?
- Ověř mechanismus "Self-Correction" - dostává LLM při selhání zpětnou vazbu o chybě parseru, aby se opravilo, nebo se jen opakuje stejný prompt?

[ÚHEL 6.3: FORENSIC PROVENANCE & CHAIN OF CUSTODY]
- Zkontroluj `evidence_log.py` a persistence vrstvu.
- Hledej kryptografické ověřování integrity: Jsou jednotlivé záznamy v logu řetězeny pomocí hashů (jako Merkle Tree nebo blockchain-lite struktura), aby bylo možné prokázat, že log nebyl zpětně zmanipulován (Tamper-Evident)?
- Podepisují se stažená OSINT data časovým razítkem a hashem původní URL+odpovědi dříve, než projdou LLM transformací?

[ÚHEL 6.4: DEEP MLX KV-CACHE & INFERENCE OPTIMIZATION]
- Zkontroluj `brain/deephermes3_engine.py` a `utils/mlx_memory/`.
- Hledej pokročilou správu KV Cache pro dlouhé konverzace/sprinty. Používá se "Sliding Window" pro uvolňování starých tokenů z paměti?
- Podporuje systém "Prompt Caching" (ukládání pre-computovaných prefixů systémových promptů), aby se nepočítaly při každém generování znovu?

--------------------------------------------------------------------------------
3. STRATEGIE PROVEDENÍ ANALÝZY PRO CLAUDE CODE
--------------------------------------------------------------------------------
1. Křížově porovnej mechanismy ve `fetching/` s chybovým zpracováním v `coordinators/` (rotují se proxy/profily při waf_block?).
2. Hledej `while` cykly nebo rekurzi v `brain/` orchestrátorech – tam číhají nekonečné agentic smyčky.
3. Analyzuj kryptografickou strukturu `EvidenceEvent`.

--------------------------------------------------------------------------------
4. POŽADOVANÝ STRUKTUROVANÝ VÝSTUP (NEXT-GEN ROADMAP)
--------------------------------------------------------------------------------
Výstup formuluj jako strukturovanou NEXT-GEN ROADMAPU (Phase 5). Každé zjištění MUSÍ mít tento přesný formát:

ISSUE [NEXTGEN]-[ČÍSLO]: [Stručný název problému]
- Soubor a řádky: [přesná cesta:řádky]
- Oblast: [Evasion | Agentic Safety | Forensics | MLX Tuning]
- Závažnost: [🔴 Strategic | 🟡 Tactical | 🟢 Enhancement]
- Root Cause: [Proč současný stav nestačí na produkci v roce 2026]
- Cutting-edge Solution: [Přesné, vizionářské technické řešení aplikovatelné na aktuální architekturu]

Zakonči analýzu sekcí "THE AUTONOMOUS HORIZON", kde shrneš, co chybí k dosažení plné L4 autonomie (kde systém sám píše OSINT reporty bez zásahu člověka i při 90% selhání sítě).
