from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _core.rust_backend.deobfuscate import DeobfuscateResult as _DeobResult

logger = logging.getLogger(__name__)

DEFAULT_MAX_DEPTH: int = 3
MAX_BATCH_SIZE: int = 1000

# Absolute path to deobfuscate.py — loaded directly to bypass rust_backend.__init__
# chain (avoids mlx_memory lock registration issues on cold start)
_DEOBFUSCATE_PY_PATH: str = (
    "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/_core/rust_backend/deobfuscate.py"
)


def _load_python_fallback_domain() -> object:
    importlib.util.spec_from_file_location("_deobfuscate_fallback", _DEOBFUSCATE_PY_PATH)
    code = open(_DEOBFUSCATE_PY_PATH).read()
    ns: dict = {}
    exec(compile(code, "deobfuscate.py", "exec"), ns)
    return ns["_PythonDeobfuscateDomain"]()


@dataclass
class DeobfuscationResult:
    candidates: list[str]
    layers_stripped: int
    encodings_detected: list[str]
    bytes_decoded: int

    @classmethod
    def from_backend(cls, result: _DeobResult) -> DeobfuscationResult:
        return cls(
            candidates=list(result.candidates),
            layers_stripped=result.layers_stripped,
            encodings_detected=list(result.encodings_detected),
            bytes_decoded=result.bytes_decoded,
        )

    @classmethod
    def empty(cls) -> DeobfuscationResult:
        return cls(candidates=[], layers_stripped=0, encodings_detected=[], bytes_decoded=0)


class DeobfuscationService:
    __slots__ = ("_domain", "_max_depth")

    def __init__(
        self,
        max_depth: int | None = None,
        domain: object | None = None,
    ) -> None:
        self._max_depth = max_depth if max_depth is not None else DEFAULT_MAX_DEPTH
        if domain is not None:
            self._domain = domain
        else:
            from _core.rust_backend import rust

            self._domain = rust.deobfuscate

    def decode_single(self, text: str, max_depth: int | None = None) -> DeobfuscationResult:
        if not text:
            return DeobfuscationResult.empty()
        depth = max_depth if max_depth is not None else self._max_depth
        raw = self._domain.decode(text, depth)
        return DeobfuscationResult.from_backend(raw)

    def decode_batch(self, texts: list[str], max_depth: int | None = None) -> list[DeobfuscationResult]:
        if not texts:
            return []
        depth = max_depth if max_depth is not None else self._max_depth
        if len(texts) > MAX_BATCH_SIZE:
            logger.warning(
                "[Deobfuscation] batch size %d exceeds cap %d, truncating",
                len(texts),
                MAX_BATCH_SIZE,
            )
            texts = texts[:MAX_BATCH_SIZE]
        raw_results = self._domain.batch_decode(texts, depth)
        return [DeobfuscationResult.from_backend(r) for r in raw_results]

    def get_telemetry(self) -> tuple[int, int, int]:
        return self._domain.telemetry()

    def reset_telemetry(self) -> None:
        self._domain.reset_telemetry()


_service_instance: DeobfuscationService | None = None
_service_init_error: str | None = None


def get_service() -> DeobfuscationService:
    global _service_instance, _service_init_error
    if _service_instance is not None:
        return _service_instance
    if _service_init_error is not None:
        fallback_cls = _load_python_fallback_domain()
        _service_instance = DeobfuscationService(domain=fallback_cls)
        return _service_instance
    try:
        _service_instance = DeobfuscationService()
        return _service_instance
    except (ValueError, ImportError, OSError) as exc:
        _service_init_error = str(exc)
        logger.warning(
            "[Deobfuscation] rust_backend unavailable (%s), using Python fallback",
            exc,
        )
        fallback_cls = _load_python_fallback_domain()
        _service_instance = DeobfuscationService(domain=fallback_cls)
        return _service_instance


def decode_ioc_candidates(text: str, max_depth: int = 3) -> list[str]:
    return get_service().decode_single(text, max_depth).candidates


def batch_decode_ioc_candidates(texts: list[str], max_depth: int = 3) -> list[list[str]]:
    return [r.candidates for r in get_service().decode_batch(texts, max_depth)]


def get_telemetry() -> dict[str, int]:
    passes, layers, bytes_decoded = get_service().get_telemetry()
    return {"passes": passes, "layers_stripped": layers, "bytes_decoded": bytes_decoded}


def reset_telemetry() -> None:
    get_service().reset_telemetry()
