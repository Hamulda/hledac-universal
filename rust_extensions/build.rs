// build.rs — dynamic Python version detection via pyo3-build-config.
//
// Previous version hard-coded `/opt/homebrew/opt/python@3.13/...` which
// broke builds against Python 3.14 (current target per CLAUDE.md).
//
// Strategy:
// 1. Let pyo3-build-config::get() auto-detect the Python interpreter
//    that `maturin` invoked (matches `requires-python` in pyproject.toml).
// 2. Emit cargo:rustc-link-arg=-undefined,dynamic_lookup ONLY on macOS,
//    where undefined symbols are resolved at extension load time by
//    the Python framework dylib. This is a no-op on Linux/Windows.
// 3. Re-run on maturin/pyproject.toml changes AND pyo3-build-config
//    env vars — covers Python header changes, virtualenv switches,
//    and maturin version upgrades that could alter ABI flags.

use pyo3_build_config::use_pyo3_cfgs;

fn main() {
    // Triggers pyo3's auto-detection and emits the necessary rustc-cfgs
    // (PyPy3, Py_3_x, etc.). Linking flags are still set by maturin
    // via PyO3's build script — we just need pyo3-build-config to run.
    use_pyo3_cfgs();

    // On macOS, use dynamic lookup so the extension does not need to
    // be linked against a specific Python.framework dylib at build
    // time — Python loads it at import-time. This is what maturin does
    // for abi3 wheels; we do it for non-abi3 builds too.
    #[cfg(target_os = "macos")]
    {
        println!("cargo:rustc-link-arg=-undefined");
        println!("cargo:rustc-link-arg=dynamic_lookup");
    }

    // Always re-run if build.rs changes (obvious).
    println!("cargo:rerun-if-changed=build.rs");

    // Re-run when maturin configuration changes — maturin version,
    // build flags, or Python version selection in pyproject.toml.
    println!("cargo:rerun-if-changed=maturin.toml");
    println!("cargo:rerun-if-changed=pyproject.toml");

    // Re-run when pyo3-build-config env vars change — these control
    // Python executable path and header location used by pyo3-build-config.
    println!("cargo:rerun-if-env-changed=PYO3_CONFIG_FILE");
    println!("cargo:rerun-if-env-changed=PYO3_PYTHON");
    println!("cargo:rerun-if-env-changed=PATH"); // Python interpreter switch
}
