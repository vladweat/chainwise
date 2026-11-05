from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Contract(Base):
    __tablename__ = "contracts"
    __table_args__ = (
        UniqueConstraint("user_id", "address", "chain_id", name="uq_contracts_user_address_chain"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    address: Mapped[str] = mapped_column(String(42), nullable=False)
    chain_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    compiler_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_verified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    indexing_step: Mapped[str | None] = mapped_column(String(32), nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    risk_score: Mapped[str | None] = mapped_column(String(1), nullable=True)
    risk_model: Mapped[str | None] = mapped_column(String(20), nullable=True)
    features_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    chunk_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    owner = relationship("User", back_populates="contracts")
    artifact = relationship(
        "ContractArtifact",
        back_populates="contract",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="select",
    )
    analysis_reports = relationship("AnalysisReport", back_populates="contract")
    chat_messages = relationship("ChatMessage", back_populates="contract")

    def __init__(self, **kwargs):
        source_code = kwargs.pop("source_code", None)
        ast = kwargs.pop("ast", None)
        slither_report = kwargs.pop("slither_report", None)
        super().__init__(**kwargs)
        if source_code is not None or ast is not None or slither_report is not None:
            from app.models.contract_artifact import ContractArtifact

            self.artifact = ContractArtifact(
                source_code=source_code or "",
                ast=ast,
                slither_report=slither_report,
            )

    @property
    def source_code(self) -> str:
        return self.artifact.source_code if self.artifact is not None else ""

    @source_code.setter
    def source_code(self, value: str) -> None:
        from app.models.contract_artifact import ContractArtifact

        if self.artifact is None:
            self.artifact = ContractArtifact(source_code=value or "")
        else:
            self.artifact.source_code = value or ""

    @property
    def ast(self) -> dict | None:
        return self.artifact.ast if self.artifact is not None else None

    @ast.setter
    def ast(self, value: dict | None) -> None:
        from app.models.contract_artifact import ContractArtifact

        if self.artifact is None:
            self.artifact = ContractArtifact(source_code="", ast=value)
        else:
            self.artifact.ast = value

    @property
    def slither_report(self) -> dict | None:
        return self.artifact.slither_report if self.artifact is not None else None

    @slither_report.setter
    def slither_report(self, value: dict | None) -> None:
        from app.models.contract_artifact import ContractArtifact

        if self.artifact is None:
            self.artifact = ContractArtifact(source_code="", slither_report=value)
        else:
            self.artifact.slither_report = value
