"""Backward-compatible RAG entrypoint — implementation lives in app.services.rag."""

from app.services.rag import *  # noqa: F403
from app.services.rag import __all__  # noqa: F401
