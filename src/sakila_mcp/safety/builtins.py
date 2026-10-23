"""Function-name lists used by the classifier.

``BUILTIN_FUNCTIONS`` is deliberately conservative: a real builtin missing from it only
causes a refusal (reported as an unknown function), never a bypass. Names are upper case.
"""

DENIED_FUNCTIONS: frozenset[str] = frozenset(
    {
        "BENCHMARK",
        "GET_LOCK",
        "LOAD_FILE",
        "MASTER_POS_WAIT",
        "RELEASE_ALL_LOCKS",
        "RELEASE_LOCK",
        "SLEEP",
        "SOURCE_POS_WAIT",
        "SYS_EVAL",
        "SYS_EXEC",
        "WAIT_FOR_EXECUTED_GTID_SET",
        "WAIT_UNTIL_SQL_THREAD_AFTER_GTIDS",
    }
)

# Keywords and type names that legitimately precede "(" without being a function call.
PAREN_KEYWORDS: frozenset[str] = frozenset(
    {
        # clauses, operators, predicates
        "AGAINST", "ALL", "AND", "ANY", "AS", "BETWEEN", "BY", "CASE", "COLUMNS",
        "DISTINCT", "DISTINCTROW", "DIV", "ELSE", "ESCAPE", "EXISTS", "FROM", "HAVING",
        "IN", "INDEX", "INTERVAL", "IS", "JOIN", "KEY", "LATERAL", "LIKE", "NOT", "OF",
        "ON", "OR", "OVER", "PARTITION", "REGEXP", "RLIKE", "SELECT", "SOME",
        "THEN", "UNION", "INTERSECT", "EXCEPT", "USING", "VALUES", "WHEN", "WHERE",
        "XOR", "EXPLAIN", "DESCRIBE", "DESC",
        # type names (CAST ... AS T(n), JSON_TABLE columns, RETURNING T(n))
        "BIT", "DATETIME", "DEC", "DECIMAL", "DOUBLE", "FLOAT", "INT", "INTEGER", "NCHAR",
        "NUMERIC", "REAL", "SIGNED", "UNSIGNED", "VARBINARY", "VARCHAR",
    }
)  # fmt: skip

BUILTIN_FUNCTIONS: frozenset[str] = frozenset(
    {
        # control flow / comparison
        "CASE", "COALESCE", "GREATEST", "IF", "IFNULL", "INTERVAL", "ISNULL", "LEAST",
        "NULLIF", "STRCMP",
        # cast / conversion / charset
        "BINARY", "CAST", "CHARSET", "COERCIBILITY", "COLLATION", "CONVERT", "ROW",
        # aggregate
        "ANY_VALUE", "AVG", "BIT_AND", "BIT_OR", "BIT_XOR", "COUNT", "GROUP_CONCAT",
        "GROUPING", "JSON_ARRAYAGG", "JSON_OBJECTAGG", "MAX", "MIN", "STD", "STDDEV",
        "STDDEV_POP", "STDDEV_SAMP", "SUM", "VAR_POP", "VAR_SAMP", "VARIANCE",
        # window
        "CUME_DIST", "DENSE_RANK", "FIRST_VALUE", "LAG", "LAST_VALUE", "LEAD",
        "NTH_VALUE", "NTILE", "PERCENT_RANK", "RANK", "ROW_NUMBER",
        # string
        "ASCII", "BIN", "BIT_LENGTH", "CHAR", "CHAR_LENGTH", "CHARACTER_LENGTH", "CONCAT",
        "CONCAT_WS", "ELT", "EXPORT_SET", "FIELD", "FIND_IN_SET", "FORMAT", "FROM_BASE64",
        "HEX", "INSERT", "INSTR", "LCASE", "LEFT", "LENGTH", "LOCATE", "LOWER", "LPAD",
        "LTRIM", "MAKE_SET", "MATCH", "MID", "OCT", "OCTET_LENGTH", "ORD", "POSITION",
        "QUOTE", "REGEXP_INSTR", "REGEXP_LIKE", "REGEXP_REPLACE", "REGEXP_SUBSTR",
        "REPEAT", "REPLACE", "REVERSE", "RIGHT", "RPAD", "RTRIM", "SOUNDEX", "SPACE",
        "SUBSTR", "SUBSTRING", "SUBSTRING_INDEX", "TO_BASE64", "TRIM", "UCASE", "UNHEX",
        "UPPER", "WEIGHT_STRING",
        # numeric
        "ABS", "ACOS", "ASIN", "ATAN", "ATAN2", "CEIL", "CEILING", "CONV", "COS", "COT",
        "CRC32", "DEGREES", "EXP", "FLOOR", "LN", "LOG", "LOG10", "LOG2", "MOD", "PI",
        "POW", "POWER", "RADIANS", "RAND", "ROUND", "SIGN", "SIN", "SQRT", "TAN",
        "TRUNCATE",
        # date / time
        "ADDDATE", "ADDTIME", "CONVERT_TZ", "CURDATE", "CURRENT_DATE", "CURRENT_TIME",
        "CURRENT_TIMESTAMP", "CURTIME", "DATE", "DATE_ADD", "DATE_FORMAT", "DATE_SUB",
        "DATEDIFF", "DAY", "DAYNAME", "DAYOFMONTH", "DAYOFWEEK", "DAYOFYEAR", "EXTRACT",
        "FROM_DAYS", "FROM_UNIXTIME", "GET_FORMAT", "HOUR", "LAST_DAY", "LOCALTIME",
        "LOCALTIMESTAMP", "MAKEDATE", "MAKETIME", "MICROSECOND", "MINUTE", "MONTH",
        "MONTHNAME", "NOW", "PERIOD_ADD", "PERIOD_DIFF", "QUARTER", "SEC_TO_TIME",
        "SECOND", "STR_TO_DATE", "SUBDATE", "SUBTIME", "SYSDATE", "TIME", "TIME_FORMAT",
        "TIME_TO_SEC", "TIMEDIFF", "TIMESTAMP", "TIMESTAMPADD", "TIMESTAMPDIFF", "TO_DAYS",
        "TO_SECONDS", "UNIX_TIMESTAMP", "UTC_DATE", "UTC_TIME", "UTC_TIMESTAMP", "WEEK",
        "WEEKDAY", "WEEKOFYEAR", "YEAR", "YEARWEEK",
        # JSON
        "JSON_ARRAY", "JSON_ARRAY_APPEND", "JSON_ARRAY_INSERT", "JSON_CONTAINS",
        "JSON_CONTAINS_PATH", "JSON_DEPTH", "JSON_EXTRACT", "JSON_INSERT", "JSON_KEYS",
        "JSON_LENGTH", "JSON_MERGE_PATCH", "JSON_MERGE_PRESERVE", "JSON_OBJECT",
        "JSON_OVERLAPS", "JSON_PRETTY", "JSON_QUOTE", "JSON_REMOVE", "JSON_REPLACE",
        "JSON_SCHEMA_VALID", "JSON_SCHEMA_VALIDATION_REPORT", "JSON_SEARCH", "JSON_SET",
        "JSON_STORAGE_FREE", "JSON_STORAGE_SIZE", "JSON_TABLE", "JSON_TYPE",
        "JSON_UNQUOTE", "JSON_VALID", "JSON_VALUE",
        # information
        "CONNECTION_ID", "CURRENT_ROLE", "CURRENT_USER",
        "DATABASE", "FOUND_ROWS", "ICU_VERSION", "LAST_INSERT_ID", "ROLES_GRAPHML",
        "ROW_COUNT", "SCHEMA", "SESSION_USER", "SYSTEM_USER", "USER", "VERSION",
        # encryption / hashing / misc
        "AES_DECRYPT", "AES_ENCRYPT", "BIN_TO_UUID", "COMPRESS", "DEFAULT", "INET6_ATON",
        "INET6_NTOA", "INET_ATON", "INET_NTOA", "IS_FREE_LOCK", "IS_IPV4",
        "IS_IPV4_COMPAT", "IS_IPV4_MAPPED", "IS_IPV6", "IS_USED_LOCK", "IS_UUID", "MD5",
        "NAME_CONST", "RANDOM_BYTES", "SHA", "SHA1", "SHA2", "STATEMENT_DIGEST",
        "STATEMENT_DIGEST_TEXT", "UNCOMPRESS", "UNCOMPRESSED_LENGTH", "UUID", "UUID_SHORT",
        "UUID_TO_BIN",
        # performance_schema / GTID helpers
        "FORMAT_BYTES", "FORMAT_PICO_TIME", "GTID_SUBSET", "GTID_SUBTRACT",
        "PS_CURRENT_THREAD_ID", "PS_THREAD_ID",
        # full-text / spatial (common subset)
        "ST_ASTEXT", "ST_ASWKT", "ST_DISTANCE", "ST_DISTANCE_SPHERE", "ST_GEOMFROMTEXT",
        "ST_SRID", "ST_X", "ST_Y", "POINT",
    }
)  # fmt: skip
