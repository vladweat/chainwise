import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class UserRole(str, enum.Enum):
    user = "user"
    admin = "admin"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    google_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    role: Mapped[str] = mapped_column(
        String(20), default=UserRole.user.value, server_default=UserRole.user.value
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    contracts = relationship("Contract", back_populates="owner", passive_deletes=True)
    analysis_reports = relationship("AnalysisReport", back_populates="user")
    chat_messages = relationship("ChatMessage", back_populates="user", passive_deletes=True)