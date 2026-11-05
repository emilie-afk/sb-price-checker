import socket

import pytest

from app import net
from app.net import (check_url_syntax, check_resolves_public, safe_get, SafeResponse,
                     UnsafeURLError, ResponseTooLarge, host_variants)

HOSTS = host_variants('https://planetdesert.com')


def public_resolver(host, port, proto=None):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.215.14', 443))]


def resolver_for(ip):
    fam = socket.AF_INET6 if ':' in ip else socket.AF_INET
    return lambda host, port, proto=None: [(fam, socket.SOCK_STREAM, 6, '', (ip, 443))]


@pytest.mark.parametrize('url', [
    'http://planetdesert.com/products.json',
    'ftp://planetdesert.com/x',
    'file:///etc/passwd',
    'https://user:pw@planetdesert.com/',
    'https://planetdesert.com@evil.example/',
    'https://evil.example/',
    'https://planetdesert.com.evil.example/',
    'https://127.0.0.1/',
    'https://[::1]/',
    'https://169.254.169.254/latest/meta-data/',
    'https://localhost/',
    'https://planetdesert.com:8080/',
    'https://planetdesert.com/\nx',
    '',
    None,
])
def test_url_syntax_rejections(url):
    with pytest.raises(UnsafeURLError):
        check_url_syntax(url, HOSTS)


def test_url_syntax_accepts_store_urls():
    assert check_url_syntax('https://planetdesert.com/products.json?page=1', HOSTS)
    assert check_url_syntax('https://www.planetdesert.com/x', HOSTS)
    assert check_url_syntax('https://planetdesert.com:443/x', HOSTS)


@pytest.mark.parametrize('ip', ['127.0.0.1', '10.1.2.3', '172.16.0.5', '192.168.1.1',
                                '169.254.169.254', '0.0.0.0', '::1', 'fd00::1',
                                '::ffff:127.0.0.1', '100.64.0.1'])
def test_private_and_metadata_destinations_rejected(ip):
    with pytest.raises(UnsafeURLError):
        check_resolves_public('planetdesert.com', resolver_for(ip))


def test_public_destination_allowed():
    check_resolves_public('planetdesert.com', public_resolver)


class FakeGetter:
    def __init__(self, responses):
        self.responses, self.urls = list(responses), []

    def __call__(self, url, headers, timeout, ip):
        self.urls.append(url)
        self.ips = getattr(self, 'ips', []) + [ip]
        status, headers_, body = self.responses.pop(0)
        return SafeResponse(status, headers_, body, url)


def test_redirect_to_forbidden_destination_is_blocked():
    g = FakeGetter([(302, {'Location': 'https://169.254.169.254/latest/meta-data/'}, b'')])
    with pytest.raises(UnsafeURLError):
        safe_get('https://planetdesert.com/products.json', HOSTS, getter=g, resolver=public_resolver)
    assert g.urls == ['https://planetdesert.com/products.json']


def test_redirect_to_other_host_is_blocked():
    g = FakeGetter([(301, {'Location': 'https://evil.example/x'}, b'')])
    with pytest.raises(UnsafeURLError):
        safe_get('https://planetdesert.com/a', HOSTS, getter=g, resolver=public_resolver)


def test_redirect_within_store_followed_and_bounded():
    g = FakeGetter([(301, {'Location': '/b'}, b''), (200, {}, b'{"products": []}')])
    r = safe_get('https://planetdesert.com/a', HOSTS, getter=g, resolver=public_resolver)
    assert r.status_code == 200 and r.url == 'https://planetdesert.com/b'
    loop = FakeGetter([(302, {'Location': '/a'}, b'')] * 10)
    with pytest.raises(UnsafeURLError):
        safe_get('https://planetdesert.com/a', HOSTS, getter=loop, resolver=public_resolver)
    assert len(loop.urls) == net.MAX_REDIRECTS + 1


def test_dns_rebinding_to_private_ip_blocked_before_request():
    g = FakeGetter([(200, {}, b'')])
    with pytest.raises(UnsafeURLError):
        safe_get('https://planetdesert.com/a', HOSTS, getter=g, resolver=resolver_for('10.0.0.1'))
    assert g.urls == []


def test_getter_receives_the_validated_ip_for_every_hop():
    g = FakeGetter([(301, {'Location': 'https://www.planetdesert.com/b'}, b''), (200, {}, b'ok')])
    answers = iter(['93.184.215.14', '93.184.215.99'])
    safe_get('https://planetdesert.com/a', HOSTS, getter=g,
             resolver=lambda h, p, proto=None: [(socket.AF_INET, 1, 6, '', (next(answers), 443))])
    assert g.ips == ['93.184.215.14', '93.184.215.99']


def test_shopify_page_fetch_goes_through_allowlist(monkeypatch):
    from app import scraper
    calls = []

    def fake_safe_get(url, hosts, **kw):  # noqa: D401
        calls.append((url, set(hosts)))
        return SafeResponse(200, {}, b'{"products": [{"id": 1}]}', url)
    monkeypatch.setattr(scraper, 'safe_get', fake_safe_get)
    page, products = scraper._fetch_shopify_page(scraper.COMPETITORS['the_sill']['url_template'], 2, '')
    assert products == [{'id': 1}]
    assert calls[0][1] == {'thesill.com', 'www.thesill.com'}


def test_parse_product_drops_unsafe_scraped_links():
    from app.scraper import parse_product
    raw = {'id': 9, 'title': 'Plant', 'handle': 'plant', 'variants': [],
           'images': [{'src': 'http://insecure.example/i.png'}],
           '_url_override': 'javascript:alert(1)'}
    product, _ = parse_product(raw, 'planet_desert')
    assert product['url'] is None and product['image_url'] is None
    raw.update(_url_override='https://evil.example/x', images=[{'src': 'https://cdn.shopify.com/i.png'}])
    product, _ = parse_product(raw, 'planet_desert')
    assert product['url'] is None
    assert product['image_url'] == 'https://cdn.shopify.com/i.png'
    raw.pop('_url_override')
    raw['handle'] = '../../admin?x=<y>'
    product, _ = parse_product(raw, 'planet_desert')
    assert product['url'].startswith('https://planetdesert.com/products/')
    assert '<' not in product['url'] and '?' not in product['url']


def test_mcg_parser_ignores_off_site_links():
    from app.scraper import _parse_mcg_page
    html = '''<html><body>
      <article class="card"><h3 class="card-title">Good Plant</h3>
        <a href="/good-plant/">x</a><span class="price">$5.00</span></article>
      <article class="card"><h3 class="card-title">Bad Plant</h3>
        <a href="https://evil.example/bad/">x</a><span class="price">$5.00</span></article>
      <article class="card"><h3 class="card-title">Creds</h3>
        <a href="https://u:p@mountaincrestgardens.com/c/">x</a><span class="price">$5.00</span></article>
    </body></html>'''
    products, _ = _parse_mcg_page(html, 2, 'https://mountaincrestgardens.com')
    assert [p['title'] for p in products] == ['Good Plant']
    assert products[0]['_url_override'] == 'https://mountaincrestgardens.com/good-plant/'


def test_collection_uses_only_code_defined_urls():
    """No route lets a user supply a URL that the server fetches."""
    import pathlib
    import re
    src = (pathlib.Path(__file__).resolve().parent.parent / 'app' / 'routes.py').read_text()
    assert not re.search(r'safe_get|requests\.|urlopen|curl_cffi', src)
    assert 'shell=True' not in ''.join(
        p.read_text() for p in (pathlib.Path(__file__).resolve().parent.parent / 'app').glob('*.py'))
