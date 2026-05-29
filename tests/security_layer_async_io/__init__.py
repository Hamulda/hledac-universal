"""
Hermetic probe tests for SecurityLayer async file I/O fixes.

Tests verify that:
1. destroy_file fallback uses ThreadPoolExecutor, not direct sync I/O in async path
2. destroy_file returns correct DestructionResult for existing files
3. destroy_file returns passes_completed=0 for non-existent files
4. destroy_directory processes nested files and removes directory
5. cleanup() is idempotent for executor shutdown
6. Exceptions in fallback return failure DestructionResult, not unhandled
"""
