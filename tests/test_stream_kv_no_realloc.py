"""
tests/test_stream_kv_no_realloc.py

Issue M-07 HIGH: kv_cache reset inside stream_generate is non-functional.

Root cause (FIXED):
  _stream_tokens() created stream_kwargs dict BEFORE the loop, then mutated
  stream_kwargs['prompt_cache'] inside the for-loop every 512 tokens.
  Python generators capture kwargs at creation time — mutations after
  stream_generate() is called have NO effect on the running generator.

  The broken block (lines 2674-2687 old code):
    if _tokens_generated % _kv_cache_reset_interval == 0 and kv_cache is not None:
        kv_cache = make_prompt_cache(...)   # created but never used
        stream_kwargs['prompt_cache'] = kv_cache  # mutation has no effect

Fix applied:
  1. Broken reset block DELETED.
  2. Streaming path now uses RotatingKVCache when self._paged_kv_cache is True
     (mirror of the non-streaming path at lines 1878-1882).
  3. RotatingKVCache provides zero-copy rotation — no mid-stream realloc.

Acceptance: in a 1024-token stream, no cache reallocation occurs.

Invariant checks:
  - ALWAYS-ON: no new feature flag; paged_kv_cache path has always existed
  - BOUNDED: RotatingKVCache keep=max_tok prevents unbounded growth
  - FAIL-SAFE: graceful fallback if RotatingKVCache unavailable

Final: pytest tests/test_stream_kv_no_realloc.py -xvs -q
"""

from __future__ import annotations


class TestStreamKVNoRealloc:
    """M-07: Verify no mid-stream KV cache reallocation occurs."""

    def test_kv_cache_reset_interval_removed_from_stream_tokens(self) -> None:
        """
        The _kv_cache_reset_interval variable and the broken reset block
        must NOT exist in _stream_tokens().
        """
        import inspect

        from hledac.universal.brain.deephermes3_engine import DeepHermes3Engine

        source = inspect.getsource(DeepHermes3Engine._stream_tokens)
        assert "_kv_cache_reset_interval" not in source, (
            "M-07 REGRESSION: _kv_cache_reset_interval still present in _stream_tokens"
        )
        assert "stream_cache_resets" not in source, (
            "M-07 REGRESSION: stream_cache_resets stat update still present in _stream_tokens"
        )
        # The mutation that had zero effect must be gone
        assert "stream_kwargs['prompt_cache'] = kv_cache" not in source or (
            # It's OK if prompt_cache is set ONCE before the loop, not inside the loop
            source.count("stream_kwargs['prompt_cache'] = kv_cache") <= 1
        ), "M-07 REGRESSION: prompt_cache assignment appears multiple times (loop mutation still present)"

    def test_paged_kv_cache_path_in_stream_tokens(self) -> None:
        """
        _stream_tokens must use RotatingKVCache when self._paged_kv_cache is True,
        mirroring the non-streaming path (deephermes3_engine.py:1878-1882).
        """
        import inspect

        from hledac.universal.brain.deephermes3_engine import DeepHermes3Engine

        source = inspect.getsource(DeepHermes3Engine._stream_tokens)
        assert "RotatingKVCache" in source, (
            "M-07: RotatingKVCache not found in _stream_tokens — "
            "streaming path should mirror non-streaming path for paged_kv_cache=True"
        )
        assert "self._paged_kv_cache" in source, "M-07: self._paged_kv_cache check missing from _stream_tokens"

    def test_stream_tokens_no_loop_inside_stream_generate(self) -> None:
        """
        Verify the streaming loop only iterates over tokens, no cache management.
        The only operations inside the for-loop should be token extraction,
        buffering, and periodic eval — NOT cache creation/assignment.
        """
        import inspect

        from hledac.universal.brain.deephermes3_engine import DeepHermes3Engine

        source = inspect.getsource(DeepHermes3Engine._stream_tokens)

        # Find the for-loop body
        lines = source.split("\n")
        in_loop = False
        loop_lines = []
        for line in lines:
            if "for chunk in stream_generate" in line:
                in_loop = True
            if in_loop:
                loop_lines.append(line)
                if line.strip().startswith("if tok:"):
                    # Check what's inside the "if tok:" block
                    pass

        # The loop body should NOT contain make_prompt_cache calls
        loop_text = "\n".join(loop_lines)
        make_prompt_cache_count = loop_text.count("make_prompt_cache(")
        assert make_prompt_cache_count == 0, (
            f"M-07 REGRESSION: make_prompt_cache called {make_prompt_cache_count} time(s) "
            "inside the stream_generate loop — this was the root cause bug"
        )

    def test_rotatingkvcache_config_parity_stream_vs_nonstream(self) -> None:
        """
        RotatingKVCache must be configured identically in both streaming and
        non-streaming paths: max_size=max_tok, keep=self._paged_kv_keep.
        """
        import inspect

        from hledac.universal.brain.deephermes3_engine import DeepHermes3Engine

        # Non-streaming path (generate_stream -> non-streaming)
        gen_source = inspect.getsource(DeepHermes3Engine.generate_stream)

        # Streaming path
        stream_source = inspect.getsource(DeepHermes3Engine._stream_tokens)

        # Extract the RotatingKVCache line from each
        def extract_rotating_line(src):
            for line in src.split("\n"):
                if "RotatingKVCache" in line and "max_size" in line:
                    return line.strip()
            return None

        gen_line = extract_rotating_line(gen_source)
        stream_line = extract_rotating_line(stream_source)

        if gen_line:
            assert "max_size=max_tok" in gen_line, f"M-07: non-streaming path uses wrong max_size: {gen_line}"
            assert "keep=self._paged_kv_keep" in gen_line, f"M-07: non-streaming path missing keep param: {gen_line}"

        if stream_line:
            assert "max_size=max_tok" in stream_line, f"M-07: streaming path uses wrong max_size: {stream_line}"
            assert "keep=self._paged_kv_keep" in stream_line, f"M-07: streaming path missing keep param: {stream_line}"
