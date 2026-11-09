"""Hybrid BM25 + vector retrieval and citation selection."""

from __future__ import annotations

import logging
import math
import re
from typing import Any

from app.schemas.chat import Citation
from app.services.qdrant_indexer import (
    QdrantIndexError,
    scroll_contract_chunks,
    search_similar_chunks,
)
from app.services.rag.prompting import is_overview_question
from app.services.rag.types import RagError, RetrievedChunk

logger = logging.getLogger(__name__)

TOP_K = 8
VECTOR_CANDIDATES = 16
RRF_K = 60
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+|[^\W\d_]+", re.UNICODE)
OVERVIEW_FULL_SOURCE_CHARS = 24_000
PRIMARY_ALL_CHUNKS_LIMIT = 24
OVERVIEW_FINDINGS_LIMIT = 8
OVERVIEW_CODE_CHUNKS = 8
FILE_PATH_RE = re.compile(r"[\w./\\-]+\.sol\b", re.IGNORECASE)
IMPACT_RANK = {
    "high": 0,
    "medium": 1,
    "low": 2,
    "informational": 3,
    "optimization": 4,
}
FUNCTION_BODY_RE = re.compile(
    r"\b(?:function|modifier|constructor|fallback|receive)\b[^{;]*\{",
    re.IGNORECASE | re.DOTALL,
)
FUNCTION_STUB_RE = re.compile(
    r"\bfunction\b[^{;]*;",
    re.IGNORECASE | re.DOTALL,
)
TYPE_HEADER_RE = re.compile(r"\b(?:interface|contract|library)\s+\w+")


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text)]


def bm25_scores(
    query: str,
    documents: list[str],
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> list[float]:
    tokenized = [tokenize(doc) for doc in documents]
    query_tokens = tokenize(query)
    n_docs = len(documents)
    if n_docs == 0:
        return []
    avgdl = sum(len(doc) for doc in tokenized) / n_docs
    df: dict[str, int] = {}
    for doc in tokenized:
        for term in set(doc):
            df[term] = df.get(term, 0) + 1

    scores: list[float] = []
    for doc in tokenized:
        tf: dict[str, int] = {}
        for term in doc:
            tf[term] = tf.get(term, 0) + 1
        dl = len(doc) or 1
        score = 0.0
        for term in query_tokens:
            n_q = df.get(term, 0)
            if n_q == 0:
                continue
            idf = math.log(1 + (n_docs - n_q + 0.5) / (n_q + 0.5))
            freq = tf.get(term, 0)
            denom = freq + k1 * (1 - b + b * dl / avgdl)
            score += idf * (freq * (k1 + 1)) / denom
        scores.append(score)
    return scores


def reciprocal_rank_fusion(
    ranked_lists: list[list[int]],
    *,
    k: int = RRF_K,
) -> list[tuple[int, float]]:
    scores: dict[int, float] = {}
    for ranking in ranked_lists:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def chunk_from_payload(payload: dict, score: float = 0.0) -> RetrievedChunk | None:
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        start_line = int(payload.get("start_line") or 1)
        end_line = int(payload.get("end_line") or start_line)
    except (TypeError, ValueError):
        start_line, end_line = 1, 1
    return RetrievedChunk(
        file_path=str(payload.get("file_path") or "Contract.sol"),
        function_name=str(payload.get("function_name") or ""),
        start_line=start_line,
        end_line=end_line,
        content=content,
        kind=str(payload.get("kind") or ""),
        score=score,
    )


def strip_solidity_comments(source: str) -> str:
    without_block = re.sub(r"/\*.*?\*/", " ", source, flags=re.DOTALL)
    return re.sub(r"//.*?$", " ", without_block, flags=re.MULTILINE)


def has_implementation_body(content: str, kind: str = "") -> bool:
    if kind == "interface":
        return False
    return bool(FUNCTION_BODY_RE.search(strip_solidity_comments(content)))


def is_function_signature_stub(content: str) -> bool:
    stripped = strip_solidity_comments(content)
    if has_implementation_body(stripped):
        return False
    if TYPE_HEADER_RE.search(stripped):
        return False
    return bool(FUNCTION_STUB_RE.search(stripped))


def citations_are_interface_stubs(citations: list[Citation]) -> bool:
    if not citations:
        return False
    return all(is_function_signature_stub(item.content) for item in citations)


def load_search_pool(
    contract_id: int,
    question: str,
    *,
    generation: str | None = None,
    client=None,
    primary_names: set[str] | None = None,
) -> list[RetrievedChunk]:
    try:
        payloads = scroll_contract_chunks(
            contract_id, generation=generation, client=client
        )
    except Exception as exc:
        logger.exception("Failed to scroll Qdrant chunks")
        raise RagError(f"Qdrant scroll failed: {exc}") from exc

    chunks: list[RetrievedChunk] = []
    seen: set[tuple] = set()
    for payload in payloads:
        chunk = chunk_from_payload(payload)
        if chunk is None or chunk.identity() in seen:
            continue
        seen.add(chunk.identity())
        chunks.append(chunk)

    if not chunks:
        return []
    return _search_pool(chunks, primary_names, question)


def hybrid_search(
    contract_id: int,
    question: str,
    *,
    generation: str | None = None,
    top_k: int = TOP_K,
    client=None,
    embedder=None,
    primary_names: set[str] | None = None,
    dump_all_primary: bool = True,
    pool: list[RetrievedChunk] | None = None,
) -> list[RetrievedChunk]:
    if pool is None:
        pool = load_search_pool(
            contract_id,
            question,
            generation=generation,
            client=client,
            primary_names=primary_names,
        )
    if not pool:
        return []

    impl_indices = [
        index
        for index, chunk in enumerate(pool)
        if has_implementation_body(chunk.content, chunk.kind)
    ]
    if (
        dump_all_primary
        and primary_names
        and impl_indices
        and len(impl_indices) <= PRIMARY_ALL_CHUNKS_LIMIT
    ):
        selected = [pool[index] for index in impl_indices]
        return sorted(
            selected,
            key=lambda chunk: (chunk.file_path, chunk.start_line, chunk.function_name),
        )

    bm25 = bm25_scores(question, [chunk.content for chunk in pool])
    bm25_ranked = sorted(range(len(pool)), key=lambda i: bm25[i], reverse=True)

    vector_ranked: list[int] = []
    try:
        hits = search_similar_chunks(
            contract_id,
            question,
            limit=max(top_k * 2, VECTOR_CANDIDATES),
            generation=generation,
            client=client,
            embedder=embedder,
        )
        identity_to_pool = {chunk.identity(): index for index, chunk in enumerate(pool)}
        for payload, score in hits:
            hit = chunk_from_payload(payload, score=score)
            if hit is None:
                continue
            idx = identity_to_pool.get(hit.identity())
            if idx is not None and idx not in vector_ranked:
                vector_ranked.append(idx)
    except (QdrantIndexError, Exception) as exc:
        logger.warning("Vector search unavailable, using BM25 only: %s", exc)

    lists = [bm25_ranked]
    if vector_ranked:
        lists.append(vector_ranked)
    fused = reciprocal_rank_fusion(lists)
    return _prefer_implementation_chunks(pool, fused, impl_indices, top_k)


def _prefer_implementation_chunks(
    chunks: list[RetrievedChunk],
    fused: list[tuple[int, float]],
    impl_indices: list[int],
    top_k: int,
) -> list[RetrievedChunk]:
    if not impl_indices:
        return [chunks[idx] for idx, _score in fused[:top_k]]
    impl_set = set(impl_indices)
    selected: list[RetrievedChunk] = []
    for idx, _score in fused:
        if idx in impl_set:
            selected.append(chunks[idx])
            if len(selected) >= top_k:
                return selected
    return selected


def primary_contract_names(ast: Any) -> set[str]:
    """Leaf contracts: not interface/library and not used as someone else's base."""
    definitions: list[dict] = []
    used_as_base: set[str] = set()
    for node in _iter_ast_nodes(ast):
        if not isinstance(node, dict) or node.get("nodeType") != "ContractDefinition":
            continue
        definitions.append(node)
        for base in node.get("baseContracts") or []:
            if not isinstance(base, dict):
                continue
            name = (base.get("baseName") or {}).get("name")
            if name:
                used_as_base.add(str(name))
    names: set[str] = set()
    for node in definitions:
        kind = node.get("contractKind") or "contract"
        if kind in {"interface", "library"}:
            continue
        name = (node.get("name") or "").strip()
        if not name or name in used_as_base:
            continue
        names.add(name)
    return names


def _iter_ast_nodes(ast: Any):
    if ast is None:
        return
    if isinstance(ast, dict) and "sources" in ast and isinstance(ast.get("sources"), dict):
        for entry in ast["sources"].values():
            if not isinstance(entry, dict):
                continue
            root = entry.get("ast")
            if isinstance(root, dict):
                yield from _walk_ast_dict(root)
        return
    if isinstance(ast, dict):
        yield from _walk_ast_dict(ast)


def _walk_ast_dict(node: dict):
    yield node
    nested = node.get("nodes")
    if isinstance(nested, list):
        for child in nested:
            if isinstance(child, dict):
                yield from _walk_ast_dict(child)


def chunk_contract_name(chunk: RetrievedChunk) -> str:
    name = chunk.function_name or ""
    if "." in name:
        return name.split(".", 1)[0]
    return name


def _search_pool(
    chunks: list[RetrievedChunk],
    primary_names: set[str] | None,
    question: str,
) -> list[RetrievedChunk]:
    if not primary_names:
        return chunks
    primary = [chunk for chunk in chunks if chunk_contract_name(chunk) in primary_names]
    if not primary:
        return chunks
    lowered = question.lower()
    extra = [
        chunk
        for chunk in chunks
        if chunk not in primary and chunk_contract_name(chunk).lower() in lowered
    ]
    return primary + extra


def retrieve_for_chat(
    contract,
    question: str,
    *,
    retriever=None,
    client=None,
    embedder=None,
) -> list[RetrievedChunk]:
    overview = is_overview_question(question)
    if retriever is not None:
        chunks = retriever(contract.id, question)
        if overview:
            chunks = select_overview_code_chunks(contract, chunks) or chunks
        return apply_contract_context(contract, question, chunks)
    if _should_use_full_source(contract):
        return apply_contract_context(contract, question, [])
    primary_names = primary_contract_names(getattr(contract, "ast", None)) or None
    if overview:
        pool = load_search_pool(
            contract.id,
            question,
            generation=getattr(contract, "analysis_generation_id", None),
            client=client,
            primary_names=primary_names,
        )
        chunks = select_overview_code_chunks(contract, pool)
        if not chunks:
            chunks = hybrid_search(
                contract.id,
                question,
                generation=getattr(contract, "analysis_generation_id", None),
                client=client,
                embedder=embedder,
                primary_names=primary_names,
                dump_all_primary=False,
                pool=pool,
            )
    else:
        chunks = hybrid_search(
            contract.id,
            question,
            generation=getattr(contract, "analysis_generation_id", None),
            client=client,
            embedder=embedder,
            primary_names=primary_names,
            dump_all_primary=True,
        )
    return apply_contract_context(contract, question, chunks)


def select_overview_code_chunks(
    contract,
    pool: list[RetrievedChunk],
) -> list[RetrievedChunk]:
    findings = top_findings(getattr(contract, "slither_report", None))
    if not pool or not findings:
        return []
    selected: list[RetrievedChunk] = []
    seen: set[tuple] = set()
    for finding in findings:
        for chunk in pool:
            identity = chunk.identity()
            if identity in seen or chunk.kind == "analysis":
                continue
            if not chunk_matches_finding(chunk, finding):
                continue
            seen.add(identity)
            selected.append(chunk)
            if len(selected) >= OVERVIEW_CODE_CHUNKS:
                return selected
    return selected


def top_findings(report: Any, *, limit: int = OVERVIEW_FINDINGS_LIMIT) -> list[dict]:
    from app.services.pdf_report import extract_slither_findings

    findings = extract_slither_findings(report if isinstance(report, dict) else None)
    findings.sort(
        key=lambda item: IMPACT_RANK.get(str(item.get("impact") or "").lower(), 9),
    )
    return findings[:limit]


def chunk_matches_finding(chunk: RetrievedChunk, finding: dict) -> bool:
    function_hint = str(finding.get("function_name") or "").strip()
    function_name = chunk.function_name or ""
    short = function_name.rsplit(".", 1)[-1]
    if function_hint:
        return function_hint == short or function_hint in function_name
    wanted = str(finding.get("file_path") or "").replace("\\", "/")
    chunk_path = chunk.file_path.replace("\\", "/")
    if wanted and (
        wanted == chunk_path
        or wanted.rsplit("/", 1)[-1] == chunk_path.rsplit("/", 1)[-1]
    ):
        start = finding.get("start_line")
        end = finding.get("end_line") or start
        if isinstance(start, int) and isinstance(end, int):
            return chunk.start_line <= end and chunk.end_line >= start
    return False


def citations_from_chunks(
    chunks: list[RetrievedChunk],
    answer: str | None = None,
) -> list[Citation]:
    code = [chunk for chunk in chunks if chunk.kind != "analysis"]
    allowed = {chunk.file_path for chunk in code}
    mentioned = _mentioned_retrieved_paths(answer, allowed)
    if mentioned:
        code = [chunk for chunk in code if chunk.file_path in mentioned]
    return [chunk.to_citation() for chunk in code]


def _mentioned_retrieved_paths(answer: str | None, allowed: set[str]) -> set[str]:
    if not answer:
        return set()
    found: set[str] = set()
    allowed_by_base = {path.rsplit("/", 1)[-1]: path for path in allowed}
    for match in FILE_PATH_RE.findall(answer):
        path = match.replace("\\", "/")
        if path in allowed:
            found.add(path)
            continue
        base = path.rsplit("/", 1)[-1]
        if base in allowed_by_base:
            found.add(allowed_by_base[base])
    return found


def apply_contract_context(
    contract,
    question: str,
    chunks: list[RetrievedChunk],
) -> list[RetrievedChunk]:
    selected: list[RetrievedChunk] = []
    if chunks:
        selected.extend(chunks)
    elif _should_use_full_source(contract):
        source = (getattr(contract, "source_code", None) or "").strip()
        filename = _contract_filename(contract)
        selected.append(
            RetrievedChunk(
                file_path=filename,
                function_name=getattr(contract, "name", None) or filename,
                start_line=1,
                end_line=max(source.count("\n") + 1, 1),
                content=source,
                kind="file",
            )
        )
    if selected:
        analysis = _analysis_chunk(contract)
        if analysis is not None:
            selected.append(analysis)
    return selected


def _should_use_full_source(contract) -> bool:
    if not _small_source(contract):
        return False
    ast = getattr(contract, "ast", None)
    if not ast:
        return False
    return not primary_contract_names(ast)


def _small_source(contract) -> bool:
    source = getattr(contract, "source_code", None) or ""
    return bool(source.strip()) and len(source) <= OVERVIEW_FULL_SOURCE_CHARS


def _contract_filename(contract) -> str:
    name = (getattr(contract, "name", None) or "").strip()
    if not name:
        return "Contract.sol"
    if name.lower().endswith(".sol"):
        return name
    return f"{name}.sol"


def _analysis_chunk(contract) -> RetrievedChunk | None:
    risk = getattr(contract, "risk_score", None)
    report = getattr(contract, "slither_report", None) or {}
    if not isinstance(report, dict):
        report = {}
    detectors = (report.get("results") or {}).get("detectors") or []
    if not risk and not detectors:
        return None
    lines = [f"Risk score: {risk or 'unknown'}"]
    findings = top_findings(report)
    if not findings:
        lines.append("Slither: no detectors.")
    else:
        lines.append("Top findings and affected functions:")
        for item in findings:
            check = item.get("check") or "unknown"
            impact = item.get("impact") or ""
            target = item.get("function_name") or item.get("location") or "unknown"
            lines.append(f"- [{impact}] {check} in {target}")
    return RetrievedChunk(
        file_path="analysis.slither",
        function_name="Slither.report",
        start_line=1,
        end_line=len(lines),
        content="\n".join(lines),
        kind="analysis",
    )
