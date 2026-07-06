#!/bin/bash
cd /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal
uv run --no-project pytest tests/test_rust_backend.py -v --timeout=120 2>&1 | grep -E "PASSED|FAILED|assert|Error" | head -60
