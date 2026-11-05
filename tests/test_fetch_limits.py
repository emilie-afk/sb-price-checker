"""DNS pinning and in-transfer size limits, tested against a real local TLS server."""
import gzip
import http.server
import socket
import ssl
import subprocess
import threading
import zlib

import pytest

from app import net, scraper
from app.net import SafeResponse, ResponseTooLarge

HOST = 'planetdesert.com'


@pytest.fixture(scope='module')
def certs(tmp_path_factory):
    d = tmp_path_factory.mktemp('certs')
    for name in (HOST, 'other.example'):
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-subj', f'/CN={name}', '-addext', f'subjectAltName=DNS:{name}',
                        '-keyout', str(d / f'{name}.key'), '-out', str(d / f'{name}.crt')],
                       check=True, capture_output=True)
    return d


class _Server:
    """Tiny HTTPS server on 127.0.0.1 that records how many body bytes it sent."""

    def __init__(self, certs, cert_name, mode):
        self.sent = 0
        self.requests = []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                server.requests.append((self.path, self.headers.get('Host')))
                if mode == 'ok':
                    body = b'{"products": []}'
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif mode == 'endless':
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html')
                    self.end_headers()
                    chunk = b'x' * 65536
                    try:
                        for _ in range(4000):           # up to ~260 MB if never stopped
                            self.wfile.write(chunk)
                            server.sent += len(chunk)
                    except (BrokenPipeError, ConnectionResetError, ssl.SSLError, OSError):
                        pass
                elif mode == 'gzip-bomb':
                    body = gzip.compress(b'\0' * (40 * 1024 * 1024))
                    self.send_response(200)
                    self.send_header('Content-Encoding', 'gzip')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    try:
                        self.wfile.write(body)
                    except OSError:
                        pass

        self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certs / f'{cert_name}.crt', certs / f'{cert_name}.key')
        self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server(certs, monkeypatch):
    servers = []

    def make(mode='ok', cert_name=HOST):
        s = _Server(certs, cert_name, mode)
        servers.append(s)
        monkeypatch.setattr(net, 'HTTPS_PORT', s.port)
        monkeypatch.setattr(net, 'CA_BUNDLE', str(certs / f'{HOST}.crt'))
        return s
    yield make
    for s in servers:
        s.close()


@pytest.fixture
def name_lookup_forbidden(monkeypatch):
    """Any attempt by the HTTP client to resolve the hostname again fails the test."""
    real = socket.getaddrinfo
    calls = []

    def guarded(host, *a, **k):
        if host == HOST:
            calls.append(host)
            # A rebinding attacker's second answer would point at a private address
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 443))]
        return real(host, *a, **k)
    monkeypatch.setattr(socket, 'getaddrinfo', guarded)
    return calls


def test_pinned_connection_uses_validated_ip_and_verifies_hostname(server, name_lookup_forbidden):
    srv = server('ok')
    resp = net._pinned_getter(f'https://{HOST}/products.json?page=1', {'User-Agent': 'test'},
                              (5, 5), '127.0.0.1')
    assert resp.status_code == 200 and resp.json() == {'products': []}
    assert srv.requests == [('/products.json?page=1', HOST)]
    assert name_lookup_forbidden == []            # the name was never resolved again


def test_safe_get_validation_public_but_second_lookup_private_cannot_redirect(server, name_lookup_forbidden):
    """Validation sees a public answer; a later lookup would say 10.0.0.1. The
    connection must go to the validated address and never consult DNS again."""
    srv = server('ok')
    validated = []

    def validation_resolver(host, port, proto=None):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.215.14', 443))]

    def recording_getter(url, headers, timeout, ip):
        validated.append(ip)
        return net._pinned_getter(url, headers, timeout, '127.0.0.1')   # stand-in for the public IP
    resp = safe = net.safe_get(f'https://{HOST}/a', {HOST}, getter=recording_getter,
                               resolver=validation_resolver)
    assert safe.status_code == 200 and validated == ['93.184.215.14']
    assert name_lookup_forbidden == []
    assert srv.requests


def test_certificate_for_another_name_is_rejected(server, certs, monkeypatch):
    server('ok', cert_name='other.example')
    monkeypatch.setattr(net, 'CA_BUNDLE', str(certs / 'other.example.crt'))
    with pytest.raises(Exception) as e:
        net._pinned_getter(f'https://{HOST}/a', {}, (5, 5), '127.0.0.1')
    assert 'certificate' in str(e.value).lower() or 'hostname' in str(e.value).lower() \
        or 'SSL' in type(e.value).__name__ or 'MaxRetry' in type(e.value).__name__


def test_streaming_body_stops_at_limit(server, monkeypatch):
    srv = server('endless')
    monkeypatch.setattr(net, 'MAX_RESPONSE_BYTES', 1024 * 1024)
    with pytest.raises(ResponseTooLarge):
        net._pinned_getter(f'https://{HOST}/big', {}, (5, 5), '127.0.0.1')
    srv.close()
    # the client stopped reading near the 1 MiB cap instead of downloading ~260 MB
    assert srv.sent < 16 * 1024 * 1024


def test_compressed_body_is_capped_after_decompression(server, monkeypatch):
    server('gzip-bomb')
    monkeypatch.setattr(net, 'MAX_RESPONSE_BYTES', 1024 * 1024)
    with pytest.raises(ResponseTooLarge):
        net._pinned_getter(f'https://{HOST}/bomb', {}, (5, 5), '127.0.0.1')


def test_manual_inflate_is_bounded(monkeypatch):
    monkeypatch.setattr(net, 'MAX_RESPONSE_BYTES', 1024 * 1024)
    bomb = SafeResponse(200, {}, gzip.compress(b'\0' * (20 * 1024 * 1024)), 'https://x/')
    with pytest.raises(ResponseTooLarge):
        scraper._decode_response(bomb)
    small = SafeResponse(200, {}, gzip.compress(b'<html>ok</html>'), 'https://x/')
    assert scraper._decode_response(small) == '<html>ok</html>'
    plain = SafeResponse(200, {}, b'<html>plain</html>', 'https://x/')
    assert scraper._decode_response(plain) == '<html>plain</html>'


class _FakeCurlSession:
    """Stands in for curl_cffi.Session: feeds chunks to content_callback like libcurl."""
    last = None

    def __init__(self, curl_options=None, use_thread_local_curl=True):
        self.curl_options = curl_options
        self.fed = 0
        _FakeCurlSession.last = self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.closed = True

    def get(self, url, content_callback=None, **kw):
        from curl_cffi.curl import CURL_WRITEFUNC_ERROR
        self.kwargs = kw
        for _ in range(10_000):                    # ~640 MB if never stopped
            chunk = b'y' * 65536
            if content_callback(chunk) == CURL_WRITEFUNC_ERROR:
                raise RuntimeError('curl: (23) Failure writing output to destination')
            self.fed += len(chunk)

        class R:
            status_code, headers, encoding = 200, {}, 'utf-8'
        return R()


def test_curl_transfer_is_aborted_at_limit_and_pinned(monkeypatch):
    from curl_cffi import requests as cf_requests
    from curl_cffi import CurlOpt
    monkeypatch.setattr(cf_requests, 'Session', _FakeCurlSession)
    monkeypatch.setattr(net, 'MAX_RESPONSE_BYTES', 1024 * 1024)
    with pytest.raises(ResponseTooLarge):
        scraper._cffi_getter('https://mountaincrestgardens.com/explore-all/', {}, 45, '93.184.215.14')
    s = _FakeCurlSession.last
    assert s.fed <= 1024 * 1024                    # nothing beyond the cap was accepted
    assert s.curl_options[CurlOpt.RESOLVE] == ['mountaincrestgardens.com:443:93.184.215.14']
    assert s.curl_options[CurlOpt.NOPROXY] == '*'
    assert s.kwargs['allow_redirects'] is False
    assert s.closed


def test_curl_ipv6_pin_is_bracketed(monkeypatch):
    from curl_cffi import requests as cf_requests
    from curl_cffi import CurlOpt

    class Small(_FakeCurlSession):
        def get(self, url, content_callback=None, **kw):
            content_callback(b'ok')

            class R:
                status_code, headers, encoding = 200, {}, 'utf-8'
            return R()
    monkeypatch.setattr(cf_requests, 'Session', Small)
    r = scraper._cffi_getter('https://mountaincrestgardens.com/', {}, 45, '2606:4700::1')
    assert r.content == b'ok'
    assert Small.last.curl_options[CurlOpt.RESOLVE] == ['mountaincrestgardens.com:443:[2606:4700::1]']
