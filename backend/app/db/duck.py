from collections.abc import Iterator
from contextlib import contextmanager

import duckdb

from app.core.settings import get_settings

SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS sources (
    id              VARCHAR PRIMARY KEY,
    filename        VARCHAR NOT NULL,
    kind            VARCHAR NOT NULL,
    sheet           VARCHAR,
    landed_path     VARCHAR NOT NULL,
    row_count       BIGINT  NOT NULL,
    column_count    INTEGER NOT NULL,
    fingerprint     VARCHAR NOT NULL,
    sniff           JSON,
    created_at      TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS column_profiles (
    source_id       VARCHAR NOT NULL,
    column_name     VARCHAR NOT NULL,
    ordinal         INTEGER NOT NULL,
    profile         JSON    NOT NULL,
    created_at      TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (source_id, column_name)
);

CREATE TABLE IF NOT EXISTS mapping_specs (
    id              VARCHAR PRIMARY KEY,
    source_id       VARCHAR NOT NULL,
    target_schema   VARCHAR NOT NULL,
    target_version  INTEGER NOT NULL,
    version         INTEGER NOT NULL,
    parent_id       VARCHAR,
    content_hash    VARCHAR NOT NULL,
    spec            JSON    NOT NULL,
    created_by      VARCHAR,
    created_at      TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id              VARCHAR PRIMARY KEY,
    source_id       VARCHAR,
    provider        VARCHAR NOT NULL,
    model           VARCHAR NOT NULL,
    request_hash    VARCHAR NOT NULL,
    request         JSON    NOT NULL,
    response        JSON,
    error           VARCHAR,
    latency_ms      INTEGER,
    created_at      TIMESTAMP DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS embedding_cache (
    content_hash    VARCHAR NOT NULL,
    model           VARCHAR NOT NULL,
    vector          FLOAT[] NOT NULL,
    created_at      TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (content_hash, model)
);

CREATE TABLE IF NOT EXISTS runs (
    id              VARCHAR PRIMARY KEY,
    spec_id         VARCHAR NOT NULL,
    engine          VARCHAR NOT NULL,
    input_path      VARCHAR NOT NULL,
    output_path     VARCHAR,
    rows_in         BIGINT,
    rows_out        BIGINT,
    rows_rejected   BIGINT,
    report_path     VARCHAR,
    created_at      TIMESTAMP DEFAULT current_timestamp
);
"""


def connect() -> duckdb.DuckDBPyConnection:
    settings = get_settings()
    path = settings.duckdb_file
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(path))
    conn.execute("INSTALL json; LOAD json;")
    return conn


def init_db() -> None:
    with session() as conn:
        conn.execute(SCHEMA_DDL)


@contextmanager
def session() -> Iterator[duckdb.DuckDBPyConnection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
