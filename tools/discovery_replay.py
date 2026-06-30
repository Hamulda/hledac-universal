"""
Discovery replay / cassette system for HTTP interaction recording.

Sprint F239A: Provides VCR-style replay of discovery adapter HTTP calls.
When replay_enabled is True, reads cached responses from disk instead of
making live HTTP requests.

This module is loaded eagerly by discovery adapters (circl_pdns, duckduckgo)
at import time. The replay functions are no-ops when replay_enabled=False.
"""

from typing import TYPE_CHECKING, Any


# Replay subsystem gates — both read live from os.environ so tests and
# adapter callers see the current value at every call site.
def replay_enabled() -> bool:
    """Return True iff ``HLEDAC_DISCOVERY_REPLAY`` env var == ``"1"``.

    Read fresh on every call (not snapshotted at import time) so
    tests can mutate ``os.environ`` between invocations and production
    adapters always observe the current operator intent.
    """
    return _os.environ.get("HLEDAC_DISCOVERY_REPLAY") == "1"


def replay_strict_enabled() -> bool:
    """Return True iff both replay + strict env vars are set to ``"1"``.

    Strict mode refuses to fall through to live HTTP on a cache miss;
    missing cassettes become hard errors. ``HLEDAC_REPLAY_STRICT=1``
    alone is ignored — it is a sub-flag, not an independent enable.
    """
    return (
        _os.environ.get("HLEDAC_DISCOVERY_REPLAY") == "1"
        and _os.environ.get("HLEDAC_REPLAY_STRICT") == "1"
    )


def read_cassette(adapter: str, key: str) -> dict[str, Any] | None:
    """Read a cassette for ``(adapter, key)``.

    Resolves the path via :func:`cassette_path` and parses the JSONL
    cassette file line-by-line.  Each line is expected to be a JSON
    envelope of the shape ``{"ts", "key", "response", "ttl_s"}``.

    Returns the ``response`` field from the most recent envelope whose
    timestamp is still within its declared TTL.  Returns ``None`` when:

    - replay is disabled,
    - the cassette file is missing or empty,
    - every line is malformed / unparseable, or
    - every otherwise-valid envelope has expired.

    Malformed lines are skipped (fail-soft); a corrupted entry never
    prevents a later valid entry from being surfaced.  Any I/O or
    decode error returns ``None`` — this function never raises.
    """
    if not replay_enabled():
        return None
    path = cassette_path(adapter, key)
    try:
        import orjson
    except Exception:
        return None
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except Exception:
        return None
    if not raw:
        return None

    import time as _time
    now = _time.time()
    best_response: dict[str, Any] | None = None

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = orjson.loads(stripped)
        except Exception:
            continue
        if not isinstance(entry, dict):
            continue
        # Envelope schema: {"ts", "key", "response", "ttl_s"}.
        ts = entry.get("ts")
        ttl_s = entry.get("ttl_s")
        response = entry.get("response")
        # TTL expiry check — skip the entry when both ts + ttl_s are
        # present and the window has lapsed.  Missing metadata means
        # "no TTL recorded" → treat as fresh (fail-soft for legacy).
        if isinstance(ts, (int, float)) and isinstance(ttl_s, (int, float)):
            if now - ts > ttl_s:
                continue
        if isinstance(response, dict):
            best_response = response

    return best_response


def write_cassette(
    adapter: str,
    key: str,
    data: dict[str, Any],
    *,
    ttl_s: int | None = None,
) -> None:
    """Write a cassette for ``(adapter, key)``.

    Wraps ``data`` in an envelope ``{"ts", "key", "response", "ttl_s"}``
    and serializes it as a single JSONL line.  ``ttl_s=None`` resolves
    to :func:`_default_ttl` (which honours :envvar:`HLEDAC_REPLAY_TTL`).

    Writes are atomic — the payload is staged in a sibling tmp file
    and then ``os.replace``\\ d onto the final path so concurrent
    readers never observe a half-written cassette.  The tmp file is
    cleaned up on failure (best-effort) so no ``.cassette_*.tmp``
    droppings remain in the cassette directory.

    No-op when ``replay_enabled`` is False.  When enabled, the
    serialized envelope is size-checked against
    :data:`CASSETTE_MAX_BYTES`; oversized payloads raise
    :class:`CassetteSizeExceeded` *before* touching disk.  All other
    I/O errors are swallowed (fail-soft) — cassettes are advisory and
    must never break a live request.
    """
    if not replay_enabled():
        return
    try:
        # Sprint S4: msgspec facade — 2-3× faster than orjson for small
        # cassette envelopes. The facade falls back to orjson internally
        # on type errors, preserving the prior fail-soft semantics.
        from hledac.universal.utils.msgspec_json import encode as _msgspec_encode
    except Exception:
        return

    import time as _time
    effective_ttl = ttl_s if ttl_s is not None else _default_ttl()
    envelope: dict[str, Any] = {
        "ts": _time.time(),
        "key": key,
        "response": data,
        "ttl_s": effective_ttl,
    }
    try:
        payload = _msgspec_encode(envelope)
    except Exception:
        return
    # JSONL line terminator — consistent with the multi-line format
    # exercised by the corrupted-line probe.
    line = payload + b"\n"
    if len(line) > CASSETTE_MAX_BYTES:
        raise CassetteSizeExceeded(
            f"Cassette payload for {adapter}/{key} exceeds "
            f"{CASSETTE_MAX_BYTES} bytes (actual={len(line)})",
            max_bytes=CASSETTE_MAX_BYTES,
            actual_bytes=len(line),
        )
    path = cassette_path(adapter, key)
    tmp_path: _pathlib.Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: stage in tmp, then os.replace onto final path.
        # tmp name uses the hashed key + pid to avoid cross-process
        # collisions when two writers race for the same cassette.
        tmp_name = f".cassette_{_hash_key(key)}_{_os.getpid()}.tmp"
        tmp_path = path.parent / tmp_name
        with open(tmp_path, "wb") as f:
            f.write(line)
        _os.replace(str(tmp_path), str(path))
    except Exception:
        # Best-effort cleanup so the directory stays free of
        # .cassette_*.tmp droppings even on failure.
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass


# TYPE_CHECKING block — imported only at type-checking time, not at runtime.
# duckduckgo_adapter is excluded from runtime import to prevent circular deps.
if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Sprint F239A extensions — added to satisfy the probe test suite
# (tests/probe_f239a_discovery_replay, tests/probe_f254c_*).  These
# helpers were originally defined in the test-side stubs and are
# back-ported here so production callers can rely on them.
# ---------------------------------------------------------------------------

import hashlib as _hashlib  # noqa: E402
import os as _os  # noqa: E402
import pathlib as _pathlib  # noqa: E402
import re as _re  # noqa: E402

# Bounded cassette size — keeps individual cassettes under 1 MB to
# avoid LMDB/DuckDB bloat and to make corruption easier to detect.
# (Matches probe test constant tests/probe_f239a/_MAX_CASSETTE_SIZE = 1_000_000.)
CASSETTE_MAX_BYTES: int = 1_000_000  # 1 MB

_DEFAULT_TTL_SECONDS: int = 24 * 3600  # 24 hours


class CassetteSizeExceeded(Exception):  # noqa: N818
    """Raised when a cassette payload exceeds CASSETTE_MAX_BYTES.

    Carries ``max_bytes`` and ``actual_bytes`` for diagnostics; both are
    stored as instance attributes so callers can surface them in metrics
    or error envelopes without re-parsing the message.
    """

    def __init__(
        self,
        message: str = "",
        *,
        max_bytes: int,
        actual_bytes: int,
    ) -> None:
        super().__init__(message)
        self.max_bytes = max_bytes
        self.actual_bytes = actual_bytes


def _default_ttl() -> int:
    """Default TTL for replayed cassettes (seconds).

    Reads :envvar:`HLEDAC_REPLAY_TTL` fresh on every call so tests can
    mutate the environment between invocations and production callers
    pick up operator-driven overrides without a process restart.
    Falls back to :data:`_DEFAULT_TTL_SECONDS` (24h) when the env var
    is unset, non-numeric, or non-positive.
    """
    raw = _os.environ.get("HLEDAC_REPLAY_TTL")
    if raw:
        try:
            value = int(raw)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    return _DEFAULT_TTL_SECONDS


def _replay_dir() -> _pathlib.Path:
    """Directory used to store replay cassettes (lazily created).

    Resolution order:
    1. ``HLEDAC_REPLAY_DIR`` env var, used verbatim as a :class:`pathlib.Path`.
    2. ``<cwd>/.hledac/replay`` — project-local default so cassette state
       stays co-located with the repo (matches probe test expectations).

    Returns a :class:`pathlib.Path` so callers can use ``.parent.mkdir()``
    and ``.write_text()`` directly without re-wrapping.
    """
    env = _os.environ.get("HLEDAC_REPLAY_DIR")
    if env:
        return _pathlib.Path(env)
    return _pathlib.Path.cwd() / ".hledac" / "replay"


def _replay_dir_path() -> _pathlib.Path:
    """Path form of :func:`_replay_dir` (alias kept for backward compat)."""
    return _replay_dir()


def _safe_key_component(s: str) -> str:
    """Sanitize a key fragment for use as a filename component.

    Allows only [A-Za-z0-9._-]; everything else is replaced with '_',
    runs of '_' are collapsed to a single '_', and leading/trailing
    '_' are stripped. Empty / whitespace-only inputs (or inputs that
    sanitize to an empty string) become 'unnamed'.
    """
    if not isinstance(s, str) or not s.strip():
        return "unnamed"
    s = _re.sub(r"[^A-Za-z0-9._-]", "_", s)
    s = _re.sub(r"_+", "_", s)
    s = s.strip("_")
    return s or "unnamed"


def _hash_key(s: str) -> str:
    """Stable, short hash for a key (first 16 hex of sha256)."""
    return _hashlib.sha256(s.encode("utf-8", errors="replace")).hexdigest()[:16]


def cassette_path(adapter: str, key: str) -> _pathlib.Path:
    """Build a deterministic cassette path for (adapter, key).

    Layout: ``<replay_dir>/<adapter>/<hash_prefix_2chars>/<hashed_key>.json``

    The ``<hash_prefix_2chars>`` shard subdirectory distributes cassettes
    across 256 buckets (00..ff) so no single directory grows unbounded
    when the replay store accumulates thousands of entries.  Callers
    should create the full tree with ``.parent.mkdir(parents=True,
    exist_ok=True)`` before writing — the extra shard level is fully
    transparent to ``.write_text()`` / ``open()`` usage.

    Returns a :class:`pathlib.Path` so callers can use ``.parent.mkdir()``
    and ``.write_text()`` directly without re-wrapping.
    """
    adapter_safe = _safe_key_component(adapter)
    key_safe = _hash_key(_safe_key_component(key))
    return _replay_dir_path() / adapter_safe / key_safe[:2] / f"{key_safe}.json"
