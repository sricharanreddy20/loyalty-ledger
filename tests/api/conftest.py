"""API tests run against a separate database, `loyalty_test`, on the same PostgreSQL server.
It is recreated and loaded with the starter export once per session, so the development
database is never touched. All API tests are skipped if PostgreSQL is not reachable."""
import os

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from generator.config import DEFAULT_SEED
from generator.export import export
from generator.generate import build
from generator.load import database_url, load

TEST_DB = "loyalty_test"
WRITE_DAY = "2026-12-31"          # every API write in the tests happens late on 31 Dec


@pytest.fixture(scope="session")
def test_db_url(tmp_path_factory):
    base = database_url()
    try:
        admin = psycopg.connect(base, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL is not reachable (start it with: docker compose up -d db)")
    with admin:
        admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {TEST_DB}")
    url = make_conninfo(base, dbname=TEST_DB)
    out = tmp_path_factory.mktemp("starter")
    ds, ground_truth = build(DEFAULT_SEED, "starter")
    export(ds, ground_truth, out, DEFAULT_SEED, "starter")
    load(out, url)
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url       # the backend reads it on every request
    yield url
    if previous is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = previous


@pytest.fixture(scope="session")
def client(test_db_url):
    from fastapi.testclient import TestClient
    from backend.app.main import app
    return TestClient(app)


@pytest.fixture(scope="session")
def sql(test_db_url):
    """sql(query, params) -> list of dict rows, on its own autocommit connection."""
    conn = psycopg.connect(test_db_url, autocommit=True, row_factory=dict_row)

    def run(query, params=()):
        cur = conn.execute(query, params)
        return cur.fetchall() if cur.description else []
    yield run
    conn.close()


@pytest.fixture(scope="session")
def pick(sql):
    """pick(query, params) -> the first loy_id from the query not used by another test yet."""
    used = set()

    def choose(query, params=()):
        for row in sql(query, params):
            if row["loy_id"] not in used:
                used.add(row["loy_id"])
                return row["loy_id"]
        raise AssertionError("no suitable account in the starter data")
    return choose


def ledger_sum(sql, loy_id) -> int:
    return sql("SELECT COALESCE(SUM(points), 0) AS s FROM points_transaction WHERE loy_id = %s", (loy_id,))[0]["s"]
