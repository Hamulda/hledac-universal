# Bounded Resources Audit Report
**Generated:** 2026-07-14T16:38:39.804485+00:00
**Repository:** universal

## 1. Unbounded asyncio.Queue()
**Total:** 36

### unknown (4)
- `tools/bounded_queue_audit.py:5`
  ```python
  1. asyncio.Queue() without maxsize  (unbounded queue → memory leak)
  ```
- `tools/bounded_queue_audit.py:57`
  ```python
  """Find all asyncio.Queue() without maxsize."""
  ```
- `tools/bounded_queue_audit.py:69`
  ```python
  # Match asyncio.Queue() with no maxsize argument
  ```
- `tools/bounded_queue_audit.py:131`
  ```python
  "## 1. Unbounded asyncio.Queue()",
  ```

### test_only (9)
- `tests/test_issue24_lancedb_write_queue.py:38`
  ```python
  queue: asyncio.Queue[tuple[list | None, float]] = asyncio.Queue()
  ```
- `tests/test_lazy_singleton.py:192`
  ```python
  return asyncio.Queue()
  ```
- `tests/archive/probes/probe_8c0/test_bench_event_loop.py:55`
  ```python
  queue: asyncio.Queue = asyncio.Queue()
  ```
- `tests/archive/probes/probe_8c0/test_bench_event_loop.py:224`
  ```python
  q: asyncio.Queue = asyncio.Queue()
  ```
- `tests/archive/probes/probe_f207n_bounded_queue/test_bounded_queue.py:19`
  ```python
  """Audit detects asyncio.Queue() without maxsize in sample code."""
  ```
- `tests/archive/probes/probe_f207n_bounded_queue/test_bounded_queue.py:180`
  ```python
  and "asyncio.Queue()" in r["code"]
  ```
- `tests/archive/probes/probe_f207n_bounded_queue/test_bounded_queue.py:182`
  ```python
  # The old-style unbounded check finds asyncio.Queue() without maxsize in comments
  ```
- `tests/archive/probes/probe_8c/test_lifecycle_convergence.py:172`
  ```python
  engine._batch_queue = asyncio.Queue()
  ```
- `tests/archive/probes/probe_8c/test_lifecycle_convergence.py:245`
  ```python
  engine._batch_queue = asyncio.Queue()
  ```

### external (23)
- `.venv/lib/python3.14/site-packages/uvicorn/lifespan/on.py:40`
  ```python
  self.receive_queue: Queue[LifespanReceiveMessage] = asyncio.Queue()
  ```
- `.venv/lib/python3.14/site-packages/uvicorn/protocols/websockets/websockets_sansio_impl.py:74`
  ```python
  self.queue: asyncio.Queue[ASGIReceiveEvent] = asyncio.Queue()
  ```
- `.venv/lib/python3.14/site-packages/uvicorn/protocols/websockets/wsproto_impl.py:99`
  ```python
  self.queue: asyncio.Queue[WebSocketEvent] = asyncio.Queue()
  ```
- `.venv/lib/python3.14/site-packages/curl_cffi/requests/websockets.py:764`
  ```python
  self._receive_queue: asyncio.Queue[RECV_QUEUE_ITEM] = asyncio.Queue(
  ```
- `.venv/lib/python3.14/site-packages/curl_cffi/requests/websockets.py:767`
  ```python
  self._send_queue: asyncio.Queue[SEND_QUEUE_ITEM] = asyncio.Queue(
  ```
- `.venv/lib/python3.14/site-packages/transformers/generation/streamers.py:281`
  ```python
  self.text_queue = asyncio.Queue()
  ```
- `.venv/lib/python3.14/site-packages/transformers/cli/serving/transcription.py:171`
  ```python
  queue: asyncio.Queue = asyncio.Queue()
  ```
- `.venv/lib/python3.14/site-packages/transformers/cli/serving/model_manager.py:337`
  ```python
  queue: asyncio.Queue[str | None] = asyncio.Queue()
  ```
- `.venv/lib/python3.14/site-packages/transformers/cli/serving/utils.py:786`
  ```python
  queue: asyncio.Queue = asyncio.Queue()
  ```
- `.venv/lib/python3.14/site-packages/transformers/cli/serving/utils.py:907`
  ```python
  text_queue: asyncio.Queue = asyncio.Queue()
  ```
- `.venv/lib/python3.14/site-packages/mlx_audio/sts/voice_pipeline.py:592`
  ```python
  self.input_audio_queue: asyncio.Queue[np.ndarray] = asyncio.Queue(
  ```
- `.venv/lib/python3.14/site-packages/mlx_audio/sts/voice_pipeline.py:595`
  ```python
  self.transcript_queue: asyncio.Queue[str] = asyncio.Queue()
  ```
- `.venv/lib/python3.14/site-packages/mlx_audio/sts/voice_pipeline.py:596`
  ```python
  self.output_audio_queue: asyncio.Queue[np.ndarray] = asyncio.Queue(
  ```
- `.venv-test/lib/python3.14/site-packages/litellm/constants.py:299`
  ```python
  # Bounds asyncio.Queue() instances (log queues, spend update queues, etc.) to prevent unbounded memory growth
  ```
- `.venv-test/lib/python3.14/site-packages/litellm/integrations/gcs_bucket/gcs_bucket.py:50`
  ```python
  self.log_queue: asyncio.Queue[GCSLogQueueItem] = asyncio.Queue(  # type: ignore[assignment]
  ```
- `.venv-test/lib/python3.14/site-packages/litellm/proxy/db/db_transaction_queue/spend_update_queue.py:25`
  ```python
  self.update_queue: asyncio.Queue[SpendUpdateQueueItem] = asyncio.Queue(
  ```
- `.venv-test/lib/python3.14/site-packages/curl_cffi/requests/websockets.py:763`
  ```python
  self._receive_queue: asyncio.Queue[RECV_QUEUE_ITEM] = asyncio.Queue(
  ```
- `.venv-test/lib/python3.14/site-packages/curl_cffi/requests/websockets.py:766`
  ```python
  self._send_queue: asyncio.Queue[SEND_QUEUE_ITEM] = asyncio.Queue(
  ```
- `.venv-test/lib/python3.14/site-packages/transformers/generation/streamers.py:297`
  ```python
  self.text_queue = asyncio.Queue()
  ```
- `.venv-test/lib/python3.14/site-packages/transformers/cli/serving/transcription.py:171`
  ```python
  queue: asyncio.Queue = asyncio.Queue()
  ```
- `.venv-test/lib/python3.14/site-packages/transformers/cli/serving/model_manager.py:309`
  ```python
  queue: asyncio.Queue[str | None] = asyncio.Queue()
  ```
- `.venv-test/lib/python3.14/site-packages/transformers/cli/serving/utils.py:763`
  ```python
  queue: asyncio.Queue = asyncio.Queue()
  ```
- `.venv-test/lib/python3.14/site-packages/transformers/cli/serving/utils.py:884`
  ```python
  text_queue: asyncio.Queue = asyncio.Queue()
  ```

## 2. Unbounded @lru_cache(maxsize=None)
**Total:** 77

### external (77)
- `.venv/lib/python3.14/site-packages/pip_requirements_parser.py:1919`
  ```python
  @functools.lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_audio/dsp.py:39`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_audio/dsp.py:53`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_audio/dsp.py:67`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_audio/dsp.py:81`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_audio/dsp.py:499`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/charset_normalizer/md.py:217`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/tomli/_re.py:98`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:20`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:73`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:168`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:212`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:244`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:296`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:372`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:428`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:539`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:617`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:811`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:886`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:974`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:1536`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:1587`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:1732`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:1801`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:1866`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:2019`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:2301`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:2436`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:2540`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:2694`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:2819`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:2987`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:3139`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:3352`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:3537`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:3555`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:3560`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:3598`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:3625`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/turboquant.py:3677`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/pyright/utils.py:62`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/pyright/node.py:192`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/asttokens/util.py:421`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/lancedb/dependencies.py:187`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/pip_api/_vendor/tomli/_re.py:87`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/setuptools/_vendor/tomli/_re.py:98`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/hypothesis/internal/scrutineer.py:21`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/hypothesis/strategies/_internal/datetime.py:342`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/models/rope_utils.py:74`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/models/rope_utils.py:186`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/models/rope_utils.py:336`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/models/rope_utils.py:361`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/models/qwen3_5/language.py:436`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/models/qwen3_5/language.py:451`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/models/qwen3_5/language.py:1148`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/models/qwen3_5/language.py:1160`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/models/qwen3_5/language.py:1174`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_vlm/models/bonsai/klein_fast/blocks.py:494`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/pip/_vendor/tomli/_re.py:95`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/pip/_vendor/pkg_resources/__init__.py:433`
  ```python
  @functools.lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/pip/_vendor/pkg_resources/__init__.py:2629`
  ```python
  @functools.lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/torch/fx/experimental/symbolic_shapes.py:8768`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/torch/onnx/_internal/torchscript_exporter/registration.py:144`
  ```python
  # TODO(justinchuby): Add @functools.lru_cache(maxsize=None) if lookup time becomes
  ```
- `.venv/lib/python3.14/site-packages/tvm_ffi/cpp/dtype.py:62`
  ```python
  @functools.lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/tvm_ffi/stub/lib_state.py:33`
  ```python
  @functools.lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/tvm_ffi/stub/lib_state.py:111`
  ```python
  @functools.lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/huggingface_hub/inference/_providers/hf_inference.py:165`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/huggingface_hub/inference/_providers/_common.py:337`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv/lib/python3.14/site-packages/mlx_audio/tts/models/kokoro/pipeline.py:27`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv-test/lib/python3.14/site-packages/lancedb/dependencies.py:186`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv-test/lib/python3.14/site-packages/openai/_base_client.py:2140`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv-test/lib/python3.14/site-packages/setuptools/_vendor/tomli/_re.py:97`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv-test/lib/python3.14/site-packages/torch/fx/experimental/symbolic_shapes.py:8506`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv-test/lib/python3.14/site-packages/torch/onnx/_internal/torchscript_exporter/registration.py:144`
  ```python
  # TODO(justinchuby): Add @functools.lru_cache(maxsize=None) if lookup time becomes
  ```
- `.venv-test/lib/python3.14/site-packages/huggingface_hub/inference/_providers/hf_inference.py:165`
  ```python
  @lru_cache(maxsize=None)
  ```
- `.venv-test/lib/python3.14/site-packages/huggingface_hub/inference/_providers/_common.py:342`
  ```python
  @lru_cache(maxsize=None)
  ```
