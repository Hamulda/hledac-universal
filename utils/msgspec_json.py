"""
Unified msgspec-based JSON serialization facade (RE-EXPORT SHIM).

DEPRECATED: This module is now a thin re-export shim for
``hledac.universal.utils.codec``. All new code should import from
``codec`` directly. This file exists for backwards compatibility only.

.. code-block:: python

    # New code — use codec directly:
    from hledac.universal.utils.codec import encode, decode, encode_zstd

    # Legacy code — still works:
    from hledac.universal.utils.msgspec_json import encode, decode

Fallback chain: ``msgspec`` → ``orjson`` → ``json``.
"""

from hledac.universal.utils.codec import *  # noqa: F403, E402

# Re-export everything from the canonical codec module.
# This file is kept for backwards compatibility — ~55 call sites
# still import from msgspec_json. All names are present in codec.py.
