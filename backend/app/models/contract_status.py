"""Contract load-status enum and allowed transitions."""

from __future__ import annotations

from enum import StrEnum

from fastapi import HTTPException, status


class ContractStatus(StrEnum):
    PENDING = "pending"
    INDEXING = "indexing"
    READY = "ready"
    LOADED = "loaded"  # legacy alias of ready; still accepted, never newly written
    FAILED = "failed"


COMPLETED_STATUSES = frozenset({ContractStatus.READY, ContractStatus.LOADED})
TERMINAL_STATUSES = frozenset({ContractStatus.READY, ContractStatus.LOADED, ContractStatus.FAILED})
CHAT_READY_STATUSES = frozenset({ContractStatus.READY})  # chat requires ready only

# actor -> allowed target statuses
_TRANSITIONS: dict[str, frozenset[ContractStatus]] = {
    "analyze": frozenset({ContractStatus.INDEXING}),
    "worker": frozenset({ContractStatus.READY, ContractStatus.FAILED}),
}


def as_status(value: str | ContractStatus | None) -> ContractStatus:
    if isinstance(value, ContractStatus):
        return value
    if not value:
        return ContractStatus.PENDING
    try:
        return ContractStatus(value)
    except ValueError:
        return ContractStatus.PENDING


def is_completed(value: str | ContractStatus | None) -> bool:
    return as_status(value) in COMPLETED_STATUSES


def is_terminal(value: str | ContractStatus | None) -> bool:
    return as_status(value) in TERMINAL_STATUSES


def is_indexing(value: str | ContractStatus | None) -> bool:
    return as_status(value) == ContractStatus.INDEXING


def set_contract_status(contract, new_status: ContractStatus, *, actor: str) -> None:
    """Apply a status transition. Raises 500-style error on invalid actor/target."""
    allowed = _TRANSITIONS.get(actor)
    if allowed is None:
        raise ValueError(f"Unknown status actor: {actor}")
    if new_status not in allowed:
        raise ValueError(f"Actor {actor!r} cannot set status {new_status.value!r}")
    contract.status = new_status.value


def require_report_ready(contract) -> None:
    if not is_completed(contract.status):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Contract is not ready for a report",
        )


def require_chat_ready(contract) -> None:
    if as_status(contract.status) not in CHAT_READY_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Contract is not ready for chat",
        )
