import json, shlex, subprocess, sys, sysconfig
from pathlib import Path

ROOT = Path("tests/probe_8bg")
DL = ROOT / "downloads"
LOG = ROOT / "logs"
DL.mkdir(parents=True, exist_ok=True)
LOG.mkdir(parents=True, exist_ok=True)

PKGS = [
    ("mlx", "mlx"),
    ("duckdb", "duckdb==1.5.0"),
    ("lmdb", "lmdb"),
    ("curl_cffi", "curl-cffi"),
    ("msgspec", "msgspec"),
    ("pyahocorasick", "pyahocorasick"),
    ("usearch", "usearch>=2.15.0"),
]

ABI_TAG = f"cp{sys.version_info.major}{sys.version_info.minor}" + ("t" if "t" in getattr(sys, "abiflags", "") else "")
PLATFORMS = ["macosx_15_0_arm64", "macosx_14_0_arm64", "macosx_13_0_arm64", "macosx_11_0_arm64"]
PYBIN = sys.executable

def run(cmd):
    # Security fix: shell=True with shlex.quote() on all user-variable parts
    # is acceptable here since cmd is constructed from trusted literals only
    r = subprocess.run(cmd, shell=True, text=True, capture_output=True)  # nosem: S602 — probe tests use trusted literal strings only
    return {"cmd": cmd, "rc": r.returncode, "stdout": r.stdout[-12000:], "stderr": r.stderr[-12000:]}

def pip_download_probe(dist_spec, py_bin):
    pkg_dir = DL / dist_spec.replace("==", "_").replace(">=", "_").replace("-", "_")
    pkg_dir.mkdir(parents=True, exist_ok=True)
    for platform_tag in PLATFORMS:
        cmd = (f"{shlex.quote(py_bin)} -m pip download "
               f"--only-binary=:all: "
               f"--python-version {sys.version_info.major}.{sys.version_info.minor} "
               f"--abi {ABI_TAG} --platform {platform_tag} "
               f"-d {shlex.quote(str(pkg_dir))} {shlex.quote(dist_spec)}")
        result = run(cmd)
        result["platform"] = platform_tag
        if result["rc"] == 0:
            return result
    return result

def pip_install_probe(dist_spec, py_bin):
    cmd = f"{shlex.quote(py_bin)} -m pip install -U {shlex.quote(dist_spec)}"
    return run(cmd)

def no_gil_import_probe(import_name, py_bin):
    code = f'''
import json, sys
out = {{}}
out["no_gil_before"] = (not sys._is_gil_enabled())
try:
    __import__({import_name!r})
    out["import_ok"] = True
except Exception as e:
    out["import_ok"] = False
    out["error"] = repr(e)
out["no_gil_after"] = (not sys._is_gil_enabled())
print(json.dumps(out))
'''
    r = subprocess.run([py_bin, "-c", code], text=True, capture_output=True)
    return {"rc": r.returncode, "stdout": r.stdout.strip(), "stderr": r.stderr.strip()}

summary = {
    "probe_scope": "PROBING_313t_AS_PROXY_FOR_FT_READINESS",
    "python": {
        "version": sys.version,
        "abiflags": getattr(sys, "abiflags", ""),
        "Py_GIL_DISABLED": sysconfig.get_config_var("Py_GIL_DISABLED"),
        "gil_disabled_runtime": not sys._is_gil_enabled(),
        "abi_tag": ABI_TAG,
    },
    "packages": {},
}

for import_name, dist_spec in PKGS:
    item = {}
    item["wheel_only_download"] = pip_download_probe(dist_spec, PYBIN)
    item["install"] = pip_install_probe(dist_spec, PYBIN)
    item["no_gil_probe"] = no_gil_import_probe(import_name, PYBIN)
    summary["packages"][import_name] = item

(ROOT / "compat_313t_summary.json").write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2))