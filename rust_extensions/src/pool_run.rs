//! pool_run — Python-callable rayon pool runners (channel-based dispatch)
#![allow(dead_code)]
//!
//! CONSOLIDATED (R2 fix): Merged rayon_dispatch.rs into pool_run.rs.
//! Previously there were two identical implementations — pool_run.rs (with dead
//! rayon_submit/rayon_join aliases) and rayon_dispatch.rs (with the correct
//! rayon_submit_channel/rayon_join_channel names).
//!
//! ## Architecture
//!
//! One dispatcher thread per pool type (cpu, io, mixed) that runs
//! pool.install() and consumes from a bounded sync_channel (capacity=256).
//! The dispatcher pulls work items from the channel and executes them on
//! the rayon pool threads via pool.install().
//!
//! The GIL is held by the asyncio.to_thread worker thread during both
//! submit and join. The rayon pool workers acquire the GIL via
//! Python::attach() for Python callbacks — no contention because the
//! asyncio worker is blocked on the condvar during pool execution.
//!
//! ## M1 8GB Safety
//!
//! - 1 dispatcher thread per pool type (3 total, not per-task)
//! - Bounded channel (256 items) provides natural back-pressure
//! - Existing rayon pool threads are reused — no per-task allocation
//! - Zero heap allocations on the hot path (after init)
//!
//! ## Function Naming
//!
//! - `cpu_pool_run` / `io_pool_run` / `mixed_pool_run` — sync GIL wrappers
//!   (call Python directly with GIL held, no pool usage — for tiny workloads)
//! - `rayon_submit_channel` / `rayon_join_channel` / `rayon_abort_channel` /
//!   `rayon_shutdown_channel` — channel-based dispatch to rayon pools
//!   (~5μs/task vs ~500μs for thread::spawn)

use pyo3::prelude::*;
use pyo3::types::PyTuple;
use rayon::ThreadPool;
use std::sync::atomic::{AtomicBool, AtomicU8, Ordering};
use std::sync::{Arc, LazyLock, Weak};
use std::thread;
use std::time::Duration;

// Use parking_lot: no poisoning (panic in one thread won't poison the mutex),
// ~2x faster than std::sync::Mutex, and .lock() returns Guard directly (no Result).
// This prevents unwrap() panics from propagating as Rust panics across the PyO3 FFI boundary.

use crossbeam_channel::{bounded, Receiver, Sender};

use crate::elastic_pool::{get_cpu_pool, get_io_pool};
use crate::mixed_pool;
#[cfg(feature = "otel")]
use crate::tracing::{clear_tls_trace_context, is_tracing_enabled, set_tls_trace_context};

// NEW-M1 FIX: PyCapsule RAII guard for Arc-based lifecycle.
// Python 3.14+ compatible using stable PyCapsule C API.
// The capsule destructor automatically calls rayon_drop_channel when garbage collected,
// providing RAII semantics at the FFI boundary.

// Capsule name for PyCapsule FFI boundary (Python 3.14+ stable C API)
const RAYON_HANDLE_CAPSULE_NAME: &str = "hledac.universal._rayon_handle";

// Module-level FFI imports for Python 3.14+ compatibility (stable PyCapsule C API)
use pyo3::ffi::{PyCapsule_GetPointer, PyCapsule_IsValid, PyCapsule_New};

/// Tombstone set to prevent double-free on drop_rayon_handle_internal.
/// Once a handle is dropped, its pointer value is kept PERMANENTLY.
/// This is safe because Arc::into_raw returns unique, never-reused pointer values.
static DROPPED_HANDLES: LazyLock<parking_lot::RwLock<std::collections::HashSet<usize>>, fn() -> _> =
    LazyLock::new(|| parking_lot::RwLock::new(std::collections::HashSet::new()));

/// Internal helper to drop a rayon handle (Arc::from_raw + drop).
/// Thread-safe via RwLock tombstone set.
/// Returns silently if handle was already dropped (idempotent).
fn drop_rayon_handle_internal(ptr: usize) {
    if ptr == 0 {
        return;
    }

    // Check tombstone first — if already dropped, this is a no-op
    {
        let dropped = DROPPED_HANDLES.read();
        if dropped.contains(&ptr) {
            return;
        }
    }

    // Mark as dropped (Atomically: check-then-insert)
    {
        let mut dropped = DROPPED_HANDLES.write();
        if dropped.contains(&ptr) {
            return;
        }
        dropped.insert(ptr);
    }

    // NOW safe to drop: reconstruct Arc and drop it
    // SAFETY: ptr was returned by Arc::into_raw in rayon_submit_channel.
    // We hold the tombstone, so no other thread can drop this pointer.
    let _shared = unsafe { Arc::from_raw(ptr as *const SharedTask) };
    // Arc drops here — SharedTask refcount decremented
}

/// PyCapsule destructor callback — called automatically by Python GC.
/// SAFETY: This is an extern "C" fn with PyObject* pointer, per PyCapsule spec.
unsafe extern "C" fn rayon_handle_destructor(capsule: *mut pyo3::ffi::PyObject) {
    if capsule.is_null() {
        return;
    }

    // Get pointer from capsule using stable Python 3.14+ C API
    let ptr = PyCapsule_GetPointer(capsule, RAYON_HANDLE_CAPSULE_NAME.as_ptr() as *const _);
    if ptr.is_null() {
        return;
    }

    drop_rayon_handle_internal(ptr as usize);
}

// State encoding for atomic compare-exchange
const STATE_PENDING: u8 = 0;
const STATE_READY: u8 = 1;
const STATE_ABORTED: u8 = 2;

// ---------------------------------------------------------------------------
// Work item — submitted to rayon pool dispatcher via channel
// ---------------------------------------------------------------------------

/// Optional trace context for cross-language trace propagation (TEL-02).
/// Carries W3C Trace Context trace_id and span_id from Python OTel
/// into Rust rayon worker threads.
#[derive(Clone, Debug)]
struct TraceContext {
    trace_id: u128,
    span_id: u128,
}

/// Work item — submitted to rayon pool dispatcher via channel.
/// Uses Weak so the Arc can be dropped by the submitter after submission
/// without affecting the worker thread's reference.
struct WorkItem {
    func: Py<PyAny>,
    args: Py<PyTuple>,
    /// Batch size hint — used by mixed dispatcher to select pool size
    n_items: usize,
    /// Shared result storage + synchronization — Weak prevents use-after-free
    /// when submitter drops its Arc before worker finishes.
    shared: Weak<SharedTask>,
    /// TEL-02: Trace context from Python OTel — propagated across language boundary.
    /// None = no active span (worker runs without tracing instrumentation).
    trace_context: Option<TraceContext>,
}

/// Shared storage for task result + cancellation + completion signal.
struct SharedTask {
    /// Result of the Python function call.
    /// parking_lot::Mutex: no poisoning, no unwrap needed, 2x faster.
    result: parking_lot::Mutex<Option<Result<Py<PyAny>, PyErr>>>,
    /// Set by rayon_abort to request early cancellation.
    cancel_flag: AtomicBool,
    /// Atomic state: STATE_PENDING | STATE_READY | STATE_ABORTED.
    /// Prevents race between worker writing result and timeout setting aborted.
    state: AtomicU8,
    /// Signaled when result is ready.
    /// parking_lot::Condvar paired with parking_lot::Mutex (compatible, both from parking_lot).
    condvar: parking_lot::Condvar,
}

// ---------------------------------------------------------------------------
// Global channel senders — one per pool type, one dispatcher thread each
// ---------------------------------------------------------------------------

static RAYON_SHUTDOWN: AtomicBool = AtomicBool::new(false);

fn cpu_sender() -> &'static parking_lot::Mutex<Option<Sender<WorkItem>>> {
    static SENDER: LazyLock<
        parking_lot::Mutex<Option<Sender<WorkItem>>>,
        fn() -> parking_lot::Mutex<Option<Sender<WorkItem>>>,
    > = LazyLock::new(|| {
        let (tx, rx) = bounded(256);
        spawn_dispatcher("cpu", Arc::new(rx));
        parking_lot::Mutex::new(Some(tx))
    });
    &SENDER
}

fn io_sender() -> &'static parking_lot::Mutex<Option<Sender<WorkItem>>> {
    static SENDER: LazyLock<
        parking_lot::Mutex<Option<Sender<WorkItem>>>,
        fn() -> parking_lot::Mutex<Option<Sender<WorkItem>>>,
    > = LazyLock::new(|| {
        let (tx, rx) = bounded(256);
        spawn_dispatcher("io", Arc::new(rx));
        parking_lot::Mutex::new(Some(tx))
    });
    &SENDER
}

fn mixed_sender() -> &'static parking_lot::Mutex<Option<Sender<WorkItem>>> {
    static SENDER: LazyLock<
        parking_lot::Mutex<Option<Sender<WorkItem>>>,
        fn() -> parking_lot::Mutex<Option<Sender<WorkItem>>>,
    > = LazyLock::new(|| {
        let (tx, rx) = bounded(256);
        spawn_dispatcher("mixed", Arc::new(rx));
        parking_lot::Mutex::new(Some(tx))
    });
    &SENDER
}

/// Spawn a dispatcher thread that runs pool.install() and consumes work from rx.
///
/// NOTE: Uses `.expect()` because dispatcher thread is essential for pool operation.
/// If this fails, the entire thread pool becomes non-functional (work won't be
/// dispatched). This indicates a system-level OOM/resource exhaustion issue.
/// MODERN-28 FIX: Dispatcher threads use UTILITY → E-cores.
/// Dispatchers are I/O-bound (queue polling, recv operations).
/// This keeps P-cores available for CPU-intensive rayon work.
fn spawn_dispatcher(pool_name: &str, rx: Arc<Receiver<WorkItem>>) {
    let pool_name_owned = pool_name.to_string();
    thread::Builder::new()
        .name(format!("hledac-dispatch-{}", pool_name_owned))
        .stack_size(4_194_304) // 4 MiB
        .spawn(move || {
            #[cfg(target_os = "macos")]
            {
                unsafe {
                    use libc::pthread_set_qos_class_self_np;
                    // MODERN-28: Dispatchers use UTILITY (E-cores)
                    let qos = libc::qos_class_t::QOS_CLASS_UTILITY;
                    pthread_set_qos_class_self_np(qos, 0);
                }
            }

            match pool_name_owned.as_str() {
                "cpu" => run_dispatcher_loop(get_cpu_pool(), rx),
                "io" => run_dispatcher_loop(get_io_pool(), rx),
                "mixed" => run_mixed_dispatcher_loop(rx),
                _ => run_dispatcher_loop(get_cpu_pool(), rx),
            }
        })
        .expect("pool_run: OOM or system thread limit exceeded — dispatcher thread spawn failed");
}

/// Dispatcher loop for fixed pools (cpu, io).
fn run_dispatcher_loop(pool: Arc<ThreadPool>, rx: Arc<Receiver<WorkItem>>) {
    pool.install(|| loop {
        if RAYON_SHUTDOWN.load(Ordering::Acquire) {
            break;
        }
        match rx.recv_timeout(Duration::from_millis(100)) {
            Ok(work) => execute_work_item(work),
            Err(crossbeam_channel::RecvTimeoutError::Timeout) => continue,
            Err(crossbeam_channel::RecvTimeoutError::Disconnected) => break,
        }
    });
}

/// Dispatcher loop for mixed pool — selects POOL_SINGLE or POOL_PAIR at dispatch time.
fn run_mixed_dispatcher_loop(rx: Arc<Receiver<WorkItem>>) {
    if std::panic::catch_unwind(|| {
        let _ = mixed_pool(0);
        let _ = mixed_pool(usize::MAX);
    })
    .is_err()
    {
        return;
    }

    loop {
        if RAYON_SHUTDOWN.load(Ordering::Acquire) {
            break;
        }
        match rx.recv_timeout(Duration::from_millis(100)) {
            Ok(work) => {
                execute_work_item(work);
            }
            Err(crossbeam_channel::RecvTimeoutError::Timeout) => continue,
            Err(crossbeam_channel::RecvTimeoutError::Disconnected) => break,
        }
    }
}

// ---------------------------------------------------------------------------
// TEL-02: Trace context propagation — optional tracing span wrapper
// ---------------------------------------------------------------------------

// Stub when otel is disabled — just execute the closure directly.
#[cfg(not(feature = "otel"))]
fn execute_with_optional_span<R>(_trace_context: Option<TraceContext>, f: impl FnOnce() -> R) -> R {
    f()
}

#[cfg(feature = "otel")]
/// Execute a closure, optionally wrapped in a tracing span for cross-language
/// trace propagation (TEL-02).
///
/// When `trace_context` is Some, creates a tracing span with W3C Trace Context
/// attributes (trace_id, span_id) so that Rust-side spans are linked to the
/// parent Python OTel span. This enables end-to-end distributed tracing across
/// the Python ↔ Rust FFI boundary.
///
/// When `trace_context` is None, simply executes the closure without tracing.
fn execute_with_optional_span<R>(trace_context: Option<TraceContext>, f: impl FnOnce() -> R) -> R {
    match trace_context {
        Some(ctx) if is_tracing_enabled() => {
            // TEL-02: Set TLS context for Rust-side tracing span created below.
            // Note: get_tls_trace_id() / get_tls_span_id() are accessor functions exposed
            // to Python but are NOT called by pool_run.rs — only set/clear are needed here.
            set_tls_trace_context(Some(ctx.trace_id), Some(ctx.span_id));

            // Create tracing span with W3C traceparent-compatible attributes
            let span = tracing::info_span!(
                "rayon_worker",
                raython.trace_id = %format!("{:032x}", ctx.trace_id),
                raython.span_id = %format!("{:016x}", ctx.span_id),
                raython.pool_work = true,
            );

            // F351 FIX: Use catch_unwind to guarantee TLS cleanup even on panic.
            // If f() panics, in_scope propagates the panic and clear_tls_trace_context()
            // would never run — leaving stale TLS context on the worker thread,
            // contaminating subsequent work items processed by the same thread.
            let result =
                std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| span.in_scope(f)));

            // Always clear TLS after span ends — both on success and on panic
            clear_tls_trace_context();

            // Propagate panic as-is (re-panic via resume_unwind)
            match result {
                Ok(v) => v,
                Err(payload) => std::panic::panic_any(payload),
            }
        }
        _ => f(),
    }
}

/// Execute a single work item: cooperative cancellation check, GIL-acquire, call Python func.
/// R5 FIX: Uses Weak::upgrade() to safely handle the case where Python has already
/// dropped its Arc<SharedTask> (via Box::into_raw) before the worker started or finished.
/// This prevents use-after-free where Arc was dropped at line 360 (drop(shared_box))
/// while the worker thread was still running.
fn execute_work_item(work: WorkItem) {
    // R5 FIX: Upgrade Weak to Arc — may return None if Python already dropped its Arc.
    // This can happen when:
    //   1. Python called rayon_join_channel and its local Arc was dropped at function return
    //   2. Worker hasn't started yet (channel backed up) or is still running
    // If None → the task was already collected and we should exit silently.
    let Some(shared) = work.shared.upgrade() else {
        // SharedTask was already dropped by Python-side join — silent exit is correct.
        return;
    };

    // Cooperative cancellation check
    if shared.cancel_flag.load(Ordering::Acquire) {
        let mut guard = shared.result.lock();
        *guard = Some(Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
            "Task was cancelled before starting",
        )));
        shared.state.swap(STATE_READY, Ordering::AcqRel);
        shared.condvar.notify_one();
        return;
    }

    // TEL-02: Execute Python function, optionally wrapped in a tracing span
    // for cross-language trace context propagation.
    let py_result = execute_with_optional_span(work.trace_context.clone(), || {
        // Execute Python function with GIL
        Python::attach(|py| {
            let result = work
                .func
                .into_bound(py)
                .call1((work.args.into_bound(py),))?;
            Ok(result.unbind())
        })
    });

    // Atomically set STATE_READY if still PENDING (not aborted by timeout)
    let expected = STATE_PENDING;
    if shared
        .state
        .compare_exchange(expected, STATE_READY, Ordering::AcqRel, Ordering::Acquire)
        .is_ok()
    {
        let mut guard = shared.result.lock();
        *guard = Some(py_result);
    }

    shared.condvar.notify_one();
}

// ---------------------------------------------------------------------------
// Simple sync pool runners — GIL wrapper, no thread spawn (fast path for small work)
// ---------------------------------------------------------------------------

#[pyfunction]
#[pyo3(name = "cpu_pool_run")]
pub fn cpu_pool_run_(_py: Python<'_>, func: Py<PyAny>, args: Py<PyTuple>) -> PyResult<Py<PyAny>> {
    Python::attach(|py| {
        let result = func.into_bound(py).call1((args.into_bound(py),))?;
        Ok(result.unbind())
    })
}

#[pyfunction]
#[pyo3(name = "io_pool_run")]
pub fn io_pool_run_(_py: Python<'_>, func: Py<PyAny>, args: Py<PyTuple>) -> PyResult<Py<PyAny>> {
    Python::attach(|py| {
        let result = func.into_bound(py).call1((args.into_bound(py),))?;
        Ok(result.unbind())
    })
}

#[pyfunction]
#[pyo3(name = "mixed_pool_run")]
pub fn mixed_pool_run_(
    _py: Python<'_>,
    _n_items: usize,
    func: Py<PyAny>,
    args: Py<PyTuple>,
) -> PyResult<Py<PyAny>> {
    Python::attach(|py| {
        let result = func.into_bound(py).call1((args.into_bound(py),))?;
        Ok(result.unbind())
    })
}

// ---------------------------------------------------------------------------
// rayon_submit_channel — channel-based dispatch, returns handle for join/abort
// ---------------------------------------------------------------------------

/// Submit a Python function to the rayon pool via channel dispatch.
/// Returns immediately (does NOT block on the result).
/// Result is retrieved via rayon_join_channel.
///
/// GIL: caller (asyncio.to_thread) must hold the GIL during this call.
#[pyfunction]
#[pyo3(name = "rayon_submit_channel")]
pub fn rayon_submit_channel_(
    py: Python<'_>,
    pool_type: &str,
    n_items: usize,
    func: Py<PyAny>,
    args: Py<PyTuple>,
    trace_id: Option<u128>,
    span_id: Option<u128>,
) -> PyResult<Py<PyAny>> {
    let func_clone = Py::clone_ref(&func, py);
    let args_clone = Py::clone_ref(&args, py);

    // TEL-02: Capture Python OTel trace context for cross-language propagation.
    // When both trace_id and span_id are provided, create a TraceContext.
    // This allows Rust-side tracing spans to be linked to Python OTel spans.
    let trace_context: Option<TraceContext> = match (trace_id, span_id) {
        (Some(tid), Some(sid)) if tid != 0 && sid != 0 => Some(TraceContext {
            trace_id: tid,
            span_id: sid,
        }),
        _ => None,
    };

    // R5 FIX: Use Arc::new_cyclic so the Weak in WorkItem can upgrade to the Arc.
    // Ownership model:
    //   - WorkItem.shared = Weak<SharedTask> (does NOT keep SharedTask alive)
    //   - Python receives Arc::into_raw(work_shared) as usize pointer
    //   - Python passes usize back → Arc::from_raw reconstructs Arc in join/abort
    //   - Worker upgrades Weak → valid Arc for duration of execute_work_item
    //   - When worker finishes: if Arc still alive (Python hasn't dropped), condvar fires
    //   - If Python never calls rayon_join: work_shared is leaked (acceptable for abort path)
    // R5 FIX: Use Arc::new_cyclic so the Weak in WorkItem can upgrade to the Arc.
    // Ownership model:
    //   - WorkItem.shared = Weak<SharedTask> (does NOT keep SharedTask alive)
    //   - Python receives Arc::into_raw(work_shared) as usize pointer
    //   - Python passes usize back → Arc::from_raw reconstructs Arc in join/abort
    //   - Worker upgrades Weak → valid Arc for duration of execute_work_item
    //   - When worker finishes: if Arc still alive (Python hasn't dropped), condvar fires
    //   - If Python never calls rayon_join: work_shared is leaked (acceptable for abort path)
    let work_shared: Arc<SharedTask> = Arc::new_cyclic(|_weak| SharedTask {
        result: parking_lot::Mutex::new(None),
        cancel_flag: AtomicBool::new(false),
        state: AtomicU8::new(STATE_PENDING),
        condvar: parking_lot::Condvar::new(),
    });

    // Get Weak from Arc for WorkItem — weak is out of scope here (closure ended)
    let work_shared_weak = std::sync::Arc::downgrade(&work_shared);

    let work = WorkItem {
        func: func_clone,
        args: args_clone,
        n_items,
        shared: work_shared_weak,
        trace_context,
    };

    let sender_mutex: &parking_lot::Mutex<Option<Sender<WorkItem>>> = match pool_type {
        "cpu" => cpu_sender(),
        "io" => io_sender(),
        "mixed" => mixed_sender(),
        _ => cpu_sender(),
    };

    let sender_guard = sender_mutex.lock();
    if let Some(ref sender) = *sender_guard {
        match sender.send(work) {
            Ok(()) => {}
            Err(_) => {
                drop(sender_guard);
                return Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                    "Dispatcher thread died — pool may have panicked",
                ));
            }
        }
    } else {
        drop(sender_guard);
        return Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
            "Channel already shut down",
        ));
    }

    // NEW-M1 FIX: Return PyCapsule with RAII destructor instead of raw usize.
    // The capsule auto-calls rayon_drop_channel when garbage collected,
    // providing RAII semantics at the FFI boundary.
    //
    // Backward compatibility: Both PyCapsule (default) and raw usize handles
    // are supported. Python code can pass either to rayon_join/abort_channel.
    let ptr = Arc::into_raw(work_shared) as *mut std::ffi::c_void;
    let capsule = unsafe {
        PyCapsule_New(
            ptr,
            RAYON_HANDLE_CAPSULE_NAME.as_ptr() as *const std::ffi::c_char,
            Some(rayon_handle_destructor),
        )
    };
    Ok(Py::from(capsule))
}

// ---------------------------------------------------------------------------
// rayon_join_channel — wait for rayon_submit_channel task to complete
// ---------------------------------------------------------------------------

/// Wait for a rayon_submit_channel task to complete.
///
/// GIL: caller (asyncio.to_thread) must hold the GIL during this call
/// (the condvar wait releases the GIL).
///
/// timeout_s: seconds to wait. None = indefinite.
#[pyfunction]
#[pyo3(name = "rayon_join_channel")]
pub fn rayon_join_channel_(
    py: Python<'_>,
    handle_ptr: usize,
    timeout_s: Option<f64>,
) -> PyResult<Py<PyAny>> {
    // FFI-03 FIX: Null check on handle_ptr before Arc::from_raw.
    // Arc::into_raw never returns null, but Python could pass garbage on bug/premature-GC.
    // Using isize::MIN as sentinel is safer than 0 (which could be a valid offset).
    const INVALID_HANDLE: usize = 0;
    if handle_ptr == INVALID_HANDLE {
        return Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
            "Invalid handle: null pointer",
        ));
    }

    // FFI-03 FIX: Wrap Arc::from_raw + critical section in catch_unwind.
    // If handle_ptr is a dangling/invalid pointer (not from Arc::into_raw),
    // accessing shared.result or other fields could panic. catch_unwind
    // prevents panic propagation across FFI boundary (UB in C ABI).
    //
    // Arc::from_raw itself is unsafe but returns a valid Arc; the panic hazard
    // is from invalid data inside SharedTask being accessed via the locks.
    let shared = unsafe { Arc::from_raw(handle_ptr as *const SharedTask) };

    let timeout = timeout_s
        .map(|t| Duration::from_secs_f64(t.max(0.0)))
        .unwrap_or(Duration::MAX);

    // GIL RELEASE FIX (R-16.1): Use py.detach + parking_lot Condvar
    // to actually release the GIL during the wait, allowing the asyncio event
    // loop to run other coroutines in parallel.
    //
    // Previous busy-loop with std::thread::sleep() held the GIL the entire time
    // because Python threads cannot release GIL via std::thread::sleep().
    //
    // parking_lot::Condvar::wait_for() calls pthread_cond_timedwait on macOS,
    // which is a real OS-level block (not busy-polling). Wrapped in
    // py.detach() to release the GIL during the syscall.
    //
    // Memory safety: Arc<SharedTask> stays alive because:
    //   - R5 FIX: Worker thread upgrades Weak in WorkItem → valid Arc until worker finishes
    //   - Python's Arc::from_raw (from Arc::into_raw in submit) keeps SharedTask alive
    //   - SharedTask dropped when BOTH Python Arc and worker Arc are dropped
    let deadline = std::time::Instant::now() + timeout;

    // Thread-safe flag: AtomicBool is Sync (unlike Cell), usable across allow_threads boundary
    use std::sync::atomic::{AtomicBool, Ordering};
    let timed_out_flag = Arc::new(AtomicBool::new(true));
    let timed_out_flag_clone = Arc::clone(&timed_out_flag);

    // FFI-03 FIX: catch_unwind around the critical section (Arc access).
    // AssertUnwindSafe tells panic::catch_unwind that the closure won't
    // unwind through a RIIA guard that requires drop semantics.
    let inner_result: PyResult<Py<PyAny>> =
        std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            py.detach(|| {
                let mut guard = shared.result.lock();
                while (*guard).is_none() {
                    let remaining = deadline.saturating_duration_since(std::time::Instant::now());
                    if remaining.is_zero() {
                        break;
                    }
                    // wait_for: atomically unlocks mutex and blocks on condvar;
                    // on notify or timeout, reacquires mutex and returns WaitTimeoutResult.
                    // Guard is MODIFIED IN-PLACE (parking_lot semantics).
                    // On macOS this maps to pthread_cond_timedwait — real OS thread block.
                    let _wait_result = shared.condvar.wait_for(&mut guard, remaining);
                }
                // timed_out = true if result is still None after wait loop
                timed_out_flag_clone.store((*guard).is_none(), Ordering::Release);
            });
            // Re-lock to read result — allow_threads released the mutex on each iteration
            let timed_out = timed_out_flag.load(Ordering::Acquire);
            let mut guard = shared.result.lock();
            if timed_out {
                // Try to claim ABORTED ownership atomically.
                // If CAS fails → worker already won (wrote result and transitioned to READY).
                // We must NOT overwrite the worker's valid result with a timeout error.
                // If CAS succeeds → we own the result; write timeout error only if still None.
                let expected = STATE_PENDING;
                let we_own = shared
                    .state
                    .compare_exchange(expected, STATE_ABORTED, Ordering::AcqRel, Ordering::Acquire)
                    .is_ok();
                drop(guard);

                if we_own {
                    // WE are responsible for the result — write timeout error if worker didn't.
                    let mut rguard = shared.result.lock();
                    if rguard.is_none() {
                        *rguard = Some(Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                            "Rayon dispatch timed out",
                        )));
                    }
                }
                // else: worker won the race — don't overwrite its valid result

                let result = shared.result.lock().take();
                match result {
                    Some(Ok(py_obj)) => Ok(py_obj.into_pyobject(py).unwrap().into()),
                    Some(Err(err)) => Err(err),
                    None => Ok(py.None().into_pyobject(py).unwrap().into()),
                }
            } else {
                let result = (*guard).take();
                match result {
                    Some(Ok(py_obj)) => Ok(py_obj.into_pyobject(py).unwrap().into()),
                    Some(Err(err)) => Err(err),
                    None => Ok(py.None().into_pyobject(py).unwrap().into()),
                }
            }
        }))
        .map_err(|_| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("Panic in rayon_join"))?;

    inner_result
}

// ---------------------------------------------------------------------------
// rayon_abort_channel — signal cancellation and wait for worker acknowledgment
// ---------------------------------------------------------------------------

/// Abort a rayon dispatch task.
/// Sets cancel flag and waits up to 5s for worker acknowledgment.
#[pyfunction]
#[pyo3(name = "rayon_abort_channel")]
pub fn rayon_abort_channel_(py: Python<'_>, handle_ptr: usize) -> PyResult<()> {
    // FFI-03 FIX: Null check before Arc::from_raw.
    if handle_ptr == 0 {
        return Ok(());
    }

    // FFI-03 FIX: Reconstruct Arc, then catch_unwind around critical section.
    // Arc::from_raw is unsafe; the closure could panic on invalid data.
    let shared = unsafe { Arc::from_raw(handle_ptr as *const SharedTask) };

    shared.cancel_flag.store(true, Ordering::Release);

    // Wait up to 5s for worker to acknowledge
    // GIL RELEASE FIX: same pattern as rayon_join_channel_ — use condvar wait
    // with allow_threads to release GIL during blocking.
    let timeout = Duration::from_secs(5);
    let deadline = std::time::Instant::now() + timeout;

    // FFI-03 FIX: catch_unwind around Arc access.
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        py.detach(|| {
            let mut guard = shared.result.lock();
            while (*guard).is_none() {
                let remaining = deadline.saturating_duration_since(std::time::Instant::now());
                if remaining.is_zero() {
                    break;
                }
                let _wait_result = shared.condvar.wait_for(&mut guard, remaining);
            }
        });
    }))
    .map_err(|_| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("Panic in rayon_abort"))?;

    Ok(())
}

// ---------------------------------------------------------------------------
// rayon_shutdown_channel — graceful shutdown of all dispatcher threads
// ---------------------------------------------------------------------------

/// Graceful shutdown — sets shutdown flag, closes all senders,
/// and waits for dispatcher threads to exit (via channel close).
/// Safe to call multiple times.
#[pyfunction]
#[pyo3(name = "rayon_shutdown_channel")]
pub fn rayon_shutdown_channel_() -> PyResult<()> {
    RAYON_SHUTDOWN.store(true, Ordering::Release);

    // parking_lot::Mutex::lock() returns MutexGuard directly (no Result),
    // so we use a scoped block to drop the guard immediately after use.
    {
        let mut tx = cpu_sender().lock();

        let _ = tx.take();
    }
    {
        let mut tx = io_sender().lock();

        let _ = tx.take();
    }
    {
        let mut tx = mixed_sender().lock();

        let _ = tx.take();
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// rayon_drop_channel — explicitly drop a rayon handle (Arc ownership release)
// ---------------------------------------------------------------------------

/// NEW-M1 FIX: Explicitly drop a rayon handle's Arc ownership.
///
/// This function is called automatically by the PyCapsule destructor when
/// the handle is garbage collected (RAII pattern). It can also be called
/// manually for immediate cleanup (e.g., after rayon_join/abort completes).
///
/// Thread-safe via tombstone pattern — safe to call multiple times.
///
/// Args:
///     handle: usize pointer from rayon_submit_channel (raw handle).
///             Can also accept a PyCapsule — extracts pointer internally.
///
/// Returns:
///     None on success. Raises PyRuntimeError if handle is invalid.
#[pyfunction]
#[pyo3(name = "rayon_drop_channel")]
pub fn rayon_drop_channel_(handle: &PyAny) -> PyResult<()> {
    let ptr = if let Ok(ptr) = handle.extract::<usize>() {
        // Raw usize handle (backward compatibility)
        ptr
    } else if let Ok(capsule_ptr) = handle.extract::<*mut std::ffi::c_void>() {
        // PyCapsule handle — extract the pointer
        capsule_ptr as usize
    } else {
        return Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
            "Invalid handle type: expected usize or PyCapsule",
        ));
    };

    drop_rayon_handle_internal(ptr);
    Ok(())
}

// ---------------------------------------------------------------------------
// Deprecated aliases — removed in R2 (were exact duplicates of channel versions)
// ---------------------------------------------------------------------------
// NOTE: rayon_submit / rayon_join / rayon_abort / rayon_shutdown were removed.
// They were identical to the _channel versions and no Python code used them.
// If you need these names, use the _channel variants instead.

pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // GIL wrappers — sync fast path for tiny workloads (no pool used)
    m.add_function(wrap_pyfunction!(cpu_pool_run_, m)?)?;
    m.add_function(wrap_pyfunction!(io_pool_run_, m)?)?;
    m.add_function(wrap_pyfunction!(mixed_pool_run_, m)?)?;
    // Channel-based dispatch to rayon pools (~5μs/task)
    m.add_function(wrap_pyfunction!(rayon_submit_channel_, m)?)?;
    m.add_function(wrap_pyfunction!(rayon_join_channel_, m)?)?;
    m.add_function(wrap_pyfunction!(rayon_abort_channel_, m)?)?;
    m.add_function(wrap_pyfunction!(rayon_shutdown_channel_, m)?)?;
    // NEW-M1 FIX: Explicit Arc drop for manual cleanup + PyCapsule RAII support
    m.add_function(wrap_pyfunction!(rayon_drop_channel_, m)?)?;
    Ok(())
}
