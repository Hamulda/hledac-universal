#!/usr/bin/env bash
#
# setup_ramdisk_env.sh — Setup environment for Hledac with ramdisk
# Usage: source scripts/setup_ramdisk_env.sh
#
# Sets:
#   GHOST_RAMDISK      — ramdisk mount point
#   GHOST_DUCKDB_MAX_TEMP  — DuckDB temp limit (2GB on ramdisk)
#   GHOST_DUCKDB_MEMORY    — DuckDB memory (400MB)
#   GHOST_RAMDISK_ACTIVE   — legacy flag (for older code)
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAMDISK_MOUNT=""

# Detect existing ramdisk
detect_ramdisk() {
    # Check /Volumes/RAMDisk (diskutil default)
    if mount | grep -q "on /Volumes/RAMDisk "; then
        RAMDISK_MOUNT="/Volumes/RAMDisk"
        return 0
    fi

    # Check /Volumes/ghost_tmp (legacy)
    if mount | grep -q "on /Volumes/ghost_tmp "; then
        RAMDISK_MOUNT="/Volumes/ghost_tmp"
        return 0
    fi

    # Check /tmp/hledac_ramdisk
    if mount | grep -q "on /tmp/hledac_ramdisk "; then
        RAMDISK_MOUNT="/tmp/hledac_ramdisk"
        return 0
    fi

    return 1
}

# Create ramdisk if not exists
create_ramdisk() {
    echo "Creating 1GB ramdisk..."
    DEVICE=$(hdiutil attach -nomount ram://2097152 2>&1 | tr -d '[:space:]')
    if [[ -z "$DEVICE" ]]; then
        echo "ERROR: hdiutil attach failed"
        return 1
    fi

    diskutil erasevolume HFS+ RAMDisk "$DEVICE" > /dev/null 2>&1
    sleep 1

    RAMDISK_MOUNT=$(mount | grep "RAMDisk" | awk '{print $3}' | head -1)
    if [[ -z "$RAMDISK_MOUNT" ]]; then
        echo "ERROR: Could not detect ramdisk mount point"
        return 1
    fi

    # Create subdirectories
    mkdir -p "$RAMDISK_MOUNT/duckdb_tmp" "$RAMDISK_MOUNT/sockets" "$RAMDISK_MOUNT/warc" "$RAMDISK_MOUNT/arrow" 2>/dev/null

    echo "✓ Ramdisk created at $RAMDISK_MOUNT"
    return 0
}

# Main
main() {
    if ! detect_ramdisk; then
        echo "No ramdisk detected, creating new one..."
        create_ramdisk || return 1
    else
        echo "✓ Ramdisk detected at $RAMDISK_MOUNT"
    fi

    # Ensure subdirectories exist
    mkdir -p "$RAMDISK_MOUNT/duckdb_tmp" "$RAMDISK_MOUNT/sockets" "$RAMDISK_MOUNT/warc" "$RAMDISK_MOUNT/arrow" 2>/dev/null

    # Export environment variables
    export GHOST_RAMDISK="$RAMDISK_MOUNT"
    export GHOST_DUCKDB_MAX_TEMP="2GB"
    export GHOST_DUCKDB_MEMORY="400MB"
    export HLEDAC_RAMDISK_ACTIVE="1"

    echo ""
    echo "=== Environment Configured ==="
    echo "GHOST_RAMDISK=$GHOST_RAMDISK"
    echo "GHOST_DUCKDB_MAX_TEMP=$GHOST_DUCKDB_MAX_TEMP"
    echo "GHOST_DUCKDB_MEMORY=$GHOST_DUCKDB_MEMORY"
    echo ""
    echo "Run sprint with:"
    echo "  cd $SCRIPT_DIR && uv run python -m hledac.universal run --sprint '...' --duration 60"
}

main "$@"