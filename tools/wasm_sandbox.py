"""
WASM Sandbox - WebAssembly Secure Execution Environment
======================================================

Secure WASM execution with fuel limits, epoch interruption,
resource management, and WASI socket API for protocol parsing.

NEXUS-018-009: WASI sock_open/sock_send/sock_recv host functions enable
WASM modules to perform TCP connections — essential for parsing unknown
network protocols (VPN, IoT firmware, darknet RPC) in custom WASM parsers.

Architecture:
  - WasmWasiLinker: Manages fd→socket mapping and provides WASI host funcs
  - WasmSandbox: Sandbox execution (fuel + epoch isolation)
  - WasmSandbox.with_wasi(): Factory for WASI-capable sandbox

M1 8GB bounds:
  - Max 3 concurrent sockets per WASM instance
  - Socket buffer: 64 KiB (matches WASI preview1 recommendation)
  - TCP connect timeout: 10 s
  - Socket lifetime bound to sandbox instance
"""

import asyncio
import logging
import socket
import struct
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_WASMTIME_AVAILABLE = False
try:
    import wasmtime
    from wasmtime import Config, Engine, Func, Instance, Linker, Module, Store

    _WASMTIME_AVAILABLE = True
except ImportError:
    wasmtime = None
    Config = None
    Engine = None
    Func = None
    Instance = None
    Linker = None
    Module = None
    Store = None

# ── WASI constants (wasi_snapshot_preview1) ─────────────────────────────────
# Subset needed for TCP socket operations in protocol parsers.
_WASI_ERRNO_SUCCESS: int = 0
_WASI_ERRNO_NOTSUP: int = 58
_WASI_ERRNO_INVAL: int = 28
_WASI_ERRNO_AGAIN: int = 6
_WASI_ERRNO_CONNRESET: int = 49
_WASI_ERRNO_CONNREFUSED: int = 42
_WASI_ERRNO_NETUNREACH: int = 73
_WASI_ERRNO_TIMEDOUT: int = 46
_WASI_ERRNO_IO: int = 29
_WASI_ERRNO_NOMEM: int = 33
_WASI_ERRNO_BADF: int = 8
_WASI_ERRNO_NOTCONN: int = 45
_WASI_ERRNO_ACCES: int = 13  # EACCES — permission denied (SSRF guard)

# WASI address families
_WASI_AF_INET: int = 0
_WASI_AF_INET6: int = 1

# WASI socket types
_WASI_SOCK_DGRAM: int = 0
_WASI_SOCK_STREAM: int = 1

# M1 8GB bounds
_WASI_MAX_SOCKETS: int = 3
_WASI_SOCK_BUF_SIZE: int = 65536  # 64 KiB
_WASI_CONNECT_TIMEOUT: float = 10.0

# ── WASI Socket Linker ────────────────────────────────────────────────────────


class WasmWasiLinker:
    """Per-instance WASI socket host function provider.

    Manages fd→socket mapping for WASM modules that need TCP networking.
    Each ``WasmWasiLinker`` instance owns its socket lifecycle — sockets
    are closed when the linker is destroyed (or sandbox execution ends).

    Provides three WASI host functions via ``wasmtime.Func``:
      - ``sock_open``: Create TCP socket, return fd (1-3)
      - ``sock_send``: Send bytes on fd, return count sent
      - ``sock_recv``: Receive bytes on fd, return (data, count)

    M1 8GB bounded: max 3 concurrent sockets per WASM instance.
    All fd operations are synchronous (called from the WASM guest
    which already runs in a thread pool via ``run_in_executor``).
    """

    __slots__ = ("_sockets", "_lock")

    def __init__(self) -> None:
        self._sockets: dict[int, socket.socket] = {}
        self._lock = threading.Lock()

    # ── WASI host function implementations ──────────────────────────────

    def _host_sock_open(
        self,
        caller: Any,
        af: int,
        socktype: int,
        fd_ptr: int,
    ) -> int:
        """WASI sock_open(af, socktype) -> (errno, fd).

        Creates a TCP socket (SOCK_STREAM only; SOCK_DGRAM → ENOTSUP).
        Writes the allocated fd into guest memory at ``fd_ptr``.

        Returns 0 on success, WASI errno on failure.
        """
        if socktype != _WASI_SOCK_STREAM:
            return _WASI_ERRNO_NOTSUP
        if af not in (_WASI_AF_INET, _WASI_AF_INET6):
            return _WASI_ERRNO_INVAL

        with self._lock:
            if len(self._sockets) >= _WASI_MAX_SOCKETS:
                return _WASI_ERRNO_NOMEM
            # Find next available fd in 1.._WASI_MAX_SOCKETS
            for fd in range(1, _WASI_MAX_SOCKETS + 1):
                if fd not in self._sockets:
                    break
            else:
                return _WASI_ERRNO_NOMEM

            try:
                family = socket.AF_INET if af == _WASI_AF_INET else socket.AF_INET6
                sock = socket.socket(family, socket.SOCK_STREAM)
                sock.settimeout(_WASI_CONNECT_TIMEOUT)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                # macOS: TCP_KEEPALIVE = 0x10 (SOL_SOCKET/SO_KEEPALIVE covers the rest)
                self._sockets[fd] = sock
            except OSError:
                return _WASI_ERRNO_IO

        # Write fd to guest memory (4 bytes little-endian at fd_ptr)
        try:
            mem = caller.get("memory")
            if mem is not None:
                buf = struct.pack("<I", fd)
                mem.write(caller, fd_ptr, buf)
        except Exception:
            return _WASI_ERRNO_IO

        return _WASI_ERRNO_SUCCESS

    def _host_sock_connect(
        self,
        caller: Any,
        fd: int,
        addr_ptr: int,
        addr_len: int,
    ) -> int:
        """WASI sock_connect(fd, addr_ptr, addr_len) -> errno.

        Connects the socket to a remote TCP endpoint.
        The address at ``addr_ptr`` is a packed struct:
          - 2 bytes: address family (0=INET, 1=INET6)
          - 2 bytes: port (big-endian, network byte order)
          - 4 bytes: IPv4 address (big-endian, for INET)
          - 16 bytes: IPv6 address (for INET6)

        Returns 0 on success, WASI errno on failure.
        """
        if fd not in self._sockets:
            return _WASI_ERRNO_BADF

        sock = self._sockets[fd]

        if addr_len < 8:
            return _WASI_ERRNO_INVAL

        try:
            mem = caller.get("memory")
            if mem is None:
                return _WASI_ERRNO_IO
            addr_bytes = mem.read(caller, addr_ptr, addr_len)

            # Parse address family (2 bytes LE per WASI convention)
            af = struct.unpack("<H", addr_bytes[0:2])[0]
            port = struct.unpack(">H", addr_bytes[2:4])[0]

            if af == _WASI_AF_INET and addr_len >= 8:
                # IPv4: next 4 bytes in big-endian
                ip_bytes = addr_bytes[4:8]
                host = socket.inet_ntop(socket.AF_INET, ip_bytes)
            elif af == _WASI_AF_INET6 and addr_len >= 20:
                # IPv6: next 16 bytes in big-endian
                ip_bytes = addr_bytes[4:20]
                host = socket.inet_ntop(socket.AF_INET6, ip_bytes)
            else:
                return _WASI_ERRNO_INVAL

            if self._is_blocked_address(host):
                logger.warning(f"[wasm] SSRF guard blocked connect to {host}:{port}")
                return _WASI_ERRNO_ACCES

            sock.connect((host, port))
            return _WASI_ERRNO_SUCCESS
        except TimeoutError:
            return _WASI_ERRNO_TIMEDOUT
        except ConnectionRefusedError:
            return _WASI_ERRNO_CONNREFUSED
        except OSError:
            return _WASI_ERRNO_IO

    def _is_blocked_address(self, host: str) -> bool:
        """SSRF guard: True if ``host`` resolves to a non-public address.

        Blocks loopback, private, link-local (incl. 169.254.169.254 cloud
        metadata), CGNAT, reserved, multicast and unspecified ranges so a
        guest module cannot pivot to internal services.
        """
        import ipaddress

        try:
            infos = socket.getaddrinfo(host, None)
        except Exception:
            # Unresolvable / unexpected — fail closed.
            return True
        for info in infos:
            try:
                ip = info[4][0]
                addr = ipaddress.ip_address(ip)
            except Exception:
                return True
            if (
                addr.is_private
                or addr.is_loopback
                or addr.is_link_local
                or addr.is_reserved
                or addr.is_multicast
                or addr.is_unspecified
            ):
                return True
            if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
                if addr.ipv4_mapped.is_private:
                    return True
        return False

    def _host_sock_send(
        self,
        caller: Any,
        fd: int,
        ri_data_ptr: int,
        ri_data_len: int,
        si_flags: int,
        so_data_len_ptr: int,
    ) -> int:
        """WASI sock_send(fd, ri_data, si_flags) -> (errno, so_data_len).

        Sends data from guest memory at ``ri_data_ptr``.
        Writes count of bytes sent into guest memory at ``so_data_len_ptr``.
        """
        if fd not in self._sockets:
            return _WASI_ERRNO_BADF

        sock = self._sockets[fd]
        try:
            mem = caller.get("memory")
            if mem is None:
                return _WASI_ERRNO_IO
            data = mem.read(caller, ri_data_ptr, ri_data_len)
            sent = sock.send(data)
            # Write sent count (4 bytes little-endian)
            mem.write(caller, so_data_len_ptr, struct.pack("<I", sent))
            return _WASI_ERRNO_SUCCESS
        except TimeoutError:
            return _WASI_ERRNO_TIMEDOUT
        except ConnectionResetError:
            return _WASI_ERRNO_CONNRESET
        except OSError:
            return _WASI_ERRNO_IO

    def _host_sock_recv(
        self,
        caller: Any,
        fd: int,
        ri_data_ptr: int,
        ri_data_len: int,
        ri_flags: int,
        ro_data_len_ptr: int,
        ro_flags_ptr: int,
    ) -> int:
        """WASI sock_recv(fd, ri_data_len, ri_flags) -> (errno, ro_data, ro_data_len, ro_flags).

        Receives data into guest memory at ``ri_data_ptr``.
        Writes count received at ``ro_data_len_ptr`` and flags at ``ro_flags_ptr``.
        """
        if fd not in self._sockets:
            return _WASI_ERRNO_BADF

        sock = self._sockets[fd]
        try:
            mem = caller.get("memory")
            if mem is None:
                return _WASI_ERRNO_IO
            recv_len = min(ri_data_len, _WASI_SOCK_BUF_SIZE)
            data = sock.recv(recv_len)
            if data:
                mem.write(caller, ri_data_ptr, data)
            mem.write(caller, ro_data_len_ptr, struct.pack("<I", len(data)))
            mem.write(caller, ro_flags_ptr, struct.pack("<H", 0))
            return _WASI_ERRNO_SUCCESS
        except TimeoutError:
            return _WASI_ERRNO_TIMEDOUT
        except ConnectionResetError:
            return _WASI_ERRNO_CONNRESET
        except BlockingIOError:
            return _WASI_ERRNO_AGAIN
        except OSError:
            return _WASI_ERRNO_IO

    # ── Linker registration ─────────────────────────────────────────────

    def build_linker(self, engine: Engine, store: Store) -> Linker:
        """Create and configure a ``wasmtime.Linker`` with WASI socket host funcs.

        Registers ``wasi_snapshot_preview1`` imports for ``sock_open``,
        ``sock_send``, and ``sock_recv``. Other WASI imports remain
        unimplemented (module instantiation will fail if the WASM guest
        imports them).

        Args:
            engine: wasmtime Engine
            store: wasmtime Store (used for Func callback context)

        Returns:
            Configured Linker ready for ``linker.instantiate(store, module)``.
        """
        if not _WASMTIME_AVAILABLE:
            raise RuntimeError("wasmtime not available")

        linker = Linker(engine)

        # sock_open(af: i32, socktype: i32, fd_ptr: i32) -> (errno: i32)
        linker.define_func(
            "wasi_snapshot_preview1",
            "sock_open",
            Func(
                store,
                [wasmtime.ValType.i32(), wasmtime.ValType.i32(), wasmtime.ValType.i32(), wasmtime.ValType.i32()],
                [wasmtime.ValType.i32()],
                self._host_sock_open,
            ),
        )

        # sock_connect(fd: i32, addr_ptr: i32, addr_len: i32) -> (errno: i32)
        linker.define_func(
            "wasi_snapshot_preview1",
            "sock_connect",
            Func(
                store,
                [wasmtime.ValType.i32(), wasmtime.ValType.i32(), wasmtime.ValType.i32(), wasmtime.ValType.i32()],
                [wasmtime.ValType.i32()],
                self._host_sock_connect,
            ),
        )

        # sock_send(fd: i32, ri_data_ptr: i32, ri_data_len: i32,
        #           si_flags: i32, so_data_len_ptr: i32) -> (errno: i32)
        linker.define_func(
            "wasi_snapshot_preview1",
            "sock_send",
            Func(
                store,
                [
                    wasmtime.ValType.i32(),
                    wasmtime.ValType.i32(),
                    wasmtime.ValType.i32(),
                    wasmtime.ValType.i32(),
                    wasmtime.ValType.i32(),
                    wasmtime.ValType.i32(),
                ],
                [wasmtime.ValType.i32()],
                self._host_sock_send,
            ),
        )

        # sock_recv(fd: i32, ri_data_ptr: i32, ri_data_len: i32,
        #           ri_flags: i32, ro_data_len_ptr: i32, ro_flags_ptr: i32)
        #   -> (errno: i32)
        linker.define_func(
            "wasi_snapshot_preview1",
            "sock_recv",
            Func(
                store,
                [
                    wasmtime.ValType.i32(),
                    wasmtime.ValType.i32(),
                    wasmtime.ValType.i32(),
                    wasmtime.ValType.i32(),
                    wasmtime.ValType.i32(),
                    wasmtime.ValType.i32(),
                    wasmtime.ValType.i32(),
                ],
                [wasmtime.ValType.i32()],
                self._host_sock_recv,
            ),
        )

        return linker

    def close_all(self) -> None:
        """Close all open sockets. Idempotent — safe to call repeatedly."""
        with self._lock:
            for _fd, sock in list(self._sockets.items()):
                try:
                    sock.close()
                except OSError:  # noqa: BLE001
                    pass
            self._sockets.clear()

    @property
    def socket_count(self) -> int:
        """Number of currently open sockets."""
        with self._lock:
            return len(self._sockets)


# ── WasmSandbox (updated with WASI support) ──────────────────────────────────


class WasmSandbox:
    """
    Secure WebAssembly execution sandbox.

    Features:
        - Fuel consumption tracking
        - Epoch-based interruption
        - Timeout enforcement
        - Resource limits
        - WASI socket API (NEXUS-018-009): sock_open/sock_send/sock_recv

    Use ``WasmSandbox.with_wasi()`` factory for WASI-capable sandboxes
    that can execute WASM protocol parsers with TCP access.
    """

    DEFAULT_FUEL_LIMIT = 1000000
    DEFAULT_EPOCH_DEADLINE = 30
    DEFAULT_TIMEOUT = 60
    __slots__ = (
        "_config",
        "_engine",
        "_epoch_ticker",
        "_epoch_ticker_running",
        "_lock",
        "_running_instances",
        "cache_dir",
        "epoch_deadline",
        "fuel_limit",
        "timeout",
        "_enable_wasi",
        "_wasi_linker",
    )

    def __init__(
        self,
        fuel_limit: int = DEFAULT_FUEL_LIMIT,
        epoch_deadline: float = DEFAULT_EPOCH_DEADLINE,
        timeout: float = DEFAULT_TIMEOUT,
        cache_dir: Path | None = None,
        *,
        enable_wasi: bool = False,
    ) -> None:
        """
        Initialize WASM sandbox.

        Args:
            fuel_limit: Maximum fuel units per execution
            epoch_deadline: Epoch interruption deadline in seconds
            timeout: Overall execution timeout in seconds
            cache_dir: Directory for module caching
            enable_wasi: Enable WASI socket API (sock_open/sock_send/sock_recv).
                When True, WASM modules can create TCP connections for
                protocol parsing. Max 3 sockets, 10 s connect timeout.
        """
        self.fuel_limit = fuel_limit
        self.epoch_deadline = epoch_deadline
        self.timeout = timeout
        self.cache_dir = cache_dir
        self._enable_wasi = enable_wasi
        self._wasi_linker: WasmWasiLinker | None = WasmWasiLinker() if enable_wasi else None
        self._engine: Engine | None = None
        self._config: Config | None = None
        self._epoch_ticker: threading.Thread | None = None
        self._epoch_ticker_running = False
        self._running_instances: set[int] = set()
        self._lock = threading.Lock()
        if _WASMTIME_AVAILABLE:
            self._init_engine()
            self._start_epoch_ticker()
        logger.info(
            "WasmSandbox initialized: fuel=%s, epoch=%ss, timeout=%ss, wasi=%s",
            fuel_limit,
            epoch_deadline,
            timeout,
            enable_wasi,
        )

    @classmethod
    def with_wasi(
        cls,
        fuel_limit: int = DEFAULT_FUEL_LIMIT,
        epoch_deadline: float = DEFAULT_EPOCH_DEADLINE,
        timeout: float = DEFAULT_TIMEOUT,
        cache_dir: Path | None = None,
    ) -> WasmSandbox:
        """Factory for a WASI-capable sandbox.

        Convenience constructor that sets ``enable_wasi=True``.
        Equivalent to ``WasmSandbox(..., enable_wasi=True)``.

        Use this when you need WASM modules to make TCP connections
        for protocol parsing (NEXUS-018-005 / NEXUS-018-009).
        """
        return cls(
            fuel_limit=fuel_limit,
            epoch_deadline=epoch_deadline,
            timeout=timeout,
            cache_dir=cache_dir,
            enable_wasi=True,
        )

    @property
    def wasi_enabled(self) -> bool:
        """True if WASI socket API is enabled on this sandbox."""
        return self._enable_wasi and self._wasi_linker is not None

    def _init_engine(self) -> None:
        """Initialize WASM engine with fuel and epoch settings."""
        if not _WASMTIME_AVAILABLE:
            return
        try:
            self._config = Config()
            self._config.consume_fuel(True)
            self._config.epoch_interruption(True)
            self._engine = Engine(self._config)
            logger.debug("WASM engine initialized")
        except Exception as e:
            logger.error(f"Failed to initialize WASM engine: {e}")
            self._engine = None

    def _start_epoch_ticker(self) -> None:
        """Start background epoch ticker thread."""
        if not _WASMTIME_AVAILABLE:
            return
        self._epoch_ticker_running = True
        self._epoch_ticker = threading.Thread(
            target=self._epoch_ticker_loop,
            daemon=True,
            name="wasm-epoch-ticker",
        )
        self._epoch_ticker.start()
        logger.debug("Epoch ticker started")

    def _epoch_ticker_loop(self) -> None:
        """Background loop that advances the engine epoch.

        wasmtime's epoch interruption only fires when ``engine.increment_epoch()``
        is called after a store deadline is set; previously this loop only slept,
        so runaway guest loops were never interrupted.
        """
        while self._epoch_ticker_running:
            try:
                time.sleep(self.epoch_deadline / 3)
                if self._engine is not None:
                    self._engine.increment_epoch()
            except Exception as e:
                logger.debug(f"Epoch ticker error: {e}")

    def is_available(self) -> bool:
        """Check if WASM runtime is available."""
        return _WASMTIME_AVAILABLE and self._engine is not None

    async def run_async(
        self,
        wasm_bytes: bytes,
        function_name: str = "run",
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Run WASM module asynchronously with timeout and fuel limits.

        Args:
            wasm_bytes: WASM module bytecode
            function_name: Function to execute
            args: Function arguments

        Returns:
            Dict with 'success', 'result', 'fuel_used', 'error'
        """
        if not self.is_available():
            return {
                "success": False,
                "result": None,
                "fuel_used": 0,
                "error": "WASM runtime not available",
            }
        result: dict[str, Any] = {
            "success": False,
            "result": None,
            "fuel_used": 0,
            "error": None,
        }
        try:
            loop = asyncio.get_running_loop()
            async with asyncio.timeout(self.timeout):
                result = await loop.run_in_executor(
                    None,
                    self._run_sync,
                    wasm_bytes,
                    function_name,
                    args,
                )
        except TimeoutError:
            result["error"] = f"Execution timeout ({self.timeout}s)"
            logger.warning(
                "WASM execution timeout: %s (%.1fs)",
                function_name,
                self.timeout,
            )
        except Exception as e:
            result["error"] = str(e)
            logger.error(f"WASM execution error: {e}")
        finally:
            # NEXUS-018-009: Close sockets after each execution.
            # WASM guest sockets don't persist across runs — each
            # run_async call creates fresh sockets via sock_open.
            if self._wasi_linker is not None:
                self._wasi_linker.close_all()
        return result

    def _run_sync(
        self,
        wasm_bytes: bytes,
        function_name: str,
        args: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """
        Synchronous WASM execution with fuel tracking.

        NEXUS-018-009: When ``enable_wasi=True``, uses ``WasmWasiLinker``
        to instantiate the module with WASI socket host functions instead
        of the default empty-imports path.

        This runs in a thread pool to avoid blocking.
        """
        if not _WASMTIME_AVAILABLE:
            return {
                "success": False,
                "result": None,
                "fuel_used": 0,
                "error": "wasmtime not available",
            }
        result: dict[str, Any] = {
            "success": False,
            "result": None,
            "fuel_used": 0,
            "error": None,
        }
        store = None
        instance = None
        try:
            assert self._engine is not None, "Engine not initialized"
            store = Store(self._engine)
            store.set_fuel(self.fuel_limit)
            store.set_epoch_deadline(int(self.epoch_deadline))
            instance_id = id(store)
            with self._lock:
                self._running_instances.add(instance_id)
            module = Module(self._engine, wasm_bytes)

            # NEXUS-018-009: Use WASI linker when enabled
            if self._enable_wasi and self._wasi_linker is not None:
                linker = self._wasi_linker.build_linker(self._engine, store)
                instance = linker.instantiate(store, module)
            else:
                instance = Instance(store, module, [])

            if function_name in instance.exports(store):
                func = instance.exports(store)[function_name]
                if args:
                    func(**args)
                else:
                    func()
                fuel_remaining = store.get_fuel()
                result["fuel_used"] = self.fuel_limit - fuel_remaining
                result["success"] = True
                result["result"] = True
            else:
                result["error"] = f"Function '{function_name}' not found"
        except wasmtime.RuntimeError as e:
            err_str = str(e).lower()
            # NEXUS-018-009: ExitTrap may be a RuntimeError subclass in some versions
            if "exit" in err_str or "trap" in err_str:
                result["success"] = True
                result["result"] = True
            elif "fuel" in err_str:
                result["error"] = "Fuel exhausted"
                result["fuel_used"] = self.fuel_limit
            else:
                result["error"] = f"Runtime error: {e}"
        except Exception as e:
            # NEXUS-018-009: Catch ExitTrap if wasmtime version supports it
            exc_name = type(e).__name__
            if exc_name == "ExitTrap" or "exit" in str(e).lower():
                # WASM guest called proc_exit — treat as success
                result["success"] = True
                result["result"] = True
            else:
                result["error"] = str(e)
        finally:
            with self._lock:
                self._running_instances.discard(instance_id)
        return result

    def load_module(self, wasm_path: Path) -> bytes | None:
        """
        Load WASM module from file.

        Args:
            wasm_path: Path to .wasm file

        Returns:
            Module bytecode or None
        """
        try:
            return wasm_path.read_bytes()
        except Exception as e:
            logger.error(f"Failed to load WASM module: {e}")
            return None

    def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.shutdown()

    async def shutdown(self) -> None:
        """Shutdown the sandbox and cleanup resources."""
        logger.info("Shutting down WASM sandbox")
        self._epoch_ticker_running = False
        if self._epoch_ticker:
            self._epoch_ticker.join(timeout=5)
        # NEXUS-018-009: Close all WASI sockets
        if self._wasi_linker is not None:
            self._wasi_linker.close_all()
        logger.info("WASM sandbox shutdown complete")

    def get_stats(self) -> dict[str, Any]:
        """Get sandbox statistics."""
        stats: dict[str, Any] = {
            "available": self.is_available(),
            "fuel_limit": self.fuel_limit,
            "epoch_deadline": self.epoch_deadline,
            "timeout": self.timeout,
            "running_instances": len(self._running_instances),
            "epoch_ticker_running": self._epoch_ticker_running,
            "wasi_enabled": self._enable_wasi,
        }
        if self._wasi_linker is not None:
            stats["wasi_socket_count"] = self._wasi_linker.socket_count
        return stats


# ── NEXUS-018-009 integration stub ──────────────────────────────────────────
# For parser_forge.py Stage C (sandboxed execution with custom protocol
# parsing), use:
#
#   sandbox = WasmSandbox.with_wasi()
#   result = await sandbox.run_async(wasm_bytes, function_name)
#
# The WASM module can then call sock_open, sock_send, sock_recv via the
# wasi_snapshot_preview1 imports to establish TCP connections to protocol
# endpoints — enabling custom parsers for VPN, IoT, darknet protocols.

# Backward-compat re-export (module was confused by two Instance assignments)
if _WASMTIME_AVAILABLE:
    try:
        from wasmtime import Instance as _WasmtimeInstance

        Instance = _WasmtimeInstance
    except ImportError:  # noqa: BLE001
        pass
