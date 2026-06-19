# Stub for mlx_vlm — Apple Silicon VLM inference (mlx-graphics fork)
# Minimal surface consumed by tools/vlm_analyzer.py and brain/.
from typing import Any

VERSION: str
__version__: str

class GenerationResult:
    text: str
    prompt_tokens: int
    generation_tokens: int
    peak_memory: float
    finish_reason: str | None
    @property
    def usage(self) -> dict[str, int]: ...
    @property
    def text(self) -> str: ...

class BatchResponse:
    """Result from batch_generate call."""
    file: str
    text: str
    prompt_tokens: int
    generation_tokens: int

class BatchStats:
    """Aggregate stats from batch_generate."""
    total_prompt_tokens: int
    total_generation_tokens: int
    total_time_s: float
    peak_memory_gb: float

class PromptCacheState:
    """Mutable state for a prompt cache."""
    cache: Any
    metadata: Any
    @property
    def num_active_requests(self) -> int: ...

class VisionFeatureCache:
    """Per-image feature cache."""
    def __init__(self, max_size: int = 100) -> None: ...
    def get(self, key: str) -> Any: ...
    def put(self, key: str, value: Any) -> None: ...
    def clear(self) -> None: ...

# Top-level helpers
def generate(
    model: Any,
    processor: Any,
    image: str | list[str] | None = None,
    prompt: str | None = None,
    max_tokens: int = 256,
    temperature: float = 0.0,
    top_p: float = 1.0,
    top_k: int = 0,
    stop: list[str] | None = None,
    seed: int | None = None,
    stream: bool = False,
    **kwargs: Any,
) -> GenerationResult: ...
def stream_generate(
    model: Any,
    processor: Any,
    image: str | list[str] | None = None,
    prompt: str | None = None,
    max_tokens: int = 256,
    **kwargs: Any,
) -> Any: ...
def batch_generate(
    model: Any,
    processor: Any,
    images: list[str],
    prompts: list[str],
    max_tokens: int = 256,
    **kwargs: Any,
) -> list[BatchResponse]: ...
def apply_chat_template(processor: Any, messages: list[dict[str, Any]], tokenize: bool = False, add_generation_prompt: bool = True) -> str: ...
def load(path_or_hf_repo: str, **kwargs: Any) -> tuple[Any, Any]: ...
def process_image(processor: Any, image: str | bytes, **kwargs: Any) -> Any: ...
def get_message_json(messages: list[dict[str, Any]]) -> str: ...
def prepare_inputs(processor: Any, image: str | bytes, prompt: str) -> dict[str, Any]: ...

# Submodules
from . import generate as generate  # noqa: E402
