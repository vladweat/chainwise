"""Download solc 0.8.24 into backend/.solc for offline Docker builds."""

from __future__ import annotations

import os
import sys
from pathlib import Path

DEFAULT_SOLC_VERSION = "0.8.24"
SOLC_DIR = Path(__file__).resolve().parent.parent / ".solc"


def main() -> int:
    SOLC_DIR.mkdir(parents=True, exist_ok=True)
    os.environ["SOLCX_BINARY_PATH"] = str(SOLC_DIR)

    try:
        from solcx import get_installed_solc_versions, install_solc
        from solcx.exceptions import SolcNotInstalled
    except ImportError:
        print("ERROR: py-solc-x is not installed. Run make venv first.", file=sys.stderr)
        return 1

    binary_path = str(SOLC_DIR)
    expected = SOLC_DIR / f"solc-v{DEFAULT_SOLC_VERSION}"

    installed = {str(item) for item in get_installed_solc_versions(solcx_binary_path=binary_path)}
    if DEFAULT_SOLC_VERSION in installed and expected.exists():
        print(f"OK: solc {DEFAULT_SOLC_VERSION} already in {SOLC_DIR}")
        return 0

    print(f"Installing solc {DEFAULT_SOLC_VERSION} into {SOLC_DIR} ...")
    try:
        install_solc(DEFAULT_SOLC_VERSION, solcx_binary_path=binary_path)
    except SolcNotInstalled:
        # py-solc-x validates via set_solc_version() without solcx_binary_path.
        # SOLCX_BINARY_PATH in the environment makes the lookup succeed; if a
        # previous run already dropped the binary, treat it as installed.
        if not expected.exists():
            raise

    versions = get_installed_solc_versions(solcx_binary_path=binary_path)
    if DEFAULT_SOLC_VERSION not in {str(item) for item in versions} or not expected.exists():
        print(f"ERROR: solc {DEFAULT_SOLC_VERSION} was not installed into {SOLC_DIR}", file=sys.stderr)
        return 1

    print(f"OK: installed {', '.join(str(item) for item in versions)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
