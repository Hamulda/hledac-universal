"""
tests/test_inference_coordinator.py — M-10: InferenceCoordinator Tests
=========================================================

Tests for all 3 backends: mlx_inproc, mlxcel, coreml.
Backend selection via InferenceBackend enum + env var.

Edit ONLY these files:
    tests/test_inference_coordinator.py
    core/inference_coordinator.py

Invariants tested:
    IC.1  brain/ neimportuje mlx_lm přímo — vše jde přes koordinátor
    IC.2  Backend selection per-request i per-env
    IC.3  Fail-safe — InferenceError má backend + cause
    IC.4  Streaming backend-agnostic — všechny vrací AsyncIterator[Token]
    IC.5  Lazy asyncio.Lock — žádný Lock při modul importu

Test convention: TestSprintM10* — 1 test class per backend.

Author: M-10 (F350M-R)
"""
from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import from the new coordinator
from core.inference_coordinator import (
    InferenceBackend,
    InferenceCoordinator,
    InferenceError,
    InferenceRequest,
    InferenceResponse,
    MLXInProcBackend,
    MlxcelBackend,
    CoreMLBackend,
    Token,
    get_inference_coordinator,
    generate,
    stream_generate,
)


# ─── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_env_mlx_inproc():
    """Force mlx_inproc backend."""
    with patch.dict(os.environ, {"HLEDAC_INFERENCE_BACKEND": "mlx_inproc"}, clear=False):
        yield


@pytest.fixture
def mock_env_mlxcel():
    """Force mlxcel backend."""
    with patch.dict(os.environ, {"HLEDAC_INFERENCE_BACKEND": "mlxcel"}, clear=False):
        yield


@pytest.fixture
def mock_env_coreml():
    """Force coreml backend."""
    with patch.dict(os.environ, {"HLEDAC_INFERENCE_BACKEND": "coreml"}, clear=False):
        yield


@pytest.fixture
def coordinator():
    """Fresh coordinator instance."""
    return InferenceCoordinator()


@pytest.fixture
def sample_request():
    """Canonical inference request."""
    return InferenceRequest(
        prompt="What is OSINT?",
        temperature=0.3,
        max_tokens=128,
        thinking=True,
        backend=InferenceBackend.MLX_INPROC,  # explicit — avoids env dependency
    )


# ─── TestSprintM10: Backend Enum ────────────────────────────────────────────────

class TestSprintM10BackendEnum:
    """IC.2: Backend resolution from env."""

    def test_default_is_mlxcel(self):
        """Default backend is mlxcel when env is unset (M1 8GB RSS savings)."""
        # Clear any existing env
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("HLEDAC_INFERENCE_BACKEND", None)
            backend = InferenceBackend.from_env()
            assert backend == InferenceBackend.MLXCEL

    def test_env_mlx_inproc(self, mock_env_mlx_inproc):
        backend = InferenceBackend.from_env()
        assert backend == InferenceBackend.MLX_INPROC

    def test_env_mlxcel(self, mock_env_mlxcel):
        backend = InferenceBackend.from_env()
        assert backend == InferenceBackend.MLXCEL

    def test_env_coreml(self, mock_env_coreml):
        backend = InferenceBackend.from_env()
        assert backend == InferenceBackend.COREML

    def test_env_unknown_defaults_to_mlxcel(self):
        """Unknown env value falls back to mlxcel."""
        with patch.dict(os.environ, {"HLEDAC_INFERENCE_BACKEND": "invalid_backend"}):
            backend = InferenceBackend.from_env()
            assert backend == InferenceBackend.MLXCEL

    def test_backend_values(self):
        """All backends have string values."""
        assert InferenceBackend.MLX_INPROC.value == "mlx_inproc"
        assert InferenceBackend.MLXCEL.value == "mlxcel"
        assert InferenceBackend.COREML.value == "coreml"


# ─── TestSprintM10: InferenceRequest ───────────────────────────────────────────

class TestSprintM10Request:
    """InferenceRequest DTO + effective_backend()."""

    def test_effective_backend_uses_request_backend(self):
        """Per-request backend overrides env."""
        req = InferenceRequest(
            prompt="test",
            backend=InferenceBackend.MLXCEL,
        )
        with patch.dict(os.environ, {"HLEDAC_INFERENCE_BACKEND": "mlx_inproc"}):
            assert req.effective_backend() == InferenceBackend.MLXCEL

    def test_effective_backend_falls_back_to_env(self):
        """No per-request backend → use env."""
        req = InferenceRequest(prompt="test")
        with patch.dict(os.environ, {"HLEDAC_INFERENCE_BACKEND": "coreml"}):
            os.environ.pop("HLEDAC_INFERENCE_BACKEND", None)
            os.environ["HLEDAC_INFERENCE_BACKEND"] = "coreml"
            assert req.effective_backend() == InferenceBackend.COREML

    def test_request_defaults(self):
        """Canonical defaults."""
        req = InferenceRequest(prompt="hello")
        assert req.temperature == 0.3
        assert req.max_tokens == 512
        assert req.thinking is True
        assert req.adapter_path is None
        assert req.backend is None


# ─── TestSprintM10: MLXInProc Backend ─────────────────────────────────────────

class TestSprintM10MLXInProc:
    """MLXInProcBackend — in-process mlx_lm via DeepHermes3Engine."""

    @pytest.mark.asyncio
    async def test_generate_returns_inference_response(self):
        """generate() returns InferenceResponse with correct backend."""
        backend = MLXInProcBackend()
        mock_engine = AsyncMock()
        mock_engine.generate = AsyncMock(return_value="OSINT is open-source intelligence")
        backend._engine = mock_engine

        request = InferenceRequest(
            prompt="What is OSINT?",
            thinking=True,
        )
        response = await backend.generate(request)

        assert isinstance(response, InferenceResponse)
        assert response.backend == InferenceBackend.MLX_INPROC
        assert response.text == "OSINT is open-source intelligence"
        assert response.latency_ms >= 0
        mock_engine.generate.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_yields_tokens(self):
        """stream() yields Token objects with done=True at end."""
        backend = MLXInProcBackend()
        mock_engine = AsyncMock()

        async def mock_stream(*_args, **_kwargs):
            yield "OS"
            yield "INT"
            yield " response"

        mock_engine.generate_stream = mock_stream
        backend._engine = mock_engine

        request = InferenceRequest(prompt="What is OSINT?")
        tokens = []
        async for token in backend.stream(request):
            tokens.append(token)

        assert len(tokens) == 4  # 3 content + 1 done
        assert tokens[0].text == "OS"
        assert tokens[0].backend == InferenceBackend.MLX_INPROC
        assert tokens[0].done is False
        assert tokens[3].done is True
        assert tokens[3].text == ""

    @pytest.mark.asyncio
    async def test_generate_propagates_inference_error(self):
        """Exception wrapped as InferenceError with backend + cause."""
        backend = MLXInProcBackend()
        mock_engine = AsyncMock()
        mock_engine.generate = AsyncMock(side_effect=RuntimeError("Metal OOM"))
        backend._engine = mock_engine

        request = InferenceRequest(prompt="test")
        with pytest.raises(InferenceError) as exc_info:
            await backend.generate(request)

        assert exc_info.value.backend == InferenceBackend.MLX_INPROC
        assert isinstance(exc_info.value.cause, RuntimeError)
        assert "Metal OOM" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_health_check_returns_engine_presence(self):
        """health_check returns True when engine is set."""
        backend = MLXInProcBackend()
        backend._engine = MagicMock()
        result = await backend.health_check()
        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_false_on_error(self):
        """health_check returns False on exception."""
        backend = MLXInProcBackend()
        backend._get_engine = MagicMock(side_effect=RuntimeError("unavailable"))
        result = await backend.health_check()
        assert result is False


# ─── TestSprintM10: Mlxcel Backend ─────────────────────────────────────────────

class TestSprintM10Mlxcel:
    """MlxcelBackend — out-of-process via MlxcelIpcClient."""

    @pytest.mark.asyncio
    async def test_generate_returns_inference_response(self):
        """generate() calls client.generate and wraps result."""
        backend = MlxcelBackend()
        mock_result = MagicMock()
        mock_result.text = "mlxcel response"
        mock_result.tokens_generated = 5

        with patch("brain.mlxcel_ipc_client.get_mlxcel_client", new_callable=AsyncMock) as mock_get:
            mock_client = AsyncMock()
            mock_client.generate = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_client

            request = InferenceRequest(prompt="test")
            response = await backend.generate(request)

        assert isinstance(response, InferenceResponse)
        assert response.backend == InferenceBackend.MLXCEL
        assert response.text == "mlxcel response"
        assert response.tokens_generated == 5

    @pytest.mark.asyncio
    async def test_stream_yields_tokens(self):
        """stream() yields Token objects from client.generate_stream."""
        backend = MlxcelBackend()

        async def mock_stream(*_args, **_kwargs):
            yield "hello"
            yield " world"

        with patch("brain.mlxcel_ipc_client.get_mlxcel_client", new_callable=AsyncMock) as mock_get:
            mock_client = AsyncMock()
            mock_client.generate_stream = mock_stream
            mock_get.return_value = mock_client

            tokens = []
            async for token in backend.stream(InferenceRequest(prompt="test")):
                tokens.append(token)

        assert len(tokens) == 3  # 2 content + 1 done
        assert tokens[0].text == "hello"
        assert tokens[0].backend == InferenceBackend.MLXCEL
        assert tokens[2].done is True

    @pytest.mark.asyncio
    async def test_generate_propagates_inference_error(self):
        """Exception wrapped as InferenceError with MLXCEL backend."""
        backend = MlxcelBackend()

        with patch("brain.mlxcel_ipc_client.get_mlxcel_client", new_callable=AsyncMock) as mock_get:
            mock_client = AsyncMock()
            mock_client.generate = AsyncMock(side_effect=ConnectionError("socket refused"))
            mock_get.return_value = mock_client

            with pytest.raises(InferenceError) as exc_info:
                await backend.generate(InferenceRequest(prompt="test"))

        assert exc_info.value.backend == InferenceBackend.MLXCEL
        assert isinstance(exc_info.value.cause, ConnectionError)


# ─── TestSprintM10: CoreML Backend ─────────────────────────────────────────────

class TestSprintM10CoreML:
    """CoreMLBackend — FastAPI microservice via CoreMLClient."""

    @pytest.mark.asyncio
    async def test_generate_returns_inference_response(self):
        """generate() calls client.predict and wraps result."""
        backend = CoreMLBackend()
        mock_result = MagicMock()
        mock_result.text = "coreml embedding result"
        mock_client = AsyncMock()
        mock_client.predict = AsyncMock(return_value=mock_result)
        backend._client = mock_client

        response = await backend.generate(InferenceRequest(prompt="test"))

        assert isinstance(response, InferenceResponse)
        assert response.backend == InferenceBackend.COREML
        assert response.text == "coreml embedding result"

    @pytest.mark.asyncio
    async def test_stream_single_token(self):
        """CoreML doesn't support streaming — yields single done token."""
        backend = CoreMLBackend()
        mock_result = MagicMock()
        mock_result.text = "coreml result"
        mock_client = AsyncMock()
        mock_client.predict = AsyncMock(return_value=mock_result)
        backend._client = mock_client

        tokens = []
        async for token in backend.stream(InferenceRequest(prompt="test")):
            tokens.append(token)

        # Single predict result + done
        assert len(tokens) == 2
        assert tokens[0].text == "coreml result"
        assert tokens[0].backend == InferenceBackend.COREML
        assert tokens[0].done is False
        assert tokens[1].done is True

    @pytest.mark.asyncio
    async def test_generate_propagates_inference_error(self):
        """Exception wrapped as InferenceError with COREML backend."""
        backend = CoreMLBackend()
        mock_client = AsyncMock()
        mock_client.predict = AsyncMock(side_effect=ConnectionError("service down"))
        backend._client = mock_client

        with pytest.raises(InferenceError) as exc_info:
            await backend.generate(InferenceRequest(prompt="test"))

        assert exc_info.value.backend == InferenceBackend.COREML
        assert isinstance(exc_info.value.cause, ConnectionError)


# ─── TestSprintM10: InferenceCoordinator ─────────────────────────────────────────

class TestSprintM10Coordinator:
    """InferenceCoordinator — unified entry point."""

    def test_default_backend_from_env(self, mock_env_mlx_inproc):
        """Coordinator uses env default when no per-request override."""
        coord = InferenceCoordinator()
        assert coord.get_default_backend() == InferenceBackend.MLX_INPROC

    def test_resolve_backend_per_request(self):
        """_resolve_backend uses request.backend over default.

        B1 FIX: Both MLXCEL and MLX_INPROC are always in _backends dict.
        A per-request MLXCEL backend resolves to MlxcelBackend.
        """
        coord = InferenceCoordinator()
        req = InferenceRequest(prompt="test", backend=InferenceBackend.MLXCEL)
        be = coord._resolve_backend(req)
        assert isinstance(be, MlxcelBackend)

    def test_resolve_backend_env_default(self, mock_env_mlxcel):
        """_resolve_backend uses MLXCEL as default when HLEDAC_INFERENCE_BACKEND=mlxcel.

        B1 FIX: MLXCEL is now the default and is always registered.
        When env=mlxcel, _backends[MLXCEL] = MlxcelBackend is used.
        """
        coord = InferenceCoordinator()
        req = InferenceRequest(prompt="test")
        be = coord._resolve_backend(req)
        assert isinstance(be, MlxcelBackend)

    @pytest.mark.asyncio
    async def test_generate_delegates_to_backend(self, sample_request):
        """generate() calls the resolved backend's generate()."""
        coord = InferenceCoordinator()
        mock_be = AsyncMock()
        mock_be.generate = AsyncMock(return_value=InferenceResponse(
            text="delegated",
            tokens_generated=2,
            latency_ms=100.0,
            backend=InferenceBackend.MLX_INPROC,
        ))
        coord._backends[InferenceBackend.MLX_INPROC] = mock_be

        response = await coord.generate(sample_request)
        assert response.text == "delegated"
        mock_be.generate.assert_called_once_with(sample_request)

    @pytest.mark.asyncio
    async def test_stream_delegates_to_backend(self, sample_request):
        """stream() calls the resolved backend's stream()."""
        coord = InferenceCoordinator()
        mock_tokens = [
            Token(text="hello", done=False, backend=InferenceBackend.MLX_INPROC),
            Token(text="", done=True, backend=InferenceBackend.MLX_INPROC),
        ]

        async def mock_stream(*_args, **_kwargs):
            for t in mock_tokens:
                yield t

        mock_be = MagicMock()
        mock_be.stream = mock_stream
        coord._backends[InferenceBackend.MLX_INPROC] = mock_be

        tokens = []
        async for t in coord.stream(sample_request):
            tokens.append(t)
        assert tokens[0].text == "hello"

    @pytest.mark.asyncio
    async def test_generate_wraps_unknown_errors(self, sample_request):
        """Unknown errors wrapped as InferenceError."""
        coord = InferenceCoordinator()
        mock_be = AsyncMock()
        mock_be.generate = AsyncMock(side_effect=ValueError("unexpected"))
        coord._backends[InferenceBackend.MLX_INPROC] = mock_be

        with pytest.raises(InferenceError) as exc_info:
            await coord.generate(sample_request)
        assert "unexpected" in str(exc_info.value)
        assert exc_info.value.cause is not None

    @pytest.mark.asyncio
    async def test_health_check_default_backend(self):
        """health_check checks the default backend by default."""
        coord = InferenceCoordinator()
        mock_be = AsyncMock()
        mock_be.health_check = AsyncMock(return_value=True)
        # B1: default backend is now MLXCEL, mock the actual default
        coord._backends[coord._default_backend] = mock_be

        result = await coord.health_check()
        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_specific_backend(self):
        """health_check can check a specific backend."""
        coord = InferenceCoordinator()
        mock_be = AsyncMock()
        mock_be.health_check = AsyncMock(return_value=False)
        coord._backends[InferenceBackend.MLXCEL] = mock_be

        result = await coord.health_check(InferenceBackend.MLXCEL)
        assert result is False


# ─── TestSprintM10: Module-level API ────────────────────────────────────────────

class TestSprintM10ModuleAPI:
    """Module-level convenience functions: get_inference_coordinator, generate, stream_generate."""

    def test_get_inference_coordinator_singleton(self):
        """get_inference_coordinator returns same object."""
        c1 = get_inference_coordinator()
        c2 = get_inference_coordinator()
        assert c1 is c2

    @pytest.mark.asyncio
    async def test_generate_convenience_wraps_coordinator(self):
        """generate() is a convenience wrapper over coordinator.generate()."""
        coord = InferenceCoordinator()
        mock_be = AsyncMock()
        mock_be.generate = AsyncMock(return_value=InferenceResponse(
            text="convenience",
            tokens_generated=1,
            latency_ms=50.0,
            backend=InferenceBackend.MLX_INPROC,
        ))
        # B1: mock whatever the actual default backend is
        coord._backends[coord._default_backend] = mock_be

        with patch("core.inference_coordinator.get_inference_coordinator", return_value=coord):
            response = await generate("test prompt")
        assert response.text == "convenience"

    @pytest.mark.asyncio
    async def test_stream_generate_convenience_wraps_coordinator(self):
        """stream_generate() is a convenience wrapper over coordinator.stream()."""
        coord = InferenceCoordinator()
        mock_tokens = [
            Token(text="stream", done=False, backend=InferenceBackend.MLX_INPROC),
            Token(text="token", done=False, backend=InferenceBackend.MLX_INPROC),
            Token(text="", done=True, backend=InferenceBackend.MLX_INPROC),
        ]

        async def mock_stream(*_args, **_kwargs):
            for t in mock_tokens:
                yield t

        mock_be = MagicMock()
        mock_be.stream = mock_stream
        # B1: mock whatever the actual default backend is
        coord._backends[coord._default_backend] = mock_be

        with patch("core.inference_coordinator.get_inference_coordinator", return_value=coord):
            tokens = []
            async for t in stream_generate("test prompt"):
                tokens.append(t)
        assert tokens[0].text == "stream"
        assert tokens[1].text == "token"


# ─── TestSprintM10: IC Invariants ───────────────────────────────────────────────

class TestSprintM10Invariants:
    """IC.* invariants — verified through behavior."""

    def test_ic1_no_mlx_lm_in_core_coordinator(self):
        """IC.1: core/inference_coordinator.py must NOT import mlx_lm at module level."""
        import core.inference_coordinator as mod

        source = open(mod.__file__).read()
        # Check for actual import statements, not docstring mentions
        # "import mlx_lm" as a statement (beginning of line or after ;)
        import_lines = [l.strip() for l in source.split('\n')
                        if l.strip().startswith('import mlx_lm')
                        or l.strip().startswith('from mlx_lm')]
        assert not import_lines, f"Found mlx_lm imports: {import_lines}"

    def test_ic3_inference_error_has_backend_and_cause(self):
        """IC.3: InferenceError carries backend and cause."""
        cause = ValueError("original")
        err = InferenceError("msg", InferenceBackend.MLXCEL, cause=cause)
        assert err.backend == InferenceBackend.MLXCEL
        assert err.cause is cause

    def test_ic5_lazy_lock_no_module_level_lock(self):
        """IC.5: No asyncio.Lock at module level in coordinator (only lazy _get_lock())."""
        import core.inference_coordinator as mod
        source = open(mod.__file__).read()
        lines = source.split("\n")
        for line in lines:
            # Check no asyncio.Lock() at module level
            # Allow threading.Lock() and allow Lock inside function bodies
            if "asyncio.Lock()" in line:
                # Module-level: starts at column 0 or after only whitespace + =
                indent = len(line) - len(line.lstrip())
                if indent == 0 or (indent <= 4 and "=" in line[:indent + 8]):
                    if "_COORDINATOR_LOCK" not in line and "_client_lock" not in line:
                        pytest.fail(f"Module-level asyncio.Lock found: {line.strip()}")

    @pytest.mark.asyncio
    async def test_ic4_stream_returns_async_iterator(self):
        """IC.4: All backends return AsyncIterator[Token] from stream()."""
        for BackendClass, _backend_type in [
            (MLXInProcBackend, InferenceBackend.MLX_INPROC),
            (MlxcelBackend, InferenceBackend.MLXCEL),
            (CoreMLBackend, InferenceBackend.COREML),
        ]:
            be = BackendClass()
            result = be.stream(InferenceRequest(prompt="test"))
            # Must be an async iterator (has __aiter__)
            assert hasattr(result, "__aiter__")


# ─── Helpers ─────────────────────────────────────────────────────────────────────

async def async_iter(items):
    """Turn a list into an async iterator."""
    for item in items:
        yield item


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-q"])
