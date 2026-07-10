import os
import psycopg
from psycopg.rows import namedtuple_row
from flask import g, current_app


class _DbWrapper:
    """Makes a psycopg connection look like sqlite3 in our codebase.
    All cursors use namedtuple_row so rows support both row[0] and row.column_name."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        cur = self._conn.cursor(row_factory=namedtuple_row)
        cur.execute(sql, params or ())
        return cur

    def cursor(self):
        return self._conn.cursor(row_factory=namedtuple_row)

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def _connect():
    dsn = current_app.config['DATABASE_URL']
    conn = psycopg.connect(dsn)
    return _DbWrapper(conn)


def get_db():
    if 'db' not in g:
        g.db = _connect()
    return g.db


def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


# --------------------------------------------------------------------------- #
# Schema                                                                        #
# --------------------------------------------------------------------------- #

_TABLES = [
    '''
    CREATE TABLE IF NOT EXISTS sb_products (
        id          SERIAL PRIMARY KEY,
        external_id TEXT UNIQUE,
        title       TEXT NOT NULL,
        handle      TEXT,
        product_type TEXT,
        url         TEXT,
        price_min   REAL DEFAULT 0,
        price_max   REAL DEFAULT 0,
        tracked     INTEGER DEFAULT 1,
        synced_at   TEXT,
        created_at  TEXT
    )
    ''',
    '''
    CREATE TABLE IF NOT EXISTS sb_variants (
        id                  SERIAL PRIMARY KEY,
        product_id          INTEGER REFERENCES sb_products(id),
        external_variant_id TEXT UNIQUE,
        variant_title       TEXT,
        price               REAL DEFAULT 0,
        available           INTEGER DEFAULT 1,
        sku                 TEXT,
        created_at          TEXT
    )
    ''',
    '''
    CREATE TABLE IF NOT EXISTS competitor_products (
        id           SERIAL PRIMARY KEY,
        source       TEXT NOT NULL,
        external_id  TEXT,
        title        TEXT NOT NULL,
        handle       TEXT,
        product_type TEXT,
        description  TEXT,
        url          TEXT,
        image_url    TEXT,
        collected_at TEXT,
        UNIQUE(source, external_id)
    )
    ''',
    '''
    CREATE TABLE IF NOT EXISTS competitor_variants (
        id                  SERIAL PRIMARY KEY,
        product_id          INTEGER REFERENCES competitor_products(id),
        external_variant_id TEXT,
        variant_title       TEXT,
        price               REAL,
        available           INTEGER DEFAULT 1,
        sku                 TEXT,
        collected_at        TEXT
    )
    ''',
    '''
    CREATE TABLE IF NOT EXISTS matches (
        id                    SERIAL PRIMARY KEY,
        sb_product_id         INTEGER REFERENCES sb_products(id),
        competitor_variant_id INTEGER REFERENCES competitor_variants(id),
        relationship          TEXT,
        confidence            INTEGER DEFAULT 0,
        status                TEXT DEFAULT 'pending',
        ai_explanation        TEXT,
        price_diff_pct        REAL,
        market_position       TEXT DEFAULT 'unknown',
        reviewed_by           TEXT,
        reviewed_at           TEXT,
        created_at            TEXT
    )
    ''',
    '''
    CREATE TABLE IF NOT EXISTS price_snapshots (
        id          SERIAL PRIMARY KEY,
        variant_id  INTEGER REFERENCES competitor_variants(id),
        price       REAL,
        available   INTEGER,
        captured_at TEXT
    )
    ''',
    '''
    CREATE TABLE IF NOT EXISTS collection_log (
        id             SERIAL PRIMARY KEY,
        source         TEXT,
        products_found INTEGER DEFAULT 0,
        status         TEXT,
        message        TEXT,
        ran_at         TEXT
    )
    ''',
]


def init_db():
    db = _connect()
    for stmt in _TABLES:
        db.execute(stmt)
    db.commit()
    db.close()
    current_app.teardown_appcontext(close_db)


def execute_db(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur
