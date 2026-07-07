#!/bin/bash
cd /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal
PYTHONPATH=. timeout 20 .venv/bin/python -m pytest tests/test_storage_router.py::TestInvalidationChain::test_invalidation_propagates_through_chain -v --tb=short 2>&1
echo "Exit: $?"
