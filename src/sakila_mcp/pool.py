"""Connection pool with read-only session initialization on every checkout."""

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import Any

import mysql.connector
from mysql.connector.connection import MySQLConnection

from sakila_mcp import __version__
from sakila_mcp.capabilities import Capabilities, probe
from sakila_mcp.config import Config
from sakila_mcp.safety.redact import redact

log = logging.getLogger(__name__)

LOCK_WAIT_TIMEOUT_S = 5
CONNECT_TIMEOUT_S = 10
# Session sql_mode flags that change how quotes and backslashes are lexed. The
# classifier assumes MySQL's default lexing, so these are always removed.
# "ANSI" is listed because @@sql_mode reports it alongside the flags it implies.
STRIPPED_SQL_MODES = frozenset({"NO_BACKSLASH_ESCAPES", "ANSI_QUOTES", "ANSI"})

# Auth-related client/server error numbers for which we report the auth plugin.
_AUTH_ERRNOS = frozenset({1045, 1251, 2059, 2061})


class DatabaseError(Exception):
    """An infrastructure failure. The message is already redacted and safe to show."""


def connect_args(config: Config) -> dict[str, Any]:
    return {
        "host": config.host,
        "port": config.port,
        "user": config.user,
        "password": config.password.get_secret_value(),
        "use_pure": True,
        # A pooled connection must not sit in an open transaction: it would keep an
        # InnoDB read view (stale data, undo purge held back) between tool calls.
        "autocommit": True,
        "charset": "utf8mb4",
        "connection_timeout": CONNECT_TIMEOUT_S,
        # Keep values short: the whole attribute block must stay under 255 bytes.
        "conn_attrs": {"program_name": "sakila-mcp", "sakila_mcp_version": __version__},
    }


def init_session(cursor: Any, statement_timeout_ms: int) -> None:
    """Make the session read-only and bounded. Raises DatabaseError if it cannot.

    Runs on every checkout, not once per connection: COM_RESET_CONNECTION on release
    clears session variables and transaction characteristics.
    """
    cursor.execute("/* sakila-mcp init */ SET SESSION TRANSACTION READ ONLY")
    cursor.execute(
        "/* sakila-mcp init */ SET SESSION max_execution_time = %s, SESSION lock_wait_timeout = %s",
        (statement_timeout_ms, LOCK_WAIT_TIMEOUT_S),
    )
    cursor.execute("/* sakila-mcp init */ SELECT @@SESSION.sql_mode")
    (mode,) = cursor.fetchone()
    flags = [f for f in str(mode).split(",") if f]
    kept = [f for f in flags if f.upper() not in STRIPPED_SQL_MODES]
    if kept == flags:
        return
    cursor.execute("/* sakila-mcp init */ SET SESSION sql_mode = %s", (",".join(kept),))
    # Re-read instead of trusting the SET: the classifier is unsound if any stripped
    # flag is still active, so fail closed.
    cursor.execute("/* sakila-mcp init */ SELECT @@SESSION.sql_mode")
    (mode,) = cursor.fetchone()
    if STRIPPED_SQL_MODES & {f.upper() for f in str(mode).split(",")}:
        raise DatabaseError(f"could not clear lexing-related sql_mode flags (got {mode})")


def describe_connect_error(exc: mysql.connector.Error, cnx: MySQLConnection | None) -> str:
    """Redacted, agent-facing message; for auth failures it names the auth plugin."""
    message = redact(f"cannot connect to MySQL: {exc}")
    if exc.errno not in _AUTH_ERRNOS or cnx is None:
        return message
    plugin = _requested_auth_plugin(cnx)
    return f"{message} (server requested auth plugin: {plugin})"


def _requested_auth_plugin(cnx: MySQLConnection) -> str:
    # Connector internals, read defensively: the active strategy reflects any
    # AuthSwitchRequest; the handshake holds the server's initial plugin.
    authenticator = getattr(cnx, "_authenticator", None)
    strategy = getattr(authenticator, "_auth_strategy", None)
    name = getattr(strategy, "name", None)
    if isinstance(name, str) and name:
        return name
    handshake = getattr(cnx, "_handshake", None)
    if isinstance(handshake, dict):
        initial = handshake.get("auth_plugin")
        if isinstance(initial, bytes):
            initial = initial.decode("ascii", "replace")
        if isinstance(initial, str) and initial:
            return initial
    return "unknown"


class Database:
    """Owns a small connection pool. ``open()`` must succeed before tools can run.

    Not mysql.connector's MySQLConnectionPool: its config check opens a C-extension
    connection that adds keys to the shared ``conn_attrs`` dict, pushing the attribute
    block past the connector's 255-byte encoding limit. Here every connection gets a
    fresh ``connect_args()`` and connections are reset on return.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._slots = threading.BoundedSemaphore(config.pool_size)
        self._lock = threading.Lock()
        self._idle: list[MySQLConnection] = []
        self._capabilities: Capabilities | None = None
        self._closed = False

    @property
    def capabilities(self) -> Capabilities:
        if self._capabilities is None:
            raise DatabaseError("database is not open")
        return self._capabilities

    def open(self) -> Capabilities:
        """Connect once to verify credentials and probe capabilities."""
        cnx = self._connect()
        try:
            self._init(cnx)
            cursor = cnx.cursor()
            self._capabilities = probe(cursor)
            cursor.close()
        except mysql.connector.Error as exc:
            _discard(cnx)
            raise DatabaseError(redact(f"capability probe failed: {exc}")) from None
        self._closed = False
        self._release(cnx)
        log.info(
            "connected to MySQL %s (%s)",
            self._capabilities.version_raw,
            self._capabilities.release_line,
        )
        return self._capabilities

    def close(self) -> None:
        with self._lock:
            self._closed = True
            idle, self._idle = self._idle, []
        for cnx in idle:
            _discard(cnx)

    @contextmanager
    def connection(self) -> Iterator[MySQLConnection]:
        """Check out a connection with a freshly initialized read-only session."""
        if self._capabilities is None or self._closed:
            raise DatabaseError("database is not open")
        # A busy slot frees within one statement timeout (max_execution_time), plus
        # room for a reconnect, so waiting longer than that means something is wrong.
        wait_s = self._config.statement_timeout_ms / 1000 + CONNECT_TIMEOUT_S
        if not self._slots.acquire(timeout=wait_s):
            raise DatabaseError(f"all {self._config.pool_size} connections are busy; retry shortly")
        try:
            cnx = self._checkout()
            try:
                yield cnx
            finally:
                self._release(cnx)
        finally:
            self._slots.release()

    def _connect(self) -> MySQLConnection:
        cnx = MySQLConnection()
        try:
            cnx.connect(**connect_args(self._config))
        except mysql.connector.Error as exc:
            raise DatabaseError(describe_connect_error(exc, cnx)) from None
        return cnx

    def _init(self, cnx: MySQLConnection) -> None:
        cursor = cnx.cursor()
        try:
            init_session(cursor, self._config.statement_timeout_ms)
        finally:
            cursor.close()

    def _checkout(self) -> MySQLConnection:
        with self._lock:
            cnx = self._idle.pop() if self._idle else None
        if cnx is not None:
            try:
                self._init(cnx)
                return cnx
            except (mysql.connector.Error, DatabaseError):
                # Probably dropped by the server (wait_timeout) while idle: replace once.
                # DatabaseError (init_session fail-closed) must discard too, or it leaks.
                _discard(cnx)
        cnx = self._connect()
        try:
            self._init(cnx)
        except mysql.connector.Error as exc:
            _discard(cnx)
            raise DatabaseError(redact(f"session initialization failed: {exc}")) from None
        except DatabaseError:
            _discard(cnx)
            raise
        return cnx

    def _release(self, cnx: MySQLConnection) -> None:
        # A truncated result set leaves rows unread. Draining them could take as long as
        # the query itself, and cursor.close() / cmd_reset_connection() refuse to run
        # with a pending result ("Unread result found"). Closing the socket is instant
        # and the server abandons the statement, so throw the connection away.
        if cnx.unread_result:
            _discard(cnx)
            return
        # Reset so user variables, temp tables and session settings never leak to the
        # next caller; init_session re-applies the read-only settings at checkout.
        try:
            cnx.cmd_reset_connection()
        except mysql.connector.Error:
            _discard(cnx)
            return
        with self._lock:
            if not self._closed:
                self._idle.append(cnx)
                return
        _discard(cnx)


def _discard(cnx: MySQLConnection) -> None:
    with suppress(mysql.connector.Error, OSError):
        cnx.close()
