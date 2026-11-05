"""Contract status transitions."""

import pytest
from fastapi import HTTPException

from app.models.contract import Contract
from app.models.contract_status import (
    ContractStatus,
    is_completed,
    require_chat_ready,
    require_report_ready,
    set_contract_status,
)


def test_analyze_actor_sets_indexing():
    contract = Contract(
        user_id=1,
        address="0x" + "a" * 40,
        chain_id=1,
        source_code="pragma solidity ^0.8.24; contract X {}",
        status="pending",
    )
    set_contract_status(contract, ContractStatus.INDEXING, actor="analyze")
    assert contract.status == "indexing"


def test_worker_sets_ready_and_failed():
    contract = Contract(
        user_id=1,
        address="0x" + "b" * 40,
        chain_id=1,
        source_code="x",
        status="indexing",
    )
    set_contract_status(contract, ContractStatus.READY, actor="worker")
    assert contract.status == "ready"
    set_contract_status(contract, ContractStatus.FAILED, actor="worker")
    assert contract.status == "failed"


def test_analyze_cannot_set_ready():
    contract = Contract(
        user_id=1,
        address="0x" + "c" * 40,
        chain_id=1,
        source_code="x",
        status="indexing",
    )
    with pytest.raises(ValueError, match="cannot set status"):
        set_contract_status(contract, ContractStatus.READY, actor="analyze")


def test_loaded_is_completed_for_report_not_chat():
    contract = Contract(
        user_id=1,
        address="0x" + "d" * 40,
        chain_id=1,
        source_code="x",
        status="loaded",
    )
    assert is_completed(contract.status)
    require_report_ready(contract)
    with pytest.raises(HTTPException) as exc:
        require_chat_ready(contract)
    assert exc.value.status_code == 409
