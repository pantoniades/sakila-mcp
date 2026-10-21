"""Policy: classification + configuration -> Allowed or Refusal."""

from dataclasses import dataclass

from mysql_mcp.config import Config
from mysql_mcp.models.common import Refusal
from mysql_mcp.safety.allowlist import check_access
from mysql_mcp.safety.classifier import Classification, classify


@dataclass(frozen=True)
class Allowed:
    classification: Classification


def check(
    classification: Classification, config: Config, database: str | None
) -> Allowed | Refusal:
    if classification.unknown_functions:
        names = ", ".join(classification.unknown_functions)
        return Refusal(
            code="unknown_function",
            reason=f"Not a known MySQL built-in function (stored functions are not allowed): "
            f"{names}.",
            suggestion="Use built-in functions only. sys schema helpers have built-in "
            "equivalents, e.g. FORMAT_BYTES() and FORMAT_PICO_TIME().",
        )

    hint = classification.max_execution_time_hint
    if hint is not None and not 0 < hint <= config.statement_timeout_ms:
        return Refusal(
            code="hint_exceeds_timeout",
            reason=f"MAX_EXECUTION_TIME({hint}) would override the server's "
            f"{config.statement_timeout_ms} ms statement timeout.",
            suggestion=f"Use a value between 1 and {config.statement_timeout_ms}, "
            "or remove the hint.",
        )

    refusal = check_access(
        classification.tables, classification.databases, database, config.database_allowlist
    )
    if refusal is not None:
        return refusal
    return Allowed(classification)


def evaluate(sql: str, config: Config, database: str | None) -> Allowed | Refusal:
    """Classify ``sql`` and apply configuration policy."""
    classification = classify(sql)
    if isinstance(classification, Refusal):
        return classification
    return check(classification, config, database)
