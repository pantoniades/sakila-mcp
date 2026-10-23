"""Server capabilities, probed once at startup."""

from dataclasses import dataclass
from typing import Any

from sakila_mcp.versions import Version, parse_version, release_line


@dataclass(frozen=True)
class Capabilities:
    version: Version
    version_raw: str
    version_comment: str
    release_line: str  # "8.0 LTS" | "8.4 LTS" | "9.7 LTS" | "Innovation" | "unknown"
    explain_json_version: int  # 1 or 2
    # Visible to this account (information_schema.SCHEMATA is privilege-filtered).
    has_sys_schema: bool


def probe(cursor: Any) -> Capabilities:
    """Probe capabilities with an open cursor on an initialized session."""
    cursor.execute("/* sakila-mcp probe=capabilities */ SELECT VERSION(), @@version_comment")
    version_raw, version_comment = cursor.fetchone()
    version = parse_version(str(version_raw))

    cursor.execute(
        "/* sakila-mcp probe=capabilities */ SHOW VARIABLES LIKE 'explain_json_format_version'"
    )
    row = cursor.fetchone()
    explain_json_version = 2 if row is not None and str(row[1]) == "2" else 1

    cursor.execute(
        "/* sakila-mcp probe=capabilities */ "
        "SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = 'sys'"
    )
    (sys_count,) = cursor.fetchone()

    return Capabilities(
        version=version,
        version_raw=str(version_raw),
        version_comment=str(version_comment),
        release_line=release_line(version),
        explain_json_version=explain_json_version,
        has_sys_schema=int(sys_count) > 0,
    )
