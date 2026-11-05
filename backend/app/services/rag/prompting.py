"""Prompt construction and question-type heuristics."""

from __future__ import annotations

import re

from app.services.rag.types import RetrievedChunk

OVERVIEW_QUESTION_RE = re.compile(
    r"безопас|уязвим|аудит|риск|защищ|"
    r"\b(?:safe|unsafe|secur|vulnerab|audit|risk|exploit|reentranc|overflow)\w*",
    re.IGNORECASE,
)

SYSTEM_PROMPT = (
    "You are a smart-contract security expert. Answer ONLY using the provided "
    "Solidity code chunks and any analysis report. If they do not contain enough "
    "information, say so explicitly — do not invent code, modifiers, or behavior. "
    "If the source is only an interface or has no function bodies, say that a "
    "security assessment of a token or protocol is not possible from this file "
    "alone and that the implementation must be uploaded. When a Slither / risk "
    "score report is provided, use it. When you refer to code, mention the file "
    "path and line range. Reply in the same language as the user's question."
)

NO_CONTEXT_ANSWER = (
    "В индексе нет фрагментов кода по этому контракту, поэтому я не могу ответить "
    "на вопрос. Дождитесь завершения индексации или загрузите контракт заново."
)


def is_overview_question(question: str) -> bool:
    return bool(OVERVIEW_QUESTION_RE.search(question))


def build_messages(question: str, chunks: list[RetrievedChunk]) -> list[dict[str, str]]:
    if not chunks:
        context = "(no code chunks retrieved)"
    else:
        parts = []
        for index, chunk in enumerate(chunks, start=1):
            parts.append(
                f"[Chunk {index}] {chunk.file_path}:{chunk.start_line}-{chunk.end_line} "
                f"({chunk.function_name})\n```solidity\n{chunk.content}\n```"
            )
        context = "\n\n".join(parts)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Code chunks:\n{context}\n\nQuestion: {question}",
        },
    ]
