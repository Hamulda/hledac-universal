# Stub for curl_cffi.aiohttp submodule (referenced in transport/curl_cffi_runtime.py)
# Real package is curl_cffi with both sync + aio subpackages; we only need aio.

from . import aiohttp as aiohttp
from . import requests as requests

__version__: str

class CurlError(Exception): ...
class RequestsError(CurlError): ...

# Top-level constants
DEFAULT_TIMEOUT: float

# Re-export common impersonate profiles
IMPERSONATE: dict[str, str]
