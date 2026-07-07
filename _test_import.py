#!/usr/bin/env python
print('Starting...')
import sys
sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal')
from core.storage_router import StorageRouter, StorageKind, reset_storage_router
print('Import OK')
reset_storage_router()
router = StorageRouter()
print('Router created')
