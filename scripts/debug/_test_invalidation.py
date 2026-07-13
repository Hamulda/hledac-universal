#!/usr/bin/env python
"""Debug script for test_invalidation_propagates_through_chain"""
from core.storage_router import StorageRouter, StorageKind, reset_storage_router
from unittest.mock import MagicMock

print("Creating router...")
reset_storage_router()
router = StorageRouter()

print("Registering callbacks...")
hot_callback = MagicMock()
cold_callback = MagicMock()
router.register_invalidation_callback(StorageKind.WARM, hot_callback)
router.register_invalidation_callback(StorageKind.COLD, cold_callback)

print("Calling put...")
result = router.put("key", "value", data_kind="embedding.float32[768]")
print(f"put result: {result}")
print(f"cold_callback called: {cold_callback.called}")
if cold_callback.called:
    print(f"cold_callback call_args: {cold_callback.call_args}")
else:
    print("FAIL: cold_callback was NOT called")
    print(f"WARM subscribers: {router._invalidation_subscribers[StorageKind.WARM]}")
    print(f"COLD subscribers: {router._invalidation_subscribers[StorageKind.COLD]}")
    print(f"Classified policy: {router.classify('embedding.float32[768]')}")
    print(f"Invalidates chain: {StorageKind.WARM} -> {(StorageKind.COLD,)}")
