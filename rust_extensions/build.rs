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

fn extract_features_from_cargo_toml() -> Vec<String> {
    // Build.rs runs after Cargo parses features, but there's no CARGO_FEATURE_*
    // env var from maturin. Parse Cargo.toml directly to get feature names.
    let cargo_toml = std::fs::read_to_string("Cargo.toml").ok();
    let Some(toml_text) = cargo_toml else {
        return Vec::new();
    };
    let features_section = toml_text.split("[features]").nth(1)
        .and_then(|s| s.split('\n').take_while(|l| !l.starts_with('[')).collect::<String>().into());
    let Some(features_text) = features_section else {
        return Vec::new();
    };
    features_text
        .lines()
        .filter_map(|line| {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') {
                return None;
            }
            // Feature line format: "name = [...]" or "name/"
            let name = line.split('=').next().unwrap_or(line).split('/').next().unwrap_or(line).trim();
            if name.is_empty() || name == "[features]" {
                None
            } else {
                Some(name.to_string())
            }
        })
        .collect()
}

fn main() {
    // Parse Cargo.toml [features] section and emit as compile-time env var.
    // __features__() in lib.rs reads this via option_env!.
    let features = extract_features_from_cargo_toml();
    if !features.is_empty() {
        let features_list = features.join(",");
        println!("cargo:rustc-env=CARGO_FEATURES_LIST={}", features_list);
    }

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

    // F350M-R FIX: Detect Darwin version for vDSP availability.
    // On macOS 26.5+ (Darwin 25.5+), Apple removed vDSP symbols from
    // Accelerate.framework. We detect this at build time and set a cfg
    // so the accelerate module can fall back to scalar implementations.
    //
    // Darwin version mapping:
    //   24.x  = macOS 15.x (Sonoma)
    //   25.x  = macOS 26.x (whatever comes after)
    //   26.x  = macOS 27.x (future)
    // vDSP was removed in Darwin 25.5 (macOS 26.5).
    #[cfg(target_os = "macos")]
    {
        if let Ok(output) = std::process::Command::new("uname")
            .arg("-r")
            .output()
        {
            let version = String::from_utf8_lossy(&output.stdout);
            // Parse "25.5.0" -> (25, 5)
            let parts: Vec<&str> = version.trim().split('.').collect();
            let major: u32 = parts.get(0)
                .and_then(|s| s.parse().ok())
                .unwrap_or(0);
            let minor: u32 = parts.get(1)
                .and_then(|s| s.parse().ok())
                .unwrap_or(0);

            // Darwin 25.5+ = macOS 26.5+ — vDSP removed
            if major > 25 || (major == 25 && minor >= 5) {
                println!("cargo:rustc-cfg=vdsp_unavailable");
                eprintln!(
                    "[build.rs] WARNING: Darwin {}.{} detected — vDSP symbols removed. \
                    accelerate feature will use scalar fallback.",
                    major, minor
                );
            }
        }
    }

    // R4-05 FIX: Emit neon_available cfg for aarch64 targets.
    // build.rs runs as a PROCESSOR — it does NOT compile the crate, so
    // cfg(target_arch = "aarch64") in Cargo.toml does NOT apply here.
    // We detect aarch64 via the CARGO_CFG_TARGET_ARCH environment variable
    // (set by Cargo when it invokes build.rs) and emit the cfg so that
    // neon.rs functions with #[target_feature(enable = "neon")] actually
    // get NEON instructions emitted rather than scalar fallback.
    //
    // Without this, the compiler sees #[target_feature(enable = "neon")]
    // and falls back to scalar code even though #[cfg(target_arch = "aarch64")]
    // guards are present — the feature is not "enabled" from the compiler's
    // perspective without +neon in the target features.
    if std::env::var("CARGO_CFG_TARGET_ARCH").unwrap_or_default() == "aarch64" {
        println!("cargo:rustc-cfg=neon_available");
        eprintln!("[build.rs] aarch64 detected — NEON enabled");
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
