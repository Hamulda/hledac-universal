"""
ISSUE-007: Rust Zombie Module Cleanup Script

This script automates the removal of zombie Rust modules identified by audit.py.

WARNING: This script makes destructive changes. Review before running!

Usage:
    python rust_extensions/cleanup_zombies.py --plan          # Show what would be removed
    python rust_extensions/cleanup_zombies.py --execute       # Execute the cleanup
"""
from __future__ import annotations
import sys
import re
from pathlib import Path
from dataclasses import dataclass
PROJECT_ROOT = Path(__file__).parent.parent
RUST_EXTENSIONS_DIR = PROJECT_ROOT / 'rust_extensions'
LIB_RS_PATH = RUST_EXTENSIONS_DIR / 'src' / 'lib.rs'
CARGO_TOML_PATH = RUST_EXTENSIONS_DIR / 'Cargo.toml'
ZOMBIE_MODULES_SAFE = ['aho_corasick_simd', 'claims_extraction', 'compress', 'consistency_verifier', 'content_hasher', 'crypto_accelerate', 'deobfuscate', 'feed_decision', 'ffi_safe', 'h2_safari_preset', 'health', 'hot_edges_rs', 'html_parse', 'int_counter_layout', 'ioc_extract', 'ioc_extract_fast', 'ioc_extract_simd', 'ioc_stream_scan', 'mpsc_pool', 'query_terms', 'regex_lz4', 'sendfile', 'simdjson_extract', 'simd_similarity', 'simhash_ext', 'spsc_queue', 'telemetry_agg', 'tls_metadata', 'topology', 'tracing', 'unindexed_scanner', 'url_engine', 'warc_parser', 'zero_copy', 'collections_backup']
ZOMBIE_MODULES_WITH_DEPS = ['fulltext_index', 'git_forensics', 'graph_analytics', 'lmdb_dht', 'metal_hashcrack', 'metal_shared_buf', 'native_db', 'nw_connection', 'p2p_harvest', 'pdf', 'office', 'dns_tunnel', 'async_bridge', 'async_query', 'aimd_controller', 'federated_qtable']

@dataclass(slots=True)
class CleanupPlan:
    """Plan for cleaning up zombie modules."""
    files_to_remove: list[Path]
    lib_rs_mods_to_remove: list[str]
    cargo_toml_lines_to_remove: list[int]
    errors: list[str]

def analyze_rust_files() -> set[str]:
    """Find all .rs files in src directory."""
    src_dir = RUST_EXTENSIONS_DIR / 'src'
    rs_files = set()
    for f in src_dir.rglob('*.rs'):
        rel_path = f.relative_to(src_dir)
        if rel_path.name == 'mod.rs':
            module_name = str(rel_path.parent.name)
        else:
            module_name = rel_path.stem
        rs_files.add(module_name)
    return rs_files

def parse_lib_rs() -> tuple[list[str], dict[str, str]]:
    """Parse lib.rs to find all module declarations."""
    content = LIB_RS_PATH.read_text()
    lines = content.splitlines()
    module_declarations = []
    comments = {}
    for i, line in enumerate(lines):
        match = re.match('\\s*mod\\s+(\\w+)\\s*;', line)
        if match:
            module_name = match.group(1)
            module_declarations.append(module_name)
            if i > 0 and '//' in lines[i - 1]:
                comments[module_name] = lines[i - 1].strip()
    return (module_declarations, comments)

def create_cleanup_plan() -> CleanupPlan:
    """Create a cleanup plan for zombie modules."""
    plan = CleanupPlan(files_to_remove=[], lib_rs_mods_to_remove=[], cargo_toml_lines_to_remove=[], errors=[])
    existing_files = analyze_rust_files()
    for module_name in ZOMBIE_MODULES_SAFE:
        rs_file = RUST_EXTENSIONS_DIR / 'src' / f'{module_name}.rs'
        if rs_file.exists():
            plan.files_to_remove.append(rs_file)
        else:
            rs_dir = RUST_EXTENSIONS_DIR / 'src' / module_name
            if rs_dir.exists():
                plan.files_to_remove.append(rs_dir)
        plan.lib_rs_mods_to_remove.append(module_name)
    for module_name in ZOMBIE_MODULES_WITH_DEPS:
        rs_file = RUST_EXTENSIONS_DIR / 'src' / f'{module_name}.rs'
        if rs_file.exists():
            plan.errors.append(f'REQUIRES REVIEW: {module_name} may have external dependencies')
    return plan

def print_plan(plan: CleanupPlan) -> None:
    """Print the cleanup plan."""
    print('\n' + '=' * 80)
    print('ISSUE-007: Rust Zombie Module Cleanup Plan')
    print('=' * 80)
    print(f'\n📁 Files to remove ({len(plan.files_to_remove)}):')
    for f in plan.files_to_remove:
        print(f'   - {f.relative_to(PROJECT_ROOT)}')
    print(f'\n📝 lib.rs module declarations to remove ({len(plan.lib_rs_mods_to_remove)}):')
    for mod_name in plan.lib_rs_mods_to_remove[:10]:
        print(f'   - mod {mod_name};')
    if len(plan.lib_rs_mods_to_remove) > 10:
        print(f'   ... and {len(plan.lib_rs_mods_to_remove) - 10} more')
    if plan.errors:
        print(f'\n⚠️  Manual Review Required ({len(plan.errors)}):')
        for error in plan.errors:
            print(f'   - {error}')
    print(f'\n💡 Estimated compile time savings: 30-40%')
    print('\n' + '=' * 80)

def execute_cleanup(plan: CleanupPlan) -> None:
    """Execute the cleanup plan."""
    print('\n⚠️  EXECUTING CLEANUP - This is DESTRUCTIVE!')
    print('   Files will be permanently deleted.')
    for f in plan.files_to_remove:
        if f.is_dir():
            import shutil
            shutil.rmtree(f)
            print(f'   ✅ Removed directory: {f.name}/')
        elif f.is_file():
            f.unlink()
            print(f'   ✅ Removed file: {f.name}')
    content = LIB_RS_PATH.read_text()
    lines = content.splitlines()
    new_lines = []
    skip_next = 0
    for i, line in enumerate(lines):
        if skip_next > 0:
            skip_next -= 1
            continue
        match = re.match('\\s*mod\\s+(\\w+)\\s*;', line)
        if match:
            module_name = match.group(1)
            if module_name in plan.lib_rs_mods_to_remove:
                if new_lines and '// ZOMBIE:' in new_lines[-1]:
                    new_lines.pop()
                skip_next = 0
                continue
        new_lines.append(line)
    LIB_RS_PATH.write_text('\n'.join(new_lines) + '\n')
    print(f'\n✅ Updated lib.rs')
    print('\n✅ Cleanup complete!')
    print('\nNext steps:')
    print('   1. Run: cargo check')
    print('   2. Review any compilation errors')
    print('   3. Run: cargo build')
    print('   4. Run tests to verify nothing broke')

def main() -> int:
    """Main entry point."""
    import argparse
    parser = argparse.ArgumentParser(description='Rust Zombie Module Cleanup')
    parser.add_argument('--plan', action='store_true', help='Show cleanup plan')
    parser.add_argument('--execute', action='store_true', help='Execute cleanup')
    args = parser.parse_args()
    if not args.plan and (not args.execute):
        parser.print_help()
        return 1
    plan = create_cleanup_plan()
    print_plan(plan)
    if args.execute:
        print('\n❓ Are you sure you want to proceed? (yes/no): ', end='')
        response = input().strip().lower()
        if response == 'yes':
            execute_cleanup(plan)
        else:
            print('Aborted.')
    return 0
if __name__ == '__main__':
    sys.exit(main())