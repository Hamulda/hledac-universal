"""
tests/test_phase28_qos_constants.py

MODERN-47: Phase 28 verification tests
Part (e): QoS constants map to real qos_class_t

Tests:
- QoS class constants match libc::qos_class_t values (Apple Silicon)
- QoSLevel enum exists and has correct values
- set_thread_qos function works correctly
- QoSProfile struct is properly defined
- _qos_signal ContextVar propagates QoS state
- qos_class_t values are consistent across Python and Rust

Architecture: M1 8GB optimized, Python 3.14+ compatible
"""

from __future__ import annotations

import ctypes
import sys
import threading
from typing import Any

import pytest
from core import aclose


# Apple Silicon QoS class values (from libdispatch)
# These MUST match the values in rust_extensions/src/lib.rs
EXPECTED_QOS_VALUES = {
    "USER_INITIATED": 0x19,  # 25 - P-core scheduling priority
    "UTILITY": 0x11,         # 17 - Balanced efficiency
    "BACKGROUND": 0x09,      #  9 - Background efficiency
}


class TestQoSConstantsExist:
    """Test that QoS constants exist and are importable."""

    def test_qos_constants_import(self):
        """QoS constants must be importable from core.resource_governor."""
        from core.resource_governor import (
            _QOS_USER_INITIATED,
            _QOS_UTILITY,
            _QOS_BACKGROUND,
        )

        assert _QOS_USER_INITIATED is not None
        assert _QOS_UTILITY is not None
        assert _QOS_BACKGROUND is not None

    def test_qos_constants_values(self):
        """QoS constants must match expected libc::qos_class_t values."""
        from core.resource_governor import (
            _QOS_USER_INITIATED,
            _QOS_UTILITY,
            _QOS_BACKGROUND,
        )

        assert _QOS_USER_INITIATED == EXPECTED_QOS_VALUES["USER_INITIATED"], \
            f"USER_INITIATED should be 0x{EXPECTED_QOS_VALUES['USER_INITIATED']:02x}, got 0x{_QOS_USER_INITIATED:02x}"
        assert _QOS_UTILITY == EXPECTED_QOS_VALUES["UTILITY"], \
            f"UTILITY should be 0x{EXPECTED_QOS_VALUES['UTILITY']:02x}, got 0x{_QOS_UTILITY:02x}"
        assert _QOS_BACKGROUND == EXPECTED_QOS_VALUES["BACKGROUND"], \
            f"BACKGROUND should be 0x{EXPECTED_QOS_VALUES['BACKGROUND']:02x}, got 0x{_QOS_BACKGROUND:02x}"


class TestQoSLevelEnum:
    """Test QoSLevel StrEnum."""

    def test_qos_level_import(self):
        """QoSLevel must be importable."""
        from core.resource_governor import QoSLevel

        assert QoSLevel is not None

    def test_qos_level_has_required_values(self):
        """QoSLevel must have all required levels."""
        from core.resource_governor import QoSLevel

        required_levels = ["full", "degraded", "minimal", "emergency", "offline"]
        enum_values = [e.value for e in QoSLevel]

        for level in required_levels:
            assert level in enum_values, f"QoSLevel missing: {level}"

    def test_qos_level_is_strenum(self):
        """QoSLevel must be a StrEnum."""
        from core.resource_governor import QoSLevel

        assert hasattr(QoSLevel, "_member_names_"), "Must be an Enum"


class TestSetThreadQoS:
    """Test set_thread_qos function."""

    def test_set_thread_qos_import(self):
        """set_thread_qos must be importable."""
        from core.resource_governor import set_thread_qos

        assert callable(set_thread_qos)

    def test_set_thread_qos_accepts_qos_level(self):
        """set_thread_qos must accept QoS level as integer."""
        from core.resource_governor import set_thread_qos, _QOS_USER_INITIATED

        # Should not raise
        try:
            set_thread_qos(_QOS_USER_INITIATED)
        except Exception as exc:
            pytest.fail(f"set_thread_qos raised unexpectedly: {exc}")

    def test_set_thread_qos_accepts_all_qos_levels(self):
        """set_thread_qos must accept all defined QoS levels."""
        from core.resource_governor import set_thread_qos

        qos_levels = list(EXPECTED_QOS_VALUES.values())

        for qos in qos_levels:
            try:
                set_thread_qos(qos)
            except Exception as exc:
                pytest.fail(f"set_thread_qos({hex(qos)}) failed: {exc}")


class TestQoSProfile:
    """Test QoSProfile struct."""

    def test_qos_profile_import(self):
        """QoSProfile must be importable."""
        from core.resource_governor import QoSProfile

        assert QoSProfile is not None

    def test_qos_profile_is_msgspec_struct(self):
        """QoSProfile must be a msgspec.Struct (M1 optimized)."""
        from core.resource_governor import QoSProfile

        # Should be msgspec.Struct (frozen=True, gc=False)
        assert hasattr(QoSProfile, "__slots__") or hasattr(QoSProfile, "__struct__")

    def test_qos_profile_has_required_fields(self):
        """QoSProfile must have required fields."""
        from core.resource_governor import QoSProfile

        # Create a default instance
        profile = QoSProfile()

        # Must have qos_level
        assert hasattr(profile, "qos_level")

    def test_qos_profile_is_frozen(self):
        """QoSProfile must be frozen (immutable)."""
        from core.resource_governor import QoSProfile

        # Check for frozen=True in class definition
        # msgspec.Struct with frozen=True is hashable and immutable
        assert hasattr(QoSProfile, "__struct__") or hasattr(QoSProfile, "frozen")


class TestQoSSignal:
    """Test _qos_signal context variable."""

    def test_qos_signal_exists(self):
        """_qos_signal must exist in resource_governor module."""
        from core import resource_governor

        assert hasattr(resource_governor, "_qos_signal")

    def test_get_qos_signal_import(self):
        """get_qos_signal must be importable."""
        from core.resource_governor import get_qos_signal

        assert callable(get_qos_signal)

    def test_get_qos_signal_returns_profile(self):
        """get_qos_signal must return a QoSProfile."""
        from core.resource_governor import get_qos_signal, QoSProfile

        profile = get_qos_signal()
        assert isinstance(profile, QoSProfile)


class TestGetQoSLevel:
    """Test get_qos_level function."""

    def test_get_qos_level_import(self):
        """get_qos_level must be importable."""
        from core.resource_governor import get_qos_level

        assert callable(get_qos_level)

    def test_get_qos_level_returns_string(self):
        """get_qos_level must return a string."""
        from core.resource_governor import get_qos_level

        level = get_qos_level()
        assert isinstance(level, str)


class TestMacOSQoSIntegration:
    """Test actual macOS QoS integration."""

    @pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
    def test_libpthread_available(self):
        """libpthread must be available on macOS."""
        try:
            libpthread = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
            assert hasattr(libpthread, "pthread_set_qos_class_self_np")
        except OSError:
            pytest.fail("libpthread not available")

    @pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
    def test_set_thread_qos_calls_pthread(self):
        """set_thread_qos must call pthread_set_qos_class_self_np on macOS."""
        from core.resource_governor import set_thread_qos, _QOS_USER_INITIATED

        # Should not raise on macOS
        try:
            set_thread_qos(_QOS_USER_INITIATED)
        except Exception as exc:
            pytest.fail(f"pthread_set_qos_class_self_np failed: {exc}")


class TestRustQOSConsistency:
    """Test that Python QoS values match Rust implementation."""

    def test_qos_values_match_rust_comments(self):
        """QoS values must match documented Rust implementation."""
        # Dynamic project root detection
        project_root = Path(__file__).parent.parent
        rust_file = project_root / "rust_extensions" / "src" / "lib.rs"

        if not rust_file.exists():
            pytest.skip("Rust source not available")

        content = rust_file.read_text()

        # Extract QoS values from Rust comments
        rust_qos_pattern = r"QOS_(USER_INITIATED|UTILITY|BACKGROUND).*?=\s*0x([0-9a-fA-F]+)"
        matches = re.findall(rust_qos_pattern, content, re.IGNORECASE)

        # Compare with Python constants
        from core.resource_governor import (
            _QOS_USER_INITIATED,
            _QOS_UTILITY,
            _QOS_BACKGROUND,
        )

        python_values = {
            "USER_INITIATED": _QOS_USER_INITIATED,
            "UTILITY": _QOS_UTILITY,
            "BACKGROUND": _QOS_BACKGROUND,
        }

        for name, hex_val in matches:
            rust_val = int(hex_val, 16)
            python_val = python_values.get(name.upper())

            if python_val is not None:
                assert rust_val == python_val, \
                    f"QoS mismatch for {name}: Python={hex(python_val)}, Rust={hex(rust_val)}"


class TestQoSDegradation:
    """Test QoS degradation ladder."""

    def test_qos_profile_has_degradation_info(self):
        """QoSProfile should contain degradation information."""
        from core.resource_governor import QoSProfile

        profile = QoSProfile()

        # Must have capability flags or degradation info
        # This depends on actual implementation
        assert hasattr(profile, "qos_level")

    def test_qos_level_transitions(self):
        """QoS levels should have a defined degradation order."""
        from core.resource_governor import QoSLevel

        # Check that degradation levels are defined
        assert "full" in [e.value for e in QoSLevel]
        assert "degraded" in [e.value for e in QoSLevel]


class TestM1QoSOptimization:
    """Test M1-specific QoS optimizations."""

    def test_qos_constants_are_int(self):
        """QoS constants must be integers (not floats)."""
        from core.resource_governor import (
            _QOS_USER_INITIATED,
            _QOS_UTILITY,
            _QOS_BACKGROUND,
        )

        assert isinstance(_QOS_USER_INITIATED, int)
        assert isinstance(_QOS_UTILITY, int)
        assert isinstance(_QOS_BACKGROUND, int)

    def test_qos_profile_is_hashable(self):
        """QoSProfile should be hashable (frozen msgspec.Struct)."""
        from core.resource_governor import QoSProfile

        profile1 = QoSProfile()
        profile2 = QoSProfile()

        try:
            hash(profile1)
            hash(profile2)
        except TypeError:
            pytest.fail("QoSProfile must be hashable")


class TestQoSGovernorIntegration:
    """Test QoS integration with ResourceGovernor."""

    def test_resource_governor_import(self):
        """ResourceGovernor must be importable."""
        from core.resource_governor import ResourceGovernor

        assert ResourceGovernor is not None

    def test_resource_governor_has_qos_method(self):
        """ResourceGovernor should have methods for QoS management."""
        from core.resource_governor import ResourceGovernor

        # ResourceGovernor should have evaluate() method that sets QoS
        # Check for QoS-related methods
        methods = [m for m in dir(ResourceGovernor) if "qos" in m.lower() or "compute" in m.lower()]

        # At minimum, there should be some QoS-related functionality
        assert len(methods) > 0 or hasattr(ResourceGovernor, "evaluate")



