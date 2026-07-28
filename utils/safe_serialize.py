"""
Safe serialization for isolated executor RPC (P1-04).

Eliminates pickle.load/dump security risk (RCE via __reduce__) in
interpreter-to-interpreter RPC by using msgspec.json + function name lookup.

Why not pickle
--------------
pickle.loads() can execute arbitrary code via __reduce__ on malicious objects.
OSINT system processes untrusted data — an attacker could inject a malicious
object into args that executes code when unpickled in the isolated interpreter.

Solution: msgspec.json + function registry
-------------------------------------------
1. Serialize (func, args, kwargs) as JSON: {"func": "module.func_name", "args": [...], "kwargs": {...}}
2. msgspec cannot serialize arbitrary callables — only data
3. In sub-interpreter, look up function by name from registry
4. Call with deserialized args

This eliminates __reduce__ code execution because msgspec only handles
plain data types (dict, list, str, int, float, bool, None).

Invariant: Only pre-registered functions can be called. No arbitrary code execution.

Usage
-----
    from hledac.universal.utils.safe_serialize import encode_call, decode_and_execute

    # Encode a call
    payload = encode_call("duckdb.execute_query", (sql,), {"params": []})

    # In sub-interpreter
    result = decode_and_execute(payload)

Bounded, fail-safe. Returns None on any error.
"""

from __future__ import annotations

import logging
import typing
from typing import Any, Callable

import msgspec

logger = logging.getLogger(__name__)

# msgspec schema for safe function calls
class FuncCall(msgspec.Struct):
    """Safe function call payload — msgspec.Struct for zero-copy decode."""
    func: str  # "module.attr" format
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = {}

# -----------------------------------------------------------------------------
# Function registry — explicit whitelist of allowed functions
# -----------------------------------------------------------------------------

# Registry: func_name -> callable
# Only functions in this registry can be called via decode_and_execute()
_FUNCTION_REGISTRY: dict[str, Callable[..., Any]] = {}

def register_function(name: str, func: Callable[..., Any]) -> None:
    """
    Register a function for safe RPC.

    Args:
        name: Dotted function name (e.g., "duckdb.execute_query").
               Must match what encode_call() uses.
        func: The callable to register.
    """
    _FUNCTION_REGISTRY[name] = func

def register_module_funcs(module_name: str, module: Any, func_names: list[str]) -> None:
    """
    Bulk register functions from a module.

    Args:
        module_name: Prefix for function names (e.g., "duckdb").
        module: The module object.
        func_names: List of attribute names to register.
    """
    for fname in func_names:
        full_name = f"{module_name}.{fname}"
        attr = getattr(module, fname, None)
        if callable(attr):
            _FUNCTION_REGISTRY[full_name] = attr

# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

def encode_call(func_name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> bytes:
    """
    Encode a function call as msgspec JSON bytes.

    Args:
        func_name: Dotted function name (e.g., "duckdb.execute_query").
        args: Positional arguments.
        kwargs: Keyword arguments.

    Returns:
        JSON bytes representing the call. Can be safely stored/transmitted.

    Note:
        Uses msgspec.json.Encoder for efficiency (reuses internal buffer).
        For orjson fallback, see utils.msgspec_json facade.
    """
    call = FuncCall(func=func_name, args=args, kwargs=kwargs)
    return msgspec.json.encode(call)


def decode_and_execute(payload: bytes) -> Any | None:
    """
    Decode a function call payload and execute it.

    Args:
        payload: Bytes from encode_call().

    Returns:
        Result of the function call, or None on any error.

    Security:
        - Only functions in _FUNCTION_REGISTRY can be executed
        - No arbitrary code execution via __reduce__
        - All arguments are plain JSON types (no deserialized callables)

    Fail-safe: returns None on any error, never raises.
    """
    try:
        call = msgspec.json.decode(payload, type=FuncCall)
    except Exception as e:
        logger.warning("[safe_serialize] Decode failed: %s", e)
        return None

    func = _FUNCTION_REGISTRY.get(call.func)
    if func is None:
        logger.warning("[safe_serialize] Unknown function: %s", call.func)
        return None

    try:
        return func(*call.args, **call.kwargs)
    except Exception as e:
        logger.warning("[safe_serialize] Execution failed for %s: %s", call.func, e)
        return None


# -----------------------------------------------------------------------------
# Pre-populate registry with known-safe internal functions
# These are the only functions that can be called via decode_and_execute()
# -----------------------------------------------------------------------------

def _populate_registry() -> None:
    """Populate registry with known-safe functions. Called once at module load."""
    # Import here to avoid circular imports and to defer heavy imports
    # DuckDB core functions — duckdb is an optional dependency
    try:
        import duckdb
        register_module_funcs("duckdb", duckdb, [
            "execute", "sql", "query", "read_csv", "read_parquet",
            "register", "unregister", "close", "connect",
        ])
    except ImportError:
        logger.debug("[safe_serialize] duckdb not available, skipping registration")

    # NumPy core functions — numpy is an optional dependency
    try:
        import numpy
        register_module_funcs("numpy", numpy, [
            "array", "zeros", "ones", "empty", "save", "load",
            "concatenate", "stack", "sum", "mean", "std",
        ])
    except ImportError:
        logger.debug("[safe_serialize] numpy not available, skipping registration")

    # NOTE: Register additional application-specific functions here using
    # register_function() and register_module_funcs().
    # Only pre-approved functions can be called via decode_and_execute().
    #
    # Example (uncomment if needed):
    # try:
    #     from my_module import my_function
    #     register_function("my_module.my_function", my_function)
    # except ImportError:
    #     pass

_populate_registry()
