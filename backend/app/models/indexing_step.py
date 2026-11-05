"""Indexing pipeline steps persisted on contracts.indexing_step."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy.orm import Session


class IndexingStep(StrEnum):
    COMPILE = "compile"
    SLITHER = "slither"
    EMBED = "embed"
    SCORE = "score"
    DONE = "done"


STEP_ORDER = (
    IndexingStep.COMPILE,
    IndexingStep.SLITHER,
    IndexingStep.EMBED,
    IndexingStep.SCORE,
    IndexingStep.DONE,
)


def set_indexing_step(db: Session, contract, step: IndexingStep | None) -> None:
    contract.indexing_step = step.value if step is not None else None
    db.commit()
