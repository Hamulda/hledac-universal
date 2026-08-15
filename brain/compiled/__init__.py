"""
brain/compiled/ — lazy-loaded DSPy compiled programs
=====================================================

Canonical storage for DSPy MIPROv2-compiled programs.

Loaded lazily by ``dspy_optimizer.load_compiled_program()`` — no DSPy
imported at module load time.

File format (JSON, msgspec-decoded):

    {
        "demos": [
            {"input_field": "value", ...},
            ...
        ]
    }

``demos`` is a list of ``dspy.Example``-compatible dicts. The fields
depend on the program signature (see ``_PROGRAM_CLASSES`` in
``dspy_optimizer.py``).

3 known programs (see ``_PROGRAM_CLASSES``):
    dark_query           → ``dark_query.json``
    hypothesis_generator → ``hypothesis_generator.json``
    hypothesis_ranker    → ``hypothesis_ranker.json``

Placeholders (``demos: []``) cause the optimizer to fall back to
uncompiled programs (always available, zero-shot).

M1 8GB: each JSON file is typically < 10 KB. No runtime overhead.
"""

from __future__ import annotations

import logging
from core import aclose

logger = logging.getLogger(__name__)

__all__: list[str] = []
