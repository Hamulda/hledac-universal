# compat/rust_backend_legacy_v1.py — R-23: ABI v1 legacy compatibility shim
"""
Compatibilní vrstva pro staré callery, které vyžadují ABI version 1.0.


Pozadí:
    Některé external callery (např. staré verze integrací, third-party nástroje)
    mohou mít hardcoded očekávání, že hledac_rust_extensions.__abi_version__()
    vrací presně (1, 0, 0). Tento modul poskytuje transparentní fallback
    pro případ, kdy Rust extension není dostupná.

Použití:
    # Místo:
    #   from core.rust_backend import get_accel
    # Staré callery mohou použít:
    from compat.rust_backend_legacy_v1 import get_legacy_backend

    backend = get_legacy_backend()
    if backend.is_available:
        backend.ioc.extract_iocs_flat(text)
    else:
        # Graceful degradation — použij čistě Python fallback
        from hledac.universal.core.optional_imports import optional
        rust = optional("hledac.universal.core.rust_backend")
        if rust is not None:
            accel = rust.get_accel()
        else:
            raise ImportError(
                "Ani Rust extension, ani core.rust_backend nejsou dostupné. "
                "Sestavte Rust extension: cd rust_extensions && maturin develop --release"
            )

ABI Versioning Policy (R-23):
    - (major, minor, patch): major = breaking change, minor = new API, patch = bugfix
    - ABI 1.0.0 je baseline — jakýkoliv major > 1 vyžaduje rebuild
    - Minor/patch mismatch je backward-compatible (log warning)
    - Python-side kontrola při importu: viz core.rust_backend._prober.probe()
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from core import aclose

if TYPE_CHECKING:
    from typing import Any

logger = logging.getLogger(__name__)

# Konstanty musí odpovídat Rust ABI_VERSION v lib.rs:625
_LEGACY_EXPECTED_ABI: tuple[int, int, int] = (1, 0, 0)
_LEGACY_EXPECTED_ABI_MAJOR: int = 1
_legacy_backend_instance: "_LegacyRustBackend | None" = None


def get_legacy_backend() -> "_LegacyRustBackend":
    """
    Vrací cached _LegacyRustBackend instanci (singleton).

    Tato funkce provádí lazy probe Rust extension při prvním volání.
    Výsledek je cachován — repeated volání vrací stejnou instanci.
    """
    global _legacy_backend_instance
    if _legacy_backend_instance is None:
        _legacy_backend_instance = _LegacyRustBackend()
    return _legacy_backend_instance


class _LegacyRustBackend:
    """
    Legacy-compatibilní wrapper kolem Rust extension.

    Poskytuje stejný API jako core.rust_backend.AccelBackend,
    ale navíc poskytuje atributy specifické pro ABI v1:
    - abi_version: (major, minor, patch)
    - is_abi_compatible: bool — True pokud ABI verze sedí
    - backend_type: "rust" | "python"
    """

    __slots__ = ("_probe_result", "_accel")

    def __init__(self) -> None:
        from hledac.universal.core.rust_backend._prober import probe as _rust_probe

        # Probe se provádí pouze jednou — modul _prober.py cachuje výsledek
        self._probe_result = _rust_probe()

        # Lazy-load AccelBackend — načítá se až při prvním delegovaném volání
        self._accel: "Any" = None

    @property
    def is_available(self) -> bool:
        """True pokud je Rust extension dostupná a ABI je kompatibilní."""
        return self._probe_result.is_compatible

    @property
    def abi_version(self) -> tuple[int, int, int]:
        """ABI verze Rust extension, např. (1, 0, 0)."""
        return self._probe_result.abi_version

    @property
    def is_abi_compatible(self) -> bool:
        """
        True pokud extension ABI major verze odpovídá očekávané (1.x.x).

        False znamená, že je třeba rebuild:
            Run: cd rust_extensions && maturin develop --release
        """
        return self._probe_result.abi_major == _LEGACY_EXPECTED_ABI_MAJOR

    @property
    def backend_type(self) -> str:
        """'rust' pokud je Rust extension aktivní, 'python' pokud je fallback."""
        return self._probe_result.backend

    @property
    def capability_score(self) -> float:
        """Podíl dostupných symbolů v Rust extension (0.0–1.0)."""
        return self._probe_result.capability_score

    @property
    def apple_target(self) -> str | None:
        """Apple target triple, např. 'aarch64-apple-darwin'."""
        return self._probe_result.apple_target

    @property
    def py_version(self) -> tuple[int, int, int] | None:
        """Python verze, pro kterou byl extension kompilován."""
        return self._probe_result.py_version

    def _get_accel(self) -> Any:
        """Lazy getter pro AccelBackend — načítá se až při prvním volání."""
        if self._accel is None:
            from hledac.universal.core.rust_backend import get_accel

            self._accel = get_accel()
        return self._accel

    def __getattr__(self, name: str) -> Any:
        """
        Transparentní delegace na AccelBackend.

        Podporuje:
            backend.ioc.extract_iocs_flat(text)
            backend.bloom.BloomFilter(...)
            backend.url.normalize_url(url)
        """
        if name in ("_probe_result", "_accel"):
            raise AttributeError(name)
        accel = self._get_accel()
        return getattr(accel, name)
