"""Test fixtures. Uses a disposable local PostgreSQL database and synthetic data.

The suite creates its own uniquely named database on a LOCAL server, uses it
and drops only that database at the end (see tests/db_safety.py):

    SBPC_ALLOW_DESTRUCTIVE_TESTS=1 \
    TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5432/sbpc_test pytest

The URL names the server and a database-name prefix; it must be a loopback
IP and the name must start with "sbpc_test". Never point it at production.
"""
import os
import re

import pg8000.dbapi as pg
import pytest

from tests.db_safety import (validate_test_target, create_disposable_database,
                             drop_disposable_database, DEFAULT_URL, UnsafeTestDatabase)

TEST_PASSWORD = 'synthetic-test-password-123'
TEST_SECRET = 'synthetic-test-signing-key-0123456789abcdef'

BASE_ENV = {
    'APP_ENV': 'test',
    'APP_PASSWORD': TEST_PASSWORD,
    'FLASK_SECRET_KEY': TEST_SECRET,
    'DATABASE_SSL': 'disable',
}

TABLES = ['matches', 'price_snapshots', 'competitor_variants', 'competitor_products',
          'sb_variants', 'sb_products', 'collection_log', 'scrape_queue', 'app_tasks',
          'login_attempts', 'auth_revoked_sessions', 'app_security']


@pytest.fixture(scope='session', autouse=True)
def _database():
    url = os.environ.get('TEST_DATABASE_URL', DEFAULT_URL)
    try:
        target = validate_test_target(url, os.environ)      # before any connection
    except UnsafeTestDatabase as e:
        pytest.exit(f'Refusing to run database tests: {e}', returncode=2)
    name, dsn = create_disposable_database(target, pg.connect)
    BASE_ENV['DATABASE_URL'] = dsn
    try:
        yield dsn
    finally:
        drop_disposable_database(target, name, pg.connect, created={name})


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for k in ('APP_ENV', 'APP_PASSWORD', 'FLASK_SECRET_KEY', 'DATABASE_URL', 'DATABASE_SSL',
              'FLASK_DEBUG', 'SESSION_LIFETIME_HOURS', 'TRUSTED_PROXY_HOPS'):
        monkeypatch.delenv(k, raising=False)
    for k, v in BASE_ENV.items():
        monkeypatch.setenv(k, v)
    return monkeypatch


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Committed tests must never reach real stores or APIs."""
    import requests

    def blocked(*a, **k):
        raise AssertionError('network access attempted in tests')
    monkeypatch.setattr(requests, 'get', blocked)
    monkeypatch.setattr(requests.Session, 'request', blocked)

    # The pinned fetcher uses urllib3 directly: only loopback test servers allowed
    import urllib3.connectionpool as cp
    real_urlopen = cp.HTTPConnectionPool.urlopen

    def loopback_only(self, *a, **k):
        if self.host not in ('127.0.0.1', '::1'):
            raise AssertionError('network access attempted in tests')
        return real_urlopen(self, *a, **k)
    monkeypatch.setattr(cp.HTTPConnectionPool, 'urlopen', loopback_only)
    try:
        from curl_cffi import requests as cf
        monkeypatch.setattr(cf, 'get', blocked)
        monkeypatch.setattr(cf.Session, 'request', blocked)
    except ImportError:
        pass


def _reset(app):
    from app.database import _connect
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    import synthetic_data
    with app.app_context():
        db = _connect()
        db.execute('TRUNCATE ' + ', '.join(TABLES) + ' RESTART IDENTITY CASCADE')
        db.commit()
        synthetic_data.seed(db)
        db.close()


@pytest.fixture
def make_app():
    from app import create_app

    def _make(**overrides):
        a = create_app()
        a.config.update(TESTING=True, **overrides)
        return a
    return _make


@pytest.fixture
def app(make_app):
    a = make_app()
    _reset(a)
    return a


@pytest.fixture
def client(app):
    return app.test_client()


def csrf_token(client):
    html = client.get('/login').get_data(as_text=True)
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return m.group(1)


def login(client, password=TEST_PASSWORD, next_url=None):
    data = {'password': password, 'csrf_token': csrf_token(client)}
    if next_url is not None:
        data['next'] = next_url
    return client.post('/login', data=data)


@pytest.fixture
def auth_client(client):
    r = login(client)
    assert r.status_code == 302
    html = client.get('/').get_data(as_text=True)
    token = re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)
    client.csrf = token
    return client


def post_json(client, url, payload=None, csrf=True):
    headers = {'X-Requested-With': 'fetch'}
    if csrf:
        headers['X-CSRFToken'] = client.csrf
    return client.post(url, json=payload, headers=headers)


def db_query(app, sql, params=()):
    from app.database import _connect
    with app.app_context():
        db = _connect()
        try:
            rows = db.execute(sql, params).fetchall()
            db.commit()
            return rows
        finally:
            db.close()


def db_exec(app, sql, params=()):
    from app.database import _connect
    with app.app_context():
        db = _connect()
        try:
            db.execute(sql, params)
            db.commit()
        finally:
            db.close()
