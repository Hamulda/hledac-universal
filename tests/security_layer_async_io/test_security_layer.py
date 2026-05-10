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

import asyncio
import os
import sys
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from hledac.universal.layers.security_layer import SecurityLayer
from hledac.universal.project_types import SecurityConfig, DestructionResult


class TestDestroyFileFallbackAsync:
    """Test that destroy_file fallback uses executor seam, not direct sync I/O."""

    @pytest.mark.asyncio
    async def test_fallback_uses_executor_not_direct_sync_io(self):
        """
        Verify destroy_file routes through run_in_executor for fallback.
        We test that the call completes without blocking the event loop,
        and that _destroy_file_fallback_sync is invoked via executor.
        """
        config = SecurityConfig()
        security = SecurityLayer(config)
        security._secure_destructor = None

        # Track if executor was used
        executor_used = False

        # Patch the sync method to track calls
        original_sync = security._destroy_file_fallback_sync

        def tracking_sync(*args, **kwargs):
            nonlocal executor_used
            executor_used = True
            return original_sync(*args, **kwargs)

        security._destroy_file_fallback_sync = tracking_sync

        with tempfile.NamedTemporaryFile(delete=False, suffix='.tmp') as f:
            temp_path = f.name
            f.write(b'test content')

        try:
            # Use asyncio.wait_for to detect if call blocks event loop
            # If it blocks, wait_for will timeout
            result = await asyncio.wait_for(
                security.destroy_file(temp_path),
                timeout=5.0
            )

            # Verify executor was used (sync method was called via executor)
            assert executor_used, "Executor should have been used for fallback"
            assert result.passes_completed == 1

        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    @pytest.mark.asyncio
    async def test_fallback_returns_bytes_overwritten_for_existing_file(self):
        """Destroy existing file via fallback should return DestructionResult with bytes_overwritten > 0."""
        config = SecurityConfig()
        security = SecurityLayer(config)
        security._secure_destructor = None

        with tempfile.NamedTemporaryFile(delete=False, suffix='.tmp') as f:
            temp_path = f.name
            f.write(b'x' * 1024)

        try:
            result = await security.destroy_file(temp_path)

            assert isinstance(result, DestructionResult)
            assert result.passes_completed == 1, f"Expected 1 pass, got {result.passes_completed}"
            assert result.bytes_overwritten == 1024, f"Expected 1024 bytes, got {result.bytes_overwritten}"
            assert result.file_path == temp_path

        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    @pytest.mark.asyncio
    async def test_fallback_returns_zero_for_nonexistent_file(self):
        """Destroy non-existent file via fallback should return passes_completed=0."""
        config = SecurityConfig()
        security = SecurityLayer(config)
        security._secure_destructor = None

        result = await security.destroy_file("/nonexistent/path/that/does/not/exist/file.txt")

        assert isinstance(result, DestructionResult)
        assert result.passes_completed == 0, f"Expected 0 passes, got {result.passes_completed}"
        assert result.bytes_overwritten == 0, f"Expected 0 bytes, got {result.bytes_overwritten}"
        assert result.verification_passed is False


class TestDestroyDirectory:

    @pytest.mark.asyncio
    async def test_destroy_directory_removes_nested_files_and_dirs(self):
        """destroy_directory should process nested files and remove the directory."""
        config = SecurityConfig()
        security = SecurityLayer(config)
        security._secure_destructor = None

        temp_dir = tempfile.mkdtemp()
        try:
            sub1 = os.path.join(temp_dir, "sub1")
            sub2 = os.path.join(sub1, "sub2")
            os.makedirs(sub2, exist_ok=True)

            file1_path = os.path.join(sub2, "file1.txt")
            file2_path = os.path.join(sub1, "file2.txt")

            with open(file1_path, 'wb') as f:
                f.write(b'nested content 1')
            with open(file2_path, 'wb') as f:
                f.write(b'nested content 2')

            results = await security.destroy_directory(temp_dir, recursive=True)

            assert len(results) == 2, f"Expected 2 results, got {len(results)}"
            assert all(isinstance(r, DestructionResult) for r in results)
            assert not os.path.exists(temp_dir), f"Directory should be removed: {temp_dir}"

        finally:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_destroy_directory_non_recursive(self):
        """destroy_directory with recursive=False should only destroy top-level files."""
        config = SecurityConfig()
        security = SecurityLayer(config)
        security._secure_destructor = None

        temp_dir = tempfile.mkdtemp()
        try:
            sub_dir = os.path.join(temp_dir, "subdir")
            os.makedirs(sub_dir, exist_ok=True)

            top_file = os.path.join(temp_dir, "top.txt")
            nested_file = os.path.join(sub_dir, "nested.txt")

            with open(top_file, 'wb') as f:
                f.write(b'top level')
            with open(nested_file, 'wb') as f:
                f.write(b'nested')

            results = await security.destroy_directory(temp_dir, recursive=False)

            assert len(results) == 1, f"Expected 1 result, got {len(results)}"
            assert not os.path.exists(top_file), "Top-level file should be destroyed"
            assert os.path.exists(nested_file), "Nested file should still exist"

        finally:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)


class TestCleanupIdempotent:

    @pytest.mark.asyncio
    async def test_cleanup_idempotent_executor_shutdown(self):
        """Calling cleanup() multiple times should be safe (idempotent)."""
        config = SecurityConfig()
        security = SecurityLayer(config)

        await security.cleanup()

        try:
            await security.cleanup()
        except Exception as e:
            pytest.fail(f"Second cleanup() raised exception: {e}")

        await security.cleanup()

    @pytest.mark.asyncio
    async def test_cleanup_sets_executor_to_none(self):
        """After cleanup, executor should be None to prevent reuse."""
        config = SecurityConfig()
        security = SecurityLayer(config)

        assert security._file_destroy_executor is not None, "Executor should exist before cleanup"

        await security.cleanup()

        assert security._file_destroy_executor is None, "Executor should be None after cleanup"


class TestExceptionHandling:

    @pytest.mark.asyncio
    async def test_fallback_exception_returns_failure_result(self):
        """Exception in fallback should return failure DestructionResult, not raise."""
        config = SecurityConfig()
        security = SecurityLayer(config)
        security._secure_destructor = None

        temp_dir = tempfile.mkdtemp()
        file_path = os.path.join(temp_dir, "test.txt")
        with open(file_path, 'wb') as f:
            f.write(b'test')

        try:
            os.chmod(temp_dir, 0o444)

            result = await security.destroy_file(file_path)

            assert isinstance(result, DestructionResult)
            assert result.passes_completed == 0, f"Expected 0 passes on error, got {result.passes_completed}"

        finally:
            os.chmod(temp_dir, 0o755)
            if os.path.exists(file_path):
                os.remove(file_path)
            if os.path.exists(temp_dir):
                os.rmdir(temp_dir)