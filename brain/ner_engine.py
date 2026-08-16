"""
NER Engine — Named Entity Recognition using GLiNER-X on CPU.

Uses knowledgator/gliner-relex-large-v0.5 model for joint NER + RE extraction




with lazy loading and explicit CPU-only mode.

Alternative: utils/entity_extractor.py (regex-based, faster but less accurate)


Usage:
    from hledac.universal.brain.ner_engine import NEREngine, get_ner_engine

Features:
- Lazy model loading (loaded on first use)
- CPU-only inference (map_location="cpu")
- Batch and single prediction support
- Explicit unload for memory release
- ANE acceleration via NaturalLanguage framework (PyObjC)
- CoreML NER model fallback
"""
from __future__ import annotations
import asyncio
import orjson as json
import logging
import os
import subprocess
import sys
import tempfile
import threading
from collections import deque
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from transformers import PreTrainedModel

from operator import attrgetter, itemgetter
import msgspec
from compat.msgspec_gc_compat import Struct
from hledac.universal.utils.msgspec_json import decode as _msgspec_decode, encode as _msgspec_encode
from pathlib import Path
_TORCH_AVAILABLE = False
_torch_module = None

def _get_torch():
    """Lazy torch accessor - imports torch only when first needed."""
    global _torch_module, _TORCH_AVAILABLE
    if _torch_module is None:
        try:
            import torch
            _torch_module = torch
            _TORCH_AVAILABLE = True
        except ImportError:
            _torch_module = None
            _TORCH_AVAILABLE = False
    return _torch_module
logger = logging.getLogger(__name__)
_NL_AVAILABLE = False
try:
    import NaturalLanguage
    _NL_AVAILABLE = True
except ImportError:  # noqa: BLE001
    pass
MAX_STRICT_TEXT_LENGTH = 10000
MAX_STRICT_LABELS = 5
MAX_STRICT_TEXTS = 3

# ---------------------------------------------------------------------------
# Persistent Worker Pool for GLiNER Subprocess
# ---------------------------------------------------------------------------
# Long-running subprocess that loads GLiNER once and processes via JSONL stdin/stdout.
# Survives 1000+ requests without reloading model. ~50-150ms startup (no torch/GLiNER reload).
# M1 8GB: single worker = ~1GB resident for GLiNER model.


class _NERPersistentWorker:
    """
    Long-running GLiNER subprocess — loads model once, handles 1000+ requests.

    Fallback: If worker dies or is unavailable, falls back to temporary subprocess
    (original behavior preserved for robustness).
    """

    __slots__ = tuple(
        (
            "_closed",
            "_lock",
            "_proc",
            "_reader_task",
            "_stderr_task",
            "_stderr_buffer",
            "_stdout_reader",
            "_stdin_lock",
            "_model_name",
            "_ready_event",
            "_response_queues",
            "_request_id",
            "_started",
    )
    )

    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = threading.Lock()
        self._stdin_lock = asyncio.Lock()
        self._closed = False
        self._started = False
        self._ready_event: asyncio.Event | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_buffer: list[bytes] = []
        self._stdout_reader: asyncio.StreamReader | None = None
        self._response_queues: dict[int, asyncio.Queue[dict | None]] = {}
        self._request_id = 0

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> bool:
        """Start the persistent worker subprocess. Returns True if successful."""
        if self._closed:
            return False
        with self._lock:
            if self._started and self.is_running:
                return True
            # Reset state if previous start failed or worker died
            self._started = True
            self._closed = False

        self._ready_event = asyncio.Event()
        self._response_queues.clear()
        self._request_id = 0
        self._stderr_buffer.clear()  # Bounded: always clear before reuse

        try:
            self._proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "brain.ner_engine_worker",
                "--model",
                self._model_name,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "TOKENIZERS_PARALLELISM": "false"},
                limit=10 * 1024 * 1024,  # 10 MB stdout buffer
    )
            self._stdout_reader = self._proc.stdout

            # Start stderr collector (non-blocking)
            async def _read_stderr() -> None:
                proc_stderr: asyncio.StreamReader | None = self._proc.stderr
                if proc_stderr is None:
                    return
                try:
                    while not self._closed:
                        try:
                            async with asyncio.timeout(1.0):
                                line = await proc_stderr.readline()
                        except TimeoutError:
                            continue
                        except Exception:
                            break
                except Exception:  # noqa: BLE001
                    pass

            # Start stdout reader (parses responses and dispatches to correct queue)
            async def _read_stdout() -> None:
                proc_stdout: asyncio.StreamReader | None = self._proc.stdout
                if proc_stdout is None:
                    return
                ready_event: asyncio.Event | None = self._ready_event
                try:
                    while not self._closed:
                        try:
                            line = await safe_wait_for(proc_stdout.readline(), timeout=5.0)
                            if not line:
                                break
                            if line.startswith(b"READY"):
                                if ready_event is not None:
                                    ready_event.set()
                                continue
                            if line.startswith(b"LOAD_ERROR:"):
                                logger.warning(f"NER worker load error: {line.decode().strip()}")
                                if ready_event is not None:
                                    ready_event.set()
                                continue
                            try:
                                response = json.loads(line)
                            except ValueError:
                                logger.warning(f"NER worker invalid JSON: {line[:100]}")
                                continue
                            rid = response.pop("_request_id", None)
                            if rid is not None and rid in self._response_queues:
                                self._response_queues[rid].put_nowait(response)
                            else:
                                logger.warning(f"NER worker unexpected response: {line[:100]}")
                        except asyncio.TimeoutError:
                            if self._proc is None or self._proc.returncode is not None:
                                break
                            continue
                        except Exception as e:
                            if not self._closed:
                                logger.warning(f"NER worker stdout read error: {e}")
                            break
                except Exception as e:
                    if not self._closed:
                        logger.warning(f"NER worker stdout reader died: {e}")

            self._stderr_task = asyncio.create_task(_read_stderr())
            self._reader_task = asyncio.create_task(_read_stdout())

            # Wait for READY signal with timeout
            try:
                async with asyncio.timeout(60.0):
                    await self._ready_event.wait()
            except TimeoutError:
                logger.warning("NER worker startup timeout — not ready within 60s")
                self._emergency_shutdown()
                return False
            logger.debug(f"NER persistent worker started (model={self._model_name})")
            return True

        except Exception as e:
            logger.warning(f"NER persistent worker start failed: {e}")
            self._emergency_shutdown()
            return False

    async def _ensure_started(self) -> bool:
        """Ensure worker is running, start if needed."""
        if self.is_running:
            return True
        return await self.start()

    def _emergency_shutdown(self) -> None:
        """Best-effort shutdown on error."""
        try:
            if self._reader_task:
                self._reader_task.cancel()
                self._reader_task = None
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._stderr_task:
                self._stderr_task.cancel()
                self._stderr_task = None
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._proc:
                self._proc.terminate()
        except Exception:  # noqa: BLE001
            pass
        self._proc = None
        self._started = False

    async def extract(
        self, texts: list[str], labels: list[str], threshold: float, timeout: float = 120.0
    ) -> list[list[dict]] | None:
        """
        Send extraction request to worker and wait for response.

        Returns None if worker unavailable (caller should fall back to temp subprocess).
        """
        if not await self._ensure_started():
            return None

        # Check for stderr errors first
        if self._stderr_buffer:
            error_lines = [b"".join(self._stderr_buffer).decode("utf-8", errors="replace")][:500]
            logger.debug(f"NER worker stderr: {error_lines}")

        request_id = self._request_id
        self._request_id += 1
        # S1-10 FIX: was Queue(maxsize=1) — producer blocks if consumer is slow to read.
        # S1-10: size 16 gives sub-process a small response buffer so it doesn't stall.
        queue: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=16)
        self._response_queues[request_id] = queue

        request = {
            "_request_id": request_id,
            "texts": texts,
            "labels": labels,
            "threshold": threshold,
        }

        try:
            async with self._stdin_lock:
                if self._proc is None or self._proc.stdin is None:
                    return None
                try:
                    self._proc.stdin.write(json.dumps(request) + b"\n")
                    async with asyncio.timeout(5.0):
                        await self._proc.stdin.drain()
                except BrokenPipeError:
                    logger.warning("NER worker stdin BrokenPipe — restarting")
                    self._emergency_shutdown()
                    return None

            response: dict | None = None
            try:
                async with asyncio.timeout(timeout):
                    response = await queue.get()
            except TimeoutError:
                logger.warning(f"NER worker request timeout ({timeout}s) — killing and restarting")
                self._emergency_shutdown()
                return None

            if response is None:
                return None

            if not response.get("success", False):
                error = response.get("error", "Unknown error")
                logger.warning(f"NER worker request failed: {error}")
                return None

            return response.get("results")

        finally:
            self._response_queues.pop(request_id, None)

    def close(self) -> None:
        """Synchronously close the worker (for use from non-async contexts)."""
        with self._lock:
            self._closed = True

        if self._reader_task:
            self._reader_task.cancel()
            self._reader_task = None

        if self._stderr_task:
            self._stderr_task.cancel()
            self._stderr_task = None

        if self._proc:
            try:
                self._proc.terminate()
            except Exception:  # noqa: BLE001
                pass
            self._proc = None

    async def aclose(self) -> None:
        """Gracefully close the worker."""
        self.close()
        if self._reader_task:
            try:
                await asyncio.wait_for(asyncio.shield(self._reader_task), timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):  # noqa: BLE001
                pass
            self._reader_task = None

        if self._stderr_task:
            try:
                await asyncio.wait_for(asyncio.shield(self._stderr_task), timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):  # noqa: BLE001
                pass
            self._stderr_task = None

        if self._proc:
            try:
                async with self._stdin_lock:
                    if self._proc.stdin:
                        self._proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                async with asyncio.timeout(5.0):
                    await self._proc.wait()
            except TimeoutError:
                self._proc.terminate()
                try:
                    async with asyncio.timeout(3.0):
                        await self._proc.wait()
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001
                pass
            self._proc = None


# Singleton persistent worker (single GLiNER process for all NEREngine instances)
_ner_worker: _NERPersistentWorker | None = None
_ner_worker_lock = threading.Lock()


def _get_ner_worker(model_name: str = "knowledgator/gliner-relex-large-v0.5") -> _NERPersistentWorker:
    """Get or create the singleton NER persistent worker."""
    global _ner_worker
    with _ner_worker_lock:
        if _ner_worker is None:
            _ner_worker = _NERPersistentWorker(model_name)
        return _ner_worker


async def _close_ner_worker() -> None:
    """Close the singleton NER worker (call on shutdown)."""
    global _ner_worker
    with _ner_worker_lock:
        if _ner_worker is not None:
            await _ner_worker.aclose()
            _ner_worker = None


class NEREngine:
    """
    Engine pro Named Entity Recognition pomocí GLiNER-X.

    Features:
    - Lazy loading modelu (načte se až při prvním použití)
    - CPU-only inference (map_location="cpu")
    - Podpora batch i single prediction
    - Explicitní unload pro uvolnění paměti
    - Sprint 76: ANE acceleration via NaturalLanguage framework
    - Sprint 76: CoreML NER model fallback
    """
    __slots__ = tuple(('_ane_predictions', '_coreml_ner_model', '_initialized', '_lock', '_mlx_gliner2_available', '_mlx_gliner2_extractor', '_model', '_nl_available', 'model_name'))

    def __init__(self, model_name: str='knowledgator/gliner-relex-large-v0.5'):
        self.model_name = model_name
        self._model: PreTrainedModel | Any | None = None  # type: ignore[assignment]
        self._lock = threading.RLock()
        self._initialized = False
        self._nl_available = _NL_AVAILABLE
        self._coreml_ner_model = None
        self._ane_predictions = 0
        self._mlx_gliner2_available = False
        self._mlx_gliner2_extractor = None

    async def _load_mlx_gliner2(self) -> bool:
        """
        Lazy load mlx-gliner2 extractor (běží na Metal GPU / ANE).

        ISSUE-B3: Profile-gated — only loads when HLEDAC_ENABLE_GLINER2=1.
        Without the flag, mlx-gliner2 is never imported (RAM budget on M1 8GB).
        """
        # ISSUE-B3: Guard — mlx-gliner2 adds ~500MB resident; only load when profile-enabled
        if os.getenv('HLEDAC_ENABLE_GLINER2', '1') != '1':
            logger.debug('mlx-gliner2 skipped (HLEDAC_ENABLE_GLINER2 != 1)')
            return False
        if self._mlx_gliner2_extractor is not None:
            return True
        try:
            import mlx_gliner2
            model_path = os.environ.get('MLX_GLINER2_MODEL', str(Path.home() / '.hledac' / 'models' / 'fastino_gliner2-base-v1'))
            self._mlx_gliner2_extractor = mlx_gliner2.GLiNER2.from_pretrained(model_path)
            self._mlx_gliner2_available = True
            logger.info('mlx-gliner2 loaded (Metal GPU / ANE)')
            return True
        except ImportError:
            logger.debug('mlx-gliner2 not installed')
            return False
        except Exception as e:
            logger.debug(f'mlx-gliner2 load failed: {e}')
            return False

    def _mlx_gliner2_extract(self, text: str, labels: list[str], threshold: float=0.5) -> list[dict]:
        """
        Synchronní mlx-gliner2 inference na Metal GPU.

        API SPRINT F320: mlx_gliner2.extract_entities vrací
        List[Dict[str,Any]] s keys: text, label, score, start, end.
        Starý dict-of-lists format (result.items()) je zastaralý.
        """
        if self._mlx_gliner2_extractor is None:
            return []
        try:
            # SPRINT F320: správný API — List[Dict] s text/label/score/start/end
            result: list[dict] = self._mlx_gliner2_extractor.extract_entities(
                text, labels, threshold=threshold, include_confidence=True, include_spans=True
    )
            entities = []
            for item in result:
                if isinstance(item, dict):
                    entities.append({
                        'entity': item.get('text', ''),
                        'label': item.get('label', ''),
                        'span': (item.get('start', 0), item.get('end', 0)),
                        'score': item.get('score', 0.9),
                    })
            return entities
        except Exception as e:
            logger.warning(f'mlx-gliner2 extraction failed: {e}')
            return []

    def _mlx_gliner2_extract_batch(
        self, texts: list[str], labels: list[str], threshold: float=0.5, batch_size: int=8
    ) -> list[list[dict]]:
        """
        Batch mlx-gliner2 inference na Metal GPU.

        SPRINT F320: používá batch_extract_entities místo per-text loop.
        Výrazně rychlejší na M1 unified memory (paralelizace přes Metal).
        """
        if self._mlx_gliner2_extractor is None:
            return [[] for _ in texts]
        try:
            # SPRINT F320: batch API — jeden Metal kernel pro celý batch
            results: list[list[dict]] = self._mlx_gliner2_extractor.batch_extract_entities(
                texts, labels, threshold=threshold, batch_size=batch_size,
                include_confidence=True, include_spans=True
    )
            # Normalizace na stejný formát jako _mlx_gliner2_extract
            normalized: list[list[dict]] = []
            for batch_result in results:
                batch_entities: list[dict] = []
                for item in batch_result:
                    if isinstance(item, dict):
                        batch_entities.append({
                            'entity': item.get('text', ''),
                            'label': item.get('label', ''),
                            'span': (item.get('start', 0), item.get('end', 0)),
                            'score': item.get('score', 0.9),
                        })
                normalized.append(batch_entities)
            return normalized
        except Exception as e:
            logger.warning(f'mlx-gliner2 batch extraction failed: {e}')
            return [[] for _ in texts]

    async def _load_coreml_model(self):
        """Lazy load CoreML NER model (běží na ANE)."""
        try:
            import coremltools as ct
            model_path = Path.home() / '.hledac' / 'models' / 'ner.mlmodel'
            if model_path.exists():
                self._coreml_ner_model = ct.models.MLModel(str(model_path))
                logger.info('CoreML NER model loaded')
        except Exception as e:
            logger.debug(f'CoreML NER load failed: {e}')

    def _nl_process_sync(self, text: str) -> list[dict]:
        """Synchronní volání NaturalLanguage.framework přes PyObjC."""
        if not self._nl_available:
            return []
        try:
            from Foundation import NSString
            from NaturalLanguage import NLTagger, NLTagScheme, NLTokenUnit
            entities = []
            ns_string = NSString.stringWithString_(text)
            tagger = NLTagger.alloc().initWithTagSchemes_([NLTagScheme.nameType])
            tagger.setString_(ns_string)

            def _block(tag, token_range, stop):
                if tag:
                    entities.append({'text': text[token_range.location:token_range.location + token_range.length], 'type': str(tag).split('.')[-1], 'confidence': 0.85})
                return True
            tagger.enumerateTagsInRange_unit_scheme_options_usingBlock_((0, len(text)), NLTokenUnit.word, NLTagScheme.nameType, 0, _block)
            return entities
        except Exception as e:
            logger.warning(f'NL framework failed: {e}')
            return []

    def get_ane_prediction_count(self) -> int:
        """Vrátí počet ANE predikcí pro monitoring."""
        return self._ane_predictions

    @property
    def is_loaded(self) -> bool:
        """Vrátí True pokud je model načten v paměti."""
        return self._model is not None

    async def initialize(self) -> None:
        """
        Explicitní inicializace - načte model do paměti.

n        Pokud je model již načten, nic nedělá.
        """
        if self._initialized and self._model is not None:
            logger.debug('NEREngine již inicializován')
            return
        with self._lock:
            if self._initialized and self._model is not None:
                return
            logger.info(f'Načítání GLiNER modelu: {self.model_name}')
            try:
                from gliner import GLiNER
                self._model = GLiNER.from_pretrained(self.model_name, load_tokenizer=True)
                self._model.eval()
                if hasattr(self._model, 'device'):
                    self._model = self._model.to('cpu')
                self._initialized = True
                logger.info('GLiNER model úspěšně načten (CPU)')
            except Exception as e:
                logger.error(f'Chyba při načítání GLiNER modelu: {e}')
                self._model = None
                self._initialized = False
                raise RuntimeError(f'Nepodařilo se načíst GLiNER model: {e}') from e

    def _ensure_loaded(self) -> None:
        """Interní metoda pro lazy loading - volá se automaticky před inference."""
        if self._model is None:
            logger.info('Lazy loading GLiNER modelu...')
            try:
                from gliner import GLiNER
                self._model = GLiNER.from_pretrained(self.model_name, load_tokenizer=True)
                self._model.eval()
                if hasattr(self._model, 'device'):
                    self._model = self._model.to('cpu')
                self._initialized = True
                logger.info('GLiNER model lazy-loaded (CPU)')
            except Exception as e:
                logger.error(f'Chyba při lazy loadingu GLiNER modelu: {e}')
                raise RuntimeError(f'Nepodařilo se načíst GLiNER model: {e}') from e

    def predict(self, text: str, labels: list[str], threshold: float=0.5) -> list[dict[str, Any]]:
        """
        Extrahuje entity z textu.

        Args:
            text: Vstupní text
            labels: Seznam labelů pro extrakci (např. ["person", "organization", "location"])
            threshold: Minimální confidence score (0.0 - 1.0)

        Returns:
            list[dict]: Seznam nalezených entit s klíči:
                - entity: text entity
                - label: typ entity
                - span: (start, end) pozice v textu
                - score: confidence score
        """
        self._ensure_loaded()
        if not text or not text.strip():
            return []
        if not labels:
            raise ValueError('Musí být zadán alespoň jeden label')
        try:
            entities = self._model.predict_entities(text, labels, threshold=threshold)
            result = []
            for entity in entities:
                result.append({'entity': entity.get('text', ''), 'label': entity.get('label', ''), 'span': (entity.get('start', 0), entity.get('end', 0)), 'score': entity.get('score', 0.0)})
            return result
        except Exception as e:
            logger.error(f'Chyba při NER predikci: {e}')
            raise RuntimeError(f'NER predikce selhala: {e}') from e
    _MLX_AVAILABLE = False
    _MLX_EXTRACTOR = None
    _MLX_LOAD_LOCK: asyncio.Lock | None = None

    @staticmethod
    def _get_mlx_lock() -> asyncio.Lock:
        """Lazy asyncio lock for MLX loader — ISSUE-014 pattern."""
        if NEREngine._MLX_LOAD_LOCK is None:
            NEREngine._MLX_LOAD_LOCK = asyncio.Lock()
        return NEREngine._MLX_LOAD_LOCK

    async def _load_mlx_extractor(self):
        """
        Lazy load MLX outlines extractor (async-safe DCLP).

        ISSUE-B3: Profile-gated — only loads when HLEDAC_ENABLE_MLX_OUTLINES=1.
        Without the flag, outlines+mlx are never imported (RAM budget on M1 8GB).
        """
        if os.getenv('HLEDAC_ENABLE_MLX_OUTLINES', '1') != '1':
            logger.debug('MLX outlines skipped (HLEDAC_ENABLE_MLX_OUTLINES != 1)')
            NEREngine._MLX_AVAILABLE = False
            return
        if NEREngine._MLX_AVAILABLE:
            return
        async with NEREngine._get_mlx_lock():
            if NEREngine._MLX_AVAILABLE:
                return
            try:
                from outlines.models import mlx as mlx_outlines
                NEREngine._MLX_EXTRACTOR = mlx_outlines('mlx-community/Llama-3.2-3B-Instruct-4bit')
                NEREngine._MLX_AVAILABLE = True
                logger.info('MLX outlines extractor loaded')
            except Exception as e:
                logger.debug(f'MLX outlines load failed: {e}')
                NEREngine._MLX_AVAILABLE = False

    async def _extract_with_mlx(self, text: str) -> list[dict]:
        """Extract entities using MLX outlines structured generation."""
        if not NEREngine._MLX_AVAILABLE:
            await self._load_mlx_extractor()
        if not NEREngine._MLX_AVAILABLE or NEREngine._MLX_EXTRACTOR is None:
            return []
        try:
            import msgspec
from compat.msgspec_gc_compat import Struct
            import outlines

            class EntityList(Struct):
                entities: list[dict]
            generator = outlines.generate.json(NEREngine._MLX_EXTRACTOR, EntityList)
            prompt = f'Extract named entities from text:\n{text[:2000]}'
            result = generator(prompt)
            return result.entities
        except Exception as e:
            logger.warning(f'MLX extraction failed: {e}')
            return []

    async def predict_async(self, text: str, labels: list[str], threshold: float=0.5) -> list[dict[str, Any]]:
        """
        Asynchronní varianta predict - běží v thread poolu.

        Sprint 76: ANE-first strategy - NaturalLanguage framework (ANE) is tried first,
        then CoreML fallback, then GLiNER.

        Args:
            text: Vstupní text
            labels: Seznam labelů pro extrakci
            threshold: Minimální confidence score

        Returns:
            list[dict]: Seznam nalezených entit
        """
        if self._mlx_gliner2_extractor is None:
            await self._load_mlx_gliner2()
        if self._mlx_gliner2_available and self._mlx_gliner2_extractor is not None:
            return await asyncio.to_thread(self._mlx_gliner2_extract, text, labels, threshold)
        if self._nl_available:
            return await asyncio.to_thread(self._nl_process_sync, text)
        if self._coreml_ner_model is None:
            await self._load_coreml_model()
        if self._coreml_ner_model:
            result = await asyncio.to_thread(self._coreml_ner_model.predict, {'text': text[:512]})
            self._ane_predictions += 1
            return result.get('entities', [])
        return await asyncio.to_thread(self.predict, text, labels, threshold)

    def predict_with_relations(self, text: str, labels: list[str], relations: list[dict[str, Any]] | None=None, threshold: float=0.5) -> dict[str, Any]:
        """
        Extrahuje entity a volitelně vztahy z textu pomocí gliner-relex.

        Args:
            text: Vstupní text
            labels: Seznam labelů pro extrakci (např. ["person", "organization", "threat_actor"])
            relations: Seznam definic vztahů pro joint extraction
                   Format: [{"relation": "attributed_to", "pairs_filter": [("malware", "threat_actor")]}]
            threshold: Minimální confidence score

        Returns:
            dict s klíči "entities" a "relations"
        """
        self._ensure_loaded()
        if not text or not text.strip():
            return {'entities': [], 'relations': []}
        if not labels:
            raise ValueError('Musí být zadán alespoň jeden label')
        try:
            if relations:
                entities, rels = self._model.predict(texts=[text], labels=labels, relations=relations, threshold=threshold, return_relations=True)
                return {'entities': entities[0] if entities else [], 'relations': rels[0] if rels else []}
            else:
                entities = self._model.predict_entities(text, labels, threshold=threshold)
                return {'entities': entities, 'relations': []}
        except Exception as e:
            logger.error(f'Chyba při NER+RE predikci: {e}')
            raise RuntimeError(f'NER+RE predikce selhala: {e}') from e

    def predict_batch(self, texts: list[str], labels: list[str], threshold: float=0.5, batch_size: int=8) -> list[list[dict[str, Any]]]:
        """
        Batch predikce pro více textů.

        Args:
            texts: Seznam vstupních textů
            labels: Seznam labelů pro extrakci
            threshold: Minimální confidence score
            batch_size: Velikost batch (pro budoucí optimalizaci)

        Returns:
            list[list[dict]]: Seznam výsledků pro každý text
        """
        self._ensure_loaded()
        if not texts:
            return []
        if not labels:
            return [[] for _ in texts]
        results = []
        for text in texts:
            try:
                entities = self.predict(text, labels, threshold)
                results.append(entities)
            except Exception as e:
                logger.error(f'Chyba při batch predikci pro text: {e}')
                results.append([])
        return results

    async def predict_batch_async(self, texts: list[str], labels: list[str], threshold: float=0.5, batch_size: int=8) -> list[list[dict[str, Any]]]:
        """
        Asynchronní batch predikce — MLX batch-first.

        Sprint F320: pokud je mlx_gliner2 dostupný, použije batch_extract_entities
        (paralelizace přes Metal). Jinak fallback na serial predict_batch.

        Args:
            texts: Seznam vstupních textů
            labels: Seznam labelů pro extrakci
            threshold: Minimální confidence score
            batch_size: Velikost batch pro MLX

        Returns:
            list[list[dict]]: Seznam výsledků pro každý text
        """
        if self._mlx_gliner2_extractor is None:
            await self._load_mlx_gliner2()
        if self._mlx_gliner2_available and self._mlx_gliner2_extractor is not None:
            # SPRINT F320: MLX batch path — paralelní Metal inference
            return await asyncio.to_thread(
                self._mlx_gliner2_extract_batch, texts, labels, threshold, batch_size
    )
        # Fallback: serial per-text inference
        return await asyncio.to_thread(self.predict_batch, texts, labels, threshold, batch_size)

    def unload(self) -> None:
        """
        Uvolní model z paměti.

        Po volání unload() se model znovu načte při příštím použití (lazy load).
        """
        with self._lock:
            if self._model is not None:
                logger.info('Uvolňování GLiNER modelu z paměti...')
                del self._model
                self._model = None
                self._initialized = False
                if _TORCH_AVAILABLE:
                    _t = _get_torch()
                    if _t is not None and hasattr(_t, 'cuda') and _t.cuda.is_available():
                        _t.cuda.empty_cache()
                import gc
                gc.collect()
                logger.info('GLiNER model uvolněn')

    async def predict_strict(self, text: str, labels: list[str], threshold: float=0.5, timeout: int=60) -> list[dict[str, Any]]:
        """
        MEMORY_STRICT mód - optimalizované rozhodování.

        Pro malé vstupy (<10KB) kde je model už načtený: použije in-process singleton
        (žádný subprocess overhead).
        Pro velké vstupy nebo nenainstalovaný model: subprocess pro memory isolation.

        Args:
            text: Vstupní text (max 10k chars v subprocess režimu)
            labels: Seznam labelů (max 5)
            threshold: Minimální confidence score
            timeout: Timeout v sekundách

        Returns:
            list[dict]: Seznam nalezených entit
        """
        if len(text) > MAX_STRICT_TEXT_LENGTH:
            text = text[:MAX_STRICT_TEXT_LENGTH]
            logger.warning(f'Text truncated to {MAX_STRICT_TEXT_LENGTH} chars in strict mode')
        if len(labels) > MAX_STRICT_LABELS:
            labels = labels[:MAX_STRICT_LABELS]
            logger.warning(f'Labels limited to {MAX_STRICT_LABELS} in strict mode')
        if len(text) <= MAX_STRICT_TEXT_LENGTH and self._model is not None:
            try:
                import asyncio
                result = await asyncio.to_thread(self.predict, text=text, labels=labels, threshold=threshold)
                return result
            except Exception as e:
                logger.warning(f'In-process NER failed ({e}), falling back to subprocess')
        try:
            return await self._run_in_subprocess(texts=[text], labels=labels, threshold=threshold, timeout=timeout)
        except Exception as e:
            logger.error(f'Strict mode NER failed: {e}')
            return []

    async def predict_batch_strict(self, texts: list[str], labels: list[str], threshold: float=0.5, timeout: int=120) -> list[list[dict[str, Any]]]:
        """
        MEMORY_STRICT batch mód.

        Args:
            texts: Seznam textů (max 3)
            labels: Seznam labelů (max 5)
            threshold: Minimální confidence score
            timeout: Timeout v sekundách

        Returns:
            list[list[dict]]: Seznam výsledků pro každý text
        """
        if len(texts) > MAX_STRICT_TEXTS:
            texts = texts[:MAX_STRICT_TEXTS]
            logger.warning(f'Texts limited to {MAX_STRICT_TEXTS} in strict mode')
        texts = [t[:MAX_STRICT_TEXT_LENGTH] if len(t) > MAX_STRICT_TEXT_LENGTH else t for t in texts]
        if len(labels) > MAX_STRICT_LABELS:
            labels = labels[:MAX_STRICT_LABELS]
        results: list[list[dict[str, Any]]] = []
        for text in texts:
            if len(text) <= MAX_STRICT_TEXT_LENGTH and self._model is not None:
                try:
                    import asyncio
                    entity_result = await asyncio.to_thread(self.predict, text=text, labels=labels, threshold=threshold)
                    results.append(entity_result)
                except Exception as e:
                    logger.warning(f'In-process NER failed ({e}), falling back to subprocess')
                    try:
                        sub_result = await self._run_in_subprocess(texts=[text], labels=labels, threshold=threshold, timeout=timeout)
                        results.append(sub_result[0] if sub_result else [])
                    except Exception:
                        results.append([])
            else:
                try:
                    sub_result = await self._run_in_subprocess(texts=[text], labels=labels, threshold=threshold, timeout=timeout)
                    results.append(sub_result[0] if sub_result else [])
                except Exception as e:
                    logger.error(f'Strict mode NER failed: {e}')
                    results.append([])
        return results

    async def _run_in_subprocess(self, texts: list[str], labels: list[str], threshold: float, timeout: int) -> list[list[dict[str, Any]]]:
        """
        Spustí GLiNER inference — preferuje persistent worker, fallback na temp subprocess.

        Persistent worker (P1-7):
        - Loahuje GLiNER model jednou, přežije 1000+ requestů
        - ~0ms startup (žádný import/model load)
        - Komunikace přes JSONL stdin/stdout

        Fallback temp subprocess:
        - Spustí nový Python proces pro každé volání
        - 50-150ms startup + 2-10s GLiNER load
        - Temp soubor pro kód, OS ho smaže po dokončení
        """
        # 1) Try persistent worker first
        try:
            worker = _get_ner_worker(self.model_name)
            results = await worker.extract(texts=texts, labels=labels, threshold=threshold, timeout=float(timeout))
            if results is not None:
                if len(texts) == 1:
                    return results[0] if results else []
                return results
            # Worker unavailable or failed — fall through to temp subprocess
        except Exception as e:
            logger.debug(f"NER persistent worker unavailable ({e}), falling back to temp subprocess")

        # 2) Fallback: temporary subprocess (original behavior)
        child_code = '\nimport json\nimport sys\nimport os\n\n# Potlačit PyTorch warningy\nos.environ[\'TOKENIZERS_PARALLELISM\'] = \'false\'\n\n# Načíst vstup\ninput_data = json.loads(sys.stdin.read())\ntexts = input_data[\'texts\']\nlabels = input_data[\'labels\']\nthreshold = input_data[\'threshold\']\nmodel_name = input_data.get(\'model_name\', \'knowledgator/gliner-relex-large-v0.5\')\n\ntry:\n    from gliner import GLiNER\n    import torch\n\n    # Načíst model\n    model = GLiNER.from_pretrained(model_name, load_tokenizer=True)\n    model.eval()\n\n    results = []\n    for text in texts:\n        if not text.strip():\n            results.append([])\n            continue\n\n        try:\n            entities = model.predict_entities(text, labels, threshold=threshold)\n            result = [{\n                "entity": e.get("text", ""),\n                "label": e.get("label", ""),\n                "span": (e.get("start", 0), e.get("end", 0)),\n                "score": e.get("score", 0.0)\n            } for e in entities]\n            results.append(result)\n        except Exception as e:\n            results.append([{"error": str(e)}])\n\n    # Výstup jako JSON\n    print(json.dumps({"success": True, "results": results}))\n\nexcept Exception as e:\n    print(json.dumps({"success": False, "error": str(e)}))\n'
        input_data = {'texts': texts, 'labels': labels, 'threshold': threshold, 'model_name': self.model_name}
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(child_code)
            temp_script = f.name
        try:
            proc = await asyncio.create_subprocess_exec(sys.executable, temp_script, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={**os.environ, 'TOKENIZERS_PARALLELISM': 'false'})
            async with asyncio.timeout(timeout):
                stdout, stderr = await proc.communicate(input=_msgspec_encode(input_data))
            if proc.returncode != 0:
                error_msg = stderr.decode() if stderr else 'Unknown error'
                raise RuntimeError(f'Subprocess failed: {error_msg}')
            result = _msgspec_decode(stdout)
            if not result.get('success'):
                raise RuntimeError(result.get('error', 'Unknown error'))
            results = result['results']
            if len(texts) == 1:
                return results[0] if results else []
            return results
        finally:
            try:
                os.unlink(temp_script)
            except Exception:  # noqa: BLE001
                pass

    def get_info(self) -> dict[str, Any]:
        """Vrátí informace o engine včetně MEMORY_STRICT podpory."""
        num_threads = 0
        if _TORCH_AVAILABLE:
            _t = _get_torch()
            if _t:
                try:
                    num_threads = _t.get_num_threads()
                except Exception:  # noqa: BLE001
                    pass
        return {'model_name': self.model_name, 'is_loaded': self.is_loaded, 'initialized': self._initialized, 'device': 'cpu', 'num_threads': num_threads, 'memory_strict_limits': {'max_text_length': MAX_STRICT_TEXT_LENGTH, 'max_labels': MAX_STRICT_LABELS, 'max_texts': MAX_STRICT_TEXTS}}
_default_engine: NEREngine | None = None
_ner_lock: threading.Lock = threading.Lock()

def get_ner_engine(model_name: str='knowledgator/gliner-relex-large-v0.5') -> NEREngine:
    """
    Vrátí singleton instanci NEREngine (thread-safe, double-checked locking).

    Args:
        model_name: Název modelu (default: knowledgator/gliner-relex-large-v0.5)

    Returns:
        NEREngine instance
    """
    global _default_engine
    if _default_engine is not None:
        return _default_engine
    with _ner_lock:
        if _default_engine is None:
            _default_engine = NEREngine(model_name)
        return _default_engine

def reset_ner_engine() -> None:
    """Resetuje singleton instanci (thread-safe, uvolní model z paměti a worker)."""
    global _default_engine
    with _ner_lock:
        if _default_engine is not None:
            _default_engine.unload()
            _default_engine = None
    # Close persistent worker (sync, best-effort)
    global _ner_worker
    with _ner_worker_lock:
        if _ner_worker is not None:
            _ner_worker.close()
            _ner_worker = None

def get_ner_backend() -> str:
    """
    Return the active NER/RE backend name.

    Returns:
        "gliner-relex" when model loaded,
        "nltagger" when ANE available,
        "coreml" when CoreML model loaded,
        "unavailable" when no backend available.
    """
    engine = _default_engine
    if engine is None:
        return 'unavailable'
    if engine._model is not None:
        return 'gliner-relex'
    if engine._coreml_ner_model is not None:
        return 'coreml'
    if engine._nl_available:
        return 'nltagger'
    return 'unavailable'

def get_extraction_status() -> dict:
    """
    Return diagnostic snapshot of extraction subsystem health.

    Returns:
        dict with keys: ner_backend, ner_loaded, pii_backend,
                        coreml_ner_inactive, nltagger_inactive,
                        relex_model, config_model
    """
    return {'ner_backend': get_ner_backend(), 'ner_loaded': _default_engine._model is not None if _default_engine else False, 'pii_backend': 'regex', 'coreml_ner_inactive': True, 'nltagger_inactive': not (_default_engine._nl_available if _default_engine else False), 'relex_model': 'knowledgator/gliner-relex-large-v0.5', 'config_model': 'knowledgator/gliner-x-base'}
import math as _math
import re as _re
from hledac.universal.utils.asyncx import safe_wait_for
from _core import aclose

# OSINT-01 FIX: Use `regex` module (linear-time guarantees) instead of `re` for
# domain pattern. The `re` module's Python engine suffers catastrophic backtracking
# on nested quantifiers like (?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])? combined with outer +.
# The Rust path uses a DFA engine and is immune — only the Python fallback is vulnerable.
# `regex` is a well-maintained drop-in replacement with linear-time guarantees.
try:
    import regex as _regex_module

    _DOMAIN_PAT = _regex_module.compile(
        r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b"
    )
except ImportError:
    # Fallback: compile with `re` but rely on the text[:10000] pre-truncate in
    # extract_iocs_from_text as a depth-limiting measure. This is a best-effort
    # fallback when `regex` is not installed — the ReDoS risk remains for
    # pathological inputs within 10k chars but is significantly reduced.
    _DOMAIN_PAT = _re.compile(
        r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b"
    )

_GUESS_PATTERNS: tuple[tuple[_re.Pattern, str], ...] = ((_re.compile('\\b(?:Corp|LLC|Inc|Ltd|Technologies|Software|Systems|Security)\\b', _re.IGNORECASE), 'organization'), (_re.compile('\\b(?:Mr|Mrs|Ms|Dr|Prof)\\.\\s+\\w+', _re.IGNORECASE), 'person'), (_re.compile('\\b(?:St|Street|City|Town|Country|Road|Ave|Boulevard)\\b', _re.IGNORECASE), 'location'), (_re.compile('\\b[A-Fa-f0-9]{32,64}\\b'), 'hash'))
_IOC_PATTERNS: list[tuple[str, _re.Pattern]] = [('cve', _re.compile('\\bCVE-\\d{4}-\\d{4,7}\\b')), ('sha256', _re.compile('\\b[0-9a-fA-F]{64}\\b')), ('md5', _re.compile('\\b[0-9a-fA-F]{32}\\b')), ('sha1', _re.compile('\\b[0-9a-fA-F]{40}\\b')), ('email', _re.compile('\\b[A-Za-z0-9._%+\\-]+@[A-Za-z0-9.\\-]+\\.[A-Z|a-z]{2,}\\b')), ('url', _re.compile('https?://[^\\s<>"{}|\\\\^`\\[\\]]+')), ('ipv4', _re.compile('\\b(?:(?:25[0-5]|2[0-4]\\d|[01]?\\d\\d?)\\.){3}(?:25[0-5]|2[0-4]\\d|[01]?\\d\\d?)\\b')), ('ipv6', _re.compile('\\b[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4}){7}\\b'))]
_DOMAIN_TLD_DENYLIST: frozenset[str] = frozenset({'exe', 'dll', 'bin', 'so', 'dylib', 'lib', 'o', 'a', 'obj', 'deb', 'rpm', 'dmg', 'pkg', 'apk', 'ipa', 'jar', 'war', 'ear', 'class', 'cab', 'msi', 'lnk', 'tar', 'gz', 'zip', 'rar', '7z', 'iso', 'img', 'dat', 'tmp', 'bak', 'log', 'conf', 'cfg', 'ini', 'env', 'py', 'js', 'ts', 'html', 'htm', 'json', 'xml', 'yaml', 'yml', 'toml', 'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx'})
_IOC_CONFIDENCE: dict[str, float] = {'cve': 0.98, 'sha256': 0.97, 'sha1': 0.96, 'md5': 0.95, 'email': 0.9, 'url': 0.85, 'ipv4': 0.85, 'ipv6': 0.8, 'domain': 0.7}
_SPACY_NLP = None

def _get_spacy():
    """Lazy spaCy loader."""
    global _SPACY_NLP
    if _SPACY_NLP is None:
        try:
            import spacy
            _SPACY_NLP = spacy.load('en_core_web_sm')
        except Exception:  # noqa: BLE001
            pass
    return _SPACY_NLP

def extract_iocs_from_text(text: str) -> list[dict]:
    """
    Extract IOCs from arbitrary text.
    Strategy: regex primary → spaCy secondary (attribution entities).
    Returns: [{"value": str, "ioc_type": str, "confidence": float}]
    Never raises.
    """
    if not text:
        return []
    results: list[dict] = []
    seen: set[str] = set()

    def _add(value: str, ioc_type: str, conf: float):
        v = value.strip()
        if v and v not in seen and (len(v) > 3):
            seen.add(v)
            results.append({'value': v, 'ioc_type': ioc_type, 'confidence': conf})
    # OSINT-01: Domain extraction uses _DOMAIN_PAT (regex module with linear-time
    # guarantees) instead of the vulnerable re.compile() pattern. Processed first
    # so TLD denylist check runs before other patterns.
    try:
        for m in _DOMAIN_PAT.findall(text[:10000]):
            tld = m.rsplit('.', 1)[-1].lower()
            if tld in _DOMAIN_TLD_DENYLIST:
                continue
            _add(m, 'domain', 0.7)
    except Exception:  # noqa: BLE001
        pass
    for ioc_type, pattern in _IOC_PATTERNS:
        try:
            for m in pattern.findall(text[:10000]):
                _add(m, ioc_type, _IOC_CONFIDENCE.get(ioc_type, 0.7))
        except Exception:  # noqa: BLE001
            pass
    nlp = _get_spacy()
    if nlp is not None:
        try:
            doc = nlp(text[:5000])
            for ent in doc.ents:
                if ent.label_ in ('ORG', 'PERSON', 'GPE', 'PRODUCT'):
                    _add(ent.text, ent.label_.lower(), 0.65)
        except Exception:  # noqa: BLE001
            pass
    return results

class IOCScorer:
    """
    Skóruje IOC záznamy podle zdroje a koroborace.
    Výsledné skóre vždy v [0.0, 1.0].
    """
    SOURCE_WEIGHTS: dict[str, float] = {'abuse_ch': 0.96, 'circl_pdns': 0.92, 'crtsh': 0.88, 'taxii': 0.9, 'shodan': 0.82, 'github_dork': 0.75, 'multi_engine': 0.65, 'ner_extracted': 0.58, 'dht_crawl': 0.52, 'regex_fallback': 0.5}

    @classmethod
    def score_by_source(cls, source: str) -> float:
        """Lookup weight pro zdroj, fallback 0.5."""
        for key, weight in cls.SOURCE_WEIGHTS.items():
            if key in source.lower():
                return weight
        return 0.5

    @staticmethod
    def score_by_corroboration(hit_count: int) -> float:
        """
        Log-scale bonus za opakovaný výskyt.
        hit_count=1 → 0.0 bonus, hit_count=10 → ~0.23, hit_count=100 → ~0.46
        """
        return min(0.5, _math.log1p(hit_count - 1) / _math.log1p(99))

    @classmethod
    def final_score(cls, ioc_entry: dict) -> float:
        """
        Kombinuje source weight + corroboration bonus.
        Clamp na [0.0, 1.0].
        """
        base = cls.score_by_source(ioc_entry.get('source', ''))
        bonus = cls.score_by_corroboration(ioc_entry.get('hit_count', 1))
        existing = float(ioc_entry.get('confidence', 0.5))
        combined = max(base, existing) * 0.7 + bonus * 0.3
        return round(min(1.0, max(0.0, combined)), 4)

def _normalize_entity_text(text: str) -> str:
    """Lowercase + strip for dedup."""
    return text.strip().lower()

def _extract_snippet(text: str, entity_value: str, context_chars: int=60) -> str:
    """Extract a short contextual snippet around entity occurrence."""
    if not text or not entity_value:
        return ''
    pos = _normalize_entity_text(text).find(_normalize_entity_text(entity_value))
    if pos < 0:
        return text[:context_chars] + ('...' if len(text) > context_chars else '')
    start = max(0, pos - context_chars // 2)
    end = min(len(text), pos + len(entity_value) + context_chars // 2)
    snippet = text[start:end]
    if start > 0:
        snippet = '…' + snippet
    if end < len(text):
        snippet = snippet + '…'
    return snippet

def _guess_entity_type(ioc_type: str | None, raw_text: str) -> str:
    """Guess entity type from IOC type or text patterns."""
    if ioc_type:
        return ioc_type
    text_lower = raw_text.lower()
    for pattern, entity_type in _GUESS_PATTERNS:
        if pattern.search(text_lower):
            return entity_type
    return 'unknown'

def _ioc_type_to_entity_type(ioc_type: str) -> str:
    """Map IOC type string to entity type string."""
    mapping = {'cve': 'cve', 'sha256': 'hash', 'sha1': 'hash', 'md5': 'hash', 'email': 'email', 'url': 'url', 'ipv4': 'ipv4', 'ipv6': 'ipv6', 'domain': 'domain'}
    return mapping.get(ioc_type, ioc_type)

def _extract_iocs_from_text_bounded(text: str) -> list[dict]:
    """
    Bounded wrapper around extract_iocs_from_text.
    Returns list[dict] with ioc_type as 'type' field for uniform interface.
    """
    iocs = extract_iocs_from_text(text)
    for ioc in iocs:
        ioc['type'] = _ioc_type_to_entity_type(ioc.get('ioc_type', ''))
    return iocs

def extract_entities_from_texts(texts: list[str], *, min_count: int=1, max_entities: int=100, include_types: list[str] | None=None) -> list[dict]:
    """
    Extract and rank entities from a list of raw texts.
    Falls back to IOC regex patterns when no model is loaded.

    Args:
        texts: List of raw text strings.
        min_count: Minimum occurrence count to include entity (default 1).
        max_entities: Maximum number of top entities to return (default 100).
        include_types: Optional whitelist of entity types to include.

    Returns:
        List of entity dicts sorted by (count * confidence) descending:
            {
                "value": str,          # normalized entity text
                "type": str,            # cve, hash, email, url, ipv4, domain, organization, ...
                "count": int,          # occurrence count across texts
                "confidence": float,   # 0.0-1.0 combined confidence
                "snippets": list[str], # up to 3 contextual snippets
            }
    """
    if not texts:
        return []
    entity_map: dict[tuple[str, str], dict] = {}
    capped_texts = [t[:15000] if t else '' for t in texts]
    from hledac.universal.pipeline.public_patterns import extract_iocs_from_texts as _batch_extract
    all_iocs = _batch_extract(capped_texts)
    for idx, iocs in enumerate(all_iocs):
        text = capped_texts[idx]
        for ioc in iocs:
            key = (_normalize_entity_text(ioc['value']), ioc['type'])
            if key not in entity_map:
                entity_map[key] = {'value': ioc['value'], 'type': ioc['type'], 'count': 0, 'confidence': ioc.get('confidence', 0.5), 'snippets': deque(), '_snippet_seen': set()}
            entity_map[key]['count'] += 1
            snippet = _extract_snippet(text, ioc['value'])
            if snippet and snippet not in entity_map[key]['_snippet_seen']:
                entity_map[key]['_snippet_seen'].add(snippet)
                entity_map[key]['snippets'].append(snippet)
                if len(entity_map[key]['snippets']) > 3:
                    entity_map[key]['snippets'].popleft()
    entities = []
    for (_value, etype), ent in entity_map.items():
        if include_types and etype not in include_types:
            continue
        if ent['count'] < min_count:
            continue
        ent['confidence'] = round(min(1.0, ent['confidence'] + _math.log1p(ent['count'] - 1) * 0.05), 4)
        entities.append(ent)
    entities.sort(key=lambda e: e['count'] * e['confidence'], reverse=True)
    return entities[:max_entities]

def extract_entities_from_findings(findings: list[dict], *, min_count: int=1, max_entities: int=100, include_types: list[str] | None=None) -> list[dict]:
    """
    Extract and rank entities from structured findings.
    Each finding should have 'text' field; optional 'url' and 'source' for co-occurrence.

    Args:
        findings: List of dicts with keys:
            - text (str): Raw text content.
            - url (str, optional): Source URL.
            - source (str, optional): Source name (e.g. "shodan", "whois").
        min_count: Minimum occurrence count (default 1).
        max_entities: Maximum top entities to return (default 100).
        include_types: Optional type whitelist.

    Returns:
        List of entity dicts sorted by (count * confidence):
            {
                "value": str,
                "type": str,
                "count": int,
                "confidence": float,
                "snippets": list[str],
                "sources": list[str],    # unique source names
                "urls": list[str],       # unique source URLs
            }
    """
    if not findings:
        return []
    texts: list[str] = []
    source_by_text: dict[int, str] = {}
    url_by_text: dict[int, str] = {}
    for f in findings:
        text = f.get('text', '') if isinstance(f, dict) else str(f)
        if text:
            idx = len(texts)
            texts.append(text)
            if isinstance(f, dict):
                if f.get('source'):
                    source_by_text[idx] = f['source']
                if f.get('url'):
                    url_by_text[idx] = f['url']
    entity_map: dict[tuple[str, str], dict] = {}
    capped_texts = [t[:15000] if t else '' for t in texts]
    from hledac.universal.pipeline.public_patterns import extract_iocs_from_texts as _batch_extract
    all_iocs = _batch_extract(capped_texts)
    for idx, iocs in enumerate(all_iocs):
        source = source_by_text.get(idx)
        url = url_by_text.get(idx)
        for ioc in iocs:
            key = (_normalize_entity_text(ioc['value']), ioc['type'])
            if key not in entity_map:
                entity_map[key] = {'value': ioc['value'], 'type': ioc['type'], 'count': 0, 'confidence': ioc.get('confidence', 0.5), 'snippets': deque(), '_snippet_seen': set(), 'sources': [], 'urls': []}
            entity_map[key]['count'] += 1
            snippet = _extract_snippet(capped_texts[idx], ioc['value'])
            if snippet and snippet not in entity_map[key]['_snippet_seen']:
                entity_map[key]['_snippet_seen'].add(snippet)
                entity_map[key]['snippets'].append(snippet)
                if len(entity_map[key]['snippets']) > 3:
                    entity_map[key]['snippets'].popleft()
            if source and source not in entity_map[key]['sources']:
                entity_map[key]['sources'].append(source)
            if url and url not in entity_map[key]['urls']:
                entity_map[key]['urls'].append(url)
    entities = []
    for (_value, etype), ent in entity_map.items():
        if include_types and etype not in include_types:
            continue
        if ent['count'] < min_count:
            continue
        ent['confidence'] = round(min(1.0, ent['confidence'] + _math.log1p(ent['count'] - 1) * 0.05), 4)
        entities.append(ent)
    entities.sort(key=lambda e: e['count'] * e['confidence'], reverse=True)
    return entities[:max_entities]

def _extract_cooccurrence_hints_from_text(text: str) -> dict[str, list[str]]:
    """
    Extract co-occurrence hints: domains mentioned alongside orgs, IPs, emails.
    Returns: {"domains": [...], "urls": [...], "orgs": [...], "ips": [...]}

    Uses Rust batch extraction (single GIL acquisition, rayon parallel) via
    public_patterns.extract_iocs_from_texts when batch size is large enough
    to amortize rayon overhead. Falls back to single-text path for small inputs.
    """
    hints: dict[str, list[str]] = {'domains': [], 'urls': [], 'orgs': [], 'ips': []}
    text = text[:5000]
    seen_domain: set[str] = set()
    seen_url: set[str] = set()
    seen_org: set[str] = set()
    seen_ip: set[str] = set()
    for ioc in _extract_iocs_from_text_bounded(text):
        t = ioc.get('type', '')
        v = ioc.get('value', '')
        if t == 'domain' and v not in seen_domain:
            seen_domain.add(v)
            hints['domains'].append(v)
        elif t == 'url' and v not in seen_url:
            seen_url.add(v)
            hints['urls'].append(v)
        elif t in ('ipv4', 'ipv6') and v not in seen_ip:
            seen_ip.add(v)
            hints['ips'].append(v)
    nlp = _get_spacy()
    if nlp is not None:
        try:
            doc = nlp(text[:2000])
            for ent in doc.ents:
                if ent.label_ in ('ORG', 'PRODUCT'):
                    v = ent.text.strip()
                    if v and v not in seen_org:
                        seen_org.add(v)
                        hints['orgs'].append(v)
        except Exception:  # noqa: BLE001
            pass
    for k in hints:
        hints[k] = hints[k][:10]
    return hints

def build_entity_cooccurrence_map(findings: list[dict], *, max_findings: int=50) -> dict[str, list[dict]]:
    """
    Build a co-occurrence map across findings.
    Groups entities that appear in the same or closely related findings.

    Args:
        findings: List of findings dicts (with 'text', optional 'url', 'source').
        max_findings: Cap on how many findings to process (default 50).

    Returns:
        dict with entity co-occurrence hints:
            {
                "domain_org": [(domain, org, count), ...],
                "domain_ip": [(domain, ip, count), ...],
                "url_org": [(url, org, count), ...],
                "by_domain": {domain: {"orgs": [...], "ips": [...], "urls": [...]}},
            }
    """
    if not findings:
        return {}
    findings = findings[:max_findings]
    finding_hints: list[dict] = []
    for f in findings:
        text = f.get('text', '') if isinstance(f, dict) else str(f)
        if not text:
            finding_hints.append({})
            continue
        hint = _extract_cooccurrence_hints_from_text(text)
        finding_hints.append(hint)
    domain_org_map: dict[tuple[str, str], int] = {}
    domain_ip_map: dict[tuple[str, str], int] = {}
    url_org_map: dict[tuple[str, str], int] = {}
    by_domain: dict[str, dict[str, list[str]]] = {}
    for hints in finding_hints:
        domains = hints.get('domains', [])
        urls = hints.get('urls', [])
        orgs = hints.get('orgs', [])
        ips = hints.get('ips', [])
        for d in domains:
            if d not in by_domain:
                by_domain[d] = {'orgs': [], 'ips': [], 'urls': []}
            for o in orgs:
                key = (d, o)
                domain_org_map[key] = domain_org_map.get(key, 0) + 1
                if o not in by_domain[d]['orgs']:
                    by_domain[d]['orgs'].append(o)
        for d in domains:
            for ip in ips:
                key = (d, ip)
                domain_ip_map[key] = domain_ip_map.get(key, 0) + 1
                if ip not in by_domain[d]['ips']:
                    by_domain[d]['ips'].append(ip)
        for u in urls:
            for o in orgs:
                key = (u, o)
                url_org_map[key] = url_org_map.get(key, 0) + 1
        for d in domains:
            for u in urls:
                if u not in by_domain[d]['urls']:
                    by_domain[d]['urls'].append(u)

    def _top_k(mapping: dict, k: int=10) -> list:
        return sorted(mapping.items(), key=lambda x: x[1], reverse=True)[:k]
    return {'domain_org': [(d, o, c) for (d, o), c in _top_k(domain_org_map)], 'domain_ip': [(d, ip, c) for (d, ip), c in _top_k(domain_ip_map)], 'url_org': [(u, o, c) for (u, o), c in _top_k(url_org_map)], 'by_domain': by_domain}

def _top_by_score(entities: list[dict], k: int=10) -> list[dict]:
    """Return top-k entities sorted by count * confidence."""
    if not entities:
        return []
    scored = sorted(entities, key=lambda e: e.get('count', 1) * e.get('confidence', 0.5), reverse=True)
    return scored[:k]

def _corroborated_findings(entities: list[dict], min_sources: int=2) -> list[dict]:
    """Filter entities seen across multiple sources (corroborated)."""
    return [e for e in entities if len(e.get('sources', [])) >= min_sources]

def _dominant_type(entities: list[dict]) -> str | None:
    """Return the most frequent entity type by total count."""
    if not entities:
        return None
    type_counts: dict[str, int] = {}
    for e in entities:
        t = e.get('type', 'unknown')
        type_counts[t] = type_counts.get(t, 0) + e.get('count', 1)
    if not type_counts:
        return None
    return max(type_counts, key=type_counts.get)

def _build_cooccurrence_pivots(co_map: dict, top_k: int=5) -> list[dict]:
    """
    Extract useful co-occurrence pivots from cooccurrence map.
    Returns small list of readable pivot dicts.
    """
    pivots: list[dict] = []
    for rel_type, pairs in [('domain_org', co_map.get('domain_org', [])), ('domain_ip', co_map.get('domain_ip', []))]:
        for domain, target, count in pairs[:top_k]:
            pivots.append({'pivot': domain, 'relation': rel_type, 'target': target, 'count': count})
    return pivots

def build_entity_summary(findings: list[dict], *, max_entities: int=20, max_cooccurrence_findings: int=30) -> dict:
    """
    Condensed entity summary from findings — second-level condensation.

    Produkuje malý, praktický output vhodný pro scheduler / export / core wiring:
    - top_entities:       ranked list (top 20 by count*confidence) — CAP: max_entities param
    - corroborated:       entities seen in multiple sources — CAP: max 10 items
    - co_occurrence_pivots: useful cross-entity pivots (domain↔org, domain↔ip) — CAP: max 5
    - dominant_type:      most frequent entity type across all findings
    - entity_takeaway:    one-line so-what string
    - type_breakdown:     count per type

    Args:
        findings: List of dicts with 'text', optional 'url', 'source'.
        max_entities: Max top entities to include (default 20).
        max_cooccurrence_findings: Max findings for cooccurrence (default 30).

    Returns:
        Condensed entity summary dict:
            {
                "top_entities": list[dict],           # CAP: max_entities (default 20)
                "corroborated": list[dict],           # CAP: max 10 items
                "co_occurrence_pivots": list[dict],   # CAP: max 5 items
                "dominant_type": str | None,
                "entity_takeaway": str,
                "type_breakdown": dict[str, int],
                "total_entities": int,
            }
    """
    if not findings:
        return {'top_entities': [], 'corroborated': [], 'co_occurrence_pivots': [], 'dominant_type': None, 'entity_takeaway': 'No findings to analyze.', 'type_breakdown': {}, 'total_entities': 0}
    entities = extract_entities_from_findings(findings, min_count=1, max_entities=200)
    co_map = build_entity_cooccurrence_map(findings[:max_cooccurrence_findings], max_findings=max_cooccurrence_findings)
    top_entities = _top_by_score(entities, k=max_entities)
    corroborated = _corroborated_findings(entities, min_sources=2)[:10]
    pivots = _build_cooccurrence_pivots(co_map, top_k=5)
    dominant = _dominant_type(entities)
    type_breakdown: dict[str, int] = {}
    for e in entities:
        t = e.get('type', 'unknown')
        type_breakdown[t] = type_breakdown.get(t, 0) + e.get('count', 1)
    total_count = sum((e.get('count', 1) for e in entities))
    unique_count = len(entities)
    top_type = dominant or 'unknown'
    top_entity_val = top_entities[0]['value'] if top_entities else None
    if top_entity_val:
        takeaway = f'{unique_count} unique entities ({total_count} total hits); dominant type={top_type}; top entity={top_entity_val}'
    else:
        takeaway = f'{unique_count} unique entities across {len(findings)} findings.'
    return {'top_entities': top_entities, 'corroborated': corroborated, 'co_occurrence_pivots': pivots, 'dominant_type': dominant, 'entity_takeaway': takeaway, 'type_breakdown': type_breakdown, 'total_entities': unique_count}

class FeedbackPack(Struct):
    """
    Unified compact feedback artifact for findings→entity→hypothesis→semantic loop.

    Combines entity summary + hypothesis pack + semantic pivots into a single
    bounded, actionable schema consumable by scheduler/windup.

    Field roles (STRICT separation):
    - entity_summary: Output of build_entity_summary() — top_entities, corroborated,
      co_occurrence_pivots, entity_takeaway, type_breakdown
    - hypothesis_pack_as_dict: HypothesisPack serialized as dict (hypotheses,
      suggested_queries, ioc_follow_ups, source_hints, provenance)
    - semantic_pivots: List of semantic_pivot results — text/score/source_type
    - provenance: "heuristic" or "mixed" (never "model" alone)

    Priority order for shortlist: IOC pivots > entity_pair > relationship > entity
    """
    entity_summary: dict = field(default_factory=dict)
    hypothesis_pack_as_dict: dict = field(default_factory=dict)
    semantic_pivots: list = field(default_factory=list)
    provenance: str = 'heuristic'

    def is_empty(self) -> bool:
        """Check if pack has any actionable content."""
        return not self.entity_summary.get('top_entities') and (not self.hypothesis_pack_as_dict.get('suggested_queries')) and (not self.hypothesis_pack_as_dict.get('ioc_follow_ups')) and (not self.semantic_pivots)

    def actionable_shortlist(self, max_items: int=5) -> list:
        """
        Return compact shortlist for scheduler consumption.

        Prioritizes: IOC pivots > entity_pair > relationship > entity > semantic.
        Returns max_items items, never blocks, never loads models.
        """
        shortlist = []
        seen_queries = set()

        def _add(item):
            query = item.get('query', '')
            if query and query not in seen_queries:
                seen_queries.add(query)
                item['priority'] = item.get('priority', 0.5)
                shortlist.append(item)
        for pivot in self.hypothesis_pack_as_dict.get('ioc_follow_ups', []):
            if len(shortlist) >= max_items:
                break
            _add({'action_type': 'ioc_pivot', 'query': pivot.get('query', ''), 'from_ioc': pivot.get('from', ''), 'to_field': pivot.get('to', ''), 'rationale': pivot.get('rationale', 'IOC pivot'), 'priority': pivot.get('priority', 0.9), 'pivot_type': 'ioc'})
        for q in self.hypothesis_pack_as_dict.get('suggested_queries', []):
            if len(shortlist) >= max_items:
                break
            pt = q.get('pivot_type', 'general')
            if pt in ('entity_pair', 'relationship', 'entity'):
                _add({'action_type': q.get('action_type', 'query'), 'query': q.get('query', ''), 'rationale': q.get('rationale', ''), 'priority': q.get('priority', 0.5), 'pivot_type': pt})
        for piv in self.semantic_pivots:
            if len(shortlist) >= max_items:
                break
            _add({'action_type': 'semantic_pivot', 'query': piv.get('text', '')[:200], 'rationale': f"semantic similarity {piv.get('score', 0):.2f}", 'priority': piv.get('score', 0.3), 'pivot_type': 'semantic'})
        shortlist.sort(key=attrgetter("get")('priority', 0), reverse=True)
        return shortlist[:max_items]

    @property
    def operator_shortlist(self) -> list:
        """Bounded operator shortlist (max 3) in scheduler-consumable shape.

        Returns items: {action: query, target: rationale[:80], rationale: pivot_type}

        This mirrors HypothesisPack.operator_shortlist for shape consistency
        across correlation/hypothesis/NER-augmented paths.
        """
        raw = self.actionable_shortlist(max_items=3)
        return [{'action': item.get('query', ''), 'target': item.get('rationale', '')[:80], 'rationale': item.get('pivot_type', '')} for item in raw]

def feedback_compact(findings: list, context: dict | None=None, semantic_pivots: list | None=None) -> FeedbackPack:
    """
    Build FeedbackPack from findings — unified entry point for feedback loop.

    Combines:
    1. build_entity_summary(findings) → entity_summary
    2. HypothesisEngine().build_hypothesis_pack(findings, context) → hypothesis_pack_as_dict
    3. semantic_pivots from caller (optional, filled by SemanticStore if available)

    Args:
        findings: List of finding dicts with 'text', optional 'source', 'url'
        context: Optional context for hypothesis generation
        semantic_pivots: Optional list of semantic pivot results from SemanticStore.semantic_pivot()
                         Each pivot should have: text, score, source_type, finding_id, ts, ioc_types

    Returns:
        FeedbackPack with all fields bounded and populated
    """
    if not findings:
        return FeedbackPack(entity_summary={}, hypothesis_pack_as_dict={}, semantic_pivots=semantic_pivots or [], provenance='heuristic')
    entity_summary = build_entity_summary(findings, max_entities=20)
    finding_texts = [f.get('text', '') if isinstance(f, dict) else str(f) for f in findings]
    from hledac.universal.brain.research_hypothesis_engine import HypothesisEngine
    engine = HypothesisEngine()
    engine._hypotheses = {}
    enriched_context = context.copy() if context else {}
    if not enriched_context.get('known_entities'):
        top_vals = [e['value'] for e in entity_summary.get('top_entities', [])[:20]]
        enriched_context['known_entities'] = set(top_vals)
    pack = engine.build_hypothesis_pack(finding_texts, enriched_context)
    hypothesis_pack_as_dict = {'hypotheses': pack.hypotheses, 'suggested_queries': pack.suggested_queries, 'ioc_follow_ups': pack.ioc_follow_ups, 'source_hints': pack.source_hints, 'provenance': pack.provenance}
    final_pivots = semantic_pivots if semantic_pivots is not None else []
    return FeedbackPack(entity_summary=entity_summary, hypothesis_pack_as_dict=hypothesis_pack_as_dict, semantic_pivots=final_pivots, provenance='mixed' if pack.provenance == 'model-assisted' else 'heuristic')
__all__ = ['extract_iocs_from_text', '_IOC_PATTERNS', '_IOC_CONFIDENCE', 'IOCScorer', 'NEREngine', 'get_ner_engine', 'reset_ner_engine', 'get_ner_backend', 'get_extraction_status', 'extract_entities_from_texts', 'extract_entities_from_findings', 'build_entity_cooccurrence_map', 'build_entity_summary', 'FeedbackPack', 'feedback_compact']