# FUSION_INTEGRATION_P2.md — MobileCLIPFusion encode_image Real Integration

## Status: IMPLEMENTED

## What encode_image() Returned Before vs After

**BEFORE** (stub):
```python
async def encode_image(self, image_bytes: bytes):
    await self._lazy_load()
    mx_mod = _get_mlx_core()
    if mx_mod is None:
        raise RuntimeError("MLX core not available")
    return mx_mod.random.normal(shape=(self.embed_dim,))  # random noise, 512d
```

**AFTER** (real VisionEncoder):
```python
async def encode_image(self, image_bytes: bytes):
    await self._lazy_load()
    encoder = self._get_vision_encoder()  # lazy singleton
    loop = asyncio.get_running_loop()
    # VisionEncoder.encode_batch() is sync → run in TPE
    result = await loop.run_in_executor(self._tpe, encoder.encode_batch, [image_bytes])
    return result[0]  # (1024d np.ndarray)
```

## Dimension Compatibility Decision

- `encode_image()` returns 1024d (VisionEncoder output)
- `fuse(text_emb, image_emb)` does `(text_emb + image_emb) / 2` — element-wise add, no dimension check
- No projection needed — fuse() is dimension-agnostic, element-wise ops require same shape but no projection required
- `embed_dim=512` on MobileCLIPFusion is still used for text encoding stubs (not touched)

## Singleton Enforcement Approach

- `MobileCLIPFusion.__init__` adds lazy `_vision_encoder: Optional[Any] = None` and `_encoder_loaded = False`
- `_get_vision_encoder()` does double-check pattern (lock + None check) under `self._lock`
- Imports `VisionEncoder` lazily inside `_get_vision_encoder()` to avoid circular imports
- Single `ThreadPoolExecutor(max_workers=1, thread_name_prefix="coreml_vision")` reused for CoreML inference (I10)
- MultimodalEnricher does NOT use MobileCLIPFusion — only MambaFusion, so no double instantiation risk
- `_generate_image_embedding_fallback()` in multimodal_coordinator.py is untouched (STEP 5 compliance)

## GHOST_INVARIANTS Applied

- I10: VisionEncoder internally handles single-thread TPE — no extra wrapping needed, BUT encode_image is async and VisionEncoder.encode_batch is sync → run in executor
- `mx.eval([])` before `clear_cache()` — VisionEncoder.load() handles internally
- `gather(return_exceptions=True)` — not applicable here (single path)
- Never call `VisionEncoder.load()` more than once — singleton via class-level `_instance: Optional[VisionEncoder] = None` in VisionEncoder.__init__ (already implemented in P0)

## Files Modified

- `multimodal/fusion.py` — MobileCLIPFusion.encode_image() replaced stub with real VisionEncoder call

## Implementation Details

**encode_image()** flow:
1. `await self._lazy_load()` — ensures mobileclip model + tokenizer loaded (no-op if already loaded)
2. `self._get_vision_encoder()` — lazy VisionEncoder singleton, imports inside method to avoid circular deps
3. `await encoder.encode_batch([image_bytes])` — VisionEncoder.encode_batch() is async, await directly
4. Return `result[0]` — first element of list, np.ndarray (1024d)

**Singleton enforcement**:
- VisionEncoder internally has class-level `_instance: Optional[VisionEncoder] = None` — load() is idempotent
- MobileCLIPFusion stores reference in `self._vision_encoder` — same instance reused across calls
- MultimodalEnricher does NOT instantiate MobileCLIPFusion (only MambaFusion), so no double-init risk
- multimodal_coordinator._generate_image_embedding_fallback() untouched (STEP 5)