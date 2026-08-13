# NEXTGEN-05: UniFFI Alternative Path

## Overview

This document describes the **UniFFI alternative path** for new PyO3 modules in the `hledac_rust_extensions` project.

UniFFI is Mozilla's toolkit for generating FFI bindings from a simple interface definition language (IDL). It provides **automatic** type-safe bindings for Python (and other languages) from Rust code with **zero manual stub maintenance**.

## When to Use UniFFI vs. PyO3 Codegen

| Scenario | Recommendation |
|----------|---------------|
| New modules with simple APIs | **UniFFI** — auto-generates .pyi + Rust scaffolding |
| Complex modules with custom memory management | PyO3 — full control needed |
| Modules requiring pyo3-async-runtimes | PyO3 — UniFFI doesn't support async |
| Quick prototyping | UniFFI — faster iteration |
| Performance-critical hot paths | PyO3 — manual optimization possible |
| Existing PyO3 modules | Keep PyO3 — migration cost too high |

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    UniFFI Generation Flow                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  1. Define interface in *.udl (UniFFI IDL)                          │
│     ┌──────────────────────────────────────────┐                    │
│     │ namespace hledac_example {               │                    │
│     │   [Throws=Error]                         │                    │
│     │   string compute_hash(string input);      │                    │
│     │ };                                       │                    │
│     └──────────────────────────────────────────┘                    │
│                        │                                           │
│                        ▼                                           │
│  2. Run uniffi-bindgen generate                                    │
│     $ uniffi-bindgen generate src/example.udl \                    │
│         --language python                                           │
│                                                                     │
│                        │                                           │
│                        ▼                                           │
│  3. Generated outputs:                                              │
│     ┌──────────────────────────────────────────┐                    │
│     │ • example.pyi          (Python stub)     │ ◄── Auto-updated  │
│     │ • example.pyi          (Python wrapper)  │ ◄── Auto-updated  │
│     │ • example_component.rs  (Rust scaffolding)│ ◄── Auto-updated  │
│     └──────────────────────────────────────────┘                    │
│                                                                     │
│  4. Implement business logic in Rust                                │
│     ┌──────────────────────────────────────────┐                    │
│     │ fn compute_hash(input: String)           │                    │
│     │     -> Result<String, Error> { ... }    │                    │
│     └──────────────────────────────────────────┘                    │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## Migration Guide: Adding a New Module with UniFFI

### Step 1: Create the .udl Interface File

Create `src/example.udl`:

```udl
namespace hledac_example {
    // Basic function
    string compute_hash(string input);

    // Function with error handling
    [Throws=ExampleError]
    u64 parse_version(string version_str);

    // Record (struct-like) definition
    record ExampleResult {
        value: string;
        confidence: f64;
        tags: list<string>;
    };

    // Function returning a record
    ExampleResult analyze(string input);
};

// Custom error type
[Error]
enum ExampleError {
    InvalidInput,
    ProcessingFailed,
    OutOfMemory;
}
```

### Step 2: Generate Bindings

```bash
# Install uniffi-bindgen if not already installed
pip install uniffi-bindgen

# Generate Python bindings
uniffi-bindgen generate src/example.udl \
    --language python \
    --out-dir rust_extensions/hledac_rust_extensions

# This creates:
#   - example.pyi          (type stub for mypy/pyright)
#   - example.py           (Python wrapper)
#   - example_component.rs (Rust scaffolding)
```

### Step 3: Implement the Rust Code

Create `src/example_impl.rs`:

```rust
use uniffi::*;

#[derive(Debug, thiserror::Error)]
pub enum ExampleError {
    #[error("Invalid input: {0}")]
    InvalidInput(String),
    #[error("Processing failed")]
    ProcessingFailed,
    #[error("Out of memory")]
    OutOfMemory,
}

#[derive(Debug, Clone, uniffi::Record)]
pub struct ExampleResult {
    pub value: String,
    pub confidence: f64,
    pub tags: Vec<String>,
}

#[uniffi::export]
pub fn compute_hash(input: String) -> String {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};
    
    let mut hasher = DefaultHasher::new();
    input.hash(&mut hasher);
    format!("{:x}", hasher.finish())
}

#[uniffi::export]
pub fn parse_version(version_str: &str) -> Result<u64, ExampleError> {
    let parts: Vec<u64> = version_str
        .split('.')
        .map(|s| s.parse().map_err(|_| ExampleError::InvalidInput(s.to_string())))
        .collect::<Result<Vec<_>, _>>()?;
    
    if parts.len() != 3 {
        return Err(ExampleError::InvalidInput(version_str.to_string()));
    }
    
    Ok((parts[0] << 16) | (parts[1] << 8) | parts[2])
}

#[uniffi::export]
pub fn analyze(input: &str) -> ExampleResult {
    ExampleResult {
        value: input.to_uppercase(),
        confidence: 0.95,
        tags: vec!["processed".to_string()],
    }
}
```

### Step 4: Integrate with Cargo.toml

```toml
[dependencies]
# UniFFI core (add this)
uniffi = { version = "0.29", features = ["testing"] }

# Your module
example-crate = { path = "src/example", version = "0.1" }
```

### Step 5: Build and Test

```bash
# Generate bindings
cd rust_extensions
uniffi-bindgen generate src/example.udl --language python

# Build with maturin
maturin develop

# Test
python -c "from hledac_rust_extensions import example; print(example.compute_hash('test'))"
```

## UniFFI vs. PyO3 Comparison

| Feature | UniFFI | PyO3 (NEXTGEN-05) |
|---------|--------|-------------------|
| Type stub generation | ✓ Automatic | ✓ Automatic (codegen) |
| Error handling | ✓ Typed errors | ✓ Manual |
| Async support | ✗ Not yet | ✓ Via pyo3-async-runtimes |
| Memory management | ✓ Automatic Arc/Rc | ✓ Manual Drop |
| Complex types | Limited | Full Rust ecosystem |
| Performance | Good | Excellent (no overhead) |
| Learning curve | Low | Medium |
| Debugging | Easier | More complex |

## Mixing UniFFI and PyO3

For projects that need both:

```
rust_extensions/
├── src/
│   ├── lib.rs                 # PyO3 module (main)
│   ├── pyo3_module.rs         # PyO3 classes
│   ├── example.udl           # UniFFI interface
│   └── example_impl.rs       # UniFFI implementation
├── hledac_rust_extensions/
│   ├── __init__.py           # Imports both PyO3 and UniFFI
│   ├── example.pyi           # UniFFI-generated
│   └── example.py            # UniFFI-generated
└── pyproject.toml
```

In `__init__.py`:

```python
"""hledac_rust_extensions - Unified FFI module."""

# PyO3 extension (native .so)
from . import _hledac_rust_extensions  # type: ignore[attr-defined]

# UniFFI bindings (pure Python)
from . import example as _example

# Expose both under unified API
__all__ = [
    # PyO3 exports
    "__version__",
    "AhoCorasickMatcher",
    "BloomFilter",
    # UniFFI exports
    "compute_hash",
    "analyze",
]
```

## Best Practices

1. **Use UniFFI for new, simple modules** — faster development, automatic stubs
2. **Keep PyO3 for performance-critical code** — full control over memory/layout
3. **Document the boundary** — clear which bindings use which approach
4. **Test both paths** — UniFFI has different failure modes than PyO3
5. **Version UniFFI carefully** — breaking changes affect Python automatically

## References

- [UniFFI Documentation](https://mozilla.github.io/uniffi-rs/)
- [UniFFI Python Bindings](https://mozilla.github.io/uniffi-rs/python/overview.html)
- [PyO3 Book](https://pyo3.rs/)
- [NEXTGEN-05 Implementation](./NEXTGEN_05_IMPLEMENTATION.md)
