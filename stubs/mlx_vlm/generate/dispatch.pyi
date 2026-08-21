class GenerationResult:
    text: str
    prompt_tokens: int
    generation_tokens: int
    finish_reason: str | None
    peak_memory: float
    @property
    def usage(self) -> dict[str, int]: ...
