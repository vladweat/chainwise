import io
import json
import logging

from app.utils.logging import RedactingJsonFormatter, setup_logging


def _format(record: logging.LogRecord) -> dict:
    formatter = RedactingJsonFormatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
    )
    return json.loads(formatter.format(record))


def test_redacts_source_code_extra():
    record = logging.LogRecord(
        "test",
        logging.INFO,
        __file__,
        1,
        "indexing done",
        (),
        None,
    )
    record.source_code = "pragma solidity ^0.8.24; contract Foo {}"
    data = _format(record)
    assert data["source_code"] == "[redacted]"
    assert "pragma" not in data["message"]


def test_redacts_llm_answer_extra():
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "cached", (), None)
    record.llm_answer = "The owner can call withdraw"
    record.answer = "The owner can call withdraw"
    data = _format(record)
    assert data["llm_answer"] == "[redacted]"
    assert data["answer"] == "[redacted]"


def test_redacts_long_solidity_message():
    source = "pragma solidity ^0.8.24;\n" + ("function foo() public {}\n" * 20)
    record = logging.LogRecord("test", logging.INFO, __file__, 1, source, (), None)
    data = _format(record)
    assert data["message"] == "[redacted]"


def test_setup_logging_replaces_formatter():
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    setup_logging("INFO")
    setup_logging("DEBUG")
    assert root.handlers
    assert isinstance(root.handlers[0].formatter, RedactingJsonFormatter)


def test_short_messages_stay_plaintext():
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "ok", (), None)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        RedactingJsonFormatter(
            "%(asctime)s %(name)s %(levelname)s %(message)s",
            rename_fields={"asctime": "timestamp", "levelname": "level"},
        )
    )
    handler.emit(record)
    assert '"message": "ok"' in stream.getvalue()
