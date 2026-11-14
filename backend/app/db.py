"""Database access: one connection and one transaction per request."""
import os
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[2]


def database_url() -> str:
    """DATABASE_URL from the environment (Docker), else from the project's .env (local dev)."""
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key == "DATABASE_URL":
                return value.strip()
    raise RuntimeError("DATABASE_URL is not set")


@contextmanager
def transaction():
    """Commit when the block ends normally; roll back if anything raises."""
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        yield conn
