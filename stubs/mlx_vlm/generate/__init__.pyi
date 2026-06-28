# Stub for mlx_vlm.generate
# Real package has dispatch.py + common.py with GenerationResult classes.

from typing import List, Optional, Union

def generate(
    model,
    processor,
    prompt: str,
    image: Optional[Union[str, List[str]]] = None,
    audio: Optional[Union[str, List[str]]] = None,
    video: Optional[Union[str, List[str]]] = None,
    verbose: bool = False,
    **kwargs
) -> str: ...