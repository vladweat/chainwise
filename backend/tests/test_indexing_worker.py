from app.services.indexing_worker import run_indexing
from app.services.solc_compiler import CompileResult, SolcCompileError
from app.services.slither_runner import SlitherError
from app.services.qdrant_indexer import QdrantIndexError
from app.models.contract import Contract


def _stub_pipeline(monkeypatch, compile_fn=None, slither_report=None, upsert_fn=None):
    if compile_fn is not None:
        monkeypatch.setattr("app.services.indexing_worker.compile_to_ast", compile_fn)
    monkeypatch.setattr(
        "app.services.indexing_worker.run_slither",
        lambda *_args, **_kwargs: slither_report
        or {"success": True, "error": None, "results": {"detectors": []}},
    )
    monkeypatch.setattr(
        "app.services.indexing_worker.upsert_code_chunks",
        upsert_fn or (lambda _contract_id, chunks, **_kwargs: len(chunks)),
    )


def test_run_indexing_saves_ast(monkeypatch):
    saved = {}

    class FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

    class FakeSession:
        def commit(self):
            saved["committed"] = True

        def query(self, *_args, **_kwargs):
            return FakeQuery()

        def rollback(self):
            saved["rolled_back"] = True

        def close(self):
            pass

    def fake_compile(source_code, compiler_version=None, filename=None):
        saved["filename"] = filename
        saved["version"] = compiler_version
        return CompileResult(
            solc_version="0.8.24",
            sources={
                filename or "Contract.sol": {
                    "id": 0,
                    "ast": {"nodeType": "SourceUnit", "nodes": []},
                }
            },
        )

    captured = {}

    def fake_upsert(contract_id, chunks, **_kwargs):
        captured["contract_id"] = contract_id
        captured["chunk_count"] = len(chunks)
        return len(chunks)

    monkeypatch.setattr("app.services.indexing_worker.compile_to_ast", fake_compile)
    _stub_pipeline(monkeypatch, upsert_fn=fake_upsert)

    contract = Contract(
        id=1,
        address="0x" + "a" * 40,
        chain_id=1,
        name="Demo",
        source_code="pragma solidity ^0.8.24;\ncontract Demo {}",
        compiler_version=None,
        status="indexing",
    )
    result = run_indexing(FakeSession(), contract, celery_task_id="task-1")

    assert result["status"] == "ready"
    assert contract.status == "ready"
    assert contract.compiler_version == "0.8.24"
    assert contract.celery_task_id == "task-1"
    assert contract.ast["solc_version"] == "0.8.24"
    assert contract.slither_report["success"] is True
    assert contract.chunk_count == 1
    assert result["chunk_count"] == 1
    assert contract.risk_score == "A"
    assert contract.risk_model in {"xgboost", "heuristic"}
    assert contract.features_version == "v1"
    assert result["risk_score"] == "A"
    assert captured == {"contract_id": 1, "chunk_count": 1}
    assert saved["filename"] == "Demo.sol"
    assert saved["committed"] is True


def test_run_indexing_reports_function_chunks(monkeypatch):
    source = (
        "pragma solidity ^0.8.24;\n"
        "contract Demo {\n"
        "    function ping() external {}\n"
        "    modifier gated() { _; }\n"
        "}\n"
    )
    ping = "function ping() external {}"
    gated = "modifier gated() { _; }"

    def _src(snippet: str) -> str:
        encoded = source.encode("utf-8")
        needle = snippet.encode("utf-8")
        start = encoded.find(needle)
        return f"{start}:{len(needle)}:0"

    monkeypatch.setattr(
        "app.services.indexing_worker.compile_to_ast",
        lambda *_args, **_kwargs: CompileResult(
            solc_version="0.8.24",
            sources={
                "Demo.sol": {
                    "id": 0,
                    "ast": {
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
                                        "src": _src(ping),
                                    },
                                    {
                                        "nodeType": "ModifierDefinition",
                                        "name": "gated",
                                        "src": _src(gated),
                                    },
                                ],
                            }
                        ],
                    },
                }
            },
        ),
    )
    _stub_pipeline(monkeypatch)

    contract = Contract(
        id=4,
        address="0x" + "d" * 40,
        chain_id=1,
        name="Demo",
        source_code=source,
        compiler_version="0.8.24",
        status="indexing",
    )

    class FakeSession:
        def commit(self):
            pass

        def query(self, *_args, **_kwargs):
            return None

    result = run_indexing(FakeSession(), contract)
    assert result["chunk_count"] == 2
    assert result["status"] == "ready"
    assert contract.status == "ready"
    assert contract.chunk_count == 2
    assert contract.risk_score == "A"


def test_run_indexing_compile_error(monkeypatch):
    def fail_compile(*_args, **_kwargs):
        raise SolcCompileError("parse error")

    monkeypatch.setattr("app.services.indexing_worker.compile_to_ast", fail_compile)

    contract = Contract(
        id=2,
        address="0x" + "b" * 40,
        chain_id=1,
        name="Broken",
        source_code="contract Broken {",
        status="indexing",
    )

    class FakeSession:
        def commit(self):
            return None

    try:
        run_indexing(FakeSession(), contract)
        assert False, "expected SolcCompileError"
    except SolcCompileError:
        assert contract.status == "indexing"
        assert contract.ast is None
        assert contract.indexing_step == "compile"


def test_run_indexing_slither_error(monkeypatch):
    monkeypatch.setattr(
        "app.services.indexing_worker.compile_to_ast",
        lambda *_args, **_kwargs: CompileResult(
            solc_version="0.8.24",
            sources={"Broken.sol": {"id": 0, "ast": {"nodeType": "SourceUnit"}}},
        ),
    )

    def fail_slither(*_args, **_kwargs):
        raise SlitherError("Cannot reach Slither service")

    monkeypatch.setattr("app.services.indexing_worker.run_slither", fail_slither)

    contract = Contract(
        id=3,
        address="0x" + "c" * 40,
        chain_id=1,
        name="Demo",
        source_code="pragma solidity ^0.8.24;\ncontract Demo {}",
        status="indexing",
    )

    class FakeSession:
        def commit(self):
            return None

    try:
        run_indexing(FakeSession(), contract)
        assert False, "expected SlitherError"
    except SlitherError:
        assert contract.status == "indexing"
        assert contract.slither_report is None
        assert contract.indexing_step == "slither"


def test_run_indexing_qdrant_error(monkeypatch):
    monkeypatch.setattr(
        "app.services.indexing_worker.compile_to_ast",
        lambda *_args, **_kwargs: CompileResult(
            solc_version="0.8.24",
            sources={"Demo.sol": {"id": 0, "ast": {"nodeType": "SourceUnit", "nodes": []}}},
        ),
    )

    def fail_upsert(*_args, **_kwargs):
        raise QdrantIndexError("Qdrant upsert failed: connection refused")

    _stub_pipeline(monkeypatch, upsert_fn=fail_upsert)

    contract = Contract(
        id=5,
        address="0x" + "e" * 40,
        chain_id=1,
        name="Demo",
        source_code="pragma solidity ^0.8.24;\ncontract Demo {}",
        status="indexing",
    )

    class FakeSession:
        def commit(self):
            return None

    try:
        run_indexing(FakeSession(), contract)
        assert False, "expected QdrantIndexError"
    except QdrantIndexError:
        assert contract.status == "indexing"
        assert contract.chunk_count is None
        assert contract.indexing_step == "embed"


def test_run_indexing_sets_high_risk_score(monkeypatch):
    dangerous = {
        "success": True,
        "results": {
            "detectors": [
                {"check": "reentrancy-eth", "impact": "High", "confidence": "High"},
                {"check": "suicidal", "impact": "High", "confidence": "High"},
                {"check": "controlled-delegatecall", "impact": "High", "confidence": "Medium"},
            ]
        },
    }
    monkeypatch.setattr(
        "app.services.indexing_worker.compile_to_ast",
        lambda *_args, **_kwargs: CompileResult(
            solc_version="0.8.24",
            sources={"Demo.sol": {"id": 0, "ast": {"nodeType": "SourceUnit", "nodes": []}}},
        ),
    )
    _stub_pipeline(monkeypatch, slither_report=dangerous)

    contract = Contract(
        id=6,
        address="0x" + "f" * 40,
        chain_id=1,
        name="Risky",
        source_code="pragma solidity ^0.8.24;\ncontract Risky {}",
        compiler_version="0.8.24",
        status="indexing",
    )

    class FakeSession:
        def commit(self):
            pass

    result = run_indexing(FakeSession(), contract)
    assert result["status"] == "ready"
    assert contract.risk_score in {"C", "D"}
    assert result["risk_score"] == contract.risk_score
