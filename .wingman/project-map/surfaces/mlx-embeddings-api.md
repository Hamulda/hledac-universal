# MLX Embeddings API

## Metadata

| Field | Value |
| --- | --- |
| Kind | surface |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `surfaces/mlx-embeddings-api.md` |
| Source Path | `mlx_server.py` |

## Summary

FastAPI server exposing /v1/embeddings endpoint with MLX embeddings. Model: mlx-community/nomicai-modernbert-embed-base-8bit.

## Endpoint

```bash
POST /v1/embeddings
{
  "input": "text" | ["text1", "text2"],
  "model": "mlx-community/nomicai-modernbert-embed-base-8bit"
}
```

## Evidence

- FastAPI + uvicorn
- Model loaded once and reused (mlx_embeddings.load())
- Embedding dimension: 256d (MRL truncation)

## Use When

- Generating text embeddings via MLX
- Semantic search indexing

## Do Not Use When

- General LLM inference (see MLX inference capability)
