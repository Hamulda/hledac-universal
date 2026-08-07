"""Offline DSPy prompt optimizer – MIPROv2, idle-only, memory/thermal guards, circuit breaker."""
import asyncio
from hledac.universal.utils.async_helpers import safe_create_task
import logging

from operator import attrgetter, itemgetter
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, cast
import psutil
try:
    import orjson
    ORJSON_AVAILABLE = True

except ImportError:
    ORJSON_AVAILABLE = False
    import json as _json
from hledac.universal.utils.msgspec_json import encode as _msgspec_encode, decode as _msgspec_decode
logger = logging.getLogger(__name__)

def _check_dspy_version() -> tuple[bool, str]:
    """
    Sprint P0-1: Lazy DSPy version + MIPROv2 availability check.

    Returns:
        (has_mipro, version_str)
    """
    try:
        import dspy
        ver = getattr(dspy, '__version__', 'unknown')
        has_mipro = hasattr(dspy.teleprompt, 'MIPROv2')
        return (has_mipro, ver)
    except Exception:
        return (False, 'unavailable')

class DSPyOptimizer:
    __slots__ = tuple(('_brain', '_cache_path', '_circuit_duration', '_circuit_open_until', '_current_version', '_failure_count', '_max_failures', '_max_versions_per_task', '_optimization_interval', '_optimized_prompts', '_performance_history', '_prompt_versions', '_rollback_threshold', '_stop', '_task'))

    def __init__(self, brain_manager):
        self._brain = brain_manager
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._optimized_prompts = {}
        self._prompt_versions: dict[str, list[dict]] = defaultdict(list)
        self._current_version: dict[str, int] = defaultdict(int)
        self._performance_history: dict[str, list[float]] = defaultdict(list)
        self._rollback_threshold = 0.2
        self._max_versions_per_task = 10
        self._failure_count = 0
        self._max_failures = 3
        self._circuit_open_until = 0.0
        self._circuit_duration = 3600
        self._cache_path = Path.home() / '.hledac' / 'dspy_cache.json'
        self._load_cache()
        self._optimization_interval = 86400

    def _load_cache(self):
        if self._cache_path.exists():
            try:
                with open(self._cache_path, 'rb') as f:
                    data_str = f.read()
                if ORJSON_AVAILABLE:
                    data = orjson.loads(data_str)
                else:
                    data = _msgspec_decode(data_str)
                self._optimized_prompts = data.get('prompts', {})
                self._prompt_versions = defaultdict(list, data.get('versions', {}))
                self._current_version = defaultdict(int, data.get('current', {}))
                logger.info(f'Loaded {len(self._optimized_prompts)} optimized prompts')
            except Exception as e:
                logger.warning(f'Failed to load DSPy cache: {e}')

    def _save_cache(self):
        try:
            data = {'prompts': self._optimized_prompts, 'versions': dict(self._prompt_versions), 'current': dict(self._current_version)}
            if ORJSON_AVAILABLE:
                data_bytes = orjson.dumps(data)
            else:
                data_bytes = _msgspec_encode(data)
            with open(self._cache_path, 'wb') as f:
                f.write(data_bytes)
        except Exception as e:
            logger.warning(f'Failed to save DSPy cache: {e}')

    def _should_optimize(self) -> bool:
        """Check if system is idle enough (CPU < 15%, RAM > 4GB, not on battery unless >80%, thermal OK, circuit breaker)."""
        if os.getenv('HLEDAC_DSPY_OPTIMIZE') != '1':
            return False
        if time.time() < self._circuit_open_until:
            return False
        if psutil.cpu_percent(interval=0.5) > 15:
            return False
        if psutil.virtual_memory().available / 1024 ** 3 < 2.0:
            return False
        if hasattr(self._brain, '_orch') and self._brain._orch._memory_mgr:
            if self._brain._orch._memory_mgr._on_battery_power():
                logger.debug('[DSPy] Defer – on battery')
                return False
        else:
            battery = psutil.sensors_battery()
            if battery and (not battery.power_plugged) and (battery.percent < 80):
                logger.debug('[DSPy] Defer – on battery (psutil)')
                return False
        if hasattr(self._brain, '_orch') and self._brain._orch._memory_mgr:
            thermal = self._brain._orch._memory_mgr.get_thermal_state()
            if thermal.name in ('HOT', 'CRITICAL'):
                return False
            hist = getattr(self._brain._orch._memory_mgr, '_thermal_history', [])
            if len(hist) >= 3:
                recent = [t[1].value for t in hist[-3:]]
                if recent[2] > recent[1] > recent[0]:
                    logger.debug('[DSPy] Defer – thermal rising')
                    return False
        if 'pytest' in sys.modules:
            return False
        return True

    async def _optimize_loop(self):
        while not self._stop.is_set():
            await asyncio.sleep(self._optimization_interval)
            if await asyncio.to_thread(self._should_optimize):
                await self._run_optimization()

    async def _generate_synthetic_examples(self, limit: int=100) -> list[tuple]:
        """
        F234: Generate synthetic (query, answer) training pairs from packet data.

        Reads packet files from ~/.hledac/evidence_packets/shards/ and extracts
        (url, normalized_content) pairs as minimal OSINT training examples.

        Falls back to curated seed examples when packet data is unavailable.
        This ensures MIPROv2 always has a non-empty trainset.
        """
        examples: list[tuple] = []
        packet_base = Path.home() / '.hledac' / 'evidence_packets'
        if packet_base.exists():
            try:
                shards = sorted(packet_base.glob('shards/*'))[:20]
                for shard in shards:
                    if len(examples) >= limit:
                        break
                    for pkt_file in sorted(shard.glob('*.json'))[:5]:
                        try:
                            raw = await asyncio.to_thread(pkt_file.read_bytes)
                            data = orjson.loads(raw) if ORJSON_AVAILABLE else _json.loads(raw.decode())
                            url = data.get('url', '')
                            content = data.get('content', '')[:3000]
                            if url and content and (len(content) > 100):
                                query = f'Analyze this OSINT target: {url}'
                                normalized = content[:2000].strip()
                                examples.append((query, normalized))
                        except Exception:
                            continue
                        if len(examples) >= limit:
                            break
            except Exception as e:
                logger.debug(f'[DSPy] Packet scan failed: {e}')
        if len(examples) < 10:
            seed_examples: list[tuple] = [('Analyze this domain for potential data exposure: example.onion', '{"indicator": "example.onion", "type": "darknet_domain", "risk": "medium", "findings": ["potential Tor hidden service", "requires Nym or I2P access"]}'), ('Check if this URL shows signs of compromise: http://185.220.101.47/', '{"indicator": "185.220.101.47", "type": "ipv4", "risk": "high", "findings": ["high-risk Tor exit node IP", "potential hostile scanner"]}'), ('Investigate this email pattern for breach correlation: user@company.com', '{"indicator": "user@company.com", "type": "email", "risk": "medium", "findings": ["email pattern matches corporate naming convention"]}'), ('Analyze this URL structure for infrastructure tracking: http://target.com/api/v2/internal/users', '{"indicator": "target.com/api/v2/internal/users", "type": "url", "risk": "high", "findings": ["internal API endpoint exposed", "potential information disclosure"]}'), ('Check this IP for threat intelligence: 194.5.249.253', '{"indicator": "194.5.249.253", "type": "ipv4", "risk": "critical", "findings": ["known infrastructure", "APT associated", "immediate block recommended"]}'), ('Analyze this GitHub repository for secrets: https://github.com/org/repo', '{"indicator": "github.com/org/repo", "type": "github", "risk": "high", "findings": ["repository name suggests internal tooling", "requires auth for deep scan"]}'), ('Investigate this ASN for infrastructure mapping: AS212238', '{"indicator": "AS212238", "type": "asn", "risk": "medium", "findings": ["Censys-infrastructure ASN", "known VPN/proxy provider"]}'), ('Check this certificate for certificate pinning issues: *.example.com', '{"indicator": "*.example.com", "type": "cert_fingerprint", "risk": "low", "findings": ["wildcard cert observed", "certificate transparency log available"]}'), ('Analyze this Pastebin paste for exposed credentials: https://pastebin.com/raw/abc123', '{"indicator": "pastebin.com/abc123", "type": "pastebin", "risk": "critical", "findings": ["contains potential API keys", "requires immediate takedown check"]}'), ('Investigate domain for DNS history: suspicious-domain.com', '{"indicator": "suspicious-domain.com", "type": "domain", "risk": "high", "findings": ["recently registered", "nameservers in offshore jurisdiction"]}')]
            examples.extend(seed_examples)
        return examples[:limit]

    async def _load_training_examples(self, limit: int=1000) -> list[tuple]:
        """
        Load training examples from evidence JSONL files.

        Reads from EVIDENCE_ROOT/*.jsonl — one JSON per line, each line is an
        EvidenceEvent dict with event_type + payload. Fails safe on err (returns []).

        GHOST_INVARIANTS: async only (aiofiles), fail-safe on empty/corrupt files.

        F234: Falls back to _generate_synthetic_examples when evidence returns
        fewer than 10 examples (ensures MIPROv2 always has a trainset).
        """
        examples: list[tuple] = []
        try:
            import aiofiles
        except ImportError:
            logger.debug('[DSPy] aiofiles not available, skipping evidence load')
            return []
        try:
            from hledac.universal.paths import EVIDENCE_ROOT
        except ImportError:
            logger.debug('[DSPy] EVIDENCE_ROOT not available')
            return []
        evidence_files = []
        try:
            if EVIDENCE_ROOT.exists():
                evidence_files = sorted(EVIDENCE_ROOT.glob('*.jsonl'), key=attrgetter("stat")().st_mtime, reverse=True)[:10]
        except Exception as e:
            logger.debug(f'[DSPy] Failed to list evidence files: {e}')
        for ev_file in evidence_files:
            if len(examples) >= limit:
                break
            try:
                async with aiofiles.open(ev_file, 'rb') as f:
                    content = await f.read()
            except Exception as e:
                logger.debug(f'[DSPy] Failed to read {ev_file}: {e}')
                continue
            try:
                text = content.decode('utf-8') if isinstance(content, bytes) else content
                lines = text.strip().split('\n')
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    if ORJSON_AVAILABLE:
                        ev = orjson.loads(line)
                    else:
                        ev = _msgspec_decode(line)
                    ev_type = ev.get('event_type', '')
                    if ev_type not in ('decision', 'action_executed'):
                        continue
                    payload = ev.get('payload') or {}
                    query = payload.get('query') or payload.get('params', {}).get('query') or payload.get('action_params', {}).get('query') or ''
                    result = payload.get('result') or payload.get('action_result') or payload.get('response', {}).get('content', '') or str(payload.get('response', '')) or ''
                    if query and result:
                        examples.append((query, result))
                    if len(examples) >= limit:
                        break
            except Exception:
                continue
        if len(examples) < 10:
            logger.info(f'[DSPy] Evidence returned {len(examples)} examples, generating synthetic fallback...')
            synthetic = await self._generate_synthetic_examples(limit - len(examples))
            examples.extend(synthetic)
        return examples

    async def _run_optimization(self):
        """Load training data from evidence log and run DSPy."""
        logger.info('Starting DSPy optimization...')
        try:
            raw_examples = await self._load_training_examples(1000)
            examples = self._filter_training_examples(raw_examples)
            if len(examples) < 10:
                logger.debug('Not enough training examples')
                return
            async with asyncio.timeout(600):
                new_prompts = await asyncio.to_thread(self._dspy_optimize_mipro, examples)
            if new_prompts:
                self._optimized_prompts.update(new_prompts)
                for task, prompt in new_prompts.items():
                    ver = self._current_version[task] + 1
                    self._prompt_versions[task].append({'version': ver, 'prompt': prompt, 'trained_at': time.time(), 'examples': len(examples)})
                    self._current_version[task] = ver
                for task in self._prompt_versions:
                    if len(self._prompt_versions[task]) > self._max_versions_per_task:
                        self._prompt_versions[task] = self._prompt_versions[task][-self._max_versions_per_task:]
                self._save_cache()
                self._failure_count = 0
                logger.info(f'DSPy optimization done, updated {len(new_prompts)} prompts')
        except TimeoutError:
            logger.warning('DSPy optimization timed out after 10 minutes')
            self._failure_count += 1
        except Exception as e:
            if isinstance(e, ExceptionGroup):
                logger.warning(f'DSPy optimization failed (ExceptionGroup): {e}')
            else:
                logger.warning(f'DSPy optimization failed: {e}')
            self._failure_count += 1
        if self._failure_count >= self._max_failures:
            self._circuit_open_until = time.time() + self._circuit_duration
            logger.warning(f'[DSPy] Circuit breaker opened after {self._failure_count} failures')

    def _filter_training_examples(self, examples: list[tuple]) -> list[tuple]:
        """Filter examples by quality heuristics."""
        filtered = []
        for query, result in examples:
            if len(query) < 20 or len(result) < 50:
                continue
            if query.count('?') > 3:
                continue
            if 'error' in result.lower() or 'failed' in result.lower():
                continue
            if len(result) / max(1, len(query)) < 0.5:
                continue
            filtered.append((query, result))
        return filtered[:50]

    def _dspy_optimize_mipro(self, examples: list[tuple]) -> dict:
        """Synchronní DSPy optimalizace s MIPROv2."""
        try:
            import dspy
            from dspy.teleprompt import MIPROv2
            trainset = [dspy.Example(query=q, answer=a).with_inputs('query') for q, a in examples[:50]]

            class OSINTAnalyze(dspy.Signature):
                """Analyze OSINT query and return structured result."""
                query: str = dspy.InputField()
                answer: str = dspy.OutputField()
            program = dspy.Predict(OSINTAnalyze)
            from hledac.universal.brain.dspy_service import get_hermes_dspy_lm
            lm = get_hermes_dspy_lm()
            if lm is None:
                logger.warning('DSPy MIPROv2: Hermes3DSPyLM unavailable — skipping optimization')
                return {}

            def _osint_metric(_example, pred, _trace=None):
                answer = str(pred.answer)
                if len(answer) < 50:
                    return 0.0
                try:
                    data = _msgspec_decode(answer)
                    fields = data.keys() if isinstance(data, dict) else []
                    field_bonus = min(1.0, len(fields) / 3)
                    return 0.7 + 0.3 * field_bonus
                except Exception:
                    return 0.3 if len(answer) > 100 else 0.0
            if not trainset or not trainset:
                logger.warning('DSPy MIPROv2: trainset is empty — skipping optimization')
                return {}
            try:
                with dspy.context(lm=lm):
                    optimizer = MIPROv2(metric=_osint_metric, auto=None, num_candidates=2)
                    optimized = optimizer.compile(program, trainset=trainset, num_trials=2, minibatch=False)
            except (AttributeError, TypeError):
                cast(Any, program).lm = lm
                optimizer = MIPROv2(metric=_osint_metric, auto=None, num_candidates=2)
                optimized = optimizer.compile(program, trainset=trainset, num_trials=2, minibatch=False)
            instr = None
            try:
                instr = str(optimized.predictors()[0].signature.instructions)
            except (AttributeError, IndexError):  # noqa: BLE001
                pass
            if instr is None:
                try:
                    predictor = list(optimized.named_predictors())[0][1]
                    instr = str(predictor.signature.instructions)
                except (AttributeError, IndexError, StopIteration):  # noqa: BLE001
                    pass
            if instr is None:
                try:
                    instr = str(optimized.signature)
                except AttributeError:  # noqa: BLE001
                    pass
            if instr is None:
                logger.warning('DSPy optimizer: could not extract instructions from optimized module — using default fallback')
                instr = 'optimized:mipro_fallback'
            return {'analysis:medium': instr, 'summarization:medium': instr, 'extraction:medium': instr}
        except Exception as e:
            logger.warning(f'MIPROv2 failed: {e}')
            return {}

    def get_prompt(self, task: str, context: dict) -> str:
        """Vrátí optimalizovaný prompt pro daný úkol a kontext."""
        complexity = context.get('complexity', 'medium')
        key = f'{task}:{complexity}'
        if key in self._optimized_prompts:
            return self._optimized_prompts[key]
        return self._default_prompt(task)

    def _default_prompt(self, task: str) -> str:
        """OSINT-specifické výchozí prompty."""
        templates = {'analysis': 'You are an OSINT analyst. Analyze this query and identify:\n1. Key entities (people, organizations, locations)\n2. Information gaps\n3. Recommended sources\n4. Potential verification challenges\n\nQuery: {query}\n\nRespond in structured JSON format.', 'summarization': 'Summarize the following OSINT findings:\n- Focus on verified facts\n- Note contested information\n- Include source credibility assessment\n\nFindings: {text}\n\nProvide a concise summary with confidence levels.', 'extraction': 'Extract entities and relationships from this OSINT content:\n- People, organizations, locations\n- Dates and temporal relationships\n- Claims and their sources\n- Contradictions or uncertainties\n\nContent: {text}\n\nOutput as structured JSON with confidence scores.'}
        return templates.get(task, 'Process the following: {input}')

    def record_performance(self, task: str, score: float):
        """Zaznamená výkon pro auto‑rollback."""
        self._performance_history[task].append(score)
        if len(self._performance_history[task]) > 20:
            self._performance_history[task] = self._performance_history[task][-20:]

    def check_auto_rollback(self, task: str) -> bool:
        """Zkontroluje, zda je třeba provést auto‑rollback."""
        history = self._performance_history.get(task, [])
        if len(history) < 10:
            return False
        recent_avg = sum(history[-5:]) / 5
        older_avg = sum(history[-10:-5]) / 5
        if older_avg > 0 and (older_avg - recent_avg) / older_avg > self._rollback_threshold:
            current_ver = self._current_version.get(task, 1)
            if current_ver > 1:
                logger.warning(f'[DSPy] Auto-rollback triggered for {task}')
                return self.rollback(task, current_ver - 1)
        return False

    def rollback(self, task: str, version: int) -> bool:
        """Vrátí prompt na předchozí verzi."""
        for v in self._prompt_versions[task]:
            if v['version'] == version:
                self._optimized_prompts[f'{task}:medium'] = v['prompt']
                logger.info(f'Rolled back {task} to version {version}')
                return True
        return False

    async def start(self):
        has_mipro, ver = _check_dspy_version()
        logger.info(f'[DSPy] version={ver}, has_mipro={has_mipro}')
        if os.getenv('HLEDAC_DSPY_OPTIMIZE') != '1':
            logger.info('[DSPy] HLEDAC_DSPY_OPTIMIZE not set — skipping optimization loop')
            return
        if self._brain is None:
            logger.warning('[DSPy] brain_manager=None — task NOT tracked')
            self._task = safe_create_task(self._optimize_loop(), name='dspy_optimizer')
            return
        self._task = safe_create_task(self._optimize_loop(), name='dspy_optimizer')
        if hasattr(self._brain, '_orch') and hasattr(self._brain._orch, '_bg_tasks'):
            self._brain._orch._bg_tasks.add(self._task)
            self._task.add_done_callback(self._brain._orch._bg_tasks.discard)

def load_optimized_prompts() -> dict:
    """
    Lazy load DSPy optimalizované prompty z cache.

    Vrací:
        dict: {task_key: prompt_string} — prázdný dict pokud cache neexistuje
              nebo optimalizace neproběhla.
    """
    cache_path = Path.home() / '.hledac' / 'dspy_cache.json'
    if not cache_path.exists():
        return {}
    try:
        if ORJSON_AVAILABLE:
            import orjson
            with open(cache_path, 'rb') as f:
                data = orjson.loads(f.read())
        else:
            with open(cache_path, 'rb') as f:
                data = _msgspec_decode(f.read())
        prompts = data.get('prompts', {})
        return {k: v for k, v in prompts.items() if v and isinstance(v, str)}
    except Exception:
        return {}
_COMPILED_DIR = Path(__file__).resolve().parent / 'compiled'
_LEGACY_COMPILED_DIR = Path.home() / '.hledac' / 'dspy'
_PROGRAM_CLASSES: dict[str, str] = {'dark_query': 'DarkQueryProgram', 'hypothesis_generator': 'HypothesisGeneratorProgram', 'hypothesis_ranker': 'HypothesisRankProgram'}

def _dspy_available() -> bool:
    """Return True if ``dspy`` is importable. Lazy probe (no top-level import)."""
    try:
        import importlib.util
        return importlib.util.find_spec('dspy') is not None
    except Exception:
        return False

def _read_compiled_state(path: Path) -> dict | None:
    """Read a compiled-program JSON file. Returns the parsed dict, or ``None``."""
    try:
        if not path.exists():
            return None
        text = path.read_text(encoding='utf-8')
        if not text.strip():
            return None
        return _msgspec_decode(text)
    except Exception as e:
        logger.warning('Failed to read compiled program at %s: %s', path, e)
        return None

def _import_program_class(name: str) -> Any | None:
    """Import a program class by short name (e.g. ``HypothesisGeneratorProgram``).

    Lazy: import is performed on first call only. Returns ``None`` on
    any failure (fail-soft). The module is force-reloaded so the
    module-level ``HLEDAC_ENABLE_DSPY`` flag re-evaluates against the
    current environment on every call (otherwise a process that started
    with the gate disabled cannot recover when the gate is flipped on
    mid-run, which matters for test fixtures and for the legacy-cache
    fallback path).
    """
    try:
        import importlib
        mod = importlib.import_module('hledac.universal.brain.dspy_programs')
        importlib.reload(mod)
        cls = getattr(mod, name, None)
        return cls
    except Exception as e:
        logger.warning('Failed to import program class %s: %s', name, e)
        return None

def _instantiate_uncompiled(name: str) -> Any | None:
    """Build a fresh uncompiled program instance by name (fail-soft)."""
    class_name = _PROGRAM_CLASSES.get(name)
    if class_name is None:
        logger.debug('Unknown DSPy program name: %s', name)
        return None
    cls = _import_program_class(class_name)
    if cls is None:
        return None
    try:
        return cls()
    except Exception as e:
        logger.warning('Failed to instantiate %s: %s', class_name, e)
        return None

def _inject_demos(program: Any, demos: list[dict]) -> Any:
    """Attach a list of demo dicts to ``program.program.demos`` (DSPy convention).

    DSPy's ``BootstrapFewShot`` compiled modules store their tuned
    few-shot demonstrations on ``module.demos`` (a list of
    ``dspy.Example``). When the program is reloaded from JSON we
    reconstruct the demos and re-bind them.

    Returns the program unchanged on any failure (fail-soft).
    """
    if not demos:
        return program
    try:
        import dspy
        examples = []
        for demo in demos:
            try:
                ex = dspy.Example(**demo)
                input_fields = ('research_query', 'rag_context', 'graph_summary', 'reward_context', 'existing_hypotheses', 'ioc_brief', 'available_transports', 'max_queries', 'hypotheses', 'sprint_context')
                present = [k for k in input_fields if k in demo]
                if present:
                    ex = ex.with_inputs(*present)
                examples.append(ex)
            except Exception:
                continue
        target = getattr(program, 'program', program)
        try:
            target.demos = examples
        except Exception:  # noqa: BLE001
            pass
        return program
    except Exception as e:
        logger.debug('Failed to inject demos: %s', e)
        return program

def load_compiled_program(name: str) -> Any | None:
    """Load a compiled DSPy program by short name.

    Resolution order (fail-soft at every step):

    1. ``brain/compiled/{name}.json`` (project-local, canonical new path)
    2. ``~/.hledac/dspy/{name}.json`` (legacy cache, kept for back-compat)
    3. Fresh uncompiled program instance (``HypothesisGeneratorProgram()``,
       ``DarkQueryProgram()``, …) — always returns a usable object when
       the name is known and DSPy is installed
    4. ``None`` — only when DSPy is unavailable or the name is unknown

    The returned program is always ready to call ``.forward(**kwargs)``
    on — either with compiled demonstrations baked in, or in the
    default zero-shot configuration.

    M1 invariant: no top-level MLX / DSPy / Hermes3 import — every
    dependency is probed lazily inside this function.
    """
    if not _dspy_available():
        return None
    state = _read_compiled_state(_COMPILED_DIR / f'{name}.json')
    if state is None:
        state = _read_compiled_state(_LEGACY_COMPILED_DIR / f'{name}.json')
    if state is not None:
        program = _instantiate_uncompiled(name)
        if program is not None:
            demos = state.get('demos') or []
            program = _inject_demos(program, demos)
            logger.info("Loaded compiled DSPy program '%s' (%d demos)", name, len(demos) if isinstance(demos, list) else 0)
            return program
    return _instantiate_uncompiled(name)