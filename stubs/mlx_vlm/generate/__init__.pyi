# Stub for mlx_vlm.generate
# Real package has dispatch.py + common.py with GenerationResult classes.

def generate(
    model,
    processor,
    prompt: str,
    image: str | list[str] | None = None,
    audio: str | list[str] | None = None,
    video: str | list[str] | None = None,
    verbose: bool = False,
    **kwargs,
) -> str: ...
