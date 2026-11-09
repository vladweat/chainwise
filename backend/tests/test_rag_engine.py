from decimal import Decimal
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.services.rag_engine import (
    LLMUsage,
    RetrievedChunk,
    bm25_scores,
    build_messages,
    call_openai,
    chunk_from_payload,
    citations_are_interface_stubs,
    estimate_cost,
    hybrid_search,
    load_cached_answer,
    question_hash,
    reciprocal_rank_fusion,
    save_cached_answer,
    tokenize,
)
from app.schemas.chat import Citation
from app.services.qdrant_indexer import COLLECTION_NAME, VECTOR_SIZE


def test_tokenize_keeps_solidity_identifiers():
    tokens = tokenize("function onlyOwner() { msg.sender; }")
    assert "function" in tokens
    assert "onlyowner" in tokens
    assert "msg" in tokens
    assert "sender" in tokens


def test_bm25_ranks_relevant_document_first():
    docs = [
        "function mint(address to, uint256 amount) external onlyOwner {}",
        "function ping() external view returns (bool) { return true; }",
        "modifier onlyOwner() { require(msg.sender == owner); _; }",
    ]
    scores = bm25_scores("who can mint tokens onlyOwner", docs)
    assert scores[0] == max(scores)


def test_reciprocal_rank_fusion_prefers_overlap():
    fused = reciprocal_rank_fusion([[0, 1, 2], [1, 0, 2]])
    assert fused[0][0] in (0, 1)
    assert {idx for idx, _score in fused} == {0, 1, 2}


def test_question_hash_is_sha256_hex():
    digest = question_hash("Is transfer safe?")
    assert len(digest) == 64
    assert digest == hashlib.sha256(b"v7\nIs transfer safe?").hexdigest()
    assert digest == question_hash("Is transfer safe?")
    assert digest != question_hash("is transfer safe?")


def test_question_hash_is_scoped_to_successful_analysis_generation():
    assert question_hash("same question", "attempt-one") != question_hash(
        "same question", "attempt-two"
    )


def test_answer_cache_lookup_uses_contract_generation(monkeypatch):
    from app.services.rag import engine
    from app.services.rag.types import ChatResult

    generation_ids = []
    cached = ChatResult(answer="cached", citations=[], cached=True)

    def load(_db, _contract_id, _question, *, generation_id):
        generation_ids.append(generation_id)
        return cached

    monkeypatch.setattr(engine, "load_cached_answer", load)
    monkeypatch.setattr(engine, "persist_chat_turn", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "_refresh_ready_contract", lambda _db, value: value)
    contract = SimpleNamespace(
        id=71, status="ready", analysis_generation_id="generation-current"
    )
    user = SimpleNamespace(id=7)

    result = engine.answer_question(object(), contract, user, "same question")

    assert result is cached
    assert generation_ids == ["generation-current"]


@pytest.mark.parametrize("current_status", ["ready", "failed"])
def test_answer_uses_fresh_contract_status_and_generation(monkeypatch, current_status):
    from app.models.contract import Contract
    from app.services.rag import engine
    from app.services.rag.types import ChatResult

    current_contract = SimpleNamespace(
        id=71,
        status=current_status,
        analysis_generation_id="generation-new",
    )

    class FakeQuery:
        def filter(self, *_args):
            return self

        def populate_existing(self):
            return self

        def first(self):
            return current_contract

    class FakeSession:
        def query(self, model):
            assert model is Contract
            return FakeQuery()

        def expire(self, _instance, _attributes):
            pass

    generations = []

    def load(_db, _contract_id, _question, *, generation_id):
        generations.append(generation_id)
        return ChatResult(answer="cached", citations=[], cached=True)

    monkeypatch.setattr(engine, "load_cached_answer", load)
    monkeypatch.setattr(engine, "persist_chat_turn", lambda *_args, **_kwargs: None)
    stale_contract = SimpleNamespace(
        id=71,
        status="ready",
        analysis_generation_id="generation-old",
    )
    user = SimpleNamespace(id=7)

    if current_status == "failed":
        with pytest.raises(HTTPException) as error:
            engine.answer_question(FakeSession(), stale_contract, user, "same question")
        assert error.value.status_code == 409
        assert generations == []
    else:
        result = engine.answer_question(
            FakeSession(), stale_contract, user, "same question"
        )
        assert result.answer == "cached"
        assert generations == ["generation-new"]


def test_stream_cache_lookup_uses_contract_generation(monkeypatch):
    from app.services.rag import engine
    from app.services.rag.types import ChatResult

    generation_ids = []
    cached = ChatResult(answer="cached", citations=[], cached=True)

    class FakeSession:
        def close(self):
            pass

    monkeypatch.setattr(engine, "SessionLocal", FakeSession)
    monkeypatch.setattr(
        engine,
        "load_cached_answer",
        lambda _db, _contract_id, _question, *, generation_id: (
            generation_ids.append(generation_id) or cached
        ),
    )
    monkeypatch.setattr(engine, "persist_chat_turn", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(engine, "_refresh_ready_contract", lambda _db, value: value)
    contract = SimpleNamespace(
        id=71, status="ready", analysis_generation_id="generation-current"
    )
    user = SimpleNamespace(id=7)

    events = list(engine.iter_sse_events(contract, user, "same question"))

    assert events[-1] == "data: [DONE]\n\n"
    assert generation_ids == ["generation-current"]


def test_saved_analysis_report_hash_includes_generation(monkeypatch):
    from app.services.rag.cache import save_cached_answer
    from app.services.rag.types import ChatResult

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    monkeypatch.setattr("app.services.rag.cache._redis_set", lambda *_args: None)

    save_cached_answer(
        db,
        contract_id=71,
        user_id=7,
        question="same question",
        result=ChatResult(answer="answer", citations=[], cached=False),
        generation_id="generation-current",
    )

    report = db.add.call_args.args[0]
    assert report.question_hash == question_hash("same question", "generation-current")


def test_chunk_from_payload_and_estimate_cost():
    chunk = chunk_from_payload(
        {
            "file_path": "Token.sol",
            "function_name": "Token.transfer",
            "start_line": 40,
            "end_line": 55,
            "content": "function transfer(address to, uint256 amount) public {}",
        },
        score=0.8,
    )
    assert chunk is not None
    assert chunk.function_name == "Token.transfer"
    assert chunk.identity()[2] == 40
    assert estimate_cost(1_000_000, 0) == Decimal("0.150000")
    assert estimate_cost(0, 1_000_000) == Decimal("0.600000")


def test_build_messages_includes_chunks_and_question():
    chunk = RetrievedChunk(
        file_path="Demo.sol",
        function_name="Demo.ping",
        start_line=3,
        end_line=5,
        content="function ping() external {}",
    )
    messages = build_messages("What does ping do?", [chunk])
    assert messages[0]["role"] == "system"
    assert "Demo.sol:3-5" in messages[1]["content"]
    assert "What does ping do?" in messages[1]["content"]
    assert "function ping" in messages[1]["content"]


class _FakePoint:
    def __init__(self, payload, score=0.9):
        self.payload = payload
        self.score = score


class _FakeQdrant:
    def __init__(self, payloads: list[dict]):
        self.payloads = payloads
        self.collections = {COLLECTION_NAME}

    def collection_exists(self, name: str) -> bool:
        return name in self.collections

    def scroll(self, **_kwargs):
        return [_FakePoint(payload) for payload in self.payloads], None

    def search(self, **_kwargs):
        return [_FakePoint(self.payloads[0], 0.99)]

    def query_points(self, **_kwargs):
        class _Result:
            points = [_FakePoint(self.payloads[0], 0.99)]

        return _Result()


class _FakeEmbedder:
    def encode(self, texts, **_kwargs):
        return [[0.1] * VECTOR_SIZE for _ in texts]


def test_hybrid_search_returns_top_chunks():
    payloads = [
        {
            "contract_id": 1,
            "file_path": "Demo.sol",
            "function_name": "Demo.mint",
            "start_line": 10,
            "end_line": 20,
            "content": "function mint(address to) external onlyOwner {}",
        },
        {
            "contract_id": 1,
            "file_path": "Demo.sol",
            "function_name": "Demo.ping",
            "start_line": 3,
            "end_line": 5,
            "content": "function ping() external {}",
        },
    ]
    results = hybrid_search(
        1,
        "who can mint",
        top_k=1,
        client=_FakeQdrant(payloads),
        embedder=_FakeEmbedder(),
    )
    assert len(results) == 1
    assert results[0].function_name == "Demo.mint"


def test_retrieval_reads_only_the_ready_analysis_generation(monkeypatch):
    from app.services.rag.retrieval import retrieve_for_chat

    payload = {
        "contract_id": 71,
        "indexing_attempt_id": "generation-current",
        "file_path": "Demo.sol",
        "function_name": "Demo.ping",
        "start_line": 3,
        "end_line": 5,
        "content": "function ping() external { return; }",
        "kind": "function",
    }
    seen_generations = []

    def scroll(_contract_id, *, generation, **_kwargs):
        seen_generations.append(generation)
        return [payload]

    def search(_contract_id, _question, *, generation, **_kwargs):
        seen_generations.append(generation)
        return [(payload, 0.9)]

    monkeypatch.setattr("app.services.rag.retrieval.scroll_contract_chunks", scroll)
    monkeypatch.setattr("app.services.rag.retrieval.search_similar_chunks", search)
    contract = SimpleNamespace(
        id=71,
        status="ready",
        analysis_generation_id="generation-current",
        name="Demo",
        source_code="x" * 25_000,
        ast={
            "nodeType": "SourceUnit",
            "nodes": [],
        },
        slither_report=None,
        risk_score=None,
    )

    chunks = retrieve_for_chat(contract, "how does ping work?")

    assert [chunk.function_name for chunk in chunks] == ["Demo.ping"]
    assert seen_generations == ["generation-current", "generation-current"]


def test_tokenize_keeps_cyrillic_words():
    tokens = tokenize("контракт безопасен?")
    assert "контракт" in tokens
    assert "безопасен" in tokens


def test_hybrid_search_skips_interface_stubs_for_safety_question():
    payloads = [
        {
            "contract_id": 1,
            "file_path": "contract_safe.sol",
            "function_name": "IERC20.approve",
            "start_line": 52,
            "end_line": 66,
            "content": (
                "/**\n"
                "     * IMPORTANT: Beware that changing an allowance brings the risk\n"
                "     * of a race condition.\n"
                "     */\n"
                "    function approve(address spender, uint256 amount) external returns (bool);"
            ),
        },
        {
            "contract_id": 1,
            "file_path": "contract_safe.sol",
            "function_name": "SafeToken.transfer",
            "start_line": 80,
            "end_line": 90,
            "content": (
                "function transfer(address to, uint256 amount) public returns (bool) {\n"
                "        _balances[msg.sender] -= amount;\n"
                "        _balances[to] += amount;\n"
                "        return true;\n"
                "    }"
            ),
        },
    ]
    results = hybrid_search(
        1,
        "контракт безопасен?",
        top_k=8,
        client=_FakeQdrant(payloads),
        embedder=_FakeEmbedder(),
    )
    names = [chunk.function_name for chunk in results]
    assert names == ["SafeToken.transfer"]


def test_hybrid_search_prefers_implementation_over_stub():
    payloads = [
        {
            "contract_id": 1,
            "file_path": "Token.sol",
            "function_name": "IERC20.transfer",
            "start_line": 34,
            "end_line": 41,
            "content": "function transfer(address to, uint256 amount) external returns (bool);",
        },
        {
            "contract_id": 1,
            "file_path": "Token.sol",
            "function_name": "Token.transfer",
            "start_line": 90,
            "end_line": 99,
            "content": (
                "function transfer(address to, uint256 amount) public returns (bool) {\n"
                "        return true;\n"
                "    }"
            ),
        },
    ]
    results = hybrid_search(
        1,
        "how does transfer work",
        top_k=1,
        client=_FakeQdrant(payloads),
        embedder=_FakeEmbedder(),
    )
    assert results[0].function_name == "Token.transfer"


def test_citations_are_interface_stubs():
    stubs = [
        Citation(
            file_path="contract_safe.sol",
            function_name="IERC20.approve",
            start_line=52,
            end_line=66,
            content="function approve(address spender, uint256 amount) external returns (bool);",
        )
    ]
    assert citations_are_interface_stubs(stubs) is True
    impl = [
        Citation(
            file_path="Token.sol",
            function_name="Token.transfer",
            start_line=1,
            end_line=5,
            content="function transfer(address to, uint256 amount) public returns (bool) { return true; }",
        )
    ]
    assert citations_are_interface_stubs(impl) is False
    assert citations_are_interface_stubs([]) is False
    full_interface = [
        Citation(
            file_path="contract_safe.sol",
            function_name="contract_safe",
            start_line=1,
            end_line=79,
            content=(
                "interface IERC20 {\n"
                "    function transfer(address to, uint256 amount) external returns (bool);\n"
                "}"
            ),
        )
    ]
    assert citations_are_interface_stubs(full_interface) is False


def test_retrieve_for_chat_uses_full_source_on_overview():
    from types import SimpleNamespace

    from app.services.rag_engine import apply_contract_context, retrieve_for_chat

    source = (
        "// OpenZeppelin IERC20.sol\n"
        "interface IERC20 {\n"
        "    function transfer(address to, uint256 amount) external returns (bool);\n"
        "}\n"
    )
    contract = SimpleNamespace(
        id=60,
        name="contract_safe",
        source_code=source,
        ast={
            "sources": {
                "contract_safe.sol": {
                    "ast": {
                        "nodeType": "SourceUnit",
                        "nodes": [
                            {
                                "nodeType": "ContractDefinition",
                                "name": "IERC20",
                                "contractKind": "interface",
                                "baseContracts": [],
                            }
                        ],
                    }
                }
            }
        },
        risk_score="A",
        slither_report={
            "results": {
                "detectors": [
                    {
                        "check": "solc-version",
                        "impact": "Informational",
                        "description": "Version constraint ^0.8.0 contains known issues",
                    }
                ]
            }
        },
    )
    chunks = retrieve_for_chat(
        contract,
        "контрат безопасен?",
        retriever=lambda *_args, **_kwargs: [
            RetrievedChunk(
                file_path="contract_safe.sol",
                function_name="IERC20.transfer",
                start_line=34,
                end_line=41,
                content="function transfer(address to, uint256 amount) external returns (bool);",
            )
        ],
    )
    assert chunks[0].function_name == "IERC20.transfer"

    chunks = apply_contract_context(contract, "контрат безопасен?", [])
    names = [chunk.function_name for chunk in chunks]
    assert names[0] == "contract_safe"
    assert "interface IERC20" in chunks[0].content
    assert chunks[-1].function_name == "Slither.report"
    assert "Risk score: A" in chunks[-1].content


def test_primary_contract_names_are_leaf_contracts():
    from app.services.rag_engine import primary_contract_names

    ast = {
        "sources": {
            "contract.sol": {
                "ast": {
                    "nodeType": "SourceUnit",
                    "nodes": [
                        {
                            "nodeType": "ContractDefinition",
                            "name": "IERC721",
                            "contractKind": "interface",
                            "baseContracts": [],
                        },
                        {
                            "nodeType": "ContractDefinition",
                            "name": "Address",
                            "contractKind": "library",
                            "baseContracts": [],
                        },
                        {
                            "nodeType": "ContractDefinition",
                            "name": "ERC721",
                            "contractKind": "contract",
                            "baseContracts": [{"baseName": {"name": "IERC721"}}],
                        },
                        {
                            "nodeType": "ContractDefinition",
                            "name": "Ownable",
                            "contractKind": "contract",
                            "baseContracts": [],
                        },
                        {
                            "nodeType": "ContractDefinition",
                            "name": "LostPuffins",
                            "contractKind": "contract",
                            "baseContracts": [
                                {"baseName": {"name": "ERC721"}},
                                {"baseName": {"name": "Ownable"}},
                            ],
                        },
                    ],
                }
            }
        }
    }
    assert primary_contract_names(ast) == {"LostPuffins"}


def test_hybrid_search_stays_on_primary_contract():
    payloads = [
        {
            "contract_id": 59,
            "file_path": "contract.sol",
            "function_name": "Address.sendValue",
            "start_line": 240,
            "end_line": 261,
            "content": (
                "function sendValue(address payable recipient, uint256 amount) internal {\n"
                "        recipient.call{value: amount}(\"\");\n"
                "    }"
            ),
        },
        {
            "contract_id": 59,
            "file_path": "contract.sol",
            "function_name": "LostPuffins.withdrawAll",
            "start_line": 1600,
            "end_line": 1608,
            "content": (
                "function withdrawAll() public onlyOwner {\n"
                "        _widthdraw(owner(), address(this).balance);\n"
                "    }"
            ),
        },
        {
            "contract_id": 59,
            "file_path": "contract.sol",
            "function_name": "LostPuffins.mint",
            "start_line": 1553,
            "end_line": 1573,
            "content": (
                "function mint(uint256 amount) external payable {\n"
                "        require(msg.value >= PRICE * amount);\n"
                "    }"
            ),
        },
    ]
    results = hybrid_search(
        59,
        "если я его использую, есть шанс, что мои деньги украдут?",
        top_k=8,
        client=_FakeQdrant(payloads),
        embedder=_FakeEmbedder(),
        primary_names={"LostPuffins"},
    )
    names = [chunk.function_name for chunk in results]
    assert "Address.sendValue" not in names
    assert set(names) == {"LostPuffins.withdrawAll", "LostPuffins.mint"}


def test_load_cached_answer_skips_interface_stub_payload(monkeypatch):
    stub_payload = {
        "answer": "недостаточно",
        "citations": [
            {
                "file_path": "contract_safe.sol",
                "function_name": "IERC20.allowance",
                "start_line": 43,
                "end_line": 50,
                "content": (
                    "function allowance(address owner, address spender) "
                    "external view returns (uint256);"
                ),
            }
        ],
        "tokens_used": 10,
        "cost_usd": None,
    }
    deleted: list[str] = []
    monkeypatch.setattr("app.services.rag.cache._redis_get", lambda _key: stub_payload)
    monkeypatch.setattr("app.services.rag.cache._redis_delete", deleted.append)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    assert load_cached_answer(db, 1, "контракт безопасен?") is None
    assert deleted


def test_save_cached_answer_skips_interface_stubs(monkeypatch):
    from app.services.rag_engine import ChatResult

    redis_ops: list[str] = []
    monkeypatch.setattr(
        "app.services.rag.cache._redis_set",
        lambda *_args, **_kwargs: redis_ops.append("set"),
    )
    monkeypatch.setattr(
        "app.services.rag.cache._redis_delete",
        lambda *_args, **_kwargs: redis_ops.append("delete"),
    )
    db = MagicMock()
    save_cached_answer(
        db,
        contract_id=1,
        user_id=1,
        question="контракт безопасен?",
        result=ChatResult(
            answer="нет данных",
            citations=[
                Citation(
                    file_path="contract_safe.sol",
                    function_name="IERC20.transfer",
                    start_line=34,
                    end_line=41,
                    content="function transfer(address to, uint256 amount) external returns (bool);",
                )
            ],
        ),
    )
    assert redis_ops == ["delete"]
    db.add.assert_not_called()


def test_call_openai_parses_completion(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    monkeypatch.setenv("OPENAI_MODEL", "")
    get_settings.cache_clear()

    def handler(request):
        import httpx

        assert request.headers["authorization"] == "Bearer sk-test"
        assert str(request.url) == "https://api.openai.com/v1/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == "gpt-4o-mini"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Safe: ping is a no-op."}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )

    import httpx

    client = httpx.Client(transport=httpx.MockTransport(handler))
    answer, usage = call_openai(
        [{"role": "user", "content": "ping?"}],
        client=client,
    )
    assert answer == "Safe: ping is a no-op."
    assert usage.tokens_used == 15
    assert usage.cost_usd == LLMUsage(
        tokens_used=15,
        cost_usd=estimate_cost(10, 5),
    ).cost_usd

    get_settings.cache_clear()


def test_kodik_key_uses_kodikrouter_url(monkeypatch):
    from app.core.config import get_settings
    from app.services.rag_engine import openai_base_url, openai_completions_url, openai_model

    monkeypatch.setenv("OPENAI_API_KEY", "sk-kr_test_dummy")
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    monkeypatch.setenv("OPENAI_MODEL", "")
    get_settings.cache_clear()

    assert openai_base_url() == "https://api.kodikrouter.ru/v1"
    assert openai_completions_url() == "https://api.kodikrouter.ru/v1/chat/completions"
    assert openai_model() == "openai/gpt-4o-mini"
    get_settings.cache_clear()


def test_citations_from_chunks_ignore_invented_file_path():
    from app.services.rag_engine import citations_from_chunks

    chunks = [
        RetrievedChunk(
            file_path="Token.sol",
            function_name="Token.transfer",
            start_line=10,
            end_line=20,
            content="function transfer(address to, uint256 amount) public {}",
        ),
        RetrievedChunk(
            file_path="analysis.slither",
            function_name="Slither.report",
            start_line=1,
            end_line=2,
            content="Risk score: B",
            kind="analysis",
        ),
    ]
    citations = citations_from_chunks(
        chunks,
        "Backdoor lives in Evil.sol:1-40 and Token.sol:10-20.",
    )
    assert [item.file_path for item in citations] == ["Token.sol"]


def test_overview_prompt_uses_top_findings_not_all_chunks():
    from types import SimpleNamespace

    from app.services.rag_engine import OVERVIEW_CODE_CHUNKS, retrieve_for_chat

    filler = [
        RetrievedChunk(
            file_path="Vault.sol",
            function_name=f"Vault.fn{index}",
            start_line=index * 10,
            end_line=index * 10 + 4,
            content=f"function fn{index}() public {{}}",
        )
        for index in range(20)
    ]
    withdraw = RetrievedChunk(
        file_path="Vault.sol",
        function_name="Vault.withdraw",
        start_line=80,
        end_line=90,
        content="function withdraw() public { msg.sender.call{value: 1}(\"\"); }",
    )
    contract = SimpleNamespace(
        id=91,
        name="Vault",
        source_code="pragma solidity ^0.8.24; contract Vault {}",
        ast={
            "sources": {
                "Vault.sol": {
                    "ast": {
                        "nodeType": "SourceUnit",
                        "nodes": [
                            {
                                "nodeType": "ContractDefinition",
                                "name": "Vault",
                                "contractKind": "contract",
                                "baseContracts": [],
                            }
                        ],
                    }
                }
            }
        },
        risk_score="D",
        slither_report={
            "results": {
                "detectors": [
                    {
                        "check": "reentrancy-eth",
                        "impact": "High",
                        "confidence": "High",
                        "description": "Reentrancy in withdraw()",
                        "elements": [
                            {
                                "type": "function",
                                "name": "withdraw",
                                "source_mapping": {
                                    "filename_short": "Vault.sol",
                                    "lines": [80, 81, 82],
                                },
                            }
                        ],
                    }
                ]
            }
        },
    )
    chunks = retrieve_for_chat(
        contract,
        "контракт безопасен?",
        retriever=lambda *_args, **_kwargs: filler + [withdraw],
    )
    code = [chunk for chunk in chunks if chunk.kind != "analysis"]
    assert len(code) <= OVERVIEW_CODE_CHUNKS
    assert [chunk.function_name for chunk in code] == ["Vault.withdraw"]
    assert "Top findings and affected functions" in chunks[-1].content
    assert "withdraw" in chunks[-1].content
    assert "fn19" not in "".join(chunk.function_name for chunk in code)


def test_openzeppelin_zip_not_in_overview_context():
    from types import SimpleNamespace
    import io
    import zipfile

    vault_src = (
        "pragma solidity ^0.8.24;\n"
        'import "@openzeppelin/contracts/utils/Address.sol";\n'
        "contract Vault {\n"
        "    function withdraw() external {\n"
        "        Address.sendValue(payable(msg.sender), address(this).balance);\n"
        "    }\n"
        "}\n"
    )
    oz_src = (
        "pragma solidity ^0.8.24;\n"
        "library Address {\n"
        "    function sendValue(address payable recipient, uint256 amount) internal {\n"
        "        recipient.call{value: amount}(\"\");\n"
        "    }\n"
        "}\n"
    )
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("contracts/Vault.sol", vault_src)
        zipped.writestr("@openzeppelin/contracts/utils/Address.sol", oz_src)
    names = zipfile.ZipFile(archive).namelist()
    assert "@openzeppelin/contracts/utils/Address.sol" in names

    payloads = [
        {
            "contract_id": 77,
            "file_path": "@openzeppelin/contracts/utils/Address.sol",
            "function_name": "Address.sendValue",
            "start_line": 3,
            "end_line": 8,
            "content": oz_src,
            "kind": "function",
        },
        {
            "contract_id": 77,
            "file_path": "contracts/Vault.sol",
            "function_name": "Vault.withdraw",
            "start_line": 4,
            "end_line": 7,
            "content": (
                "function withdraw() external {\n"
                "        Address.sendValue(payable(msg.sender), address(this).balance);\n"
                "    }"
            ),
            "kind": "function",
        },
    ]
    contract = SimpleNamespace(
        id=77,
        name="Vault",
        source_code=vault_src,
        ast={
            "sources": {
                "contracts/Vault.sol": {
                    "ast": {
                        "nodeType": "SourceUnit",
                        "nodes": [
                            {
                                "nodeType": "ContractDefinition",
                                "name": "Vault",
                                "contractKind": "contract",
                                "baseContracts": [],
                            }
                        ],
                    }
                },
                "@openzeppelin/contracts/utils/Address.sol": {
                    "ast": {
                        "nodeType": "SourceUnit",
                        "nodes": [
                            {
                                "nodeType": "ContractDefinition",
                                "name": "Address",
                                "contractKind": "library",
                                "baseContracts": [],
                            }
                        ],
                    }
                },
            }
        },
        risk_score="C",
        slither_report={"results": {"detectors": []}},
    )
    from app.services.rag_engine import retrieve_for_chat

    chunks = retrieve_for_chat(
        contract,
        "есть ли уязвимости?",
        client=_FakeQdrant(payloads),
        embedder=_FakeEmbedder(),
    )
    fnames = [chunk.function_name for chunk in chunks]
    joined = "\n".join(f"{chunk.file_path}\n{chunk.content}" for chunk in chunks)
    assert "Address.sendValue" not in fnames
    assert "@openzeppelin" not in joined
    assert any(chunk.function_name == "Vault.withdraw" for chunk in chunks)
