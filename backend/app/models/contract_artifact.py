"""Heavy contract blobs kept out of the contracts list row."""

from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class ContractArtifact(Base):
    __tablename__ = "contract_artifacts"

    contract_id: Mapped[int] = mapped_column(
        ForeignKey("contracts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_code: Mapped[str] = mapped_column(Text, nullable=False)
    ast: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    slither_report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    contract = relationship("Contract", back_populates="artifact")
