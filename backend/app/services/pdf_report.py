"""Compact PDF summary of a contract analysis. Does not embed full source."""

from __future__ import annotations

from datetime import datetime, timezone

from fpdf import FPDF

from app.models.contract import Contract
from app.services.risk_scorer import risk_model_attribution, risk_reasons

CHAIN_LABELS = {1: "Ethereum", 56: "BSC", 11124: "Abstract", 0: "Upload"}
MAX_FINDINGS = 40
MAX_DESCRIPTION = 220
RISK_SCORE_ADVISORY_NOTE = (
    "Experimental A-D score: a heuristic-derived advisory pre-screening indicator. "
    "It is not a likelihood of exploitation, not a calibrated risk probability, "
    "and not a security audit."
)


def extract_slither_findings(report: dict | None) -> list[dict]:
    if not isinstance(report, dict):
        return []
    results = report.get("results")
    if not isinstance(results, dict):
        return []
    detectors = results.get("detectors") or []
    findings: list[dict] = []
    for item in detectors:
        if not isinstance(item, dict):
            continue
        description = (item.get("description") or "").strip().split("\n")[0]
        span = _finding_span(item)
        findings.append(
            {
                "check": str(item.get("check") or "unknown"),
                "impact": str(item.get("impact") or ""),
                "confidence": str(item.get("confidence") or ""),
                "location": span["location"],
                "file_path": span["file_path"],
                "start_line": span["start_line"],
                "end_line": span["end_line"],
                "function_name": span["function_name"],
                "description": _latin1(description[:MAX_DESCRIPTION]),
            }
        )
        if len(findings) >= MAX_FINDINGS:
            break
    return findings


def build_contract_report_pdf(contract: Contract) -> bytes:
    findings = extract_slither_findings(contract.slither_report)
    pdf = _ReportPDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "ChainWise analysis report", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(
        0,
        6,
        "Pre-audit helper. Not a substitute for a professional security audit.",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.set_text_color(0, 0, 0)
    pdf.ln(4)
    _write_line(pdf, RISK_SCORE_ADVISORY_NOTE, size=9)
    pdf.ln(3)

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    created = _format_dt(contract.created_at)
    chain = CHAIN_LABELS.get(contract.chain_id, f"chain {contract.chain_id}")

    rows = [
        ("Name", contract.name or f"Contract #{contract.id}"),
        ("Address", contract.address),
        ("Chain", f"{chain} ({contract.chain_id})"),
        ("Compiler", f"solc {contract.compiler_version}" if contract.compiler_version else "-"),
        ("Status", contract.status),
        ("Risk score", contract.risk_score or "-"),
        ("Indexed chunks", str(contract.chunk_count if contract.chunk_count is not None else "-")),
        ("Analyzed at", created),
        ("Report generated", generated),
    ]
    if contract.risk_model:
        rows.insert(
            -2,
            (
                "Scoring source",
                f"{risk_model_attribution(contract.risk_model)} / "
                f"{contract.features_version or '-'}",
            ),
        )
    label_width = 42
    for label, value in rows:
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(label_width, 6, _latin1(label))
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(pdf.epw - label_width, 6, _latin1(str(value)))

    reasons = risk_reasons(contract.slither_report)
    pdf.ln(3)
    _write_line(pdf, "Slither feature summary", bold=True, size=12, height=8)
    for reason in reasons:
        _write_line(pdf, f"- {reason}", size=9)

    pdf.ln(3)
    _write_line(pdf, "Slither findings", bold=True, size=12, height=8)

    if not findings:
        _write_line(pdf, "No detectors reported.", size=9)
    else:
        pdf.set_text_color(90, 90, 90)
        _write_line(
            pdf,
            "Detector / impact / confidence. Source code is not included.",
            size=9,
        )
        pdf.set_text_color(0, 0, 0)
        pdf.ln(2)
        for index, finding in enumerate(findings, start=1):
            title = f"{index}. {finding['check']}"
            meta = " / ".join(part for part in (finding["impact"], finding["confidence"]) if part)
            _write_line(pdf, title + (f" ({meta})" if meta else ""), bold=True, size=10)
            if finding["location"]:
                _write_line(pdf, f"Location: {finding['location']}", size=9)
            if finding["description"]:
                _write_line(pdf, finding["description"], size=9)
            pdf.ln(1)

    return bytes(pdf.output())


class _ReportPDF(FPDF):
    def __init__(self) -> None:
        super().__init__()
        self.compress = False

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 8, f"ChainWise  |  page {self.page_no()}  |  not an audit", align="C")
        self.set_text_color(0, 0, 0)


def _display_source_path(filename: str) -> str:
    normalized = filename.replace("\\", "/")
    if "/tmp/" in normalized or normalized.startswith("../"):
        return normalized.rsplit("/", 1)[-1] or normalized
    return normalized.lstrip("./")


def _finding_span(item: dict) -> dict:
    empty = {
        "location": "",
        "file_path": None,
        "start_line": None,
        "end_line": None,
        "function_name": None,
    }
    elements = item.get("elements") or []
    for element in elements:
        if not isinstance(element, dict):
            continue
        mapping = element.get("source_mapping") or {}
        filename = (
            mapping.get("filename_short")
            or mapping.get("filename")
            or element.get("type")
            or ""
        )
        filename = _display_source_path(str(filename)) if filename else ""
        lines = mapping.get("lines") or []
        name = element.get("name")
        if filename and lines:
            start, end = int(lines[0]), int(lines[-1])
            span = f"{start}-{end}" if start != end else str(start)
            return {
                "location": f"{filename}:{span}",
                "file_path": str(filename),
                "start_line": start,
                "end_line": end,
                "function_name": str(name) if name else None,
            }
        if name:
            return {**empty, "location": str(name), "function_name": str(name)}
    return empty


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return "-"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _write_line(
    pdf: FPDF, text: str, *, bold: bool = False, size: int = 10, height: float = 5
) -> None:
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "B" if bold else "", size)
    pdf.multi_cell(pdf.epw, height, _latin1(text))


def _latin1(value: str) -> str:
    return value.encode("latin-1", "replace").decode("latin-1")
