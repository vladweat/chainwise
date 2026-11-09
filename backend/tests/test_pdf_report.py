from pathlib import Path
import re

from app.models.contract import Contract
from app.schemas.chat import Citation
from app.services.pdf_report import build_contract_report_pdf, extract_slither_findings
from app.services.semantic_splitter import split_code_chunks
from app.services.solc_compiler import CompileResult

FIXTURE_SOL = Path(__file__).parent / "fixtures" / "Vault.sol"
CANARY = "UNIQUE_CANARY_xyz_not_in_pdf"
PDF_TEXT_RUN = re.compile(rb"\(((?:\\.|[^\\)])*)\)\s*Tj")


def _pdf_visible_text(pdf: bytes) -> str:
    runs = PDF_TEXT_RUN.findall(pdf)
    unescaped = [re.sub(rb"\\([()\\])", rb"\1", run) for run in runs]
    return " ".join(run.decode("latin-1") for run in unescaped)


def _src(content: str, snippet: str, file_id: int = 0) -> str:
    encoded = content.encode("utf-8")
    needle = snippet.encode("utf-8")
    start = encoded.find(needle)
    assert start >= 0, f"snippet not found: {snippet!r}"
    return f"{start}:{len(needle)}:{file_id}"


def test_extract_slither_findings_maps_location():
    report = {
        "success": True,
        "results": {
            "detectors": [
                {
                    "check": "reentrancy-eth",
                    "impact": "High",
                    "confidence": "Medium",
                    "description": "Reentrancy in withdraw()\nfunction withdraw() { ... }",
                    "elements": [
                        {
                            "type": "function",
                            "name": "withdraw",
                            "source_mapping": {
                                "filename_short": "Vault.sol",
                                "lines": [11, 12, 13, 14],
                            },
                        }
                    ],
                }
            ]
        },
    }
    findings = extract_slither_findings(report)
    assert findings[0]["check"] == "reentrancy-eth"
    assert findings[0]["location"] == "Vault.sol:11-14"
    assert findings[0]["file_path"] == "Vault.sol"
    assert findings[0]["start_line"] == 11
    assert findings[0]["end_line"] == 14
    assert findings[0]["function_name"] == "withdraw"


def test_extract_slither_findings_strips_sidecar_tmp_path():
    report = {
        "success": True,
        "results": {
            "detectors": [
                {
                    "check": "immutable-states",
                    "impact": "Optimization",
                    "confidence": "High",
                    "description": "Vault.owner should be immutable",
                    "elements": [
                        {
                            "source_mapping": {
                                "filename_short": "../tmp/slither-abc/src/Vault.sol",
                                "lines": [4],
                            }
                        }
                    ],
                }
            ]
        },
    }
    findings = extract_slither_findings(report)
    assert findings[0]["file_path"] == "Vault.sol"
    assert findings[0]["location"] == "Vault.sol:4"


def test_pdf_omits_full_source_and_includes_metadata():
    source = FIXTURE_SOL.read_text(encoding="utf-8") + f"\n// {CANARY}\n"
    contract = Contract(
        id=42,
        address="0x1234567890123456789012345678901234567890",
        chain_id=1,
        name="Vault",
        source_code=source,
        compiler_version="0.8.24",
        status="ready",
        risk_score="C",
        risk_model="xgboost",
        features_version="v1",
        chunk_count=3,
        slither_report={
            "success": True,
            "results": {
                "detectors": [
                    {
                        "check": "arbitrary-send-eth",
                        "impact": "High",
                        "confidence": "Medium",
                        "description": "transfer() to msg.sender",
                    }
                ]
            },
        },
    )
    pdf = build_contract_report_pdf(contract)
    assert pdf.startswith(b"%PDF")
    text = _pdf_visible_text(pdf)
    assert "Vault" in text
    assert "0x1234567890123456789012345678901234567890" in text
    assert "Ethereum" in text
    assert "Risk score" in text
    assert "C" in text
    assert (
        "heuristic-derived advisory pre-screening indicator" in text
        and "not a likelihood of exploitation" in text
        and "not a calibrated risk probability" in text
        and "not a security audit" in text
    )
    assert "XGBoost model trained on synthetic heuristic labels / v1" in text
    assert "arbitrary-send-eth" in text
    assert "Slither feature summary" in text
    assert "Slither detectors" in text
    assert CANARY not in text
    assert "pragma solidity" not in text
    assert "payable(msg.sender)" not in text


def test_pdf_reports_heuristic_fallback_attribution():
    contract = Contract(
        id=43,
        address="0x1234567890123456789012345678901234567890",
        chain_id=1,
        status="ready",
        risk_score="B",
        risk_model="heuristic",
        features_version="v1",
        slither_report={"success": True, "results": {"detectors": []}},
    )

    text = _pdf_visible_text(build_contract_report_pdf(contract))

    assert "Heuristic fallback / v1" in text
    assert "XGBoost model trained on synthetic heuristic labels" not in text


def test_fixture_sol_chunks_become_citations():
    source = FIXTURE_SOL.read_text(encoding="utf-8")
    withdraw = (
        "function withdraw() external {\n"
        "        require(msg.sender == owner, \"not owner\");\n"
        "        payable(msg.sender).transfer(address(this).balance);\n"
        "    }"
    )
    constructor = "constructor() {\n        owner = msg.sender;\n    }"
    ast = {
        "nodeType": "SourceUnit",
        "nodes": [
            {
                "nodeType": "ContractDefinition",
                "name": "Vault",
                "contractKind": "contract",
                "nodes": [
                    {
                        "nodeType": "FunctionDefinition",
                        "name": "constructor",
                        "kind": "constructor",
                        "src": _src(source, constructor),
                    },
                    {
                        "nodeType": "FunctionDefinition",
                        "name": "withdraw",
                        "kind": "function",
                        "src": _src(source, withdraw),
                    },
                ],
            }
        ],
    }
    compile_result = CompileResult(
        solc_version="0.8.24",
        sources={"Vault.sol": {"id": 0, "ast": ast}},
    )
    chunks = split_code_chunks(source, compile_result, filename="Vault.sol")
    citations = [
        Citation(
            file_path=chunk.file_path,
            function_name=chunk.function_name,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            content=chunk.content,
        )
        for chunk in chunks
    ]

    assert citations
    assert all(item.file_path == "Vault.sol" for item in citations)
    withdraw_cites = [item for item in citations if item.function_name.endswith("withdraw")]
    assert len(withdraw_cites) == 1
    cite = withdraw_cites[0]
    assert "function withdraw" in cite.content
    assert cite.start_line == source[: source.index("function withdraw")].count("\n") + 1
    assert cite.end_line >= cite.start_line
    assert source.splitlines()[cite.start_line - 1].strip().startswith("function withdraw")
