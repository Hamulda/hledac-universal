# /// script
# dependencies = [
#   "mlx-embeddings",
#   "fastapi",
#   "uvicorn",
# ]
# ///
"""
M-2026-FIX: Standalone MLX embedding FastAPI server (8-bit ModernBERT).
Lazy MLX import — was loaded at module top-level before; this crashed cold start
on M1 (and invalidated AGENTS.md invariant #1). Now imports happen only when
``create_app()`` is called, which is exclusively inside ``if __name__ == "__main__":``.
"""
from __future__ import annotations

from typing import Any

MODEL_NAME = "mlx-community/nomicai-modernbert-embed-base-8bit"


def create_app() -> Any:
    """Lazy FastAPI app factory — MLX imports only happen here."""
    # All imports below were previously at module top-level; now they fire
    # only when this factory is called. AGENTS.md invariant #1 compliant.
    from fastapi import FastAPI
    from mlx_embeddings import generate, load
    from pydantic import BaseModel
    import uvicorn

    app = FastAPI()

    # Load 8-bit ModernBERT MLX model (only if the server actually starts).
    model, tokenizer = load(MODEL_NAME)

    class EmbeddingRequest(BaseModel):
        input: str | list[str]
        model: str = MODEL_NAME

    @app.post("/v1/embeddings")
    def create_embeddings(req: EmbeddingRequest):
        texts = [req.input] if isinstance(req.input, str) else req.input
        output = generate(model, tokenizer, texts=texts)

        # Convert mlx.core.array → Python list.
        embeddings_list = output.text_embeds.tolist()

        data = [{"object": "embedding", "embedding": emb, "index": i} for i, emb in enumerate(embeddings_list)]
        return {"object": "list", "data": data, "model": req.model}

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "model": MODEL_NAME}

    return app


if __name__ == "__main__":
    import uvicorn
    _app = create_app()
    uvicorn.run(_app, host="127.0.0.1", port=8080)
