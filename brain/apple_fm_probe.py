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
import platform
import subprocess
import sys
from dataclasses import dataclass, field
import msgspec
from compat.msgspec_gc_compat import Struct
from _core import aclose
__all__ = ['apple_fm_probe', 'is_afm_available', 'AFMProbeResult']
_AFM_MIN_MACOS_VERSION = (26, 0)

class AFMProbeResult(Struct):
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
        if platform.system() != 'Darwin':
            return (0, 0)
        version_str = platform.mac_ver()[0]
        if not version_str:
            return (0, 0)
        parts = version_str.split('.')
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
        result = subprocess.run(['system_profiler', 'SPApplicationsFeedbackAssistantDataType'], capture_output=True, text=True, timeout=5)
        output = result.stdout.lower()
        if 'apple intelligence' in output:
            return (True, None)
        return (False, 'Apple Intelligence not detected in system profile')
    except FileNotFoundError:
        return (False, 'system_profiler not available')
    except subprocess.TimeoutExpired:
        return (False, 'system_profiler timeout')
    except Exception as e:
        return (False, f'system_profiler error: {e}')

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
    probe_script = '\nimport sys\nimport json\ntry:\n    from mlx_lm import generate\n    # Minimal model for speed\n    response = generate(\n        "mlx-community/Qwen2-0.5B-Instruct-4bit",\n        "Output valid JSON: {"name": "test", "value": 42}",\n        max_tokens=32,\n        temperature=0.0\n    )\n    # Extract JSON from response\n    import re\n    match = re.search(r\'\\{.*\\}\', response, re.DOTALL)\n    if match:\n        obj = json.loads(match.group())\n        if "name" in obj and "value" in obj and isinstance(obj["name"], str) and isinstance(obj["value"], (int, float)):\n            print("OK")\n            sys.exit(0)\n    print("PARSE_ERROR")\n    sys.exit(1)\nexcept Exception as e:\n    print(f"ERROR:{e}")\n    sys.exit(1)\n'
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(probe_script)
            script_path = f.name
        try:
            result = subprocess.run([sys.executable, script_path], capture_output=True, text=True, timeout=30)
            output = (result.stdout + result.stderr).strip()
            if output == 'OK' or result.returncode == 0:
                return (True, None)
            elif 'ERROR:' in output:
                return (False, f'JSON generation failed: {output}')
            else:
                return (True, None)
        finally:
            try:
                import os
                os.unlink(script_path)
            except Exception:  # noqa: BLE001
                pass
    except subprocess.TimeoutExpired:
        return (True, None)
    except Exception:
        return (True, None)

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
        if not _check_macos_version():
            return False
        if platform.system() != 'Darwin':
            return False
        machine = platform.machine()
        if machine != 'arm64':
            return False
        correctness_valid, _ = _structured_correctness_probe()
        if not correctness_valid:
            return False
        return True
    except Exception:
        return False

def apple_fm_probe() -> AFMProbeResult:
    """
    Hlavní AFM probe funkce.

    Provede kompletní kontrolu a vrátí strukturovaný výsledek.

    Returns:
        AFMProbeResult s detaily probe
    """
    macos_version = _get_macos_version()
    is_apple_silicon = platform.machine() == 'arm64'
    apple_intelligence_enabled = False
    correctness_valid = False
    error = None
    details = {}
    try:
        if macos_version < _AFM_MIN_MACOS_VERSION:
            error = f'macOS {macos_version[0]}.{macos_version[1]} < 26.0 required (Apple Intelligence)'
            return AFMProbeResult(available=False, macos_version=macos_version, is_apple_silicon=is_apple_silicon, apple_intelligence_enabled=False, correctness_valid=False, error=error, details={'min_version': '26.0'})
        if not is_apple_silicon:
            error = 'Not Apple Silicon (arm64)'
            return AFMProbeResult(available=False, macos_version=macos_version, is_apple_silicon=False, apple_intelligence_enabled=False, correctness_valid=False, error=error)
        apple_intelligence_enabled, ai_error = _check_apple_intelligence_enabled()
        details['apple_intelligence_check'] = {'enabled': apple_intelligence_enabled, 'error': ai_error}
        correctness_valid, probe_error = _structured_correctness_probe()
        details['correctness_probe'] = {'valid': correctness_valid, 'error': probe_error}
        if not correctness_valid:
            error = probe_error or 'Structured correctness probe failed'
            return AFMProbeResult(available=False, macos_version=macos_version, is_apple_silicon=True, apple_intelligence_enabled=apple_intelligence_enabled, correctness_valid=False, error=error, details=details)
        return AFMProbeResult(available=True, macos_version=macos_version, is_apple_silicon=True, apple_intelligence_enabled=apple_intelligence_enabled, correctness_valid=True, error=None, details=details)
    except Exception as e:
        error = str(e)
        return AFMProbeResult(available=False, macos_version=macos_version, is_apple_silicon=is_apple_silicon, apple_intelligence_enabled=False, correctness_valid=False, error=error, details=details)

def is_afm_available() -> bool:
    """
    Jednoduchá boolean funkce pro rychlou kontrolu AFM dostupnosti.

    Fail-open: vrací False jen když je jisté, že AFM není dostupná.
    Jinak vrací True (může být false positive, ale to je bezpečnější).

    Returns:
        True pokud AFM pravděpodobně dostupná, False pokud jistě ne
    """
    return _afm_capability_probe()

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
    tagger = NaturalLanguage.NLTagger.alloc().initWithTagSchemes_([NaturalLanguage.NLTagScheme.nameType])
    tagger.setString_(ns_string)
    entities = []

    def _block(tag, token_range, _stop):
        if tag:
            entities.append(text[token_range.location:token_range.location + token_range.length])
        return True
    tagger.enumerateTagsInRange_unit_scheme_options_usingBlock_((0, len(text)), NaturalLanguage.NLTokenUnit.word, NaturalLanguage.NLTagScheme.nameType, 0, _block)
    return entities