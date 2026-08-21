"""
NER Worker — Long-running subprocess for GLiNER inference.

Loads GLiNER model once at startup and processes requests via JSONL stdin/stdout.
Survives 1000+ requests without reloading model.

Usage:
    python -m brain.ner_engine_worker [--model MODEL_NAME]

Communication protocol:
    - Read JSON request from stdin: {"texts": [...], "labels": [...], "threshold": float}
    - Write JSON response to stdout: {"success": True, "results": [...]}
    - Or on error: {"success": False, "error": "message"}
"""

from __future__ import annotations

import asyncio
import os
import sys

import orjson as json


async def main() -> None:
    """Main worker loop — load model once, process many requests."""
    model_name = "knowledgator/gliner-relex-large-v0.5"
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--model" and i + 1 < len(args):
            model_name = args[i + 1]

    # Suppress tokenizer parallelism warning
    os.environ["TOKENIZERS_PARALLELISM"] = "false"

    # Notify parent that we're ready
    print("READY", flush=True)

    gliner_model = None
    load_error: str | None = None

    try:
        from gliner import GLiNER

        gliner_model = GLiNER.from_pretrained(model_name, load_tokenizer=True)
        gliner_model.eval()
        # Ensure CPU
        if hasattr(gliner_model, "device"):
            gliner_model = gliner_model.to("cpu")
    except Exception as e:
        load_error = str(e)
        # Still notify ready even on error - let parent decide how to handle
        print(f"LOAD_ERROR:{load_error}", flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except ValueError as e:
            print(json.dumps({"success": False, "error": f"Invalid JSON: {e}"}), flush=True)
            continue

        texts = request.get("texts", [])
        labels = request.get("labels", [])
        threshold = request.get("threshold", 0.5)

        if gliner_model is None:
            print(json.dumps({"success": False, "error": load_error or "Model not loaded"}), flush=True)
            continue

        try:
            # Sprint B10 FIX: Batch inference — single GPU kernel launch for all texts.
            # GLiNER.predict_entities(texts: list[str]) returns list[list[dict]] directly.
            # Previously: for text in texts: predict_entities(text) — N× kernel launches.
            batch_results: list[list[dict]] = gliner_model.predict_entities(texts, labels, threshold=threshold)
            results: list[list[dict]] = []
            for ent_list in batch_results:
                results.append(
                    [
                        {
                            "entity": e.get("text", ""),
                            "label": e.get("label", ""),
                            "span": (e.get("start", 0), e.get("end", 0)),
                            "score": e.get("score", 0.0),
                        }
                        for e in ent_list
                    ]
                )

            print(json.dumps({"success": True, "results": results}), flush=True)

        except Exception as e:
            print(json.dumps({"success": False, "error": str(e)}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
