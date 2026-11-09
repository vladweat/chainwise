"""Idempotently remove orphaned and superseded Qdrant code chunks."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.contract import Contract
from app.models.contract_status import COMPLETED_STATUSES, ContractStatus
from app.services.qdrant_indexer import COLLECTION_NAME, _get_client, _payload_of

logger = logging.getLogger(__name__)

DEFAULT_QDRANT_RECONCILE_BATCH_SIZE = 256


def reconcile_qdrant_generations(
    db: Session,
    *,
    client=None,
    dry_run: bool = True,
    batch_size: int = DEFAULT_QDRANT_RECONCILE_BATCH_SIZE,
) -> dict[str, int]:
    """Count or delete Qdrant points not referenced by the PostgreSQL state.

    Each page is compared with PostgreSQL after it is read. A committed active
    attempt is therefore visible before its worker can have written that page.
    Only one page of point IDs is retained at a time.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    qdrant = client if client is not None else _get_client()
    counts = {
        "orphan_points": 0,
        "stale_generation_points": 0,
        "unclassified_points": 0,
        "deleted_points": 0,
    }
    if not qdrant.collection_exists(COLLECTION_NAME):
        return counts

    offset = None
    while True:
        records, next_offset = qdrant.scroll(
            collection_name=COLLECTION_NAME,
            limit=batch_size,
            offset=offset,
            with_payload=["contract_id", "indexing_attempt_id"],
            with_vectors=False,
        )
        records = records or []
        if not records:
            break

        point_data = []
        contract_ids = set()
        for record in records:
            payload = _payload_of(record)
            try:
                contract_id = int(payload["contract_id"])
            except (KeyError, TypeError, ValueError):
                counts["unclassified_points"] += 1
                continue
            point_id = record.get("id") if isinstance(record, dict) else getattr(record, "id", None)
            if point_id is None:
                counts["unclassified_points"] += 1
                continue
            point_data.append((point_id, contract_id, payload.get("indexing_attempt_id")))
            contract_ids.add(contract_id)

        rows = (
            db.query(
                Contract.id,
                Contract.status,
                Contract.analysis_generation_id,
                Contract.indexing_attempt_id,
            )
            .filter(Contract.id.in_(contract_ids))
            .all()
            if contract_ids
            else []
        )
        states = {row.id: row for row in rows}
        delete_ids = []

        for point_id, contract_id, generation in point_data:
            state = states.get(contract_id)
            if state is None:
                counts["orphan_points"] += 1
                delete_ids.append(point_id)
                continue

            current_generations = set()
            if state.analysis_generation_id is not None:
                current_generations.add(state.analysis_generation_id)
            if (
                state.status == ContractStatus.INDEXING.value
                and state.indexing_attempt_id is not None
            ):
                current_generations.add(state.indexing_attempt_id)
            if (
                state.analysis_generation_id is None
                and state.status in COMPLETED_STATUSES | {ContractStatus.INDEXING}
            ):
                current_generations.add(None)

            if generation not in current_generations:
                counts["stale_generation_points"] += 1
                delete_ids.append(point_id)

        if delete_ids and not dry_run:
            qdrant.delete(
                collection_name=COLLECTION_NAME,
                points_selector=delete_ids,
                wait=True,
            )
            counts["deleted_points"] += len(delete_ids)

        if next_offset is None:
            break
        if next_offset == offset:
            raise RuntimeError("Qdrant scroll did not advance to the next page")
        offset = next_offset

    logger.info(
        "Qdrant generation reconciliation complete",
        extra={"dry_run": dry_run, **counts},
    )
    return counts
