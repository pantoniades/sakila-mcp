"""Function-name lists used by the classifier.

``BUILTIN_FUNCTIONS`` is deliberately conservative: a real builtin missing from it only
causes a refusal (reported as an unknown function), never a bypass. Names are upper case.

``_HELP_TOPIC_FUNCTIONS`` was generated from ``mysql.help_topic`` on MySQL 8.4.11 Community
(run as an admin account)::

    SELECT c.name AS category, t.name AS topic
    FROM mysql.help_topic t
    JOIN mysql.help_category c USING (help_category_id)
    ORDER BY c.name, t.name;

Rules applied to the rows (help_category names are unreliable: JSON and many ST_ functions
sit under "MBR Functions", so categories are only used to pick what to read):

* Read: Aggregate, Bit, Cast, Comparison, Date and Time, Encryption, Flow Control,
  Geometry/GeometryCollection/LineString/Point/Polygon, GTID, Information, Locking, MBR
  Functions, Miscellaneous, Numeric, Performance Schema, String, Window, WKB, WKT, XML.
* Skipped categories: Internal Functions (not callable by users), Enterprise Encryption
  Functions (loadable functions from a component, not builtins), statements, data types,
  language structure, logical operators.
* A trailing " FUNCTION" / " OPERATOR" or "()" is stripped ("IF FUNCTION" -> IF).
* Topics that are not a single identifier are dropped (operators, "COUNT DISTINCT",
  "MATCH AGAINST", "MEMBER OF", "SOUNDS LIKE", ...).
* Operators/keywords that are not calls are dropped: DIV, EXISTS, IN, IS, LIKE, REGEXP,
  VALUES (PAREN_KEYWORDS covers those that can precede "(").

``_MANUAL_FUNCTIONS`` holds names carried over from the earlier hand-written list that
help_topic does not provide. Nothing 9.x-specific has been added yet.
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

_HELP_TOPIC_FUNCTIONS: frozenset[str] = frozenset(
    {
        # Aggregate Functions and Modifiers
        "AVG", "BIT_AND", "BIT_OR", "BIT_XOR", "COUNT", "GROUP_CONCAT", "JSON_ARRAYAGG",
        "JSON_OBJECTAGG", "MAX", "MIN", "STD", "STDDEV", "STDDEV_POP", "STDDEV_SAMP", "SUM",
        "VARIANCE", "VAR_POP", "VAR_SAMP",
        # Bit Functions
        "BIT_COUNT",
        # Cast Functions and Operators
        "BINARY", "CAST", "CONVERT",
        # Comparison Operators
        "COALESCE", "GREATEST", "INTERVAL", "ISNULL", "LEAST",
        # Date and Time Functions
        "ADDDATE", "ADDTIME", "CONVERT_TZ", "CURDATE", "CURRENT_DATE", "CURRENT_TIME",
        "CURRENT_TIMESTAMP", "CURTIME", "DATE", "DATEDIFF", "DATE_ADD", "DATE_FORMAT",
        "DATE_SUB", "DAY", "DAYNAME", "DAYOFMONTH", "DAYOFWEEK", "DAYOFYEAR", "EXTRACT",
        "FROM_DAYS", "FROM_UNIXTIME", "GET_FORMAT", "HOUR", "LAST_DAY", "LOCALTIME",
        "LOCALTIMESTAMP", "MAKEDATE", "MAKETIME", "MICROSECOND", "MINUTE", "MONTH",
        "MONTHNAME", "NOW", "PERIOD_ADD", "PERIOD_DIFF", "QUARTER", "SECOND", "SEC_TO_TIME",
        "STR_TO_DATE", "SUBDATE", "SUBTIME", "SYSDATE", "TIME", "TIMEDIFF", "TIMESTAMP",
        "TIMESTAMPADD", "TIMESTAMPDIFF", "TIME_FORMAT", "TIME_TO_SEC", "TO_DAYS",
        "TO_SECONDS", "UNIX_TIMESTAMP", "UTC_DATE", "UTC_TIME", "UTC_TIMESTAMP", "WEEK",
        "WEEKDAY", "WEEKOFYEAR", "YEAR", "YEARWEEK",
        # Encryption Functions
        "AES_DECRYPT", "AES_ENCRYPT", "COMPRESS", "MD5", "RANDOM_BYTES", "SHA1", "SHA2",
        "STATEMENT_DIGEST", "STATEMENT_DIGEST_TEXT", "UNCOMPRESS", "UNCOMPRESSED_LENGTH",
        "VALIDATE_PASSWORD_STRENGTH",
        # Flow Control Functions
        "CASE", "IF", "IFNULL", "NULLIF",
        # Geometry Constructors
        "GEOMCOLLECTION", "GEOMETRYCOLLECTION", "LINESTRING", "MULTILINESTRING",
        "MULTIPOINT", "MULTIPOLYGON", "POINT", "POLYGON",
        # Geometry Property / Relation, GeometryCollection, LineString, Point, Polygon
        "ST_DIMENSION", "ST_ENVELOPE", "ST_GEOMETRYTYPE", "ST_ISEMPTY", "ST_ISSIMPLE",
        "ST_SRID", "ST_CONTAINS", "ST_CROSSES", "ST_DISJOINT", "ST_DISTANCE", "ST_EQUALS",
        "ST_FRECHETDISTANCE", "ST_HAUSDORFFDISTANCE", "ST_INTERSECTS", "ST_OVERLAPS",
        "ST_TOUCHES", "ST_WITHIN", "ST_BUFFER", "ST_BUFFER_STRATEGY", "ST_CONVEXHULL",
        "ST_DIFFERENCE", "ST_GEOMETRYN", "ST_INTERSECTION", "ST_LINEINTERPOLATEPOINT",
        "ST_LINEINTERPOLATEPOINTS", "ST_NUMGEOMETRIES", "ST_POINTATDISTANCE",
        "ST_SYMDIFFERENCE", "ST_TRANSFORM", "ST_UNION", "ST_ENDPOINT", "ST_ISCLOSED",
        "ST_LENGTH", "ST_NUMPOINTS", "ST_POINTN", "ST_STARTPOINT", "ST_LATITUDE",
        "ST_LONGITUDE", "ST_X", "ST_Y", "ST_AREA", "ST_CENTROID", "ST_EXTERIORRING",
        "ST_INTERIORRINGN", "ST_NUMINTERIORRINGS",
        # GTID (MASTER_POS_WAIT, SOURCE_POS_WAIT, WAIT_FOR_EXECUTED_GTID_SET are denied)
        "GTID_SUBSET", "GTID_SUBTRACT", "MASTER_POS_WAIT", "SOURCE_POS_WAIT",
        "WAIT_FOR_EXECUTED_GTID_SET",
        # Information Functions (BENCHMARK is denied)
        "BENCHMARK", "CHARSET", "COERCIBILITY", "COLLATION", "CONNECTION_ID", "CURRENT_ROLE",
        "CURRENT_USER", "DATABASE", "FOUND_ROWS", "ICU_VERSION", "LAST_INSERT_ID",
        "ROLES_GRAPHML", "ROW_COUNT", "SCHEMA", "SESSION_USER", "SYSTEM_USER", "USER",
        "VERSION",
        # Locking Functions (GET_LOCK, RELEASE_* are denied)
        "GET_LOCK", "IS_FREE_LOCK", "IS_USED_LOCK", "RELEASE_ALL_LOCKS", "RELEASE_LOCK",
        # "MBR Functions" (JSON, MBR, GeoJSON, geohash)
        "JSON_ARRAY", "JSON_ARRAY_APPEND", "JSON_ARRAY_INSERT", "JSON_CONTAINS",
        "JSON_CONTAINS_PATH", "JSON_DEPTH", "JSON_EXTRACT", "JSON_INSERT", "JSON_KEYS",
        "JSON_LENGTH", "JSON_MERGE", "JSON_MERGE_PATCH", "JSON_MERGE_PRESERVE",
        "JSON_OBJECT", "JSON_OVERLAPS", "JSON_PRETTY", "JSON_QUOTE", "JSON_REMOVE",
        "JSON_REPLACE", "JSON_SCHEMA_VALID", "JSON_SCHEMA_VALIDATION_REPORT", "JSON_SEARCH",
        "JSON_SET", "JSON_STORAGE_FREE", "JSON_STORAGE_SIZE", "JSON_TABLE", "JSON_TYPE",
        "JSON_UNQUOTE", "JSON_VALID", "JSON_VALUE", "MBRCONTAINS", "MBRCOVEREDBY",
        "MBRCOVERS", "MBRDISJOINT", "MBREQUALS", "MBRINTERSECTS", "MBROVERLAPS",
        "MBRTOUCHES", "MBRWITHIN", "ST_ASGEOJSON", "ST_COLLECT", "ST_DISTANCE_SPHERE",
        "ST_GEOHASH", "ST_GEOMFROMGEOJSON", "ST_ISVALID", "ST_LATFROMGEOHASH",
        "ST_LONGFROMGEOHASH", "ST_MAKEENVELOPE", "ST_POINTFROMGEOHASH", "ST_SIMPLIFY",
        "ST_VALIDATE",
        # Miscellaneous Functions (SLEEP is denied)
        "ANY_VALUE", "BIN_TO_UUID", "DEFAULT", "GROUPING", "INET6_ATON", "INET6_NTOA",
        "INET_ATON", "INET_NTOA", "IS_IPV4", "IS_IPV4_COMPAT", "IS_IPV4_MAPPED", "IS_IPV6",
        "IS_UUID", "NAME_CONST", "SLEEP", "UUID", "UUID_SHORT", "UUID_TO_BIN",
        # Numeric Functions
        "ABS", "ACOS", "ASIN", "ATAN", "ATAN2", "CEIL", "CEILING", "CONV", "COS", "COT",
        "CRC32", "DEGREES", "EXP", "FLOOR", "LN", "LOG", "LOG10", "LOG2", "MOD", "PI", "POW",
        "POWER", "RADIANS", "RAND", "ROUND", "SIGN", "SIN", "SQRT", "TAN", "TRUNCATE",
        # Performance Schema Functions
        "FORMAT_BYTES", "FORMAT_PICO_TIME", "PS_CURRENT_THREAD_ID", "PS_THREAD_ID",
        # String Functions (LOAD_FILE is denied)
        "ASCII", "BIN", "BIT_LENGTH", "CHAR", "CHARACTER_LENGTH", "CHAR_LENGTH", "CONCAT",
        "CONCAT_WS", "ELT", "EXPORT_SET", "FIELD", "FIND_IN_SET", "FORMAT", "FROM_BASE64",
        "HEX", "INSERT", "INSTR", "LCASE", "LEFT", "LENGTH", "LOAD_FILE", "LOCATE", "LOWER",
        "LPAD", "LTRIM", "MAKE_SET", "MID", "OCT", "OCTET_LENGTH", "ORD", "POSITION",
        "QUOTE", "REGEXP_INSTR", "REGEXP_LIKE", "REGEXP_REPLACE", "REGEXP_SUBSTR", "REPEAT",
        "REPLACE", "REVERSE", "RIGHT", "RPAD", "RTRIM", "SOUNDEX", "SPACE", "STRCMP",
        "SUBSTR", "SUBSTRING", "SUBSTRING_INDEX", "TO_BASE64", "TRIM", "UCASE", "UNHEX",
        "UPPER", "WEIGHT_STRING",
        # Window Functions
        "CUME_DIST", "DENSE_RANK", "FIRST_VALUE", "LAG", "LAST_VALUE", "LEAD", "NTH_VALUE",
        "NTILE", "PERCENT_RANK", "RANK", "ROW_NUMBER",
        # WKB Functions
        "ST_ASBINARY", "ST_ASTEXT", "ST_GEOMCOLLFROMWKB", "ST_GEOMFROMWKB",
        "ST_LINEFROMWKB", "ST_MLINEFROMWKB", "ST_MPOINTFROMWKB", "ST_MPOLYFROMWKB",
        "ST_POINTFROMWKB", "ST_POLYFROMWKB", "ST_SWAPXY",
        # WKT Functions
        "ST_GEOMCOLLFROMTEXT", "ST_GEOMFROMTEXT", "ST_LINEFROMTEXT", "ST_MLINEFROMTEXT",
        "ST_MPOINTFROMTEXT", "ST_MPOLYFROMTEXT", "ST_POINTFROMTEXT", "ST_POLYFROMTEXT",
        # XML
        "EXTRACTVALUE", "UPDATEXML",
    }
)  # fmt: skip

# Carried over from the earlier hand-written list; help_topic has no single-name entry.
_MANUAL_FUNCTIONS: frozenset[str] = frozenset({"MATCH", "ROW", "SHA", "ST_ASWKT"})

BUILTIN_FUNCTIONS: frozenset[str] = (_HELP_TOPIC_FUNCTIONS | _MANUAL_FUNCTIONS) - DENIED_FUNCTIONS
