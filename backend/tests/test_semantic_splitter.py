import pytest

from app.services.semantic_splitter import CodeChunk, split_code_chunks
from app.services.solc_compiler import CompileResult, is_solc_available

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


def _src(content: str, snippet: str, file_id: int = 0) -> str:
    encoded = content.encode("utf-8")
    needle = snippet.encode("utf-8")
    start = encoded.find(needle)
    assert start >= 0, f"snippet not found: {snippet!r}"
    return f"{start}:{len(needle)}:{file_id}"


def _compile_result(path: str, ast: dict) -> CompileResult:
    return CompileResult(solc_version="0.8.24", sources={path: {"id": 0, "ast": ast}})


def test_splits_function_not_by_lines():
    source = (
        "pragma solidity ^0.8.24;\n"
        "contract Demo {\n"
        "    function ping() external pure returns (uint256) {\n"
        "        return 1;\n"
        "    }\n"
        "}\n"
    )
    snippet = (
        "function ping() external pure returns (uint256) {\n"
        "        return 1;\n"
        "    }"
    )
    ast = {
        "nodeType": "SourceUnit",
        "nodes": [
            {
                "nodeType": "ContractDefinition",
                "name": "Demo",
                "nodes": [
                    {
                        "nodeType": "FunctionDefinition",
                        "name": "ping",
                        "kind": "function",
                        "src": _src(source, snippet),
                    }
                ],
            }
        ],
    }

    chunks = split_code_chunks(source, _compile_result("Demo.sol", ast), filename="Demo.sol")

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.kind == "function"
    assert chunk.function_name == "Demo.ping"
    assert chunk.file_path == "Demo.sol"
    assert chunk.start_line == 3
    assert chunk.end_line == 5
    assert "function ping" in chunk.content
    assert "return 1" in chunk.content
    assert chunk.content.count("function ") == 1


def test_splits_modifier_and_constructor():
    source = (
        "contract Vault {\n"
        "    modifier onlyOwner() {\n"
        "        _;\n"
        "    }\n"
        "    constructor() {}\n"
        "}\n"
    )
    modifier = "modifier onlyOwner() {\n        _;\n    }"
    constructor = "constructor() {}"
    ast = {
        "nodeType": "SourceUnit",
        "nodes": [
            {
                "nodeType": "ContractDefinition",
                "name": "Vault",
                "nodes": [
                    {
                        "nodeType": "ModifierDefinition",
                        "name": "onlyOwner",
                        "src": _src(source, modifier),
                    },
                    {
                        "nodeType": "FunctionDefinition",
                        "name": "",
                        "kind": "constructor",
                        "src": _src(source, constructor),
                    },
                ],
            }
        ],
    }

    chunks = split_code_chunks(source, _compile_result("Vault.sol", ast), filename="Vault.sol")
    names = [chunk.function_name for chunk in chunks]
    kinds = [chunk.kind for chunk in chunks]
    assert names == ["Vault.onlyOwner", "Vault.constructor"]
    assert kinds == ["modifier", "constructor"]


def test_fallback_receive_and_free_function_names():
    source = (
        "function add(uint256 a, uint256 b) pure returns (uint256) { return a + b; }\n"
        "contract Demo {\n"
        "    fallback() external payable {}\n"
        "    receive() external payable {}\n"
        "}\n"
    )
    free_fn = "function add(uint256 a, uint256 b) pure returns (uint256) { return a + b; }"
    fallback = "fallback() external payable {}"
    receive = "receive() external payable {}"
    ast = {
        "nodeType": "SourceUnit",
        "nodes": [
            {
                "nodeType": "FunctionDefinition",
                "name": "add",
                "kind": "freeFunction",
                "src": _src(source, free_fn),
            },
            {
                "nodeType": "ContractDefinition",
                "name": "Demo",
                "nodes": [
                    {
                        "nodeType": "FunctionDefinition",
                        "name": "",
                        "kind": "fallback",
                        "src": _src(source, fallback),
                    },
                    {
                        "nodeType": "FunctionDefinition",
                        "name": "",
                        "kind": "receive",
                        "src": _src(source, receive),
                    },
                ],
            },
        ],
    }

    chunks = split_code_chunks(source, _compile_result("Demo.sol", ast), filename="Demo.sol")
    assert [chunk.function_name for chunk in chunks] == [
        "add",
        "Demo.fallback",
        "Demo.receive",
    ]
    assert [chunk.kind for chunk in chunks] == ["freeFunction", "fallback", "receive"]


def test_includes_natspec_documentation():
    source = "/// @notice ping helper\nfunction ping() {}\n"
    function_src = "function ping() {}"
    doc_src = "/// @notice ping helper\n"
    ast = {
        "nodeType": "SourceUnit",
        "nodes": [
            {
                "nodeType": "FunctionDefinition",
                "name": "ping",
                "kind": "freeFunction",
                "src": _src(source, function_src),
                "documentation": {
                    "nodeType": "StructuredDocumentation",
                    "src": _src(source, doc_src),
                    "text": "@notice ping helper",
                },
            }
        ],
    }

    chunks = split_code_chunks(source, _compile_result("Lib.sol", ast), filename="Lib.sol")
    assert len(chunks) == 1
    assert chunks[0].content.startswith("/// @notice ping helper")
    assert "function ping() {}" in chunks[0].content
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 2


def test_utf8_line_offsets():
    source = "// проверка\nfunction ping() {}\n"
    snippet = "function ping() {}"
    ast = {
        "nodeType": "SourceUnit",
        "nodes": [
            {
                "nodeType": "FunctionDefinition",
                "name": "ping",
                "kind": "freeFunction",
                "src": _src(source, snippet),
            }
        ],
    }

    chunks = split_code_chunks(source, _compile_result("Cyr.sol", ast), filename="Cyr.sol")
    assert chunks[0].start_line == 2
    assert chunks[0].end_line == 2
    assert chunks[0].content == snippet


def test_contract_without_callables_is_one_chunk():
    source = "pragma solidity ^0.8.24;\ncontract Empty { uint256 public x; }\n"
    contract_src = "contract Empty { uint256 public x; }"
    ast = {
        "nodeType": "SourceUnit",
        "nodes": [
            {
                "nodeType": "ContractDefinition",
                "name": "Empty",
                "src": _src(source, contract_src),
                "nodes": [{"nodeType": "VariableDeclaration", "name": "x"}],
            }
        ],
    }

    chunks = split_code_chunks(source, _compile_result("Empty.sol", ast), filename="Empty.sol")
    assert len(chunks) == 1
    assert chunks[0].kind == "contract"
    assert chunks[0].function_name == "Empty"
    assert "uint256 public x" in chunks[0].content


def test_missing_ast_falls_back_to_file_chunk():
    source = "pragma solidity ^0.8.24;\ncontract Demo {}\n"
    ast = CompileResult(solc_version="0.8.24", sources={})

    chunks = split_code_chunks(source, ast, filename="Demo.sol")
    assert len(chunks) == 1
    assert chunks[0].kind == "file"
    assert chunks[0].function_name == "Demo"
    assert chunks[0].content == source.strip()
    assert chunks[0].start_line == 1


def test_skips_nodes_with_invalid_src():
    source = "function ping() {}\n"
    ast = {
        "nodeType": "SourceUnit",
        "nodes": [
            {
                "nodeType": "FunctionDefinition",
                "name": "ping",
                "kind": "freeFunction",
                "src": "not-a-src",
            },
            {
                "nodeType": "FunctionDefinition",
                "name": "ok",
                "kind": "freeFunction",
                "src": _src(source, "function ping() {}"),
            },
        ],
    }

    chunks = split_code_chunks(source, _compile_result("Demo.sol", ast), filename="Demo.sol")
    assert [chunk.function_name for chunk in chunks] == ["ok"]


def test_accepts_ast_dict_and_payload_shape():
    source = "function ping() {}\n"
    ast = {
        "solc_version": "0.8.24",
        "sources": {
            "Demo.sol": {
                "id": 0,
                "ast": {
                    "nodeType": "SourceUnit",
                    "nodes": [
                        {
                            "nodeType": "FunctionDefinition",
                            "name": "ping",
                            "kind": "freeFunction",
                            "src": _src(source, "function ping() {}"),
                        }
                    ],
                },
            }
        },
    }

    chunks = split_code_chunks(source, ast, filename="Demo.sol")
    payload = chunks[0].to_payload(contract_id=42)
    assert payload == {
        "contract_id": 42,
        "file_path": "Demo.sol",
        "function_name": "ping",
        "start_line": 1,
        "end_line": 1,
        "content": "function ping() {}",
        "kind": "freeFunction",
    }


def test_multifile_chunks_keep_paths():
    owned = (
        "pragma solidity ^0.8.24;\n\n"
        "contract Owned {\n"
        "    address public owner;\n"
        "    constructor() { owner = msg.sender; }\n"
        "}"
    )
    token = (
        "pragma solidity ^0.8.24;\n\n"
        'import "./Owned.sol";\n\n'
        "contract Token is Owned {\n"
        "    function mint() external {}\n"
        "}"
    )
    ast = CompileResult(
        solc_version="0.8.24",
        sources={
            "contracts/Owned.sol": {
                "id": 0,
                "ast": {
                    "nodeType": "SourceUnit",
                    "nodes": [
                        {
                            "nodeType": "ContractDefinition",
                            "name": "Owned",
                            "nodes": [
                                {
                                    "nodeType": "FunctionDefinition",
                                    "name": "",
                                    "kind": "constructor",
                                    "src": _src(owned, "constructor() { owner = msg.sender; }"),
                                }
                            ],
                        }
                    ],
                },
            },
            "contracts/Token.sol": {
                "id": 1,
                "ast": {
                    "nodeType": "SourceUnit",
                    "nodes": [
                        {
                            "nodeType": "ContractDefinition",
                            "name": "Token",
                            "nodes": [
                                {
                                    "nodeType": "FunctionDefinition",
                                    "name": "mint",
                                    "kind": "function",
                                    "src": _src(token, "function mint() external {}"),
                                }
                            ],
                        }
                    ],
                },
            },
        },
    )

    chunks = split_code_chunks(MULTI_FILE_SOL, ast)
    by_name = {chunk.function_name: chunk for chunk in chunks}
    assert set(by_name) == {"Owned.constructor", "Token.mint"}
    assert by_name["Owned.constructor"].file_path == "contracts/Owned.sol"
    assert by_name["Token.mint"].file_path == "contracts/Token.sol"


def test_codechunk_is_frozen():
    chunk = CodeChunk(
        file_path="A.sol",
        function_name="x",
        start_line=1,
        end_line=1,
        content="x",
        kind="function",
    )
    with pytest.raises(AttributeError):
        chunk.kind = "modifier"


@pytest.mark.skipif(not is_solc_available(), reason="solc 0.8.24 is not installed")
def test_split_uses_real_solc_ast():
    from app.services.solc_compiler import compile_to_ast

    result = compile_to_ast(SIMPLE_SOL, compiler_version="0.8.24", filename="Demo.sol")
    chunks = split_code_chunks(SIMPLE_SOL, result, filename="Demo.sol")

    assert [chunk.function_name for chunk in chunks] == ["Demo.ping"]
    ping = chunks[0]
    assert ping.kind == "function"
    assert ping.start_line == 4
    assert ping.end_line == 6
    assert "return 1" in ping.content


@pytest.mark.skipif(not is_solc_available(), reason="solc 0.8.24 is not installed")
def test_split_real_multifile_and_special_kinds():
    from app.services.solc_compiler import compile_to_ast

    source = (
        "pragma solidity ^0.8.24;\n\n"
        "/// @notice Adds two numbers\n"
        "function add(uint256 a, uint256 b) pure returns (uint256) {\n"
        "    return a + b;\n"
        "}\n\n"
        "contract Demo {\n"
        "    address public owner;\n\n"
        "    modifier onlyOwner() {\n"
        "        require(msg.sender == owner);\n"
        "        _;\n"
        "    }\n\n"
        "    constructor() {\n"
        "        owner = msg.sender;\n"
        "    }\n\n"
        "    function transfer(address to) external onlyOwner {\n"
        "        payable(to).transfer(1);\n"
        "    }\n\n"
        "    fallback() external payable {}\n"
        "    receive() external payable {}\n"
        "}\n"
    )
    result = compile_to_ast(source, compiler_version="0.8.24", filename="Demo.sol")
    chunks = split_code_chunks(source, result, filename="Demo.sol")
    names = [chunk.function_name for chunk in chunks]
    assert names == [
        "add",
        "Demo.onlyOwner",
        "Demo.constructor",
        "Demo.transfer",
        "Demo.fallback",
        "Demo.receive",
    ]
    add_chunk = chunks[0]
    assert add_chunk.content.startswith("/// @notice Adds two numbers")
    assert add_chunk.kind == "freeFunction"

    result = compile_to_ast(MULTI_FILE_SOL, compiler_version="0.8.24")
    chunks = split_code_chunks(MULTI_FILE_SOL, result)
    assert {chunk.function_name for chunk in chunks} == {"Owned.constructor", "Token.mint"}


def test_skips_interface_function_stubs():
    source = (
        "pragma solidity ^0.8.24;\n"
        "interface IERC20 {\n"
        "    function transfer(address to, uint256 amount) external returns (bool);\n"
        "}\n"
        "contract SafeToken is IERC20 {\n"
        "    function transfer(address to, uint256 amount) external returns (bool) {\n"
        "        return true;\n"
        "    }\n"
        "}\n"
    )
    ierc20_fn = "function transfer(address to, uint256 amount) external returns (bool);"
    ierc20_contract = (
        "interface IERC20 {\n"
        "    function transfer(address to, uint256 amount) external returns (bool);\n"
        "}"
    )
    impl_fn = (
        "function transfer(address to, uint256 amount) external returns (bool) {\n"
        "        return true;\n"
        "    }"
    )
    ast = {
        "nodeType": "SourceUnit",
        "nodes": [
            {
                "nodeType": "ContractDefinition",
                "name": "IERC20",
                "contractKind": "interface",
                "src": _src(source, ierc20_contract),
                "nodes": [
                    {
                        "nodeType": "FunctionDefinition",
                        "name": "transfer",
                        "kind": "function",
                        "implemented": False,
                        "src": _src(source, ierc20_fn),
                    }
                ],
            },
            {
                "nodeType": "ContractDefinition",
                "name": "SafeToken",
                "contractKind": "contract",
                "nodes": [
                    {
                        "nodeType": "FunctionDefinition",
                        "name": "transfer",
                        "kind": "function",
                        "implemented": True,
                        "src": _src(source, impl_fn),
                    }
                ],
            },
        ],
    }
    chunks = split_code_chunks(source, _compile_result("contract_safe.sol", ast), filename="contract_safe.sol")
    names = [chunk.function_name for chunk in chunks]
    assert "IERC20.transfer" not in names
    assert "SafeToken.transfer" in names
    assert any(chunk.kind == "interface" and chunk.function_name == "IERC20" for chunk in chunks)
