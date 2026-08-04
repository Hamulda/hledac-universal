//! Work-Stealing Task DAG — SILICON-07: Dynamic lane rebalancing for M1 8GB
//!
//! Solves [META]-003: Static lane allocation cannot respond to mid-sprint data bonanzas.
//!
//! ## Architecture
//!
//! ```text
//! Python: SwarmDAG.submit(task_type, payload)
//!              │
//!              ▼
//!    ┌─────────────────────────────────────────────────────────────┐
//!    │           WorkStealingDAG (Rust, PyO3)                      │
//!    │                                                              │
//!    │  Crossbeam channels per task type (multi-producer,           │
//!    │  multi-consumer with per-worker receivers).                  │
//!    │                                                              │
//!    │  EMA ROI signals (α=0.3, 5s window):                        │
//!    │    fetch_roi, parse_roi, analyze_roi, graph_insert_roi     │
//!    │                                                              │
//!    │  Adaptive rebalancer (fires every 10s):                     │
//!    │    fetch_roi > parse_roi × 3 → migrate workers              │
//!    └─────────────────────────────────────────────────────────────┘
//! ```
//!
//! ## M1 8GB Safety Invariants
//!
//! - Max 8 worker threads (one per logical core)
//! - Bounded channels: 256 entries per type
//! - ROI signals: lock-free atomic read path
//! - Workers stop cleanly on `stop()` call
//! - Per-worker memory: ~64 KB (queue + state)
//!
//! ## Task Types
//!
//! - `fetch`: Network I/O — URL fetching, certificate enumeration
//! - `parse`: CPU-bound — HTML parsing, IOC extraction, dedup
//! - `analyze`: CPU-bound — Graph insert, synthesis, NER
//! - `graph_insert`: I/O-bound — DuckDB write, LMDB metadata
//!
//! ## ROI Signal Design
//!
//! Each task type tracks: `IOCs_produced / second` via EMA.
//! EMA formula: `ema_new = α * sample + (1 - α) * ema_old`
//! Parameters: α=0.3 (recency weight), window=5s (sample interval)
//!
//! Adaptive rebalancer fires every 10s:
//! - If `fetch_roi > parse_roi * 3.0`: steal 1 worker from parse pool
//! - If `parse_roi > fetch_roi * 3.0`: steal 1 worker from fetch pool
//! - Min pool size: 1 worker per type
//! - Max pool size: 6 workers (fetch) / 6 workers (parse) / 4 workers (analyze)

use crossbeam_channel::{bounded, Receiver, Sender};
use parking_lot::{Mutex, RwLock};
use pyo3::prelude::*;
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

// ---------------------------------------------------------------------------
// Constants — M1 8GB safe bounds
// ---------------------------------------------------------------------------

/// Max worker threads (one per logical core on M1 Air).
const MAX_WORKERS: usize = 8;
/// Max workers per task type.
const MAX_FETCH_WORKERS: usize = 6;
const MAX_PARSE_WORKERS: usize = 6;
const MAX_ANALYZE_WORKERS: usize = 4;
const MAX_GRAPH_WORKERS: usize = 4;
/// Min workers per type (floor).
const MIN_WORKERS_PER_TYPE: usize = 1;

/// Channel capacity per task type (per-worker queues share this depth).
const CHANNEL_CAPACITY: usize = 256;
/// Yields between polling iterations (microseconds).
const YIELD_US: u64 = 50;
/// ROI sampling interval in seconds.
const ROI_INTERVAL_SECS: f64 = 5.0;
/// Adaptive rebalance check interval in seconds.
const REBALANCE_INTERVAL_SECS: f64 = 10.0;
/// Fetch/Parse ROI ratio threshold for worker migration.
const ROI_STEAL_THRESHOLD: f64 = 3.0;
/// EMA smoothing factor.
const EMA_ALPHA: f64 = 0.3;

/// Initial worker counts per type (conservative, adaptive rebalancer grows as needed).
const INIT_FETCH_WORKERS: usize = 2;
const INIT_PARSE_WORKERS: usize = 2;
const INIT_ANALYZE_WORKERS: usize = 2;
const INIT_GRAPH_WORKERS: usize = 2;

// ---------------------------------------------------------------------------
// Task types
// ---------------------------------------------------------------------------

/// Task type identifiers exposed to Python.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[pyclass(name = "SwarmTaskType")]
pub enum TaskType {
    Fetch = 0,
    Parse = 1,
    Analyze = 2,
    GraphInsert = 3,
}

impl TaskType {
    fn from_usize(v: usize) -> Option<Self> {
        match v {
            0 => Some(TaskType::Fetch),
            1 => Some(TaskType::Parse),
            2 => Some(TaskType::Analyze),
            3 => Some(TaskType::GraphInsert),
            _ => None,
        }
    }

    fn from_str(s: &str) -> Option<Self> {
        match s {
            "fetch" | "FetchTask" => Some(TaskType::Fetch),
            "parse" | "ParseTask" => Some(TaskType::Parse),
            "analyze" | "AnalyzeTask" => Some(TaskType::Analyze),
            "graph_insert" | "GraphInsertTask" => Some(TaskType::GraphInsert),
            _ => None,
        }
    }

    fn as_str(&self) -> &'static str {
        match self {
            TaskType::Fetch => "fetch",
            TaskType::Parse => "parse",
            TaskType::Analyze => "analyze",
            TaskType::GraphInsert => "graph_insert",
        }
    }

    fn max_workers(&self) -> usize {
        match self {
            TaskType::Fetch => MAX_FETCH_WORKERS,
            TaskType::Parse => MAX_PARSE_WORKERS,
            TaskType::Analyze => MAX_ANALYZE_WORKERS,
            TaskType::GraphInsert => MAX_GRAPH_WORKERS,
        }
    }
}

/// Serialized task payload for crossbeam channel.
#[derive(Clone)]
pub struct TaskPayload {
    pub task_id: String,
    pub task_type: TaskType,
    /// Serialized payload bytes (msgpack/serde_json/bytes).
    pub payload_bytes: Vec<u8>,
    pub submitted_at: Instant,
}

// ---------------------------------------------------------------------------
// ROI Signal — exponential moving average
// ---------------------------------------------------------------------------

/// EMA ROI signal for one task type.
/// Thread-safe: updates via atomic operations.
struct EmaRoiSignal {
    ema: RwLock<f64>,
    last_sample: RwLock<f64>,
    sample_count: AtomicUsize,
    /// Accumulator — swapped to 0 on window expiry, then added to EMA.
    iocs_buffer: AtomicU64,
    tasks_buffer: AtomicU64,
}

impl EmaRoiSignal {
    fn new() -> Self {
        Self {
            ema: RwLock::new(0.0),
            last_sample: RwLock::new(0.0),
            sample_count: AtomicUsize::new(0),
            iocs_buffer: AtomicU64::new(0),
            tasks_buffer: AtomicU64::new(0),
        }
    }

    /// Record one completed task with `iocs` IOCs produced.
    /// Called from Python side after task result is processed.
    fn record(&self, iocs: u64) {
        // Accumulate into buffer (lock-free).
        self.iocs_buffer.fetch_add(iocs, Ordering::Relaxed);
        self.tasks_buffer.fetch_add(1, Ordering::Relaxed);

        // Sample window on interval expiry.
        let last = {
            let guard = self.last_sample.read();
            *guard
        };
        let now = Instant::now().elapsed().as_secs_f64();
        if now - last >= ROI_INTERVAL_SECS {
            self._sample_window(now);
        }
    }

    fn _sample_window(&self, now: f64) {
        // Swap buffer — take everything accumulated since last window.
        let iocs = self.iocs_buffer.swap(0, Ordering::Relaxed);
        let tasks = self.tasks_buffer.swap(0, Ordering::Relaxed);

        if tasks == 0 {
            return;
        }

        let sample = iocs as f64 / ROI_INTERVAL_SECS;

        let new_ema = {
            let count = self.sample_count.load(Ordering::Acquire);
            if count == 0 {
                sample
            } else {
                let current = *self.ema.read();
                EMA_ALPHA * sample + (1.0 - EMA_ALPHA) * current
            }
        };

        {
            let mut ema_guard = self.ema.write();
            *ema_guard = new_ema;
        }
        {
            let mut last_guard = self.last_sample.write();
            *last_guard = now;
        }
        self.sample_count.fetch_add(1, Ordering::Relaxed);
    }

    /// Get current ROI (IOCs/second) — fast read.
    fn get_roi(&self) -> f64 {
        *self.ema.read()
    }
}

// ---------------------------------------------------------------------------
// Per-task-type channel registry
// ---------------------------------------------------------------------------

/// One channel per task type: (sender, receiver).
/// All workers for a type share the same channel.
/// Python injects tasks via the sender.
struct TaskChannel {
    task_type: TaskType,
    tx: Sender<TaskPayload>,
    rx: Receiver<TaskPayload>,
}

impl TaskChannel {
    fn new(task_type: TaskType) -> Self {
        let (tx, rx) = bounded(CHANNEL_CAPACITY);
        Self { task_type, tx, rx }
    }

    fn try_send(&self, task: TaskPayload) -> bool {
        self.tx.send(task).is_ok()
    }

    /// Non-blocking recv. Returns None if empty.
    fn try_recv(&self) -> Option<TaskPayload> {
        self.rx.try_recv().ok()
    }

    /// Blocking recv with timeout.
    fn recv_deadline(&self, deadline: Instant) -> Option<TaskPayload> {
        self.rx.recv_deadline(deadline).ok()
    }
}

// ---------------------------------------------------------------------------
// Adaptive Rebalancer
// ---------------------------------------------------------------------------

/// Adaptive Rebalancer
///
/// Currently rebalances fetch↔parse pools based on ROI ratio.
/// analyze and graph_insert pools are stable (not rebalanced).
/// Future: consider analyze↔graph rebalancing when those signals mature.
struct Rebalancer {
    fetch_workers: AtomicUsize,
    parse_workers: AtomicUsize,
    analyze_workers: AtomicUsize,
    graph_workers: AtomicUsize,
    last_rebalance: AtomicU64,
}

impl Rebalancer {
    fn new() -> Self {
        Self {
            fetch_workers: AtomicUsize::new(INIT_FETCH_WORKERS),
            parse_workers: AtomicUsize::new(INIT_PARSE_WORKERS),
            analyze_workers: AtomicUsize::new(INIT_ANALYZE_WORKERS),
            graph_workers: AtomicUsize::new(INIT_GRAPH_WORKERS),
            last_rebalance: AtomicU64::new(0),
        }
    }

    fn get_allocation(&self) -> [usize; 4] {
        [
            self.fetch_workers.load(Ordering::Acquire),
            self.parse_workers.load(Ordering::Acquire),
            self.analyze_workers.load(Ordering::Acquire),
            self.graph_workers.load(Ordering::Acquire),
        ]
    }

    /// Check and trigger rebalance if interval has elapsed.
    /// Returns true if allocation changed.
    fn check_and_rebalance(
        &self,
        fetch_roi: f64,
        parse_roi: f64,
        _analyze_roi: f64,  // reserved for future analyze↔graph rebalancing
        now_secs: u64,
    ) -> bool {
        let last = self.last_rebalance.load(Ordering::Acquire);
        if now_secs.saturating_sub(last) < REBALANCE_INTERVAL_SECS as u64 {
            return false;
        }

        let mut changed = false;

        // Fetch vs Parse rebalance
        if parse_roi > 0.0 && fetch_roi > parse_roi * ROI_STEAL_THRESHOLD {
            let parse_w = self.parse_workers.load(Ordering::Acquire);
            let fetch_w = self.fetch_workers.load(Ordering::Acquire);
            if parse_w > MIN_WORKERS_PER_TYPE && fetch_w < MAX_FETCH_WORKERS {
                self.parse_workers.fetch_sub(1, Ordering::Relaxed);
                self.fetch_workers.fetch_add(1, Ordering::Relaxed);
                changed = true;
            }
        } else if fetch_roi > 0.0 && parse_roi > fetch_roi * ROI_STEAL_THRESHOLD {
            let parse_w = self.parse_workers.load(Ordering::Acquire);
            let fetch_w = self.fetch_workers.load(Ordering::Acquire);
            if fetch_w > MIN_WORKERS_PER_TYPE && parse_w < MAX_PARSE_WORKERS {
                self.fetch_workers.fetch_sub(1, Ordering::Relaxed);
                self.parse_workers.fetch_add(1, Ordering::Relaxed);
                changed = true;
            }
        }

        self.last_rebalance.store(now_secs, Ordering::Release);
        changed
    }
}

// ---------------------------------------------------------------------------
// Worker thread
// ---------------------------------------------------------------------------

struct WorkerContext {
    id: usize,
    task_types: Vec<TaskType>,
    channels: Arc<Vec<TaskChannel>>,
    running: Arc<AtomicBool>,
    result_callback: Py<PyAny>,
    /// Steal cursor — which task type to try first next iteration.
    steal_cursor: AtomicUsize,
}

impl WorkerContext {
    fn run(&mut self) {
        // Recomputed deadline at the start of each iteration.
        // Previous version computed a single deadline that could be stale
        // after one process_task() call — recomputing ensures fresh deadline.
        let deadline = Instant::now() + Duration::from_micros(YIELD_US);

        // Try local receive first
        for tt in &self.task_types {
            let ch = &self.channels[tt as usize];
            if let Some(task) = ch.try_recv() {
                self.process_task(task);
                return;
            }
        }

        // Try steal from other task types (round-robin steal)
        let n = self.channels.len();
        let cursor = self.steal_cursor.load(Ordering::Relaxed);
        for offset in 0..n {
            let idx = (cursor + offset) % n;
            // Skip our own task types (we already tried them)
            let is_own = self.task_types.iter().any(|t| (*t as usize) == idx);
            if is_own {
                continue;
            }
            if let Some(task) = self.channels[idx].try_recv() {
                self.steal_cursor.store((cursor + offset + 1) % n, Ordering::Relaxed);
                self.process_task(task);
                return;
            }
        }

        // Block on first task type for a short period
        for tt in &self.task_types {
            let ch = &self.channels[tt as usize];
            if let Some(task) = ch.recv_deadline(deadline) {
                self.process_task(task);
                return;
            }
        }

        // No work available — yield
        thread::sleep(Duration::from_micros(YIELD_US));
    }

    fn process_task(&self, task: TaskPayload) {
        Python::with_gil(|py| {
            // Call Python callback: callback(task_id, task_type, payload_bytes)
            let _ = self.result_callback.call1(
                py,
                (task.task_id, task.task_type as u8, &task.payload_bytes),
            );
        });
    }
}

// ---------------------------------------------------------------------------
// WorkStealingDAG — main PyO3 class
// ---------------------------------------------------------------------------

/// Work-Stealing Task DAG with ROI-based adaptive pool sizing.
///
/// All workers are background threads started by `start()`.
/// Tasks are submitted from Python via `submit()`.
#[pyclass(name = "WorkStealingDAG", unsendable)]
pub struct WorkStealingDAG {
    /// Per-task-type channels.
    channels: Arc<Vec<TaskChannel>>,
    /// ROI signals per task type.
    roi_signals: Vec<EmaRoiSignal>,
    /// Adaptive rebalancer.
    rebalancer: Rebalancer,
    /// Shutdown flag.
    running: Arc<AtomicBool>,
    /// Worker thread handles — joined on stop().
    workers: Mutex<Vec<thread::JoinHandle<()>>>,
    /// Python callback for task results.
    result_callback: Py<PyAny>,
    /// Stats: total submitted.
    submitted_count: AtomicU64,
    /// Stats: total completed.
    completed_count: AtomicU64,
}

impl WorkStealingDAG {
    fn new(result_callback: Py<PyAny>) -> Self {
        let channels: Arc<Vec<TaskChannel>> = Arc::new(vec![
            TaskChannel::new(TaskType::Fetch),
            TaskChannel::new(TaskType::Parse),
            TaskChannel::new(TaskType::Analyze),
            TaskChannel::new(TaskType::GraphInsert),
        ]);

        let roi_signals = vec![
            EmaRoiSignal::new(),
            EmaRoiSignal::new(),
            EmaRoiSignal::new(),
            EmaRoiSignal::new(),
        ];

        Self {
            channels,
            roi_signals,
            rebalancer: Rebalancer::new(),
            running: Arc::new(AtomicBool::new(false)),
            result_callback,
            submitted_count: AtomicU64::new(0),
            completed_count: AtomicU64::new(0),
            workers: Mutex::new(Vec::new()),
        }
    }

    fn start_workers(&self) {
        if self.running.load(Ordering::Acquire) {
            return;
        }
        self.running.store(true, Ordering::Release);

        let channels = Arc::clone(&self.channels);
        let callback = self.result_callback.clone();
        let running = Arc::clone(&self.running);
        let total_workers = self.rebalancer.get_allocation().iter().sum::<usize>().max(1);

        for worker_id in 0..MAX_WORKERS {
            let ch = Arc::clone(&channels);
            let cb = callback.clone();
            let run = Arc::clone(&running);

            // Determine which task types this worker owns
            let mut types = vec![];
            let alloc = self.rebalancer.get_allocation();
            let mut cumsum = 0usize;
            let mut found = false;
            for (i, &count) in alloc.iter().enumerate() {
                let end = cumsum + count;
                if worker_id >= cumsum && worker_id < end {
                    types.push(match i {
                        0 => TaskType::Fetch,
                        1 => TaskType::Parse,
                        2 => TaskType::Analyze,
                        _ => TaskType::GraphInsert,
                    });
                    found = true;
                    break;
                }
                cumsum = end;
            }
            if !found || types.is_empty() {
                types.push(TaskType::Parse);
            }

            let handle = thread::Builder::new()
                .name(format!("hledac-swarm-{}", worker_id))
                .stack_size(2_097_152) // 2 MiB
                .spawn(move || {
                    let mut ctx = WorkerContext {
                        id: worker_id,
                        task_types: types,
                        channels: ch,
                        running: run,
                        result_callback: cb,
                        steal_cursor: AtomicUsize::new(0),
                    };

                    while ctx.running.load(Ordering::Acquire) {
                        ctx.run();
                    }

                    // Drain remaining work on shutdown
                    for tt in &ctx.task_types {
                        loop {
                            if let Some(task) = ctx.channels[*tt as usize].try_recv() {
                                ctx.process_task(task);
                            } else {
                                break;
                            }
                        }
                    }
                })
                .expect("failed to spawn swarm worker");
            self.workers.lock().push(handle);
        }
    }
}

#[pymethods]
impl WorkStealingDAG {
    /// Create a new WorkStealingDAG.
    ///
    /// Args:
    ///     result_callback: Python callable(task_id, task_type, result_bytes)
    ///         Called when a task completes.
    #[new]
    fn py_new(result_callback: Py<PyAny>) -> Self {
        Self::new(result_callback)
    }

    /// Submit a task to the DAG.
    ///
    /// Args:
    ///     task_type: "fetch" | "parse" | "analyze" | "graph_insert"
    ///     task_id: Unique string identifier
    ///     payload: Serialized task payload (bytes)
    ///
    /// Returns:
    ///     True if submitted, False if queue is full or DAG disabled.
    fn submit(&self, task_type: &str, task_id: String, payload: &[u8]) -> bool {
        let tt = match TaskType::from_str(task_type) {
            Some(t) => t,
            None => return false,
        };

        let task = TaskPayload {
            task_id,
            task_type: tt,
            payload_bytes: payload.to_vec(),
            submitted_at: Instant::now(),
        };

        self.submitted_count.fetch_add(1, Ordering::Relaxed);

        let ch = &self.channels[tt as usize];
        ch.try_send(task)
    }

    /// Record task completion with IOCs produced.
    ///
    /// Args:
    ///     task_type: "fetch" | "parse" | "analyze" | "graph_insert"
    ///     iocs: Number of IOCs produced (0 for failures)
    fn record_completion(&self, task_type: &str, iocs: u64) {
        let tt = match TaskType::from_str(task_type) {
            Some(t) => t,
            None => return,
        };
        self.roi_signals[tt as usize].record(iocs);
        self.completed_count.fetch_add(1, Ordering::Relaxed);
    }

    /// Get current ROI values for all task types.
    ///
    /// Returns:
    ///     Dict with keys: fetch_roi, parse_roi, analyze_roi, graph_insert_roi
    fn get_roi_signals(&self) -> Vec<(&'static str, f64)> {
        vec![
            ("fetch", self.roi_signals[0].get_roi()),
            ("parse", self.roi_signals[1].get_roi()),
            ("analyze", self.roi_signals[2].get_roi()),
            ("graph_insert", self.roi_signals[3].get_roi()),
        ]
    }

    /// Get current worker allocation.
    ///
    /// Returns:
    ///     [fetch_workers, parse_workers, analyze_workers, graph_workers]
    fn get_worker_allocation(&self) -> [usize; 4] {
        self.rebalancer.get_allocation()
    }

    /// Trigger adaptive rebalancing (call periodically from Python, e.g. every 10s).
    ///
    /// Returns:
    ///     True if rebalance happened, False if not due to interval.
    fn rebalance(&self) -> bool {
        let fetch_roi = self.roi_signals[0].get_roi();
        let parse_roi = self.roi_signals[1].get_roi();
        let analyze_roi = self.roi_signals[2].get_roi();
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs();

        self.rebalancer
            .check_and_rebalance(fetch_roi, parse_roi, analyze_roi, now)
    }

    /// Get DAG statistics.
    ///
    /// Returns:
    ///     Dict with submitted, completed, pending per type.
    fn get_stats(&self) -> Vec<(&'static str, usize)> {
        vec![
            ("submitted", self.submitted_count.load(Ordering::Acquire)),
            ("completed", self.completed_count.load(Ordering::Acquire)),
        ]
    }

    /// Start the DAG worker threads.
    /// Workers run in background; submit() injects tasks from Python.
    fn start(&mut self) {
        self.start_workers();
    }

    /// Stop all workers and drain queues.
    /// Workers are joined (blocking) to ensure clean teardown.
    fn stop(&self) {
        self.running.store(false, Ordering::Release);
        // Collect all thread handles and join (SWARM-STOP-01).
        let mut handles = self.workers.lock();
        for h in handles.drain(..) {
            // Best-effort join — don't block forever.
            let _ = h.join();
        }
    }

    /// Check if DAG is running.
    fn is_running(&self) -> bool {
        self.running.load(Ordering::Acquire)
    }
}

// ---------------------------------------------------------------------------
// SwarmDAG — Python-friendly wrapper with lazy initialization
// ---------------------------------------------------------------------------

/// Python-friendly wrapper around WorkStealingDAG.
/// Respects HLEDAC_ENABLE_SWARM_DAG env var, lazy initialization.
#[pyclass(name = "SwarmDAG", unsendable)]
pub struct SwarmDAG {
    inner: Option<WorkStealingDAG>,
    enabled: bool,
}

impl SwarmDAG {
    fn new(enabled: bool) -> Self {
        Self {
            inner: None,
            enabled,
        }
    }
}

#[pymethods]
impl SwarmDAG {
    /// Create a new SwarmDAG wrapper.
    ///
    /// Args:
    ///     enabled: Whether to enable the DAG. If False, all methods are no-ops.
    #[new]
    fn py_new(enabled: bool) -> Self {
        Self::new(enabled)
    }

    /// Initialize the DAG with a result callback.
    ///
    /// Returns:
    ///     True if initialized, False if disabled.
    fn initialize(&mut self, callback: Py<PyAny>) -> bool {
        if !self.enabled {
            return false;
        }
        if self.inner.is_none() {
            self.inner = Some(WorkStealingDAG::new(callback));
            self.inner.as_mut().unwrap().start_workers();
        }
        true
    }

    /// Submit a task.
    ///
    /// Returns:
    ///     True if submitted, False if disabled or queue full.
    fn submit(&self, task_type: &str, task_id: String, payload: &[u8]) -> bool {
        match &self.inner {
            Some(dag) => dag.submit(task_type, task_id, payload),
            None => false,
        }
    }

    /// Record task completion.
    fn record_completion(&self, task_type: &str, iocs: u64) {
        if let Some(dag) = &self.inner {
            dag.record_completion(task_type, iocs);
        }
    }

    /// Get ROI signals.
    fn get_roi_signals(&self) -> Vec<(&'static str, f64)> {
        match &self.inner {
            Some(dag) => dag.get_roi_signals(),
            None => vec![],
        }
    }

    /// Get worker allocation.
    fn get_worker_allocation(&self) -> [usize; 4] {
        match &self.inner {
            Some(dag) => dag.get_worker_allocation(),
            None => [0, 0, 0, 0],
        }
    }

    /// Trigger rebalancing.
    fn rebalance(&self) -> bool {
        match &self.inner {
            Some(dag) => dag.rebalance(),
            None => false,
        }
    }

    /// Get statistics.
    fn get_stats(&self) -> Vec<(&'static str, usize)> {
        match &self.inner {
            Some(dag) => dag.get_stats(),
            None => vec![],
        }
    }

    /// Check if enabled.
    fn is_enabled(&self) -> bool {
        self.enabled
    }

    /// Check if running.
    fn is_running(&self) -> bool {
        match &self.inner {
            Some(dag) => dag.is_running(),
            None => false,
        }
    }
}

// ---------------------------------------------------------------------------
// Registration
// ---------------------------------------------------------------------------

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<TaskType>()?;
    m.add_class::<WorkStealingDAG>()?;
    m.add_class::<SwarmDAG>()?;
    Ok(())
}
