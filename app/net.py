"""Outbound HTTP for scraping, restricted to approved store hosts.

This is NOT a general-purpose fetcher: every URL (and every redirect hop) must
be https, on an allowlisted host, on the default port, with no embedded
credentials, and must resolve only to public IP addresses.

DNS pinning: the host is resolved once per hop, every answer is checked, and
the connection is then made to that validated IP address. The client never
resolves the name again, so a changed DNS answer cannot redirect the request
to a private address. TLS still uses the hostname for SNI and certificate
verification. Pinned connections are direct (environment proxies are not used).

Responses are capped while they stream (after decompression) and every
request has connect/read timeouts.
"""
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlsplit

import requests
import urllib3

CA_BUNDLE = requests.certs.where()
HTTPS_PORT = 443          # only the default port is ever allowed (see check_url_syntax)

MAX_RESPONSE_BYTES = 25 * 1024 * 1024
DEFAULT_TIMEOUT = (10, 45)       # (connect, read) seconds
MAX_REDIRECTS = 5


class UnsafeURLError(ValueError):
    pass


class ResponseTooLarge(RuntimeError):
    pass


def host_variants(base_url):
    """{'example.com', 'www.example.com'} for a store's base URL."""
    host = (urlsplit(base_url).hostname or '').lower()
    bare = host[4:] if host.startswith('www.') else host
    return {bare, 'www.' + bare}


def check_url_syntax(url, allowed_hosts, schemes=('https',)):
    """Validate a URL without network access. Returns the normalised URL."""
    if not isinstance(url, str) or not url or len(url) > 2048:
        raise UnsafeURLError('invalid URL')
    if any(ord(c) < 33 for c in url):
        raise UnsafeURLError('URL contains whitespace or control characters')
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise UnsafeURLError('invalid URL')
    if parts.scheme.lower() not in schemes:
        raise UnsafeURLError('URL scheme not allowed')
    if parts.username is not None or parts.password is not None or '@' in parts.netloc:
        raise UnsafeURLError('credentials in URL are not allowed')
    host = (parts.hostname or '').lower().rstrip('.')
    if not host:
        raise UnsafeURLError('URL has no host')
    try:
        ipaddress.ip_address(host)
        raise UnsafeURLError('IP-address URLs are not allowed')
    except ValueError:
        pass
    if host not in {h.lower() for h in allowed_hosts}:
        raise UnsafeURLError('host is not an approved store')
    if port not in (None, 443):
        raise UnsafeURLError('non-default port not allowed')
    return url


def _is_public_ip(ip):
    addr = ipaddress.ip_address(ip)
    if getattr(addr, 'ipv4_mapped', None):
        addr = addr.ipv4_mapped
    return addr.is_global and not (addr.is_private or addr.is_loopback or addr.is_link_local
                                   or addr.is_multicast or addr.is_reserved
                                   or addr.is_unspecified)


def check_resolves_public(host, resolver=socket.getaddrinfo):
    """Resolve once, require every answer to be public, return the IP to connect to."""
    try:
        infos = resolver(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise UnsafeURLError('host does not resolve')
    ips = [info[4][0] for info in infos]
    if not ips or not all(_is_public_ip(ip) for ip in ips):
        # private, loopback, link-local (incl. 169.254.169.254 metadata) etc.
        raise UnsafeURLError('host resolves to a non-public address')
    v4 = [ip for ip in ips if ':' not in ip]
    return (v4 or ips)[0]


class SafeResponse:
    def __init__(self, status_code, headers, content, url, encoding=None):
        self.status_code = status_code
        self.headers = headers
        self.content = content
        self.url = url
        self.encoding = encoding

    @property
    def text(self):
        return self.content.decode(self.encoding or 'utf-8', errors='replace')

    def json(self):
        import json
        return json.loads(self.content.decode('utf-8', errors='replace'))


def _check_declared_length(headers):
    declared = headers.get('Content-Length') if headers else None
    if declared and str(declared).strip().isdigit() and int(declared) > MAX_RESPONSE_BYTES:
        raise ResponseTooLarge('response too large')


def read_capped(chunks):
    """Join an iterator of byte chunks, stopping as soon as the cap is exceeded."""
    out, total = [], 0
    for chunk in chunks:
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise ResponseTooLarge('response too large')
        out.append(chunk)
    return b''.join(out)


def _charset(headers):
    m = re.search(r'charset=([\w.-]+)', (headers or {}).get('Content-Type', '') or '', re.I)
    return m.group(1) if m else None


def _pinned_getter(url, headers, timeout, ip):
    """GET over TLS to the validated `ip`, verifying the certificate for the hostname."""
    parts = urlsplit(url)
    host = parts.hostname
    path = (parts.path or '/') + (f'?{parts.query}' if parts.query else '')
    connect_t, read_t = timeout if isinstance(timeout, tuple) else (timeout, timeout)
    pool = urllib3.HTTPSConnectionPool(
        ip, port=HTTPS_PORT, server_hostname=host, assert_hostname=host,
        cert_reqs='CERT_REQUIRED', ca_certs=CA_BUNDLE, maxsize=1, retries=False,
        timeout=urllib3.Timeout(connect=connect_t, read=read_t))
    try:
        req_headers = {'Accept-Encoding': 'gzip, deflate', **(headers or {}), 'Host': host}
        resp = pool.urlopen('GET', path, headers=req_headers, redirect=False, retries=False,
                            preload_content=False, assert_same_host=False)
        try:
            _check_declared_length(resp.headers)
            # stream() yields at most 64 KiB of *decompressed* data per chunk
            body = read_capped(resp.stream(64 * 1024, decode_content=True))
            return SafeResponse(resp.status, resp.headers, body, url, _charset(resp.headers))
        finally:
            resp.release_conn()
            resp.close()
    finally:
        pool.close()


def safe_get(url, allowed_hosts, headers=None, timeout=DEFAULT_TIMEOUT,
             getter=_pinned_getter, resolver=socket.getaddrinfo):
    """GET an approved URL, validating and pinning every redirect hop.

    getter(url, headers, timeout, ip) must connect to `ip` and must not resolve
    the hostname itself.
    """
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        check_url_syntax(current, allowed_hosts)
        ip = check_resolves_public(urlsplit(current).hostname, resolver)
        resp = getter(current, headers or {}, timeout, ip)
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get('Location')
            if not location:
                return resp
            current = urljoin(current, location)
            continue
        resp.url = current
        return resp
    raise UnsafeURLError('too many redirects')
