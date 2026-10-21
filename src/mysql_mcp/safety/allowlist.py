"""Database allowlist.

System schemas are always readable. ``mysql`` is denied unless listed explicitly.
Allow checks are exact (case-sensitive) and deny checks are case-insensitive, so the
result is never more permissive than the server's ``lower_case_table_names`` setting.
"""

from collections.abc import Iterable, Sequence

from mysql_mcp.models.common import Refusal
from mysql_mcp.safety.classifier import TableRef

SYSTEM_SCHEMAS = frozenset({"information_schema", "performance_schema", "sys"})
DENIED_BY_DEFAULT = "mysql"


def is_database_allowed(db: str, allowlist: Sequence[str]) -> bool:
    if db.lower() in SYSTEM_SCHEMAS:
        return True
    if db.lower() == DENIED_BY_DEFAULT:
        return DENIED_BY_DEFAULT in allowlist and db == DENIED_BY_DEFAULT
    return not allowlist or db in allowlist


def filter_databases(names: Iterable[str], allowlist: Sequence[str]) -> list[str]:
    return [name for name in names if is_database_allowed(name, allowlist)]


def _denied(db: str) -> Refusal:
    return Refusal(
        code="database_not_allowed",
        reason=f"Database {db!r} is not in this server's allowlist.",
        suggestion="Call list_databases to see which databases you can query.",
    )


def check_access(
    tables: Sequence[TableRef],
    databases: Sequence[str | None],
    default_db: str | None,
    allowlist: Sequence[str],
) -> Refusal | None:
    """Refuse if the statement touches a non-allowlisted database, or needs a default."""
    if default_db is not None and not is_database_allowed(default_db, allowlist):
        return _denied(default_db)
    referenced = [t.db for t in tables] + list(databases)
    for db in referenced:
        if db is None:
            if default_db is None:
                return Refusal(
                    code="no_database_selected",
                    reason="The statement uses an unqualified name and no database was given.",
                    suggestion="Pass the database argument or qualify names as db.table.",
                )
        elif not is_database_allowed(db, allowlist):
            return _denied(db)
    return None
