import pytest

from app.services import solc_compiler
from app.services.solc_compiler import (
    SolcCompileError,
    compile_to_ast,
    is_solc_available,
    split_source_files,
)

SIMPLE_SOL = """pragma solidity ^0.8.24;

contract Demo {
    function ping() external pure returns (uint256) {
        return 1;
    }
}
"""

MULTI_FILE_SOL = """// File: contracts/Owned.sol
pragma solidity ^0.8.24;

contract Owned {
    address public owner;
    constructor() { owner = msg.sender; }
}

// File: contracts/Token.sol
pragma solidity ^0.8.24;

import "./Owned.sol";

contract Token is Owned {
    function mint() external {}
}
"""

INVALID_SOL = """pragma solidity ^0.8.24;
contract Broken {
    function nope(
}
"""


def test_split_single_file():
    files = split_source_files(SIMPLE_SOL, default_filename="Demo.sol")
    assert list(files) == ["Demo.sol"]
    assert "contract Demo" in files["Demo.sol"]


def test_split_concatenated_files():
    files = split_source_files(MULTI_FILE_SOL)
    assert set(files) == {"contracts/Owned.sol", "contracts/Token.sol"}
    assert "contract Owned" in files["contracts/Owned.sol"]
    assert 'import "./Owned.sol"' in files["contracts/Token.sol"]


def test_split_rejects_parent_path():
    with pytest.raises(SolcCompileError, match="Unsafe source path"):
        split_source_files("// File: ../../etc/passwd\npragma solidity ^0.8.24;")


@pytest.mark.parametrize("path", ["/etc/passwd", "C:\\Windows\\system.ini", "\\\\server\\share\\x.sol"])
def test_split_rejects_absolute_and_windows_paths(path):
    with pytest.raises(SolcCompileError, match="Unsafe source path"):
        split_source_files(f"// File: {path}\npragma solidity ^0.8.24;")


def test_split_normalizes_dot_segments_and_rejects_duplicates():
    normalized = split_source_files("// File: contracts/./Owned.sol\ncontract Owned {}")
    assert set(normalized) == {"contracts/Owned.sol"}

    source = (
        "// File: contracts/Owned.sol\ncontract Owned {}\n"
        "// File: contracts/./Owned.sol\ncontract Owned {}"
    )
    with pytest.raises(SolcCompileError, match="Duplicate source path"):
        split_source_files(source)


def test_split_rejects_excessive_file_markers_before_mapping_them():
    source = "\n".join(f"// File: File{index}.sol\ncontract C{index} {{}}" for index in range(101))

    with pytest.raises(SolcCompileError, match="at most 100 source files"):
        split_source_files(source)


def test_split_empty_source():
    with pytest.raises(SolcCompileError, match="empty"):
        split_source_files("   ")


@pytest.mark.skipif(not is_solc_available(), reason="solc 0.8.24 is not installed")
def test_compile_simple_contract_ast():
    result = compile_to_ast(SIMPLE_SOL, compiler_version="0.8.24", filename="Demo.sol")
    assert result.solc_version == "0.8.24"
    assert "Demo.sol" in result.sources
    ast = result.sources["Demo.sol"]["ast"]
    assert ast["nodeType"] == "SourceUnit"
    contract_nodes = [
        node for node in ast.get("nodes", []) if node.get("nodeType") == "ContractDefinition"
    ]
    assert contract_nodes[0]["name"] == "Demo"
    function_nodes = [
        node
        for node in contract_nodes[0].get("nodes", [])
        if node.get("nodeType") == "FunctionDefinition"
    ]
    assert function_nodes[0]["name"] == "ping"


@pytest.mark.skipif(not is_solc_available(), reason="solc 0.8.24 is not installed")
def test_compile_multifile_ast():
    result = compile_to_ast(MULTI_FILE_SOL, compiler_version="0.8.24")
    assert set(result.sources) == {"contracts/Owned.sol", "contracts/Token.sol"}
    token_ast = result.sources["contracts/Token.sol"]["ast"]
    names = [
        node["name"]
        for node in token_ast.get("nodes", [])
        if node.get("nodeType") == "ContractDefinition"
    ]
    assert names == ["Token"]


@pytest.mark.skipif(not is_solc_available(), reason="solc 0.8.24 is not installed")
def test_compile_falls_back_to_installed_solc():
    source = """pragma solidity ^0.8.0;

contract Demo {
    function ping() external pure returns (uint256) {
        return 1;
    }
}
"""
    result = compile_to_ast(source, compiler_version="0.8.0", filename="Demo.sol")
    assert result.solc_version == "0.8.24"
    ast = result.sources["Demo.sol"]["ast"]
    assert ast["nodeType"] == "SourceUnit"


def test_select_installed_version_fallback():
    from app.services.solc_compiler import SolcCompileError, _select_installed_version

    assert _select_installed_version("0.8.24", {"0.8.24"}) == "0.8.24"
    assert _select_installed_version("0.8.0", {"0.8.24"}) == "0.8.24"
    assert _select_installed_version("0.7.6", {"0.8.19", "0.8.24"}) == "0.8.24"
    with pytest.raises(SolcCompileError, match="not installed"):
        _select_installed_version("0.8.0", set())


@pytest.mark.skipif(not is_solc_available(), reason="solc 0.8.24 is not installed")
def test_compile_invalid_source():
    with pytest.raises(SolcCompileError, match="Solidity compilation failed"):
        compile_to_ast(INVALID_SOL, compiler_version="0.8.24")


def test_compile_to_ast_uses_analyzer_and_preserves_compile_result(monkeypatch):
    payload = {
        "solc_version": "0.8.24",
        "sources": {
            "Demo.sol": {
                "id": 0,
                "ast": {"nodeType": "SourceUnit", "nodes": []},
            }
        },
    }
    captured = {}

    class FakeResponse:
        status_code = 200

        def iter_bytes(self):
            import json

            yield json.dumps(payload).encode("utf-8")

    class FakeStream:
        def __enter__(self):
            return FakeResponse()

        def __exit__(self, *_args):
            return False

    def fake_stream(method, url, **kwargs):
        captured.update(method=method, url=url, kwargs=kwargs)
        return FakeStream()

    monkeypatch.setattr(solc_compiler.httpx, "stream", fake_stream)

    result = compile_to_ast(SIMPLE_SOL, compiler_version="0.8.24", filename="Demo.sol")

    assert result.to_dict() == payload
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/compile")
    assert captured["kwargs"]["headers"]["content-type"] == "application/json"


def test_compile_to_ast_returns_sanitized_analyzer_error(monkeypatch):
    class FakeResponse:
        status_code = 422

        def iter_bytes(self):
            yield b'{"detail":"Solidity compilation failed (ParserError, 2314) at Demo.sol:3"}'

    class FakeStream:
        def __enter__(self):
            return FakeResponse()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(solc_compiler.httpx, "stream", lambda *_args, **_kwargs: FakeStream())

    with pytest.raises(SolcCompileError, match="ParserError, 2314") as exc_info:
        compile_to_ast(INVALID_SOL, compiler_version="0.8.24")

    assert "contract Broken" not in str(exc_info.value)
