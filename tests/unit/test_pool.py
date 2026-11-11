from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import mysql.connector
import pytest

from sakila_mcp import __version__
from sakila_mcp.config import Config
from sakila_mcp.pool import (
    LOCK_WAIT_TIMEOUT_S,
    Database,
    DatabaseError,
    connect_args,
    describe_connect_error,
    init_session,
)
from sakila_mcp.safety.redact import clear_secrets, register_secret

PASSWORD = "pw-Zq81-secret"


class FakeCursor:
    """Records statements; SELECT @@SESSION.sql_mode answers from a queue."""

    def __init__(self, modes: list[str]) -> None:
        self.modes = modes
        self.executed: list[tuple[str, Any]] = []
        self._row: tuple[Any, ...] | None = None

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((sql, params))
        if "SELECT @@SESSION.sql_mode" in sql:
            self._row = (self.modes.pop(0),)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row


@pytest.fixture(autouse=True)
def _secret() -> Iterator[None]:
    clear_secrets()
    register_secret(PASSWORD)
    yield
    clear_secrets()


def test_init_session_sets_read_only_and_limits() -> None:
    cursor = FakeCursor(["ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES"])
    init_session(cursor, 1234)
    statements = [sql for sql, _ in cursor.executed]
    assert statements[0].endswith("SET SESSION TRANSACTION READ ONLY")
    assert "max_execution_time" in statements[1]
    assert "lock_wait_timeout" in statements[1]
    assert cursor.executed[1][1] == (1234, LOCK_WAIT_TIMEOUT_S)
    assert len(statements) == 3  # no sql_mode change needed


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("NO_BACKSLASH_ESCAPES,STRICT_TRANS_TABLES", "STRICT_TRANS_TABLES"),
        ("STRICT_TRANS_TABLES,NO_BACKSLASH_ESCAPES", "STRICT_TRANS_TABLES"),
        (
            "REAL_AS_FLOAT,PIPES_AS_CONCAT,ANSI_QUOTES,IGNORE_SPACE,ONLY_FULL_GROUP_BY,ANSI",
            "REAL_AS_FLOAT,PIPES_AS_CONCAT,IGNORE_SPACE,ONLY_FULL_GROUP_BY",
        ),
        ("NO_BACKSLASH_ESCAPES", ""),
    ],
)
def test_init_session_strips_lexing_modes(mode: str, expected: str) -> None:
    cursor = FakeCursor([mode, expected])
    init_session(cursor, 1000)
    set_mode = [(sql, p) for sql, p in cursor.executed if "SET SESSION sql_mode" in sql]
    assert set_mode == [("/* sakila-mcp init */ SET SESSION sql_mode = %s", (expected,))]


def test_init_session_fails_closed_if_mode_sticks() -> None:
    cursor = FakeCursor(["NO_BACKSLASH_ESCAPES", "NO_BACKSLASH_ESCAPES"])
    with pytest.raises(DatabaseError, match="sql_mode"):
        init_session(cursor, 1000)


def test_connect_args() -> None:
    args = connect_args(Config(user="mcp", password=PASSWORD, host="db", port=3307))
    assert args["conn_attrs"] == {"program_name": "sakila-mcp", "sakila_mcp_version": __version__}
    assert args["autocommit"] is True
    assert (args["host"], args["port"], args["user"]) == ("db", 3307, "mcp")


def test_auth_error_names_plugin_and_hides_password() -> None:
    exc = mysql.connector.Error(
        msg=f"Access denied for user 'mcp' (password={PASSWORD})", errno=1045
    )
    cnx = SimpleNamespace(
        _authenticator=SimpleNamespace(_auth_strategy=SimpleNamespace(name="caching_sha2_password"))
    )
    message = describe_connect_error(exc, cnx)  # type: ignore[arg-type]
    assert "caching_sha2_password" in message
    assert PASSWORD not in message


def test_auth_error_falls_back_to_handshake_plugin() -> None:
    exc = mysql.connector.Error(msg="Access denied", errno=1045)
    cnx = SimpleNamespace(_handshake={"auth_plugin": b"mysql_native_password"})
    message = describe_connect_error(exc, cnx)  # type: ignore[arg-type]
    assert "mysql_native_password" in message


def test_auth_error_plugin_unknown() -> None:
    exc = mysql.connector.Error(msg="Access denied", errno=1045)
    assert "unknown" in describe_connect_error(exc, SimpleNamespace())  # type: ignore[arg-type]


def test_non_auth_error_has_no_plugin_suffix() -> None:
    exc = mysql.connector.Error(msg=f"Can't connect to mysql://u:{PASSWORD}@h", errno=2003)
    message = describe_connect_error(exc, None)
    assert "auth plugin" not in message
    assert PASSWORD not in message


def test_checkout_discards_idle_connection_when_init_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[bool] = []
    idle = SimpleNamespace(close=lambda: closed.append(True))
    db = Database(Config(user="mcp", password=PASSWORD))
    db._idle.append(idle)  # type: ignore[arg-type]

    def fail_init(_cnx: Any) -> None:
        raise DatabaseError("could not clear lexing-related sql_mode flags")

    def fail_connect() -> Any:
        raise DatabaseError("cannot connect")

    monkeypatch.setattr(db, "_init", fail_init)
    monkeypatch.setattr(db, "_connect", fail_connect)
    with pytest.raises(DatabaseError, match="cannot connect"):
        db._checkout()
    assert closed == [True]
