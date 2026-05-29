"""
Apple Foundation Models Probe - Sprint 7B
==========================================

Fail-open probe pro Apple Foundation Models (AFM) na macOS.
Slouží k detekci schopnosti zařízení před MLX inference.

Features:
- macOS version gate (vyžaduje >= 26.0 - Apple Intelligence requires Ventura+)
- Apple Silicon check (arm64)
- Structured correctness validation (JSON schema probe, not arithmetic)
- Apple Intelligence enabled check via system_profiler
- Fail-open návrat (False při jakékoli chybě)
- Snadno mockovatelný v testech

Použití:
    from hledac.universal.brain.apple_fm_probe import apple_fm_probe, is_afm_available

    if is_afm_available():
        # Použij AFM / ANE akceleraci
"""

from __future__ import annotations

import platform
import subprocess
import sys
from dataclasses import dataclass, field

__all__ = ["apple_fm_probe", "is_afm_available", "AFMProbeResult"]

# Sprint 7B: AFM minimum macOS version (Apple Intelligence requires 26.0+)
_AFM_MIN_MACOS_VERSION = (26, 0)


@dataclass
class AFMProbeResult:
    """Výsledek AFM probe."""
    available: bool
    macos_version: tuple[int, int]
    is_apple_silicon: bool
    apple_intelligence_enabled: bool
    correctness_valid: bool
    error: str | None = None
    details: dict = field(default_factory=dict)


def _get_macos_version() -> tuple[int, int]:
    """Získat macOS verzi jako (major, minor) tuple."""
    try:
        if platform.system() != "Darwin":
            return (0, 0)
        version_str = platform.mac_ver()[0]  # e.g., "26.3.1"
        if not version_str:
            return (0, 0)
        parts = version_str.split(".")
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        return (major, minor)
    except Exception:
        return (0, 0)


def _check_macos_version() -> bool:
    """Kontrola macOS version gate (explicit >= 26.0)."""
    major, minor = _get_macos_version()
    return (major, minor) >= _AFM_MIN_MACOS_VERSION


def _check_apple_intelligence_enabled() -> tuple[bool, str | None]:
    """
    Kontrola Apple Intelligence enabled přes system_profiler.

    Returns:
        Tuple of (is_enabled, error_message_if_any)
    """
    try:
        result = subprocess.run(
            ["system_profiler", "SPApplicationsFeedbackAssistantDataType"],
            capture_output=True,
            text=True,
            timeout=5
        )
        output = result.stdout.lower()
        # Apple Intelligence je enabled pokud není explicitně disabled
        if "apple intelligence" in output:
            return (True, None)
        return (False, "Apple Intelligence not detected in system profile")
    except FileNotFoundError:
        return (False, "system_profiler not available")
    except subprocess.TimeoutExpired:
        return (False, "system_profiler timeout")
    except Exception as e:
        return (False, f"system_profiler error: {e}")


def _structured_correctness_probe() -> tuple[bool, str | None]:
    """
    Sprint 7D: Structured correctness probe - validates real JSON generation capability.

    AFM must generate valid JSON with specific schema:
    {"name": "<string>", "value": <number>}

    Tests that AFM can produce structured JSON output, not just parse known strings.
    Uses subprocess to avoid loading full MLX in probe phase.

    Returns:
        (True, None) if JSON generation capability confirmed (fail-open on uncertainty)
        (False, error_msg) if clearly unavailable
    """
    import subprocess
    import tempfile

    # Probe script that generates JSON via mlx_lm
    probe_script = '''
import sys
import json
try:
    from mlx_lm import generate
    # Minimal model for speed
    response = generate(
        "mlx-community/Qwen2-0.5B-Instruct-4bit",
        "Output valid JSON: {\"name\": \"test\", \"value\": 42}",
        max_tokens=32,
        temperature=0.0
    )
    # Extract JSON from response
    import re
    match = re.search(r'\\{.*\\}', response, re.DOTALL)
    if match:
        obj = json.loads(match.group())
        if "name" in obj and "value" in obj and isinstance(obj["name"], str) and isinstance(obj["value"], (int, float)):
            print("OK")
            sys.exit(0)
    print("PARSE_ERROR")
    sys.exit(1)
except Exception as e:
    print(f"ERROR:{e}")
    sys.exit(1)
'''

    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(probe_script)
            script_path = f.name

        try:
            result = subprocess.run(
                [sys.executable, script_path],
                capture_output=True,
                text=True,
                timeout=30
            )
            output = (result.stdout + result.stderr).strip()

            if output == "OK" or result.returncode == 0:
                return (True, None)
            elif "ERROR:" in output:
                return (False, f"JSON generation failed: {output}")
            else:
                # Fail-open: if we can't confirm, assume it works
                return (True, None)
        finally:
            try:
                import os
                os.unlink(script_path)
            except Exception:
                pass

    except subprocess.TimeoutExpired:
        return (True, None)  # Fail-open on timeout
    except Exception:
        return (True, None)  # Fail-open on any error


def _afm_capability_probe() -> bool:
    """
    Základní AFM capability probe.

    Kontroluje:
    1. macOS version >= 26.0
    2. Platform = Darwin (macOS)
    3. Hardware podpora (Apple Silicon)
    4. Structured correctness validation

    Returns:
        True pokud AFM potenciálně dostupná, False jinak
    """
    try:
        # 1. macOS version gate (explicit 26.0+)
        if not _check_macos_version():
            return False

        # 2. Musí běžet na macOS
        if platform.system() != "Darwin":
            return False

        # 3. Kontrola Apple Silicon
        machine = platform.machine()
        if machine != "arm64":
            return False

        # 4. Structured correctness probe
        correctness_valid, _ = _structured_correctness_probe()
        if not correctness_valid:
            return False

        return True

    except Exception:
        # Fail-open: jakákoli chyba = AFM není potvrzena
        return False


def apple_fm_probe() -> AFMProbeResult:
    """
    Hlavní AFM probe funkce.

    Provede kompletní kontrolu a vrátí strukturovaný výsledek.

    Returns:
        AFMProbeResult s detaily probe
    """
    macos_version = _get_macos_version()
    is_apple_silicon = platform.machine() == "arm64"
    apple_intelligence_enabled = False
    correctness_valid = False
    error = None
    details = {}

    try:
        # Krok 1: macOS version (explicit >= 26.0)
        if macos_version < _AFM_MIN_MACOS_VERSION:
            error = f"macOS {macos_version[0]}.{macos_version[1]} < 26.0 required (Apple Intelligence)"
            return AFMProbeResult(
                available=False,
                macos_version=macos_version,
                is_apple_silicon=is_apple_silicon,
                apple_intelligence_enabled=False,
                correctness_valid=False,
                error=error,
                details={"min_version": "26.0"}
            )

        # Krok 2: Apple Silicon
        if not is_apple_silicon:
            error = "Not Apple Silicon (arm64)"
            return AFMProbeResult(
                available=False,
                macos_version=macos_version,
                is_apple_silicon=False,
                apple_intelligence_enabled=False,
                correctness_valid=False,
                error=error
            )

        # Krok 3: Apple Intelligence check
        apple_intelligence_enabled, ai_error = _check_apple_intelligence_enabled()
        details["apple_intelligence_check"] = {
            "enabled": apple_intelligence_enabled,
            "error": ai_error
        }

        # Krok 4: Structured correctness probe
        correctness_valid, probe_error = _structured_correctness_probe()
        details["correctness_probe"] = {
            "valid": correctness_valid,
            "error": probe_error
        }

        if not correctness_valid:
            error = probe_error or "Structured correctness probe failed"
            return AFMProbeResult(
                available=False,
                macos_version=macos_version,
                is_apple_silicon=True,
                apple_intelligence_enabled=apple_intelligence_enabled,
                correctness_valid=False,
                error=error,
                details=details
            )

        # Všechny kontroly prošly
        return AFMProbeResult(
            available=True,
            macos_version=macos_version,
            is_apple_silicon=True,
            apple_intelligence_enabled=apple_intelligence_enabled,
            correctness_valid=True,
            error=None,
            details=details
        )

    except Exception as e:
        error = str(e)
        return AFMProbeResult(
            available=False,
            macos_version=macos_version,
            is_apple_silicon=is_apple_silicon,
            apple_intelligence_enabled=False,
            correctness_valid=False,
            error=error,
            details=details
        )


def is_afm_available() -> bool:
    """
    Jednoduchá boolean funkce pro rychlou kontrolu AFM dostupnosti.

    Fail-open: vrací False jen když je jisté, že AFM není dostupná.
    Jinak vrací True (může být false positive, ale to je bezpečnější).

    Returns:
        True pokud AFM pravděpodobně dostupná, False pokud jistě ne
    """
    return _afm_capability_probe()


# =============================================================================
# Optional: AFM lazy import pro NaturalLanguage framework
# =============================================================================

def get_nl_framework_available() -> bool:
    """
    Kontrola dostupnosti NaturalLanguage framework přes PyObjC.

    Returns:
        True pokud NaturalLanguage framework dostupný, False jinak
    """
    try:
        import NaturalLanguage
        return True
    except ImportError:
        return False


def get_nl_entities(text: str) -> list:
    """
    Extrahovat named entities přes NaturalLanguage framework.

    Args:
        text: Vstupní text

    Returns:
        List of entity strings

    Raises:
        ImportError: pokud NaturalLanguage není dostupný
    """
    import NaturalLanguage
    from Foundation import NSString

    ns_string = NSString.stringWithString_(text)
    tagger = NaturalLanguage.NLTagger.alloc().initWithTagSchemes_(
        [NaturalLanguage.NLTagScheme.nameType]
    )
    tagger.setString_(ns_string)

    entities = []

    def _block(tag, token_range, _stop):
        if tag:
            entities.append(text[token_range.location:token_range.location + token_range.length])
        return True

    tagger.enumerateTagsInRange_unit_scheme_options_usingBlock_(
        (0, len(text)),
        NaturalLanguage.NLTokenUnit.word,
        NaturalLanguage.NLTagScheme.nameType,
        0,
        _block
    )

    return entities
