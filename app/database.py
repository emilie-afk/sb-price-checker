import sqlite3
import os
from flask import g, current_app


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(
            current_app.config['DATABASE'],
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA journal_mode=WAL')
    return g.db


def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(current_app.config['DATABASE'])
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.commit()

    db.executescript('''
        CREATE TABLE IF NOT EXISTS sb_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id TEXT UNIQUE,
            title TEXT NOT NULL,
            handle TEXT,
            product_type TEXT,
            url TEXT,
            price_min REAL DEFAULT 0,
            price_max REAL DEFAULT 0,
            tracked INTEGER DEFAULT 1,
            synced_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS sb_variants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER REFERENCES sb_products(id),
            external_variant_id TEXT UNIQUE,
            variant_title TEXT,
            price REAL DEFAULT 0,
            available INTEGER DEFAULT 1,
            sku TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS competitor_products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            external_id TEXT,
            title TEXT NOT NULL,
            handle TEXT,
            product_type TEXT,
            description TEXT,
            url TEXT,
            image_url TEXT,
            collected_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source, external_id)
        );

        CREATE TABLE IF NOT EXISTS competitor_variants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER REFERENCES competitor_products(id),
            external_variant_id TEXT,
            variant_title TEXT,
            price REAL,
            available INTEGER DEFAULT 1,
            sku TEXT,
            collected_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sb_product_id INTEGER REFERENCES sb_products(id),
            competitor_variant_id INTEGER REFERENCES competitor_variants(id),
            relationship TEXT,
            confidence INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            ai_explanation TEXT,
            price_diff_pct REAL,
            market_position TEXT DEFAULT 'unknown',
            reviewed_by TEXT,
            reviewed_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS price_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            variant_id INTEGER REFERENCES competitor_variants(id),
            price REAL,
            available INTEGER,
            captured_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS collection_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            products_found INTEGER DEFAULT 0,
            status TEXT,
            message TEXT,
            ran_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    ''')

    # Migrate old schema if needed (adds new columns to sb_products if upgrading)
    _migrate(db)

    db.commit()
    db.close()

    current_app.teardown_appcontext(close_db)


def _migrate(db):
    """Add new columns to existing tables if upgrading from old schema."""
    existing = {row[1] for row in db.execute("PRAGMA table_info(sb_products)").fetchall()}
    new_cols = [
        ('external_id', 'TEXT'),
        ('handle', 'TEXT'),
        ('product_type', 'TEXT'),
        ('url', 'TEXT'),
        ('price_min', 'REAL DEFAULT 0'),
        ('price_max', 'REAL DEFAULT 0'),
        ('synced_at', 'TEXT'),
    ]
    for col, typ in new_cols:
        if col not in existing:
            try:
                db.execute(f'ALTER TABLE sb_products ADD COLUMN {col} {typ}')
            except Exception:
                pass


def query_db(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rv = cur.fetchall()
    return (rv[0] if rv else None) if one else rv


def execute_db(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur
