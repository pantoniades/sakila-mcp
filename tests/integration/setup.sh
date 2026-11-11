#!/usr/bin/env bash
# Prepare a MySQL server for the integration tests. Run once per server, as an admin:
#   SAKILA_MCP_TEST_CLIENT_HOST=127.0.0.1 SAKILA_MCP_TEST_PASSWORD=abc123 \
#     tests/integration/setup.sh -u root -p
# Arguments are passed to the mysql client (host, port, user, ...). Run it in a real
# terminal so that -p can prompt, or use --login-path instead of -p.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
host=${SAKILA_MCP_TEST_CLIENT_HOST:-}
password=${SAKILA_MCP_TEST_PASSWORD:-}

if [[ -z $host || -z $password ]]; then
  cat >&2 <<'USAGE'
Set both variables, then pass mysql client arguments for an admin login:

  SAKILA_MCP_TEST_CLIENT_HOST  address the server sees the test client connect from
                               (127.0.0.1 for a local server)
  SAKILA_MCP_TEST_PASSWORD     password for the sakila_mcp_test account (letters and
                               digits only, so the DSN needs no URL escaping)

  SAKILA_MCP_TEST_CLIENT_HOST=127.0.0.1 SAKILA_MCP_TEST_PASSWORD=abc123 \
    tests/integration/setup.sh -u root -p

Afterwards put the DSN in .env.test or SAKILA_MCP_TEST_DSN:
  mysql://sakila_mcp_test:<password>@<host>:3306/sakila_mcp_test
USAGE
  exit 2
fi

# Both values are spliced into a SET statement below, so only plain characters pass.
[[ $host =~ ^[A-Za-z0-9._:%-]+$ ]] || { echo "invalid SAKILA_MCP_TEST_CLIENT_HOST" >&2; exit 2; }
[[ $password =~ ^[A-Za-z0-9]+$ ]] || { echo "SAKILA_MCP_TEST_PASSWORD must be letters and digits only" >&2; exit 2; }

# SQL goes on stdin, so the password never appears in the process list. mysql -p then
# prompts on the terminal (/dev/tty); without one (CI, an agent shell) it would read the
# password from this stream instead, so use --login-path there.
{
  printf "SET @client_host = '%s', @test_password = '%s';\n" "$host" "$password"
  cat "$here/setup.sql"
} | mysql "$@"
