from sqlalchemy.orm import Session

from app.models.chat_message import ChatMessage
from app.schemas.chat import ChatMessageOut, Citation

CHAT_HISTORY_LIMIT = 500


def save_chat_turn(
    db: Session,
    *,
    contract_id: int,
    user_id: int,
    question: str,
    answer: str,
    citations: list[Citation],
    cached: bool,
) -> None:
    citation_payload = [citation.model_dump() for citation in citations]
    db.add(
        ChatMessage(
            contract_id=contract_id,
            user_id=user_id,
            role="user",
            content=question,
            citations=None,
            cached=False,
        )
    )
    db.add(
        ChatMessage(
            contract_id=contract_id,
            user_id=user_id,
            role="assistant",
            content=answer,
            citations=citation_payload,
            cached=cached,
        )
    )
    db.commit()


def list_chat_messages(
    db: Session,
    *,
    contract_id: int,
    user_id: int,
) -> list[ChatMessageOut]:
    rows = (
        db.query(ChatMessage)
        .filter(
            ChatMessage.contract_id == contract_id,
            ChatMessage.user_id == user_id,
        )
        .order_by(ChatMessage.id.asc())
        .limit(CHAT_HISTORY_LIMIT)
        .all()
    )
    return [_to_out(row) for row in rows]


def _to_out(row: ChatMessage) -> ChatMessageOut:
    citations: list[Citation] = []
    if row.citations:
        citations = [Citation.model_validate(item) for item in row.citations]
    return ChatMessageOut(
        id=row.id,
        role=row.role,
        content=row.content,
        citations=citations,
        cached=bool(row.cached),
        created_at=row.created_at,
    )
