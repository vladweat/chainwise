"""Download MiniLM ONNX weights into backend/.models for offline Docker builds."""

from __future__ import annotations

import sys
from pathlib import Path

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
MODELS_DIR = Path(__file__).resolve().parent.parent / ".models"
VECTOR_SIZE = 384


def _has_onnx(path: Path) -> bool:
    return path.is_dir() and any(path.rglob("*.onnx"))


def main() -> int:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    if _has_onnx(MODELS_DIR):
        print(f"OK: MiniLM already in {MODELS_DIR}")
        return 0

    try:
        from fastembed import TextEmbedding
    except ImportError:
        print("ERROR: fastembed is not installed. Run make venv first.", file=sys.stderr)
        return 1

    print(f"Downloading {MODEL_NAME} (ONNX) into {MODELS_DIR} ...")
    model = TextEmbedding(model_name=MODEL_NAME, cache_dir=str(MODELS_DIR))
    vector = next(model.embed(["warmup"]))
    length = len(vector) if vector is not None else 0
    if length != VECTOR_SIZE:
        print(f"ERROR: expected {VECTOR_SIZE}-dim embeddings, got {length}", file=sys.stderr)
        return 1
    if not _has_onnx(MODELS_DIR):
        print(f"ERROR: ONNX model was not saved into {MODELS_DIR}", file=sys.stderr)
        return 1
    print(f"OK: saved {MODEL_NAME} -> {MODELS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
