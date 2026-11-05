import logging
import re

from pythonjsonlogger import jsonlogger

SENSITIVE_KEYS = {
    "source_code",
    "llm_answer",
    "answer",
    "question",
    "prompt",
    "messages",
    "files",
    "content",
    "authorization",
    "cookie",
    "password",
    "id_token",
    "access_token",
    "refresh_token",
}

_SOURCE_MARKERS = re.compile(
    r"pragma\s+solidity|function\s+\w+\s*\(|contract\s+\w+",
    re.IGNORECASE,
)


class RedactingJsonFormatter(jsonlogger.JsonFormatter):
    """JSON logs without Solidity source or LLM plaintext."""

    def add_fields(self, log_record, record, message_dict) -> None:
        super().add_fields(log_record, record, message_dict)
        for key in list(log_record):
            if key.lower() in SENSITIVE_KEYS:
                log_record[key] = "[redacted]"
        message = log_record.get("message")
        if isinstance(message, str) and _looks_like_source_or_llm(message):
            log_record["message"] = "[redacted]"


def _looks_like_source_or_llm(text: str) -> bool:
    if len(text) < 80:
        return False
    if _SOURCE_MARKERS.search(text):
        return True
    if len(text) > 1200:
        return True
    return False


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if root.handlers:
        for handler in root.handlers:
            handler.setFormatter(
                RedactingJsonFormatter(
                    "%(asctime)s %(name)s %(levelname)s %(message)s",
                    rename_fields={"asctime": "timestamp", "levelname": "level"},
                )
            )
        root.setLevel(level.upper())
        return

    handler = logging.StreamHandler()
    formatter = RedactingJsonFormatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
    )
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(level.upper())
