"""Guards for the test-suite's disposable PostgreSQL database.

The suite creates its own uniquely named database (``<prefix>_<random>``),
uses it, and drops only that database afterwards. It never drops or
truncates a database it did not create. Before any connection is opened the
target must be:

* explicitly opted in:  SBPC_ALLOW_DESTRUCTIVE_TESTS=1
* a PostgreSQL URL on a loopback IP literal (127.0.0.0/8 or ::1)
* named with the dedicated prefix ``sbpc_test`` (letters, digits, underscore)
* free of query parameters (which could redirect the connection elsewhere)
"""
import ipaddress
import re
import secrets
from urllib.parse import urlsplit, unquote

OPT_IN_VAR = 'SBPC_ALLOW_DESTRUCTIVE_TESTS'
DEFAULT_URL = 'postgresql://postgres@127.0.0.1:5432/sbpc_test'
NAME_RE = re.compile(r'^sbpc_test[a-z0-9_]{0,30}$')


class UnsafeTestDatabase(RuntimeError):
    pass


def validate_test_target(url, environ):
    """Return connection parts for a safe target, or raise before any connection."""
    if environ.get(OPT_IN_VAR) != '1':
        raise UnsafeTestDatabase(
            f'Database tests create and drop a disposable database; set {OPT_IN_VAR}=1 to allow it')
    try:
        parts = urlsplit(url)
        port = parts.port or 5432
    except ValueError:
        raise UnsafeTestDatabase('TEST_DATABASE_URL is not a valid URL')
    if parts.scheme not in ('postgresql', 'postgres'):
        raise UnsafeTestDatabase('TEST_DATABASE_URL must be a postgresql:// URL')
    if parts.query or parts.fragment:
        raise UnsafeTestDatabase('TEST_DATABASE_URL must not contain query parameters')
    host = parts.hostname or ''
    try:
        if not ipaddress.ip_address(host).is_loopback:
            raise UnsafeTestDatabase('TEST_DATABASE_URL must point at a loopback address')
    except ValueError:
        raise UnsafeTestDatabase('TEST_DATABASE_URL host must be a loopback IP (127.0.0.1 or ::1)')
    prefix = parts.path.lstrip('/')
    if not NAME_RE.match(prefix):
        raise UnsafeTestDatabase('TEST_DATABASE_URL database name must start with "sbpc_test"')
    return {'host': host, 'port': port, 'user': unquote(parts.username or 'postgres'),
            'password': unquote(parts.password) if parts.password else None, 'prefix': prefix}


def create_disposable_database(target, connect):
    """Create ``<prefix>_<random>`` and return (name, dsn). Only creates, never drops."""
    name = f"{target['prefix']}_{secrets.token_hex(4)}"
    c = connect(host=target['host'], port=target['port'], user=target['user'],
                password=target['password'], database='postgres')
    try:
        c.autocommit = True
        c.cursor().execute(f'CREATE DATABASE {name}')
    finally:
        c.close()
    auth = target['user'] + (f":{target['password']}" if target['password'] else '')
    host = f"[{target['host']}]" if ':' in target['host'] else target['host']
    return name, f"postgresql://{auth}@{host}:{target['port']}/{name}"


def drop_disposable_database(target, name, connect, created):
    """Drop a database only if this test session created it."""
    if name not in created or not name.startswith(target['prefix'] + '_'):
        raise UnsafeTestDatabase('refusing to drop a database this session did not create')
    c = connect(host=target['host'], port=target['port'], user=target['user'],
                password=target['password'], database='postgres')
    try:
        c.autocommit = True
        c.cursor().execute(f'DROP DATABASE IF EXISTS {name} WITH (FORCE)')
    finally:
        c.close()
