#!/bin/zsh
# Phase4: Use system allocator instead of pymalloc on macOS 14+
# Apple's allocator is NUMA-aware for unified memory; pymalloc adds overhead.
# Must be exported BEFORE python starts (env var read at interpreter init).
export PYTHONMALLOC=malloc
export PYTHONMALLOCSTATS=0

# Run the provided command with all arguments
exec python3.14 -m hledac.universal "$@"
