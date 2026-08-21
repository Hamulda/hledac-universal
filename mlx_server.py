# /// script
# dependencies = [
#   "mlx-embeddings",
#   "fastapi",
#   "uvicorn",
# ]
# ///

import uvicorn
from fastapi import FastAPI
from mlx_embeddings import generate, load
from pydantic import BaseModel

app = FastAPI()

MODEL_NAME = "mlx-community/nomicai-modernbert-embed-base-8bit"

# Načtení 8-bit ModernBERT MLX modelu
model, tokenizer = load(MODEL_NAME)


class EmbeddingRequest(BaseModel):
    input: str | list[str]
    model: str = MODEL_NAME


@app.post("/v1/embeddings")
def create_embeddings(req: EmbeddingRequest):
    texts = [req.input] if isinstance(req.input, str) else req.input
    output = generate(model, tokenizer, texts=texts)

    # Převod mlx.core.array na Python list
    embeddings_list = output.text_embeds.tolist()

    data = [{"object": "embedding", "embedding": emb, "index": i} for i, emb in enumerate(embeddings_list)]
    return {"object": "list", "data": data, "model": req.model}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080)
