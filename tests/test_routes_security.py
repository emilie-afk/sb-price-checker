import csv
import io

import pytest

from tests.conftest import post_json, db_query, db_exec


def _pid(app, ext='sb-4'):
    return db_query(app, 'SELECT id FROM sb_products WHERE external_id=%s', (ext,))[0][0]


# --------------------------------------------------------------------------- #
# CSRF and HTTP methods                                                         #
# --------------------------------------------------------------------------- #

def test_every_state_changing_route_requires_csrf(app, auth_client):
    posts = [r.rule.replace('<int:product_id>', '1').replace('<int:match_id>', '1')
                   .replace('<source>', 'planet_desert')
             for r in app.url_map.iter_rules() if 'POST' in r.methods and r.endpoint != 'main.login']
    assert len(posts) >= 10
    for path in posts:
        r = auth_client.post(path, json={}, headers={'X-Requested-With': 'fetch'})
        assert r.status_code == 400, path
        assert 'Security token' in r.get_json()['error']
    assert db_query(app, 'SELECT COUNT(*) FROM app_tasks')[0][0] == 0


def test_cross_site_form_post_is_rejected(app, auth_client):
    """A form auto-submitted from another site carries the cookie but no token."""
    r = auth_client.post('/trigger-mcg', data={}, headers={'Origin': 'https://evil.example'})
    assert r.status_code == 400
    assert db_query(app, 'SELECT COUNT(*) FROM scrape_queue')[0][0] == 0


def test_state_changing_routes_do_not_accept_get(app):
    for rule in app.url_map.iter_rules():
        if rule.endpoint.split('.')[-1] in ('manual_match', 'accept_match', 'reject_match', 'sync_sb',
                                             'collect', 'collect_source', 'trigger_mcg', 'run_match',
                                             'refresh_prices', 'logout'):
            assert 'GET' not in rule.methods, rule


# --------------------------------------------------------------------------- #
# Caching and headers                                                           #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize('path', ['/', '/products', '/export.csv', '/review', '/diagnostics',
                                  '/task-status/' + 'a' * 32, '/mcg-status'])
def test_private_responses_are_not_cacheable(auth_client, path):
    r = auth_client.get(path)
    assert r.status_code == 200
    assert 'no-store' in r.headers['Cache-Control']
    assert r.headers['X-Frame-Options'] == 'DENY'
    assert "frame-ancestors 'none'" in r.headers['Content-Security-Policy']
    assert r.headers['X-Content-Type-Options'] == 'nosniff'


def test_login_page_not_cacheable(client):
    assert 'no-store' in client.get('/login').headers['Cache-Control']


# --------------------------------------------------------------------------- #
# Input validation                                                              #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize('payload', [
    {'sources': ['planet_desert', 'http://169.254.169.254/']},
    {'sources': 'planet_desert'},
    {'sources': [{'x': 1}]},
    {'sources': ["x'); DROP TABLE matches;--"]},
])
def test_collect_rejects_unknown_sources(app, auth_client, payload):
    r = post_json(auth_client, '/collect', payload)
    assert r.status_code == 400
    assert db_query(app, 'SELECT COUNT(*) FROM app_tasks')[0][0] == 0


def test_collect_source_rejects_unknown(auth_client):
    r = post_json(auth_client, '/collect/not_a_store')
    assert r.status_code == 400
    assert 'not_a_store' not in r.get_data(as_text=True)


@pytest.mark.parametrize('payload', [
    {'plant_types': 'Succulents'},
    {'plant_types': [1, 2]},
    {'plant_types': ['x' * 500]},
    {'sources': ['nope']},
    {'clear_pending': 'yes'},
    [1, 2, 3],
])
def test_match_rejects_bad_input(app, auth_client, payload):
    assert post_json(auth_client, '/match', payload).status_code == 400
    assert db_query(app, 'SELECT COUNT(*) FROM app_tasks')[0][0] == 0


GOOD_MANUAL = {'source': 'bloomscape', 'title': 'Echeveria Lola', 'variant_title': '2 inch',
               'price': 8.25, 'url': 'https://bloomscape.com/product/echeveria-lola/'}


@pytest.mark.parametrize('change', [
    {'url': 'javascript:alert(1)'},
    {'url': 'http://bloomscape.com/product/x'},
    {'url': 'https://user:pw@bloomscape.com/product/x'},
    {'url': 'https://bloomscape.com.evil.example/x'},
    {'url': 'https://127.0.0.1/x'},
    {'url': 'https://mountaincrestgardens.com/x'},     # wrong store for the chosen source
    {'url': 'https://bloomscape.com:8443/x'},
    {'source': 'planet_desert'},
    {'source': 'evil'},
    {'price': 'NaN'},
    {'price': 'Infinity'},
    {'price': -5},
    {'price': 0},
    {'price': 10_000_000},
    {'price': True},
    {'title': ''},
    {'title': 'x' * 301},
    {'title': ['list']},
    {'variant_title': 'bad\x00value'},
])
def test_manual_match_validation(app, auth_client, change):
    before = db_query(app, 'SELECT COUNT(*) FROM matches')[0][0]
    r = post_json(auth_client, f'/products/{_pid(app)}/manual-match', {**GOOD_MANUAL, **change})
    assert r.status_code == 400, change
    assert db_query(app, 'SELECT COUNT(*) FROM matches')[0][0] == before


def test_manual_match_valid_and_unknown_product(app, auth_client):
    r = post_json(auth_client, f'/products/{_pid(app)}/manual-match', GOOD_MANUAL)
    assert r.status_code == 200 and r.get_json()['success'] is True
    assert r.get_json()['price_diff_pct'] == 26.9
    r = post_json(auth_client, '/products/999999/manual-match', GOOD_MANUAL)
    assert r.status_code == 404


def test_manual_match_www_host_is_allowed(app, auth_client):
    r = post_json(auth_client, f'/products/{_pid(app)}/manual-match',
                  {**GOOD_MANUAL, 'source': 'mountain_crest',
                   'url': 'https://www.mountaincrestgardens.com/echeveria-lola/'})
    assert r.status_code == 200


def test_accept_reject_unknown_match_is_404(auth_client):
    assert post_json(auth_client, '/matches/999999/accept').status_code == 404
    assert post_json(auth_client, '/matches/999999/reject').status_code == 404


def test_invalid_task_ids(auth_client):
    assert auth_client.get('/task-status/../../etc').status_code == 404
    assert auth_client.get('/task-status/xyz').status_code == 400
    assert auth_client.get('/match-progress/xyz').status_code == 400
    assert auth_client.get('/task-status/' + 'b' * 32).get_json() == {'status': 'unknown'}


def test_position_filter_cannot_alter_sql(auth_client):
    normal = auth_client.get('/products').get_data(as_text=True)
    evil = auth_client.get("/products?position=below_market' OR 1=1--").get_data(as_text=True)
    assert evil.count('<tr') == normal.count('<tr')
    r = auth_client.get("/export.csv?position=x');DROP TABLE matches;--")
    assert r.status_code == 200


def test_oversized_request_body_rejected(app, auth_client):
    r = auth_client.post('/match', data='x' * 100_000, content_type='application/json',
                         headers={'X-CSRFToken': auth_client.csrf, 'X-Requested-With': 'fetch'})
    assert r.status_code == 413


# --------------------------------------------------------------------------- #
# Output encoding: templates and CSV                                            #
# --------------------------------------------------------------------------- #

def _run_match(app):
    from app.database import _connect
    from app.matcher import run_matching
    with app.app_context():
        db = _connect()
        run_matching(db)
        db.close()


def test_untrusted_urls_and_titles_render_safely(app, auth_client):
    _run_match(app)
    db_exec(app, "UPDATE competitor_products SET url='javascript:alert(document.cookie)' "
                 "WHERE external_id='pd-3'")
    html = auth_client.get(f'/products/{_pid(app)}').get_data(as_text=True)
    assert 'javascript:alert' not in html
    assert 'href="#"' in html
    html = auth_client.get('/assortment-gaps').get_data(as_text=True)
    assert '<img src=x onerror' not in html
    assert '&lt;img src=x onerror=alert(1)&gt;' in html
    assert 'href="javascript:' not in html


def test_scraped_source_name_cannot_break_out_of_onclick(app, auth_client):
    db_exec(app, "INSERT INTO collection_log (source, products_found, status, message, ran_at) "
                 "VALUES (%s, 0, 'error', %s, '2026-01-02T00:00:00')",
            ("x');alert(1);//", '<script>alert(2)</script>'))
    html = auth_client.get('/').get_data(as_text=True)
    assert "retrySource('x')" not in html
    assert 'data-source="x&#39;);alert(1);//"' in html
    assert '<script>alert(2)</script>' not in html


def test_csv_export_neutralises_formulas_but_keeps_numbers(app, auth_client):
    _run_match(app)
    body = auth_client.get('/export.csv').get_data(as_text=True)
    rows = list(csv.reader(io.StringIO(body)))
    header, data = rows[0], rows[1:]
    titles = [r[0] for r in data]
    assert any(t.startswith("'=HYPERLINK") for t in titles)
    assert not any(t.startswith('=') for t in titles)
    for r in data:
        assert r[2].startswith('$') and r[8].startswith('$')
        assert r[9][0] in '+-' and r[9].endswith('%')     # numeric percentage left intact
        float(r[11])                                       # confidence still numeric


def test_csv_text_helper():
    from app.security import csv_text
    assert csv_text('=1+1') == "'=1+1"
    assert csv_text('+cmd') == "'+cmd"
    assert csv_text('-2') == "'-2"
    assert csv_text('@SUM(A1)') == "'@SUM(A1)"
    assert csv_text('\t=x') == "'\t=x"
    assert csv_text('Aloe') == 'Aloe'
    assert csv_text(None) == ''


def test_safe_url_filter():
    from app.security import safe_url
    assert safe_url('https://planetdesert.com/p') == 'https://planetdesert.com/p'
    for bad in ('javascript:alert(1)', 'JaVaScRiPt:x', 'data:text/html,x', '//evil', '',
                None, 'https://u:p@x.com/', 'vbscript:x'):
        assert safe_url(bad) == '#'


def test_internal_errors_are_not_exposed(app, auth_client, monkeypatch, capsys):
    import app.routes as routes

    def boom(*a, **k):
        raise RuntimeError('relation x failed for postgresql://admin:SuperSecretPw@db.internal/prod')
    monkeypatch.setattr(routes, 'run_assortment_gaps', boom, raising=False)
    import app.matcher as matcher
    monkeypatch.setattr(matcher, 'run_assortment_gaps', boom)
    r = auth_client.get('/assortment-gaps')
    assert r.status_code == 500
    body = r.get_data(as_text=True)
    assert 'SuperSecretPw' not in body and 'postgresql' not in body and 'Traceback' not in body
    assert 'ref' in body
