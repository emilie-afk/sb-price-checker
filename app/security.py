"""Authentication, session validity, login throttling and response hardening.

Uses Flask's signed-cookie session (itsdangerous HMAC) and Flask-WTF CSRF —
no custom cryptography. Server-side checks on every request:

  * the session was created by a successful login (auth flag + random sid)
  * it is younger than PERMANENT_SESSION_LIFETIME (issued-at stored inside
    the signed cookie, so browser cookie expiry alone is not trusted)
  * its sid has not been revoked by logout
  * its epoch matches the database epoch (bumped by `flask revoke-sessions`)
  * its password fingerprint matches the current APP_PASSWORD, so changing
    the password revokes every existing session
Rotating FLASK_SECRET_KEY also invalidates every existing cookie.
"""
import hashlib
import hmac
import re
import secrets
import time
import uuid
from urllib.parse import urlsplit

from flask import current_app, jsonify, request, session

from .database import get_db

EPOCH_KEY = 'session_epoch'

# GET endpoints that are polled by fetch() and must answer with JSON
JSON_ENDPOINTS = {'main.task_status', 'main.match_progress', 'main.mcg_status'}

# Endpoints reachable without a session
PUBLIC_ENDPOINTS = {'main.login', 'main.robots', 'static'}


# --------------------------------------------------------------------------- #
# Passwords and sessions                                                       #
# --------------------------------------------------------------------------- #

def password_matches(candidate):
    expected = current_app.config['APP_PASSWORD']
    return hmac.compare_digest((candidate or '').encode('utf-8'), expected.encode('utf-8'))


def password_fingerprint():
    """Keyed digest of the current password; never the password itself."""
    key = current_app.config['SECRET_KEY'].encode('utf-8')
    msg = b'app-password:' + current_app.config['APP_PASSWORD'].encode('utf-8')
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:32]


def current_epoch(db=None):
    db = db or get_db()
    row = db.execute('SELECT value FROM app_security WHERE key=%s', (EPOCH_KEY,)).fetchone()
    if row:
        return row[0]
    db.execute('INSERT INTO app_security (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING',
               (EPOCH_KEY, '1'))
    db.commit()
    row = db.execute('SELECT value FROM app_security WHERE key=%s', (EPOCH_KEY,)).fetchone()
    return row[0]


def bump_epoch(db):
    """Invalidate every existing session. Returns the new epoch token."""
    new = secrets.token_hex(8)
    db.execute('''INSERT INTO app_security (key, value, updated_at) VALUES (%s, %s, now())
                  ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()''',
               (EPOCH_KEY, new))
    db.commit()
    return new


def start_session():
    session.clear()
    session.permanent = True
    session['auth'] = True
    session['sid'] = secrets.token_urlsafe(18)
    session['iat'] = int(time.time())
    session['epoch'] = current_epoch()
    session['pwf'] = password_fingerprint()


def session_is_valid():
    if session.get('auth') is not True:
        return False
    sid, iat = session.get('sid'), session.get('iat')
    if not isinstance(sid, str) or not isinstance(iat, int):
        return False
    now = time.time()
    lifetime = current_app.config['PERMANENT_SESSION_LIFETIME'].total_seconds()
    if iat > now + 60 or now - iat > lifetime:
        return False
    if not hmac.compare_digest(str(session.get('pwf', '')), password_fingerprint()):
        return False
    db = get_db()
    if str(session.get('epoch')) != current_epoch(db):
        return False
    revoked = db.execute('SELECT 1 FROM auth_revoked_sessions WHERE sid=%s', (sid,)).fetchone()
    return revoked is None


def end_session():
    """Logout: revoke this sid server-side (so a copied cookie stops working) and clear it."""
    sid, iat = session.get('sid'), session.get('iat')
    if isinstance(sid, str) and isinstance(iat, int):
        lifetime = current_app.config['PERMANENT_SESSION_LIFETIME'].total_seconds()
        db = get_db()
        db.execute('''INSERT INTO auth_revoked_sessions (sid, expires_at)
                      VALUES (%s, to_timestamp(%s)) ON CONFLICT (sid) DO NOTHING''',
                   (sid, float(iat + lifetime + 60)))
        db.execute('DELETE FROM auth_revoked_sessions WHERE expires_at < now()')
        db.commit()
    session.clear()


# --------------------------------------------------------------------------- #
# Durable login throttling (PostgreSQL, shared by all workers)                 #
# --------------------------------------------------------------------------- #

def _ip_bucket():
    ip = request.remote_addr or 'unknown'
    digest = hmac.new(current_app.config['SECRET_KEY'].encode(), ip.encode(), hashlib.sha256)
    return 'ip:' + digest.hexdigest()[:24]


LOGIN_LOCK = (0x5BC7, 100)   # advisory xact lock that serialises login decisions


def attempt_login(candidate):
    """Check the limit, verify the password and record the outcome atomically.

    Returns 'ok', 'invalid' or 'throttled'. A transaction-scoped advisory
    lock serialises the whole decision across threads and workers, so
    concurrent guesses cannot all observe a below-limit count: every accepted
    verification has been counted before the next one is evaluated.
    """
    db = get_db()
    db.commit()                                  # start from a clean transaction
    try:
        db.execute('SELECT pg_advisory_xact_lock(%s, %s)', LOGIN_LOCK)
        window = current_app.config['LOGIN_WINDOW_MINUTES']
        q = ("SELECT COUNT(*) FROM login_attempts WHERE bucket=%s "
             "AND attempted_at > now() - (%s * interval '1 minute')")
        bucket = _ip_bucket()
        ip_fails = db.execute(q, (bucket, window)).fetchone()[0]
        global_fails = db.execute(q, ('global', window)).fetchone()[0]
        if (ip_fails >= current_app.config['LOGIN_MAX_FAILURES_PER_IP']
                or global_fails >= current_app.config['LOGIN_MAX_FAILURES_GLOBAL']):
            db.rollback()
            return 'throttled'
        if (candidate is not None and len(candidate) <= 1024 and password_matches(candidate)):
            db.execute('DELETE FROM login_attempts WHERE bucket=%s', (bucket,))
            db.commit()                          # releases the lock
            return 'ok'
        db.execute('INSERT INTO login_attempts (bucket) VALUES (%s), (%s)', (bucket, 'global'))
        # Housekeeping: keep the table small
        db.execute("DELETE FROM login_attempts WHERE attempted_at < now() - interval '1 day'")
        db.commit()                              # releases the lock
        return 'invalid'
    except Exception:
        db.rollback()
        raise


# --------------------------------------------------------------------------- #
# Request classification, redirects, headers                                   #
# --------------------------------------------------------------------------- #

def is_api_request():
    if request.method not in ('GET', 'HEAD'):
        return True
    if request.endpoint in JSON_ENDPOINTS:
        return True
    if request.headers.get('X-Requested-With'):
        return True
    best = request.accept_mimetypes.best
    return best == 'application/json'


def json_error(message, status):
    resp = jsonify({'success': False, 'error': message})
    resp.status_code = status
    return resp


def safe_next(target):
    """Allow only same-site relative paths as a post-login destination."""
    if not target or not isinstance(target, str) or len(target) > 512:
        return None
    if '\\' in target or any(ord(c) < 32 for c in target):
        return None
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith('/') or target.startswith('//'):
        return None
    if parts.path in ('/login', '/logout'):
        return None
    return target


CSP = ("default-src 'self'; "
       "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; "
       "style-src 'self' 'unsafe-inline'; "
       "img-src 'self' https: data:; "
       "connect-src 'self'; font-src 'self' data:; "
       "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


def apply_security_headers(response):
    h = response.headers
    if request.endpoint != 'static':
        # Every page/export/API response is private operational data
        h['Cache-Control'] = 'no-store, private'
        h['Pragma'] = 'no-cache'
        h.setdefault('Vary', 'Cookie')
    h.setdefault('X-Content-Type-Options', 'nosniff')
    h.setdefault('X-Frame-Options', 'DENY')
    h.setdefault('Referrer-Policy', 'same-origin')
    h.setdefault('Content-Security-Policy', CSP)
    if current_app.config.get('IS_PRODUCTION'):
        h.setdefault('Strict-Transport-Security', 'max-age=31536000')
    return response


# --------------------------------------------------------------------------- #
# Output safety                                                                #
# --------------------------------------------------------------------------- #

def safe_url(value):
    """Jinja filter: only http(s) URLs without credentials may become links."""
    if not value or not isinstance(value, str) or len(value) > 2048:
        return '#'
    v = value.strip()
    try:
        parts = urlsplit(v)
    except ValueError:
        return '#'
    if parts.scheme.lower() not in ('http', 'https') or not parts.hostname:
        return '#'
    if parts.username or parts.password or any(ord(c) < 32 for c in v):
        return '#'
    return v


_FORMULA_START = ('=', '+', '-', '@', '\t', '\r', '\n')


def csv_text(value):
    """Neutralise spreadsheet formulas in untrusted text cells."""
    if value is None:
        return ''
    s = str(value)
    if s.startswith(_FORMULA_START):
        return "'" + s
    return s


_DSN_RE = re.compile(r'(postgres(?:ql)?://)[^@\s/]*@', re.I)


def redact(text):
    """Strip credentials from connection strings that might appear in messages."""
    return _DSN_RE.sub(r'\1[redacted]@', str(text or ''))


def error_reference():
    return uuid.uuid4().hex[:8]
