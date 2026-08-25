"""
ISSUE-007: Rust Extensions Integration Audit (CORRECTED / DYNAMIC)
===============================================================

This is a ground-up rewrite of the original audit.py, which was broken in two ways:

  1. `PROJECT_ROOT / "hledac" / "universal"` pointed at a NON-EXISTENT path
     (`hledac/universal/hledac/universal`), so `find_python_callers()` returned
     ZERO callers for *every* module. That single bug is why the old report
     claimed "50+ ZOMBIE modules".
  2. The classification was driven by a hand-maintained `RUST_MODULES` dict that
     drifted from reality (e.g. it marked `circuit_breaker`, `content_hasher`,
     `aho_corasick_simd`, `simhash_ext`, `telemetry_agg`, `url_engine`,
     `tls_metadata`, `graph_analytics`, `claims_extraction`, `simd_similarity`,
     `accelerate`, `text_similarity`, `adaptive_scheduler`, `dedup_bloom`,
     `mpsc_pool`, `ioc_dedup`, `pipeline_compose`, `signal_batch`, `deobfuscate`,
     `html_parse`, `fulltext_index`, `serde_json_rs`, `text_norm`, `graph_cache`
     as ZOMBIE — all of which are in fact LIVE via the wiring layer).

This version derives liveness directly from source. A module `X` (declared
`mod X;` in src/lib.rs) is ALIVE if ANY of:

  1. A wiring facade exists: rust_extensions/wiring/<X>_wiring.py
     (wiring/__init__.py imports every *_wiring module, so any module with a
      wiring file is loaded whenever ANY rust_extensions.wiring import happens).
  2. integrations.py references the module via _rust_backend.<subname>,
     _rust_available("<subname>") or getattr(_rust_backend.raw, "<subname>").
  3. The _core/rust_backend facade, or ANY python file in a Rust context,
     references the module's registered PyO3 symbols or its submodule name
     (e.g. `getattr(ext, "finding_collapser")`, `rust.graph_centrality.…`).
  4. Another Rust module depends on it via crate::X / use crate::X / super::X,
     OR exports a macro_rules! that is invoked elsewhere (e.g. `ffi_safe!`).

Only modules satisfying NONE of the above are truly dead and safe to remove.

Run:  python rust_extensions/audit.py [--verbose] [--json FILE] [--csv FILE]
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, field
from enum import Enum, auto
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # .../hledac/universal
SRC = ROOT / "rust_extensions" / "src"
WIRING = ROOT / "rust_extensions" / "wiring"
INTEGRATIONS = ROOT / "rust_extensions" / "integrations.py"
LIB_RS = SRC / "lib.rs"
RUST_BACKEND = ROOT / "_core" / "rust_backend"

RUST_CONTEXT = ("rust", "hledac_rust_extensions", "_rust_backend", "rust_backend",
                "from hledac", "integrations", "wiring", ".ext", "probe")

# rust_backend facade domain -> rust module (when the domain name differs from file)
DOMAIN_TO_MODULE = {
    "url": "url_ops",
    "ip": "ip_parse",
    "quality": "quality_gate",
    "graph": "graph_traverse",
    "stix": "stix_2_1",
}
MODULE_TO_DOMAINS: dict[str, list[str]] = {}
for _d, _m in DOMAIN_TO_MODULE.items():
    if _m:
        MODULE_TO_DOMAINS.setdefault(_m, []).append(_d)

# module name -> wiring module basename (when the wiring file name differs)
WIRING_NAME_MAP = {"serde_json_rs": "serde_json"}

GENERIC_STOPS = {
    "new", "get", "set", "add", "remove", "contains", "len", "count", "clear",
    "is_empty", "default", "clone", "drop", "update", "delete", "insert", "query",
    "reset", "init", "close", "open", "read", "write", "run", "start", "stop",
    "process", "handle", "build", "create", "load", "save", "parse", "extract",
    "compute", "normalize", "validate", "register", "check", "find", "search",
    "batch", "to_string", "from_string", "info", "state", "version", "name",
}


class ModuleStatus(Enum):
    ACTIVE = auto()       # referenced by Python (wiring / integrations / facade / gettatr)
    ZOMBIE = auto()       # no Python caller, no Rust-internal dependency -> safe to remove


@dataclass(slots=True)
class AuditResult:
    name: str
    status: str
    feature: str
    file: str | None
    evidence: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Parsing / extraction
# --------------------------------------------------------------------------
def parse_lib_rs() -> list[tuple[str, str | None, int]]:
    """Return (module_name, cfg_feature, line_no) for every `mod X;` in lib.rs."""
    text = LIB_RS.read_text()
    lines = text.splitlines()
    out: list[tuple[str, str | None, int]] = []
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
        out.append((name, cfg, idx + 1))
    return out


def module_file(name: str) -> Path | None:
    f = SRC / f"{name}.rs"
    if f.exists():
        return f
    alt = SRC / name / "mod.rs"
    return alt if alt.exists() else None


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


# --------------------------------------------------------------------------
# Liveness detectors
# --------------------------------------------------------------------------
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
    """One pass over all Python: collect domain tokens + symbol refs (rust context)."""
    domain_re = re.compile(r"\brust\.(\w+)\.|\brust_backend\.(\w+)|rust\.raw\.(\w+)")
    domains: set[str] = set()
    symset: set[str] = set()
    for c in py.values():
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


def integrations_refs(name: str) -> list[str]:
    ic = INTEGRATIONS.read_text() if INTEGRATIONS.exists() else ""
    aliases = [name] + MODULE_TO_DOMAINS.get(name, [])
    for a in aliases:
        if re.search(r"rust_backend\." + re.escape(a) + r"(?![\w])", ic):
            return [f"integrations:rust_backend.{a}"]
        if re.search(r'_rust_available\(\s*["\']' + re.escape(a) + r'["\']', ic):
            return [f"integrations:_rust_available({a})"]
        if re.search(r'getattr\(_rust_backend\.raw,\s*["\']' + re.escape(a) + r'["\']', ic):
            return [f"integrations:raw.{a}"]
    return []


def quoted_module_ref(name: str, py: dict[Path, str]) -> bool:
    """Detect getattr(ext, "modname"), FFI_MODULE_X = "modname", dict keys, etc."""
    pat = re.compile(r'["\']' + re.escape(name) + r'["\']')
    for c in py.values():
        for ln in c.splitlines():
            if ln.strip().startswith("#"):
                continue
            if pat.search(ln):
                return True
    return False


def rust_dep(name: str, own_file: Path | None, rs: dict[Path, str]) -> str | None:
    pats = [
        re.compile(r"(?<![\w:])crate::" + re.escape(name) + r"(?![\w])"),
        re.compile(r"\buse\s+crate::" + re.escape(name) + r"\b"),
        re.compile(r"(?<![\w:])super::" + re.escape(name) + r"(?![\w])"),
    ]
    for p, c in rs.items():
        if own_file is not None and p.resolve() == own_file.resolve():
            continue
        for pat in pats:
            if pat.search(c):
                return str(p)
    return None


def macro_dep(name: str, own_file: Path | None, rs: dict[Path, str]) -> str | None:
    pat = re.compile(r"(?<![\w!])" + re.escape(name) + r"!(?!\()")
    for p, c in rs.items():
        if own_file is not None and p.resolve() == own_file.resolve():
            continue
        if pat.search(c):
            return str(p)
    return None


def run_audit() -> list[AuditResult]:
    mods = parse_lib_rs()
    py = load_python()
    rs = load_rust()
    sym_map: dict[str, list[str]] = {}
    all_symbols: set[str] = set()
    for name, _cfg, _ln in mods:
        syms = extract_symbols(module_file(name))
        sym_map[name] = syms
        for s in syms:
            if s not in GENERIC_STOPS:
                all_symbols.add(s)
    domains, symset = precompute_python(py, all_symbols)

    results: list[AuditResult] = []
    for name, cfg, ln in mods:
        f = module_file(name)
        ev: list[str] = []
        wf = WIRING / f"{WIRING_NAME_MAP.get(name, name)}_wiring.py"
        if wf.exists():
            ev.append(f"wiring:{wf.name}")
        ev += integrations_refs(name)
        if {name} | set(MODULE_TO_DOMAINS.get(name, [])) & domains:
            ev.append("rust_backend-domain")
        if set(sym_map[name]) & symset:
            ev.append("py-symbol")
        if quoted_module_ref(name, py):
            ev.append("quoted-module-ref")
        rd = rust_dep(name, f, rs)
        if rd:
            ev.append("rust_dep:" + rd)
        mu = macro_dep(name, f, rs)
        if mu:
            ev.append("macro_dep:" + mu)
        status = ModuleStatus.ACTIVE.name if ev else ModuleStatus.ZOMBIE.name
        results.append(AuditResult(name=name, status=status, feature=cfg or "core/always",
                                    file=str(f) if f else None, evidence=ev))
    return results


# --------------------------------------------------------------------------
# Reporting (compatible with the original CLI)
# --------------------------------------------------------------------------
def print_report(results: list[AuditResult], verbose: bool = False) -> None:
    total = len(results)
    active = [r for r in results if r.status == "ACTIVE"]
    zombie = [r for r in results if r.status == "ZOMBIE"]
    print("\n" + "=" * 80)
    print("ISSUE-007: Rust Extensions Integration Audit (DYNAMIC / CORRECTED)")
    print("=" * 80)
    print(f"\nModule Summary:")
    print(f"   Total modules parsed from lib.rs : {total}")
    print(f"   ACTIVE  (referenced by Python)   : {len(active)}")
    print(f"   ZOMBIE  (truly dead, removable)  : {len(zombie)}")
    print("\nActive modules (evidence):")
    for r in sorted(active, key=lambda x: x.name):
        print(f"   {r.name:<26} [{r.feature:<22}]  <- {r.evidence[0] if r.evidence else '?'}")
    if zombie:
        print("\nZOMBIE modules (no Python caller, no Rust-internal dependency):")
        for r in sorted(zombie, key=lambda x: x.name):
            print(f"   {r.name:<26} [{r.feature:<22}]  file={'yes' if r.file else 'MISSING'}")
    else:
        print("\nZOMBIE modules: NONE — every module in lib.rs is reachable from Python"
              " or from another Rust module.")
    if verbose:
        print("\nDetailed evidence:")
        for r in sorted(results, key=lambda x: x.name):
            print(f"   {r.name:<26} {r.status:<8} {', '.join(r.evidence) or '—'}")
    print("\n" + "=" * 80)


def export_json(results: list[AuditResult], out: Path) -> None:
    data = {
        "total_modules": len(results),
        "active_modules": sum(1 for r in results if r.status == "ACTIVE"),
        "zombie_modules": sum(1 for r in results if r.status == "ZOMBIE"),
        "modules": [asdict(r) for r in results],
    }
    out.write_text(json.dumps(data, indent=2))
    print(f"\nJSON report exported to: {out}")


def export_csv(results: list[AuditResult], out: Path) -> None:
    import csv
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Module", "Status", "Feature", "File", "Evidence"])
        for r in results:
            w.writerow([r.name, r.status, r.feature, r.file or "", "; ".join(r.evidence)])
    print(f"\nCSV report exported to: {out}")


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Rust Extensions Integration Audit (dynamic)")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--json", "-j", metavar="FILE")
    ap.add_argument("--csv", "-c", metavar="FILE")
    args = ap.parse_args()
    results = run_audit()
    print_report(results, verbose=args.verbose)
    if args.json:
        export_json(results, Path(args.json))
    if args.csv:
        export_csv(results, Path(args.csv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
