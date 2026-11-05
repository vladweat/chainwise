import pytest

pytest.importorskip("qdrant_client")

from app.services.qdrant_indexer import (
    COLLECTION_NAME,
    QdrantIndexError,
    VECTOR_SIZE,
    upsert_code_chunks,
)
from app.services.semantic_splitter import CodeChunk


def _chunk(name: str = "Demo.ping", start: int = 3, end: int = 5) -> CodeChunk:
    return CodeChunk(
        file_path="Demo.sol",
        function_name=name,
        start_line=start,
        end_line=end,
        content=f"function {name.split('.')[-1]}() external {{}}",
        kind="function",
    )


class FakeEmbedder:
    def __init__(self, dim: int = VECTOR_SIZE):
        self.dim = dim
        self.texts: list[str] = []

    def encode(self, texts, **_kwargs):
        self.texts = list(texts)
        return [[float((index + offset) % 7) for offset in range(self.dim)] for index in range(len(texts))]


class FakeQdrant:
    def __init__(self):
        self.collections: set[str] = set()
        self.indexes: list[dict] = []
        self.deleted: list = []
        self.points: list = []

    def collection_exists(self, name: str) -> bool:
        return name in self.collections

    def create_collection(self, collection_name: str, vectors_config=None):
        self.collections.add(collection_name)
        self.vectors_config = vectors_config

    def create_payload_index(self, **kwargs):
        self.indexes.append(kwargs)

    def delete(self, collection_name: str, points_selector=None, wait=None):
        self.deleted.append(
            {"collection": collection_name, "selector": points_selector, "wait": wait}
        )
        self.points = [
            point
            for point in self.points
            if point.payload.get("contract_id") != _filter_contract_id(points_selector)
        ]

    def upsert(self, collection_name: str, points=None, wait=None):
        assert collection_name == COLLECTION_NAME
        self.points.extend(points or [])
        self.upsert_wait = wait


def _filter_contract_id(selector) -> int | None:
    must = getattr(selector, "must", None) or []
    if not must:
        return None
    match = getattr(must[0], "match", None)
    return getattr(match, "value", None)


def test_upsert_creates_collection_and_points():
    client = FakeQdrant()
    embedder = FakeEmbedder()
    chunks = [_chunk("Demo.ping"), _chunk("Demo.mint", start=8, end=10)]

    count = upsert_code_chunks(7, chunks, client=client, embedder=embedder)

    assert count == 2
    assert COLLECTION_NAME in client.collections
    assert client.indexes[0]["field_name"] == "contract_id"
    assert len(client.points) == 2
    assert embedder.texts == [chunk.content for chunk in chunks]
    payload = client.points[0].payload
    assert payload["contract_id"] == 7
    assert payload["file_path"] == "Demo.sol"
    assert payload["function_name"] == "Demo.ping"
    assert payload["start_line"] == 3
    assert payload["end_line"] == 5
    assert payload["content"].startswith("function ping")
    assert payload["kind"] == "function"
    assert len(client.points[0].vector) == VECTOR_SIZE
    assert client.upsert_wait is True


def test_upsert_replaces_previous_points_for_contract():
    client = FakeQdrant()
    embedder = FakeEmbedder()
    first = [_chunk("Demo.old")]
    upsert_code_chunks(1, first, client=client, embedder=embedder)
    assert len(client.points) == 1

    second = [_chunk("Demo.new", start=20, end=22)]
    upsert_code_chunks(1, second, client=client, embedder=embedder)

    assert len(client.deleted) == 2
    assert [point.payload["function_name"] for point in client.points] == ["Demo.new"]


def test_upsert_empty_chunks_only_deletes():
    client = FakeQdrant()
    count = upsert_code_chunks(3, [], client=client, embedder=FakeEmbedder())
    assert count == 0
    assert client.points == []
    assert len(client.deleted) == 1


def test_upsert_rejects_wrong_vector_size():
    client = FakeQdrant()
    embedder = FakeEmbedder(dim=8)
    try:
        upsert_code_chunks(1, [_chunk()], client=client, embedder=embedder)
        assert False, "expected QdrantIndexError"
    except QdrantIndexError as exc:
        assert "384" in str(exc)


def test_upsert_wraps_client_errors():
    class BrokenQdrant(FakeQdrant):
        def collection_exists(self, name: str) -> bool:
            raise RuntimeError("connection refused")

    try:
        upsert_code_chunks(1, [_chunk()], client=BrokenQdrant(), embedder=FakeEmbedder())
        assert False, "expected QdrantIndexError"
    except QdrantIndexError as exc:
        assert "connection refused" in str(exc)
