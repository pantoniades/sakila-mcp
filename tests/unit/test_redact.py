import logging
from collections.abc import Iterator

import pytest

from sakila_mcp.safety.redact import (
    MASK,
    RedactingFilter,
    clear_secrets,
    redact,
    redact_exception,
    register_secret,
)

PASSWORD = "S3cr3t!pw"


@pytest.fixture(autouse=True)
def _registered() -> Iterator[None]:
    clear_secrets()
    register_secret(PASSWORD)
    yield
    clear_secrets()


def test_password_removed_three_ways() -> None:
    exc = RuntimeError(
        f"auth failed for {PASSWORD}; "
        f"dsn host=db user=app password={PASSWORD}; "
        f"url mysql://app:{PASSWORD}@db:3306/sakila"
    )
    text = redact_exception(exc)
    assert PASSWORD not in text
    assert text.startswith("RuntimeError: ")
    assert "mysql://app:***@db:3306/sakila" in text
    assert "user=app" in text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("password=hunter2 next", f"password={MASK} next"),
        ("PASSWORD: 'two words'", f"PASSWORD: {MASK}"),
        ('pwd="x y z",host=a', f"pwd={MASK},host=a"),
        ("passwd = abc;", f"passwd = {MASK};"),
        ("mysql://u:p@h/db", f"mysql://u:{MASK}@h/db"),
        ("mysql+connector://u:p%40ss@h", f"mysql+connector://u:{MASK}@h"),
        ("no secrets here", "no secrets here"),
    ],
)
def test_patterns_without_registered_secret(raw: str, expected: str) -> None:
    clear_secrets()
    assert redact(raw) == expected


def test_empty_secret_ignored() -> None:
    clear_secrets()
    register_secret("")
    assert redact("abc") == "abc"


def test_logging_filter_redacts_message_args_and_traceback() -> None:
    records: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(self.format(record))

    handler = _Capture()
    handler.addFilter(RedactingFilter())
    logger = logging.getLogger("sakila_mcp.test_redact")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        logger.warning("connecting with %s", PASSWORD)
        try:
            raise ValueError(f"bad password={PASSWORD}")
        except ValueError:
            logger.exception("failed")
    finally:
        logger.removeHandler(handler)

    assert len(records) == 2
    joined = "\n".join(records)
    assert PASSWORD not in joined
    assert "connecting with ***" in records[0]
    assert "Traceback" in records[1]
