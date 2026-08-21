# _exceptions.py — Rust extension exceptions
"""
RustExtension exceptions for fail-closed behavior.

These exceptions are raised when the Rust extension is detected as stale
or incompatible, ensuring that silent degradation NEVER occurs for a
security-critical tool.
"""

from __future__ import annotations


class RustExtensionError(Exception):
    """Base exception for Rust extension errors."""


class RustExtensionStale(RustExtensionError):
    """
    ISSUE-11: Raised when the Rust extension binary is stale.

    This is a FAIL-CLOSED exception - when raised, the Rust extension
    MUST NOT be used. This ensures that developers are always aware when
    their binary is out of sync with the source code.

    Attributes:
        source_hash: The hash stored in BUILD_MANIFEST at build time.
        current_hash: The hash computed from current source files.
        rebuild_command: The exact command to rebuild the extension.
        reason: Human-readable explanation of why staleness was detected.
    """

    def __init__(
        self,
        message: str | None = None,
        source_hash: str | None = None,
        current_hash: str | None = None,
        rebuild_command: str | None = None,
        reason: str | None = None,
    ) -> None:
        self.source_hash = source_hash
        self.current_hash = current_hash
        self.rebuild_command = rebuild_command
        self.reason = reason or message or "Rust extension is stale"

        detailed_msg = self._build_message()
        super().__init__(detailed_msg)

    def _build_message(self) -> str:
        """Build a detailed error message with all context."""
        lines = [
            "=" * 70,
            "RUST EXTENSION STALE — FAIL-CLOSED",
            "=" * 70,
            "",
            f"Reason: {self.reason}",
            "",
        ]

        if self.source_hash:
            lines.append(f"Build-time hash: {self.source_hash}")
        if self.current_hash:
            lines.append(f"Current hash:    {self.current_hash}")
        if self.source_hash and self.current_hash:
            lines.append(f"Hash changed:     {self.source_hash != self.current_hash}")

        lines.extend(["", "-" * 70, "REBUILD COMMAND:", "-" * 70])

        if self.rebuild_command:
            lines.append(self.rebuild_command)
        else:
            lines.append("# No rebuild command available")
            lines.append("# Try one of:")
            lines.append("  cd rust_extensions && maturin develop --release")
            lines.append("  cargo build --release --manifest-path rust_extensions/Cargo.toml")

        lines.extend(["", "-" * 70, "WHAT HAPPENED:", "-" * 70, ""])
        lines.extend(
            [
                "The Rust extension binary (.so) was compiled from an older version of",
                "the source code. The source files have been modified since the build.",
                "",
                "This is DANGEROUS for a security tool because:",
                "  1. Bug fixes in source are not present in the binary",
                "  2. New security features are missing",
                "  3. Potential ABI incompatibilities with other components",
                "",
                "FIX: Run the rebuild command above, then restart your application.",
                "",
                "=" * 70,
            ]
        )

        return "\n".join(lines)

    def __reduce__(self) -> tuple:
        """Support pickling for multiprocessing."""
        return (
            self.__class__,
            (self.reason, self.source_hash, self.current_hash, self.rebuild_command, self.reason),
        )


class RustExtensionABIError(RustExtensionError):
    """
    Raised when the Rust extension ABI version is incompatible.

    Attributes:
        expected_version: The minimum required ABI version tuple.
        actual_version: The actual ABI version from the extension.
        rebuild_required: True if a rebuild is needed (version too new).
    """

    def __init__(
        self,
        expected_version: tuple[int, int, int],
        actual_version: tuple[int, int, int],
        rebuild_required: bool = False,
    ) -> None:
        self.expected_version = expected_version
        self.actual_version = actual_version
        self.rebuild_required = rebuild_required

        if rebuild_required:
            msg = (
                f"Rust extension ABI version {actual_version} is newer than expected "
                f"{expected_version}. Rebuild required with: "
                f"cd rust_extensions && maturin develop --release"
            )
        else:
            msg = (
                f"Rust extension ABI version {actual_version} is older than required "
                f"{expected_version}. Rebuild required with: "
                f"cd rust_extensions && maturin develop --release"
            )

        super().__init__(msg)


class RustExtensionArchitectureError(RustExtensionError):
    """
    Raised when the Rust extension was built for a different architecture.

    E.g., x86_64 binary on M1 ARM64, or vice versa.
    """

    def __init__(
        self,
        built_for: str | None,
        running_on: str,
        rebuild_command: str | None = None,
    ) -> None:
        self.built_for = built_for
        self.running_on = running_on
        self.rebuild_command = rebuild_command

        msg = f"Rust extension architecture mismatch: built for {built_for}, running on {running_on}. "
        if rebuild_command:
            msg += f"Rebuild with: {rebuild_command}"
        else:
            msg += "Rebuild required for this architecture."

        super().__init__(msg)


class RustExtensionImportError(RustExtensionError):
    """
    Raised when the Rust extension cannot be imported.

    This typically means the extension is not installed or the .so file
    is missing.
    """

    def __init__(self, reason: str | None = None) -> None:
        self.reason = reason
        msg = "Failed to import hledac_rust_extensions"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
