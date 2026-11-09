"""Report or fail stale contract indexing attempts."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta

from app.core.database import SessionLocal
from app.services.indexing_lifecycle import reconcile_stale_indexing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stale-after-seconds",
        type=int,
        default=3600,
        help="minimum age since last worker heartbeat (default: 3600)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="mark discovered stale rows FAILED; without this option only report them",
    )
    args = parser.parse_args()
    if args.stale_after_seconds <= 0:
        parser.error("--stale-after-seconds must be positive")

    stale_before = datetime.now(UTC) - timedelta(seconds=args.stale_after_seconds)
    db = SessionLocal()
    try:
        contract_ids = reconcile_stale_indexing(
            db,
            stale_before=stale_before,
            dry_run=not args.apply,
        )
    finally:
        db.close()

    print(
        json.dumps(
            {
                "mode": "apply" if args.apply else "dry-run",
                "stale_contract_ids": contract_ids,
                "count": len(contract_ids),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
