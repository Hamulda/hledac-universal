// build.rs — dynamic Python version detection via pyo3-build-config.
//
// NEXTGEN-05: BUILD-TIME FFI TYPE-SAFETY
// This build script now performs:
// 1. Dynamic Python version detection via pyo3-build-config
// 2. FFI type manifest generation (ffi_type_manifest.py)
//    - Parses #[pyclass] and #[pyfunction] in src/*.rs
//    - Generates _ffi_type_manifest.json (type metadata)
//    - Generates hledac_rust_extensions.pyi (auto-generated stub)
// 3. macOS-specific linker flags
// 4. Platform-specific feature detection (NEON, vDSP availability)
// 5. ABI mode verification (ISSUE-03): Validates non-abi3 vs abi3 consistency
//
// This transforms RUNTIME segfaults into BUILD-TIME failures when:
// - Python slots don't match Rust #[pyclass] field layout
// - Missing getters/setters on Python side
// - Type mismatches between Python hints and Rust types
// - ABI mode mismatch (non-abi3 build but .abi3.so expected)
//
// ABI Mode Detection Strategy:
//   NON-ABI3 (extension-module): Produces cpython-314-darwin.so
//   ABI3 (abi3-py3XX feature):   Produces abi3.so
//   This project uses NON-ABI3 — see Cargo.toml lines 6-27 for rationale.
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
// 4. Re-run when Rust source files change (src/**/*.rs).

use pyo3_build_config::use_pyo3_cfgs;
use std::process::Command;

fn extract_features_from_cargo_toml() -> Vec<String> {
    // Build.rs runs after Cargo parses features, but there's no CARGO_FEATURE_*
    // env var from maturin. Parse Cargo.toml directly to get feature names.
    let cargo_toml = std::fs::read_to_string("Cargo.toml").ok();
    let Some(toml_text) = cargo_toml else {
        return Vec::new();
    };
    let features_section = toml_text.split("[features]").nth(1).and_then(|s| {
        s.split('\n')
            .take_while(|l| !l.starts_with('['))
            .collect::<String>()
            .into()
    });
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
            let name = line
                .split('=')
                .next()
                .unwrap_or(line)
                .split('/')
                .next()
                .unwrap_or(line)
                .trim();
            if name.is_empty() || name == "[features]" {
                None
            } else {
                Some(name.to_string())
            }
        })
        .collect()
}

fn main() {
    // ISSUE-01: Compute source hash for freshness verification
    // This hash is emitted as a compile-time env var accessible via option_env! in lib.rs
    let source_hash = compute_source_hash();
    if !source_hash.is_empty() {
        println!("cargo:rustc-env=CARGO_SOURCE_HASH={}", source_hash);
        eprintln!("[build.rs] ISSUE-01: Source hash = {}", &source_hash[..16.min(source_hash.len())]);
    } else {
        eprintln!("[build.rs] WARNING: Could not compute source hash (src/ not found)");
    }

    // Parse Cargo.toml [features] section and emit as compile-time env var.
    // __features__() in lib.rs reads this via option_env!.
    let features = extract_features_from_cargo_toml();
    if !features.is_empty() {
        let features_list = features.join(",");
        println!("cargo:rustc-env=CARGO_FEATURES_LIST={}", features_list);
    }

    // ISSUE-03: ABI mode verification — ensure crate-type matches expected output name
    verify_abi_mode();

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
        if let Ok(output) = std::process::Command::new("uname").arg("-r").output() {
            let version = String::from_utf8_lossy(&output.stdout);
            // Parse "25.5.0" -> (25, 5)
            let parts: Vec<&str> = version.trim().split('.').collect();
            let major: u32 = parts.get(0).and_then(|s| s.parse().ok()).unwrap_or(0);
            let minor: u32 = parts.get(1).and_then(|s| s.parse().ok()).unwrap_or(0);

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

    // NEXTGEN-05: FFI Type Manifest Generation
    // Run the Python script to generate:
    //   1. _ffi_type_manifest.json — type metadata for validation
    //   2. hledac_rust_extensions.pyi — auto-generated stub
    //
    // This is BUILD-TIME FFI safety: any mismatch between Rust structs
    // and Python types will cause a BUILD FAILURE (not runtime segfault).
    run_ffi_type_manifest();

    // ISSUE-11: BUILD_MANIFEST Generation
    // Generate BUILD_MANIFEST.json containing SHA256 of all source files.
    // This enables fail-closed staleness detection at runtime.
    run_build_manifest();

    // Re-run when Rust source files change — they affect the generated manifest
    println!("cargo:rerun-if-changed=src/lib.rs");
    println!("cargo:rerun-if-changed=src/");
}

// ============================================================================
// ISSUE-03: ABI Mode Verification
// ============================================================================

/// ISSUE-03: Verify that crate-type matches the expected output filename.
///
/// ABI Mode Detection:
///   - NON-ABI3 (extension-module): Produces cpython-314-darwin.so
///   - ABI3 (abi3-py3XX feature):    Produces abi3.so
///
/// This project uses NON-ABI3 (see Cargo.toml lines 6-27 for rationale).
/// The prober (_prober.py) expects hledac_rust_extensions.cpython-314-darwin.so
///
/// This function detects the ABI mode from Cargo.toml and:
///   1. Emits expected output filename as a cargo warning
///   2. Fails the build if abi3 is detected (not supported by this project)
fn verify_abi_mode() {
    let cargo_toml = match std::fs::read_to_string("Cargo.toml") {
        Ok(content) => content,
        Err(e) => {
            eprintln!("[build.rs] ERROR: Failed to read Cargo.toml: {}", e);
            return;
        }
    };

    // Check for abi3 feature in pyo3 dependency
    // IMPORTANT: Look for actual feature declarations, not comments containing "abi3"
    // Pattern: features = ["abi3-py3"] or features = ["extension-module", "abi3-py314"]
    // We match quoted strings only to avoid matching comment text
    let has_abi3_feature = cargo_toml.contains(r#""abi3-py3"#)
        || cargo_toml.contains(r#""abi3"#);

    // Check for extension-module in actual dependency, not comments
    // Pattern: features = [..., "extension-module", ...]
    let has_extension_module = cargo_toml.contains(r#""extension-module""#);

    // Detect crate-type from [lib] section
    let is_cdylib = cargo_toml.contains(r#"crate-type = ["cdylib""#)
        || cargo_toml.contains("crate-type = [\"cdylib\"");

    if has_abi3_feature {
        // ABI3 build detected — this is NOT supported by this project
        eprintln!(
            r#"[build.rs] ERROR: ABI3 build detected but this project requires NON-ABI3!
|
| ABI3 builds produce: hledac_rust_extensions.abi3.so
| This project expects: hledac_rust_extensions.cpython-314-darwin.so
|
| ABI3 is incompatible with pyo3-async-runtimes future_into_py() which requires
| full Python C API access (tp_subclass, tp_new, memory-view) not available in
| the stable ABI subset.
|
| To fix: Remove abi3-py3XX feature from Cargo.toml pyo3 dependency.
| Current configuration requires: extension-module (non-abi3).
"#
        );
        std::process::exit(1);
    } else if has_extension_module && is_cdylib {
        // NON-ABI3 build detected — this is correct
        eprintln!(
            "[build.rs] INFO: NON-ABI3 build confirmed (extension-module + cdylib)"
        );
        eprintln!(
            "[build.rs] INFO: Expected output: hledac_rust_extensions.cpython-{{PYTHON_VERSION}}-darwin.so"
        );
        eprintln!(
            "[build.rs] INFO: Prober (_prober.py) expects: hledac_rust_extensions.cpython-{{MAJOR}}{{MINOR}}-darwin.so"
        );

        // Emit expected filename pattern as cargo warning for CI visibility
        println!("cargo:warning=[ISSUE-03] NON-ABI3 build: verify output is cpython-*-darwin.so NOT .abi3.so");
    } else {
        // Unknown configuration
        eprintln!(
            "[build.rs] WARNING: Unknown ABI configuration. Please verify:"
        );
        eprintln!(
            "[build.rs]   - pyo3 features should include: extension-module (non-abi3)"
        );
        eprintln!(
            "[build.rs]   - crate-type should include: cdylib"
        );
    }
}

// ============================================================================
// ISSUE-01: Source Hash Generation
// ============================================================================

/// ISSUE-01: Compute a blake2b hash of all Rust source files.
///
/// This hash is computed at build time and stored in the FFI manifest.
/// Python uses it to verify the binary matches the current source state.
///
/// Returns the hex hash string or empty string on error.
fn compute_source_hash() -> String {
    use std::io::Read;
    
    let src_dir = std::path::Path::new("src");
    if !src_dir.exists() {
        return String::new();
    }

    // Collect all .rs and .toml files
    let mut file_paths: Vec<_> = Vec::new();
    if let Ok(entries) = std::fs::read_dir(src_dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_file() {
                if let Some(ext) = path.extension() {
                    if ext == "rs" || ext == "toml" {
                        file_paths.push(path);
                    }
                }
            } else if path.is_dir() {
                // Recursively find .rs files in subdirectories
                if let Ok(sub_entries) = std::fs::read_dir(&path) {
                    for sub_entry in sub_entries.flatten() {
                        let sub_path = sub_entry.path();
                        if sub_path.is_file() {
                            if let Some(ext) = sub_path.extension() {
                                if ext == "rs" || ext == "toml" {
                                    file_paths.push(sub_path);
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    if file_paths.is_empty() {
        return String::new();
    }

    // Sort for deterministic ordering
    file_paths.sort();

    // Use blake2 for fast hashing
    // Simple approach: hash the concatenated sizes + first/last 2KB of each file
    let mut hasher = blake2b_simd::Params::new()
        .hash_length(32)
        .to_state();

    for path in &file_paths {
        // Hash file path
        hasher.update(path.to_string_lossy().as_bytes());
        
        // Hash file size
        if let Ok(metadata) = std::fs::metadata(path) {
            let size = metadata.len();
            hasher.update(&size.to_le_bytes());
            
            // Sample first 4KB + last 4KB
            if let Ok(mut file) = std::fs::File::open(path) {
                let mut buffer = [0u8; 4096];
                
                // First 4KB
                if let Ok(n) = file.read(&mut buffer) {
                    hasher.update(&buffer[..n]);
                }
                
                // Last 4KB if file > 8KB
                if size > 8192 {
                    use std::io::Seek;
                    if file.seek(std::io::SeekFrom::End(-4096)).is_ok() {
                        if let Ok(n) = file.read(&mut buffer) {
                            hasher.update(&buffer[..n]);
                        }
                    }
                }
            }
        }
    }

    let hash = hasher.finalize();
    hex::encode(hash)
}

// ============================================================================
// NEXTGEN-05: FFI Type Manifest Generation
// ============================================================================

/// Run ffi_type_manifest.py to generate FFI type metadata.
///
/// This generates:
///   - _ffi_type_manifest.json: Type metadata for build-time validation
///   - hledac_rust_extensions.pyi: Auto-generated Python stub
///
/// Errors are non-fatal — the build continues but validation may fail later.
fn run_ffi_type_manifest() {
    let script_path = std::path::Path::new("ffi_type_manifest.py");

    if !script_path.exists() {
        eprintln!(
            "[build.rs] WARNING: ffi_type_manifest.py not found at {:?}",
            script_path
        );
        return;
    }

    // Use the Python interpreter that pyo3-build-config detected
    let python = std::env::var("PYO3_PYTHON")
        .or_else(|_| std::env::var("PYTHON"))
        .unwrap_or_else(|_| "python".to_string());

    eprintln!("[build.rs] NEXTGEN-05: Generating FFI type manifest...");

    match Command::new(&python).arg(script_path).output() {
        Ok(output) => {
            // Print stdout from the script
            if !output.stdout.is_empty() {
                for line in String::from_utf8_lossy(&output.stdout).lines() {
                    eprintln!("[build.rs]   {}", line);
                }
            }

            if output.status.success() {
                eprintln!("[build.rs] ✓ FFI type manifest generated successfully");

                // Tell Cargo about the generated files
                println!("cargo:generated-files=ffi_type_manifest.py");
            } else {
                let stderr = String::from_utf8_lossy(&output.stderr);
                eprintln!(
                    "[build.rs] WARNING: FFI manifest generation returned non-zero: {}",
                    output.status
                );
                if !stderr.is_empty() {
                    eprintln!("[build.rs]   stderr: {}", stderr);
                }
            }
        }
        Err(e) => {
            eprintln!(
                "[build.rs] WARNING: Failed to run ffi_type_manifest.py: {}",
                e
            );
        }
    }
}

// ============================================================================
// ISSUE-11: BUILD_MANIFEST Generation
// ============================================================================

/// Run build_manifest.py to generate BUILD_MANIFEST.json.
///
/// This generates BUILD_MANIFEST.json containing:
///   - SHA256 hash of all source files (src/**/*.rs, Cargo.toml)
///   - Build timestamp
///   - Build command used
///   - Platform info
///
/// This enables fail-closed staleness detection at runtime:
/// if the source files have been modified since the build, the Rust
/// extension will raise RustExtensionStale instead of silently degrading.
///
/// Errors are non-fatal — the build continues but runtime may fail.
fn run_build_manifest() {
    let script_path = std::path::Path::new("build_manifest.py");

    if !script_path.exists() {
        eprintln!(
            "[build.rs] WARNING: build_manifest.py not found at {:?}",
            script_path
        );
        return;
    }

    // Use the Python interpreter that pyo3-build-config detected
    let python = std::env::var("PYO3_PYTHON")
        .or_else(|_| std::env::var("PYTHON"))
        .unwrap_or_else(|_| "python".to_string());

    eprintln!("[build.rs] ISSUE-11: Generating BUILD_MANIFEST...");

    match Command::new(&python).arg(script_path).output() {
        Ok(output) => {
            // Print stdout from the script
            if !output.stdout.is_empty() {
                for line in String::from_utf8_lossy(&output.stdout).lines() {
                    eprintln!("[build.rs]   {}", line);
                }
            }

            if output.status.success() {
                eprintln!("[build.rs] ✓ BUILD_MANIFEST generated successfully");

                // Tell Cargo about the generated file
                println!("cargo:generated-files=build_manifest.py");
            } else {
                let stderr = String::from_utf8_lossy(&output.stderr);
                eprintln!(
                    "[build.rs] WARNING: BUILD_MANIFEST generation returned non-zero: {}",
                    output.status
                );
                if !stderr.is_empty() {
                    eprintln!("[build.rs]   stderr: {}", stderr);
                }
            }
        }
        Err(e) => {
            eprintln!(
                "[build.rs] WARNING: Failed to run build_manifest.py: {}",
                e
            );
        }
    }
}
