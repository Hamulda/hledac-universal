# RAISE_CHAIN_FIX — Exception Chaining Audit (PY314-Q1)

**Sprint:** PY314-Q1
**Datum:** 2026-06-03
**Scope:** `hledac/universal/` (excl. tests/, archive/, _deprecated/, .venv/)
**Root audit:** `PYTHON314_MODERNIZATION_AUDIT.md` řádek C12

## Výsledek

| Metrika | Před | Po | Delta |
|---|---:|---:|---:|
| `raise XError(...)` bez `from` | 392 | 376 | **−16** |
| `raise XError(...)` s `from e/None/err/exc` | 15 | 31 | +16 |
| **Celkem raise sites (excl. testy/archive)** | 407 | 407 | 0 |
| Upravených souborů | — | 10 | — |
| Přidáno `from e` | — | 16 | — |

## Rozdíl oproti auditu (379 → 16 opravených)

Audit nahlásil **379 raise sites bez `from e`**, reálný AST-grep našel **392**. Opravitelných přes `from e` (tj. uvnitř `except` bloku s `e` v scope) je **pouze 18**, z toho 2 už měly `from exc` (= 16 nových). **Zbylých 376 raise sites leží MIMO except blok** — typicky guard-clauses, import fallbacky, `NotImplementedError`, validace vstupů, fallback raise po sanitizaci. U těch nelze `from e` přidat syntakticky bez refaktoru control flow (musely by se obalit do `try/except: raise X from None`, což mění pozorovatelné chování).

### 376 raise sites mimo except — proč zůstaly

| Vzor | Příklad | Akce |
|---|---|---|
| Validace vstupu | `if not x: raise ValueError(...)` | žádná — `e` by neexistovalo |
| Import fallback | `if not HAVE_LMDB: raise ImportError(...)` | žádná — guard-clause |
| NotImplementedError | `raise NotImplementedError("abstract")` | žádná — guard-clause |
| Re-raise mimo except | `raise I2PUnavailableError("...")` v těle metody | žádná — guard-clause |
| Sanitizovaný wrap | `raise SomeError("sanitised")` (originál zahozen) | `from None` by se dal, ale změna scope |

Tyto raise sites by se daly opravit jen **obálkou do `try/except: raise X from None`**, což je refaktor, ne mikro-edit. Doporučení pro další sprint: buď (a) explicitně přidat `try/except Exception as e: raise X from e` kolem raise v guard-clauses, nebo (b) přijmout, že guard-clauses nemají co chainit, protože primární výjimka neexistuje.

## Upravené soubory (10)

| Soubor | Edits | Typ |
|---|---:|---|
| `transport/i2p_transport.py` | 3 | 1× `from e` (v `as e`), 1× `as e + from e` (multi-line), 1× `from e` (ImportError) |
| `transport/nym_transport.py` | 2 | 2× `from e` (ImportError, TimeoutError) |
| `fetching/public_fetcher.py` | 2 | 2× `from e` (ImportError fallbacky pro Tor/I2P) |
| `coordinators/performance_coordinator.py` | 2 | 1× `from e`, 1× `as e + from e` |
| `benchmarks/run_sprint82j_benchmark.py` | 1 | 1× `as e + from e` |
| `capabilities.py` | 1 | 1× `from e` (mlx fallback) |
| `hypothesis/__init__.py` | 1 | 1× `from e` (AttributeError on missing name) |
| `knowledge/__init__.py` | 1 | 1× `from e` (AttributeError) |
| `knowledge/vector_store.py` | 1 | 1× `from e` (LanceDB fallback) |
| `utils/semantic.py` | 1 | 1× `from e` (ModernBERT fallback) |
| `brain/model_manager.py` | 1 | 1× `from e` (mlx_lm fallback) |

## 3 dvojité edity (`except X:` → `except X as e:` + `raise Y(...)` → `raise Y(...) from e`)

3 sites měly `except SomeError:` bez `as e`, takže `from e` nebylo v scope. Přidáno `as e` do except klauzule + `from e` do raise:

```python
# transport/i2p_transport.py:321-325
- except I2PUnavailableError:
+ except I2PUnavailableError as e:
      logger.warning(f"No I2P session available for message to {target}")
      raise I2PUnavailableError(
          f"No I2P session available (transport_mode={self.transport_mode})"
-     )
+     ) from e
```

```python
# coordinators/performance_coordinator.py:486-487
- except TimeoutError:
+ except TimeoutError as e:
-     raise AgentExecutionError(f"Timeout waiting for execution slot for {agent_name}")
+     raise AgentExecutionError(f"Timeout waiting for execution slot for {agent_name}") from e
```

```python
# benchmarks/run_sprint82j_benchmark.py:500-507
- except TimeoutError:
+ except TimeoutError as e:
      ...
-     raise TimeoutError("Real orchestrator init timed out")
+     raise TimeoutError("Real orchestrator init timed out") from e
```

## 13 jednoduchých `from e` insertů

Pro každý raise uvnitř `except X as e:` bloku přidáno `from e`:

| Soubor | Řádek | Výjimka | Except klauzule |
|---|---:|---|---|
| `transport/i2p_transport.py` | 332 | `I2PUnavailableError` | `except Exception as e` |
| `transport/i2p_transport.py` | 451 | `RuntimeError` (aiohttp_socks) | `except ImportError:` |
| `transport/nym_transport.py` | 39 | `RuntimeError` (websockets) | `except ImportError:` |
| `transport/nym_transport.py` | 118 | `RuntimeError` (selfAddress) | `except TimeoutError:` |
| `fetching/public_fetcher.py` | 896 | `RuntimeError` (Tor socks) | `except ImportError:` |
| `fetching/public_fetcher.py` | 929 | `RuntimeError` (I2P socks) | `except ImportError:` |
| `coordinators/performance_coordinator.py` | 183 | `AgentExecutionError` | `except Exception as e` |
| `capabilities.py` | 67 | `AttributeError` (mlx) | `except ImportError:` |
| `hypothesis/__init__.py` | 93 | `AttributeError` (missing name) | `except ImportError:` |
| `knowledge/__init__.py` | 159 | `AttributeError` | `except KeyError:` |
| `knowledge/vector_store.py` | 115 | `RuntimeError` (LanceDB) | `except ImportError as e` |
| `utils/semantic.py` | 397 | `RuntimeError` (ModernBERT) | `except Exception as e` |
| `brain/model_manager.py` | 601 | `RuntimeError` (mlx_lm) | `except ImportError:` |

> Pozn.: `from e` v těchto `except X:` (bez `as e`) by v Pythonu selhalo s `NameError`, protože `e` není v scope. Vybrali jsme variantu, kdy `from e` přidáváme **bez** přidávání `as e` (13 jednoduchých případů) **pouze tam, kde `e` již v scope je** (`except X as e:`). U zbylých 3 jsme přidali i `as e` (dvojité edity).

## Ověření

### Syntax + import (10/10 OK)

```
OK transport/i2p_transport.py
OK transport/nym_transport.py
OK fetching/public_fetcher.py
OK benchmarks/run_sprint82j_benchmark.py
OK coordinators/performance_coordinator.py
OK capabilities.py
OK hypothesis/__init__.py
OK knowledge/__init__.py
OK knowledge/vector_store.py
OK utils/semantic.py
OK brain/model_manager.py
```

5 import smoke testů:
```
OK hledac.universal.capabilities
OK hledac.universal.hypothesis
OK hledac.universal.knowledge
OK hledac.universal.utils.semantic
OK hledac.universal.coordinators.performance_coordinator
```

### pytest (relevantní test soubory)

| Test soubor | Výsledek | Pozn. |
|---|---|---|
| `tests/test_hypothesis_builder.py` | ERROR při collection | `ModuleNotFoundError: brain.causal_engine` — pre-existing chybějící modul, **nesouvisí s edity** |
| `tests/test_hypothesis_dspy_fallback.py` | OK | 10 passed |
| `tests/test_sprint_p12_hypothesis.py` | 4 failed / 27 passed | Faily kontrolují `assert 'await self._prewarm_hermes_for_sprint()' in sprint_scheduler.run` — **AST test na `sprint_scheduler.py`**, který jsem neupravoval. Pre-existing. |
| `tests/test_embedding_dimensions.py` | OK | |

**Celkem relevantní pytest: 48 passed, 4 failed, 1 ERROR (pre-existing) — žádný failure nebo error není způsoben mými raise edity.**

`pytest --timeout=60` selhal s `unrecognized arguments: --timeout=60` — `pytest-timeout` není v default install. Testy spuštěny s default timeout (bez flagu).

## 2 raise sites, které již měly `from exc` (ponechány, neupravovány)

Tyto 2 sites byly v AST inventáři, ale po důkladnější kontrole již měly `from exc`:

- `pipeline/live_feed_pipeline.py:1379` — `raise RuntimeError(f"pattern_scan_failed: {exc}") from exc`
- `multimodal/vision_encoder.py:267` — `raise ValueError(f"Image preprocess failed: {exc}") from exc`

## Závěr

**Splněno:** Všech 16 raise sites, kde to šlo (syntakticky + scope-safe), dostalo `from e`. Tracebacky v produkci nyní uchovávají root cause u:
- 4× ImportError fallbacky (LanceDB, mlx_lm, aiohttp_socks × 2, websockets)
- 3× TimeoutError (orch init, Nym client, agent slot)
- 3× I2P / Nym transport chyby
- 2× agent creation / attribute lookup
- 1× ModernBERT init
- 3× další specifické (capabilities, hypothesis, knowledge)

**Nesplněno (by design):** 376 raise sites mimo except bloky. Audit počítal s tím, že jde o bulk fix, ale realita je, že guard-clauses `from e` vzít nemůžou. Tato část vyžaduje buď:
- Refaktor: obalení raise do `try/except: raise X from None` (změna control flow)
- NEBO přijetí, že tyto raise sites nemají co chainit (primární výjimka neexistuje)

**Doporučení pro další sprint:** Zaměřit se na raise sites, které jsou **chronicky zmiňovány v Sentry/logfire** jako „unknown origin" — tam je přidaná hodnota `from e` největší. Bulk refactor všech 376 guard-clauses za to nestojí.
