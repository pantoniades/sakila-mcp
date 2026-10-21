"""Entry point: ``python -m mysql_mcp [--config path.toml]`` serves MCP over stdio."""

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from mysql_mcp import __version__
from mysql_mcp.config import Config, load_config
from mysql_mcp.pool import Database, DatabaseError
from mysql_mcp.safety.redact import RedactingFilter, redact_exception, register_secret
from mysql_mcp.server import build_server

log = logging.getLogger("mysql_mcp")


def _configure_logging() -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    # sqlglot warns (echoing the SQL) whenever it falls back to a Command node; the
    # classifier already refuses those, so the warning is noise.
    logging.getLogger("sqlglot").setLevel(logging.ERROR)
    # The connector logs auth-plugin selection at INFO on every connect.
    logging.getLogger("mysql.connector").setLevel(logging.WARNING)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mysql-mcp",
        description="Read-only MySQL MCP server (stdio transport). "
        "Configure with MYSQL_MCP_* environment variables and/or a TOML file.",
    )
    parser.add_argument("--config", type=Path, metavar="PATH", help="optional TOML config file")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def _load(path: Path | None) -> Config:
    try:
        config = load_config(path)
    except ValidationError as exc:
        # Never echo input values: a misspelled key could carry the password.
        for error in exc.errors(include_input=False, include_url=False):
            field = ".".join(str(part) for part in error["loc"]) or "<config>"
            log.error("invalid config %s: %s", field, error["msg"])
        raise SystemExit(2) from None
    except Exception as exc:
        log.error("cannot load config: %s", redact_exception(exc))
        raise SystemExit(2) from None
    register_secret(config.password.get_secret_value())
    return config


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    _configure_logging()
    config = _load(args.config)
    database = Database(config)
    try:
        database.open()
    except DatabaseError as exc:
        log.error("%s", exc)
        raise SystemExit(1) from None
    server = build_server(config, database)
    log.info("mysql-mcp %s starting on stdio", __version__)
    try:
        server.run("stdio")
    finally:
        database.close()


if __name__ == "__main__":
    main()
