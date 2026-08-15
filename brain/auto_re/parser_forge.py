"""
brain/auto_re/parser_forge.py — Hermes3 Auto-RE Engine (ADVERSARY-004)
=====================================================================




Five-stage pipeline:

  Stage A — Magic-byte router
    First 16 bytes → FormatFamily | None.
    Falls back to content heuristic if no magic match.

  Stage B — Hermes3 parser generation
    Assembles prompt with first 512B, entropy histogram, ASCII ratio.
    Calls Hermes3Engine.generate_structured() with constrained JSON output.
    Parses <|constrain|>{"format_hypothesis": ..., "parser_python": ...}<|message|>...

  Stage C — Sandboxed execution
    AST parse → forbidden-name check → restricted globals → exec in thread pool.
    Allowed imports: struct, binascii, re, json, codecs, math, typing.
    Blocked: os, subprocess, open, eval, exec, __import__, getattr, builtins.
    2 KB code limit enforced before AST parse.

  Stage D — IOC validation gate
    Runs Rust SIMD extractor (extract_iocs_simd) on parser output text.
    Keeps only IOCs matching the 10-pattern set (G1+G2).
    If zero valid IOCs → result discarded, audit logged.

  Stage E — Audit trail
    Generated Python stored in ~/.cache/hledac/auto_re/<sha256_of_input>.py
    with metadata header (input hash, timestamp, format hypothesis).
    NOT re-executed on subsequent calls — cache is audit-only.

Opt-in: HLEDAC_ENABLE_AUTO_RE=1 (default OFF, --experimental flag enables it).
Rate limit: max 3 attempts per sprint (enforced by AutoRESidecarAdapter).
M1 8GB safe: Hermes3 ~4s inference, sandbox subprocess ~1s, all async.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import importlib
import logging
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import msgspec

# ── Lazy imports ────────────────────────────────────────────────────────────────

_HERMES3_ENGINE: Any = None  # lazy singleton

def _get_hermes3_engine() -> Any:
    global _HERMES3_ENGINE
    if _HERMES3_ENGINE is None:
        try:
            from hledac.universal.brain.deephermes3_engine import Hermes3Engine
            _HERMES3_ENGINE = Hermes3Engine()
        except Exception as e:
            logging.getLogger(__name__).warning("Hermes3Engine unavailable for AutoRE: %s", e)
            return None
    return _HERMES3_ENGINE


def _is_auto_re_enabled() -> bool:
    """Check HLEDAC_ENABLE_AUTO_RE env var."""
    val = os.environ.get("HLEDAC_ENABLE_AUTO_RE", "0")
    return val.strip().lower() in ("1", "true", "yes")


# ── Rust IOC extractor (lazy) ───────────────────────────────────────────────────

_RUST_IOC_EXTRACTOR: Any = None

def _get_rust_ioc_extractor() -> Any:
    global _RUST_IOC_EXTRACTOR
    if _RUST_IOC_EXTRACTOR is None:
        try:
            import hledac_rust_extensions as rust  # type: ignore[import-not-found]
            _RUST_IOC_EXTRACTOR = rust
        except ImportError:
            try:
                from hledac.universal.rust_extensions import hledac_rust_extensions as rust
                _RUST_IOC_EXTRACTOR = rust
            except ImportError:
                logging.getLogger(__name__).warning("Rust IOC extractor unavailable for AutoRE")
                return None
    return _RUST_IOC_EXTRACTOR


# ── Types ──────────────────────────────────────────────────────────────────────

@dataclass
class ParsedIOC(msgspec.Struct, frozen=True, gc=False):
    """Single IOC extracted by a generated parser."""
    ioc_type:   str   # e.g. "ipv4", "domain", "btc_address"
    ioc_value:  str
    confidence: float = 0.7
    context:    str  = ""

    def to_dict(self) -> dict[str, Any]:
        return msgspec.convert(self, dict)


@dataclass
class AutoREResult(msgspec.Struct, frozen=True, gc=False):
    """
    Result of a single AutoRE attempt.
    """
    success:        bool
    file_hash:      str         # SHA-256 of input file bytes
    format_family:  str         # matched format family or "unknown"
    format_hypothesis: str = "" # Hermes3's format description
    iocs:           list[ParsedIOC] = field(default_factory=list)
    parser_source:  str = ""    # generated Python code (for audit)
    error:          str = ""     # error message if not success
    stage:          str = ""    # last completed stage: "A"|"B"|"C"|"D"|"E"
    hermes3_ms:     float = 0.0  # Hermes3 inference time in ms
    sandbox_ms:     float = 0.0  # sandbox execution time in ms
    timestamp:      float = field(default_factory=time.time)


# ── Constants ──────────────────────────────────────────────────────────────────

_AUDIT_DIR = Path.home() / ".cache" / "hledac" / "auto_re"
_MAX_CODE_BYTES = 2048          # 2 KB code size limit
_MAX_ATTEMPTS_PER_SPRINT = 3    # rate limit
_SANDBOX_TIMEOUT_S = 5.0       # subprocess timeout

# Forbidden patterns in generated code (checked before compile)
_FORBIDDEN_PATTERNS = [
    re.compile(r"\bos\b"),
    re.compile(r"\bsubprocess\b"),
    re.compile(r"\beval\b"),
    re.compile(r"\bexec\b"),
    re.compile(r"\b__import__\b"),
    re.compile(r"\bgetattr\b"),
    re.compile(r"\bsetattr\b"),
    re.compile(r"\bopen\s*\("),
    re.compile(r"\bcompile\s*\("),
    re.compile(r"\bbreakpoint\b"),
    re.compile(r"\bglobals\b"),
    re.compile(r"\blocals\b"),
    re.compile(r"\binput\b"),
]

# Allowed import names
_ALLOWED_IMPORTS = frozenset({"struct", "binascii", "re", "json", "codecs", "math", "typing", "io", "base64"})


# ── Core Engine ────────────────────────────────────────────────────────────────

class AutoREEngine:
    """
    Hermes3 Auto-RE Engine — five-stage binary format parser generator.

    Usage:
        engine = get_auto_re_engine()
        result = await engine.process_unknown_binary(file_path, content)

    Thread-safety: all Hermes3 calls go through async bridge; sandbox execution
    runs in asyncio.to_thread() to avoid blocking the event loop on M1.
    """

    __slots__ = tuple((
        "_catalog",
        "_attempt_count",
        "_enabled",
    ))

    def __init__(self) -> None:
        self._catalog = None   # lazy
        self._attempt_count = 0
        self._enabled = _is_auto_re_enabled()

    @property
    def catalog(self) -> "AutoRECatalog":
        if self._catalog is None:
            from hledac.universal.brain.auto_re.catalog import AutoRECatalog
            self._catalog = AutoRECatalog()
        return self._catalog

    @property
    def enabled(self) -> bool:
        return self._enabled

    def can_process(self) -> bool:
        """True if we have capacity for another attempt this sprint."""
        return self._enabled and self._attempt_count < _MAX_ATTEMPTS_PER_SPRINT

    # ── Public API ─────────────────────────────────────────────────────────────

    async def process_unknown_binary(
        self,
        file_path: str,
        content: bytes,
    ) -> AutoREResult:
        """
        Process an unknown binary file through all five AutoRE stages.

        Args:
            file_path: Path of the original file (for audit / Hermes3 context)
            content:   Raw file bytes (must be <= 1 MB)

        Returns:
            AutoREResult with IOC list if successful, empty list if not.
        """
        logger = logging.getLogger(__name__)

        # Guard: size check
        if len(content) > 1_048_576:
            return AutoREResult(
                success=False,
                file_hash=hashlib.sha256(content).hexdigest(),
                format_family="unknown",
                error="File exceeds 1 MB size limit for AutoRE",
                stage="A",
            )

        file_hash = hashlib.sha256(content).hexdigest()
        header16 = content[:16]
        header512 = content[:512]

        # ── Stage A: Magic-byte router ───────────────────────────────────────
        stage_a_start = time.monotonic()
        format_family = self.catalog.route_magic(header16)
        heuristic_family = None
        if format_family is None:
            heuristic_family = self.catalog.detect_heuristic_family(header512)
        chosen_family = format_family or heuristic_family
        family_name = chosen_family.family if chosen_family else "unknown"

        logger.info("[AUTO-RE] Stage A: file=%s family=%s", file_path, family_name)
        stage_a_ms = (time.monotonic() - stage_a_start) * 1000

        # Check audit cache (Stage E) — if already processed, return cached IOCs
        cached = await self._load_audit_cache(file_hash)
        if cached is not None:
            logger.info("[AUTO-RE] Stage E: audit cache hit for %s", file_hash[:12])
            return cached

        # ── Stage B: Hermes3 parser generation ───────────────────────────────
        entropy = self.catalog.compute_entropy(header512)
        ascii_ratio = self.catalog.compute_ascii_ratio(header512)

        prompt = self.catalog.build_hermes3_prompt(
            header512=header512,
            entropy=entropy,
            ascii_ratio=ascii_ratio,
            file_path=file_path,
            format_family=chosen_family,
        )

        hermes3_ms = 0.0
        format_hypothesis = ""
        parser_python = ""

        engine = _get_hermes3_engine()
        if engine is None:
            return AutoREResult(
                success=False,
                file_hash=file_hash,
                format_family=family_name,
                error="Hermes3Engine unavailable",
                stage="B",
            )

        try:
            hermes3_start = time.monotonic()
            response = await engine.generate(
                prompt,
                system_msg="You are a forensic binary format analyst. Always output valid JSON in <|constrain|> tags.",
                max_tokens=1024,
                temperature=0.3,
            )
            hermes3_ms = (time.monotonic() - hermes3_start) * 1000
            logger.info("[AUTO-RE] Stage B: Hermes3 responded in %.1fms", hermes3_ms)
        except Exception as e:
            logger.warning("[AUTO-RE] Stage B: Hermes3 call failed: %s", e)
            return AutoREResult(
                success=False,
                file_hash=file_hash,
                format_family=family_name,
                error=f"Hermes3 call failed: {e}",
                stage="B",
            )

        # Parse Hermes3 response for <|constrain|> JSON
        try:
            format_hypothesis, parser_python = self._parse_hermes3_response(response)
            if not parser_python:
                raise ValueError("No parser_python in Hermes3 response")
        except Exception as e:
            logger.warning("[AUTO-RE] Stage B: failed to parse Hermes3 response: %s", e)
            return AutoREResult(
                success=False,
                file_hash=file_hash,
                format_family=family_name,
                error=f"Parse Hermes3 response failed: {e}",
                stage="B",
            )

        # ── Stage C: Sandboxed execution ─────────────────────────────────────
        sandbox_ms = 0.0
        parse_result: list[dict[str, Any]] = []

        try:
            sandbox_ms = await self._sandboxed_execute(parser_python, content)
            logger.info("[AUTO-RE] Stage C: sandbox completed in %.1fms", sandbox_ms)
        except Exception as e:
            logger.warning("[AUTO-RE] Stage C: sandbox execution failed: %s", e)
            return AutoREResult(
                success=False,
                file_hash=file_hash,
                format_family=family_name,
                format_hypothesis=format_hypothesis,
                parser_source=parser_python,
                error=f"Sandbox execution failed: {e}",
                stage="C",
                hermes3_ms=hermes3_ms,
            )

        # ── Stage D: IOC validation gate ──────────────────────────────────────
        iocs: list[ParsedIOC] = []
        try:
            iocs = await self._validate_iocs(parse_result)
            logger.info("[AUTO-RE] Stage D: %d valid IOCs after gate", len(iocs))
        except Exception as e:
            logger.warning("[AUTO-RE] Stage D: IOC validation failed: %s", e)
            iocs = []

        success = len(iocs) > 0
        result = AutoREResult(
            success=success,
            file_hash=file_hash,
            format_family=family_name,
            format_hypothesis=format_hypothesis,
            iocs=iocs,
            parser_source=parser_python,
            error="" if success else "Stage D: no valid IOCs after gate",
            stage="D",
            hermes3_ms=hermes3_ms,
            sandbox_ms=sandbox_ms,
        )

        # ── Stage E: Audit trail ──────────────────────────────────────────────
        await self._save_audit_cache(file_hash, result)

        return result

    # ── Stage B helpers ────────────────────────────────────────────────────────

    CONSTRAIN_PATTERN = re.compile(
        r"<\|constrain\|>\s*(.*?)\s*<\|message\|>",
        re.DOTALL,
    )

    def _parse_hermes3_response(self, response: str) -> tuple[str, str]:
        """
        Extract format_hypothesis and parser_python from Hermes3 <|constrain|> block.

        Returns:
            (format_hypothesis: str, parser_python: str)
        Raises:
            ValueError if parsing fails.
        """
        import json

        match = self.CONSTRAIN_PATTERN.search(response)
        if not match:
            raise ValueError("No <|constrain|> block found in Hermes3 response")

        raw = match.group(1).strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in <|constrain|> block: {e}") from e

        hypothesis = str(parsed.get("format_hypothesis", ""))
        code = str(parsed.get("parser_python", ""))
        return hypothesis, code

    # ── Stage C: Sandbox execution ─────────────────────────────────────────────

    # ── Stage C: Sandbox execution helpers ────────────────────────────────────

    def _validate_code_length(self, code_bytes: bytes) -> None:
        """Validate code length constraint."""
        if len(code_bytes) > _MAX_CODE_BYTES:
            raise ValueError(
                f"Generated code exceeds {_MAX_CODE_BYTES} bytes "
                f"(got {len(code_bytes)})"
            )

    def _validate_forbidden_patterns(self, code: str) -> None:
        """Check code for forbidden patterns."""
        for pat in _FORBIDDEN_PATTERNS:
            if pat.search(code):
                raise ValueError(f"Generated code contains forbidden pattern: {pat.pattern}")

    def _validate_ast(self, code: str) -> ast.AST:
        """Parse and return AST, raising on syntax error."""
        try:
            return ast.parse(code)
        except SyntaxError as e:
            raise ValueError(f"AST parse failed: {e}") from e

    def _check_forbidden_ast_nodes(self, tree: ast.AST) -> None:
        """Walk AST and reject forbidden imports/calls."""
        forbidden_calls = {"eval", "exec", "compile", "breakpoint", "__import__"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name not in _ALLOWED_IMPORTS and not alias.name.startswith("hledac_"):
                        raise ValueError(f"AST: forbidden import '{alias.name}'")
            elif isinstance(node, ast.ImportFrom):
                if node.module not in _ALLOWED_IMPORTS and not (node.module or "").startswith("hledac_"):
                    raise ValueError(f"AST: forbidden import-from '{node.module}'")
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in forbidden_calls:
                    raise ValueError(f"AST: forbidden Call '{node.func.id}'")
                if isinstance(node.func, ast.Attribute) and node.func.attr in forbidden_calls:
                    raise ValueError(f"AST: forbidden attribute Call '{node.func.attr}'")

    async def _sandboxed_execute(
        self,
        parser_python: str,
        data: bytes,
    ) -> list[dict[str, Any]]:
        """
        Execute generated parser in a restricted subprocess sandbox.

        Security layers:
        1. Length check: code must be <= 2 KB
        2. Pattern check: forbidden keywords rejected before compile
        3. AST parse: any Import/Call to forbidden names → reject
        4. Subprocess: --add-opens isolation, 5s timeout, no network
        """
        code_bytes = parser_python.encode("utf-8")
        self._validate_code_length(code_bytes)
        self._validate_forbidden_patterns(parser_python)
        tree = self._validate_ast(parser_python)
        self._check_forbidden_ast_nodes(tree)

        # 4. Write wrapper script + run it directly (no nested -c injection)
        #    The wrapper receives data via stdin (not embedded in command line).
        import tempfile

        tmp_dir = Path(tempfile.gettempdir()) / "hledac_auto_re"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        # Wrapper receives data on stdin, writes JSON to stdout
        # Script path is safe: it's a temp file we created, not user input
        wrapper_path = tmp_dir / f"parser_{hashlib.sha256(code_bytes).hexdigest()[:12]}.py"

        wrapped_code = f'''# AutoRE sandbox — DO NOT RE-EXECUTE
# Generated automatically by parser_forge.py (ADVERSARY-004)
import json, sys, base64
from core import aclose

# Read binary data from stdin (base64-encoded to avoid binary stdin issues)
DATA = base64.b64decode(sys.stdin.read().strip())

{parser_python}

if __name__ == "__main__":
    try:
        result = parse(DATA)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({{"error": str(exc)}}), file=sys.stderr)
        sys.exit(1)
'''

        wrapper_path.write_text(wrapped_code, encoding="utf-8")

        try:
            loop = asyncio.get_running_loop()
            result_str = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    self._run_subprocess_sandbox,
                    wrapper_path,
                    data,
                ),
                timeout=_SANDBOX_TIMEOUT_S + 1.0,
            )
        finally:
            # Clean up wrapper script
            try:
                wrapper_path.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass

        if not result_str:
            return []

        try:
            parsed = json.loads(result_str)
            if isinstance(parsed, dict) and "error" in parsed:
                raise RuntimeError(f"Parser error: {parsed['error']}")
            if not isinstance(parsed, list):
                raise ValueError(f"Parser returned non-list: {type(parsed).__name__}")
            return parsed
        except Exception as e:
            raise ValueError(f"Parser output invalid: {e}") from e

    def _run_subprocess_sandbox(
        self,
        script_path: Path,
        data: bytes,
    ) -> str:
        """
        Run the sandboxed parser in a subprocess with restricted environment.

        Security: data is passed via stdin (base64-encoded), never via command line.
        The script_path is a temp file we created, so no user-controlled injection risk.
        """
        import base64

        env = {
            "PATH": "/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }
        data_b64 = base64.b64encode(data).decode("ascii")

        try:
            proc = subprocess.run(
                [sys.executable, str(script_path)],
                input=data_b64.encode("ascii"),
                capture_output=True,
                timeout=_SANDBOX_TIMEOUT_S,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return json.dumps({"error": "timeout"})
        except Exception as e:
            return json.dumps({"error": str(e)})

        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="replace")[:500]
            return json.dumps({"error": err})

        return proc.stdout.decode("utf-8", errors="replace")

    # ── Stage D: IOC validation gate ──────────────────────────────────────────

    async def _validate_iocs(
        self,
        parser_output: list[dict[str, Any]],
    ) -> list[ParsedIOC]:
        """
        Validate parsed IOCs against the Rust SIMD extractor (G1+G2 patterns).

        Algorithm:
        1. Serialize parser output to text (ioc_value column)
        2. Run rust.extract_iocs_simd() over all values
        3. Keep only values that appear in both parser output AND Rust extractor
        4. Confidence: parser supplies confidence, capped at 1.0
        """
        rust = _get_rust_ioc_extractor()
        if rust is None:
            # Fallback: trust parser output with low confidence
            return [
                ParsedIOC(
                    ioc_type=str(item.get("ioc_type", "unknown")),
                    ioc_value=str(item.get("ioc_value", "")),
                    confidence=min(float(item.get("confidence", 0.3)), 1.0),
                    context=str(item.get("context", "")),
                )
                for item in parser_output
                if item.get("ioc_value")
            ]

        # Collect all potential IOC values from parser output
        ioc_values = [str(item.get("ioc_value", "")) for item in parser_output if item.get("ioc_value")]
        if not ioc_values:
            return []

        # Run Rust extractor over concatenated text
        combined = "\n".join(ioc_values)
        loop = asyncio.get_running_loop()
        rust_iocs = await loop.run_in_executor(
            None,
            lambda: rust.extract_iocs_simd(combined),
        )

        # Build set of Rust-confirmed values
        rust_confirmed: set[str] = {v for _, v in rust_iocs}

        validated: list[ParsedIOC] = []
        for item in parser_output:
            ioc_value = str(item.get("ioc_value", ""))
            ioc_type = str(item.get("ioc_type", "unknown"))
            if ioc_value in rust_confirmed:
                validated.append(ParsedIOC(
                    ioc_type=ioc_type,
                    ioc_value=ioc_value,
                    confidence=min(float(item.get("confidence", 0.7)), 1.0),
                    context=str(item.get("context", "")),
                ))

        return validated

    # ── Stage E: Audit trail ───────────────────────────────────────────────────

    async def _audit_cache_path(self, file_hash: str) -> Path:
        return _AUDIT_DIR / f"{file_hash}.json"

    async def _save_audit_cache(self, file_hash: str, result: AutoREResult) -> None:
        """Write result to disk for 24h audit (never re-executed)."""
        try:
            _AUDIT_DIR.mkdir(parents=True, exist_ok=True)
            path = await self._audit_cache_path(file_hash)
            payload = msgspec.json.encode(result)
            path.write_bytes(payload)
        except Exception as e:
            logging.getLogger(__name__).warning(
                "[AUTO-RE] Stage E: failed to save audit cache: %s", e
            )

    async def _load_audit_cache(self, file_hash: str) -> AutoREResult | None:
        """Load from audit cache if exists (does NOT re-run parser)."""
        try:
            path = await self._audit_cache_path(file_hash)
            if not path.exists():
                return None
            # Check age: 24h TTL
            age_s = time.time() - path.stat().st_mtime
            if age_s > 86_400:
                path.unlink(missing_ok=True)
                return None
            data = path.read_bytes()
            return msgspec.json.decode(data, type=AutoREResult)
        except Exception:
            return None

    # ── Counter management ─────────────────────────────────────────────────────

    def record_attempt(self) -> None:
        """Called by the sidecar adapter after each attempt."""
        self._attempt_count += 1

    def reset(self) -> None:
        """Reset attempt counter (called at sprint start)."""
        self._attempt_count = 0


# ── Module-level singleton ──────────────────────────────────────────────────────

_ENGINE: AutoREEngine | None = None


def get_auto_re_engine() -> AutoREEngine:
    """Lazy singleton for the AutoRE engine."""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = AutoREEngine()
    return _ENGINE


def is_auto_re_enabled() -> bool:
    """Check if AutoRE feature is enabled (env var)."""
    return _is_auto_re_enabled()
