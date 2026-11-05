"""Split Solidity sources into function/modifier chunks using the solc AST."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.services.solc_compiler import CompileResult, split_source_files

SPECIAL_FUNCTION_KINDS = {"constructor", "fallback", "receive"}
CALLABLE_NODE_TYPES = {"FunctionDefinition", "ModifierDefinition"}


@dataclass(frozen=True)
class CodeChunk:
    file_path: str
    function_name: str
    start_line: int
    end_line: int
    content: str
    kind: str

    def to_payload(self, contract_id: int) -> dict:
        return {
            "contract_id": contract_id,
            "file_path": self.file_path,
            "function_name": self.function_name,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "content": self.content,
            "kind": self.kind,
        }


def split_code_chunks(
    source_code: str,
    ast: CompileResult | dict,
    filename: str | None = None,
) -> list[CodeChunk]:
    """Split concatenated or single-file Solidity into semantic code chunks.

    Chunks are functions and modifiers (not sliding windows over lines).
    Files or contracts without callables become a single fallback chunk.
    """
    files = split_source_files(source_code, default_filename=filename or "Contract.sol")
    ast_sources = _ast_sources(ast)

    chunks: list[CodeChunk] = []
    for path, content in files.items():
        entry = ast_sources.get(path) or {}
        ast_root = entry.get("ast") if isinstance(entry, dict) else None
        ast_node = ast_root if isinstance(ast_root, dict) else None
        chunks.extend(_chunks_from_file(path, content, ast_node))
    return chunks


def _ast_sources(ast: CompileResult | dict) -> dict[str, dict]:
    if isinstance(ast, CompileResult):
        return ast.sources
    sources = ast.get("sources")
    if isinstance(sources, dict):
        return sources
    return {}


def _chunks_from_file(file_path: str, content: str, ast_root: dict | None) -> list[CodeChunk]:
    if ast_root is None:
        label = _file_label(file_path)
        return [_fallback_chunk(file_path, content, function_name=label, kind="file")]

    chunks: list[CodeChunk] = []
    _walk_nodes(
        ast_root.get("nodes") or [],
        file_path,
        content,
        chunks,
        contract_name=None,
        contract_kind=None,
    )

    if chunks:
        return chunks
    return [_fallback_chunk(file_path, content, _file_label(file_path), "file")]


def _walk_nodes(
    nodes: list,
    file_path: str,
    content: str,
    chunks: list[CodeChunk],
    contract_name: str | None,
    contract_kind: str | None,
) -> None:
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_type = node.get("nodeType")
        if node_type == "ContractDefinition":
            name = node.get("name") or contract_name
            kind = node.get("contractKind") or "contract"
            before = len(chunks)
            _walk_nodes(
                node.get("nodes") or [],
                file_path,
                content,
                chunks,
                contract_name=name,
                contract_kind=kind,
            )
            if len(chunks) == before:
                chunk = _chunk_from_node(
                    node,
                    file_path,
                    content,
                    name or _file_label(file_path),
                    "interface" if kind == "interface" else "contract",
                )
                if chunk is not None:
                    chunks.append(chunk)
            continue
        if node_type in CALLABLE_NODE_TYPES:
            if _skip_unimplemented_callable(node, contract_kind):
                continue
            name, kind = _callable_name(node, contract_name)
            chunk = _chunk_from_node(node, file_path, content, name, kind)
            if chunk is not None:
                chunks.append(chunk)
            continue
        nested = node.get("nodes")
        if nested:
            _walk_nodes(
                nested,
                file_path,
                content,
                chunks,
                contract_name,
                contract_kind,
            )


def _skip_unimplemented_callable(node: dict, contract_kind: str | None) -> bool:
    """Skip IERC20-style signatures so NatSpec stubs do not drown out real code."""
    if node.get("nodeType") == "ModifierDefinition":
        return False
    if contract_kind == "interface":
        return True
    return node.get("implemented") is False


def _callable_name(node: dict, contract_name: str | None) -> tuple[str, str]:
    if node.get("nodeType") == "ModifierDefinition":
        name = (node.get("name") or "").strip() or "modifier"
        kind = "modifier"
    else:
        kind = node.get("kind") or "function"
        raw_name = (node.get("name") or "").strip()
        if kind in SPECIAL_FUNCTION_KINDS:
            name = kind
        else:
            name = raw_name or kind

    if contract_name:
        return f"{contract_name}.{name}", kind
    return name, kind


def _chunk_from_node(
    node: dict,
    file_path: str,
    content: str,
    function_name: str,
    kind: str,
) -> CodeChunk | None:
    span = _node_span(node)
    if span is None:
        return None
    start, end = span
    source_bytes = content.encode("utf-8")
    if start < 0 or end > len(source_bytes) or start >= end:
        return None
    text = source_bytes[start:end].decode("utf-8")
    if not text.strip():
        return None
    last_byte = end - 1
    return CodeChunk(
        file_path=file_path,
        function_name=function_name,
        start_line=_line_number(source_bytes, start),
        end_line=_line_number(source_bytes, last_byte),
        content=text,
        kind=kind,
    )


def _fallback_chunk(file_path: str, content: str, function_name: str, kind: str) -> CodeChunk:
    source_bytes = content.encode("utf-8")
    end = max(len(source_bytes) - 1, 0)
    return CodeChunk(
        file_path=file_path,
        function_name=function_name,
        start_line=1,
        end_line=_line_number(source_bytes, end) if source_bytes else 1,
        content=content,
        kind=kind,
    )


def _node_span(node: dict) -> tuple[int, int] | None:
    parsed = _parse_src(node.get("src"))
    if parsed is None:
        return None
    start, length, _file_id = parsed
    end = start + length

    documentation = node.get("documentation")
    if isinstance(documentation, dict):
        doc_parsed = _parse_src(documentation.get("src"))
        if doc_parsed is not None:
            doc_start, doc_length, _ = doc_parsed
            start = min(start, doc_start)
            end = max(end, doc_start + doc_length)
    return start, end


def _parse_src(src: str | None) -> tuple[int, int, int] | None:
    if not src or not isinstance(src, str):
        return None
    parts = src.split(":")
    if len(parts) != 3:
        return None
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None


def _line_number(source_bytes: bytes, offset: int) -> int:
    if offset < 0:
        return 1
    if offset > len(source_bytes):
        offset = len(source_bytes)
    return source_bytes[:offset].count(b"\n") + 1


def _file_label(file_path: str) -> str:
    stem = Path(file_path).stem
    return stem or file_path
