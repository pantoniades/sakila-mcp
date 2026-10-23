-- mysql_mcp integration-test fixture. Run once as an administrative account:
--   mysql -u root -p < tests/integration/setup.sql
-- Replace CHANGE_ME below first (alphanumeric keeps the DSN free of URL escaping).
-- Teardown is at the bottom, commented out.

CREATE DATABASE IF NOT EXISTS mysql_mcp_test
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
-- Second schema that the tests leave OUT of the allowlist.
CREATE DATABASE IF NOT EXISTS mysql_mcp_other
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

USE mysql_mcp_test;

CREATE TABLE numbers (
  n INT UNSIGNED NOT NULL PRIMARY KEY
) ENGINE=InnoDB;

CREATE TABLE customer (
  id         INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  email      VARCHAR(255) NOT NULL,
  name       VARCHAR(100) NOT NULL,
  created_at DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  profile    JSON NULL,
  UNIQUE KEY uk_email (email),
  KEY idx_name_created (name, created_at)
) ENGINE=InnoDB;

CREATE TABLE orders (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  customer_id INT UNSIGNED NOT NULL,
  amount      DECIMAL(10,2) NOT NULL,
  status      ENUM('new','paid','shipped') NOT NULL DEFAULT 'new',
  note        TEXT NULL,
  KEY idx_customer_status (customer_id, status),
  CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id) REFERENCES customer (id)
) ENGINE=InnoDB;

-- Binary and oversized cells for summarization / truncation tests.
CREATE TABLE blobs (
  id       INT NOT NULL PRIMARY KEY,
  payload  BLOB NULL,
  raw_bin  VARBINARY(16) NULL,
  big_text LONGTEXT NULL
) ENGINE=InnoDB;

-- VECTOR needs 9.0+; on older servers this table does not exist and tests skip.
/*!90000 CREATE TABLE embeddings (
  id INT NOT NULL PRIMARY KEY,
  v  VECTOR(3) NOT NULL
) ENGINE=InnoDB */;

-- The test account gets INSERT on this table only, so the read-only-session test
-- fails with 1792 (read-only transaction) rather than a privilege error.
CREATE TABLE write_probe (
  id INT NOT NULL PRIMARY KEY
) ENGINE=InnoDB;

CREATE VIEW customer_order_totals AS
  SELECT c.id AS customer_id, c.email, COUNT(o.id) AS order_count, SUM(o.amount) AS total
  FROM customer c LEFT JOIN orders o ON o.customer_id = c.id
  GROUP BY c.id, c.email;

INSERT INTO numbers (n)
  WITH RECURSIVE seq (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM seq WHERE n < 1000)
  SELECT n FROM seq;

INSERT INTO customer (email, name, profile)
  SELECT CONCAT('user', n, '@example.com'), CONCAT('User ', n),
         JSON_OBJECT('tier', ELT(1 + n % 3, 'free', 'pro', 'team'), 'n', n)
  FROM numbers WHERE n <= 500;

INSERT INTO orders (customer_id, amount, status, note)
  SELECT 1 + (n % 500), ROUND(n * 1.37, 2), ELT(1 + n % 3, 'new', 'paid', 'shipped'),
         IF(n % 10 = 0, CONCAT('note ', n), NULL)
  FROM numbers;

INSERT INTO blobs (id, payload, raw_bin, big_text) VALUES
  (1, UNHEX(REPEAT('DEADBEEF', 256)), UNHEX('00FF00FF00FF00FF'), REPEAT('x', 10000)),
  (2, NULL, NULL, 'short');

/*!90000 INSERT INTO embeddings (id, v) VALUES
  (1, STRING_TO_VECTOR('[1.0, 2.0, 3.0]')),
  (2, STRING_TO_VECTOR('[0.5, 0.25, 0.125]')) */;

USE mysql_mcp_other;

CREATE TABLE secret (
  id  INT NOT NULL PRIMARY KEY,
  val VARCHAR(50) NOT NULL
) ENGINE=InnoDB;

INSERT INTO secret VALUES (1, 'must not be readable through the server');

-- Test account: TCP to 127.0.0.1, caching_sha2_password, grants as recommended for
-- production plus the single INSERT grant explained above.
CREATE USER IF NOT EXISTS 'mysql_mcp_test'@'127.0.0.1'
  IDENTIFIED WITH caching_sha2_password BY 'CHANGE_ME';
GRANT SELECT, SHOW VIEW ON mysql_mcp_test.*  TO 'mysql_mcp_test'@'127.0.0.1';
GRANT SELECT, SHOW VIEW ON mysql_mcp_other.* TO 'mysql_mcp_test'@'127.0.0.1';
GRANT PROCESS, REPLICATION CLIENT ON *.*     TO 'mysql_mcp_test'@'127.0.0.1';
GRANT INSERT ON mysql_mcp_test.write_probe   TO 'mysql_mcp_test'@'127.0.0.1';

-- Teardown:
-- DROP USER IF EXISTS 'mysql_mcp_test'@'127.0.0.1';
-- DROP DATABASE IF EXISTS mysql_mcp_test;
-- DROP DATABASE IF EXISTS mysql_mcp_other;
