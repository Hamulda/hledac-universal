"""
SLM decomposer – rozklad složitých úkolů na podúkoly pomocí tiny SLM (mlx_lm).
Podporuje paralelní běh, cache a validaci.
"""

import asyncio
import hashlib
import json
import logging

import psutil

logger = logging.getLogger(__name__)

# Lazy import for mlx_lm
MLX_LM_AVAILABLE = True
try:
    from mlx_lm import generate, load
except ImportError:
    MLX_LM_AVAILABLE = False
    logger.warning("mlx_lm not available, SLM decomposer will use fallback")


class SLMDecomposer:
    def __init__(self, governor, cache,
                 model_name: str = "mlx-community/Qwen2.5-0.5B-4bit",
                 max_parallel: int = 2):
        self.governor = governor
        self.cache = cache
        self.model_name = model_name
        self.max_parallel = max_parallel
        self._model = None
        self._tokenizer = None
        self._model_version = 1

    async def _load_model(self):
        if self._model is None and MLX_LM_AVAILABLE:
            loop = asyncio.get_running_loop()
            self._model, self._tokenizer = await loop.run_in_executor(
                None, lambda: load(self.model_name)
            )
            logger.info(f"SLM model {self.model_name} loaded")

    async def decompose(self, task_description: str, context: dict) -> list[dict]:
        if not MLX_LM_AVAILABLE:
            return self._rule_based_fallback(task_description, context)

        await self._load_model()

        # Cache
        cache_key = self._cache_key(task_description, context)
        cached = await self.cache.get(cache_key, self._model_version)
        if cached is not None:
            logger.debug("Cache hit pro rozklad")
            return cached

        # Kolik paralelních instancí můžeme spustit?
        parallel = 1
        if self.max_parallel > 1:
            free_ram = psutil.virtual_memory().available / (1024 * 1024)
            estimated_per_instance = 800  # MB pro 0.5B model
            if free_ram > estimated_per_instance * 2:
                parallel = 2
                if free_ram > estimated_per_instance * 3:
                    parallel = 3

        # Různé varianty promptu
        prompts = self._build_prompts(task_description, context, parallel)

        tasks = [self._call_slm(prompt, timeout=2.0) for prompt in prompts]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        best = None
        best_score = -1
        for res in results:
            if isinstance(res, Exception):
                logger.warning(f"SLM volání selhalo: {res}")
                continue
            if res and res.get('confidence', 0) > best_score:
                best = res['decomposition']
                best_score = res['confidence']

        if best is None:
            logger.warning("SLM selhal, používám rule‑based fallback")
            best = self._rule_based_fallback(task_description, context)

        await self.cache.put(cache_key, best, self._model_version)
        return best

    def _build_prompts(self, task: str, context: dict, count: int) -> list[str]:
        """Vytvoří různé prompt varianty pro paralelní běh."""
        base = f"""Rozlož následující výzkumný úkol na posloupnost elementárních akcí.
Úkol: {task}
Kontext: {json.dumps(context, ensure_ascii=False)}
Vrať JSON seznam akcí, každá s poli 'type', 'params' a 'priority' (1-10).
Povolené typy: fetch, deep_read, branch, analyse, synthesize, hypothesis, explain.
"""
        variants = [base]
        if count >= 2:
            variants.append(base + "\nPreferuj rychlé, levné akce.")
        if count >= 3:
            variants.append(base + "\nPreferuj hloubkové, přesné akce.")
        return variants[:count]

    async def _call_slm(self, prompt: str, timeout: float) -> dict | None:
        """Zavolá MLX LM a parsuje JSON výstup."""
        if not MLX_LM_AVAILABLE:
            return None

        loop = asyncio.get_running_loop()
        try:
            # Generování je synchronní, spustíme v executoru
            response = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: generate(self._model, self._tokenizer, prompt, max_tokens=500)),
                timeout=timeout
            )
            # Najdeme JSON část – zkusíme najít první [ a poslední ]
            start = response.find('[')
            end = response.rfind(']') + 1
            if start >= 0 and end > start:
                json_str = response[start:end]
                data = json.loads(json_str)
                # Jednoduchá validace
                if isinstance(data, list):
                    for item in data:
                        if not isinstance(item.get('type'), str):
                            raise ValueError("Missing type")
                    return {'decomposition': data, 'confidence': 0.9}
        except Exception as e:
            logger.error(f"SLM call error: {e}")
        return None

    def _rule_based_fallback(self, task: str, context: dict) -> list[dict]:
        """Jednoduchý fallback – pro ukázku vrací jeden fetch."""
        return [{'type': 'fetch', 'params': {'url': '...'}, 'priority': 5}]

    def _cache_key(self, task: str, context: dict) -> str:
        content = f"{task}:{json.dumps(context, sort_keys=True)}"
        return hashlib.sha256(content.encode()).hexdigest()
