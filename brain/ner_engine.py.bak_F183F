"""
✅ CANONICAL - NER Engine s GLiNER-X pro CPU inference
======================================================

Toto je CANONICAL implementace pro Named Entity Recognition.

Používá knowledgator/gliner-relex-large-v0.5 model pro extrakci named entities a vztahů (joint NER + RE)
s podporou lazy loading a explicitního CPU-only režimu.

Alternativa: utils/entity_extractor.py (regex-based, rychlejší ale méně přesný)

Pro NER vždy používejte tento modul:
    from hledac.universal.brain.ner_engine import NEREngine, get_ner_engine

Features:
- Lazy loading modelu (načte se až při prvním použití)
- CPU-only inference (map_location="cpu")
- Podpora batch i single prediction
- Explicitní unload pro uvolnění paměti
- Sprint 76: ANE acceleration via NaturalLanguage framework (PyObjC)
- Sprint 76: CoreML NER model fallback
"""

import asyncio
import inspect
import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any
from functools import partial

# Sprint 7B: Lazy torch import for M1 8GB memory optimization
# Sprint 8G: Made truly lazy - torch import deferred until first use
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

# Sprint 76: ANE detection via NaturalLanguage framework
_NL_AVAILABLE = False
try:
    import NaturalLanguage
    _NL_AVAILABLE = True
except ImportError:
    pass

# M1 8GB MEMORY_STRICT limity
MAX_STRICT_TEXT_LENGTH = 10000  # Max 10k chars v strict módu
MAX_STRICT_LABELS = 5           # Max 5 labels
MAX_STRICT_TEXTS = 3            # Max 3 textů v batch


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

    def __init__(self, model_name: str = "knowledgator/gliner-relex-large-v0.5"):
        self.model_name = model_name
        self._model: Optional[Any] = None
        self._lock = threading.RLock()
        self._initialized = False

        # Sprint 76: ANE-related attributes
        self._nl_available = _NL_AVAILABLE
        self._coreml_ner_model = None
        self._ane_predictions = 0  # Monitoring: count of ANE-based predictions

    # =============================================================================
    # Sprint 76: ANE Acceleration Methods
    # =============================================================================

    async def _load_coreml_model(self):
        """Lazy load CoreML NER model (běží na ANE)."""
        try:
            import coremltools as ct
            model_path = Path.home() / '.hledac' / 'models' / 'ner.mlmodel'
            if model_path.exists():
                self._coreml_ner_model = ct.models.MLModel(str(model_path))
                logger.info("CoreML NER model loaded")
        except Exception as e:
            logger.debug(f"CoreML NER load failed: {e}")

    def _nl_process_sync(self, text: str) -> List[Dict]:
        """Synchronní volání NaturalLanguage.framework přes PyObjC."""
        if not self._nl_available:
            return []

        try:
            from NaturalLanguage import NLTagger, NLTagScheme, NLTokenUnit
            from Foundation import NSString

            entities = []
            ns_string = NSString.stringWithString_(text)
            tagger = NLTagger.alloc().initWithTagSchemes_([NLTagScheme.nameType])
            tagger.setString_(ns_string)

            def _block(tag, token_range, stop):
                if tag:
                    entities.append({
                        'text': text[token_range.location:token_range.location + token_range.length],
                        'type': str(tag).split('.')[-1],
                        'confidence': 0.85
                    })
                return True

            tagger.enumerateTagsInRange_unit_scheme_options_usingBlock_(
                (0, len(text)),
                NLTokenUnit.word,
                NLTagScheme.nameType,
                0,
                _block
            )
            return entities
        except Exception as e:
            logger.warning(f"NL framework failed: {e}")
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
            logger.debug("NEREngine již inicializován")
            return

        with self._lock:
            if self._initialized and self._model is not None:
                return

            logger.info(f"Načítání GLiNER modelu: {self.model_name}")

            try:
                from gliner import GLiNER

                # CPU-only načtení modelu
                self._model = GLiNER.from_pretrained(
                    self.model_name,
                    load_tokenizer=True,
                )

                # Explicitně přesunout na CPU a nastavit eval mode
                self._model.eval()
                if hasattr(self._model, 'device'):
                    self._model = self._model.to('cpu')

                self._initialized = True
                logger.info("GLiNER model úspěšně načten (CPU)")

            except Exception as e:
                logger.error(f"Chyba při načítání GLiNER modelu: {e}")
                self._model = None
                self._initialized = False
                raise RuntimeError(f"Nepodařilo se načíst GLiNER model: {e}") from e

    def _ensure_loaded(self) -> None:
        """Interní metoda pro lazy loading - volá se automaticky před inference."""
        if self._model is None:
            logger.info("Lazy loading GLiNER modelu...")
            try:
                from gliner import GLiNER

                self._model = GLiNER.from_pretrained(
                    self.model_name,
                    load_tokenizer=True,
                )
                self._model.eval()
                if hasattr(self._model, 'device'):
                    self._model = self._model.to('cpu')

                self._initialized = True
                logger.info("GLiNER model lazy-loaded (CPU)")

            except Exception as e:
                logger.error(f"Chyba při lazy loadingu GLiNER modelu: {e}")
                raise RuntimeError(f"Nepodařilo se načíst GLiNER model: {e}") from e

    def predict(
        self,
        text: str,
        labels: List[str],
        threshold: float = 0.5
    ) -> List[Dict[str, Any]]:
        """
        Extrahuje entity z textu.

        Args:
            text: Vstupní text
            labels: Seznam labelů pro extrakci (např. ["person", "organization", "location"])
            threshold: Minimální confidence score (0.0 - 1.0)

        Returns:
            List[Dict]: Seznam nalezených entit s klíči:
                - entity: text entity
                - label: typ entity
                - span: (start, end) pozice v textu
                - score: confidence score
        """
        self._ensure_loaded()

        if not text or not text.strip():
            return []

        if not labels:
            raise ValueError("Musí být zadán alespoň jeden label")

        try:
            # GLiNER predict vrací list entit
            entities = self._model.predict_entities(
                text,
                labels,
                threshold=threshold
            )

            # Normalizace výstupu
            result = []
            for entity in entities:
                result.append({
                    "entity": entity.get("text", ""),
                    "label": entity.get("label", ""),
                    "span": (entity.get("start", 0), entity.get("end", 0)),
                    "score": entity.get("score", 0.0)
                })

            return result

        except Exception as e:
            logger.error(f"Chyba při NER predikci: {e}")
            raise RuntimeError(f"NER predikce selhala: {e}") from e

    # Sprint 80: MLX structured generation (outlines) availability
    _MLX_AVAILABLE = False
    _MLX_EXTRACTOR = None

    async def _load_mlx_extractor(self):
        """Lazy load MLX outlines extractor."""
        if NEREngine._MLX_AVAILABLE:
            return
        try:
            import outlines
            from outlines.models import mlx as mlx_outlines
            NEREngine._MLX_EXTRACTOR = mlx_outlines("mlx-community/Llama-3.2-3B-Instruct-4bit")
            NEREngine._MLX_AVAILABLE = True
            logger.info("MLX outlines extractor loaded")
        except Exception as e:
            logger.debug(f"MLX outlines load failed: {e}")
            NEREngine._MLX_AVAILABLE = False

    async def _extract_with_mlx(self, text: str) -> List[Dict]:
        """Extract entities using MLX outlines structured generation."""
        if not NEREngine._MLX_AVAILABLE:
            await self._load_mlx_extractor()
        if not NEREngine._MLX_AVAILABLE or NEREngine._MLX_EXTRACTOR is None:
            return []

        try:
            import msgspec

            class EntityList(msgspec.Struct):
                entities: List[dict]

            generator = outlines.generate.json(NEREngine._MLX_EXTRACTOR, EntityList)
            prompt = f"Extract named entities from text:\n{text[:2000]}"
            result = generator(prompt)
            return result.entities
        except Exception as e:
            logger.warning(f"MLX extraction failed: {e}")
            return []

    async def predict_async(
        self,
        text: str,
        labels: List[str],
        threshold: float = 0.5
    ) -> List[Dict[str, Any]]:
        """
        Asynchronní varianta predict - běží v thread poolu.

        Sprint 76: ANE-first strategy - NaturalLanguage framework (ANE) is tried first,
        then CoreML fallback, then GLiNER.

        Args:
            text: Vstupní text
            labels: Seznam labelů pro extrakci
            threshold: Minimální confidence score

        Returns:
            List[Dict]: Seznam nalezených entit
        """
        # Sprint 76: ANE-first via NaturalLanguage framework
        if self._nl_available:
            # ANE via NaturalLanguage
            return await asyncio.to_thread(self._nl_process_sync, text)

        # CoreML fallback (také ANE)
        if self._coreml_ner_model is None:
            await self._load_coreml_model()
        if self._coreml_ner_model:
            result = await asyncio.to_thread(
                self._coreml_ner_model.predict,
                {'text': text[:512]}
            )
            self._ane_predictions += 1
            return result.get('entities', [])

        # GLiNER fallback (CPU/GPU)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            partial(self.predict, text, labels, threshold)
        )

    def predict_with_relations(
        self,
        text: str,
        labels: List[str],
        relations: List[Dict[str, Any]] = None,
        threshold: float = 0.5
    ) -> Dict[str, Any]:
        """
        Extrahuje entity a volitelně vztahy z textu pomocí gliner-relex.

        Args:
            text: Vstupní text
            labels: Seznam labelů pro extrakci (např. ["person", "organization", "threat_actor"])
            relations: Seznam definic vztahů pro joint extraction
                   Format: [{"relation": "attributed_to", "pairs_filter": [("malware", "threat_actor")]}]
            threshold: Minimální confidence score

        Returns:
            Dict s klíči "entities" a "relations"
        """
        self._ensure_loaded()

        if not text or not text.strip():
            return {"entities": [], "relations": []}

        if not labels:
            raise ValueError("Musí být zadán alespoň jeden label")

        try:
            if relations:
                # Joint inference with relations
                entities, rels = self._model.predict(
                    texts=[text],
                    labels=labels,
                    relations=relations,
                    threshold=threshold,
                    return_relations=True
                )
                return {
                    "entities": entities[0] if entities else [],
                    "relations": rels[0] if rels else []
                }
            else:
                # NER only
                entities = self._model.predict_entities(text, labels, threshold=threshold)
                return {"entities": entities, "relations": []}

        except Exception as e:
            logger.error(f"Chyba při NER+RE predikci: {e}")
            raise RuntimeError(f"NER+RE predikce selhala: {e}") from e

    def predict_batch(
        self,
        texts: List[str],
        labels: List[str],
        threshold: float = 0.5,
        batch_size: int = 8
    ) -> List[List[Dict[str, Any]]]:
        """
        Batch predikce pro více textů.

        Args:
            texts: Seznam vstupních textů
            labels: Seznam labelů pro extrakci
            threshold: Minimální confidence score
            batch_size: Velikost batch (pro budoucí optimalizaci)

        Returns:
            List[List[Dict]]: Seznam výsledků pro každý text
        """
        self._ensure_loaded()

        if not texts:
            return []

        if not labels:
            raise ValueError("Musí být zadán alespoň jeden label")

        results = []

        for text in texts:
            try:
                entities = self.predict(text, labels, threshold)
                results.append(entities)
            except Exception as e:
                logger.error(f"Chyba při batch predikci pro text: {e}")
                results.append([])

        return results

    async def predict_batch_async(
        self,
        texts: List[str],
        labels: List[str],
        threshold: float = 0.5,
        batch_size: int = 8
    ) -> List[List[Dict[str, Any]]]:
        """
        Asynchronní batch predikce.

        Args:
            texts: Seznam vstupních textů
            labels: Seznam labelů pro extrakci
            threshold: Minimální confidence score
            batch_size: Velikost batch

        Returns:
            List[List[Dict]]: Seznam výsledků pro každý text
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            partial(self.predict_batch, texts, labels, threshold, batch_size)
        )

    def unload(self) -> None:
        """
        Uvolní model z paměti.

        Po volání unload() se model znovu načte při příštím použití (lazy load).
        """
        with self._lock:
            if self._model is not None:
                logger.info("Uvolňování GLiNER modelu z paměti...")

                # Odstranění reference na model
                del self._model
                self._model = None
                self._initialized = False

                # Vyčištění PyTorch cache
                if _TORCH_AVAILABLE:
                    _t = _get_torch()
                    if _t is not None and hasattr(_t, 'cuda') and _t.cuda.is_available():
                        _t.cuda.empty_cache()

                import gc
                gc.collect()

                logger.info("GLiNER model uvolněn")

    async def predict_strict(
        self,
        text: str,
        labels: List[str],
        threshold: float = 0.5,
        timeout: int = 60
    ) -> List[Dict[str, Any]]:
        """
        MEMORY_STRICT mód - GLiNER běží v izolovaném subprocessu.

        Args:
            text: Vstupní text (max 10k chars)
            labels: Seznam labelů (max 5)
            threshold: Minimální confidence score
            timeout: Timeout v sekundách

        Returns:
            List[Dict]: Seznam nalezených entit
        """
        # Hard limity
        if len(text) > MAX_STRICT_TEXT_LENGTH:
            text = text[:MAX_STRICT_TEXT_LENGTH]
            logger.warning(f"Text truncated to {MAX_STRICT_TEXT_LENGTH} chars in strict mode")

        if len(labels) > MAX_STRICT_LABELS:
            labels = labels[:MAX_STRICT_LABELS]
            logger.warning(f"Labels limited to {MAX_STRICT_LABELS} in strict mode")

        try:
            # Spusť GLiNER v subprocessu
            return await self._run_in_subprocess(
                texts=[text],
                labels=labels,
                threshold=threshold,
                timeout=timeout
            )
        except Exception as e:
            logger.error(f"Strict mode NER failed: {e}")
            return []

    async def predict_batch_strict(
        self,
        texts: List[str],
        labels: List[str],
        threshold: float = 0.5,
        timeout: int = 120
    ) -> List[List[Dict[str, Any]]]:
        """
        MEMORY_STRICT batch mód.

        Args:
            texts: Seznam textů (max 3)
            labels: Seznam labelů (max 5)
            threshold: Minimální confidence score
            timeout: Timeout v sekundách

        Returns:
            List[List[Dict]]: Seznam výsledků pro každý text
        """
        # Hard limity
        if len(texts) > MAX_STRICT_TEXTS:
            texts = texts[:MAX_STRICT_TEXTS]
            logger.warning(f"Texts limited to {MAX_STRICT_TEXTS} in strict mode")

        texts = [t[:MAX_STRICT_TEXT_LENGTH] if len(t) > MAX_STRICT_TEXT_LENGTH else t
                 for t in texts]

        if len(labels) > MAX_STRICT_LABELS:
            labels = labels[:MAX_STRICT_LABELS]

        try:
            return await self._run_in_subprocess(
                texts=texts,
                labels=labels,
                threshold=threshold,
                timeout=timeout
            )
        except Exception as e:
            logger.error(f"Strict mode batch NER failed: {e}")
            return [[] for _ in texts]

    async def _run_in_subprocess(
        self,
        texts: List[str],
        labels: List[str],
        threshold: float,
        timeout: int
    ) -> Any:
        """
        Spustí GLiNER inference v izolovaném subprocessu.

        Komunikace přes JSONL na stdin/stdout.
        Subprocess se ukončí po dokončení → OS uvolní RAM.
        """
        # Inline kód pro subprocess (aby nebyl potřeba nový soubor)
        child_code = '''
import json
import sys
import os

# Potlačit PyTorch warningy
os.environ['TOKENIZERS_PARALLELISM'] = 'false'

# Načíst vstup
input_data = json.loads(sys.stdin.read())
texts = input_data['texts']
labels = input_data['labels']
threshold = input_data['threshold']
model_name = input_data.get('model_name', 'knowledgator/gliner-relex-large-v0.5')

try:
    from gliner import GLiNER
    import torch

    # Načíst model
    model = GLiNER.from_pretrained(model_name, load_tokenizer=True)
    model.eval()

    results = []
    for text in texts:
        if not text.strip():
            results.append([])
            continue

        try:
            entities = model.predict_entities(text, labels, threshold=threshold)
            result = [{
                "entity": e.get("text", ""),
                "label": e.get("label", ""),
                "span": (e.get("start", 0), e.get("end", 0)),
                "score": e.get("score", 0.0)
            } for e in entities]
            results.append(result)
        except Exception as e:
            results.append([{"error": str(e)}])

    # Výstup jako JSON
    print(json.dumps({"success": True, "results": results}))

except Exception as e:
    print(json.dumps({"success": False, "error": str(e)}))
'''

        # Připrav data
        input_data = {
            'texts': texts,
            'labels': labels,
            'threshold': threshold,
            'model_name': self.model_name
        }

        # Vytvoř temp soubor s kódem
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(child_code)
            temp_script = f.name

        try:
            # Spusť subprocess
            proc = await asyncio.create_subprocess_exec(
                sys.executable, temp_script,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, 'TOKENIZERS_PARALLELISM': 'false'}
            )

            # Pošli data a čekej na výsledek
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=json.dumps(input_data).encode()),
                timeout=timeout
            )

            if proc.returncode != 0:
                error_msg = stderr.decode() if stderr else "Unknown error"
                raise RuntimeError(f"Subprocess failed: {error_msg}")

            # Parsuj výsledek
            result = json.loads(stdout.decode())

            if not result.get('success'):
                raise RuntimeError(result.get('error', 'Unknown error'))

            results = result['results']

            # Pokud je jeden text, vrať seznam entit
            if len(texts) == 1:
                return results[0] if results else []
            return results

        finally:
            # Uklid temp soubor
            try:
                os.unlink(temp_script)
            except Exception:
                pass

    def get_info(self) -> Dict[str, Any]:
        """Vrátí informace o engine včetně MEMORY_STRICT podpory."""
        num_threads = 0
        if _TORCH_AVAILABLE:
            _t = _get_torch()
            if _t:
                try:
                    num_threads = _t.get_num_threads()
                except Exception:
                    pass
        return {
            "model_name": self.model_name,
            "is_loaded": self.is_loaded,
            "initialized": self._initialized,
            "device": "cpu",
            "num_threads": num_threads,
            "memory_strict_limits": {
                "max_text_length": MAX_STRICT_TEXT_LENGTH,
                "max_labels": MAX_STRICT_LABELS,
                "max_texts": MAX_STRICT_TEXTS
            }
        }

# Singleton instance pro snadné použití
_default_engine: Optional[NEREngine] = None


def get_ner_engine(model_name: str = "knowledgator/gliner-relex-large-v0.5") -> NEREngine:
    """
    Vrátí singleton instanci NEREngine.

    Args:
        model_name: Název modelu (default: knowledgator/gliner-relex-large-v0.5)

    Returns:
        NEREngine instance
    """
    global _default_engine
    if _default_engine is None:
        _default_engine = NEREngine(model_name)
    return _default_engine


def reset_ner_engine() -> None:
    """Resetuje singleton instanci (uvolní model z paměti)."""
    global _default_engine
    if _default_engine is not None:
        _default_engine.unload()
        _default_engine = None


# ============================================================================
# Sprint 8VF + 8VG: IOC Extraction — kanonické místo pro NER/IOC
# ============================================================================

import re as _re
import math as _math

# ── Regex patterns — PRIMARY for technical IOC ──────────────────────────
_IOC_PATTERNS: list[tuple[str, _re.Pattern]] = [
    ("cve",    _re.compile(r'\bCVE-\d{4}-\d{4,7}\b')),
    ("sha256", _re.compile(r'\b[0-9a-fA-F]{64}\b')),
    ("md5",    _re.compile(r'\b[0-9a-fA-F]{32}\b')),
    ("sha1",   _re.compile(r'\b[0-9a-fA-F]{40}\b')),
    ("email",  _re.compile(
        r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Z|a-z]{2,}\b'
    )),
    ("url",    _re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+')),
    ("ipv4",   _re.compile(
        r'\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}'
        r'(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b'
    )),
    ("ipv6",   _re.compile(r'\b[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4}){7}\b')),
    ("domain", _re.compile(
        r'\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+'
        r'(?:com|net|org|io|ru|cn|de|onion|xyz|info|biz|cc|tv|gov|edu)\b'
    )),
]

_IOC_CONFIDENCE: dict[str, float] = {
    "cve": 0.98, "sha256": 0.97, "sha1": 0.96, "md5": 0.95,
    "email": 0.90, "url": 0.85, "ipv4": 0.85,
    "ipv6": 0.80, "domain": 0.70,
}

_SPACY_NLP = None


def _get_spacy():
    """Lazy spaCy loader."""
    global _SPACY_NLP
    if _SPACY_NLP is None:
        try:
            import spacy
            _SPACY_NLP = spacy.load("en_core_web_sm")
        except Exception:
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
    seen:    set[str]   = set()

    def _add(value: str, ioc_type: str, conf: float):
        v = value.strip()
        if v and v not in seen and len(v) > 3:
            seen.add(v)
            results.append({"value": v, "ioc_type": ioc_type,
                             "confidence": conf})

    # Primary: regex pass (cap at 10KB for RAM safety)
    for ioc_type, pattern in _IOC_PATTERNS:
        try:
            for m in pattern.findall(text[:10_000]):
                _add(m, ioc_type, _IOC_CONFIDENCE.get(ioc_type, 0.7))
        except Exception:
            pass

    # Secondary: spaCy for attribution entities (ORG, PERSON, GPE)
    nlp = _get_spacy()
    if nlp is not None:
        try:
            doc = nlp(text[:5_000])
            for ent in doc.ents:
                if ent.label_ in ("ORG", "PERSON", "GPE", "PRODUCT"):
                    _add(ent.text, ent.label_.lower(), 0.65)
        except Exception:
            pass

    return results


# ============================================================================
# Sprint 8VG C.3: IOCScorer — confidence pipeline
# ============================================================================

class IOCScorer:
    """
    Skóruje IOC záznamy podle zdroje a koroborace.
    Výsledné skóre vždy v [0.0, 1.0].
    """
    SOURCE_WEIGHTS: dict[str, float] = {
        "abuse_ch":       0.96,
        "circl_pdns":     0.92,
        "crtsh":          0.88,
        "taxii":          0.90,
        "shodan":         0.82,
        "github_dork":    0.75,
        "multi_engine":   0.65,
        "ner_extracted":  0.58,
        "dht_crawl":      0.52,
        "regex_fallback": 0.50,
    }

    @classmethod
    def score_by_source(cls, source: str) -> float:
        """Lookup weight pro zdroj, fallback 0.5."""
        for key, weight in cls.SOURCE_WEIGHTS.items():
            if key in source.lower():
                return weight
        return 0.50

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
        base   = cls.score_by_source(ioc_entry.get("source", ""))
        bonus  = cls.score_by_corroboration(ioc_entry.get("hit_count", 1))
        existing = float(ioc_entry.get("confidence", 0.5))
        combined = max(base, existing) * 0.7 + bonus * 0.3
        return round(min(1.0, max(0.0, combined)), 4)


# ============================================================================
# Sprint F150I: Bounded Entity Seams — findings/texts → ranked entities
# ============================================================================

def _normalize_entity_text(text: str) -> str:
    """Lowercase + strip for dedup."""
    return text.strip().lower()


def _extract_snippet(text: str, entity_value: str, context_chars: int = 60) -> str:
    """Extract a short contextual snippet around entity occurrence."""
    if not text or not entity_value:
        return ""
    pos = _normalize_entity_text(text).find(_normalize_entity_text(entity_value))
    if pos < 0:
        return text[:context_chars] + ("..." if len(text) > context_chars else "")
    start = max(0, pos - context_chars // 2)
    end = min(len(text), pos + len(entity_value) + context_chars // 2)
    snippet = text[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet


def _guess_entity_type(ioc_type: str | None, raw_text: str) -> str:
    """Guess entity type from IOC type or text patterns."""
    if ioc_type:
        return ioc_type
    # Fallback heuristics
    raw_lower = raw_text.lower()
    if _re.search(r'\b(?:Corp|LLC|Inc|Ltd|Technologies|Software|Systems|Security)\b', raw_text):
        return "organization"
    if _re.search(r'\b(?:Mr|Mrs|Ms|Dr|Prof)\.\s+\w+', raw_text):
        return "person"
    if _re.search(r'\b(?:St|City|Town|Country|Road|Ave|Boulevard)\b', raw_text):
        return "location"
    if _re.search(r'\b[A-Fa-f0-9]{32,64}\b', raw_text):
        return "hash"
    return "unknown"


def _ioc_type_to_entity_type(ioc_type: str) -> str:
    """Map IOC type string to entity type string."""
    mapping = {
        "cve": "cve", "sha256": "hash", "sha1": "hash", "md5": "hash",
        "email": "email", "url": "url", "ipv4": "ipv4", "ipv6": "ipv6",
        "domain": "domain",
    }
    return mapping.get(ioc_type, ioc_type)


def _extract_iocs_from_text_bounded(text: str) -> list[dict]:
    """
    Bounded wrapper around extract_iocs_from_text.
    Returns list[dict] with ioc_type as 'type' field for uniform interface.
    """
    iocs = extract_iocs_from_text(text)
    for ioc in iocs:
        ioc["type"] = _ioc_type_to_entity_type(ioc.get("ioc_type", ""))
    return iocs


def extract_entities_from_texts(
    texts: list[str],
    *,
    min_count: int = 1,
    max_entities: int = 100,
    include_types: list[str] | None = None,
) -> list[dict]:
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

    # 1. Collect entities from all texts (fail-soft)
    entity_map: dict[tuple[str, str], dict] = {}

    for text in texts:
        if not text:
            continue
        # Cap each text for RAM safety
        text = text[:15_000]

        try:
            # Primary: IOC regex pass
            iocs = _extract_iocs_from_text_bounded(text)
            for ioc in iocs:
                key = (_normalize_entity_text(ioc["value"]), ioc["type"])
                if key not in entity_map:
                    entity_map[key] = {
                        "value": ioc["value"],
                        "type": ioc["type"],
                        "count": 0,
                        "confidence": ioc.get("confidence", 0.5),
                        "snippets": [],
                    }
                entity_map[key]["count"] += 1
                snippet = _extract_snippet(text, ioc["value"])
                if snippet and snippet not in entity_map[key]["snippets"]:
                    entity_map[key]["snippets"].append(snippet)
                    # Keep max 3 snippets per entity
                    if len(entity_map[key]["snippets"]) > 3:
                        entity_map[key]["snippets"].pop(0)
        except Exception:
            pass

    # 2. Filter and rank
    entities = []
    for (value, etype), ent in entity_map.items():
        if include_types and etype not in include_types:
            continue
        if ent["count"] < min_count:
            continue
        # Boost confidence by count (log-scale)
        ent["confidence"] = round(
            min(1.0, ent["confidence"] + _math.log1p(ent["count"] - 1) * 0.05),
            4
        )
        entities.append(ent)

    # 3. Sort by count * confidence, return top N
    entities.sort(key=lambda e: e["count"] * e["confidence"], reverse=True)
    return entities[:max_entities]


def extract_entities_from_findings(
    findings: list[dict],
    *,
    min_count: int = 1,
    max_entities: int = 100,
    include_types: list[str] | None = None,
) -> list[dict]:
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

    # Extract texts and metadata
    texts: list[str] = []
    source_by_text: dict[int, str] = {}  # index -> source
    url_by_text: dict[int, str] = {}     # index -> url

    for f in findings:
        text = f.get("text", "") if isinstance(f, dict) else str(f)
        if text:
            idx = len(texts)
            texts.append(text)
            if isinstance(f, dict):
                if f.get("source"):
                    source_by_text[idx] = f["source"]
                if f.get("url"):
                    url_by_text[idx] = f["url"]

    # 1. Extract entities from all texts
    entity_map: dict[tuple[str, str], dict] = {}

    for idx, text in enumerate(texts):
        if not text:
            continue
        text = text[:15_000]
        source = source_by_text.get(idx)
        url = url_by_text.get(idx)

        try:
            iocs = _extract_iocs_from_text_bounded(text)
            for ioc in iocs:
                key = (_normalize_entity_text(ioc["value"]), ioc["type"])
                if key not in entity_map:
                    entity_map[key] = {
                        "value": ioc["value"],
                        "type": ioc["type"],
                        "count": 0,
                        "confidence": ioc.get("confidence", 0.5),
                        "snippets": [],
                        "sources": [],
                        "urls": [],
                    }
                entity_map[key]["count"] += 1
                snippet = _extract_snippet(text, ioc["value"])
                if snippet and snippet not in entity_map[key]["snippets"]:
                    entity_map[key]["snippets"].append(snippet)
                    if len(entity_map[key]["snippets"]) > 3:
                        entity_map[key]["snippets"].pop(0)
                if source and source not in entity_map[key]["sources"]:
                    entity_map[key]["sources"].append(source)
                if url and url not in entity_map[key]["urls"]:
                    entity_map[key]["urls"].append(url)
        except Exception:
            pass

    # 2. Filter and rank
    entities = []
    for (value, etype), ent in entity_map.items():
        if include_types and etype not in include_types:
            continue
        if ent["count"] < min_count:
            continue
        ent["confidence"] = round(
            min(1.0, ent["confidence"] + _math.log1p(ent["count"] - 1) * 0.05),
            4
        )
        entities.append(ent)

    entities.sort(key=lambda e: e["count"] * e["confidence"], reverse=True)
    return entities[:max_entities]


# ============================================================================
# Sprint F150I: Co-occurrence hints — domain/url/org/ip cross-signal
# ============================================================================

def _extract_cooccurrence_hints_from_text(text: str) -> dict[str, list[str]]:
    """
    Extract co-occurrence hints: domains mentioned alongside orgs, IPs, emails.
    Returns: {"domains": [...], "urls": [...], "orgs": [...], "ips": [...]}
    """
    hints: dict[str, list[str]] = {"domains": [], "urls": [], "orgs": [], "ips": []}

    # Quick pass - cap at 5KB
    text = text[:5_000]
    seen_domain: set[str] = set()
    seen_url: set[str] = set()
    seen_org: set[str] = set()
    seen_ip: set[str] = set()

    # IOCs
    for ioc in _extract_iocs_from_text_bounded(text):
        t = ioc.get("type", "")
        v = ioc.get("value", "")
        if t == "domain" and v not in seen_domain:
            seen_domain.add(v)
            hints["domains"].append(v)
        elif t == "url" and v not in seen_url:
            seen_url.add(v)
            hints["urls"].append(v)
        elif t in ("ipv4", "ipv6") and v not in seen_ip:
            seen_ip.add(v)
            hints["ips"].append(v)

    # Org entities via spaCy
    nlp = _get_spacy()
    if nlp is not None:
        try:
            doc = nlp(text[:2_000])
            for ent in doc.ents:
                if ent.label_ in ("ORG", "PRODUCT"):
                    v = ent.text.strip()
                    if v and v not in seen_org:
                        seen_org.add(v)
                        hints["orgs"].append(v)
        except Exception:
            pass

    # Limit each list
    for k in hints:
        hints[k] = hints[k][:10]

    return hints


def build_entity_cooccurrence_map(
    findings: list[dict],
    *,
    max_findings: int = 50,
) -> dict[str, list[dict]]:
    """
    Build a co-occurrence map across findings.
    Groups entities that appear in the same or closely related findings.

    Args:
        findings: List of findings dicts (with 'text', optional 'url', 'source').
        max_findings: Cap on how many findings to process (default 50).

    Returns:
        Dict with entity co-occurrence hints:
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

    # Collect hints per finding
    finding_hints: list[dict] = []
    for f in findings:
        text = f.get("text", "") if isinstance(f, dict) else str(f)
        if not text:
            finding_hints.append({})
            continue
        hint = _extract_cooccurrence_hints_from_text(text)
        finding_hints.append(hint)

    # Build co-occurrence
    domain_org_map: dict[tuple[str, str], int] = {}
    domain_ip_map: dict[tuple[str, str], int] = {}
    url_org_map: dict[tuple[str, str], int] = {}
    by_domain: dict[str, dict[str, list[str]]] = {}

    for hints in finding_hints:
        domains = hints.get("domains", [])
        urls = hints.get("urls", [])
        orgs = hints.get("orgs", [])
        ips = hints.get("ips", [])

        # Domain ↔ Org
        for d in domains:
            if d not in by_domain:
                by_domain[d] = {"orgs": [], "ips": [], "urls": []}
            for o in orgs:
                key = (d, o)
                domain_org_map[key] = domain_org_map.get(key, 0) + 1
                if o not in by_domain[d]["orgs"]:
                    by_domain[d]["orgs"].append(o)

        # Domain ↔ IP
        for d in domains:
            for ip in ips:
                key = (d, ip)
                domain_ip_map[key] = domain_ip_map.get(key, 0) + 1
                if ip not in by_domain[d]["ips"]:
                    by_domain[d]["ips"].append(ip)

        # URL ↔ Org
        for u in urls:
            for o in orgs:
                key = (u, o)
                url_org_map[key] = url_org_map.get(key, 0) + 1

        # URL under domain
        for d in domains:
            for u in urls:
                if u not in by_domain[d]["urls"]:
                    by_domain[d]["urls"].append(u)

    # Convert to sorted lists
    def _top_k(mapping: dict, k: int = 10) -> list:
        return sorted(mapping.items(), key=lambda x: x[1], reverse=True)[:k]

    return {
        "domain_org": [(d, o, c) for (d, o), c in _top_k(domain_org_map)],
        "domain_ip": [(d, ip, c) for (d, ip), c in _top_k(domain_ip_map)],
        "url_org": [(u, o, c) for (u, o), c in _top_k(url_org_map)],
        "by_domain": by_domain,
    }


def _top_by_score(entities: list[dict], k: int = 10) -> list[dict]:
    """Return top-k entities sorted by count * confidence."""
    if not entities:
        return []
    scored = sorted(entities, key=lambda e: e["count"] * e.get("confidence", 0.5), reverse=True)
    return scored[:k]


def _corroborated_findings(entities: list[dict], min_sources: int = 2) -> list[dict]:
    """Filter entities seen across multiple sources (corroborated)."""
    return [e for e in entities if len(e.get("sources", [])) >= min_sources]


def _dominant_type(entities: list[dict]) -> str | None:
    """Return the most frequent entity type by total count."""
    if not entities:
        return None
    type_counts: dict[str, int] = {}
    for e in entities:
        t = e.get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + e.get("count", 1)
    if not type_counts:
        return None
    return max(type_counts, key=type_counts.get)


def _build_cooccurrence_pivots(co_map: dict, top_k: int = 5) -> list[dict]:
    """
    Extract useful co-occurrence pivots from cooccurrence map.
    Returns small list of readable pivot dicts.
    """
    pivots: list[dict] = []
    for rel_type, pairs in [
        ("domain_org", co_map.get("domain_org", [])),
        ("domain_ip",  co_map.get("domain_ip",  [])),
    ]:
        for domain, target, count in pairs[:top_k]:
            pivots.append({
                "pivot": domain,
                "relation": rel_type,
                "target": target,
                "count": count,
            })
    return pivots


def build_entity_summary(
    findings: list[dict],
    *,
    max_entities: int = 20,
    max_cooccurrence_findings: int = 30,
) -> dict:
    """
    Condensed entity summary from findings — second-level condensation.

    Produkuje malý, praktický output vhodný pro scheduler / export / core wiring:
    - top_entities:       ranked list (top 20 by count*confidence)
    - corroborated:       entities seen in multiple sources
    - co_occurrence_pivots: useful cross-entity pivots (domain↔org, domain↔ip)
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
                "top_entities": list[dict],
                "corroborated": list[dict],
                "co_occurrence_pivots": list[dict],
                "dominant_type": str | None,
                "entity_takeaway": str,
                "type_breakdown": dict[str, int],
                "total_entities": int,
            }
    """
    if not findings:
        return {
            "top_entities": [],
            "corroborated": [],
            "co_occurrence_pivots": [],
            "dominant_type": None,
            "entity_takeaway": "No findings to analyze.",
            "type_breakdown": {},
            "total_entities": 0,
        }

    # Step 1: extract entities from findings
    entities = extract_entities_from_findings(
        findings,
        min_count=1,
        max_entities=200,  # extract wide, filter at output
    )

    # Step 2: build cooccurrence map
    co_map = build_entity_cooccurrence_map(
        findings[:max_cooccurrence_findings],
        max_findings=max_cooccurrence_findings,
    )

    # Step 3: build top N
    top_entities = _top_by_score(entities, k=max_entities)

    # Step 4: corroborated entities (seen in 2+ sources)
    corroborated = _corroborated_findings(entities, min_sources=2)[:10]

    # Step 5: cooccurrence pivots
    pivots = _build_cooccurrence_pivots(co_map, top_k=5)

    # Step 6: dominant type
    dominant = _dominant_type(entities)

    # Step 7: type breakdown
    type_breakdown: dict[str, int] = {}
    for e in entities:
        t = e.get("type", "unknown")
        type_breakdown[t] = type_breakdown.get(t, 0) + e.get("count", 1)

    # Step 8: entity takeaway
    total_count = sum(e.get("count", 1) for e in entities)
    unique_count = len(entities)
    top_type = dominant or "unknown"
    top_entity_val = top_entities[0]["value"] if top_entities else None

    if top_entity_val:
        takeaway = (
            f"{unique_count} unique entities ({total_count} total hits); "
            f"dominant type={top_type}; "
            f"top entity={top_entity_val}"
        )
    else:
        takeaway = f"{unique_count} unique entities across {len(findings)} findings."

    return {
        "top_entities": top_entities,
        "corroborated": corroborated,
        "co_occurrence_pivots": pivots,
        "dominant_type": dominant,
        "entity_takeaway": takeaway,
        "type_breakdown": type_breakdown,
        "total_entities": unique_count,
    }


__all__ = [
    "extract_iocs_from_text",
    "_IOC_PATTERNS",
    "_IOC_CONFIDENCE",
    "IOCScorer",
    "NEREngine",
    "get_ner_engine",
    "reset_ner_engine",
    # F150I bounded entity seams
    "extract_entities_from_texts",
    "extract_entities_from_findings",
    "build_entity_cooccurrence_map",
    # F150J condensed entity summary
    "build_entity_summary",
]
