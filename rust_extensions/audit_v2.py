"""
audit_v2.py — Dynamic Rust dead-code analyzer for hledac_rust_extensions.

Replaces the stale, dict-driven audit.py. Liveness is derived directly from source:

A module X (declared `mod X;` in src/lib.rs) is ALIVE if ANY of:
  1. wiring facade exists: rust_extensions/wiring/<X>_wiring.py
     (wiring/__init__.py imports every *_wiring module, so any module with a
      wiring file is loaded whenever ANY rust_extensions.wiring import happens).
  2. integrations.py references the module via _rust_backend.<subname>,
     _rust_available("<subname>") or getattr(_rust_backend.raw, "<subname>").
  3. The _core/rust_backend facade references the module's registered PyO3 symbols
     (scanned once across all Python in a Rust context).
  4. Another Rust module depends on it via crate::X / use crate::X / super::X.

Only modules satisfying NONE of the above are truly dead.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")
SRC = ROOT / "rust_extensions" / "src"
WIRING = ROOT / "rust_extensions" / "wiring"
INTEGRATIONS = ROOT / "rust_extensions" / "integrations.py"
LIB_RS = SRC / "lib.rs"
RUST_BACKEND = ROOT / "_core" / "rust_backend"

RUST_CONTEXT = ("rust", "hledac_rust_extensions", "_rust_backend", "rust_backend",
                "from hledac", "integrations", "wiring")

# rust_backend facade domain -> rust module (when domain name differs from file)
DOMAIN_TO_MODULE = {
    "url": "url_ops",
    "ip": "ip_parse",
    "quality": "quality_gate",
    "graph": "graph_traverse",
    "stix": "stix_2_1",
}
MODULE_TO_DOMAINS = {}
for _d, _m in DOMAIN_TO_MODULE.items():
    if _m:
        MODULE_TO_DOMAINS.setdefault(_m, []).append(_d)

# module name -> wiring module basename (when the wiring file name differs)
WIRING_NAME_MAP = {
    "serde_json_rs": "serde_json",
}

GENERIC_STOPS = {
    "new", "get", "set", "add", "remove", "contains", "len", "count", "clear",
    "is_empty", "default", "clone", "drop", "update", "delete", "insert", "query",
    "reset", "init", "close", "open", "read", "write", "run", "start", "stop",
    "process", "handle", "build", "create", "load", "save", "parse", "extract",
    "compute", "normalize", "validate", "register", "check", "find", "search",
    "batch", "to_string", "from_string", "info", "state", "version", "name",
}


@dataclass
class ModuleInfo:
    name: str
    cfg: str | None
    line: int
    file: Path | None
    used: bool = False
    reasons: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)


def parse_lib_rs() -> list[ModuleInfo]:
    text = LIB_RS.read_text()
    lines = text.splitlines()
    mods: list[ModuleInfo] = []
    for idx, line in enumerate(lines):
        m = re.match(r"\s*mod\s+(\w+)\s*;", line)
        if not m:
            continue
        name = m.group(1)
        cfg = None
        j = idx - 1
        while j >= 0 and (lines[j].strip().startswith("#[") or lines[j].strip().startswith("//")):
            cm = re.search(r"#\[cfg\((.*?)\)\]", lines[j])
            if cm:
                cfg = cm.group(1)
            j -= 1
        f = SRC / f"{name}.rs"
        if not f.exists():
            alt = SRC / name / "mod.rs"
            f = alt if alt.exists() else None
        mods.append(ModuleInfo(name=name, cfg=cfg, line=idx + 1, file=f))
    return mods


def extract_symbols(path: Path) -> list[str]:
    if path is None or not path.exists():
        return []
    text = path.read_text()
    syms: set[str] = set()
    for mm in re.finditer(r"#\[pyfunction[^\]]*\]\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)", text, re.DOTALL):
        syms.add(mm.group(1))
    for mm in re.finditer(r"#\[pyclass[^\]]*\]\s*(?:pub\s+)?(?:struct|class|enum)\s+(\w+)", text):
        syms.add(mm.group(1))
    for mm in re.finditer(r"add_function\s*\(\s*wrap_pyfunction!\s*\(\s*(\w+)", text):
        syms.add(mm.group(1))
    for mm in re.finditer(r"add_class\s*::\s*<\s*(\w+)\s*>", text):
        syms.add(mm.group(1))
    for mm in re.finditer(r'm\.add\(\s*"(\w+)"', text):
        syms.add(mm.group(1))
    return sorted(syms)


def load_python() -> dict[Path, str]:
    out: dict[Path, str] = {}
    for p in ROOT.rglob("*.py"):
        if "__pycache__" in str(p):
            continue
        try:
            out[p] = p.read_text()
        except Exception:
            out[p] = ""
    return out


def load_rust() -> dict[Path, str]:
    out: dict[Path, str] = {}
    for p in SRC.rglob("*.rs"):
        try:
            out[p] = p.read_text()
        except Exception:
            out[p] = ""
    return out


def precompute_python(py: dict[Path, str], all_symbols: set[str]):
    """One pass over all Python: collect domain tokens + symbol refs (any rust context)."""
    domain_re = re.compile(r"\brust\.(\w+)\.|\brust_backend\.(\w+)|rust\.raw\.(\w+)")
    domains: set[str] = set()
    symset: set[str] = set()
    for c in py.values():
        # fast pre-filter: only scan files that touch the Rust layer at all
        if not any(k in c for k in RUST_CONTEXT):
            continue
        for mm in domain_re.finditer(c):
            for g in mm.groups():
                if g:
                    domains.add(g)
        for ln in c.splitlines():
            if not any(k in ln for k in RUST_CONTEXT):
                continue
            for tok in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", ln):
                if tok in all_symbols:
                    symset.add(tok)
    return domains, symset


def integrations_refs(mi: ModuleInfo) -> list[str]:
    ic = INTEGRATIONS.read_text() if INTEGRATIONS.exists() else ""
    aliases = [mi.name] + MODULE_TO_DOMAINS.get(mi.name, [])
    for a in aliases:
        if re.search(r"rust_backend\." + re.escape(a) + r"(?![\w])", ic):
            return [f"integrations:rust_backend.{a}"]
        if re.search(r'_rust_available\(\s*["\']' + re.escape(a) + r'["\']', ic):
            return [f"integrations:_rust_available({a})"]
        if re.search(r'getattr\(_rust_backend\.raw,\s*["\']' + re.escape(a) + r'["\']', ic):
            return [f"integrations:raw.{a}"]
    return []


def quoted_module_ref(mi: ModuleInfo, py: dict[Path, str]) -> bool:
    """Detect `getattr(ext, "modname")`, FFI_MODULE_X = "modname", dict keys, etc.
    The submodule name is usually the file name; Python accesses it as a quoted string."""
    pat = re.compile(r'["\']' + re.escape(mi.name) + r'["\']')
    for p, c in py.items():
        for ln in c.splitlines():
            s = ln.strip()
            if s.startswith("#"):
                continue
            if pat.search(ln):
                return True
    return False


def macro_used(mi: ModuleInfo, rs: dict[Path, str]) -> str | None:
    """Detect a module that exists primarily to export a macro_rules! used elsewhere
    (e.g. ffi_safe -> ffi_safe! in madvise.rs)."""
    pat = re.compile(r"(?<![\w!])" + re.escape(mi.name) + r"!(?!\()")
    for p, c in rs.items():
        if mi.file is not None and p.resolve() == mi.file.resolve():
            continue
        if pat.search(c):
            return str(p)
    return None


def rust_uses_module(mi: ModuleInfo, rs: dict[Path, str]) -> str | None:
    pats = [
        re.compile(r"(?<![\w:])crate::" + re.escape(mi.name) + r"(?![\w])"),
        re.compile(r"\buse\s+crate::" + re.escape(mi.name) + r"\b"),
        re.compile(r"(?<![\w:])super::" + re.escape(mi.name) + r"(?![\w])"),
    ]
    for p, c in rs.items():
        if mi.file is not None and p.resolve() == mi.file.resolve():
            continue
        for pat in pats:
            if pat.search(c):
                return str(p)
    return None


def main() -> int:
    mods = parse_lib_rs()
    py = load_python()
    rs = load_rust()
    for mi in mods:
        mi.symbols = extract_symbols(mi.file)
    all_symbols = set()
    for mi in mods:
        for s in mi.symbols:
            if s not in GENERIC_STOPS:
                all_symbols.add(s)
    domains, symset = precompute_python(py, all_symbols)

    for mi in mods:
        reasons: list[str] = []
        wf = WIRING / f"{WIRING_NAME_MAP.get(mi.name, mi.name)}_wiring.py"
        if wf.exists():
            reasons.append("wiring:" + wf.name)
        ir = integrations_refs(mi)
        if ir:
            reasons += ir
        # domain / direct reference
        check_names = {mi.name} | set(MODULE_TO_DOMAINS.get(mi.name, []))
        if check_names & domains:
            reasons.append("rust_backend-domain")
        # symbol reference in any Rust-context python line
        if set(mi.symbols) & symset:
            reasons.append("py-symbol")
        rd = rust_uses_module(mi, rs)
        if rd:
            reasons.append("rust_dep:" + rd)
        if quoted_module_ref(mi, py):
            reasons.append("quoted-module-ref")
        mu = macro_used(mi, rs)
        if mu:
            reasons.append("macro_dep:" + mu)
        mi.used = bool(reasons)
        mi.reasons = reasons

    used = [m for m in mods if m.used]
    dead = [m for m in mods if not m.used]
    print("=" * 90)
    print("DYNAMIC RUST DEAD-CODE ANALYSIS (audit_v2)")
    print("=" * 90)
    print(f"Total modules in lib.rs : {len(mods)}")
    print(f"ALIVE                  : {len(used)}")
    print(f"TRULY DEAD (candidates): {len(dead)}")
    print()
    print("ALIVE modules (evidence):")
    for m in sorted(used, key=lambda x: x.name):
        cfg = f" [{m.cfg}]" if m.cfg else ""
        print(f"  {m.name:<26}{cfg}  <- {m.reasons[0] if m.reasons else '?'}")
    print()
    print("TRULY DEAD modules (NO python ref, NO rust-internal dep):")
    for m in sorted(dead, key=lambda x: x.name):
        cfg = f" [{m.cfg}]" if m.cfg else " [always]"
        print(f"  {m.name:<26}{cfg}  file={'yes' if m.file else 'MISSING'}")
    print()
    missing = [m for m in mods if m.file is None]
    if missing:
        print("WARNING: mod declarations without a .rs file:")
        for m in missing:
            print(f"  {m.name} (line {m.line})")
    data = {
        "total": len(mods),
        "alive": [m.name for m in used],
        "dead": [
            {"name": m.name, "cfg": m.cfg, "file": str(m.file) if m.file else None}
            for m in dead
        ],
    }
    (ROOT / "rust_extensions" / "audit_v2_report.json").write_text(json.dumps(data, indent=2))
    print(f"\nReport written: rust_extensions/audit_v2_report.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
