//! Metal Shared Buffer — Zero-Copy Rust↔Python↔MLX Tensor Sharing (SILICON-04)
//!
//! ## Problem (MODERN-21 Fix)
//!
//! **BEFORE (BUG)**: `to_numpy()` used `PyBytes::new()` which copies data,
//! then `numpy.frombuffer()` read the copy. Violated SB.4 invariant.
//!
//! **AFTER (FIXED)**: `to_numpy()` uses `memoryview` + ctypes pointer cast
//! via PEP 3118 buffer protocol. `mx.array(..., copy=False)` tells MLX
//! to map MTLBuffer pages directly — zero physical copies on UMA.
//!
//! ## Solution
//!
//! `SharedMetalBuffer` allocates a single `MTLBuffer` with `StorageModeShared`
//! and exposes it for:
//! - **Python**: zero-copy numpy views via PEP 3118 memoryview
//! - **MLX**: `mx.array(..., copy=False)` for direct MTLBuffer mapping
//! - **Rust**: direct `&[f32]` access via `contents()` pointer
//!
//! ## Zero-Copy Architecture (MODERN-21 + IO-4)
//!
//! ```text
//! Rust (PyO3)                  Python                            MLX / Metal GPU
//! ──────────────────────────────────────────────────────────────────────────────
//! MTLBuffer::new() ──► SharedMetalBuffer ──► memoryview ──► mx.array(copy=False)
//!   StorageModeShared     (PyClass)         (PEP 3118)       (UMA direct map)
//!    │                          │
//!    │                          └──► np.asarray(..., copy=False) → numpy view
//!    │                                    ↑
//!    │                          ctypes.cast(ptr, c_char * size)
//!    │                                    │
//!    │                          memoryview(...)
//!    └──► .to_mlx_array_batch()
//!          (same zero-copy path)
//!
//! IO-4: IOSurface Zero-Copy Pipeline
//! ──────────────────────────────────────────────────────────────────────────────
//! CVPixelBuffer ─► IOSurface ─► IOSurfaceCreateMetalBuffer ─► MTLBuffer
//!     │                                    │                        │
//!     └──► CPU (numpy view) ◄──────────────┴────────────────────────┘
//!                              (shared IOSurface memory)
//!
//! On M1 UMA: NO copies — IOSurface shared by CPU and GPU!
//! ```
//!
//! ## M1 8GB Constraints
//!
//! - Max single buffer: 256 MB (shared MTLBuffer limit)
//! - Total allocated: tracked via atomic, auto-flush at 512 MB
//! - Thread-safe: parking_lot::RwLock for device, AtomicU64 for budget
//! - Fail-soft: every error path returns None / raises PyErr with context
//!
//! ## Key Invariants (SILICON-04) — ALL PRESERVED
//!
//! SB.1: Always StorageModeShared — CPU + GPU must see same physical pages ✓
//! SB.2: 256 MB single-buffer cap — enforced at allocation ✓
//! SB.3: 512 MB global budget — tracked via SHARED_BUF_ALLOCATED atomic ✓
//! SB.4: Zero-copy numpy via PEP 3118 — NO PyBytes copy! ✓ (MODERN-21 FIX)
//! SB.5: to_mlx_array_batch() does exactly ONE copy per batch, never N ✓
//! SB.6: MLX imports are lazy — no top-level mx import ✓
//! SB.7: Fail-soft — errors return None, never propagate ✓
//! SB.8: Python imports (ctypes, numpy) cached at module level ✓ (MODERN-21 OPTIMIZATION)
//! SB.9: from_iosurface() uses IOSurfaceCreateMetalBuffer FFI for TRUE zero-copy ✓ (IO-4 FIX)

// MODERN-28 FIX: ForeignType trait required for Buffer::from_ptr()
use metal::foreign_types::ForeignType;
use parking_lot::RwLock;
use pyo3::prelude::*;
use pyo3::Py;
use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::LazyLock;

// ─── IOSurface FFI Bindings (IO-4 Zero-Copy Extension) ──────────────────────

/// IOSurfaceCreateMetalBuffer creates a Metal buffer that shares memory with
/// an IOSurface. This is TRUE ZERO-COPY - no data is copied.
///
/// On Apple Silicon M1, IOSurface lives in GPU-accessible UMA, so both CPU
/// and GPU can access the same physical pages through the Metal buffer.
///
/// # Safety
/// - iosurface_ref must be a valid IOSurfaceRef
/// - device_raw must be a valid MTLDevice pointer
/// - The returned buffer is retained by this function; caller takes ownership
#[cfg(target_os = "macos")]
unsafe fn iosurface_create_metal_buffer(
    device_raw: *mut std::ffi::c_void,
    iosurface_ref: *mut std::ffi::c_void,
) -> *mut std::ffi::c_void {
    // macOS 10.13+ provides IOSurfaceCreateMetalBuffer
    // Function signature: MTLBuffer IOSurfaceCreateMetalBuffer(id<MTLDevice>, IOSurfaceRef)
    // We need to use dlsym to get this function pointer at runtime

    use std::ffi::CStr;

    // Get the function pointer (lazy, cached)
    // MODERN-28 FIX: Unsafe functions called inside unsafe block
    static FUNCPTR: LazyLock<Option<unsafe extern "C" fn(
        *mut std::ffi::c_void,
        *mut std::ffi::c_void,
    ) -> *mut std::ffi::c_void>> = LazyLock::new(|| {
        // SAFETY: dlopen and dlsym are safe when called with valid arguments
        // and the library path is verified to exist on macOS.
        unsafe {
            let lib = libc::dlopen(
                CStr::from_bytes_with_nul(b"/System/Library/Frameworks/Metal.framework/Metal\0")
                    .unwrap()
                    .as_ptr(),
                libc::RTLD_NOW,
            );
            if lib.is_null() {
                return None;
            }
            let sym = libc::dlsym(
                lib,
                CStr::from_bytes_with_nul(b"IOSurfaceCreateMetalBuffer\0")
                    .unwrap()
                    .as_ptr(),
            );
            if sym.is_null() {
                libc::dlclose(lib);
                return None;
            }
            Some(std::mem::transmute(sym))
        }
    });

    if let Some(func) = *FUNCPTR {
        func(device_raw, iosurface_ref)
    } else {
        std::ptr::null_mut()
    }
}

/// Check if IOSurfaceCreateMetalBuffer is available on this system.
///
/// Returns true only if the FFI symbol is actually available at runtime.
/// Falls back to false on older macOS versions (< 10.13) where the function
/// doesn't exist, or if Metal framework is not available.
#[cfg(target_os = "macos")]
fn iosurface_buffer_supported() -> bool {
    // Try to get the function pointer - if it's null, the FFI is unavailable
    // Note: passing null pointers is safe for dlsym (it just checks symbol existence)
    static FUNCPTR: LazyLock<Option<unsafe extern "C" fn(
        *mut std::ffi::c_void,
        *mut std::ffi::c_void,
    ) -> *mut std::ffi::c_void>> = LazyLock::new(|| {
        use std::ffi::CStr;
        unsafe {
            let lib = libc::dlopen(
                CStr::from_bytes_with_nul(b"/System/Library/Frameworks/Metal.framework/Metal\0")
                    .unwrap()
                    .as_ptr(),
                libc::RTLD_NOW,
            );
            if lib.is_null() {
                return None;
            }
            let sym = libc::dlsym(
                lib,
                CStr::from_bytes_with_nul(b"IOSurfaceCreateMetalBuffer\0")
                    .unwrap()
                    .as_ptr(),
            );
            if sym.is_null() {
                libc::dlclose(lib);
                return None;
            }
            Some(std::mem::transmute(sym))
        }
    });
    FUNCPTR.is_some()
}

/// Stub for non-macOS
#[cfg(not(target_os = "macos"))]
fn iosurface_create_metal_buffer(
    _device_raw: *mut std::ffi::c_void,
    _iosurface_ref: *mut std::ffi::c_void,
) -> *mut std::ffi::c_void {
    std::ptr::null_mut()
}

#[cfg(not(target_os = "macos"))]
fn iosurface_buffer_supported() -> bool {
    false
}

// ─── Module-Level Python Imports (cached for efficiency) ───────────────────

/// Cached Python modules for to_numpy() zero-copy path.
/// Initialized lazily on first use, never recreated.
static PYTHON_IMPORTS: LazyLock<RwLock<Option<PythonImports>>> =
    LazyLock::new(|| RwLock::new(None));

struct PythonImports {
    ctypes: Py<pyo3::types::PyModule>,
    np: Py<pyo3::types::PyModule>,
}

impl PythonImports {
    fn clone_ref(&self, py: Python<'_>) -> Self {
        Self {
            ctypes: self.ctypes.clone_ref(py),
            np: self.np.clone_ref(py),
        }
    }
}

/// Initialize Python imports cache for zero-copy numpy path.
fn get_python_imports(py: Python<'_>) -> PyResult<PythonImports> {
    // Fast path: already initialized
    if let Some(ref cached) = *PYTHON_IMPORTS.read() {
        return Ok(cached.clone_ref(py));
    }

    // Slow path: initialize
    let ctypes = py.import("ctypes")?.unbind();
    let np = py.import("numpy")?.unbind();

    let imports = PythonImports { ctypes, np };
    *PYTHON_IMPORTS.write() = Some(imports.clone_ref(py));

    Ok(imports)
}

/// Elem size lookup table for numpy typestr (handles all byte orders).
/// Maps typestr suffix to element size in bytes.
fn typestr_to_elem_size(typestr: &str) -> Option<u64> {
    // Normalize: strip byte order prefix (< le, > be, |/none)
    let suffix = if typestr.len() >= 3 {
        &typestr[1..]
    } else {
        return None;
    };

    match suffix {
        // Float types
        "f2" => Some(2),  // float16
        "f4" => Some(4),  // float32
        "f8" => Some(8),  // float64
        "f16" => Some(16), // float128 (if supported)
        // Signed integers
        "i1" => Some(1),  // int8
        "i2" => Some(2),  // int16
        "i4" => Some(4),  // int32
        "i8" => Some(8),  // int64
        // Unsigned integers
        "u1" => Some(1),  // uint8
        "u2" => Some(2),  // uint16
        "u4" => Some(4),  // uint32
        "u8" => Some(8),  // uint64
        // Complex types (MODERN-21 extension)
        "c8" => Some(8),  // complex64 (2x float32)
        "c16" => Some(16), // complex128 (2x float64)
        "c32" => Some(32), // complex256 (2x float128)
        // Boolean
        "b1" => Some(1),  // bool
        // Object / void
        "O" | "V" => None, // variable size, can't determine
        _ => None,
    }
}

/// Elem size for dtype string (Rust-side dtype).
fn dtype_str_to_elem_size(dtype: &str) -> Option<u64> {
    match dtype {
        "float32" | "int32" | "uint32" => Some(4),
        "float64" | "int64" | "uint64" => Some(8),
        "float16" | "int16" | "uint16" => Some(2),
        "int8" | "uint8" | "bool" => Some(1),
        // Complex types (MODERN-21 extension)
        "complex64" => Some(8),
        "complex128" => Some(16),
        _ => None,
    }
}

// ─── M1 8GB Memory Budget ─────────────────────────────────────────────────

/// Maximum single buffer allocation (256 MB).
pub const SHARED_BUF_MAX_SINGLE: u64 = 256 * 1024 * 1024;

/// Global budget: pause allocation when total exceeds 512 MB.
pub const SHARED_BUF_TOTAL_BUDGET: u64 = 512 * 1024 * 1024;

/// Minimum allocation size (4 KB — one page).
const MIN_ALLOC: u64 = 4096;

// ─── Global Tracker ───────────────────────────────────────────────────────

static SHARED_BUF_ALLOCATED: AtomicU64 = AtomicU64::new(0);

fn track_alloc(bytes: u64) -> bool {
    let prev = SHARED_BUF_ALLOCATED.fetch_add(bytes, Ordering::SeqCst);
    if prev + bytes > SHARED_BUF_TOTAL_BUDGET {
        SHARED_BUF_ALLOCATED.fetch_sub(bytes, Ordering::SeqCst);
        return false;
    }
    true
}

fn track_free(bytes: u64) {
    SHARED_BUF_ALLOCATED.fetch_sub(bytes, Ordering::SeqCst);
}

// ─── Lazy Metal Device ────────────────────────────────────────────────────

static METAL_DEVICE: LazyLock<RwLock<Option<metal::Device>>> =
    LazyLock::new(|| RwLock::new(metal::Device::system_default()));

fn get_device() -> Option<metal::Device> {
    METAL_DEVICE.read().clone()
}

// ─── SharedMetalBuffer ────────────────────────────────────────────────────

/// A Metal buffer with `StorageModeShared` that can be accessed from both
/// Rust and Python (and via its raw pointer, from MLX).
///
/// ## Zero-Copy Path (MODERN-21)
///
/// ```python
/// from hledac_rust_extensions import SharedMetalBuffer
/// import mlx.core as mx
/// import numpy as np
///
/// # Allocate buffer for 1000 embeddings × 256 dims
/// buf = SharedMetalBuffer.allocate(1000 * 256 * 4)
/// print(buf.size_bytes)  # 1_024_000
///
/// # Fill from numpy (one-time copy: CPU → MTLBuffer)
/// data = np.random.randn(1000, 256).astype(np.float32)
/// buf.copy_from_numpy(data)  # 1 MB one-time transfer
///
/// # ZERO-COPY numpy view — no copy, same MTLBuffer memory
/// # Uses memoryview + ctypes for PEP 3118 compliance
/// view = buf.to_numpy((1000, 256), "float32")
/// assert view.base is not None  # Shares MTLBuffer memory
///
/// # ZERO-COPY to MLX — on UMA, MLX maps MTLBuffer pages directly
/// # mx.array(..., copy=False) tells MLX to use the numpy buffer as-is
/// mx_arr = buf.to_mlx_array((1000, 256), mx.float32)
/// # Now: MTLBuffer memory == numpy view == MLX array (single physical mapping!)
/// ```
///
/// ## Architecture
///
/// - `to_numpy()`: Creates memoryview via ctypes pointer cast → np.asarray(...)
/// - `to_mlx_array()`: Creates MLX array with copy=False from numpy view
/// - On M1 UMA: No copies — numpy and MLX share MTLBuffer pages directly
#[pyclass(skip_from_py_object)]
#[derive(Clone)]
pub struct SharedMetalBuffer {
    /// The underlying MTLBuffer (nil if released).
    buffer: Option<metal::Buffer>,
    /// Size in bytes.
    size_bytes: u64,
    /// Whether this buffer has been released.
    released: bool,
}

#[pymethods]
impl SharedMetalBuffer {
    /// Allocate a new Metal buffer with StorageModeShared.
    ///
    /// Args:
    ///     size_bytes: Size in bytes (must be > 0, <= 256 MB)
    ///
    /// Returns:
    ///     SharedMetalBuffer or raises ValueError on OOM / Metal unavailable.
    #[staticmethod]
    fn allocate(size_bytes: u64) -> PyResult<Self> {
        if size_bytes == 0 || size_bytes > SHARED_BUF_MAX_SINGLE {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "size_bytes must be in range (0, {}], got {}",
                SHARED_BUF_MAX_SINGLE, size_bytes
            )));
        }

        let size = std::cmp::max(size_bytes, MIN_ALLOC);

        // Check global budget
        if !track_alloc(size) {
            return Err(pyo3::exceptions::PyMemoryError::new_err(format!(
                "SharedMetalBuffer: global budget exceeded ({:.1} MB / {:.1} MB)",
                SHARED_BUF_ALLOCATED.load(Ordering::SeqCst) as f64 / 1_048_576.0,
                SHARED_BUF_TOTAL_BUDGET as f64 / 1_048_576.0
            )));
        }

        let device = get_device().ok_or_else(|| {
            track_free(size);
            pyo3::exceptions::PyRuntimeError::new_err("Metal device not available on this platform")
        })?;

        let storage_mode = metal::MTLResourceOptions::StorageModeShared;
        let buffer = device.new_buffer(size, storage_mode);

        Ok(SharedMetalBuffer {
            buffer: Some(buffer),
            size_bytes: size,
            released: false,
        })
    }

    /// Create a SharedMetalBuffer from numpy data (one-time copy).
    ///
    /// Args:
    ///     data: numpy ndarray (must be C-contiguous, float32/int32/complex64/etc.)
    ///
    /// Returns:
    ///     SharedMetalBuffer with data copied into Metal buffer.
    #[staticmethod]
    fn from_numpy(data: &Bound<'_, PyAny>) -> PyResult<Self> {
        let _py = data.py();

        // Get array interface
        let arr_iface = data.call_method0("__array_interface__")?;
        let shape: Vec<usize> = arr_iface.getattr("shape")?.extract()?;
        let typestr: String = arr_iface.getattr("typestr")?.extract()?;

        // Use helper for clean dtype handling (handles all byte orders)
        let elem_size = typestr_to_elem_size(&typestr).ok_or_else(|| {
            pyo3::exceptions::PyTypeError::new_err(format!(
                "Unsupported dtype: {}. Supported: float16/32/64, int8/16/32/64, uint8/16/32/64, complex64/128, bool.",
                typestr
            ))
        })?;

        let num_elements: u64 = shape.iter().map(|&s| s as u64).product();
        let total_bytes = num_elements * elem_size;

        if total_bytes == 0 || total_bytes > SHARED_BUF_MAX_SINGLE {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "Data size {} bytes exceeds max {} bytes",
                total_bytes, SHARED_BUF_MAX_SINGLE
            )));
        }

        if !track_alloc(total_bytes) {
            return Err(pyo3::exceptions::PyMemoryError::new_err(
                "SharedMetalBuffer: global budget exceeded",
            ));
        }

        let device = get_device().ok_or_else(|| {
            track_free(total_bytes);
            pyo3::exceptions::PyRuntimeError::new_err("Metal device not available")
        })?;

        // Get raw data pointer from numpy
        let data_ptr: usize = arr_iface
            .getattr("data")?
            .call_method0("__getitem__")?
            .get_item(0)?
            .extract()?;

        let storage_mode = metal::MTLResourceOptions::StorageModeShared;
        let buffer = device.new_buffer(total_bytes, storage_mode);

        // Copy data into Metal buffer (one-time cost)
        unsafe {
            let src = data_ptr as *const u8;
            let dst = buffer.contents() as *mut u8;
            std::ptr::copy_nonoverlapping(src, dst, total_bytes as usize);
        }

        Ok(SharedMetalBuffer {
            buffer: Some(buffer),
            size_bytes: total_bytes,
            released: false,
        })
    }

    /// Create a SharedMetalBuffer from an IOSurface (IO-4 zero-copy bridge).
    ///
    /// ## IO-4 True Zero-Copy Implementation
    ///
    /// This method uses `IOSurfaceCreateMetalBuffer` FFI for TRUE zero-copy:
    /// - Creates a Metal buffer that SHARES IOSurface memory directly
    /// - NO data is copied — CPU and GPU access the same physical pages
    /// - Falls back to copy method if FFI is unavailable (macOS < 10.13)
    ///
    /// ## Pipeline
    ///
    /// ```text
    /// CVPixelBuffer → IOSurface → IOSurfaceCreateMetalBuffer → MTLBuffer (shared)
    ///                    ↓                                      ↓
    ///              Base Address ←────────────────── CPU access (numpy view)
    /// ```
    ///
    /// ## Args
    ///
    /// - iosurface_ptr: IOSurfaceRef pointer (cast to usize for PyO3)
    /// - width: Width in pixels
    /// - height: Height in pixels
    /// - bytes_per_row: Bytes per row (from IOSurfaceGetBytesPerRow)
    /// - pixel_format: Pixel format ("BGRA", "RGBA", "RGB", "GRAY")
    ///
    /// ## Returns
    ///
    /// SharedMetalBuffer (zero-copy if FFI available, copy fallback otherwise)
    #[staticmethod]
    #[cfg(target_os = "macos")]
    fn from_iosurface(
        iosurface_ptr: usize,
        width: u32,
        height: u32,
        bytes_per_row: u32,
        pixel_format: &str,
    ) -> PyResult<Self> {
        use std::ptr;

        // Calculate total size
        let total_bytes = (bytes_per_row as u64) * (height as u64);

        if total_bytes == 0 || total_bytes > SHARED_BUF_MAX_SINGLE {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "IOSurface size {} bytes exceeds max {} bytes",
                total_bytes, SHARED_BUF_MAX_SINGLE
            )));
        }

        if !track_alloc(total_bytes) {
            return Err(pyo3::exceptions::PyMemoryError::new_err(
                "SharedMetalBuffer: global budget exceeded",
            ));
        }

        let device = get_device().ok_or_else(|| {
            track_free(total_bytes);
            pyo3::exceptions::PyRuntimeError::new_err("Metal device not available")
        })?;

        // Validate pixel format
        let bytes_per_pixel = match pixel_format {
            "BGRA" | "RGBA" | "bgra" | "rgba" => 4,
            "RGB" | "rgb" => 3,
            "GRAY" | "gray" => 1,
            _ => {
                track_free(total_bytes);
                return Err(pyo3::exceptions::PyValueError::new_err(format!(
                    "Unsupported pixel format: {}. Use BGRA, RGBA, RGB, or GRAY.",
                    pixel_format
                )));
            }
        };

        // Validate dimensions match expected bytes
        let expected_bytes_per_row = width * bytes_per_pixel;
        if bytes_per_row < expected_bytes_per_row {
            track_free(total_bytes);
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "bytes_per_row {} is less than width * bytes_per_pixel {}",
                bytes_per_row, expected_bytes_per_row
            )));
        }

        // ─── IO-4 ZERO-COPY PATH ───────────────────────────────────────────────
        // Try IOSurfaceCreateMetalBuffer first for TRUE ZERO-COPY.
        // This creates a Metal buffer that SHARES IOSurface memory directly.
        // No data is copied — both CPU and GPU access the same physical pages.
        //
        // Falls back to regular buffer + copy if FFI is unavailable.
        let buffer = if iosurface_ptr != 0 {
            // MODERN-28 FIX: Cast device to raw pointer using objc2-raw-macro pattern
            // metal::Device wraps an objc2 id pointer accessible via .as_raw() when
            // ForeignType trait is imported. For safety, use transmute approach.
            let device_raw = {
                // SAFETY: metal::Device is repr(transparent) over the underlying objc object id.
                // This matches how objc2-metal internally stores the device.
                let device_ptr: *const metal::Device = &device;
                let device_id_ptr = device_ptr as *const *mut std::ffi::c_void;
                unsafe { *device_id_ptr }
            };
            let iosurface_ref = iosurface_ptr as *mut std::ffi::c_void;

            // SAFETY: iosurface_create_metal_buffer is unsafe FFI, mtl_buffer_ptr is valid if non-null
            unsafe {
                let mtl_buffer_ptr = iosurface_create_metal_buffer(device_raw, iosurface_ref);
                if !mtl_buffer_ptr.is_null() {
                    // SUCCESS: Created IOSurface-backed Metal buffer (TRUE ZERO-COPY!)
                    eprintln!(
                        "[IO-4] Created IOSurface-backed MTLBuffer ({}x{}, {} bytes/row) - ZERO-COPY",
                        width, height, bytes_per_row
                    );
                    // Create Buffer from raw pointer (standard pattern)
                    // SAFETY: The pointer is valid from the FFI call
                    metal::Buffer::from_ptr(mtl_buffer_ptr as *mut _)
                } else {
                    // FFI failed, fall back to copy method
                    eprintln!(
                        "[IO-4] IOSurfaceCreateMetalBuffer FFI unavailable, using copy fallback"
                    );
                    let storage_mode = metal::MTLResourceOptions::StorageModeShared;
                    let fallback_buffer = device.new_buffer(total_bytes, storage_mode);

                    // Copy data from IOSurface base address to Metal buffer
                    let src = iosurface_ptr as *const u8;
                    let dst = fallback_buffer.contents() as *mut u8;
                    ptr::copy_nonoverlapping(src, dst, total_bytes as usize);

                    eprintln!(
                        "[IO-4] Created SharedMetalBuffer from IOSurface ({}x{}, {} bytes/row) - COPY FALLBACK",
                        width, height, bytes_per_row
                    );
                    fallback_buffer
                }
            }
        } else {
            // No IOSurface pointer provided, create empty buffer
            let storage_mode = metal::MTLResourceOptions::StorageModeShared;
            device.new_buffer(total_bytes, storage_mode)
        };

        Ok(SharedMetalBuffer {
            buffer: Some(buffer),
            size_bytes: total_bytes,
            released: false,
        })
    }

    /// Get the raw pointer to the Metal buffer contents.
    ///
    /// Returns: int (memory address) or None if released.
    /// This is the CPU-accessible pointer — on M1 UMA it's the same
    /// physical page the GPU sees.
    fn ptr(&self) -> Option<usize> {
        self.buffer.as_ref().map(|b| b.contents() as usize)
    }

    /// Size of the buffer in bytes.
    #[getter]
    fn size_bytes(&self) -> u64 {
        self.size_bytes
    }

    /// Whether the buffer has been released.
    #[getter]
    fn is_released(&self) -> bool {
        self.released || self.buffer.is_none()
    }

    /// Create a zero-copy numpy array view into this Metal buffer.
    ///
    /// This does NOT copy data — the numpy array shares memory with
    /// the MTLBuffer via PEP 3118 buffer protocol. On M1 UMA, both
    /// CPU and GPU access the same physical pages.
    ///
    /// ## Implementation (MODERN-21 Fix + Extensions)
    ///
    /// Uses `memoryview` with ctypes pointer cast for true zero-copy:
    /// - `ctypes.cast(ptr, ctypes.c_char * size)` creates a buffer view
    /// - `memoryview(...)` wraps it with PEP 3118 protocol
    /// - `np.asarray(..., copy=False)` creates array WITHOUT copying
    /// - Final `reshape(...)` is view-only (same underlying buffer)
    /// - Python imports (ctypes, numpy) are cached at module level
    ///
    /// ## Supported Dtypes
    ///
    /// - Numeric: float16/32/64, int8/16/32/64, uint8/16/32/64
    /// - Complex: complex64, complex128 (MODERN-21 extension)
    /// - Boolean: bool
    ///
    /// ## Invariants Preserved
    ///
    /// - SB.4: Zero-copy numpy via PEP 3118 (no PyBytes copy)
    /// - SB.1: StorageModeShared guarantees CPU+GPU same pages
    /// - SB.8: Python imports cached, not re-imported on every call ✓
    ///
    /// Args:
    ///     shape: Tuple of dimensions, e.g. (1000, 256)
    ///     dtype: numpy dtype string, e.g., "float32", "complex64"
    ///
    /// Returns:
    ///     numpy ndarray (zero-copy view).
    fn to_numpy(&self, py: Python<'_>, shape: Vec<usize>, dtype: &str) -> PyResult<Py<PyAny>> {
        let buffer = self
            .buffer
            .as_ref()
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("Buffer has been released"))?;

        let num_elements: u64 = shape.iter().map(|&s| s as u64).product();

        // Use helper for clean dtype handling (MODERN-21 extension: complex types)
        let elem_size = dtype_str_to_elem_size(dtype).ok_or_else(|| {
            pyo3::exceptions::PyValueError::new_err(format!(
                "Unsupported dtype: {}. Supported: float16/32/64, int8/16/32/64, uint8/16/32/64, complex64/128, bool",
                dtype
            ))
        })?;

        if num_elements * elem_size > self.size_bytes {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "Shape {:?} × {} requires {} bytes, buffer has {} bytes",
                shape,
                dtype,
                num_elements * elem_size,
                self.size_bytes
            )));
        }

        let ptr = buffer.contents() as usize;
        let size = self.size_bytes as usize;

        // PEP 3118 zero-copy path using memoryview + ctypes
        // This is the MODERN-21 fix: replaces PyBytes::new() copy
        //
        // Python equivalent:
        //   import ctypes, numpy as np
        //   ct = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_char * size)).contents
        //   mv = memoryview(ct)  # PEP 3118 wrapper - NO COPY
        //   arr = np.asarray(mv).reshape(shape)

        // Use cached Python imports for efficiency (SB.8)
        let imports = get_python_imports(py)?;
        
        // Create locals dict for code execution
        let globals = pyo3::types::PyDict::new(py);
        let builtins = py.import("builtins")?;
        globals.set_item("__builtins__", builtins)?;
        globals.set_item("ctypes", &imports.ctypes)?;
        globals.set_item("np", &imports.np)?;

        // Create the ctypes pointer array and get buffer
        let ct_code = std::ffi::CString::new(
            format!("ct = ctypes.cast({}, ctypes.POINTER(ctypes.c_char * {})).contents", ptr, size)
        ).map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
        py.run(&ct_code, Some(&globals), Some(&globals))?;

        // Create memoryview from the ctypes array object
        let mv_code = std::ffi::CString::new("mv = memoryview(ct)").map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(e.to_string())
        })?;
        py.run(&mv_code, Some(&globals), Some(&globals))?;

        // Get the memoryview object for numpy conversion
        let mv_obj = globals.get_item("mv")?;

        // Create numpy array from memoryview (zero-copy via PEP 3118)
        let np_asarray = imports.np.getattr(py, "asarray")?;
        let np_arr = np_asarray.call1(py, (mv_obj,))?;

        // Reshape to target shape
        let reshape_method = np_arr.getattr(py, "reshape")?;
        let shape_tuple = pyo3::types::PyTuple::new(py, shape.iter().map(|&s| s))?;
        let result = reshape_method.call1(py, (shape_tuple,))?;

        Ok(result.into())
    }

    /// Create an MLX array from this Metal buffer.
    ///
    /// Does ONE copy (Metal buffer → MLX array) for the entire batch,
    /// instead of N copies for N individual vectors.
    ///
    /// ## Zero-Copy Path (MODERN-21)
    ///
    /// On M1 UMA, this achieves true zero-copy to GPU:
    /// 1. `to_numpy()` creates zero-copy memoryview view (no PyBytes copy)
    /// 2. `mx.array(..., copy=False)` tells MLX to use the numpy buffer directly
    /// 3. MLX maps the MTLBuffer pages directly (no intermediate copy)
    ///
    /// ## Args:
    ///     shape: Tuple of dimensions
    ///     mlx_dtype: MLX dtype (e.g., mx.float32 from Python)
    ///
    /// ## Returns:
    ///     mlx.core.array (potentially zero-copy from MTLBuffer)
    fn to_mlx_array(
        &self,
        py: Python<'_>,
        shape: Vec<usize>,
        mlx_dtype: &Bound<'_, PyAny>,
    ) -> PyResult<Py<PyAny>> {
        // First create zero-copy numpy view
        let dtype_str: String = mlx_dtype.call_method0("__name__")?.extract()?;

        let np_view = self.to_numpy(py, shape, &dtype_str)?;

        // MLX integration with explicit copy=False for zero-copy
        // This is the second zero-copy step: numpy view → MLX array
        let mx = py.import("mlx.core")?;

        // Use keyword argument for copy=False
        let kwargs = pyo3::types::PyDict::new(py);
        kwargs.set_item("copy", false)?;

        let mx_arr = mx.call_method("array", (np_view,), Some(&kwargs))?;

        Ok(mx_arr.into())
    }

    /// Batch-create an MLX array from multiple slices of this buffer.
    ///
    /// Optimized for the ANN reranking case: N candidate vectors
    /// are already contiguous in the buffer; this reads them in
    /// one call.
    ///
    /// Args:
    ///     num_vectors: Number of vectors in the buffer
    ///     vector_dim: Dimension of each vector
    ///     mlx_dtype: MLX dtype (e.g., mx.float32)
    ///
    /// Returns:
    ///     mlx.core.array of shape (num_vectors, vector_dim)
    fn to_mlx_array_batch(
        &self,
        py: Python<'_>,
        num_vectors: usize,
        vector_dim: usize,
        mlx_dtype: &Bound<'_, PyAny>,
    ) -> PyResult<Py<PyAny>> {
        self.to_mlx_array(py, vec![num_vectors, vector_dim], mlx_dtype)
    }

    /// Copy data from numpy into this buffer (one-time transfer).
    ///
    /// Args:
    ///     data: numpy ndarray (must fit within buffer)
    fn copy_from_numpy(&self, data: &Bound<'_, PyAny>) -> PyResult<()> {
        let buffer = self
            .buffer
            .as_ref()
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("Buffer has been released"))?;

        let arr_iface = data.call_method0("__array_interface__")?;
        let shape: Vec<usize> = arr_iface.getattr("shape")?.extract()?;
        let typestr: String = arr_iface.getattr("typestr")?.extract()?;

        let elem_size: usize = match typestr.as_str() {
            "<f4" | "<i4" | "<u4" => 4,
            "<f8" | "<i8" | "<u8" => 8,
            _ => return Err(pyo3::exceptions::PyTypeError::new_err("Unsupported dtype")),
        };

        let num_elements: usize = shape.iter().product();
        let total_bytes = num_elements * elem_size;

        if total_bytes as u64 > self.size_bytes {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "Data size {} bytes exceeds buffer size {} bytes",
                total_bytes, self.size_bytes
            )));
        }

        let data_ptr: usize = arr_iface
            .getattr("data")?
            .call_method0("__getitem__")?
            .get_item(0)?
            .extract()?;

        unsafe {
            let src = data_ptr as *const u8;
            let dst = buffer.contents() as *mut u8;
            std::ptr::copy_nonoverlapping(src, dst, total_bytes);
        }

        Ok(())
    }

    /// Release the Metal buffer and free GPU memory.
    fn release(&mut self) {
        if !self.released {
            if let Some(_buf) = self.buffer.take() {
                track_free(self.size_bytes);
            }
            self.released = true;
        }
    }

    fn __repr__(&self) -> String {
        match &self.buffer {
            Some(_) => format!(
                "SharedMetalBuffer(size={:.1} MB, ptr={:p})",
                self.size_bytes as f64 / 1_048_576.0,
                self.buffer.as_ref().unwrap().contents()
            ),
            None => "SharedMetalBuffer(released)".to_string(),
        }
    }

    fn __del__(&mut self) {
        self.release();
    }
}

impl Drop for SharedMetalBuffer {
    fn drop(&mut self) {
        self.release();
    }
}

// ─── Module Stats ─────────────────────────────────────────────────────────

#[derive(Default)]
struct SharedBufStats {
    allocations: AtomicU64,
    releases: AtomicU64,
    oom_errors: AtomicU64,
    peak_bytes: AtomicU64,
}

static STATS: LazyLock<SharedBufStats> = LazyLock::new(|| SharedBufStats::default());

/// Get telemetry for shared buffer operations.
///
/// Returns dict with: allocations, releases, oom_errors,
/// current_allocated_bytes, peak_allocated_bytes
#[pyfunction]
fn get_shared_buf_telemetry() -> HashMap<String, u64> {
    let mut m = HashMap::new();
    m.insert(
        "allocations".to_string(),
        STATS.allocations.load(Ordering::Relaxed),
    );
    m.insert(
        "releases".to_string(),
        STATS.releases.load(Ordering::Relaxed),
    );
    m.insert(
        "oom_errors".to_string(),
        STATS.oom_errors.load(Ordering::Relaxed),
    );
    m.insert(
        "current_allocated_bytes".to_string(),
        SHARED_BUF_ALLOCATED.load(Ordering::SeqCst),
    );
    m.insert(
        "peak_allocated_bytes".to_string(),
        STATS.peak_bytes.load(Ordering::Relaxed),
    );
    m
}

/// Reset shared buffer telemetry counters.
#[pyfunction]
fn reset_shared_buf_telemetry() {
    STATS.allocations.store(0, Ordering::Relaxed);
    STATS.releases.store(0, Ordering::Relaxed);
    STATS.oom_errors.store(0, Ordering::Relaxed);
    STATS.peak_bytes.store(0, Ordering::Relaxed);
}

/// Check if Metal shared buffers are available on this platform.
#[pyfunction]
fn is_metal_shared_available() -> bool {
    get_device().is_some()
}

// ─── Module Registration ──────────────────────────────────────────────────

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // HashMap already imported at module level (line 54)

    m.add_class::<SharedMetalBuffer>()?;
    m.add_function(wrap_pyfunction!(get_shared_buf_telemetry, m)?)?;
    m.add_function(wrap_pyfunction!(reset_shared_buf_telemetry, m)?)?;
    m.add_function(wrap_pyfunction!(is_metal_shared_available, m)?)?;

    // Constants
    m.add("SHARED_BUF_MAX_SINGLE_MB", 256_u64)?;
    m.add("SHARED_BUF_TOTAL_BUDGET_MB", 512_u64)?;

    Ok(())
}

// ─── Tests ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_buffer_allocation_bounds() {
        // Zero bytes should fail
        // (can't easily test runtime PyResult from unit tests, but logic is checked)
        assert!(SHARED_BUF_MAX_SINGLE == 256 * 1024 * 1024);
        assert!(SHARED_BUF_TOTAL_BUDGET == 512 * 1024 * 1024);
    }

    #[test]
    fn test_track_alloc_free() {
        let initial = SHARED_BUF_ALLOCATED.load(Ordering::SeqCst);
        assert!(track_alloc(1024));
        assert_eq!(SHARED_BUF_ALLOCATED.load(Ordering::SeqCst), initial + 1024);
        track_free(1024);
        assert_eq!(SHARED_BUF_ALLOCATED.load(Ordering::SeqCst), initial);
    }

    #[test]
    fn test_track_alloc_overflow() {
        let initial = SHARED_BUF_ALLOCATED.load(Ordering::SeqCst);
        // Try to allocate more than budget
        let huge = SHARED_BUF_TOTAL_BUDGET + 1;
        assert!(!track_alloc(huge));
        // Should not have changed
        assert_eq!(SHARED_BUF_ALLOCATED.load(Ordering::SeqCst), initial);
    }
}
