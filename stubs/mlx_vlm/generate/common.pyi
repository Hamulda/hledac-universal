from typing import Any

class GenerationResult:
    text: str
    prompt_tokens: int
    generation_tokens: int
    finish_reason: str | None
    peak_memory: float
    info: dict[str, Any]
    @property
    def usage(self) -> dict[str, int]: ...
    @property
    def text(self) -> str: ...
