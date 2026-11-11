"""Classifier corpus. Every case states the expected outcome explicitly."""

import pytest

from sakila_mcp.models.common import Refusal
from sakila_mcp.safety.classifier import Classification, Kind, TableRef, classify


def _accepted(sql: str) -> Classification:
    result = classify(sql)
    assert isinstance(result, Classification), f"expected accept, got {result!r}"
    return result


def _refused(sql: str) -> Refusal:
    result = classify(sql)
    assert isinstance(result, Refusal), f"expected refusal, got {result!r}"
    assert result.reason
    assert result.suggestion
    return result


# --------------------------------------------------------------------------- accept


@pytest.mark.parametrize(
    ("sql", "kind", "tables"),
    [
        ("SELECT 1", Kind.SELECT, []),
        (
            "WITH a AS (SELECT 1 AS x), b AS (SELECT x FROM a) "
            "SELECT * FROM b JOIN sakila.film f ON f.film_id = b.x",
            Kind.SELECT,
            [TableRef("sakila", "film")],
        ),
        (
            "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 3) "
            "SELECT * FROM r",
            Kind.SELECT,
            [],
        ),
        (
            "SELECT a FROM t1 UNION SELECT a FROM db2.t2",
            Kind.SELECT,
            [TableRef(None, "t1"), TableRef("db2", "t2")],
        ),
        ("(SELECT 1) UNION ALL (SELECT 2)", Kind.SELECT, []),
        (
            "SELECT film_id, ROW_NUMBER() OVER (PARTITION BY rating ORDER BY length) FROM film",
            Kind.SELECT,
            [TableRef(None, "film")],
        ),
        (
            "SELECT SUM(amount) OVER w FROM payment WINDOW w AS (ORDER BY payment_date)",
            Kind.SELECT,
            [TableRef(None, "payment")],
        ),
        (
            "SELECT jt.x FROM JSON_TABLE('[1,2]', '$[*]' COLUMNS (x INT PATH '$')) AS jt",
            Kind.SELECT,
            [],
        ),
        (
            "SELECT d.a FROM (SELECT film_id FROM sakila.film) AS d(a)",
            Kind.SELECT,
            [TableRef("sakila", "film")],
        ),
        (
            "SELECT (SELECT COUNT(*) FROM s.x) FROM (SELECT * FROM y) d",
            Kind.SELECT,
            [TableRef("s", "x"), TableRef(None, "y")],
        ),
        ("SELECT `title` FROM `sakila`.`film`", Kind.SELECT, [TableRef("sakila", "film")]),
        ("SELECT 1 FROM DUAL", Kind.SELECT, []),
        ("TABLE t", Kind.TABLE, [TableRef(None, "t")]),
        (
            "TABLE sakila.film ORDER BY film_id LIMIT 5",
            Kind.TABLE,
            [TableRef("sakila", "film")],
        ),
        ("VALUES ROW(1, 'a'), ROW(2, 'b')", Kind.VALUES, []),
        (
            "SELECT * FROM information_schema.TABLES",
            Kind.SELECT,
            [TableRef("information_schema", "TABLES")],
        ),
    ],
)
def test_accept_select_family(sql: str, kind: Kind, tables: list[TableRef]) -> None:
    result = _accepted(sql)
    assert result.kind is kind
    assert result.tables == tables


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1;",
        "SELECT 1;\n\n",
        "SELECT 1 ;  ",
        "sElEcT 1",
        "\n\nSELECT 1",
        "\tSELECT\t1\t",
        "SELECT 1\n\n",
        "SELECT 1 -- ; DROP TABLE t",
        "SELECT 1 # ; DROP TABLE t",
        "SELECT 1 /* ; DROP TABLE t */",
        "/* leading comment */ SELECT 1",
        "SELECT ';'",
        "SELECT 'SLEEP(1); DROP TABLE t'",
        'SELECT "x;y"',
        "SELECT 1 --x\n",
    ],
)
def test_accept_whitespace_comment_and_literal_variants(sql: str) -> None:
    assert _accepted(sql).kind is Kind.SELECT


def test_optimizer_hint_allowed_and_reported() -> None:
    result = _accepted("SELECT /*+ MAX_EXECUTION_TIME(1) */ 1")
    assert result.max_execution_time_hint == 1
    assert "has_hint" in result.flags


def test_other_optimizer_hints_allowed() -> None:
    result = _accepted("SELECT /*+ BKA(t1) NO_RANGE_OPTIMIZATION(t1 PRIMARY) */ a FROM t1")
    assert result.max_execution_time_hint is None
    assert result.unknown_functions == []


def test_builtin_functions_recorded() -> None:
    result = _accepted(
        "SELECT IFNULL(a, 0), COALESCE(a, b), NOW(), CAST(a AS CHAR(10)), "
        "UUID_TO_BIN(UUID()), JSON_EXTRACT(j, '$.x'), j->>'$.y', "
        "GROUP_CONCAT(a ORDER BY a SEPARATOR ','), CASE WHEN a THEN 1 END, "
        "CURRENT_TIMESTAMP, DATE_ADD(d, INTERVAL 1 DAY), a IN (1, 2) "
        "FROM t PARTITION (p0) FORCE INDEX FOR JOIN (idx_a) "
        "WHERE a MEMBER OF ('[1]') AND JSON_VALUE(j, '$.a' RETURNING DECIMAL(10,2)) > 0 "
        "AND EXISTS (SELECT 1) AND (a, b) NOT IN (SELECT 1, 2)"
    )
    assert result.unknown_functions == []
    for name in ("IFNULL", "COALESCE", "NOW", "CAST", "UUID_TO_BIN", "UUID", "GROUP_CONCAT"):
        assert name in result.functions


def test_vector_functions_are_builtin() -> None:
    result = _accepted(
        "SELECT VECTOR_DIM(v), VECTOR_TO_STRING(v), "
        "DISTANCE(v, STRING_TO_VECTOR('[1,2]'), 'COSINE') FROM t"
    )
    assert result.unknown_functions == []


@pytest.mark.parametrize(
    ("sql", "kind", "tables"),
    [
        ("EXPLAIN SELECT 1", Kind.EXPLAIN, []),
        (
            "EXPLAIN FORMAT=JSON SELECT * FROM sakila.film WHERE film_id = 1",
            Kind.EXPLAIN,
            [TableRef("sakila", "film")],
        ),
        ("EXPLAIN FORMAT=TREE SELECT * FROM film", Kind.EXPLAIN, [TableRef(None, "film")]),
        ("EXPLAIN SELECT 1 UNION SELECT 2", Kind.EXPLAIN, []),
        ("DESCRIBE sakila.film", Kind.DESCRIBE, [TableRef("sakila", "film")]),
        ("DESC film", Kind.DESCRIBE, [TableRef(None, "film")]),
        ("EXPLAIN film", Kind.DESCRIBE, [TableRef(None, "film")]),
    ],
)
def test_accept_explain_describe(sql: str, kind: Kind, tables: list[TableRef]) -> None:
    result = _accepted(sql)
    assert result.kind is kind
    assert result.tables == tables


@pytest.mark.parametrize(
    ("sql", "tables", "databases"),
    [
        ("SHOW DATABASES", [], []),
        ("SHOW TABLES", [], [None]),
        ("SHOW FULL TABLES FROM sakila LIKE 'f%'", [], ["sakila"]),
        ("SHOW TABLE STATUS IN sakila", [], ["sakila"]),
        ("SHOW COLUMNS FROM film FROM sakila", [TableRef("sakila", "film")], []),
        ("SHOW FULL COLUMNS FROM sakila.film LIKE 'f%'", [TableRef("sakila", "film")], []),
        ("SHOW FULL FIELDS IN `film`", [TableRef(None, "film")], []),
        ("SHOW INDEX FROM film", [TableRef(None, "film")], []),
        ("SHOW INDEXES FROM sakila.film", [TableRef("sakila", "film")], []),
        ("SHOW KEYS IN film IN sakila", [TableRef("sakila", "film")], []),
        ("SHOW CREATE TABLE sakila.film", [TableRef("sakila", "film")], []),
        ("SHOW CREATE VIEW film_list", [TableRef(None, "film_list")], []),
        ("SHOW VARIABLES LIKE 'innodb%'", [], []),
        ("SHOW GLOBAL VARIABLES", [], []),
        ("SHOW SESSION VARIABLES", [], []),
        ("SHOW STATUS", [], []),
        ("SHOW GLOBAL STATUS LIKE 'Threads%'", [], []),
        ("SHOW ENGINE INNODB STATUS", [], []),
        ("SHOW PROCESSLIST", [], []),
        ("SHOW FULL PROCESSLIST", [], []),
        ("SHOW REPLICA STATUS", [], []),
        ("SHOW REPLICA STATUS FOR CHANNEL ch1", [], []),
        ("SHOW BINARY LOG STATUS", [], []),
        ("SHOW MASTER STATUS", [], []),
        ("SHOW WARNINGS", [], []),
        ("SHOW ERRORS", [], []),
        ("SHOW PLUGINS", [], []),
        ("SHOW ENGINES", [], []),
        ("SHOW CHARSET", [], []),
        ("SHOW CHARACTER SET LIKE 'utf8%'", [], []),
        ("SHOW COLLATION LIKE 'utf8mb4%'", [], []),
        ("show tables;", [], [None]),
    ],
)
def test_accept_show(sql: str, tables: list[TableRef], databases: list[str | None]) -> None:
    result = _accepted(sql)
    assert result.kind is Kind.SHOW
    assert result.tables == tables
    assert result.databases == databases


# --------------------------------------------------------------------------- refuse


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        # INTO in any position
        ("SELECT * FROM t INTO OUTFILE '/tmp/x'", "into_clause"),
        ("SELECT a FROM t INTO DUMPFILE '/tmp/x'", "into_clause"),
        ("SELECT a FROM t INTO @a", "into_clause"),
        ("SELECT a INTO @a FROM t", "into_clause"),
        ("SELECT * FROM t UNION SELECT * FROM u INTO @x", "into_clause"),
        ("EXPLAIN FORMAT=JSON INTO @v SELECT 1", "into_clause"),
        ("TABLE t INTO @x", "into_clause"),
        # locking reads
        ("SELECT * FROM t FOR UPDATE", "locking_clause"),
        ("SELECT * FROM t WHERE a = 1 FOR UPDATE NOWAIT", "locking_clause"),
        ("SELECT * FROM t FOR SHARE", "locking_clause"),
        ("SELECT * FROM t LOCK IN SHARE MODE", "locking_clause"),
        ("select * from t for update skip locked", "locking_clause"),
        ("(SELECT 1) FOR UPDATE", "locking_clause"),
        ("SELECT 1 UNION (SELECT 2 FOR UPDATE)", "locking_clause"),
        # assignment
        ("SELECT @a := 1", "variable_assignment"),
        ("SELECT a FROM t WHERE (@x := a) > 0", "variable_assignment"),
        # multi-statement
        ("SELECT 1; DROP TABLE t", "multi_statement"),
        ("SELECT ';' ; DROP TABLE t", "multi_statement"),
        ("SELECT 1;;", "multi_statement"),
        ("SELECT 1; SELECT 2", "multi_statement"),
        ("SELECT 1; -- trailing comment", "multi_statement"),
        ("SELECT 1 --; DROP TABLE t", "multi_statement"),
        # version comments, anywhere
        ("SELECT /*!50000 1 */", "version_comment"),
        ("SELECT 1 /*!80000 , SLEEP(5) */", "version_comment"),
        ("/*!*/ SELECT 1", "version_comment"),
        ("SELECT '/*!'", "version_comment"),
        # statement types
        ("/* comment */ DELETE FROM t", "statement_not_allowed"),
        ("WITH x AS (SELECT 1) DELETE FROM t", "statement_not_allowed"),
        ("WITH x AS (DELETE FROM t RETURNING a) SELECT 1", "statement_not_allowed"),
        ("CALL p()", "statement_not_allowed"),
        ("DO SLEEP(1)", "statement_not_allowed"),
        ("SET @a = 1", "statement_not_allowed"),
        ("HANDLER t OPEN", "statement_not_allowed"),
        ("LOCK TABLES t READ", "statement_not_allowed"),
        ("INSERT INTO t VALUES (1)", "statement_not_allowed"),
        ("UPDATE t SET a = 1", "statement_not_allowed"),
        ("DROP TABLE t", "statement_not_allowed"),
        ("CREATE TABLE t (a INT)", "statement_not_allowed"),
        ("USE sakila", "statement_not_allowed"),
        ("KILL 5", "statement_not_allowed"),
        ("ANALYZE TABLE t", "statement_not_allowed"),
        ("EXPLAIN DELETE FROM t", "statement_not_allowed"),
        ("EXPLAIN UPDATE t SET a = 1", "statement_not_allowed"),
        # denied functions
        ("SELECT SLEEP(10)", "denied_function"),
        ("select sleep(10)", "denied_function"),
        ("SELECT SLEEP (10)", "denied_function"),
        ("SELECT SLEEP/**/(10)", "denied_function"),
        ("SELECT `sleep`(10)", "denied_function"),
        ("SELECT BENCHMARK(1e9, MD5('x'))", "denied_function"),
        ("SELECT GET_LOCK('a', 10)", "denied_function"),
        ("SELECT RELEASE_LOCK('a')", "denied_function"),
        ("SELECT RELEASE_ALL_LOCKS()", "denied_function"),
        ("SELECT LOAD_FILE('/etc/passwd')", "denied_function"),
        ("SELECT MASTER_POS_WAIT('f', 4)", "denied_function"),
        ("SELECT SOURCE_POS_WAIT('f', 4)", "denied_function"),
        ("SELECT WAIT_FOR_EXECUTED_GTID_SET('x')", "denied_function"),
        ("SELECT WAIT_UNTIL_SQL_THREAD_AFTER_GTIDS('x')", "denied_function"),
        ("SELECT SYS_EVAL('id')", "denied_function"),
        ("SELECT SYS_EXEC('id')", "denied_function"),
        ("SELECT a FROM t WHERE b IN (SELECT SLEEP(1))", "denied_function"),
        ("VALUES ROW(1, SLEEP(1))", "denied_function"),
        ("WITH sleep(a) AS (SELECT 1) SELECT SLEEP(1)", "denied_function"),
        ("EXPLAIN SELECT SLEEP(1)", "denied_function"),
        # hints that override the session timeout or session variables
        ("SELECT /*+ SET_VAR(max_execution_time=0) */ 1", "set_var_hint"),
        ("SELECT /*+ set_var(sort_buffer_size = 16M) */ 1", "set_var_hint"),
        ("SELECT (SELECT /*+ SET_VAR(x=1) */ 1)", "set_var_hint"),
        ("SELECT /*+ MAX_EXECUTION_TIME(abc) */ 1", "invalid_hint"),
        # EXPLAIN variants
        ("EXPLAIN ANALYZE SELECT 1", "explain_analyze"),
        ("explain analyze select * from t", "explain_analyze"),
        # SHOW outside the sub-allowlist
        ("SHOW GRANTS", "show_not_allowed"),
        ("SHOW GRANTS FOR root", "show_not_allowed"),
        ("SHOW CREATE USER root", "show_not_allowed"),
        ("SHOW CREATE PROCEDURE p", "show_not_allowed"),
        ("SHOW CREATE FUNCTION f", "show_not_allowed"),
        ("SHOW BINLOG EVENTS", "show_not_allowed"),
        ("SHOW TABLES FROM", "show_not_allowed"),
        ("SHOW GLOBAL PROCESSLIST", "show_not_allowed"),
        ("SHOW TABLES WHERE SLEEP(1)", "show_where"),
        ("SHOW DATABASES WHERE `Database` = 'x'", "show_where"),
        # parse failures fail closed
        ("SELECT FROM WHERE (", "parse_error"),
        ("SELECT 'unterminated", "parse_error"),
        ("SELECT 1 -- ;\nDROP TABLE t", "parse_error"),
        ("EXPLAIN FOR CONNECTION 5", "parse_error"),
        # empty input
        ("", "empty"),
        ("   \n\t", "empty"),
        ("-- only a comment", "empty"),
        ("/* only */", "empty"),
    ],
)
def test_refuse(sql: str, code: str) -> None:
    assert _refused(sql).code == code


def test_parse_error_reason_mentions_parse() -> None:
    refusal = _refused("SELECT FROM WHERE (")
    assert "parse" in refusal.reason.lower()


def test_denied_function_named_in_reason() -> None:
    assert "SLEEP" in _refused("SELECT sleep(1)").reason


# --------------------------------------------------------------------------- unknown functions


@pytest.mark.parametrize(
    ("sql", "unknown"),
    [
        ("SELECT my_func(1)", ["MY_FUNC"]),
        ("SELECT sakila.my_func(1)", ["SAKILA.MY_FUNC"]),
        ("SELECT sakila.NOW()", ["SAKILA.NOW"]),
        ("SELECT NVL(a, b) FROM t", ["NVL"]),
        ("SELECT STRING_AGG(a, ',') FROM t", ["STRING_AGG"]),
        ("SELECT `my func`(1)", ["MY FUNC"]),
        ("SELECT `NOW`()", ["NOW"]),
        ("WITH my_func(a) AS (SELECT 1) SELECT my_func(1) FROM my_func", ["MY_FUNC"]),
        ("SELECT d.a FROM (SELECT 1) AS d(a) WHERE d(1)", ["D"]),
    ],
)
def test_unknown_functions_reported(sql: str, unknown: list[str]) -> None:
    assert _accepted(sql).unknown_functions == unknown


def test_column_lists_are_not_function_calls() -> None:
    result = _accepted(
        "WITH a(x) AS (SELECT 1), b(y) AS (SELECT x FROM a) "
        "SELECT * FROM b, (SELECT 2) AS d(z), (SELECT 3) e(w)"
    )
    assert result.unknown_functions == []
    assert result.tables == []
