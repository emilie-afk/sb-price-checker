import os
import pg8000.dbapi as pg
from collections import namedtuple
from urllib.parse import urlparse, unquote
from flask import g, current_app


class _WrappedCursor:
    """Wraps a pg8000 cursor so rows support both row[0] index AND row.column_name attribute access."""

    def __init__(self, cur):
        self._cur = cur

    def _make_row(self, row):
        if row is None or self._cur.description is None:
            return row
        fields = [d[0] for d in self._cur.description]
        Row = namedtuple('Row', fields, rename=True)
        return Row(*row)

    def execute(self, sql, params=None):
        self._cur.execute(sql, params or ())
        return self  # allow chaining: cursor.execute(...).fetchone()

    def fetchone(self):
        return self._make_row(self._cur.fetchone())

    def fetchall(self):
        if self._cur.description is None:
            return self._cur.fetchall() or []
        fields = [d[0] for d in self._cur.description]
        Row = namedtuple('Row', fields, rename=True)
        return [Row(*row) for row in (self._cur.fetchall() or [])]

    def __iter__(self):
        if self._cur.description is None:
            return
        fields = [d[0] for d in self._cur.description]
        Row = namedtuple('Row', fields, rename=True)
        for row in self._cur:
            yield Row(*row)


class _DbWrapper:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        cur = _WrappedCursor(self._conn.cursor())
        cur.execute(sql, params or ())
        return cur

    def cursor(self):
        return _WrappedCursor(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def _connect():
    dsn = current_app.config['DATABASE_URL']
    p = urlparse(dsn)
    conn = pg.connect(
        host=p.hostname,
        port=p.port or 5432,
        database=p.path.lstrip('/'),
        user=unquote(p.username or ''),
        password=unquote(p.password or ''),
        ssl_context=True,  # required for Supabase
    )
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
        in_stock    INTEGER DEFAULT 1,
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
    '''
    CREATE TABLE IF NOT EXISTS scrape_queue (
        id           SERIAL PRIMARY KEY,
        source       TEXT NOT NULL,
        status       TEXT DEFAULT 'pending',
        requested_at TEXT,
        completed_at TEXT,
        message      TEXT
    )
    ''',
]


# Schema changes for tables that already exist in the database.
# Safe to run repeatedly — IF NOT EXISTS makes each a no-op once applied.
_MIGRATIONS = [
    'ALTER TABLE sb_products ADD COLUMN IF NOT EXISTS in_stock INTEGER DEFAULT 1',
]


def init_db():
    db = _connect()
    for stmt in _TABLES:
        db.execute(stmt)
    for stmt in _MIGRATIONS:
        try:
            db.execute(stmt)
        except Exception as e:
            print(f'[migration] skipped ({e}): {stmt}', flush=True)
    db.commit()
    db.close()
    current_app.teardown_appcontext(close_db)


def execute_db(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur
