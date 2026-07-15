#!/bin/bash
# Run import test with clean PATH to avoid ImageMagick 'import' collision
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
cd /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal
uv run python _test_imports.py
