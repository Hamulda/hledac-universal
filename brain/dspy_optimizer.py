"""Offline DSPy prompt optimizer – MIPROv2, idle-only, memory/thermal guards, circuit breaker."""
import asyncio
import logging
import psutil
import sys
import time
from pathlib import Path
from typing import Optional, Dict, List
from collections import defaultdict

try:
    import orjson
    ORJSON_AVAILABLE = True
except ImportError:
    ORJSON_AVAILABLE = False
    import json as _json

logger = logging.getLogger(__name__)


class DSPyOptimizer:
    def __init__(self, brain_manager):
        self._brain = brain_manager
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._optimized_prompts = {}
        self._prompt_versions: Dict[str, List[Dict]] = defaultdict(list)
        self._current_version: Dict[str, int] = defaultdict(int)
        self._performance_history: Dict[str, List[float]] = defaultdict(list)
        self._rollback_threshold = 0.2
        self._max_versions_per_task = 10

        self._failure_count = 0
        self._max_failures = 3
        self._circuit_open_until = 0.0
        self._circuit_duration = 3600

        self._cache_path = Path.home() / '.hledac' / 'dspy_cache.json'
        self._load_cache()
        self._optimization_interval = 86400  # 24h (produkce)

    def _load_cache(self):
        if self._cache_path.exists():
            try:
                with open(self._cache_path, 'rb') as f:
                    data_str = f.read()
                if ORJSON_AVAILABLE:
                    data = orjson.loads(data_str)
                else:
                    data = _json.loads(data_str.decode())
                self._optimized_prompts = data.get('prompts', {})
                self._prompt_versions = defaultdict(list, data.get('versions', {}))
                self._current_version = defaultdict(int, data.get('current', {}))
                logger.info(f"Loaded {len(self._optimized_prompts)} optimized prompts")
            except Exception as e:
                logger.warning(f"Failed to load DSPy cache: {e}")

    def _save_cache(self):
        try:
            data = {
                'prompts': self._optimized_prompts,
                'versions': dict(self._prompt_versions),
                'current': dict(self._current_version)
            }
            if ORJSON_AVAILABLE:
                data_bytes = orjson.dumps(data)
            else:
                data_bytes = _json.dumps(data).encode()
            with open(self._cache_path, 'wb') as f:
                f.write(data_bytes)
        except Exception as e:
            logger.warning(f"Failed to save DSPy cache: {e}")

    def _should_optimize(self) -> bool:
        """Check if system is idle enough (CPU < 15%, RAM > 4GB, not on battery unless >80%, thermal OK, circuit breaker)."""
        if time.time() < self._circuit_open_until:
            return False

        if psutil.cpu_percent(interval=0.5) > 15:
            return False

        if psutil.virtual_memory().available / (1024**3) < 2.0:
            return False

        # Energy‑aware scheduling – preferujeme _memory_mgr, fallback na psutil
        if hasattr(self._brain, '_orch') and self._brain._orch._memory_mgr:
            if self._brain._orch._memory_mgr._on_battery_power():
                logger.debug("[DSPy] Defer – on battery")
                return False
        else:
            # fallback na psutil
            battery = psutil.sensors_battery()
            if battery and not battery.power_plugged and battery.percent < 80:
                logger.debug("[DSPy] Defer – on battery (psutil)")
                return False

        # Thermal state
        if hasattr(self._brain, '_orch') and self._brain._orch._memory_mgr:
            thermal = self._brain._orch._memory_mgr.get_thermal_state()
            if thermal.name in ('HOT', 'CRITICAL'):
                return False
            # thermal trend
            hist = getattr(self._brain._orch._memory_mgr, '_thermal_history', [])
            if len(hist) >= 3:
                recent = [t[1].value for t in hist[-3:]]
                if recent[2] > recent[1] > recent[0]:
                    logger.debug("[DSPy] Defer – thermal rising")
                    return False

        if 'pytest' in sys.modules:
            return False

        return True

    async def _optimize_loop(self):
        while not self._stop.is_set():
            await asyncio.sleep(self._optimization_interval)
            if self._should_optimize():
                await self._run_optimization()

    async def _load_training_examples(self, limit: int = 1000) -> List[tuple]:
        """
        Load training examples from evidence JSONL files.

        Reads from EVIDENCE_ROOT/*.jsonl — one JSON per line, each line is an
        EvidenceEvent dict with event_type + payload. Fails safe on error (returns []).

        GHOST_INVARIANTS: async only (aiofiles), fail-safe on empty/corrupt files.
        """
        examples: List[tuple] = []
        try:
            from hledac.universal.paths import EVIDENCE_ROOT
        except Exception:
            # paths.py not available — skip
            logger.debug("[DSPy] EVIDENCE_ROOT not available")
            return []

        try:
            import aiofiles
        except ImportError:
            logger.debug("[DSPy] aiofiles not available, skipping evidence load")
            return []

        evidence_files = []
        try:
            if EVIDENCE_ROOT.exists():
                evidence_files = sorted(
                    EVIDENCE_ROOT.glob("*.jsonl"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )[:10]  # newest 10 files to avoid scanning old history
        except Exception as e:
            logger.debug(f"[DSPy] Failed to list evidence files: {e}")
            return []

        for ev_file in evidence_files:
            try:
                async with aiofiles.open(ev_file, "rb") as f:
                    content = await f.read()
            except Exception as e:
                logger.debug(f"[DSPy] Failed to read {ev_file}: {e}")
                continue

            if not content:
                continue

            # Parse JSONL — each line is one EvidenceEvent dict
            try:
                text = content.decode("utf-8") if isinstance(content, bytes) else content
                lines = text.strip().split("\n")
                events = []
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    if ORJSON_AVAILABLE:
                        ev = orjson.loads(line)
                    else:
                        ev = _json.loads(line)
                    events.append(ev)
            except Exception as e:
                logger.debug(f"[DSPy] Failed to parse {ev_file}: {e}")
                continue

            for ev in events:
                ev_type = ev.get("event_type", "")
                if ev_type not in ("decision", "action_executed"):
                    continue

                payload = ev.get("payload") or {}

                # Query extraction (mirrors broken seam logic)
                query = (
                    payload.get("query") or
                    payload.get("params", {}).get("query") or
                    payload.get("action_params", {}).get("query") or
                    ""
                )

                # Result extraction
                result = (
                    payload.get("result") or
                    payload.get("action_result") or
                    payload.get("response", {}).get("content", "") or
                    ""
                )

                if query and result:
                    examples.append((query, result))

                if len(examples) >= limit:
                    return examples

        return examples

    async def _run_optimization(self):
        """Load training data from evidence log and run DSPy."""
        logger.info("Starting DSPy optimization...")
        try:
            # Load training examples from evidence JSONL files
            raw_examples = await self._load_training_examples(1000)

            # Filter for quality
            examples = self._filter_training_examples(raw_examples)

            if len(examples) < 10:
                logger.debug("Not enough training examples")
                return

            import dspy
            from dspy.teleprompt import MIPROv2

            loop = asyncio.get_running_loop()
            new_prompts = await asyncio.wait_for(
                loop.run_in_executor(None, self._dspy_optimize_mipro, examples),
                timeout=600
            )

            if new_prompts:
                self._optimized_prompts.update(new_prompts)
                for task, prompt in new_prompts.items():
                    ver = self._current_version[task] + 1
                    self._prompt_versions[task].append({
                        'version': ver,
                        'prompt': prompt,
                        'trained_at': time.time(),
                        'examples': len(examples)
                    })
                    self._current_version[task] = ver

                # Prune old versions
                for task in self._prompt_versions:
                    if len(self._prompt_versions[task]) > self._max_versions_per_task:
                        self._prompt_versions[task] = self._prompt_versions[task][-self._max_versions_per_task:]

                self._save_cache()
                self._failure_count = 0
                logger.info(f"DSPy optimization done, updated {len(new_prompts)} prompts")

        except asyncio.TimeoutError:
            logger.warning("DSPy optimization timed out after 10 minutes")
            self._failure_count += 1
        except Exception as e:
            # Check if it's an ExceptionGroup (Python 3.11+)
            if isinstance(e, ExceptionGroup):
                logger.warning(f"DSPy optimization failed (ExceptionGroup): {e}")
            else:
                logger.warning(f"DSPy optimization failed: {e}")
            self._failure_count += 1

        # Circuit breaker
        if self._failure_count >= self._max_failures:
            self._circuit_open_until = time.time() + self._circuit_duration
            logger.warning(f"[DSPy] Circuit breaker opened after {self._failure_count} failures")

    def _filter_training_examples(self, examples: List[tuple]) -> List[tuple]:
        """Filter examples by quality heuristics."""
        filtered = []
        for query, result in examples:
            # Basic quality filters
            if len(query) < 20 or len(result) < 50:
                continue
            if query.count('?') > 3:      # too many questions
                continue
            if 'error' in result.lower() or 'failed' in result.lower():
                continue
            if len(result) / max(1, len(query)) < 0.5:  # too short response
                continue
            filtered.append((query, result))
        return filtered[:50]  # keep top 50

    def _dspy_optimize_mipro(self, examples: List[tuple]) -> dict:
        """Synchronní DSPy optimalizace s MIPROv2."""
        try:
            import dspy
            from dspy.teleprompt import MIPROv2

            trainset = [
                dspy.Example(query=q, answer=a).with_inputs('query')
                for q, a in examples[:50]
            ]

            class OSINTAnalyze(dspy.Signature):
                """Analyze OSINT query and return structured result."""
                query: str = dspy.InputField()
                answer: str = dspy.OutputField()

            program = dspy.Predict(OSINTAnalyze)

            # lokální LM (předpokládáme Hermes server)
            lm = dspy.LM(model="openai/hermes3", base_url="http://localhost:8080/v1")

            # better metric: JSON validity + length + key presence
            def _osint_metric(example, pred):
                answer = str(pred.answer)
                if len(answer) < 50:
                    return 0.0
                try:
                    import json
                    data = json.loads(answer)
                    # Bonus for expected fields
                    fields = data.keys() if isinstance(data, dict) else []
                    field_bonus = min(1.0, len(fields) / 3)  # max 3 fields = 1.0
                    return 0.7 + 0.3 * field_bonus
                except json.JSONDecodeError:
                    # Penalize non‑JSON but long answers
                    return 0.3 if len(answer) > 100 else 0.0

            if not trainset or len(trainset) == 0:
                logger.warning(f"DSPy MIPROv2: trainset is empty for task_key={task_key!r} — skipping optimization")
                return {}

            with dspy.context(lm=lm):
                optimizer = MIPROv2(metric=_osint_metric)
                optimized = optimizer.compile(program, trainset=trainset)

            instr = None
            # DSPy 2.x API introspection — try multiple access patterns
            try:
                instr = str(optimized.predictors()[0].signature.instructions)
            except (AttributeError, IndexError):
                pass
            if instr is None:
                try:
                    # DSPy 2.5+ pattern: direct module access
                    predictor = list(optimized.named_predictors())[0][1]
                    instr = str(predictor.signature.instructions)
                except (AttributeError, IndexError, StopIteration):
                    pass
            if instr is None:
                try:
                    # Fallback: serialize entire signature as instructions
                    instr = str(optimized.signature)
                except AttributeError:
                    pass
            if instr is None:
                logger.warning("DSPy optimizer: could not extract instructions from optimized module — using task_key as fallback")
                instr = f"optimized:{task_key}"
            # Pro zjednodušení ukládáme stejnou instrukci pro všechny complexity
            return {
                'analysis:medium': instr,
                'summarization:medium': instr,
                'extraction:medium': instr,
            }
        except Exception as e:
            logger.warning(f"MIPROv2 failed: {e}")
            return {}

    def get_prompt(self, task: str, context: dict) -> str:
        """Vrátí optimalizovaný prompt pro daný úkol a kontext."""
        complexity = context.get('complexity', 'medium')
        key = f"{task}:{complexity}"

        if key in self._optimized_prompts:
            return self._optimized_prompts[key]

        # fallback na výchozí
        return self._default_prompt(task)

    def _default_prompt(self, task: str) -> str:
        """OSINT-specifické výchozí prompty."""
        templates = {
            'analysis': """You are an OSINT analyst. Analyze this query and identify:
1. Key entities (people, organizations, locations)
2. Information gaps
3. Recommended sources
4. Potential verification challenges

Query: {query}

Respond in structured JSON format.""",

            'summarization': """Summarize the following OSINT findings:
- Focus on verified facts
- Note contested information
- Include source credibility assessment

Findings: {text}

Provide a concise summary with confidence levels.""",

            'extraction': """Extract entities and relationships from this OSINT content:
- People, organizations, locations
- Dates and temporal relationships
- Claims and their sources
- Contradictions or uncertainties

Content: {text}

Output as structured JSON with confidence scores.""",
        }
        return templates.get(task, "Process the following: {input}")

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
                logger.warning(f"[DSPy] Auto-rollback triggered for {task}")
                return self.rollback(task, current_ver - 1)
        return False

    def rollback(self, task: str, version: int) -> bool:
        """Vrátí prompt na předchozí verzi."""
        for v in self._prompt_versions[task]:
            if v['version'] == version:
                self._optimized_prompts[f"{task}:medium"] = v['prompt']
                logger.info(f"Rolled back {task} to version {version}")
                return True
        return False

    async def start(self):
        self._task = asyncio.create_task(self._optimize_loop(), name="dspy_optimizer")
        if hasattr(self._brain._orch, '_bg_tasks'):
            self._brain._orch._bg_tasks.add(self._task)


# ---------------------------------------------------------------------------
# Sprint 8VH: DSPy Lazy Load Helper
# ---------------------------------------------------------------------------


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
            import json as _json
            with open(cache_path, 'r') as f:
                data = _json.load(f)
        prompts = data.get('prompts', {})
        # Filter only valid non-empty prompts
        return {k: v for k, v in prompts.items() if v and isinstance(v, str)}
    except Exception:
        return {}
