# PEP 750: t-strings — Kompletní Analýza pro Hledac Universal

**Datum:** 2026-07-10
**Projekt:** Hledac Universal OSINT Orchestrator
**Python verze:** 3.14.6 (requires-python = ">=3.14,<3.15")
**Status:** ANALYSIS COMPLETE — NO MIGRATION NEEDED

---

## 1. PEP 750 — Stav implementace v Python 3.14

### 1.1 Dostupnost

| Feature | Status | Detail |
|---------|--------|--------|
| `t"..."` literal syntax | ✅ AKTIVNÍ | Parsuje se jako AST `TemplateStr` node |
| `string.templatelib.Template` | ✅ AKTIVNÍ | Plně funkční |
| `string.templatelib.Interpolation` | ✅ AKTIVNÍ | Plně funkční |
| `from __future__ import t_strings` | ❌ NEPOTŘEBA | Bez flagu funguje |
| `t_strings` v `sys.flags` | `None` | Flag neexistuje, ale syntax funguje |

### 1.2 Jak t-strings fungují

```python
from __future__ import annotations  # pro type hints

query = "ransomware"
prompt = t"Research query: {query}"

# Výsledek je Template objekt:
# Template(
#   strings=('Research query: ', ''),
#   interpolations=(Interpolation('ransomware', 'query', None, ''),),
#   values=('ransomware',)
# )
```

---

## 2. KLÍČOVÝ NÁLEZ: t-strings jsou COMPILE-TIME

**CRITICAL:** t-strings vyhodnocují hodnoty interpolací v **compile time**, ne runtime!

```python
query = "ransomware"
tpl = t"Query: {query}"

# .values obsahuje ('ransomware',) — HODNOTA ZAPEČENÁ při kompilaci!
# Změna query = "malware" NEMÁ na tpl žádný vliv!
query = "malware"
print(tpl.values)  # stále ('ransomware',)
```

**DŮSLEDEK:** t-strings jsou **NEPOUŽITELNÉ pro dynamické MLX prompty** kde se `query`, `context` atd. mění runtime.

### 2.1 Skutečné Use Cases v Hledac

| Use Case | Vhodnost | Implementace |
|----------|----------|-------------|
| Prompt metadata registry | ✅ PERFEKTNÍ | `t_inspect()` — analýza bez spuštění |
| Security auditování promptů | ✅ PERFEKTNÍ | `t_find_suspicious()` — detekce dangerous patterns |
| Statická analýza promptů | ✅ PERFEKTNÍ | `t_variables()`, `t_analyze()` |
| MLX dynamické prompty | ❌ NEVHODNÉ | f-strings zůstávají |
| Logování promptů | ⚠️ OMEZENÉ | Zaznamená hodnoty z compile-time |

---

## 3. Analýza MLX Prompt Patternů v Hledac

### 3.1 Sprint Entry Point (`runtime/sprint_entrypoint.py`)

**f-string usage:** Primárně logovací a status zprávy — **NENÍ vhodné pro migraci**.

### 3.2 NER Engine (`brain/ner_engine.py`)

```python
# Line 374 — MLX OUTLINES prompt
prompt = f"Extract named entities from text:\n{text[:2000]}"
```
**❌ NEMIGROVAT** — dynamický runtime `text` parameter.

### 3.3 DeepHermes3 Engine (`brain/deephermes3_engine.py`)

```python
# Line 3946 — Decision making prompt
prompt = f"""Research query: {query}
Step: {step}/{max_steps}

History:
{history_str}

What should be the next action?"""
```
**❌ NEMIGROVAT** — všechny proměnné jsou runtime.

```python
# Line 4424 — JSON schema prompt (CRITICAL SECURITY)
json_prompt = f"""{prompt}

Respond ONLY with valid JSON matching this schema:
{schema_str}

Do not include any other text. Output valid JSON only."""
```
**❌ NEMIGROVAT** — dynamický prompt injection point.

### 3.4 Ostatní MLX prompty

| Soubor | Line | Typ | Důvod nemigrovat |
|--------|------|-----|-------------------|
| `brain/deephermes3_engine.py` | 3946, 4040, 4312, 4424 | MLX prompts | Runtime proměnné |
| `brain/ner_engine.py` | 374 | MLX NER | Runtime text |
| `brain/model_manager.py` | 1148 | MLX Report | Runtime data |
| `brain/model_lifecycle.py` | 942 | MLX ChatML | Runtime system_prompt |
| `brain/synthesis_runner.py` | 1463 | MLX IOC | Runtime prompt |
| `brain/moe_router.py` | 786 | MLX Synthesis | Runtime query |
| `brain/mlx_kv_cache_share.py` | 211 | Cache key | Runtime system_msg |
| `brain/research_hypothesis_engine.py` | 964 | Hypothesis | Runtime query + context |

---

## 4. Helper Utility — `utils/t_string_helpers.py`

**VYTVOŘENO** (2026-07-10)

```python
from string.templatelib import Interpolation, Template

def t_analyze(tpl: Template) -> dict[str, str]:
    """Analýza bez spuštění — metadata o prompt šabloně."""

def t_inspect(tpl: Template) -> dict[str, object]:
    """Kompletní metadata: variable_count, static_parts, variables, has_format_specs."""

def t_variables(tpl: Template) -> list[str]:
    """Seznam proměnných v promptu."""

def t_has_variable(tpl: Template, name: str) -> bool:
    """Kontrola zda prompt používá konkrétní proměnnou."""

def t_find_suspicious(tpl: Template) -> list[str]:
    """Security audit — detekce nebezpečných vzorů."""
```

---

## 5. Doporučení

### 5.1 OKAMŽITÉ (F331)

1. ✅ **Vytvořeno:** `utils/t_string_helpers.py` — analysis helpers
2. ✅ **NEMIGROVAT** f-string MLX prompty — t-strings nejsou vhodné pro dynamické prompty
3. ❌ **NEPOTŘEBNÉ** — rewrite MLX pipeline

### 5.2 DLOUHODOBÉ

1. **Prompt registry** — nové prompty registrovat s `t_inspect()` pro audit
2. **Security auditing** — použít `t_find_suspicious()` pro statickou analýzu
3. **Dokumentace** — přidat do CLAUDE.md sekci o t-strings

### 5.3 Co ZŮSTÁVÁ

- f-strings pro MLX prompty — správná volba pro dynamické runtime hodnoty
- `logger.info(f"...")` — nemigrovat
- Konfigurační generování — nemigrovat

---

## 6. Závěr

**PEP 750 t-strings JSOU dostupné v Python 3.14.6**, ale:

1. **Hodnoty jsou zapečeny v compile-time** — nelze použít pro MLX dynamické prompty
2. **Hlavní přínos:** Statická analýza, security audit, prompt registry
3. **Žádná migrace f-string → t-string není potřebná** pro MLX pipeline
4. **Helper utility vytvořena** v `utils/t_string_helpers.py`

**Akční krok:** Zaznamenat toto zjištění, NEPROVÁDĚT žádnou migraci MLX promptů.
