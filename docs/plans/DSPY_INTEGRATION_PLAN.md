# DSPy Integration — Komplexní Analýza a Plán Oprav

## Současný Stav

### Architektura DSPy v projektu

```
brain/dspy_optimizer.py         # MIPROv2 optimizer (299L) —种子训练
brain/dspy_signatures.py        # DSPy signatures (NEW) — ChainOfThought signatury
brain/synthesis_runner.py      # Synthesis orchestration (1585L) — integrace
brain/hermes3_engine.py        # LLM inference (2500L) — CoT augment (info-only)
```

### DSPY_OPTIMIZATION_MAP.md — tři trained task keys

| Key | Desc |
|-----|------|
| `analysis:medium` | OSINT analysis — entities, gaps, sources, verification |
| `summarization:medium` | Finding summarization — facts, contested, credibility |
| `extraction:medium` | Entity/relation extraction — people, orgs, dates, claims |

---

## Problém 1: `set_custom_prompt()` nikdy nezmění synthesis prompt

**Lokace:** `brain/synthesis_runner.py:445-448` + `brain/synthesis_runner.py`

**Příčina:** `set_custom_prompt(prompt)` nastaví `_custom_synthesis_prompt`, ale metoda `_build_system_prompt()` (line ~500-570) tuto hodnotu **nikdy nečte**. Nikde v `_build_system_prompt` není reference na `_custom_synthesis_prompt`.

**Důsledek:** DSPy optimizer volá `get_prompt('analysis', {'complexity': 'medium'})` → `set_custom_prompt(optimized)` → ale synthesis pořád jede na default system prompt. Optimalizované prompty jsou uloženy do cache, ale nikdy aplikovány.

**Fix:** V `_build_system_prompt()` — pokud `_custom_synthesis_prompt` je nastaven, použít ho jako base system prompt místo default.

---

## Problém 2: ChainOfThought v HermesEngine je čistě informační

**Lokace:** `brain/hermes3_engine.py:2418-2432`

**Příčina:** CoT běží, logged output, ale výsledek (`q_list`) se nikam neukládá a neinjektuje se do dalšího promptu. Padá do standardního Outlines path s původním promptem.

**Důsledek:** DSPy CoT signature validace je "smoke test bez kouře" — běží, nic nemění.

**To je OK** — dokumentace nyní říká "Informational-only, full MIPROv2 → dspy_optimizer → synthesis_runner". Ale znamená to, že HermesEngine CoT block nemá žádný efekt na inference.

**Otázka:** Má CoT block v HermesEngine vůbec smysl, nebo je to dead code?

---

## Problém 3: `optimized.predictors()[0].signature.instructions` — možná DSPy 2.x API změna

**Lokace:** `brain/dspy_optimizer.py:333`

```python
instr = str(optimized.predictors()[0].signature.instructions)
```

**Příčina (HYPOTÉZA — nutno ověřit):** DSPy 2.x možná změnilo API. `optimized.predictors()[0]` nemusí být validní přístup. Zároveň `signature.instructions` možná nefunguje jak expected.

**Ověření:** Spustit `test_dspy_optimizer_mipro_train` a capture exception. Pokud `MIPROv2 failed` log je prázdný → problém je někde jinde. Pokud exception obsahuje `predictors` → API změna potvrzena.

**Poznámka:** Krok 2 je "opravit až po ověření", ne předpokládat že API je broken.

**Důsledek:** MIPROv2 training pravděpodobně padá na Exception, která je zachycena v `except Exception as e: logger.W(f"MIPROv2 failed: {e}")` — optimizer neukládá žádné prompty, cache je prázdná.

**Fix:** Ověřit DSPy 2.x API a opravit přístup k trained predictoru.

---

## Problém 4: M1 8GB RAM — DSPy MIPROv2 je memory-intensive

**Příčina:** MIPROv2 training s velkým trainset a více kol optimalizace spotřebuje více RAM. Na M1 8GB může dojít k OOM nebo silent swap.

**Graf:**guards v `dspy_optimizer.py` kontrolují:
- CPU > 15%
- RAM < 4GB available → skip
- Battery < 80% unplugged → skip
- Thermal HOT/CRITICAL → skip

**Ale:** Guard pro RAM available < 4GB je kontra-intuitivní na 8GB machine — s 8GB fyzické RAM je volných ~2-3GB typicky, takže optimizer by se přeskakoval skoro vždy.

**Fix:** Změnit threshold na < 2GB (ne < 4GB) pro M1 8GB safe, protože 8GB UMA má ~2.5GB pro macOS + orchestrátor + LLM + KV cache = ~6GB reserved, zůstává ~2GB volné.

---

## Problém 5: Fake-green testy — žádné reálné testy pro DSPy integraci

**Příčina:** Žádné probe testy pro `dspy_optimizer`, `dspy_signatures`, ani pro `synthesis_runner` DSPy path. Existují pouze testy pro `hypothesis_engine` (8td, 8va, 8vf, f202g, f191d).

**Fix:** Přidat probe testy pro:
- `test_dspy_optimizer_mipro_train` — spustí MIPROv2 training, capture exception pokud padne
- `test_dspy_optimizer_prompt_retrieval` — ověří že `get_prompt()` vrací správný formát
- `test_synthesis_custom_prompt_applied` — ověří že `_custom_synthesis_prompt` je skutečně použit v `_build_system_prompt`
- `test_dspy_signature_import` — fail-soft když dspy není installed

---

## Plán Oprav

### Krok 1: Opravit `_build_system_prompt()` — použít `_custom_synthesis_prompt`

**Soubor:** `brain/synthesis_runner.py`

V metodě `_build_system_prompt()` přidat early-return logic:
```python
if self._custom_synthesis_prompt:
    return self._custom_synthesis_prompt  # DSPy optimized prompt takes precedence
```

**Ověření:** Přidat probe test `test_synthesis_custom_prompt_applied`

---

### Krok 2: Opravit DSPy 2.x API pro `predictors()[0].signature.instructions`

**Soubor:** `brain/dspy_optimizer.py:333`

Závisí na verzi DSPy — při volání MIPROv2 optimizer logovat výstup a zjistit skutečnou strukturu. Opravit tak aby fungovalo s DSPy 2.x `Optimized` objektem.

**Ověření:** `test_dspy_optimizer_mipro_train` — spustit MIPROv2 training a ověřit že cache obsahuje non-empty prompty.

---

### Krok 3: Opravit RAM threshold pro M1 8GB

**Soubor:** `brain/dspy_optimizer.py` — `_should_skip_optimization()` → `available_memory_mb < 2048` (ne < 4096)

**Proč:** 8GB stroj má ~2GB volných při běžícím systému. < 4GB threshold přeskakuje skoro vždy.

---

### Krok 4: Přidat probe testy pro DSPy integraci

**Soubory:**
- `tests/probe_dspy/test_dspy_optimizer_mipro.py`
- `tests/probe_dspy/test_synthesis_custom_prompt.py`

**Pokrytí:**
- MIPROv2 training nepadá na exception
- `get_prompt()` vrací string
- `_custom_synthesis_prompt` je aplikován v `_build_system_prompt()`
- Fail-soft když DSPy není installed

---

### Krok 5: Rozhodnout o HermesEngine CoT blocku

**Možnost A (Doporučeno):** Odstranit CoT block z `generate_structured_safe` — je to dead code / informational-only. Skutečná hodnota je v `dspy_optimizer` → `synthesis_runner` path.

**Možnost B:** Nechat jako smoke test, ale přejmenovat comment na "# DSPy smoke test — informational only, no effect on output"

---

## Tech Stack / Cutting Edge Metody

| Fáze | Technologie | Benefit |
|------|------------|---------|
| Prompt optimization | DSPy 2.x MIPROv2 | Automatická optimalizace instrukcí přes Bayesian search |
| Inference | MLX + Hermes3 3B | M1 native, lazy eval, <2GB RAM |
| Structured output | Outlines (MLX) + orjson | Garantovaný JSON schema, fallback retry |
| Memory | LMDB zero-copy | Rychlé persistitentní KV storage |
| Context | LLMLingua-2 (optional) | 50-70% compression ratio pro dlouhé contexty |

---

## Časový Odhad

| Krok | Komplexita | Odhad |
|------|-----------|-------|
| 1. Fix `_build_system_prompt` | Nízká | 30 min |
| 2. Fix DSPy 2.x API | Střední | 1-2h (závisí na DSPy verzi) |
| 3. Fix RAM threshold | Nízká | 15 min |
| 4. Přidat probe testy | Střední | 2h |
| 5. Rozhodnout o CoT blocku | Nízká | 15 min |

**Celkem:** ~4-5h práce

---

## Non-Goals (co nedělat)

- Neměnit `dspy_signatures.py` — je správně jako fail-soft stub
- Nepřidávat nové DSPy signatury — stávající DarkQuerySignature a HypothesisSignature stačí
- Nemodifikovat `hermes3_engine.py` CoT block — pouze se rozhodnout jestli ho smazat nebo nechat jako smoke test
- Nepřepisovat celý optimizer — pouze opravit broken seams