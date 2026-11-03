import time

from flask.sessions import SecureCookieSessionInterface

from tests.conftest import (TEST_PASSWORD, csrf_token, login, post_json, db_query, db_exec)

PUBLIC = {'main.login', 'main.robots', 'static'}


def _all_routes(app):
    for rule in app.url_map.iter_rules():
        if rule.endpoint in PUBLIC:
            continue
        path = rule.rule
        for arg in rule.arguments:
            path = path.replace(f'<int:{arg}>', '1').replace(f'<{arg}>', 'a' * 32)
        for method in rule.methods - {'HEAD', 'OPTIONS'}:
            yield rule.endpoint, method, path


def test_every_protected_route_rejects_anonymous_requests(app, client):
    seen = 0
    for endpoint, method, path in _all_routes(app):
        r = client.open(path, method=method)
        seen += 1
        assert r.status_code in (302, 401), (endpoint, method, r.status_code)
        if r.status_code == 302:
            assert '/login' in r.headers['Location'], endpoint
            assert method == 'GET'
        else:
            assert r.get_json() == {'success': False, 'error': 'Authentication required'}
    assert seen >= 20


def test_api_calls_get_json_401_not_login_html(client):
    for path in ('/task-status/' + 'a' * 32, '/match-progress/' + 'a' * 32, '/mcg-status'):
        r = client.get(path)
        assert r.status_code == 401 and r.is_json
    r = client.get('/', headers={'X-Requested-With': 'fetch'})
    assert r.status_code == 401 and r.is_json


def test_anonymous_cannot_launch_jobs(app, client):
    for path in ('/sync-sb', '/collect', '/collect/planet_desert', '/match', '/refresh-prices',
                 '/trigger-mcg'):
        assert client.post(path, json={}).status_code == 401
    assert db_query(app, 'SELECT COUNT(*) FROM app_tasks')[0][0] == 0
    assert db_query(app, 'SELECT COUNT(*) FROM scrape_queue')[0][0] == 0


def test_anonymous_cannot_read_exports_or_private_pages(client):
    for path in ('/export.csv', '/products', '/products/1', '/review', '/diagnostics',
                 '/assortment-gaps', '/size-gaps'):
        r = client.get(path)
        assert r.status_code == 302
        assert b'Haworthia' not in r.data


def test_login_success_and_generic_failure(client):
    r = login(client, 'wrong-password')
    assert r.status_code == 401
    body = r.get_data(as_text=True)
    assert 'Incorrect password' in body
    for leak in ('APP_PASSWORD', 'environment', TEST_PASSWORD, 'Traceback'):
        assert leak not in body
    r = login(client)
    assert r.status_code == 302 and r.headers['Location'].endswith('/')
    assert client.get('/').status_code == 200


def test_session_cookie_flags(client):
    r = login(client)
    cookie = r.headers.get('Set-Cookie')
    assert 'HttpOnly' in cookie and 'SameSite=Lax' in cookie
    assert 'Expires=' in cookie


def test_session_cookie_is_secure_in_production_settings():
    from app.config import load_settings
    s = load_settings({'APP_ENV': 'production', 'APP_PASSWORD': 'x' * 20,
                       'FLASK_SECRET_KEY': 'y' * 40, 'DATABASE_URL': 'postgresql://h/db'})
    assert s['SESSION_COOKIE_SECURE'] and s['SESSION_COOKIE_HTTPONLY']


def test_forged_session_with_other_key_is_rejected(app, client):
    other = app.__class__('forger')
    other.secret_key = 'attacker-chosen-key-0123456789abcdefghij'
    ser = SecureCookieSessionInterface().get_signing_serializer(other)
    forged = ser.dumps({'auth': True, 'authenticated': True, 'sid': 'x', 'iat': int(time.time()),
                        'epoch': '1', 'pwf': 'x'})
    client.set_cookie('sbpc_session', forged)
    assert client.get('/').status_code == 302


def test_old_style_session_flag_is_not_enough(app, client):
    with client.session_transaction() as s:
        s['authenticated'] = True          # what the previous version checked
    assert client.get('/').status_code == 302


def test_tampered_cookie_is_rejected(auth_client):
    cookie = auth_client.get_cookie('sbpc_session')
    auth_client.set_cookie('sbpc_session', cookie.value[:-3] + 'abc')
    assert auth_client.get('/').status_code == 302


def test_expired_session_is_rejected_server_side(auth_client, app):
    with auth_client.session_transaction() as s:
        s['iat'] = int(time.time()) - int(app.config['PERMANENT_SESSION_LIFETIME'].total_seconds()) - 5
    assert auth_client.get('/').status_code == 302


def test_logout_requires_post_and_revokes_copied_cookie(app, auth_client):
    assert auth_client.get('/logout').status_code in (302, 405)
    stolen = auth_client.get_cookie('sbpc_session').value
    r = auth_client.post('/logout', data={'csrf_token': auth_client.csrf})
    assert r.status_code == 302
    assert auth_client.get('/').status_code == 302
    other = app.test_client()
    other.set_cookie('sbpc_session', stolen)
    assert other.get('/').status_code == 302


def test_logout_without_csrf_is_rejected(auth_client):
    r = auth_client.post('/logout', data={})
    assert r.status_code == 400
    assert auth_client.get('/').status_code == 200


def test_revoke_all_sessions_command(app, auth_client):
    runner = app.test_cli_runner()
    result = runner.invoke(args=['revoke-sessions'])
    assert 'revoked' in result.output
    assert auth_client.get('/').status_code == 302


def test_password_change_revokes_existing_sessions(app, auth_client):
    app.config['APP_PASSWORD'] = 'a-brand-new-password-456'
    assert auth_client.get('/').status_code == 302


def test_signing_key_rotation_revokes_existing_sessions(make_app, app, auth_client, env):
    cookie = auth_client.get_cookie('sbpc_session').value
    env.setenv('FLASK_SECRET_KEY', 'rotated-signing-key-abcdefghijklmnopqrstuvwxyz')
    rotated = make_app()
    c = rotated.test_client()
    c.set_cookie('sbpc_session', cookie)
    assert c.get('/').status_code == 302


def test_login_limit_is_durable_across_workers(make_app, app):
    """Failures recorded by one app instance block logins on another (shared DB)."""
    worker_a, worker_b = app.test_client(), make_app().test_client()
    limit = app.config['LOGIN_MAX_FAILURES_PER_IP']
    for i in range(limit):
        client = worker_a if i % 2 else worker_b
        assert login(client, 'nope-%d' % i).status_code == 401
    r = login(worker_a)                       # even the right password is refused now
    assert r.status_code == 429
    assert 'Too many sign-in attempts' in r.get_data(as_text=True)
    r = login(worker_b)
    assert r.status_code == 429
    db_exec(app, "UPDATE login_attempts SET attempted_at = now() - interval '1 hour'")
    assert login(worker_a).status_code == 302


def test_global_login_limit(app, client):
    app.config['LOGIN_MAX_FAILURES_GLOBAL'] = 3
    app.config['LOGIN_MAX_FAILURES_PER_IP'] = 100
    for i in range(3):
        login(client, 'bad')
    assert login(client).status_code == 429


def test_login_requires_csrf_token(client):
    r = client.post('/login', data={'password': TEST_PASSWORD})
    assert r.status_code == 302 and '/login' in r.headers['Location']
    assert client.get('/').status_code == 302


def test_post_login_redirect_is_validated(app):
    for target, expected in [('/products?position=below_market', '/products?position=below_market'),
                             ('https://evil.example/', '/'),
                             ('//evil.example/x', '/'),
                             ('/\\evil.example', '/'),
                             ('javascript:alert(1)', '/'),
                             ('/logout', '/')]:
        c = app.test_client()
        r = login(c, next_url=target)
        assert r.status_code == 302
        loc = r.headers['Location']
        assert loc == expected or loc.endswith(expected) and 'evil' not in loc, (target, loc)


def test_login_redirect_preserves_requested_page(client):
    r = client.get('/products?position=near_market')
    assert r.status_code == 302
    assert 'next=/products?position%3Dnear_market' in r.headers['Location'] or \
        'next=%2Fproducts' in r.headers['Location']


def test_password_never_logged(app, client, capsys):
    login(client, 'Wrong-But-Secret-Guess-999')
    login(client)
    out = capsys.readouterr()
    for s in ('Wrong-But-Secret-Guess-999', TEST_PASSWORD, app.config['SECRET_KEY']):
        assert s not in out.out and s not in out.err


def test_concurrent_wrong_guesses_cannot_exceed_the_limit(make_app, app, monkeypatch):
    """Many simultaneous guesses across two app instances: exactly LIMIT are verified."""
    import threading
    import app.security as security
    limit = 5
    apps = [app, make_app()]
    for a in apps:
        a.config['LOGIN_MAX_FAILURES_PER_IP'] = limit
    real = security.password_matches
    verified = []

    def slow_check(candidate):
        verified.append(candidate)
        time.sleep(0.05)                     # widen the race window
        return real(candidate)
    monkeypatch.setattr(security, 'password_matches', slow_check)

    clients = [apps[i % 2].test_client() for i in range(16)]
    tokens = [csrf_token(c) for c in clients]
    barrier = threading.Barrier(len(clients))
    codes = []

    def guess(i):
        barrier.wait()
        r = clients[i].post('/login', data={'password': f'wrong-{i}', 'csrf_token': tokens[i]})
        codes.append(r.status_code)
    threads = [threading.Thread(target=guess, args=(i,)) for i in range(len(clients))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(codes) == [401] * limit + [429] * (len(clients) - limit)
    assert len(verified) == limit
    ip_rows = db_query(app, "SELECT COUNT(*) FROM login_attempts WHERE bucket LIKE %s", ('ip:%',))[0][0]
    assert ip_rows == limit
