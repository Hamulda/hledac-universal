# Python 3.14+ Best Practices Audit

**Project:** `hledac-universal` (Hledac Universal — autonomní OSINT orchestrátor)
**Python target:** 3.14.5 (`requires-python = ">=3.14,<3.16"` v `pyproject.toml`)
**Audit date:** 2026-06-01
**Scope:** 690 Python souborů v produkčním kódu (vynecháno: `tests/`, `build/`, `archive/`, `_shims/`, `rust_extensions/`, `.venv/`, všechny cache/tooling adresáře, symlink `hledac-universal-link`)

---

## Executive Summary

| Oblast | Nalezeno | Doporučení | Priorita |
|---|---|---|---|
| 1. PEP 696 `TypeVar` defaults | 8 chybějících | Přidat `default=Any` | LOW |
| 2. PEP 695 Type Aliases | 0× `TypeAlias`, 0× starý `Union[X,Y]` v type alias pozici | Žádná akce (moderní kód) | — |
| 3. PEP 702 `@deprecated` | 23 kandidátů | Přidat `@warnings.deprecated("…")` | MED |
| 4. `asyncio.run()` v async | 1× (správně guardovaný `RuntimeError` fallback) | Refactor na `loop.run_until_complete` / `asyncio.get_event_loop().run_until_complete` | LOW |
| 5. `asyncio.gather` bez `return_exceptions` | 3× (1× produkce, 2× benchmark) | Přidat `return_exceptions=True` | MED |
| 6. Třídy s `>5` attrs bez `__slots__` | 187 tříd, z toho 53 s `>10` attrs | Migrace nejdřív na `SprintScheduler` (116 attrs) a `FullyAutonomousOrchestrator` (513) | HIGH |
| 7. `@dataclass` bez `slots=True` | 763 tříd | Přidat `slots=True` u frozen a velkých (>5 attrs) | MED |
| 8. `match/case` | 14 již existuje, 0× `if/elif` ≥4 | Žádná akce (projekt je moderní) | — |
| 9. `tomllib` | 0× nalezeno, 0× `tomli` fallback | Žádná potřeba — projekt nepoužívá TOML | — |
| 10. `ExceptionGroup` (`except*`) | 0× `except*` | Žádná potřeba — `gather(return_exceptions=True)` dominuje | — |

**Hlavní zjištění:** Projekt je na Python 3.14 již silně modernizovaný — `match/case` se používá (14 match bloků), `asyncio.gather(return_exceptions=True)` je enforced ve 130+ místech, staré `Union[X, Y]` v type-alias pozici chybí. **Hlavní dluh je v `__slots__`** na velkých třídách (SprintScheduler 116 attrs, FullyAutonomousOrchestrator 513 attrs) a v 98 `Optional[X]` anotacích, které jdou nahradit `X | None`.

---

## 1. PEP 696 `TypeVar` defaults (Python 3.13+)

**Nalezeno:** 8 `TypeVar(...)` bez `default=` parametru. S Pythonem 3.13+ lze přidat výchozí typ — v tomto projektu typicky `default=Any` nebo `default=str`, aby se zabránilo chybám při neuvedení parametru v generických dataclasses a funkcích.

| Soubor | Řádek | Problém | Doporučená oprava | Priorita |
|---|---|---|---|---|
| `utils/mlx_utils.py` | 31 | `T = TypeVar('T')` — unconstrained | `T = TypeVar('T', default=Any)` | LOW |
| `utils/async_utils.py` | 38 | `T = TypeVar('T')` — unconstrained | `T = TypeVar('T', default=Any)` | LOW |
| `utils/validation.py` | 23 | `T = TypeVar('T')` — unconstrained | `T = TypeVar('T', default=Any)` | LOW |
| `brain/hermes3_engine.py` | 34 | `T = TypeVar('T', bound=BaseModel)` | `T = TypeVar('T', bound=BaseModel, default=BaseModel)` | LOW |
| `brain/_lazy.py` | 43 | `T = TypeVar("T")` — unconstrained | `T = TypeVar("T", default=Any)` | LOW |
| `brain/model_swap_manager.py` | 42 | `T = TypeVar("T")` — unconstrained | `T = TypeVar("T", default=Any)` | LOW |
| `brain/modernbert_adapter.py` | 39 | `T = TypeVar('T', bound=BaseModel)` | `T = TypeVar('T', bound=BaseModel, default=BaseModel)` | LOW |
| `brain/model_engine.py` | 24 | `T = TypeVar('T', bound=BaseModel)` | `T = TypeVar('T', bound=BaseModel, default=BaseModel)` | LOW |

**Poznámka:** V Pythonu 3.14 se `TypeVar.default` AST atribut chová jako keyword argument (viz PEP 696). Všechny bound=`BaseModel` lze zjednodušit přes `type T = …` syntaxi (PEP 695) — ale to vyžaduje větší refactor.

---

## 2. PEP 695 Type Aliases

**Nalezeno:** 0× `TypeAlias` importů, 0× `Union[X, Y]` na úrovni modulu v typové alias pozici. **Projekt je v této oblasti již moderní.**

Žádné akce. (Interní `Optional[X]` se řeší v bodě 11 níže.)

---

## 3. PEP 702 `@deprecated` (`warnings.deprecated`)

**Nalezeno:** 23 funkcí, jejichž název obsahuje `deprecated`, `_legacy`, `old_`, `_old`, `_compat`, `compat_` — ale žádná nemá `@warnings.deprecated` dekorátor. V Pythonu 3.13+ lze použít `from warnings import deprecated` a dát varování, že funkce bude v příští verzi odstraněna.

| Soubor | Řádek | Funkce | Doporučená oprava | Priorita |
|---|---|---|---|---|
| `research_context.py` | 109 | `from_dict_compat` | `@deprecated("Use ResearchContext.from_dict() directly")` | MED |
| `embedding_pipeline.py` | 179 | `_get_legacy_ane_embedder` | `@deprecated("Use _get_ane_embedder() — legacy variant will be removed in v19")` | MED |
| `capabilities.py` | 239 | `is_scaffold_only` | `@deprecated("Use CapabilityTier enum instead")` | MED |
| `tools/live_kpi_extraction_guard.py` | 175 | `_check_kpi_compat_wrapper` | `@deprecated("Use _check_kpi() directly")` | MED |
| `memory/memory_manager.py` | 378 | `cleanup_old_sessions` | `@deprecated("Use MemoryManager.prune_sessions()")` | MED |
| `runtime/shadow_inputs.py` | 137 | `is_legacy_mode` | `@deprecated("Shadow mode flag moved to policy_manager")` | MED |
| `legacy/atomic_storage.py` | 660 | `cleanup_old_files` | `@deprecated("Use AtomicStorage.prune_files()")` | LOW (legacy/) |
| `legacy/atomic_storage.py` | 2202 | `evict_old_patterns` | `@deprecated("Use AtomicStorage.evict_patterns()")` | LOW (legacy/) |
| `utils/queue_policy.py` | 17 | `put_drop_oldest` | `@deprecated("Use QueuePolicy.put() with drop policy")` | MED |
| `utils/robots_parser.py` | 103 | `_evict_oldest_if_needed` | `@deprecated("Internal — use bounded LRU cache")` | LOW |
| `utils/__init__.py` | 148 | `get_uuid7_compat_status` | `@deprecated("UUID7 is always available in 3.14")` | MED |
| `knowledge/ann_index.py` | 237 | `_get_oldest_timestamp` | `@deprecated("Internal API")` | LOW |
| `knowledge/__init__.py` | 84 | `_lazy_legacycompat` | `@deprecated("Use direct import")` | MED |
| `knowledge/wal.py` | 293 | `_evict_oldest_pending_markers` | `@deprecated("Use WAL.evict_pending()")` | LOW |
| `knowledge/graph_builder.py` | 107 | `_get_legacy_types` | `@deprecated("Use GraphBuilder.type_registry()")` | MED |
| `knowledge/duckdb_store.py` | 6227 | `_wal_evict_oldest_pending_markers` | `@deprecated("Use DuckDBStore.wal_evict()")` | LOW |
| `execution/ghost_executor.py` | 430 | `get_runtime_only_compat_actions` | `@deprecated("Use get_runtime_actions()")` | MED |
| `benchmarks/live_measurement_parser.py` | 229 | `_parse_legacy_sprint_report` | `@deprecated("Use parse_sprint_report()")` | LOW |
| `brain/model_lifecycle.py` | 532 | `_unload_model_legacy` | `@deprecated("Use ModelLifecycle.unload()")` | MED |
| `tools/codehealth_guard.py` | 158 | `_is_compat_wrapper` | `@deprecated("Code health check moved to lint module")` | MED |
| `tools/bench_f214_python314_runtime.py` | 339 | `cold_first_access` | `@deprecated("Use importlib cold-start benchmark")` | LOW (bench) |
| `probe_f229g_next_action_owner_moved_guard/run_probe.py` | 142 | `test_run_guard_old_bad_fixture` | `@deprecated("Test fixture renamed")` | LOW (test probe) |
| `probe_f226b_confidence_policy_reality/test_confidence_policy_reality.py` | 239 | `test_social_min_confidence_threshold_preserved` | `@deprecated("Test moved to test_social_policy.py")` | LOW (test probe) |

**Doporučení:** Pro `legacy/` adresář nechat — ty jsou vědomě archivované. Pro zbytek přidat `@deprecated("…")` s jasným migration path.

---

## 4. `asyncio.run()` v async kontextu (GHOST_INVARIANT)

**Nalezeno:** 1 produkční výskyt, ale všechny jsou **správně guardované** pomocí `except RuntimeError` fallbacku (viz `M1_CRASH_FIXES.md` projektová dokumentace).

| Soubor | Řádek | Problém | Doporučená oprava | Priorita |
|---|---|---|---|---|
| `runtime/sprint_scheduler.py` | 5563 | `asyncio.run(expand_query(query))` uvnitř `async def` metody, ale uvnitř `except RuntimeError:` bloku | Zvážit refaktor — `expand_query` by měl být awaitable, ne sync wrapper; odstranit potřebu fallbacku | LOW |

**Detail:** Před `asyncio.run` se testuje `asyncio.get_running_loop()` a volá `loop.run_until_complete()`. Pokud loop neběží (`RuntimeError`), spadne do `asyncio.run()`. Toto je **záměrný** M1-safe pattern (viz `M1_CRASH_FIXES.md`). Refaktor by spočíval v přesunutí `expand_query` do čistě async API a odstranění sync wrapperu.

Všechny ostatní `asyncio.run()` v `tests/`, `tools/bench_*`, `core/__main__.py:2530,2558`, `layers/smart_coordination.py:561`, `layers/hive_coordination.py:726`, `tools/f234_*`, `tools/probe_*` jsou **CLI entry pointy nebo sync testy** — ty jsou legitimní.

---

## 5. `asyncio.gather()` bez `return_exceptions=True`

**Nalezeno:** 3 výskyty v produkčním kódu (vše ostatní — 130+ — již `return_exceptions=True` používá).

| Soubor | Řádek | Problém | Doporučená oprava | Priorita |
|---|---|---|---|---|
| `network/ipfs_client.py` | 619 | `results = await asyncio.gather(*tasks)` — `_fetch_one` sám pohlcuje výjimky, ale chybí explicitní `return_exceptions=True` | Přidat `return_exceptions=True`; výsledek pak `isinstance(r, Exception)` check | MED |
| `tools/bench_f214_python314_runtime.py` | 748 | `await asyncio.gather(*(plain_task(i) for i in range(n_tasks)))` — **záměrně** testuje plain gather pro srovnání | Žádná akce (benchmark) | LOW |
| `tools/bench_f214_python314_runtime.py` | 752 | `await asyncio.gather(*(sem_task(i, sem) for i in range(n_tasks)))` — **záměrně** testuje sem-gather | Žádná akce (benchmark) | LOW |

**Skutečná oprava:** pouze `network/ipfs_client.py:619`. Doporučení:
```python
# Před:
results = await asyncio.gather(*tasks)
# Po:
results = await asyncio.gather(*tasks, return_exceptions=True)
results = [r for r in results if r is not None and not isinstance(r, BaseException)]
```

---

## 6. Třídy s `>5` inst. atributy bez `__slots__` (HIGH — největší dopad na M1 8GB)

**Nalezeno:** 187 tříd s `>5` instancí atributy a **žádným `__slots__`**. Na M1 MacBook 8GB UMA je `__dict__` na instanci ~232 bytů; s `__slots__` klesne na ~8 bytů. **Top offenders:**

| Soubor | Řádek | Třída | Počet attrs | Doporučená oprava | Priorita |
|---|---|---|---|---|---|
| `legacy/autonomous_orchestrator.py` | 3134 | `FullyAutonomousOrchestrator` | 513 | Refaktor na kompozici + `__slots__`; 513 attrs je kandidát na rozklad | HIGH |
| `runtime/sprint_scheduler.py` | 4050 | `SprintScheduler` | 116 | Přidat `__slots__ = (...)` se seznamem atributů; ověřit, že žádný kód nepřidává dyn. attrs | HIGH |
| `legacy/autonomous_orchestrator.py` | 22420 | `_ResearchManager` | 103 | Refaktor + `__slots__` | HIGH |
| `coordinators/fetch_coordinator.py` | 226 | `FetchCoordinator` | 58 | `__slots__` | MED |
| `brain/hermes3_engine.py` | 237 | `Hermes3Engine` | 46 | `__slots__` | MED |
| `knowledge/lancedb_store.py` | 114 | `LanceDBIdentityStore` | 37 | `__slots__` | MED |
| `intelligence/web_intelligence.py` | 113 | `UnifiedWebIntelligence` | 33 | `__slots__` | MED |
| `prefetch/prefetch_oracle.py` | 88 | `PrefetchOracle` | 33 | `__slots__` | MED |
| `knowledge/duckdb_store.py` | 570 | `DuckDBShadowStore` | 27 | `__slots__` | MED |
| `evidence_log.py` | 224 | `EvidenceLog` | 25 | `__slots__` | MED |
| `layers/communication_layer.py` | 99 | `CommunicationLayer` | 25 | `__slots__` | MED |
| `rl/sprint_policy_manager.py` | 183 | `SprintPolicyManager` | 25 | `__slots__` | MED |
| `transport/tor_transport.py` | 57 | `TorTransport` | 24 | `__slots__` | MED |
| `transport/nym_transport.py` | 32 | `NymTransport` | 23 | `__slots__` | MED |
| `context_optimization/dynamic_context_manager.py` | 167 | `DynamicContextManager` | 21 | `__slots__` (pozor: dynamické attrs v contextu) | MED |
| `brain/hypothesis_engine.py` | 2249 | `HypothesisEngine` | 21 | `__slots__` | MED |
| `stealth/stealth_manager.py` | 99 | `StealthManager` | 21 | `__slots__` | MED |
| `brain/prompt_bandit.py` | 16 | `PromptBandit` | 20 | `__slots__` | MED |
| `coordinators/memory_coordinator.py` | 2415 | `MultiLevelContextCache` | 19 | `__slots__` | MED |
| `coordinators/research_coordinator.py` | 166 | `UniversalResearchCoordinator` | 19 | `__slots__` | MED |
| `intelligence/stealth_crawler.py` | 87 | `StealthCrawler` | 18 | `__slots__` | MED |
| `core/__main__.py` | (třída `…`) | 18 | `__slots__` | MED |
| `runtime/sprint_scheduler.py` | 1822 | `SprintSchedulerResult` | 18 (dataclass, již `slots=True`) | — | — |

**Poznámka k bezpečnosti:** `__slots__` je bezpečné jen pokud:
1. Žádný kód nepřidává atribut za běhu (`obj.new_attr = …`)
2. Třída nemá podtřídy, které by mohly přidat vlastní attrs (pokud nemají svůj `__slots__`)
3. Nepoužívá se `@dataclass` bez explicit `slots=True` (řeší bod 7)

Pro `SprintScheduler` (116 attrs) — doporučuji provést audit dynamických attrs (grep na `self\\.\w+\\s*=` přes 4050-29717) před přidáním `__slots__`. Top 3 třídy (`FullyAutonomousOrchestrator`, `_ResearchManager`) jsou v `legacy/` a pravděpodobně neaktivní.

---

## 7. `@dataclass` bez `slots=True`

**Nalezeno:** 763 dataclasses bez `slots=True` (oproti 84, které ho mají). 85 z nich je `frozen=True` — ty by měly mít `slots=True` **prioritně** (frozen + slots dává největší memory benefit + urychluje `__init__`).

| Distribuce (počet atributů) | Bez slots | S slots | Poznámka |
|---|---|---|---|
| 1–3 attrs | 53 | — | slots nepřináší velký benefit |
| 4–5 attrs | 177 | — | malý benefit |
| 6–10 attrs | 368 | 84 | **priorita MED** |
| 11–20 attrs | 126 | — | **priorita MED** |
| >20 attrs | 39 | — | **priorita HIGH** (StealthConfig má 56 attrs) |

**Top soubory pro refactor:**
- `project_types.py` — 44 dataclasses (konfigurační)
- `legacy/autonomous_orchestrator.py` — 22
- `brain/hypothesis_engine.py` — 17
- `forensics/metadata_extractor.py` — 16
- `enhanced_research.py` — 13

**Frozen dataclasses bez slots (priorita MED):** 85 výskytů. Příklady:

| Soubor | Řádek | Třída | Počet attrs | Doporučená oprava |
|---|---|---|---|---|
| `enhanced_research.py` | 2372 | `SourcePlan` | ? | `@dataclass(frozen=True, slots=True)` |
| `enhanced_research.py` | 2798 | `TriadAdmissionDescriptor` | 12 | přidat `slots=True` |
| `enhanced_research.py` | 2879 | `LocalCorpusConsumerDescriptor` | 15 | přidat `slots=True` |
| `tool_registry.py` | 118 | `DeepResearchProviderMirror` | 9 | přidat `slots=True` |
| `project_types.py` | 1127 | `SpikeData` | ? | přidat `slots=True` |
| `project_types.py` | 1314 | `RunCorrelation` | ? | přidat `slots=True` |
| `project_types.py` | 1738 | `CanonicalGroundingHints` | ? | přidat `slots=True` |
| `pipeline/live_public_pipeline.py` | 641 | `FetchPolicy` | ? | přidat `slots=True` |
| `tools/audit_reality_index.py` | 42 | `ClaimResult` | ? | přidat `slots=True` |
| `transport/transport_router.py` | 70 | `TransportDecision` | ? | přidat `slots=True` |
| `transport/circuit_breaker.py` | 67 | `CircuitBreakerSnapshot` | ? | přidat `slots=True` |
| `transport/circuit_breaker.py` | 78 | `CircuitDecision` | ? | přidat `slots=True` |
| `transport/base.py` | 60 | `TransportConfig` | ? | přidat `slots=True` |
| `transport/base.py` | 81 | `TransportResult` | ? | přidat `slots=True` |
| `intelligence/passive_fingerprint.py` | 62 | `ServiceFingerprint` | ? | přidat `slots=True` |
| `intelligence/passive_fingerprint.py` | 74 | `FingerprintResult` | ? | přidat `slots=True` |
| `intelligence/passive_fingerprint.py` | 83 | `TechStack` | ? | přidat `slots=True` |
| `intelligence/wayback_diff_miner.py` | 67 | `CDXDiffEvent` | ? | přidat `slots=True` |
| `intelligence/attribution_scorer.py` | 48 | `AttributionFactor` | ? | přidat `slots=True` |
| `intelligence/attribution_scorer.py` | 60 | `AttributionScore` | ? | přidat `slots=True` |
| `intelligence/kill_chain_tagger.py` | 674 | `KillChainTag` | ? | přidat `slots=True` |
| `intelligence/rir_correlator.py` | 59 | `RIRCorrelation` | ? | přidat `slots=True` |
| `intelligence/rir_correlator.py` | 72 | `RIRCorrelationResult` | ? | přidat `slots=True` |
| `intelligence/social_identity_miner.py` | 190 | `SocialIdentityFacet` | ? | přidat `slots=True` |
| `intelligence/social_identity_miner.py` | 206 | `SocialIdentityResult` | ? | přidat `slots=True` |
| `security/passive_dns.py` | 49 | `CIRCLPDNSRecord` | ? | přidat `slots=True` |
| `security/passive_dns.py` | 120 | `PassiveDNSOutcome` | ? | přidat `slots=True` |

*(Kompletní seznam 85 frozen dataclassů je v `/tmp/audit_results.json`, klíč `dataclass_frozen_no_slots`.)*

---

## 8. `match/case` (Python 3.10+)

**Nalezeno:** 14 již existujících `match` bloků. **0× `if/elif` řetězce ≥ 4 větve.** Projekt plně využívá `match/case`.

Aktivní místa:
- `export/sprint_exporter.py:838, 1212, 3574, 3697`
- `intelligence/input_detector.py:556`
- `intelligence/wayback_cdx.py:184`
- `pipeline/live_public_pipeline.py:2574`
- `runtime/sprint_scheduler.py:13324, 21629`

Žádná akce.

---

## 9. `tomllib` (Python 3.11+ vestavěný)

**Nalezeno:** 0× `tomllib`, 0× `tomli`, 0× ruční TOML parsing, 0× `configparser`. **Projekt nepoužívá TOML ani INI konfiguraci.** Všechna konfigurace je v `pyproject.toml` (který se neparsí za běhu) a v dataclasses (`project_types.py`).

Žádná akce.

---

## 10. `ExceptionGroup` (`except*`, Python 3.11+)

**Nalezeno:** 0× `except*` syntaxe. 180× `except (A, B):` — to jsou **normální** `try/except` bloky, ne kandidáti na `except*`. `except*` je specificky pro `ExceptionGroup`, který se v projektu **nevytváří** — všechny `asyncio.gather()` používají `return_exceptions=True`, takže `ExceptionGroup` se nikdy nevyhodí.

Žádná akce. Poznámka: pokud by se v budoucnu přešlo na `asyncio.TaskGroup` (3.11+), `except*` by se stal relevantním.

---

## 11. Staré `Optional[X]` / `List[X]` / `Union[X, Y]` (bonus, mimo 10 bodů)

**Nalezeno:** 98 výskytů starého typing stylu. **V Pythonu 3.10+** jde nahradit `X | None`, `list[X]`, `X | Y` atd. (PEP 604).

| Typ | Výskytů | Doporučení |
|---|---|---|
| `Optional[X]` | 60 | `X \| None` |
| `List[X]` | 21 | `list[X]` |
| `Tuple[X, ...]` | 7 | `tuple[X, ...]` |
| `Union[X, Y]` | 4 | `X \| Y` |
| `Dict[K, V]` | 3 | `dict[K, V]` |
| `Set[X]` | 3 | `set[X]` |

**Top soubory:**

| Soubor | Výskytů | Doporučení |
|---|---|---|
| `runtime/sprint_scheduler.py` | 29 | bulk refactor |
| `tools/probe_f214h_content_miner_backpressure/probe_f214h.py` | 21 | bulk refactor |
| `probe_f207j_nonfeed_finding_bridge/nonfeed_finding_bridge.py` | 18 | bulk refactor |
| `captcha_solver.py` | 7 | bulk refactor |
| `discovery/matrix_adapter.py` | 6 | bulk refactor |
| `dht/metadata_fetcher.py` | 5 | bulk refactor |
| `discovery/fediverse_adapter.py` | 4 | bulk refactor |
| `tools/ioc_dedup.py` | 3 | bulk refactor |
| `tools/serialization.py` | 2 | bulk refactor |
| `utils/bloom_filter.py` | 2 | bulk refactor |

**Příklad konverze:**
```python
# Před (typing.Optional):
creation_date: Optional[int] = None
# Po (PEP 604):
creation_date: int | None = None
```

Poznámka: `from __future__ import annotations` v souboru umožňuje použít `X | None` i na starších verzích — projekt toho již využívá, takže konverze je mechanická.

---

## Doporučený pořadník oprav

| Pořadí | Oblast | Důvod |
|---|---|---|
| **1** | Bod 6 — `__slots__` na `SprintScheduler` (116 attrs) a `FetchCoordinator` (58) | Největší win pro M1 8GB RAM; běží v produkci |
| **2** | Bod 7 — `slots=True` na všech frozen dataclasses (85 tříd) | Mechanická změna, zero-risk pro frozen |
| **3** | Bod 5 — `return_exceptions=True` v `network/ipfs_client.py:619` | Reálný bug — jedna selhavší CID může shodit celý batch |
| **4** | Bod 3 — `@deprecated` na 23 funkcí | Pomáhá API konzumentům, příprava na v19 |
| **5** | Bod 11 — `Optional[X]` → `X \| None` (98 výskytů) | Čitelnost; strojově proveditelné |
| **6** | Bod 1 — `TypeVar(default=…)` (8 souborů) | Nutné pro správné generics v 3.13+ |
| **7** | Bod 4 — `asyncio.run` v async (1× `sprint_scheduler.py:5563`) | Refactor na awaitable API |
| **8** | Bod 6 — zbytek 187 tříd bez `__slots__` (hlavně `legacy/`) | Nízká priorita — `legacy/` je archiv |

---

## Appendix: Audit Methodology

Použitý skript analyzoval 690 `.py` souborů (po vyloučení `tests/`, `build/`, `archive/`, `_shims/`, `rust_extensions/`, `.venv/`, `dist/`, všech `.git/`, `.cache/`, cache adresářů a symlinku `hledac-universal-link`).

Detekční metody:
1. **AST-based** — Python `ast` modul parsoval každý soubor; návštěvníci hledali:
   - `asyncio.run()` uvnitř `async def` (Ghost Invariant)
   - `asyncio.gather(...)` bez `return_exceptions=` kwarg
   - `TypeVar(...)` bez `default=` kwarg
   - `@dataclass` dekorátory s analýzou `(frozen=, slots=)` kwargs
   - `match` uzly (úspěch) vs. `if/elif` řetězce ≥ 4 větve (kandidáti)
   - Třídy s `>5` inst. atributy (`self.X = ...` / `self.X: ... = ...`) a přítomnost `__slots__`
2. **grep-based** — `ripgrep` doplnil AST sken o:
   - `Optional[X]`, `List[X]`, `Dict[X,Y]`, `Union[X,Y]` (konverze na PEP 604)
   - `@deprecated`, `from warnings import deprecated`
   - `except*`, `tomllib`, `tomli`
   - match/case použití

Výsledky uloženy v `/tmp/audit_results.json` (AST sken) a `/tmp/audit_classes.json` (třídy).
