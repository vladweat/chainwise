from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.contract import ContractStatusResponse
from app.services.contract_service import list_contracts_for_user

router = APIRouter(tags=["history"])


@router.get("/history", response_model=list[ContractStatusResponse])
def list_history(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ContractStatusResponse]:
    return list_contracts_for_user(db, current_user)
