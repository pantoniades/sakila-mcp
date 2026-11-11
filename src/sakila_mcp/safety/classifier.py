"""Read-only SQL classifier. Pure: no database access, no configuration.

Checks run in this order, and anything that cannot be understood is refused:

1. ``/*!`` anywhere in the raw text (even inside a literal).
2. Tokenize with sqlglot's MySQL tokenizer (comments are dropped here; optimizer hints
   become HINT tokens). Exactly one statement; an optional trailing ``;`` may be
   followed only by whitespace.
3. Statement type by first keyword; then INTO / locking / ``:=`` at token level,
   so they are caught even where sqlglot cannot parse them.
4. SHOW is matched against a hand-written sub-allowlist (no sqlglot parse).
   Everything else is parsed and its AST walked for nested write statements.
5. Function calls are found from tokens (``name`` followed by ``(``), not from the
   AST, because sqlglot normalizes names (``NVL`` -> ``COALESCE``) and stored
   functions can only be invoked with parentheses.
"""

import re
from dataclasses import dataclass, field
from enum import StrEnum

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.tokenizer_core import Token, TokenType

from sakila_mcp.models.common import Refusal
from sakila_mcp.safety.builtins import BUILTIN_FUNCTIONS, DENIED_FUNCTIONS, PAREN_KEYWORDS


class Kind(StrEnum):
    SELECT = "select"
    SHOW = "show"
    EXPLAIN = "explain"
    DESCRIBE = "describe"
    TABLE = "table"
    VALUES = "values"


@dataclass(frozen=True)
class TableRef:
    db: str | None
    name: str


@dataclass(frozen=True)
class Classification:
    kind: Kind
    tables: list[TableRef] = field(default_factory=list)
    # Schemas referenced without a table (SHOW TABLES FROM db); None = default database.
    databases: list[str | None] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    unknown_functions: list[str] = field(default_factory=list)
    flags: frozenset[str] = frozenset()
    max_execution_time_hint: int | None = None


# "(" admits parenthesized query expressions. The first word is only a cheap gate; the
# AST walk and token checks below are what actually prove the statement is a read
# (e.g. ``WITH x AS (...) DELETE ...`` starts with an allowed word).
_ALLOWED_FIRST_WORDS = frozenset(
    {"SELECT", "WITH", "TABLE", "VALUES", "SHOW", "DESCRIBE", "DESC", "EXPLAIN", "("}
)

# Nodes that must never appear anywhere in an accepted statement's AST. ``Command`` is
# what sqlglot falls back to for syntax it cannot parse, so listing it makes unknown
# syntax fail closed. ``Show`` is listed because SHOW is accepted only through the
# hand-written matcher, never as a nested node.
_FORBIDDEN_NODES: tuple[type[exp.Expr], ...] = (
    exp.DML,
    exp.DDL,
    exp.Command,
    exp.Set,
    exp.Use,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.LoadData,
    exp.Grant,
    exp.Revoke,
    exp.Analyze,
    exp.Kill,
    exp.TruncateTable,
    exp.Merge,
    exp.Pragma,
    exp.Copy,
    exp.Cache,
    exp.Refresh,
    exp.Execute,
    exp.Returning,
    exp.Show,
)

# Token types that can be followed by "(" without being a function name ('abc'(, 1().
_STRING_TOKENS = frozenset(t for t in TokenType if t.name.endswith("STRING")) | {
    TokenType.NUMBER,
    TokenType.PARAMETER,
}
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_SET_VAR_RE = re.compile(r"\bSET_VAR\s*\(", re.IGNORECASE)
_MAX_EXEC_RE = re.compile(r"\bMAX_EXECUTION_TIME\s*\(([^)]*)\)", re.IGNORECASE)

_SUGGEST_READ = "Use a single SELECT, SHOW, DESCRIBE or EXPLAIN statement."


def _refuse(code: str, reason: str, suggestion: str = _SUGGEST_READ) -> Refusal:
    return Refusal(code=code, reason=reason, suggestion=suggestion)


def classify(sql: str) -> Classification | Refusal:
    """Classify one SQL statement as an accepted read, or return a Refusal."""
    if "/*!" in sql:
        return _refuse(
            "version_comment",
            "MySQL version comments (/*! ... */) are not allowed.",
            "Remove the /*! ... */ comment; write the SQL it contains directly.",
        )
    try:
        tokens = sqlglot.tokenize(sql, read="mysql")
    except SqlglotError as exc:
        return _parse_error(exc)
    if not tokens:
        return _refuse("empty", "The statement is empty.", "Provide a SQL statement.")

    multi = _check_single_statement(sql, tokens)
    if multi is not None:
        return multi
    if tokens[-1].token_type is TokenType.SEMICOLON:
        tokens = tokens[:-1]
        if not tokens:
            return _refuse("empty", "The statement is empty.", "Provide a SQL statement.")

    first = tokens[0].text.upper()
    if first not in _ALLOWED_FIRST_WORDS:
        return _refuse(
            "statement_not_allowed",
            f"{first} statements are not allowed; this server is read-only.",
        )

    token_refusal = _check_tokens(tokens)
    if token_refusal is not None:
        return token_refusal

    hint = _check_hints(tokens)
    if isinstance(hint, Refusal):
        return hint

    if first == "SHOW":
        return _classify_show(tokens[1:])

    return _classify_parsed(sql, tokens, first, hint)


# --------------------------------------------------------------------------- token checks


def _parse_error(exc: Exception) -> Refusal:
    detail = str(exc).splitlines()[0][:200] if str(exc) else type(exc).__name__
    return _refuse(
        "parse_error",
        f"The statement could not be parsed, so it is refused: {detail}",
        "Check the SQL syntax. Only standard MySQL SELECT/SHOW/DESCRIBE/EXPLAIN is accepted.",
    )


def _check_single_statement(sql: str, tokens: list[Token]) -> Refusal | None:
    # The tokenizer drops comments, so whatever follows the final ";" is checked on the
    # raw text; this also refuses a trailing comment after the semicolon.
    semicolons = [i for i, t in enumerate(tokens) if t.token_type is TokenType.SEMICOLON]
    if not semicolons:
        return None
    last = semicolons[-1]
    if len(semicolons) > 1 or last != len(tokens) - 1 or sql[tokens[last].end + 1 :].strip():
        return _refuse(
            "multi_statement",
            "Only one statement per call is allowed.",
            "Send each statement in a separate call, with nothing after a trailing ';'.",
        )
    return None


def _check_tokens(tokens: list[Token]) -> Refusal | None:
    # Token level on purpose: sqlglot does not parse INTO after FROM, and a trailing
    # "LOCK IN SHARE MODE" / ":=" may never reach the AST. These must not depend on a
    # successful parse.
    for i, tok in enumerate(tokens):
        nxt = tokens[i + 1].text.upper() if i + 1 < len(tokens) else ""
        if tok.token_type is TokenType.INTO:
            return _refuse(
                "into_clause",
                "SELECT ... INTO (OUTFILE, DUMPFILE or variables) is not allowed.",
                "Remove the INTO clause; results are returned to you directly.",
            )
        if tok.token_type is TokenType.COLON_EQ:
            return _refuse(
                "variable_assignment",
                "Variable assignment with := is not allowed.",
                "Rewrite the query without user-variable assignment (e.g. use a window function).",
            )
        word = tok.text.upper()
        if (word == "FOR" and nxt in {"UPDATE", "SHARE"}) or (word == "LOCK" and nxt == "IN"):
            return _refuse(
                "locking_clause",
                "Locking reads (FOR UPDATE, FOR SHARE, LOCK IN SHARE MODE) are not allowed.",
                "Remove the locking clause; a plain SELECT returns the same rows.",
            )
    return None


def _check_hints(tokens: list[Token]) -> int | Refusal | None:
    """Return the MAX_EXECUTION_TIME hint value (largest if repeated), or a Refusal."""
    values: list[int] = []
    for tok in tokens:
        if tok.token_type is not TokenType.HINT:
            continue
        # SET_VAR could override the per-session limits and sql_mode pinned at checkout.
        if _SET_VAR_RE.search(tok.text):
            return _refuse(
                "set_var_hint",
                "The SET_VAR optimizer hint is not allowed; it changes session settings.",
                "Remove the SET_VAR hint. Other optimizer hints are allowed.",
            )
        for match in _MAX_EXEC_RE.finditer(tok.text):
            arg = match.group(1).strip()
            if not arg.isdigit():
                return _refuse(
                    "invalid_hint",
                    f"MAX_EXECUTION_TIME hint has a non-integer argument: {arg!r}.",
                    "Use MAX_EXECUTION_TIME(<milliseconds>) or remove the hint.",
                )
            values.append(int(arg))
    return max(values) if values else None


# --------------------------------------------------------------------------- SHOW


class _ShowCursor:
    """Word-level cursor over SHOW tokens. Quoted identifiers are never keywords."""

    def __init__(self, tokens: list[Token]) -> None:
        # (word, original text, may_be_identifier, is_string). A quoted identifier gets
        # an empty word so `from` can never match a keyword. One token's text can hold
        # several words (multi-word keywords), hence the split.
        self.items: list[tuple[str, str, bool, bool]] = []
        for tok in tokens:
            if tok.token_type is TokenType.IDENTIFIER:
                self.items.append(("", tok.text, True, False))
            elif tok.token_type in _STRING_TOKENS:
                self.items.append(("", tok.text, False, tok.token_type is TokenType.STRING))
            else:
                is_var = tok.token_type is TokenType.VAR
                for part in tok.text.split():
                    self.items.append((part.upper(), part, is_var, False))
        self.pos = 0

    def at_end(self) -> bool:
        return self.pos >= len(self.items)

    def kw(self, *words: str) -> bool:
        end = self.pos + len(words)
        if end > len(self.items):
            return False
        if all(self.items[self.pos + i][0] == w for i, w in enumerate(words)):
            self.pos = end
            return True
        return False

    def ident(self) -> str | None:
        if self.at_end() or not self.items[self.pos][2]:
            return None
        self.pos += 1
        return self.items[self.pos - 1][1]

    def table_ref(self) -> TableRef | None:
        first = self.ident()
        if first is None:
            return None
        if self.kw("."):
            second = self.ident()
            return TableRef(first, second) if second is not None else None
        return TableRef(None, first)

    def like(self) -> bool:
        """Consume an optional ``LIKE 'pattern'``; False if LIKE has no string."""
        if not self.kw("LIKE"):
            return True
        if self.at_end() or not self.items[self.pos][3]:
            return False
        self.pos += 1
        return True


_ShowResult = tuple[list[TableRef], list[str | None]]


def _show_tables_tail(c: _ShowCursor) -> _ShowResult | None:
    db: str | None = None
    if c.kw("FROM") or c.kw("IN"):
        db = c.ident()
        if db is None:
            return None
    return ([], [db]) if c.like() else None


def _show_columns_tail(c: _ShowCursor, allow_like: bool) -> _ShowResult | None:
    if not (c.kw("FROM") or c.kw("IN")):
        return None
    ref = c.table_ref()
    if ref is None:
        return None
    if c.kw("FROM") or c.kw("IN"):
        db = c.ident()
        if db is None or ref.db is not None:  # both forms at once: refuse, don't guess
            return None
        ref = TableRef(db, ref.name)
    if allow_like and not c.like():
        return None
    return [ref], []


def _match_show(c: _ShowCursor) -> _ShowResult | None:
    none: _ShowResult = ([], [])
    if c.kw("FULL"):
        if c.kw("TABLES"):
            return _show_tables_tail(c)
        if c.kw("COLUMNS") or c.kw("FIELDS"):
            return _show_columns_tail(c, allow_like=True)
        return none if c.kw("PROCESSLIST") else None
    if c.kw("GLOBAL") or c.kw("SESSION") or c.kw("LOCAL"):
        if c.kw("VARIABLES") or c.kw("STATUS"):
            return none if c.like() else None
        return None
    if c.kw("TABLES") or c.kw("TABLE", "STATUS"):
        return _show_tables_tail(c)
    if c.kw("COLUMNS") or c.kw("FIELDS"):
        return _show_columns_tail(c, allow_like=True)
    if c.kw("INDEX") or c.kw("INDEXES") or c.kw("KEYS"):
        return _show_columns_tail(c, allow_like=False)
    if c.kw("CREATE", "TABLE") or c.kw("CREATE", "VIEW"):
        ref = c.table_ref()
        return ([ref], []) if ref is not None else None
    if c.kw("REPLICA", "STATUS"):
        if c.kw("FOR", "CHANNEL") and c.ident() is None:
            return None
        return none
    if (
        c.kw("DATABASES")
        or c.kw("SCHEMAS")
        or c.kw("VARIABLES")
        or c.kw("STATUS")
        or c.kw("CHARSET")
        or c.kw("CHARACTER", "SET")
        or c.kw("COLLATION")
    ):
        return none if c.like() else None
    if (
        c.kw("ENGINE", "INNODB", "STATUS")
        or c.kw("PROCESSLIST")
        or c.kw("BINARY", "LOG", "STATUS")
        or c.kw("MASTER", "STATUS")
        or c.kw("WARNINGS")
        or c.kw("ERRORS")
        or c.kw("PLUGINS")
        or c.kw("ENGINES")
    ):
        return none
    return None


def _classify_show(tokens: list[Token]) -> Classification | Refusal:
    # SHOW is not parsed, so a WHERE clause would carry expressions (calls, subqueries)
    # that the function and table checks never see.
    if any(t.token_type is TokenType.WHERE for t in tokens):
        return _refuse(
            "show_where",
            "SHOW ... WHERE is not allowed.",
            "Use SHOW ... LIKE 'pattern', or SELECT from information_schema with a WHERE.",
        )
    cursor = _ShowCursor(tokens)
    matched = _match_show(cursor)
    if matched is None or not cursor.at_end():
        shown = " ".join(t.text for t in tokens[:3]).upper()
        return _refuse(
            "show_not_allowed",
            f"SHOW {shown} is not in the allowed SHOW list.",
            "Allowed: SHOW DATABASES, [FULL] TABLES, TABLE STATUS, [FULL] COLUMNS, INDEX, "
            "CREATE TABLE/VIEW, [GLOBAL|SESSION] VARIABLES/STATUS, ENGINE INNODB STATUS, "
            "[FULL] PROCESSLIST, REPLICA/BINARY LOG/MASTER STATUS, WARNINGS, ERRORS, "
            "PLUGINS, ENGINES, CHARSET, COLLATION.",
        )
    tables, databases = matched
    return Classification(kind=Kind.SHOW, tables=tables, databases=databases)


# --------------------------------------------------------------------------- parsed statements


def _classify_parsed(
    sql: str, tokens: list[Token], first: str, hint: int | None
) -> Classification | Refusal:
    # MySQL's TABLE statement is not understood by sqlglot; analyse the equivalent
    # SELECT * FROM. Only the analysis uses this text; the user's SQL is never rewritten.
    text = f"SELECT * FROM {sql[tokens[0].end + 1 :]}" if first == "TABLE" else sql  # noqa: S608
    try:
        statements = [s for s in sqlglot.parse(text, read="mysql") if s is not None]
    except SqlglotError as exc:
        return _parse_error(exc)
    if len(statements) != 1:
        return _refuse("multi_statement", "Only one statement per call is allowed.")
    tree = statements[0]

    # Walk every node, not just the root: writes can hide in CTEs and subqueries.
    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            return _refuse(
                "statement_not_allowed",
                f"{type(node).__name__.upper()} is not allowed; this server is read-only.",
            )
        if isinstance(node, exp.Describe) and node is not tree:
            return _refuse("statement_not_allowed", "Nested EXPLAIN is not allowed.")
        if isinstance(node, exp.Into):
            return _refuse("into_clause", "SELECT ... INTO is not allowed.")
        if isinstance(node, exp.Lock):
            return _refuse("locking_clause", "Locking reads are not allowed.")
        # sqlglot parses ``@v := expr`` as PropertyEQ (belt and braces with COLON_EQ).
        if isinstance(node, exp.PropertyEQ):
            return _refuse("variable_assignment", "Variable assignment with := is not allowed.")

    kind = _kind_of(tree, first)
    if isinstance(kind, Refusal):
        return kind

    functions, unknown, denied = _scan_function_calls(tokens, tree)
    if denied:
        return _refuse(
            "denied_function",
            f"Function {denied} is not allowed (it can block, lock, wait or read files).",
            f"Remove the call to {denied}.",
        )

    flags: set[str] = set()
    if any(t.token_type is TokenType.HINT for t in tokens):
        flags.add("has_hint")
    if tree.find(exp.With) is not None:
        flags.add("has_cte")

    return Classification(
        kind=kind,
        tables=_table_refs(tree),
        functions=functions,
        unknown_functions=unknown,
        flags=frozenset(flags),
        max_execution_time_hint=hint,
    )


def _kind_of(tree: exp.Expr, first: str) -> Kind | Refusal:
    if first == "TABLE":
        return Kind.TABLE
    if isinstance(tree, exp.Select | exp.SetOperation):
        return Kind.SELECT
    if isinstance(tree, exp.Values):
        return Kind.VALUES
    if isinstance(tree, exp.Describe):
        style = tree.args.get("style")
        if style is not None and str(style).upper() == "ANALYZE":
            return _refuse(
                "explain_analyze",
                "EXPLAIN ANALYZE executes the statement and is not allowed.",
                "Use EXPLAIN or EXPLAIN FORMAT=JSON / FORMAT=TREE instead.",
            )
        if style is not None or tree.args.get("kind") not in (None, "TABLE"):
            return _refuse("statement_not_allowed", "This EXPLAIN form is not allowed.")
        target = tree.this
        if isinstance(target, exp.Table) and tree.args.get("kind") is None:
            return Kind.DESCRIBE
        if isinstance(target, exp.Select | exp.SetOperation | exp.Values | exp.Table):
            return Kind.EXPLAIN
        return _refuse(
            "statement_not_allowed",
            "EXPLAIN is only allowed for SELECT, TABLE and VALUES statements.",
        )
    return _refuse("statement_not_allowed", "Only read statements are allowed.")


def _table_refs(tree: exp.Expr) -> list[TableRef]:
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    refs: list[TableRef] = []
    for table in tree.find_all(exp.Table):
        ident = table.this
        if not isinstance(ident, exp.Identifier):  # JSON_TABLE and other table functions
            continue
        db = table.db or None
        name = table.name
        if db is None:
            if name.lower() in cte_names:
                continue
            if name.upper() == "DUAL" and not ident.quoted:
                continue
        ref = TableRef(db, name)
        if ref not in refs:
            refs.append(ref)
    return refs


def _matching_paren(tokens: list[Token], open_index: int) -> int | None:
    depth = 0
    for i in range(open_index, len(tokens)):
        if tokens[i].token_type is TokenType.L_PAREN:
            depth += 1
        elif tokens[i].token_type is TokenType.R_PAREN:
            depth -= 1
            if depth == 0:
                return i
    return None


def _is_column_list(
    tokens: list[Token], i: int, cte_names: set[str], alias_names: set[str]
) -> bool:
    """True if ``tokens[i](`` defines a CTE or derived-table column list, not a call."""
    if i == 0:
        return False
    # ``WITH c (a, b) AS (...)`` and ``(...) AS t (a, b)`` put a name before "(" without
    # calling anything; identify them by the CTE/alias names the parser found.
    name = tokens[i].text.lower()
    prev = tokens[i - 1].token_type
    if name in alias_names and prev in (TokenType.ALIAS, TokenType.R_PAREN):
        return True
    if name in cte_names and prev in (TokenType.WITH, TokenType.RECURSIVE, TokenType.COMMA):
        close = _matching_paren(tokens, i + 1)
        return (
            close is not None
            and close + 2 < len(tokens)
            and tokens[close + 1].token_type is TokenType.ALIAS
            and tokens[close + 2].token_type is TokenType.L_PAREN
        )
    return False


def _scan_function_calls(
    tokens: list[Token], tree: exp.Expr
) -> tuple[list[str], list[str], str | None]:
    """Return (builtin calls, unknown calls, first denied call)."""
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    alias_names = {alias.name.lower() for alias in tree.find_all(exp.TableAlias) if alias.columns}
    functions: list[str] = []
    unknown: list[str] = []
    for i, tok in enumerate(tokens[:-1]):
        if tokens[i + 1].token_type is not TokenType.L_PAREN:
            continue
        quoted = tok.token_type is TokenType.IDENTIFIER
        if not quoted and (tok.token_type in _STRING_TOKENS or not _NAME_RE.match(tok.text)):
            continue
        name = tok.text.upper()
        qualified = i >= 2 and tokens[i - 1].token_type is TokenType.DOT
        # Denied first and regardless of quoting or qualification: refusing is always safe.
        if name in DENIED_FUNCTIONS:
            return functions, unknown, name
        # ``db.f(`` can only be a stored function, never a builtin. A quoted name is
        # likewise never a builtin: `now`() would call a stored function named now.
        if qualified:
            full = f"{tokens[i - 2].text.upper()}.{name}"
            if full not in unknown:
                unknown.append(full)
            continue
        if not quoted and name in BUILTIN_FUNCTIONS:
            if name not in functions:
                functions.append(name)
            continue
        if not quoted and name in PAREN_KEYWORDS:
            continue
        if _is_column_list(tokens, i, cte_names, alias_names):
            continue
        if name not in unknown:
            unknown.append(name)
    return functions, unknown, None
