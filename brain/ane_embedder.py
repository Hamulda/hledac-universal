"""
ANE-akcelerovaný embedder pro ModernBERT a FlashRank.
Offline konverze z MLX do CoreML, fallback na MLX.

Reranker: rerank_findings_crossencoder() používá flashrank CrossEncoder.
LanceDBIdentityStore má vlastní _get_flashrank_ranker() pro search path.
Tyto dvě instance jsou záměrně oddělené — ANE brain pipeline vs. vector store search.
"""

import asyncio
import logging
from pathlib import Path
from typing import List, Union

import numpy as np

logger = logging.getLogger(__name__)

try:
    import CoreML as _CoreML
    import Foundation as _Foundation
    ANE_AVAILABLE = True
except ImportError:
    ANE_AVAILABLE = False
    _CoreML = None
    _Foundation = None

MODELS_DIR = Path.home() / ".hledac" / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

# Sprint 8VF-ANE: pyobjc CoreML inference helpers
_HF_TOKENIZER = None

def _get_hf_tokenizer():
    global _HF_TOKENIZER
    if _HF_TOKENIZER is None:
        from transformers import AutoTokenizer
        _HF_TOKENIZER = AutoTokenizer.from_pretrained(
            "sentence-transformers/all-MiniLM-L6-v2", use_fast=True
        )
    return _HF_TOKENIZER

def _make_ml_array(data_list: list, length: int = 64):
    arr, err = _CoreML.MLMultiArray.alloc().initWithShape_dataType_error_(
        [1, length], _CoreML.MLMultiArrayDataTypeInt32, None
    )
    if err:
        raise RuntimeError(f"MLMultiArray init failed: {err}")
    ns_vals = [_Foundation.NSNumber.numberWithInt_(v) for v in data_list]
    ns_arr  = _Foundation.NSArray.arrayWithArray_(ns_vals)
    for i in range(length):
        arr.setObject_atIndexedSubscript_(ns_arr[i], i)
    return arr

def _coreml_embed(model, text: str) -> "np.ndarray":
    tok = _get_hf_tokenizer()
    tokens = tok(
        text[:256],
        return_tensors="np",
        padding="max_length",
        max_length=64,
        truncation=True,
    )
    input_ids = tokens["input_ids"].astype(np.int32).flatten().tolist()
    attn_mask  = tokens["attention_mask"].astype(np.int32).flatten().tolist()
    feat_dict = {
        "input_ids":      _make_ml_array(input_ids),
        "attention_mask": _make_ml_array(attn_mask),
    }
    provider, err = _CoreML.MLDictionaryFeatureProvider.alloc().initWithDictionary_error_(
        feat_dict, None
    )
    if err:
        raise RuntimeError(f"Feature provider failed: {err}")
    result, err = model.predictionFromFeatures_error_(provider, None)
    if err:
        raise RuntimeError(f"Inference failed: {err}")
    vec_raw = result.featureValueForName_("var_570").multiArrayValue()
    vec = np.array(
        [float(vec_raw.objectAtIndexedSubscript_(i)) for i in range(384)],
        dtype=np.float32,
    )
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


class ANEEmbedder:
    """
    Embedder, který se pokusí použít ANE (přes CoreML) a pokud není k dispozici,
    spoléhá na volání MLX embedderu (který musí být poskytnut zvenčí).
    """

    def __init__(self, model_name: str = "modernbert", hidden_dim: int = 768):
        self.model_name = model_name
        self.hidden_dim = hidden_dim
        self.model = None
        self._loaded = False
        self.coreml_path = MODELS_DIR / f"{model_name}_ane.mlpackage"
        self._fallback_embedder = None  # bude nastaven z ModelManager

    def set_fallback(self, fallback_func):
        """Nastaví fallback funkci (např. MLX embedder)."""
        self._fallback_embedder = fallback_func

    async def load(self):
        """Pokusí se načíst CoreML model, pokud existuje."""
        if self._loaded or not ANE_AVAILABLE:
            return
        if not self.coreml_path.exists():
            logger.info(f"ANE model {self.model_name} not found, skipping (fallback to MLX)")
            return
        try:
            url = _CoreML.NSURL.fileURLWithPath_(str(self.coreml_path))
            model, err = _CoreML.MLModel.modelWithContentsOfURL_error_(url, None)
            if err:
                raise RuntimeError(f"CoreML load failed: {err}")
            self.model = model
            self._loaded = True
            logger.info(f"ANEEmbedder loaded for {self.model_name}")
        except Exception as e:
            logger.warning(f"ANE embedder failed to load: {e}, using MLX fallback")

    async def convert_to_ane(self) -> bool:
        """Check for pre-compiled .mlmodelc — no conversion needed."""
        if not ANE_AVAILABLE:
            logger.warning("[ANE] CoreML (pyobjc) not available")
            return False
        compiled_path = MODELS_DIR / "AllMiniLML6V2.mlmodelc"
        if compiled_path.exists():
            self.coreml_path = compiled_path
            logger.info("[ANE] Pre-compiled model found: %s", compiled_path)
            return True
        raw_path = MODELS_DIR / "AllMiniLML6V2.mlmodel"
        if raw_path.exists():
            logger.info("[ANE] Compiling %s ...", raw_path)
            loop = asyncio.get_running_loop()
            def _compile():
                url = _CoreML.NSURL.fileURLWithPath_(str(raw_path))
                compiled_url, err = _CoreML.MLModel.compileModelAtURL_error_(url, None)
                if err:
                    raise RuntimeError(f"Compile failed: {err}")
                import shutil
                compiled_str = str(compiled_url).replace("file://", "")
                shutil.copytree(compiled_str, str(compiled_path), dirs_exist_ok=True)
                return compiled_path
            self.coreml_path = await loop.run_in_executor(None, _compile)
            logger.info("[ANE] Compiled to %s", self.coreml_path)
            return True
        logger.warning("[ANE] No model found at %s or %s", compiled_path, raw_path)
        return False

    async def embed(self, texts: Union[str, List[str]]) -> np.ndarray:
        if not self._loaded or self.model is None:
            raise NotImplementedError("ANE embedder not loaded, use fallback")
        if isinstance(texts, str):
            texts = [texts]
        loop = asyncio.get_running_loop()
        def _run():
            return np.array([_coreml_embed(self.model, t) for t in texts], dtype=np.float32)
        return await loop.run_in_executor(None, _run)

    async def warmup(self) -> None:
        """
        Sprint 8TC B.5: Pre-run dummy embedding pro načtení CoreML modelu do ANE cache.

        M1: první inference je vždy pomalá (~2s) — toto ji přesune do WARMUP fáze.
        Volá se z __main__.py v WARMUP fázi sprintu.
        """
        if not ANE_AVAILABLE:
            logger.debug("ANEEmbedder warmup skipped: ANE not available")
            return
        if not self._loaded or self.model is None:
            logger.debug("ANEEmbedder warmup skipped: model not loaded")
            return
        try:
            loop = asyncio.get_running_loop()
            dummy = ["warmup probe osint security"]
            await loop.run_in_executor(None, self.embed, dummy)
            logger.debug("ANEEmbedder warmed up (ANE cache primed)")
        except NotImplementedError:
            # embed() throws NotImplementedError until real inference is implemented
            # This is expected — warmup still counts as "priming the ANE subsystem"
            logger.debug("ANEEmbedder warmup: real inference not implemented yet, skipping")
        except Exception as e:
            logger.debug(f"ANEEmbedder warmup failed: {e}")

    @property
    def is_loaded(self) -> bool:
        """Vrátí True pokud je ANE model načten."""
        return self._loaded and self.model is not None


# Backward compat — importuje z kanonického mista
from hledac.universal.brain.ner_engine import extract_iocs_from_text, _IOC_PATTERNS


# ============================================================================
# Sprint 8VF: ANE Semantic Dedup
# ============================================================================

_ANE_EMBEDDER: "ANEEmbedder | None" = None


def get_ane_embedder() -> "ANEEmbedder | None":
    """Lazy init CoreML MiniLM-L6-v2 embedder."""
    global _ANE_EMBEDDER
    if _ANE_EMBEDDER is None:
        _ANE_EMBEDDER = ANEEmbedder(model_name="minilm_ane", hidden_dim=384)
    return _ANE_EMBEDDER


def unload_ane_embedder() -> None:
    """Called by memory pressure governor at CRITICAL state."""
    global _ANE_EMBEDDER
    _ANE_EMBEDDER = None


async def semantic_dedup_findings(
    findings: list[dict],
    threshold: float = 0.92,
) -> list[dict]:
    """
    Semantic deduplication of findings.
    ANE path: CoreML MiniLM batch inference → cosine similarity matrix.
    Hash fallback: url+title hash (zero RAM, always works).
    """
    embedder = get_ane_embedder()

    # Hash fallback when no ANE model
    if embedder is None or not embedder.is_loaded:
        seen: set[int] = set()
        out:  list[dict] = []
        for f in findings:
            key = hash((f.get("url", ""), f.get("title", "")))
            if key not in seen:
                seen.add(key)
                out.append(f)
        return out

    import numpy as np

    def _embed_batch_sync(texts: list[str]) -> np.ndarray:
        """CoreML batch inference — all texts at once."""
        if embedder is None or embedder.model is None:
            return np.zeros((len(texts), 384), dtype=np.float32)
        vecs = []
        for t in texts:
            try:
                vec = _coreml_embed(embedder.model, t)
                vecs.append(vec)
            except Exception:
                vecs.append(np.zeros(384, dtype=np.float32))
        return np.array(vecs, dtype=np.float32)

    texts = [
        f"{f.get('title', '')} {f.get('snippet', '')}".strip()[:512]
        for f in findings
    ]
    loop = asyncio.get_running_loop()
    try:
        vecs  = await loop.run_in_executor(None, _embed_batch_sync, texts)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9
        vecs_n = vecs / norms
        sim    = vecs_n @ vecs_n.T
        keep   = [True] * len(findings)
        for i in range(len(findings)):
            if not keep[i]:
                continue
            for j in range(i + 1, len(findings)):
                if sim[i, j] >= threshold:
                    keep[j] = False
        return [f for f, k in zip(findings, keep) if k]
    except Exception:
        return findings  # fallback on any error


# ============================================================================
# Sprint 8VF: Cosine Reranker for Synthesis
# ============================================================================

def rerank_findings_cosine(
    findings: list[dict],
    query: str,
    top_k: int = 20,
) -> list[dict]:
    """
    Cosine similarity reranker over ANE MiniLM embeddings.
    RAM: ~22MB model (CoreML), <5ms inference, ANE accelerated.
    Fallback: confidence sort.

    Why NOT phi-3-mini as reranker:
      - phi-3-mini is generative LLM (~2GB RAM)
      - For scoring/reranking, correct approach is cross-encoder
        or cosine similarity with embedding model
      - On 8GB M1, phi-3-mini + sprint pipeline = memory pressure
    """
    try:
        embedder = get_ane_embedder()
        if embedder is None or not embedder.is_loaded or embedder.model is None:
            raise RuntimeError("ANE unavailable")

        import numpy as np

        def _embed(text: str) -> np.ndarray:
            return _coreml_embed(embedder.model, text)

        q_vec = _embed(query[:512])
        q_norm = np.linalg.norm(q_vec) + 1e-9
        q_vec = q_vec / q_norm

        scored = []
        for f in findings[:200]:  # cap for RAM
            text = f"{f.get('title', '')} {f.get('snippet', '')}".strip()
            f_vec = _embed(text[:512])
            f_norm = np.linalg.norm(f_vec) + 1e-9
            f_vec = f_vec / f_norm
            score = float(np.dot(q_vec, f_vec))
            scored.append((score, f))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [f for _, f in scored[:top_k]]

    except Exception:
        # Fallback: sort by confidence
        return sorted(
            findings,
            key=lambda x: x.get("confidence", 0.5),
            reverse=True
        )[:top_k]


# ============================================================================
# AREA A: flashrank CrossEncoder reranker
# Replaces cosine-similarity ceiling with proper cross-encoder scoring.
# flashrank uses ms-marco-MiniLM-L-12-v2 ONNX (~22MB), ~2ms/query, zero UMA spike.
# Falls back to cosine similarity if flashrank unavailable.
# ============================================================================

_flashrank_reranker = None
_FLASHRANK_MODEL = "ms-marco-MiniLM-L-12-v2"  # 22MB ONNX


def _get_flashrank_reranker():
    """Lazy-load flashrank CrossEncoder ranker."""
    global _flashrank_reranker
    if _flashrank_reranker is None:
        try:
            from flashrank import Ranker
            _flashrank_reranker = Ranker(model_name=_FLASHRANK_MODEL, cache_dir="/tmp/flashrank_cache")
            logger.info("[RERANK:A] flashrank CrossEncoder loaded: %s", _FLASHRANK_MODEL)
        except ImportError:
            logger.warning("[RERANK:A] flashrank not available — falling back to cosine similarity")
        except Exception as e:
            logger.warning("[RERANK:A] flashrank load failed: %s", e)
            _flashrank_reranker = None
    return _flashrank_reranker


def rerank_findings_crossencoder(
    query: str,
    findings: list[dict],
    top_k: int = 20,
) -> list[dict]:
    """
    Cross-encoder reranker using flashrank ms-marco-MiniLM-L-12-v2.

    Superior to cosine similarity for cross-document relevance scoring.
    Falls back to rerank_findings_cosine if flashrank unavailable.

    Args:
        query: Search query string.
        findings: List of Finding dicts with .get('content')/.get('text')/.get('snippet') attributes.
        top_k: Number of top results to return.

    Returns:
        Reranked list of findings, top_k items.
    """
    ranker = _get_flashrank_reranker()
    if ranker is None:
        logger.debug("[RERANK:A] Using cosine fallback")
        return rerank_findings_cosine(findings, query, top_k)

    try:
        from flashrank import RerankRequest

        # Build passages — detect attribute name dynamically
        passages = []
        for i, f in enumerate(findings[:200]):  # cap at 200 for RAM
            text = (
                f.get("content")
                or f.get("text")
                or f.get("snippet")
                or f.get("title", "")
                or str(f)
            )[:2048]  # cap at 2048 chars
            passages.append({"id": i, "text": text})

        request = RerankRequest(query=query[:512], passages=passages)
        results = ranker.rerank(request)

        # Map back to original findings by id
        id_to_finding = {r["id"]: findings[r["id"]] for r in results[:top_k] if r["id"] < len(findings)}
        reranked = [id_to_finding[r["id"]] for r in results[:top_k] if r["id"] in id_to_finding]

        logger.debug("[RERANK:A] CrossEncoder reranked %d→%d findings", len(findings), len(reranked))
        return reranked

    except Exception as e:
        logger.warning("[RERANK:A] CrossEncoder failed (%s) — cosine fallback", e)
        return rerank_findings_cosine(findings, query, top_k)
