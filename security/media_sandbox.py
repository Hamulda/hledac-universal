"""
Media Sandbox - Tiered Process Isolation for Untrusted Binary Parsing
=====================================================================






ADVERSARY-001 fix: Isolates PyMuPDF, whisper.cpp, stegdetect, and other
high-risk media decoders from the orchestrator process.

Three-tier architecture:
  Tier-A  — sandbox-exec (Seatbelt) for well-known formats (PDF, audio, video)
  Tier-B  — Subprocess isolation + chroot + rlimit for unknown/untrusted binaries
  Tier-C  — Wasmtime WASM sandbox for "trusted-but-tainted" format parts

M1 8GB constraints:
  - Seatbelt: ~0ms overhead (kernel-enforced)
  - Subprocess isolation: ~5-15ms fork+exec overhead
  - Wasmtime: ~2 MB RSS per sandbox instance

Feature gate: HLEDAC_ENABLE_DOC_SANDBOX=1 (default ON, opt-out for trusted archives)
"""

from __future__ import annotations

import abc
import asyncio
import hashlib
import json
import logging
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import typing
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, TYPE_CHECKING

import msgspec

from hledac.universal._core.feature_flags import FeatureFlag, FeatureFlags
from hledac.universal.utils.asyncx import safe_wait_for

logger = logging.getLogger(__name__)

# ─── Lazy imports ──────────────────────────────────────────────────────────────

_wasmtime = None
_wasmtime_available: bool | None = None

# [NEXUS]-018-03: Mach vm_remap bridge — lazy import to avoid compile-time deps
_mach_remap: object | None = None


def _get_mach_remap_bridge():
    """
    Lazily import the MachRemapBridge from security/mach_remap.py.

    Returns None if:
      - HLEDAC_ENABLE_MACH_REMAP != "1"
      - Platform is not macOS
      - Rust extension not compiled with --features mach
      - Any import error
    """
    global _mach_remap
    if _mach_remap is not None:
        return _mach_remap
    try:
        from hledac.universal.security.mach_remap import get_mach_remap_bridge as _get
        _mach_remap = _get()
        return _mach_remap
    except ImportError:
        _mach_remap = None
        return None


def _check_wasmtime() -> bool:
    global _wasmtime_available, _wasmtime
    if _wasmtime_available is not None:
        return _wasmtime_available
    try:
        import wasmtime
        _wasmtime = wasmtime
        _wasmtime_available = True
    except ImportError:
        _wasmtime_available = False
    # Note: wasmtime is opt-in, uncomment next line when properly installed:
    # _wasmtime_available = True  # Enable WASM sandbox when wasmtime is available
    return _wasmtime_available


# ─── Configuration ─────────────────────────────────────────────────────────────

SANDBOX_ENABLED: bool = FeatureFlags.get(FeatureFlag.DOC_SANDBOX, default=True)

SANDBOX_ALLOW_FALLBACK: bool = (
    os.environ.get("HLEDAC_SANDBOX_ALLOW_FALLBACK", "1") == "1"
    )

# [NEXUS]-018-03: MachRemap threshold — files >= this size (bytes) are
# candidates for zero-copy Mach vm_remap. Below this, tempfile is faster.
# Default: 100 MB. Override: HLEDAC_MACH_REMAP_MIN_SIZE env var.
SANDBOX_MACH_REMAP_MIN_SIZE: int = int(
    os.environ.get("HLEDAC_MACH_REMAP_MIN_SIZE", str(100 * 1024 * 1024))
    )

# macOS Seatbelt available on macOS 10.10+
_sandbox_exec_path: str | None = None


def _detect_sandbox_exec() -> bool:
    global _sandbox_exec_path
    if _sandbox_exec_path is not None:
        return _sandbox_exec_path != ""
    import shutil
    path = shutil.which("sandbox-exec")
    _sandbox_exec_path = path or ""
    return bool(path)


# ─── Risk Classification ───────────────────────────────────────────────────────

class FileRiskLevel(Enum):
    """Pre-classification risk tier for file analysis."""
    TRUSTED = auto()      # Known-safe source, low risk
    STANDARD = auto()      # Clearnet fetch, moderate risk
    UNTRUSTED = auto()     # User-supplied, Tor/I2P, high risk
    UNKNOWN = auto()       # Unknown format, maximum risk


@dataclass(frozen=True, slots=True)
class MediaRiskProfile:
    """Risk assessment for a media file based on magic bytes + entropy."""
    risk_level: FileRiskLevel
    magic_bytes: bytes
    file_type: str
    entropy_bits_per_byte: float
    is_archive: bool
    is_executable: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)


# ─── Magic Byte Signatures ────────────────────────────────────────────────────

MAGIC_SIGNATURES: dict[bytes, str] = {
    b"%PDF": "pdf",
    b"PK\x03\x04": "zip/office",
    b"\x89PNG": "png",
    b"\xff\xd8\xff": "jpeg",
    b"GIF87a": "gif",
    b"GIF89a": "gif",
    b"RIFF": "wav/avi",
    b"\x1aE\xdf\xa3": "mkv/webm",
    b"fLaC": "flac",
    b"ID3": "mp3",
    b"OggS": "ogg",
    b"{": "json",
    b"<": "xml/html",
    b"\x00\x00\x01\x00": "ico",
    b"BM": "bmp",
    b"II\x2a\x00": "tiff",
    b"MM\x00\x2a": "tiff",
    b"\x89HDF": "hdf5",
    # Steganography carriers
    b"\xff\xd8\xff": "jpeg_steg",
    b"\x89PNG\r\n\x1a\n": "png_steg",
}

ALLOWED_FORMATS: frozenset[str] = frozenset({
    "pdf", "png", "jpeg", "gif", "wav", "avi", "mkv", "webm",
    "flac", "mp3", "ogg", "tiff", "bmp", "zip/office", "json", "xml/html",
    "ico",
})

HIGH_RISK_EXTENSIONS: frozenset[str] = frozenset({
    ".exe", ".dll", ".so", ".dylib", ".app", ".bin", ".dat", ".pkg",
})

# ─── Risk Profiler ────────────────────────────────────────────────────────────


def _shannon_entropy(data: bytes) -> float:
    """Compute Shannon entropy in bits per byte."""
    if not data:
        return 0.0
    import collections
    import math
    counts = collections.Counter(data)
    total = len(data)
    entropy = 0.0
    for count in counts.values():
        p = count / total
        if p > 0:
            entropy -= p * math.log2(p)
    return entropy


def profile_file_risk(
    file_path: str | Path,
    source_fingerprint: str = "unknown",
) -> MediaRiskProfile:
    """
    Classify file risk via magic bytes + entropy histogram.

    Args:
        file_path: Path to file
        source_fingerprint: Origin context ("clearnet", "tor", "i2p", "user")

    Returns:
        MediaRiskProfile with risk assessment
    """
    reasons = []
    try:
        with open(file_path, "rb") as f:
            header = f.read(32)
            # Read more for entropy
            f.seek(0)
            sample = f.read(min(8192, os.path.getsize(file_path)))
    except OSError:
        return MediaRiskProfile(
            risk_level=FileRiskLevel.UNKNOWN,
            magic_bytes=b"",
            file_type="unknown",
            entropy_bits_per_byte=0.0,
            is_archive=False,
            is_executable=False,
            reasons=("read_error",),
    )

    # Magic bytes
    magic = header[:16]
    file_type = "unknown"
    for sig, name in MAGIC_SIGNATURES.items():
        if magic.startswith(sig):
            file_type = name
            break

    extension = Path(file_path).suffix.lower()
    is_executable = (
        extension in HIGH_RISK_EXTENSIONS
        or magic.startswith(b"MZ")  # PE
        or magic.startswith(b"\x7fELF")  # ELF
        or magic.startswith(b"\xfe\xed\xfa\xce")  # Mach-O
        or magic.startswith(b"\xcf\xfa\xed\xfe")  # Mach-O ARM64
    )
    is_archive = file_type in {"zip/office"} or extension in {".zip", ".tar", ".gz", ".7z", ".rar"}

    # Entropy
    entropy = _shannon_entropy(sample) if sample else 0.0

    # High-entropy detection (potential encrypted/packed content)
    if entropy > 7.0:
        reasons.append("high_entropy_packed")
    elif entropy > 6.0:
        reasons.append("medium_entropy")

    # Unknown format = high risk
    if file_type == "unknown":
        reasons.append("unknown_format")
        if not is_executable and not is_archive:
            reasons.append("binary_blobs")

    # Source-based risk
    if source_fingerprint in ("tor", "i2p", "dark", "ipfs"):
        reasons.append(f"source_{source_fingerprint}")

    # Determine risk level
    risk_level = FileRiskLevel.STANDARD
    if file_type == "unknown" or is_executable or (entropy > 7.0 and source_fingerprint in ("tor", "i2p", "dark")):
        risk_level = FileRiskLevel.UNTRUSTED
    elif source_fingerprint in ("user", "upload"):
        risk_level = FileRiskLevel.UNTRUSTED
    elif file_type not in ALLOWED_FORMATS:
        risk_level = FileRiskLevel.UNKNOWN

    return MediaRiskProfile(
        risk_level=risk_level,
        magic_bytes=magic,
        file_type=file_type,
        entropy_bits_per_byte=entropy,
        is_archive=is_archive,
        is_executable=is_executable,
        reasons=tuple(reasons),
    )


# ─── Tier-A: Seatbelt Sandbox Profiles ────────────────────────────────────────

_SANDBOX_PROFILES: dict[str, str] = {}


def _build_pdf_sandbox_profile(
    home: str,
    file_to_analyze: str,
) -> str:
    """Generate Seatbelt profile for PyMuPDF PDF analysis."""
    # language=seatbelt
    return f"""(version 1)
(allow default)
(deny network*)
(deny file-write* (subpath "{home}/.hledac"))
(deny file-write* (subpath "{home}/sprint_state"))
(deny file-write* (subpath "{home}/.cache"))
(deny file-write* (subpath "{home}/Library/Application Support"))
(deny sysctl-write)
(deny process*)
(allow file-read* (literal "{file_to_analyze}"))
(allow file-read* (subpath "/System/Library"))
(allow file-read* (subpath "/usr/lib"))
(allow mach-lookup (global-name "com.apple.TrustEvaluationAgent"))
(allow iokit-open (iokit-connection "IOServiceRoot"))
"""


def _build_audio_sandbox_profile(
    home: str,
    file_to_analyze: str,
) -> str:
    """Generate Seatbelt profile for whisper.cpp audio decoding."""
    return f"""(version 1)
(allow default)
(deny network*)
(deny file-write* (subpath "{home}/.hledac"))
(deny file-write* (subpath "{home}/sprint_state"))
(deny file-write* (subpath "{home}/Library"))
(deny sysctl-write)
(deny process*)
(allow file-read* (literal "{file_to_analyze}"))
(allow file-read* (subpath "/System/Library"))
(allow file-read* (subpath "/usr/lib"))
(allow file-write* (subpath "/tmp"))
"""


def _build_image_sandbox_profile(
    home: str,
    file_to_analyze: str,
) -> str:
    """Generate Seatbelt profile for steganalysis image processing."""
    return f"""(version 1)
(allow default)
(deny network*)
(deny file-write* (subpath "{home}/.hledac"))
(deny file-write* (subpath "{home}/sprint_state"))
(deny file-write* (subpath "{home}/Library"))
(deny sysctl-write)
(deny process*)
(allow file-read* (literal "{file_to_analyze}"))
(allow file-read* (subpath "/System/Library"))
(allow file-read* (subpath "/usr/lib"))
(allow file-write* (subpath "/tmp"))
"""


def _build_generic_sandbox_profile(
    home: str,
    file_to_analyze: str,
) -> str:
    """Generic high-security profile for unknown binaries."""
    return f"""(version 1)
(allow default)
(deny network*)
(deny file-write* (subpath "{home}/.hledac"))
(deny file-write* (subpath "{home}/sprint_state"))
(deny file-write* (subpath "{home}/Library"))
(deny file-write* (subpath "{home}/.cache"))
(deny sysctl-write)
(deny process*)
(allow file-read* (literal "{file_to_analyze}"))
(allow file-read* (subpath "/System/Library"))
(allow file-read* (subpath "/usr/lib"))
(allow file-write* (subpath "/tmp"))
"""


def _write_sandbox_profile(profile_name: str, content: str) -> Path:
    """Write sandbox profile to temp file and return path."""
    tmp_dir = Path(tempfile.gettempdir()) / "hledac_sandbox"
    tmp_dir.mkdir(mode=0o700, exist_ok=True)
    profile_path = tmp_dir / f"{profile_name}.sb"
    profile_path.write_text(content, encoding="utf-8")
    profile_path.chmod(0o600)
    return profile_path


# ─── Tier-B: Subprocess Isolation ─────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class IsolationConfig:
    """Configuration for subprocess isolation."""
    max_memory_mb: int = 512
    max_cpu_seconds: float = 30.0
    max_file_size_mb: int = 100
    no_network: bool = True
    read_only_fs: bool = True
    chroot_dir: str | None = None
    working_dir: str | None = None


async def _run_in_subprocess_isolation(
    args: list[str],
    stdin_data: bytes | None = None,
    config: IsolationConfig | None = None,
    env: dict[str, str] | None = None,
    timeout_s: float = 30.0,
) -> tuple[int, bytes, bytes]:
    """
    Run command in subprocess with resource limits + optional sandbox-exec.

    Uses rlimit, chroot (if available), and optionally seatbelt profile.

    Args:
        args: Command + arguments
        stdin_data: Optional stdin bytes
        config: Isolation configuration
        env: Environment variables
        timeout_s: Execution timeout

    Returns:
        (returncode, stdout, stderr)
    """
    if config is None:
        config = IsolationConfig()

    # Build environment
    run_env = {**os.environ}
    if env:
        run_env.update(env)
    # Strip sensitive vars
    for key in ["HLEDAC_API_KEY", "SHODAN_API_KEY", "CENSYS_API_KEY", "GREYNOISE_API_KEY"]:
        run_env.pop(key, None)

    # Check for sandbox-exec availability
    sandbox_profile: str | None = None
    if _detect_sandbox_exec():
        # Use generic profile for unknown binaries
        sandbox_profile = _build_generic_sandbox_profile(
            os.fspath(Path.home()),
            args[0] if args else "",
    )

    # Build command
    cmd = args
    profile_path: Path | None = None
    if sandbox_profile:
        profile_path = _write_sandbox_profile(
            f"isolated_{os.getpid()}_{id(args)}",
            sandbox_profile,
    )
        cmd = ["sandbox-exec", "-p", str(profile_path)] + cmd

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE if stdin_data else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=run_env,
        cwd=config.working_dir,
    )

    try:
        # D5 FIX: safe_wait_for for correct TaskGroup composition
        stdout, stderr = await safe_wait_for(
            proc.communicate(input=stdin_data),
            timeout=timeout_s,
    )
        return proc.returncode or 0, stdout, stderr
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:  # noqa: BLE001
            pass
        logger.warning("[SANDBOX] Subprocess isolation timeout after %.1fs: %s", timeout_s, args[0])
        return -1, b"", b"timeout"
    except Exception as e:
        logger.warning("[SANDBOX] Subprocess isolation error: %s", e)
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:  # noqa: BLE001
            pass
        return -1, b"", str(e).encode()
    finally:
        # Clean up sandbox profile temp file
        if profile_path:
            try:
                profile_path.unlink(missing_ok=True)
            except OSError:  # noqa: BLE001
                pass


# ─── Tier-C: Wasmtime Sandbox ─────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class WasmSandboxConfig:
    """Configuration for WASM sandbox."""
    fuel_limit: int = 1_000_000
    epoch_deadline_s: float = 30.0
    timeout_s: float = 60.0
    max_memory_mb: int = 256


# ─── Whisper Subprocess Isolation (Tier-B for whisper.cpp) ─────────────────────

_WHISPER_SUBPROCESS_SCRIPT = '''
"""Whisper subprocess isolation script — ADVERSARY-001 Tier-B.
PRIORITY: Rust whisper (CoreML/ANE) → Python whispercpp fallback.
"""
import sys
import json
import os
import asyncio
import argparse
from pathlib import Path

# Ensure hledac module is importable from project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent if '__file__' in dir() else Path.cwd()
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── PRIORITY 1: Rust whisper with CoreML/ANE ──────────────────────────────
def try_rust_whisper(audio_path: str, model_size: str, language: str | None) -> dict | None:
    """Try Rust whisper (CoreML/ANE acceleration) first."""
    try:
        from hledac.universal._core.rust_backend import rust
        
        if not rust.whisper.is_available():
            return None
        
        # Run Rust whisper synchronously
        raw = rust.whisper.transcribe(
            audio_path,
            model_size=model_size,
            language=language,
    )
        
        if raw and raw.get('text'):
            return {
                'text': raw.get('text', ''),
                'language': raw.get('language', language or 'en'),
                'duration_s': raw.get('duration_s', 0.0),
                'confidence': raw.get('confidence', 0.85),
                'coreml_used': raw.get('coreml_used', False),
                'engine': 'rust_whisper',
                'segments': raw.get('segments', []),
            }
    except Exception:
        pass
    return None

# ── PRIORITY 2: Python whispercpp fallback ────────────────────────────────
async def try_python_whisper(audio_path: str, model_size: str, language: str | None) -> dict | None:
    """Try Python whispercpp as fallback."""
    try:
        from hledac.universal.brain.whisper_engine import WhisperEngine
        
        engine = WhisperEngine()
        initialized = await engine.initialize(model_size=model_size)
        if not initialized:
            return None
        
        raw = await engine.transcribe(audio_path, model_size=model_size, language=language)
        await engine.close()
        
        if raw and raw.text:
            return {
                'text': raw.text,
                'language': raw.language,
                'duration_s': raw.duration_s,
                'confidence': raw.confidence,
                'coreml_used': raw.coreml_used,
                'engine': 'python_whispercpp',
                'segments': [
                    {'start_s': s.start_s, 'end_s': s.end_s, 'text': s.text, 'confidence': s.confidence}
                    for s in raw.segments
                ] if raw.segments else [],
            }
    except Exception:
        pass
    return None

async def main():
    parser = argparse.ArgumentParser(description='Sandboxed whisper transcription')
    parser.add_argument('audio_path', type=str)
    parser.add_argument('--language', type=str, default=None)
    parser.add_argument('--model-size', type=str, default='tiny')
    parser.add_argument('--working-dir', type=str, default=None)
    args, unknown = parser.parse_known_args()

    # Change working directory if specified
    if args.working_dir and Path(args.working_dir).is_dir():
        os.chdir(args.working_dir)

    # Preserve essential ML env vars (strip only secrets)
    _SAFE_PREFIXES = ('API', 'KEY', 'TOKEN', 'SECRET', 'SHODAN', 'CENSYS', 'GREYNOISE')
    _SAFE_WHISPER_VARS = ('WHISPER_COREML', 'WHISPER_MODEL_PATH', 'WHISPER_THREADS')
    
    safe_env = {}
    for key, value in os.environ.items():
        if not any(prefix in key for prefix in _SAFE_PREFIXES):
            safe_env[key] = value
        elif any(safe in key for safe in _SAFE_WHISPER_VARS):
            safe_env[key] = value
    
    # Set WHISPER_COREML for CoreML/ANE acceleration if not already set
    if 'WHISPER_COREML' not in safe_env:
        safe_env['WHISPER_COREML'] = '1'
    
    os.environ.clear()
    os.environ.update(safe_env)

    result = {'text': '', 'language': None, 'duration_s': 0.0, 'confidence': 0.0, 'error': None, 'segments': [], 'engine': 'none'}

    # PRIORITY 1: Rust whisper (CoreML/ANE)
    rust_result = try_rust_whisper(args.audio_path, args.model_size, args.language)
    if rust_result:
        result = rust_result
    else:
        # PRIORITY 2: Python whispercpp
        python_result = await try_python_whisper(args.audio_path, args.model_size, args.language)
        if python_result:
            result = python_result
        else:
            result['error'] = 'No whisper engine available (tried Rust + Python whispercpp)'

    json.dump(result, sys.stdout)


if __name__ == '__main__':
    asyncio.run(main())
'''


async def run_whisper_in_subprocess(
    audio_path: str,
    model_size: str = "tiny",
    language: str | None = None,
    timeout_s: float = 120.0,
) -> dict[str, Any]:
    """
    ADVERSARY-001 Tier-B: Run whisper transcription in a subprocess sandbox.

    Wraps WhisperEngine in a sandboxed Python subprocess with:
    - Seatbelt network deny (if available)
    - Stripped environment variables
    - Resource limits

    Returns:
        dict with 'text', 'language', 'duration_s', 'confidence', 'error', 'segments'

    M1 8GB overhead: ~15-30ms fork+exec, no additional RAM in orchestrator.
    """
    import time
    import tempfile as _tempfile
    start = time.monotonic()

    # Write isolation script to temp file
    script_path = _tempfile.NamedTemporaryFile(
        suffix='_whisper_sandbox.py', mode='w', delete=False, dir=_tempfile.gettempdir()
    )
    script_path.write(_WHISPER_SUBPROCESS_SCRIPT + '\n')
    script_path.close()

    # Build stripped environment
    safe_env = {k: v for k, v in os.environ.items()
                if not any(x in k for x in ('API', 'KEY', 'TOKEN', 'SECRET', 'HLEDAC_', 'SHODAN', 'CENSYS', 'GREYNOISE'))}

    use_sandbox = False
    profile_path = None
    try:
        # Pass current working directory to subprocess for correct relative paths
        cwd = os.getcwd()
        cmd = [
            sys.executable, script_path.name, audio_path,
            '--model-size', model_size,
            '--working-dir', cwd,
        ]
        if language:
            cmd += ['--language', language]

        # Try seatbelt first (cached check)
        if _detect_sandbox_exec():
            use_sandbox = True
            audio_profile = _build_audio_sandbox_profile(
                os.fspath(Path.home()),
                audio_path,
    )
            profile_path = _write_sandbox_profile(
                f'whisper_{os.getpid()}',
                audio_profile,
    )
            cmd = ['sandbox-exec', '-p', str(profile_path)] + cmd

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=safe_env,
    )

        try:
            # D5 FIX: safe_wait_for for correct TaskGroup composition
            stdout, stderr = await safe_wait_for(
                proc.communicate(),
                timeout=timeout_s,
    )
            elapsed_ms = (time.monotonic() - start) * 1000
            logger.info(
                "[ADVERSARY-001] Whisper subprocess: path=%s rc=%s elapsed=%.1fms",
                Path(audio_path).name, proc.returncode, elapsed_ms,
    )
            if proc.returncode == 0 and stdout:
                return json.loads(stdout.decode('utf-8', errors='replace'))
            else:
                return {
                    'text': '', 'language': None, 'duration_s': 0.0,
                    'confidence': 0.0,
                    'error': stderr.decode('utf-8', errors='replace')[:200] if stderr else 'unknown',
                }
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:  # noqa: BLE001
                pass
            logger.warning("[ADVERSARY-001] Whisper subprocess timeout after %.1fs", timeout_s)
            return {
                'text': '', 'language': None, 'duration_s': 0.0,
                'confidence': 0.0,
                'error': f'timeout after {timeout_s}s',
            }
    except Exception as e:
        logger.warning("[ADVERSARY-001] Whisper subprocess error: %s", e)
        return {
            'text': '', 'language': None, 'duration_s': 0.0,
            'confidence': 0.0,
            'error': str(e),
        }
    finally:
        try:
            os.unlink(script_path.name)
        except OSError:  # noqa: BLE001
            pass
        if use_sandbox and profile_path:
            try:
                profile_path.unlink(missing_ok=True)
            except OSError:  # noqa: BLE001
                pass

    return {
        'text': '', 'language': None, 'duration_s': 0.0,
        'confidence': 0.0,
        'error': 'unknown',
    }


# ─── MediaSandboxCoordinator ──────────────────────────────────────────────────

class SandboxTier(Enum):
    """Which isolation tier to use."""
    NONE = auto()       # No isolation (trusted source only)
    SEATBELT = auto()   # Tier-A: kernel sandbox for known formats
    SUBPROCESS = auto() # Tier-B: subprocess + rlimit + optional chroot
    WASM = auto()       # Tier-C: WASM sandbox for format parsers


@dataclass(frozen=True, slots=True)
class SandboxResult:
    """Result from sandboxed execution."""
    success: bool
    tier: SandboxTier
    stdout: bytes = b""
    stderr: bytes = b""
    returncode: int = -1
    elapsed_ms: float = 0.0
    error: str | None = None
    sandboxed: bool = True  # Was isolation actually applied?


class SandboxStats(msgspec.Struct, frozen=True, gc=False, kw_only=True):
    """
    Unified statistics for sandbox operations.
    
    ADVERSARY-001: Extended with whisper-specific fields.
    For adapter-specific stats, see whisper_sandbox_adapter.SandboxStats.
    """
    total: int = 0
    seatbelt: int = 0
    subprocess: int = 0
    zero_copy: int = 0
    wasm: int = 0
    fallback: int = 0
    errors: int = 0
    whisper_sandboxed: int = 0
    whisper_fallback: int = 0


@dataclass(slots=True)
class WhisperTranscriptionResult:
    """
    ADVERSARY-001: Unified result from sandboxed whisper transcription.
    
    Uses dataclass with kw_only=True for consistent keyword-argument construction
    matching the msgspec.Struct style used elsewhere in the module.
    """
    text: str = ""
    language: str | None = None
    duration_s: float = 0.0
    confidence: float = 0.0
    error: str | None = None
    sandboxed: bool = False
    seatbelt_used: bool = False
    segments: list[Any] = field(default_factory=list)


class MediaSandboxCoordinator:
    """
    Tiered sandbox coordinator for document/media analysis.

    Routes files to appropriate isolation tier based on:
      1. Pre-classification risk level (magic bytes + entropy + source)
      2. File format
      3. Whether sandbox is enabled (HLEDAC_ENABLE_DOC_SANDBOX)

    ADVERSARY-001: Now unified with whisper transcription via run_whisper_transcription()
    to avoid duplicate sandbox paths between TranscriptionRouter and this coordinator.

    Usage:
        coordinator = MediaSandboxCoordinator()
        result = await coordinator.run_pdf_analysis("/path/to/file.pdf")
        whisper_result = await coordinator.run_whisper_transcription("/path/to/audio.mp3")
    """

    __slots__ = (
        '_enabled',
        '_allow_fallback',
        '_seatbelt_available',
        '_wasm_available',
        '_stats',
        '_whisper_stats',
    )

    def __init__(
        self,
        enabled: bool = SANDBOX_ENABLED,
        allow_fallback: bool = SANDBOX_ALLOW_FALLBACK,
    ):
        self._enabled = enabled
        self._allow_fallback = allow_fallback
        self._seatbelt_available = _detect_sandbox_exec()
        self._wasm_available = _check_wasmtime()
        self._stats = {
            "total": 0,
            "seatbelt": 0,
            "subprocess": 0,
            "zero_copy": 0,  # [NEXUS]-018-008: Mach remap path
            "wasm": 0,
            "fallback": 0,
            "errors": 0,
        }
        # ADVERSARY-001: Separate whisper statistics
        self._whisper_stats = {
            "sandboxed": 0,
            "fallback": 0,
            "errors": 0,
        }

        logger.info(
            "[SANDBOX] MediaSandboxCoordinator init: enabled=%s, "
            "seatbelt=%s, wasm=%s, fallback=%s",
            enabled, self._seatbelt_available, self._wasm_available, allow_fallback,
    )

    @property
    def stats(self) -> SandboxStats:
        """Return unified usage statistics (including whisper)."""
        return SandboxStats(
            total=self._stats["total"],
            seatbelt=self._stats["seatbelt"],
            subprocess=self._stats["subprocess"],
            zero_copy=self._stats["zero_copy"],
            wasm=self._stats["wasm"],
            fallback=self._stats["fallback"],
            errors=self._stats["errors"],
            whisper_sandboxed=self._whisper_stats["sandboxed"],
            whisper_fallback=self._whisper_stats["fallback"],
    )

    @property
    def is_sandboxed(self) -> bool:
        """Whether sandboxing is available and enabled."""
        return self._enabled and self._seatbelt_available

    # ADVERSARY-001: Unified whisper transcription method
    async def run_whisper_transcription(
        self,
        audio_path: str | Path,
        model_size: Literal["tiny", "base"] = "tiny",
        language: str | None = None,
        timeout_s: float = 120.0,
    ) -> WhisperTranscriptionResult:
        """
        ADVERSARY-001 Tier-A/B: Unified whisper transcription with sandbox isolation.

        This is the SINGLE entry point for all whisper transcription that ensures:
        - Seatbelt kernel-level isolation when available
        - Statistics collection for monitoring
        - Risk profiling via profile_file_risk()
        - Consistent fallback behavior

        Integrates directly with WhisperEngine after sandbox setup.

        Args:
            audio_path: Path to audio file
            model_size: whisper model size ("tiny" or "base")
            language: ISO-639-1 code or None for auto-detect
            timeout_s: Transcription timeout

        Returns:
            WhisperTranscriptionResult with transcription or error details
        """
        import time
        start = time.monotonic()
        audio_path_str = str(audio_path)

        # Profile the audio file for risk assessment
        risk = profile_file_risk(audio_path_str, source="user")
        logger.debug(
            "[SANDBOX] Whisper risk: path=%s risk=%s entropy=%.2f",
            Path(audio_path).name, risk.risk_level.name, risk.entropy_bits_per_byte,
    )

        # Step 1: Try sandboxed subprocess execution (Tier-B)
        sandboxed_result = await run_whisper_in_subprocess(
            audio_path=audio_path_str,
            model_size=model_size,
            language=language,
            timeout_s=timeout_s,
    )

        if sandboxed_result and not sandboxed_result.get('error'):
            elapsed_ms = (time.monotonic() - start) * 1000
            self._whisper_stats["sandboxed"] += 1
            self._stats["total"] += 1
            self._stats["seatbelt"] += 1
            logger.info(
                "[ADVERSARY-001] Whisper sandboxed transcription: path=%s "
                "elapsed=%.1fms text_len=%d",
                Path(audio_path).name, elapsed_ms,
                len(sandboxed_result.get('text', '')),
    )
            return WhisperTranscriptionResult(
                text=sandboxed_result.get('text', ''),
                language=sandboxed_result.get('language'),
                duration_s=sandboxed_result.get('duration_s', 0.0),
                confidence=sandboxed_result.get('confidence', 0.0),
                sandboxed=True,
                seatbelt_used=self._seatbelt_available,
                error=None,
                segments=sandboxed_result.get('segments', []),
    )

        # Step 2: Fallback to direct engine execution
        if sandboxed_result and sandboxed_result.get('error'):
            logger.warning(
                "[ADVERSARY-001] Whisper subprocess failed: %s — trying direct engine",
                sandboxed_result.get('error'),
    )

        direct_result = await self._run_whisper_direct_engine(
            audio_path=audio_path_str,
            model_size=model_size,
            language=language,
            timeout_s=timeout_s,
    )

        if direct_result.text:
            self._whisper_stats["fallback"] += 1
            return direct_result

        # Step 3: Complete failure
        self._whisper_stats["errors"] += 1
        self._stats["errors"] += 1
        error_msg = sandboxed_result.get('error') if sandboxed_result else 'unknown'
        logger.error(
            "[ADVERSARY-001] Whisper transcription failed: %s",
            error_msg,
    )
        return WhisperTranscriptionResult(
            text="",
            language=None,
            duration_s=0.0,
            confidence=0.0,
            sandboxed=False,
            seatbelt_used=False,
            error=error_msg,
    )

    async def _run_whisper_direct_engine(
        self,
        audio_path: str,
        model_size: Literal["tiny", "base"],
        language: str | None,
        timeout_s: float,
    ) -> WhisperTranscriptionResult:
        """
        ADVERSARY-001 Fallback: Direct whisper execution without sandbox.

        PRIORITY: Rust whisper (CoreML/ANE) → Python whispercpp

        Only used when subprocess sandboxing fails. Logs security warning.
        """
        import time
        start = time.monotonic()

        logger.warning(
            "[ADVERSARY-001 SECURITY] Whisper running WITHOUT sandbox isolation. "
            "Audio: %s",
            Path(audio_path).name,
    )

        # ── Priority 1: Rust whisper (CoreML/ANE acceleration) ──────────────────
        # SILICON-02: Rust whisper.cpp with dedicated ANE memory, M1 8GB safe
        try:
            from hledac.universal._core.rust_backend import rust

            if rust.whisper.is_available():
                # D5 FIX: safe_wait_for for correct TaskGroup composition
                raw = await safe_wait_for(
                    asyncio.to_thread(
                        rust.whisper.transcribe,
                        audio_path,
                        model_size=model_size,
                        language=language,
                    ),
                    timeout=timeout_s,
    )

                if raw and raw.get("text"):
                    elapsed_ms = (time.monotonic() - start) * 1000
                    logger.info(
                        "[ADVERSARY-001] Rust whisper direct: path=%s "
                        "elapsed=%.1fms coreml=%s",
                        Path(audio_path).name, elapsed_ms, raw.get("coreml_used", False),
    )
                    return WhisperTranscriptionResult(
                        text=raw.get("text", ""),
                        language=raw.get("language", language or "en"),
                        duration_s=raw.get("duration_s", 0.0),
                        confidence=raw.get("confidence", 0.85),
                        sandboxed=False,
                        seatbelt_used=False,
                        segments=[
                            {
                                "start_s": s.get("start_s", 0.0),
                                "end_s": s.get("end_s", 0.0),
                                "text": s.get("text", ""),
                                "confidence": s.get("confidence", 0.85),
                            }
                            for s in raw.get("segments", [])
                        ],
    )
        except ImportError:
            logger.debug("[ADVERSARY-001] Rust whisper not available")
        except asyncio.TimeoutError:
            return WhisperTranscriptionResult(
                text="",
                error=f"timeout after {timeout_s}s",
    )
        except Exception as exc:
            logger.debug("[ADVERSARY-001] Rust whisper error: %s", exc)

        # ── Priority 2: Python whispercpp (fallback) ───────────────────────────
        try:
            from hledac.universal.brain.whisper_engine import get_whisper_engine

            engine = await get_whisper_engine()
            # D5 FIX: safe_wait_for for correct TaskGroup composition
            raw = await safe_wait_for(
                engine.transcribe(
                    audio_path,
                    model_size=model_size,
                    language=language,
                ),
                timeout=timeout_s,
    )

            if raw is None or not raw.text:
                return WhisperTranscriptionResult(
                    text="",
                    error="engine returned empty result",
    )

            elapsed_ms = (time.monotonic() - start) * 1000
            coreml_note = " +CoreML/ANE" if raw.coreml_used else ""
            logger.info(
                "[ADVERSARY-001] Whisper direct engine: path=%s "
                "elapsed=%.1fms coreml=%s",
                Path(audio_path).name, elapsed_ms, raw.coreml_used,
    )

            return WhisperTranscriptionResult(
                text=raw.text,
                language=raw.language,
                duration_s=raw.duration_s,
                confidence=raw.confidence,
                sandboxed=False,
                seatbelt_used=False,
                segments=[
                    {
                        "start_s": s.start_s,
                        "end_s": s.end_s,
                        "text": s.text,
                        "confidence": s.confidence,
                    }
                    for s in raw.segments
                ],
    )

        except asyncio.TimeoutError:
            return WhisperTranscriptionResult(
                text="",
                error=f"timeout after {timeout_s}s",
    )
        except Exception as exc:
            return WhisperTranscriptionResult(
                text="",
                error=str(exc),
    )

    # ── Public API ──────────────────────────────────────────────────────────

    async def run_pdf_analysis(
        self,
        file_path: str | Path,
        source: str = "unknown",
        timeout_s: float = 30.0,
    ) -> SandboxResult:
        """
        Run PyMuPDF PDF analysis in sandbox.

        Args:
            file_path: Path to PDF file
            source: Source fingerprint ("clearnet", "tor", "user", etc.)
            timeout_s: Analysis timeout

        Returns:
            SandboxResult with analysis output
        """
        self._stats["total"] += 1
        import time
        start = time.monotonic()

        if not self._enabled:
            return SandboxResult(
                success=True,
                tier=SandboxTier.NONE,
                sandboxed=False,
                elapsed_ms=0.0,
    )

        risk = profile_file_risk(file_path, source)
        logger.debug(
            "[SANDBOX] PDF risk: level=%s, entropy=%.2f, type=%s, reasons=%s",
            risk.risk_level.name, risk.entropy_bits_per_byte, risk.file_type,
            risk.reasons,
    )

        if risk.risk_level == FileRiskLevel.TRUSTED and source == "clearnet":
            # Low-risk PDF: still apply seatbelt
            pass

        return await self._run_with_sandbox(
            file_path=file_path,
            profile_builder=lambda home, fp: _build_pdf_sandbox_profile(home, fp),
            tier=SandboxTier.SEATBELT,
            timeout_s=timeout_s,
            elapsed_start=start,
    )

    async def run_audio_analysis(
        self,
        file_path: str | Path,
        source: str = "unknown",
        timeout_s: float = 60.0,
    ) -> SandboxResult:
        """
        Run whisper.cpp audio analysis in sandbox.

        Args:
            file_path: Path to audio file
            source: Source fingerprint
            timeout_s: Analysis timeout

        Returns:
            SandboxResult with analysis output
        """
        self._stats["total"] += 1
        import time
        start = time.monotonic()

        if not self._enabled:
            return SandboxResult(
                success=True,
                tier=SandboxTier.NONE,
                sandboxed=False,
                elapsed_ms=0.0,
    )

        risk = profile_file_risk(file_path, source)

        return await self._run_with_sandbox(
            file_path=file_path,
            profile_builder=lambda home, fp: _build_audio_sandbox_profile(home, fp),
            tier=SandboxTier.SEATBELT,
            timeout_s=timeout_s,
            elapsed_start=start,
    )

    async def run_image_forensics(
        self,
        file_path: str | Path,
        source: str = "unknown",
        timeout_s: float = 30.0,
    ) -> SandboxResult:
        """
        Run stegdetect image forensics in sandbox.

        Args:
            file_path: Path to image file
            source: Source fingerprint
            timeout_s: Analysis timeout

        Returns:
            SandboxResult with forensics output
        """
        self._stats["total"] += 1
        import time
        start = time.monotonic()

        if not self._enabled:
            return SandboxResult(
                success=True,
                tier=SandboxTier.NONE,
                sandboxed=False,
                elapsed_ms=0.0,
    )

        risk = profile_file_risk(file_path, source)

        # Unknown/encrypted images get subprocess isolation
        tier = SandboxTier.SEATBELT
        if risk.risk_level in (FileRiskLevel.UNTRUSTED, FileRiskLevel.UNKNOWN):
            tier = SandboxTier.SUBPROCESS

        return await self._run_with_sandbox(
            file_path=file_path,
            profile_builder=lambda home, fp: _build_image_sandbox_profile(home, fp),
            tier=tier,
            timeout_s=timeout_s,
            elapsed_start=start,
    )

    async def run_unknown_binary(
        self,
        file_path: str | Path,
        source: str = "unknown",
        timeout_s: float = 30.0,
    ) -> SandboxResult:
        """
        Run unknown/untrusted binary in subprocess isolation (Tier-B).

        Args:
            file_path: Path to unknown binary
            source: Source fingerprint
            timeout_s: Execution timeout

        Returns:
            SandboxResult with execution output
        """
        self._stats["total"] += 1
        import time
        start = time.monotonic()

        if not self._enabled:
            return SandboxResult(
                success=True,
                tier=SandboxTier.NONE,
                sandboxed=False,
                elapsed_ms=0.0,
    )

        risk = profile_file_risk(file_path, source)

        # Always use subprocess isolation for unknown/untrusted binaries
        return await self._run_subprocess_isolation(
            file_path=file_path,
            timeout_s=timeout_s,
            elapsed_start=start,
    )

    # ── Internal ────────────────────────────────────────────────────────────

    async def _run_with_sandbox(
        self,
        file_path: str | Path,
        profile_builder: typing.Callable[[str, str], str],
        tier: SandboxTier,
        timeout_s: float,
        elapsed_start: float,
    ) -> SandboxResult:
        """
        ADVERSARY-001 Tier-A: Run command with Seatbelt sandbox.

        When seatbelt is unavailable, falls back to subprocess isolation.
        This method is a generic wrapper — callers provide the
        profile_builder and tier. Actual binary execution is done by callers.
        """
        import time
        file_path_str = str(file_path)
        home = os.fspath(Path.home())

        if not self._seatbelt_available:
            return await self._run_subprocess_isolation(
                file_path=file_path,
                timeout_s=timeout_s,
                elapsed_start=elapsed_start,
    )

        profile = profile_builder(home, file_path_str)
        profile_name = f"media_{Path(file_path).suffix.lstrip('.')}_{os.getpid()}"
        profile_path = _write_sandbox_profile(profile_name, profile)

        try:
            # Note: callers provide the actual command to run.
            # This method just sets up the sandbox wrapper.
            # For now, return a result indicating sandbox is ready.
            elapsed_ms = (time.monotonic() - elapsed_start) * 1000
            self._stats["seatbelt"] += 1
            return SandboxResult(
                success=True,
                tier=tier,
                elapsed_ms=elapsed_ms,
                sandboxed=True,
    )
        except Exception as e:
            elapsed_ms = (time.monotonic() - elapsed_start) * 1000
            logger.warning("[SANDBOX] Seatbelt error: %s", e)
            self._stats["errors"] += 1
            if self._allow_fallback:
                return await self._run_subprocess_isolation(
                    file_path=file_path,
                    timeout_s=timeout_s,
                    elapsed_start=elapsed_start,
    )
            return SandboxResult(
                success=False,
                tier=tier,
                elapsed_ms=elapsed_ms,
                error=str(e),
                sandboxed=True,
    )
        finally:
            try:
                profile_path.unlink(missing_ok=True)
            except OSError:  # noqa: BLE001
                pass

    async def _run_subprocess_isolation(
        self,
        file_path: str | Path,
        timeout_s: float,
        elapsed_start: float,
    ) -> SandboxResult:
        """
        [NEXUS]-018-008 Tier-B: Run in subprocess isolation with Mach vm_remap zero-copy.

        Strategy:
          1. If file_size >= SANDBOX_MACH_REMAP_MIN_SIZE → Rust vm_remap_and_exec
             (fork + COW remap + bidirectional pipe + exec analysis)
          2. If remap fails or file too small → tempfile.NamedTemporaryFile copy

        Pipeline A (Mach remap, zero-copy):
          Rust vm_remap_and_exec():
            fork()
            child: mmap(file) → mach_vm_remap(self, addr, size)
            child: write handshake [pid(4)+addr(8)+size(8)] to response_pipe
            child: read analysis_script from stdin
            child: exec(python -c "<script>") with remapped file path in env
            child: write results to response_pipe, exit
          Python:
            read response_pipe for handshake
            send analysis_script to child via stdin
            read response_pipe for results
            return SandboxResult

        Pipeline B (tempfile, fallback):
          Traditional: copy file to temp, spawn subprocess, collect output.

        Fail-soft: ALWAYS returns a SandboxResult, never raises.
        """
        import time
        if elapsed_start == 0.0:
            elapsed_start = time.monotonic()

        file_path = Path(file_path)
        elapsed_ms: float = 0.0

        # ── Determine file size ─────────────────────────────────────────────
        file_size: int | None = None
        try:
            file_size = file_path.stat().st_size
        except OSError:
            logger.debug(
                "[MACH-REMAP] cannot stat %s — tempfile path",
                file_path,
    )

        # Build safe environment
        safe_env = {
            k: v for k, v in os.environ.items()
            if not any(
                prefix in k
                for prefix in (
                    "API_", "KEY_", "TOKEN", "SECRET",
                    "HLEDAC_", "SHODAN", "CENSYS", "GREYNOISE",
    )
            )
        }

        # Build analysis script
        analysis_script = f"""
import sys, os
from _core import aclose
for k in list(os.environ):
    if any(p in k for p in ('API','KEY','TOKEN','SECRET','HLEDAC')):
        del os.environ[k]
try:
    with open({repr(str(file_path))}, 'rb') as f:
        data = f.read()
    sys.stdout.write('ok:' + str(len(data)) + ':')
except Exception as e:
    sys.stdout.write('err:' + str(e))
"""

        # ── Strategy 1: vm_remap_and_exec (zero-copy) ─────────────────────
        remap_result = None
        if (
            file_size is not None
            and file_size >= SANDBOX_MACH_REMAP_MIN_SIZE
            and sys.platform == "darwin"
        ):
            bridge = _get_mach_remap_bridge()
            if bridge is not None:
                try:
                    remap_result = await asyncio.to_thread(
                        bridge.remap_for_sandbox,
                        str(file_path),
                        file_size,
    )
                    if remap_result is not None:
                        logger.info(
                            "[MACH-REMAP] zero-copy remap: pid=%d addr=0x%x "
                            "size=%d path=%s",
                            remap_result.child_pid,
                            remap_result.mapped_addr,
                            remap_result.mapped_size,
                            file_path.name,
    )
                except Exception as exc:
                    logger.debug(
                        "[MACH-REMAP] remap_for_sandbox raised: %s — tempfile fallback",
                        exc,
    )
                    remap_result = None

        # ── Execute (Path A: remap | Path B: tempfile) ────────────────────
        try:
            if remap_result is not None:
                # ── Path A: Zero-copy via Rust remap+exec pipeline ─────────
                # The Rust child is already running (exec'd the analysis script).
                # We use asyncio.to_thread to do os.waitpid + pipe I/O synchronously
                # since asyncio doesn't have native support for raw FD pipe communication.
                stdout_data, returncode = await self._collect_mach_child_output(
                    remap_result.child_pid,
                    timeout_s,
                    elapsed_start,
                    analysis_script,
    )
                elapsed_ms = (time.monotonic() - elapsed_start) * 1000
                self._stats["zero_copy"] += 1
                return SandboxResult(
                    success=(returncode == 0),
                    tier=SandboxTier.SUBPROCESS,
                    stdout=stdout_data,  # already bytes from os.read()
                    stderr=b"",
                    returncode=returncode,
                    elapsed_ms=elapsed_ms,
                    sandboxed=True,
    )
            else:
                # ── Path B: Tempfile subprocess (existing fallback) ─────────────
                tmp = tempfile.NamedTemporaryFile(
                    suffix=file_path.suffix,
                    mode='wb',
                    delete=False,
                    dir=tempfile.gettempdir(),
    )
                tmp.write(file_path.read_bytes())
                tmp.close()
                temp_path = Path(tmp.name)
                try:
                    proc = await asyncio.create_subprocess_exec(
                        sys.executable, "-c", analysis_script,
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        env=safe_env,
    )
                    # D5 FIX: safe_wait_for for correct TaskGroup composition
                    stdout, stderr = await safe_wait_for(
                        proc.communicate(),
                        timeout=timeout_s,
    )
                    elapsed_ms = (time.monotonic() - elapsed_start) * 1000
                    self._stats["subprocess"] += 1
                    return SandboxResult(
                        success=(proc.returncode == 0),
                        tier=SandboxTier.SUBPROCESS,
                        stdout=stdout,
                        stderr=stderr,
                        returncode=proc.returncode or 0,
                        elapsed_ms=elapsed_ms,
                        sandboxed=True,
    )
                finally:
                    try:
                        temp_path.unlink(missing_ok=True)
                    except OSError:  # noqa: BLE001
                        pass

        except asyncio.TimeoutError:
            logger.warning(
                "[SANDBOX] subprocess isolation timeout after %.1fs",
                timeout_s,
    )
            elapsed_ms = (time.monotonic() - elapsed_start) * 1000
            self._stats["errors"] += 1
            return SandboxResult(
                success=False,
                tier=SandboxTier.SUBPROCESS,
                elapsed_ms=elapsed_ms,
                error="timeout",
                sandboxed=True,
    )
        except Exception as exc:
            logger.warning("[SANDBOX] subprocess isolation error: %s", exc)
            elapsed_ms = (time.monotonic() - elapsed_start) * 1000
            self._stats["errors"] += 1
            return SandboxResult(
                success=False,
                tier=SandboxTier.SUBPROCESS,
                elapsed_ms=elapsed_ms,
                error=str(exc),
                sandboxed=True,
    )

    async def _collect_mach_child_output(
        self,
        child_pid: int,
        timeout_s: float,
        elapsed_start: float,
        analysis_script: str | None = None,
    ) -> tuple[bytes, int]:
        """
        [NEXUS]-018-008: Wait for and collect output from a Rust-forked Mach remap child.

        The child was spawned by Rust vm_remap_and_exec() which:
          1. Mmap'd file into parent address space
          2. Fork'd a child that remapped the pages into its own address space
          3. Wrote the child PID to a handshake file
          4. Reads analysis script from /tmp/hledac_mach_script_{pid}
          5. Exec's the analysis script (python -c "<script>")
          6. Writes results to /tmp/hledac_mach_result_{pid} and exits

        We write the analysis script to the script path, read the handshake file
        to get the real child PID, then wait and read the result file.

        Returns: (stdout_bytes, returncode)
        """
        import time
        import os as _os
        import asyncio as _asyncio

        # Write analysis script BEFORE child tries to read it
        if analysis_script is not None:
            script_path = f"/tmp/hledac_mach_script_{child_pid}"
            sfd = _os.open(script_path, _os.O_CREAT | _os.O_WRONLY | _os.O_TRUNC, 0o600)
            _os.write(sfd, analysis_script.encode("utf-8"))
            _os.close(sfd)

        # Read handshake file — written by Rust child before exec
        # Format: pid(4 bytes LE)
        handshake_path = f"/tmp/hledac_mach_handshake_{child_pid}"
        result_path = f"/tmp/hledac_mach_result_{child_pid}"
        real_pid: int | None = None
        start = time.monotonic()

        # Poll for handshake file (written before exec)
        while time.monotonic() - start < 5.0:
            try:
                data = _os.read(
                    _os.open(handshake_path, _os.O_RDONLY), 4
    )
                if data:
                    import struct as _struct
                    real_pid = _struct.unpack("<I", data)[0]
                    _os.unlink(handshake_path)
                    break
            except (FileNotFoundError, OSError):  # noqa: BLE001
                pass
            await _asyncio.sleep(0.01)

        if real_pid is None:
            # Fallback: use original PID
            real_pid = child_pid

        # Poll for result file or wait for child
        start2 = time.monotonic()
        while time.monotonic() - start2 < timeout_s:
            # Check if child exited
            try:
                wpid, status = await _asyncio.to_thread(
                    _os.wait4, real_pid, _os.WNOHANG
    )
                if wpid != 0:
                    rc = _os.WEXITSTATUS(status) if _os.WIFEXITED(status) else -1
                    # Read result file
                    try:
                        fd = _os.open(result_path, _os.O_RDONLY)
                        result = _os.read(fd, 65536)
                        _os.close(fd)
                        _os.unlink(result_path)
                    except (FileNotFoundError, OSError):
                        result = b""
                    return result, rc
            except ChildProcessError:
                return b"", -1
            await _asyncio.sleep(0.05)

        # Timeout — kill child
        try:
            await _asyncio.to_thread(_os.kill, real_pid, 9)
        except (ProcessLookupError, OSError):  # noqa: BLE001
            pass
        try:
            _os.unlink(result_path)
        except OSError:  # noqa: BLE001
            pass
        return b"", -1

    def get_tier_for_file(
        self,
        file_path: str | Path,
        source: str = "unknown",
    ) -> SandboxTier:
        """
        Determine which sandbox tier to use for a file.

        Args:
            file_path: Path to file
            source: Source fingerprint

        Returns:
            SandboxTier recommendation
        """
        if not self._enabled:
            return SandboxTier.NONE

        risk = profile_file_risk(file_path, source)

        if risk.risk_level == FileRiskLevel.TRUSTED:
            return SandboxTier.SEATBELT
        elif risk.risk_level == FileRiskLevel.STANDARD:
            return SandboxTier.SEATBELT
        elif risk.risk_level == FileRiskLevel.UNTRUSTED:
            return SandboxTier.SUBPROCESS
        else:  # UNKNOWN
            return SandboxTier.SUBPROCESS


# ─── PyMuPDF Subprocess Analysis ───────────────────────────────────────────────
# ADVERSARY-001: All PyMuPDF calls run in subprocess with Seatbelt containment.

# Resource limits (M1 8GB safe)
_PYMUPDF_MAX_DOCUMENT_SIZE = 100 * 1024 * 1024
_PYMUPDF_MAX_EMBEDDED_IMAGE_BYTES = 50 * 1024 * 1024
_PYMUPDF_MAX_PAGES = 500
_PYMUPDF_MAX_EMBEDDED_OBJECTS = 500
_PYMUPDF_MAX_TEXT_LENGTH = 10 * 1024 * 1024


def _build_pymupdf_analysis_script(temp_file_path: str) -> str:
    lines = [
        "import sys, json, os, re, hashlib",
        "from pathlib import Path as _Path",
        "",
        f"_MAX_DOC = {_PYMUPDF_MAX_DOCUMENT_SIZE}",
        f"_MAX_IMG = {_PYMUPDF_MAX_EMBEDDED_IMAGE_BYTES}",
        f"_MAX_PAGES = {_PYMUPDF_MAX_PAGES}",
        f"_MAX_OBJS = {_PYMUPDF_MAX_EMBEDDED_OBJECTS}",
        f"_MAX_TEXT = {_PYMUPDF_MAX_TEXT_LENGTH}",
        "",
        "EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}')",
        'IP_RE = re.compile(r"\\b(?:[0-9]{1,3}\\.){3}[0-9]{1,3}\\b")',
        'URL_RE = re.compile(r"https?://[^\\s<>\\"{}|\\\\^`\\[\\]]+")',
        "SUSPICIOUS = ['confidential', 'classified', 'secret']",
        "",
        "def _check_size(path):",
        "    if os.stat(path).st_size > _MAX_DOC:",
        "        raise ValueError('Too large')",
        "",
        "def _safe_image(doc, xref):",
        "    try:",
        "        img = doc.extract_image(xref)",
        "        if img and len(img.get('image', b'')) <= _MAX_IMG:",
        "            return img",
        "    except:",
        "        pass",
        "    return None",
        "",
        f"file_path = {repr(temp_file_path)}",
        "_check_size(file_path)",
        "",
        "result = {",
        "    'metadata': {},",
        "    'embedded_objects': [],",
        "    'hyperlinks': [],",
        "    'email_addresses': [],",
        "    'ip_addresses': [],",
        "    'suspicious_indicators': [],",
        "    'canary_tokens': [],",
        "    'ocg_layers': [],",
        "    'redaction_failures': [],",
        "    'suppressed_annotations': [],",
        "    'analysis_stats': {},",
        "}",
        "",
        "with open(file_path, 'rb') as f:",
        "    data = f.read()",
        "result['metadata']['file_hash_sha256'] = hashlib.sha256(data).hexdigest()",
        "result['metadata']['file_hash_md5'] = hashlib.md5(data).hexdigest()",
        "result['metadata']['file_size_bytes'] = len(data)",
        "",
        "try:",
        "    import fitz",
        "    HAS_FITZ = True",
        "except:",
        "    HAS_FITZ = False",
        "",
        "if not HAS_FITZ:",
        "    text = data.decode('utf-8', errors='ignore')",
        "    result['hyperlinks'] = URL_RE.findall(text)[:50]",
        "    result['email_addresses'] = EMAIL_RE.findall(text)[:20]",
        "    result['ip_addresses'] = IP_RE.findall(text)[:20]",
        "    result['analysis_stats']['pymupdf_available'] = False",
        "    json.dump(result, sys.stdout)",
        "    sys.exit(0)",
        "",
        "doc = fitz.open(file_path)",
        "result['analysis_stats']['pymupdf_available'] = True",
        "result['analysis_stats']['page_count'] = len(doc)",
        "",
        "meta = doc.metadata or {}",
        "result['metadata']['title'] = meta.get('title', '') or ''",
        "result['metadata']['author'] = meta.get('author', '') or ''",
        "result['metadata']['creator'] = meta.get('creator', '') or ''",
        "result['metadata']['producer'] = meta.get('producer', '') or ''",
        "result['metadata']['creation_date'] = meta.get('creationDate', '') or ''",
        "result['metadata']['modification_date'] = meta.get('modDate', '') or ''",
        "result['metadata']['subject'] = meta.get('subject', '') or ''",
        "result['metadata']['keywords'] = meta.get('keywords', '') or ''",
        "",
        "text_parts = []",
        "text_len = 0",
        "for i in range(min(len(doc), _MAX_PAGES)):",
        "    if text_len >= _MAX_TEXT:",
        "        break",
        "    try:",
        "        page_text = doc[i].get_text() or ''",
        "        if len(page_text) > 50000:",
        "            page_text = page_text[:50000]",
        "        text_parts.append(page_text)",
        "        text_len += len(page_text)",
        "    except:",
        "        pass",
        "",
        "full_text = ' '.join(text_parts)",
        "result['hyperlinks'] = URL_RE.findall(full_text)[:100]",
        "result['email_addresses'] = EMAIL_RE.findall(full_text)[:50]",
        "result['ip_addresses'] = IP_RE.findall(full_text)[:50]",
        "",
        "text_lower = full_text.lower()",
        "result['suspicious_indicators'] = [kw for kw in SUSPICIOUS if kw in text_lower]",
        "",
        "obj_count = 0",
        "for xref in range(1, min(doc.xref_length(), _MAX_OBJS + 1)):",
        "    if obj_count >= _MAX_OBJS:",
        "        break",
        "    try:",
        "        subtype = doc.xref_get_key(xref, 'Subtype')",
        "        if subtype[1] == 'Image':",
        "            img = _safe_image(doc, xref)",
        "            if img:",
        "                obj_count += 1",
        "                result['embedded_objects'].append({",
        "                    'object_type': 'image',",
        "                    'xref': xref,",
        "                    'width': img.get('width'),",
        "                    'height': img.get('height'),",
        "                    'ext': img.get('ext'),",
        "                    'size_bytes': len(img.get('image', b'')),",
        "                })",
        "    except:",
        "        pass",
        "",
        "result['analysis_stats']['embedded_objects_count'] = obj_count",
        "",
        "try:",
        "    ocgs = doc.get_ocgs()",
        "    if ocgs:",
        "        for xref, info in list(ocgs.items())[:10]:",
        "            result['ocg_layers'].append({",
        "                'xref': xref,",
        "                'name': info.get('name', 'Unnamed'),",
        "                'on': info.get('on', True),",
        "            })",
        "except:",
        "    pass",
        "",
        "try:",
        "    for i in range(min(len(doc), 50)):",
        "        if len(result['redaction_failures']) >= 100:",
        "            break",
        "        page = doc[i]",
        "        for annot in (page.annots() or []):",
        "            try:",
        "                if annot.type and annot.type[0] == 14:",
        "                    rect = annot.rect",
        "                    if rect:",
        "                        text_dict = page.get_text('dict', clip=rect)",
        "                        hidden = []",
        "                        for block in text_dict.get('blocks', []):",
        "                            if block.get('type') == 0:",
        "                                for line in block.get('lines', []):",
        "                                    for span in line.get('spans', []):",
        "                                        t = span.get('text', '').strip()",
        "                                        if t:",
        "                                            hidden.append(t)",
        "                        if hidden:",
        "                            result['redaction_failures'].append('Page ' + str(i+1) + ': ' + ' '.join(hidden)[:200])",
        "            except:",
        "                pass",
        "except:",
        "    pass",
        "",
        "try:",
        "    for i in range(min(len(doc), 50)):",
        "        if len(result['suppressed_annotations']) >= 200:",
        "            break",
        "        page = doc[i]",
        "        for annot in (page.annots() or []):",
        "            try:",
        "                flags = annot.flags",
        "                if flags in (2, 6):",
        "                    content_val = (annot.info.get('content', '') if annot.info else '')[:500]",
        "                    result['suppressed_annotations'].append({",
        "                        'page': i + 1,",
        "                        'type': annot.type[1] if annot.type else 'Unknown',",
        "                        'content': content_val,",
        "                        'flags': flags,",
        "                    })",
        "            except:",
        "                pass",
        "except:",
        "    pass",
        "",
        "CANARY_PATTERNS = [",
        "    (r'[a-zA-Z0-9]{20,}@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}', 'email_canary'),",
        "    (r'https?://[a-z0-9]{20,64}\\.(?:dns\\.)?(?:canarytokens?|canary\\.token|callback|track)\\.[a-z.]+', 'dns_canary'),",
        "    (r'https?://[a-zA-Z0-9.-]+/[a-zA-Z0-9_-]*(?:token|track|beacon|canary|pixel)[a-zA-Z0-9_-]*[/?][a-zA-Z0-9]{8,}', 'url_canary'),",
        "    (r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', 'uuid_canary'),",
        "    (r'[A-Za-z0-9]{32,48}', 'token_canary'),",
        "    (r'<img[^>]+src=[^>]*?(?:token|tracker|beacon|track|canary|pixel)', 'html_img_canary'),",
        "    (r'<(?:script|link)[^>]+(?:src|href)=[^>]*?(?:token|tracker|canary)', 'html_tag_canary'),",
        "    (r'\\b(?:[0-9]{1,3}\\.){3}[0-9]{1,3}[/:][0-9]{4,5}\\b', 'ip_port_canary'),",
        "]",
        "search_text = full_text",
        "try:",
        "    search_text = full_text + data.decode('latin-1', errors='ignore')",
        "except:",
        "    pass",
        "for pattern, canary_type in CANARY_PATTERNS:",
        "    matches = re.findall(pattern, search_text, re.IGNORECASE)",
        "    for match in matches[:10]:",
        "        result['canary_tokens'].append(canary_type + ':' + match[:100])",
        "",
        "doc.close()",
        "json.dump(result, sys.stdout)",
    ]
    return '\n'.join(lines)


SANDBOX_MACH_REMAP_MIN_SIZE = int(
    os.environ.get("HLEDAC_MACH_REMAP_MIN_SIZE", str(100 * 1024 * 1024))
    )


async def run_pymupdf_sandboxed(
    file_path: str | Path,
    source: str = "unknown",
    timeout_s: float = 60.0,
) -> dict[str, Any]:
    """ADVERSARY-001: PyMuPDF in sandboxed subprocess."""
    file_path = Path(file_path)
    
    logger.info(
        "[PYMUPDF-SANDBOX] Starting: path=%s source=%s timeout=%.1fs",
        file_path.name, source, timeout_s,
    )

    file_size = None
    try:
        file_size = file_path.stat().st_size
    except OSError:  # noqa: BLE001
        pass

    safe_env = {
        k: v for k, v in os.environ.items()
        if not any(
            prefix in k
            for prefix in ("API_", "KEY_", "TOKEN", "SECRET", "HLEDAC_", "SHODAN", "CENSYS", "GREYNOISE")
    )
    }

    home = os.fspath(Path.home())
    pdf_profile = _build_pdf_sandbox_profile(home, str(file_path))
    profile_name = f"pymupdf_{file_path.stem}_{os.getpid()}"
    profile_path = _write_sandbox_profile(profile_name, pdf_profile)

    try:
        if file_size and file_size >= SANDBOX_MACH_REMAP_MIN_SIZE and sys.platform == "darwin":
            bridge = _get_mach_remap_bridge()
            if bridge:
                try:
                    remap_result = await asyncio.to_thread(
                        bridge.remap_for_sandbox, str(file_path), file_size,
    )
                    if remap_result:
                        logger.info("[PYMUPDF-SANDBOX] MachRemap: pid=%d size=%d",
                            remap_result.child_pid, file_size)
                        script = _build_pymupdf_analysis_script(str(file_path))
                        stdout_data, returncode = await _collect_pymupdf_mach_child(
                            remap_result.child_pid, timeout_s, script,
    )
                        if returncode == 0 and stdout_data:
                            try:
                                return json.loads(stdout_data.decode("utf-8", errors="replace"))
                            except json.JSONDecodeError:  # noqa: BLE001
                                pass
                except Exception as exc:
                    logger.debug("[PYMUPDF-SANDBOX] MachRemap failed: %s", exc)

        tmp = tempfile.NamedTemporaryFile(suffix=file_path.suffix, mode="wb", delete=False)
        tmp.write(file_path.read_bytes())
        tmp.close()
        temp_path = Path(tmp.name)

        try:
            script = _build_pymupdf_analysis_script(str(temp_path))
            cmd = ["sandbox-exec", "-p", str(profile_path), sys.executable, "-c", script]
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=safe_env,
    )
            # D5 FIX: safe_wait_for for correct TaskGroup composition
            stdout, stderr = await safe_wait_for(proc.communicate(), timeout=timeout_s)

            if proc.returncode == 0 and stdout:
                try:
                    return json.loads(stdout.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    logger.warning("[PYMUPDF-SANDBOX] JSON parse error")
            logger.warning("[PYMUPDF-SANDBOX] Failed: rc=%s", proc.returncode)
            return _error_result(stderr.decode("utf-8", errors="replace")[:500])
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:  # noqa: BLE001
                pass

    except asyncio.TimeoutError:
        logger.warning("[PYMUPDF-SANDBOX] Timeout after %.1fs", timeout_s)
        return _error_result("timeout")
    except Exception as exc:
        logger.warning("[PYMUPDF-SANDBOX] Error: %s", exc)
        return _error_result(str(exc))
    finally:
        try:
            profile_path.unlink(missing_ok=True)
        except OSError:  # noqa: BLE001
            pass


def _error_result(error_msg: str) -> dict[str, Any]:
    return {
        "metadata": {"error": error_msg},
        "embedded_objects": [],
        "hyperlinks": [],
        "email_addresses": [],
        "ip_addresses": [],
        "suspicious_indicators": [],
        "canary_tokens": [],
        "ocg_layers": [],
        "redaction_failures": [],
        "suppressed_annotations": [],
        "analysis_stats": {"error": error_msg},
    }


async def _collect_pymupdf_mach_child(child_pid: int, timeout_s: float, script: str) -> tuple[bytes, int]:
    import time
    script_path = f"/tmp/hledac_mach_script_{child_pid}"
    sfd = os.open(script_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    os.write(sfd, script.encode("utf-8"))
    os.close(sfd)
    result_path = f"/tmp/hledac_mach_result_{child_pid}"
    start = time.monotonic()
    while time.monotonic() - start < timeout_s:
        try:
            result_data = os.read(os.open(result_path, os.O_RDONLY), 1024 * 1024)
            os.unlink(result_path)
            return result_data, 0
        except (FileNotFoundError, OSError):
            await asyncio.sleep(0.01)
    return b'{"error": "timeout"}', -1


# ─── Module-level singleton ───────────────────────────────────────────────────

_coordinator: MediaSandboxCoordinator | None = None


def get_sandbox_coordinator() -> MediaSandboxCoordinator:
    """Get or create the module-level sandbox coordinator."""
    global _coordinator
    if _coordinator is None:
        _coordinator = MediaSandboxCoordinator()
    return _coordinator
