"""Account deletion for personal SaaS."""

from __future__ import annotations

import logging

from fastapi import Response
from sqlalchemy.orm import Session

from app.core.auth_cookies import clear_auth_cookies, revoke_refresh_token
from app.models.contract import Contract
from app.models.user import User
from app.services.qdrant_indexer import delete_contract_chunks

logger = logging.getLogger(__name__)


def delete_user_account(
    db: Session,
    user: User,
    *,
    refresh_token: str | None = None,
    response: Response | None = None,
) -> None:
    contract_ids = [
        row.id for row in db.query(Contract.id).filter(Contract.user_id == user.id).all()
    ]
    for contract_id in contract_ids:
        try:
            delete_contract_chunks(contract_id)
        except Exception as exc:
            logger.warning("Qdrant cleanup failed for contract %s: %s", contract_id, exc)

    email = user.email
    db.delete(user)
    db.commit()
    logger.info("Deleted account %s (%s contracts)", email, len(contract_ids))

    if refresh_token:
        revoke_refresh_token(refresh_token)
    if response is not None:
        clear_auth_cookies(response)
