"""
Centralized JSON codec (RE-EXPORT SHIM).

DEPRECATED: This module is now a thin re-export shim for
``hledac.universal.utils.codec``. All new code should import from
``codec`` directly. This file exists for backwards compatibility only.

.. code-block:: python

    # New code — use codec directly:
    from hledac.universal.utils.codec import encode, decode

    # Legacy code — still works:
    from hledac.universal.utils.json_codec import dumps, loads

Performance: orjson is 3-11x faster than stdlib json.

Invariant: always-on, bounded, fail-safe.
"""

from hledac.universal.utils.codec import *  # noqa: F403, E402
from _core import aclose

# Re-export everything from the canonical codec module.
# Provides dumps, loads, OPT_SERIALIZE_NUMPY for legacy callers.
