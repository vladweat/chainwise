"""Embed code chunks with MiniLM and upsert them into Qdrant `code_chunks`."""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

from app.core.config import get_settings
from app.services.semantic_splitter import CodeChunk

logger = logging.getLogger(__name__)

COLLECTION_NAME = "code_chunks"
VECTOR_SIZE = 384
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

_client = None
_embedder = None


class QdrantIndexError(Exception):
    """Raised when embeddings or Qdrant upsert fail."""


def embedding_model_dir() -> Path:
    env_path = os.environ.get("EMBEDDING_MODEL_PATH")
    if env_path:
        return Path(env_path)
    settings = get_settings()
    if settings.embedding_model_path:
        return Path(settings.embedding_model_path)
    return Path(__file__).resolve().parents[2] / ".models"


def is_embedder_available() -> bool:
    path = embedding_model_dir()
    return path.is_dir() and any(path.rglob("*.onnx"))


def is_qdrant_available() -> bool:
    settings = get_settings()
    if not settings.qdrant_url:
        return False
    try:
        import httpx

        response = httpx.get(
            f"{settings.qdrant_url.rstrip('/')}/healthz",
            timeout=2.0,
            trust_env=False,
        )
        return response.status_code == 200
    except Exception:
        return False


def upsert_code_chunks(
    contract_id: int,
    chunks: list[CodeChunk],
    *,
    generation: str | None = None,
    client=None,
    embedder=None,
) -> int:
    """Replace vectors for one contract generation with embeddings of `chunks`."""
    qdrant = client if client is not None else _get_client()
    encoder = embedder if embedder is not None else _get_embedder()

    try:
        _ensure_collection(qdrant)
        _delete_contract_points(qdrant, contract_id, generation=generation)
        if not chunks:
            logger.info(
                "No code chunks to index",
                extra={"contract_id": contract_id, "chunk_count": 0},
            )
            return 0

        vectors = encode_texts(encoder, [chunk.content for chunk in chunks])
        if len(vectors) != len(chunks):
            raise QdrantIndexError("Embedding count does not match chunk count")

        from qdrant_client.models import PointStruct

        points = [
            PointStruct(
                id=_point_id(contract_id, chunk, generation),
                vector=vectors[index],
                payload=_chunk_payload(contract_id, chunk, generation),
            )
            for index, chunk in enumerate(chunks)
        ]
        qdrant.upsert(collection_name=COLLECTION_NAME, points=points, wait=True)
    except QdrantIndexError:
        raise
    except Exception as exc:
        logger.exception("Failed to upsert code chunks")
        raise QdrantIndexError(f"Qdrant upsert failed: {exc}") from exc

    logger.info(
        "Indexed code chunks in Qdrant",
        extra={
            "contract_id": contract_id,
            "collection": COLLECTION_NAME,
            "chunk_count": len(chunks),
        },
    )
    return len(chunks)


def _get_client():
    global _client
    if _client is None:
        from qdrant_client import QdrantClient

        from app.core.proxy import apply_internal_no_proxy

        apply_internal_no_proxy()
        settings = get_settings()
        # trust_env=False: never send Docker-internal Qdrant through Squid
        # (httpx otherwise honors HTTPS_PROXY even when no_proxy is incomplete).
        kwargs: dict = {
            "url": settings.qdrant_url,
            "timeout": 60,
            "trust_env": False,
        }
        if settings.qdrant_api_key:
            kwargs["api_key"] = settings.qdrant_api_key
        _client = QdrantClient(**kwargs)
    return _client


def _get_embedder():
    global _embedder
    if _embedder is None:
        _embedder = _load_embedder()
    return _embedder


def _load_embedder():
    cache_dir = embedding_model_dir()
    if not is_embedder_available():
        raise QdrantIndexError(
            f"Embedding model not found at {cache_dir}. Run: make embeddings"
        )
    try:
        from fastembed import TextEmbedding
    except ImportError as exc:
        raise QdrantIndexError("fastembed is not installed") from exc

    logger.info("Loading MiniLM embedder from %s", cache_dir)
    kwargs = {"model_name": MODEL_NAME, "cache_dir": str(cache_dir)}
    try:
        model = TextEmbedding(**kwargs, local_files_only=True)
    except TypeError:
        model = TextEmbedding(**kwargs)
    return _FastEmbedEncoder(model)


class _FastEmbedEncoder:
    """Adapt fastembed.TextEmbedding to the encode() interface used in tests."""

    def __init__(self, model):
        self._model = model

    def encode(self, texts, **_kwargs):
        return list(self._model.embed(list(texts)))


def _ensure_collection(client) -> None:
    from qdrant_client.models import Distance, VectorParams

    if not client.collection_exists(COLLECTION_NAME):
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )
    try:
        client.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name="contract_id",
            field_schema="integer",
        )
    except Exception as exc:
        message = str(exc).lower()
        if "already" not in message and "conflict" not in message:
            logger.warning("Could not create contract_id payload index: %s", exc)
    try:
        client.create_payload_index(
            collection_name=COLLECTION_NAME,
            field_name="indexing_attempt_id",
            field_schema="keyword",
        )
    except Exception as exc:
        message = str(exc).lower()
        if "already" not in message and "conflict" not in message:
            logger.warning("Could not create indexing_attempt_id payload index: %s", exc)


def _delete_contract_points(
    client, contract_id: int, *, generation: str | None = None
) -> None:
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    conditions = [
        FieldCondition(key="contract_id", match=MatchValue(value=int(contract_id)))
    ]
    if generation is not None:
        conditions.append(
            FieldCondition(
                key="indexing_attempt_id", match=MatchValue(value=generation)
            )
        )
    client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(must=conditions),
        wait=True,
    )


def delete_contract_chunks(contract_id: int, *, client=None) -> None:
    """Remove all Qdrant points for a contract. No-op if collection missing."""
    qdrant = client if client is not None else _get_client()
    try:
        if not qdrant.collection_exists(COLLECTION_NAME):
            return
        _delete_contract_points(qdrant, contract_id)
    except Exception as exc:
        logger.warning(
            "Failed to delete Qdrant points for contract %s: %s",
            contract_id,
            exc,
        )


def scroll_contract_chunks(
    contract_id: int,
    *,
    generation: str | None = None,
    client=None,
    batch_size: int = 100,
) -> list[dict]:
    """Return all Qdrant payloads for `contract_id` (no vectors)."""
    qdrant = client if client is not None else _get_client()
    if not qdrant.collection_exists(COLLECTION_NAME):
        return []

    filt = _contract_filter(contract_id, generation)
    payloads: list[dict] = []
    offset = None
    while True:
        records, offset = qdrant.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=filt,
            limit=batch_size,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for record in records or []:
            payload = _payload_of(record)
            if payload:
                payloads.append(payload)
        if offset is None:
            break
    return payloads


def search_similar_chunks(
    contract_id: int,
    query: str,
    *,
    generation: str | None = None,
    limit: int = 16,
    client=None,
    embedder=None,
) -> list[tuple[dict, float]]:
    """Dense cosine search over `code_chunks` filtered by contract_id."""
    qdrant = client if client is not None else _get_client()
    if not qdrant.collection_exists(COLLECTION_NAME):
        return []

    encoder = embedder if embedder is not None else _get_embedder()
    vector = encode_texts(encoder, [query])[0]
    points = _query_similar(qdrant, vector, contract_id, limit, generation)
    results: list[tuple[dict, float]] = []
    for point in points or []:
        payload = _payload_of(point)
        if not payload:
            continue
        score = float(getattr(point, "score", 0.0) or 0.0)
        results.append((payload, score))
    return results


def encode_texts(embedder, texts: list[str]) -> list[list[float]]:
    raw = embedder.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    if hasattr(raw, "tolist"):
        raw = raw.tolist()
    vectors: list[list[float]] = []
    for item in raw:
        vector = item.tolist() if hasattr(item, "tolist") else [float(value) for value in item]
        if len(vector) != VECTOR_SIZE:
            raise QdrantIndexError(
                f"Expected {VECTOR_SIZE}-dim embeddings, got {len(vector)}"
            )
        vectors.append(vector)
    return vectors


def _contract_filter(contract_id: int, generation: str | None):
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    conditions = [
        FieldCondition(key="contract_id", match=MatchValue(value=int(contract_id)))
    ]
    if generation is not None:
        conditions.append(
            FieldCondition(
                key="indexing_attempt_id", match=MatchValue(value=generation)
            )
        )
    return Filter(must=conditions)


def _query_similar(
    client, vector: list[float], contract_id: int, limit: int, generation: str | None
):
    filt = _contract_filter(contract_id, generation)
    if hasattr(client, "query_points"):
        try:
            result = client.query_points(
                collection_name=COLLECTION_NAME,
                query=vector,
                query_filter=filt,
                limit=limit,
                with_payload=True,
            )
            return getattr(result, "points", result)
        except TypeError:
            pass
    return client.search(
        collection_name=COLLECTION_NAME,
        query_vector=vector,
        query_filter=filt,
        limit=limit,
        with_payload=True,
    )


def _payload_of(record) -> dict:
    if isinstance(record, dict):
        payload = record.get("payload")
        return payload if isinstance(payload, dict) else record
    payload = getattr(record, "payload", None)
    return payload if isinstance(payload, dict) else {}


def _chunk_payload(
    contract_id: int, chunk: CodeChunk, generation: str | None
) -> dict:
    payload = chunk.to_payload(contract_id)
    if generation is not None:
        payload["indexing_attempt_id"] = generation
    return payload


def _point_id(
    contract_id: int, chunk: CodeChunk, generation: str | None = None
) -> str:
    key = (
        f"{contract_id}:{chunk.file_path}:{chunk.function_name}:"
        f"{chunk.start_line}:{chunk.end_line}:{chunk.kind}"
    )
    if generation is not None:
        key = f"{key}:{generation}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))
