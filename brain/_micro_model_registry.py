"""
MicroModelRegistry — Plain data registry (no MLX imports).

Extracted from micro_model_pool.py to break the MLX eager-load chain.
TaskType and MICRO_MODELS are static data — no runtime MLX needed.

This file MUST NOT import mlx.core or mlx_lm at module level.
Any file that needs only the enum/dict should import from here.
Files that need the actual MLX model pool should import from micro_model_pool.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class TaskType(Enum):
    """Task categories for micro-model routing."""

    CODE = auto()
    TRANSLATION = auto()
    EMBEDDINGS = auto()
    CLASSIFICATION = auto()
    SYNTHESIS = auto()
    TRIAGE = auto()
    GENERAL = auto()


@dataclass(frozen=True, slots=True)
class MicroModelSpec:
    """Immutable specification for a micro-model in the swarm."""

    name: str
    model_path: str
    task_type: TaskType
    quant: str = "q4"
    memory_mb: int = 350
    max_tokens: int = 2048
    priority: int = 0
    warmup_prompt: str = ""
    code_patterns: tuple[str, ...] = ()
    sql_patterns: tuple[str, ...] = ()
    translation_patterns: tuple[str, ...] = ()
    embedding_keywords: tuple[str, ...] = ()

    @property
    def full_path(self) -> str:
        """Get full quantized model path."""
        if self.quant == "q4":
            return f"{self.model_path}-4bit"
        elif self.quant == "q8":
            return f"{self.model_path}-8bit"
        return self.model_path


MICRO_MODELS: dict[str, MicroModelSpec] = {
    "qwen_coder": MicroModelSpec(
        name="Qwen2.5-0.5B-Instruct",
        model_path="mlx-community/Qwen2.5-0.5B-Instruct-mlx",
        task_type=TaskType.CODE,
        quant="q4",
        memory_mb=350,
        max_tokens=4096,
        priority=10,
        code_patterns=(
            r"def\s+\w+\s*\(",
            r"class\s+\w+",
            r"import\s+\w+",
            r"from\s+\w+\s+import",
            r"function\s*\w*\s*\(",
            r"=>\s*\{",
            r"const\s+\w+\s*=",
            r"let\s+\w+\s*=",
            r"var\s+\w+\s*=",
            r"#include",
            r"public\s+class",
            r"private\s+void",
            r"async\s+def",
            r"@\w+\s*\(",
            r"```\w*",
        ),
        sql_patterns=(
            r"SELECT\s+.+\s+FROM",
            r"INSERT\s+INTO",
            r"UPDATE\s+\w+\s+SET",
            r"DELETE\s+FROM",
            r"CREATE\s+TABLE",
            r"ALTER\s+TABLE",
            r"DROP\s+TABLE",
            r"JOIN\s+\w+\s+ON",
            r"WHERE\s+\w+",
        ),
    ),
    "phi35_mini": MicroModelSpec(
        name="Phi-3.5-mini-instruct",
        model_path="mlx-community/Phi-3.5-mini-instruct-4bit",
        task_type=TaskType.SYNTHESIS,
        quant="q4",
        memory_mb=700,
        max_tokens=4096,
        priority=8,
        translation_patterns=(
            r"\b(translate|translation|Übersetzung|traduction|traducción)\b",
            r"\b(from\s+\w+\s+to\s+\w+)\b",
            r"\b(english|german|french|spanish|chinese|japanese|korean)\s+(to|into|ins?)\b",
        ),
    ),
    "smollm_triage": MicroModelSpec(
        name="SmolLM2-360M-Instruct",
        model_path="mlx-community/SmolLM2-360M-Instruct-mlx",
        task_type=TaskType.TRIAGE,
        quant="q4",
        memory_mb=200,
        max_tokens=512,
        priority=15,
    ),
    "nomic_embed": MicroModelSpec(
        name="nomic-embed-text-v1.5",
        model_path="mlx-community/nomic-embed-text-v1.5-quantized",
        task_type=TaskType.EMBEDDINGS,
        quant="q4",
        memory_mb=274,
        max_tokens=8192,
        priority=12,
        embedding_keywords=(
            "embed",
            "similarity",
            "semantic",
            "vector",
            "embedding",
            "compare",
            "rank",
            "search",
            "find related",
            "most similar",
        ),
    ),
}
