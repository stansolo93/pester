"""Telegram bot tokens never reach log output."""

from __future__ import annotations

import io
import logging

import pytest

from pester.cli.main import _configure_logging, _RedactSecretsFilter

# Fictional token in the Bot API shape "<bot id>:<35-char secret>", assembled at
# runtime so the repository secret scan does not flag this file.
TOKEN = "123456789" + ":" + "AA" + "Fake" * 8 + "_"
URL = f"https://api.telegram.org/bot{TOKEN}/getUpdates"


def _emit(logger_name: str, *args, exc: BaseException | None = None) -> str:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(_RedactSecretsFilter())
    logger = logging.getLogger(logger_name)
    logger.addHandler(handler)
    logger.propagate = False
    try:
        if exc is None:
            logger.warning(*args)
        else:
            try:
                raise exc
            except type(exc):
                logger.exception(*args)
    finally:
        logger.removeHandler(handler)
        logger.propagate = True
    return stream.getvalue()


def test_token_in_message_args_is_redacted():
    out = _emit("test.redact.args", 'HTTP Request: POST %s "%s"', URL, "HTTP/1.1 200 OK")
    assert TOKEN not in out
    assert "bot<redacted>/getUpdates" in out


def test_token_in_traceback_is_redacted():
    out = _emit(
        "test.redact.exc", "request failed", exc=RuntimeError(f"Client error for url {URL}")
    )
    assert TOKEN not in out
    assert "<redacted>" in out
    assert "RuntimeError" in out


def test_unrelated_numbers_pass_through():
    out = _emit("test.redact.plain", "processed %d of %d at 12:30:45", 3, 10)
    assert "processed 3 of 10 at 12:30:45" in out


@pytest.fixture
def restore_logging():
    root = logging.getLogger()
    level, handlers = root.level, list(root.handlers)
    client_levels = {name: logging.getLogger(name).level for name in ("httpx", "httpcore")}
    yield
    for handler in root.handlers:
        handler.filters = [f for f in handler.filters if not isinstance(f, _RedactSecretsFilter)]
    root.handlers = handlers
    root.setLevel(level)
    for name, client_level in client_levels.items():
        logging.getLogger(name).setLevel(client_level)


@pytest.mark.parametrize("level", [logging.DEBUG, logging.INFO])
def test_configure_logging_silences_http_client_request_lines(restore_logging, level):
    # basicConfig is a no-op when the root logger already has handlers (as under
    # pytest), so set the root level the way a fresh `pester -v` run would.
    logging.getLogger().setLevel(level)
    _configure_logging(level)
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
    handlers = logging.getLogger().handlers
    assert handlers
    assert all(any(isinstance(f, _RedactSecretsFilter) for f in h.filters) for h in handlers)
