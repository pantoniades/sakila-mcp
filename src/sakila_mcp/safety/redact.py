"""Secret redaction for exception text and log records."""

import logging
import re
import threading

MASK = "***"

_URL_CREDENTIALS_RE = re.compile(r"(?P<prefix>\b[a-zA-Z][\w+.-]*://[^:/@\s]*):[^@\s]*@")
_PASSWORD_KV_RE = re.compile(
    r"(?P<key>\b(?:password|passwd|pwd)\b\s*[=:]\s*)"
    r"(?:'[^']*'|\"[^\"]*\"|[^\s,;)&]+)",
    re.IGNORECASE,
)

_lock = threading.Lock()
_secrets: set[str] = set()


def register_secret(secret: str) -> None:
    """Register a literal value (e.g. the configured password) to mask everywhere."""
    if not secret:
        return
    with _lock:
        _secrets.add(secret)


def clear_secrets() -> None:
    """Forget all registered secrets. Intended for tests."""
    with _lock:
        _secrets.clear()


def redact(text: str) -> str:
    """Mask registered secrets, ``password=...`` pairs and ``scheme://user:pass@`` URLs."""
    text = _URL_CREDENTIALS_RE.sub(lambda m: f"{m.group('prefix')}:{MASK}@", text)
    text = _PASSWORD_KV_RE.sub(lambda m: f"{m.group('key')}{MASK}", text)
    with _lock:
        # Longest first so a secret containing another secret is masked whole.
        secrets = sorted(_secrets, key=len, reverse=True)
    for secret in secrets:
        text = text.replace(secret, MASK)
    return text


def redact_exception(exc: BaseException) -> str:
    """Return ``"ExcType: message"`` with secrets masked."""
    return redact(f"{type(exc).__name__}: {exc}")


class RedactingFilter(logging.Filter):
    """Logging filter that redacts the rendered message and any exception text."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True
