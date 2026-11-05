import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

SOLC_DIR = Path(os.environ.get("SOLC_DIR", "/opt/solc"))
SOLC_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
SLITHER_TIMEOUT_SECONDS = 90

app = FastAPI(title="ChainWise Slither", version="0.1.0")


class AnalyzeRequest(BaseModel):
    files: dict[str, str] = Field(..., min_length=1)
    solc_version: str = "0.8.24"


def _safe_path(root: Path, relative: str) -> Path:
    normalized = relative.replace("\\", "/").lstrip("/")
    dest = (root / normalized).resolve()
    if ".." in Path(normalized).parts or not str(dest).startswith(str(root.resolve())):
        raise HTTPException(status_code=400, detail=f"Unsafe source path: {relative}")
    return dest


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/analyze")
def analyze(body: AnalyzeRequest) -> dict:
    if not SOLC_VERSION_RE.match(body.solc_version):
        raise HTTPException(status_code=400, detail="Invalid solc version")

    solc_bin = SOLC_DIR / f"solc-v{body.solc_version}"
    if not solc_bin.is_file():
        raise HTTPException(
            status_code=503,
            detail=f"solc {body.solc_version} binary not found at {solc_bin}",
        )

    with tempfile.TemporaryDirectory(prefix="slither-") as tmp:
        root = Path(tmp)
        workdir = root / "src"
        workdir.mkdir()
        for relative, content in body.files.items():
            dest = _safe_path(workdir, relative)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")

        solc_dir = root / "solc-bin"
        solc_dir.mkdir()
        solc_link = solc_dir / "solc"
        solc_link.symlink_to(solc_bin)

        report_path = root / "slither-report.json"
        env = os.environ.copy()
        env["PATH"] = f"{solc_dir}{os.pathsep}{env.get('PATH', '')}"

        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "slither",
                    str(workdir),
                    "--solc",
                    str(solc_bin),
                    "--json",
                    str(report_path),
                    "--fail-none",
                    "--disable-color",
                ],
                capture_output=True,
                text=True,
                timeout=SLITHER_TIMEOUT_SECONDS,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(status_code=504, detail="slither timed out") from exc

        if not report_path.is_file():
            detail = (completed.stderr or completed.stdout or "slither produced no JSON").strip()
            raise HTTPException(status_code=500, detail=detail[:4000])

        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=500, detail="slither JSON is invalid") from exc

    if not isinstance(report, dict):
        raise HTTPException(status_code=500, detail="slither JSON must be an object")

    results = report.setdefault("results", {})
    if not isinstance(results, dict):
        results = {}
        report["results"] = results
    results.setdefault("detectors", [])

    return report
