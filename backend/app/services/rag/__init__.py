"""RAG package public surface (also re-exported via app.services.rag_engine)."""

from app.services.rag.cache import (
    CACHE_VERSION,
    cache_key,
    load_cached_answer,
    persist_chat_turn,
    question_hash,
    save_cached_answer,
)
from app.services.rag.engine import answer_question, iter_sse_events
from app.services.rag.llm_client import (
    call_openai,
    estimate_cost,
    openai_base_url,
    openai_completions_url,
    openai_model,
    stream_openai,
)
from app.services.rag.prompting import (
    NO_CONTEXT_ANSWER,
    SYSTEM_PROMPT,
    build_messages,
    is_overview_question,
)
from app.services.rag.retrieval import (
    OVERVIEW_CODE_CHUNKS,
    apply_contract_context,
    bm25_scores,
    chunk_from_payload,
    citations_are_interface_stubs,
    citations_from_chunks,
    hybrid_search,
    primary_contract_names,
    reciprocal_rank_fusion,
    retrieve_for_chat,
    tokenize,
)
from app.services.rag.types import (
    ChatResult,
    LLMUsage,
    OpenAINotConfiguredError,
    OpenAIRequestError,
    RagError,
    RetrievedChunk,
)

__all__ = [
    "CACHE_VERSION",
    "ChatResult",
    "LLMUsage",
    "NO_CONTEXT_ANSWER",
    "OVERVIEW_CODE_CHUNKS",
    "OpenAINotConfiguredError",
    "OpenAIRequestError",
    "RagError",
    "RetrievedChunk",
    "SYSTEM_PROMPT",
    "answer_question",
    "apply_contract_context",
    "bm25_scores",
    "build_messages",
    "cache_key",
    "call_openai",
    "chunk_from_payload",
    "citations_are_interface_stubs",
    "citations_from_chunks",
    "estimate_cost",
    "hybrid_search",
    "is_overview_question",
    "iter_sse_events",
    "load_cached_answer",
    "openai_base_url",
    "openai_completions_url",
    "openai_model",
    "persist_chat_turn",
    "primary_contract_names",
    "question_hash",
    "reciprocal_rank_fusion",
    "retrieve_for_chat",
    "save_cached_answer",
    "stream_openai",
    "tokenize",
]
