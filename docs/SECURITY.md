# Security configuration

This repository is public. The current source tree contains code and
synthetic test data only — never real passwords, signing keys, database URLs,
exports or sales data. Keep it that way: private values belong in Render's
environment settings, never in commits.

## Required environment variables (Render → Environment)

| Name | Purpose |
| --- | --- |
| `APP_PASSWORD` | Shared sign-in password. At least 12 characters; not a placeholder. Set privately (`sync: false`). |
| `FLASK_SECRET_KEY` | Session-signing key, separate from the password. Render generates it (`generateValue: true`). At least 32 characters. |
| `DATABASE_URL` | PostgreSQL connection string. Private (`sync: false`). TLS is always required in production. |
| `ANTHROPIC_API_KEY` | Unchanged. |

Optional: `SESSION_LIFETIME_HOURS` (default 12), `LOGIN_MAX_FAILURES_PER_IP` (10),
`LOGIN_MAX_FAILURES_GLOBAL` (100), `LOGIN_WINDOW_MINUTES` (15),
`TRUSTED_PROXY_HOPS` (1 in production), `MCG_WORKER_DIR` / `MCG_WORKER_CMD`.

Any environment without `APP_ENV=development` or `APP_ENV=test` is treated as
production: the app refuses to start if `APP_PASSWORD` or `FLASK_SECRET_KEY` is
missing, empty, too short, equal to each other, or a known placeholder
(including the old values that were once hardcoded here), or if debug mode or
an unencrypted database connection is requested.

## Sessions

* Cookies are `HttpOnly`, `SameSite=Lax`, and `Secure` in production.
* Lifetime is enforced on the server from the issue time stored in the signed
  cookie (default 12 hours), not by the browser's cookie expiry.
* **Sign Out** (POST) revokes that session server-side, so a copied cookie stops working.
* **Change `APP_PASSWORD`** → every existing session is rejected on its next request
  (sessions carry a keyed fingerprint of the password they were created under).
* **Rotate `FLASK_SECRET_KEY`** → every existing cookie fails signature checks.
* **Sign everyone out without changing credentials:**
  `FLASK_APP=wsgi flask revoke-sessions` (Render Shell).

Login failures are counted in PostgreSQL (shared by all workers): 10 per client
IP or 100 overall within 15 minutes blocks further attempts until the window passes.
The limit check, password verification and failure recording happen inside one
short transaction under an advisory lock, so concurrent guesses cannot exceed it.

## Background jobs

Sync, collect, match and refresh jobs each hold a PostgreSQL session advisory
lock on the same connection they use for all their writes. A second job of the
same kind cannot start until that connection closes — when the job finishes or
its worker/connection dies (and a dead connection can no longer write). A job
is never released for being slow. This requires a direct or session-mode
database connection (not a transaction-mode pooler). Individual network
requests have timeouts; a job's total run time is not capped.

All state-changing requests are POST and require a CSRF token
(Flask-WTF). Pages, exports and API responses are sent with `Cache-Control: no-store`.

## Local development and tests

```
export APP_ENV=development            # or test
export APP_PASSWORD=local-dev-only-password
export FLASK_SECRET_KEY=local-dev-only-signing-key-not-for-production-use
export DATABASE_URL=postgresql://postgres@127.0.0.1:5432/sb_price_checker_dev
export DATABASE_SSL=disable          # only accepted outside production
python wsgi.py
```

Tests use only synthetic data with all external network access blocked. They
create a uniquely named database on a **local** PostgreSQL server and drop only
that database afterwards. The URL must use a loopback IP, the name must start
with `sbpc_test`, and you must opt in explicitly:

```
pip install -r requirements-dev.txt
SBPC_ALLOW_DESTRUCTIVE_TESTS=1 \
TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5432/sbpc_test pytest
```

## Outbound requests

Scraping only fetches code-defined store URLs. Every request and redirect hop
must be https on the competitor's own host (bare or `www.`), default port, no
embedded credentials, and resolve to public IP addresses. The connection is
then made to that validated address (DNS pinning) while TLS still verifies the
hostname, so a changed DNS answer cannot reach a private address. Responses
are capped while downloading (after decompression) and requests have
connect/read timeouts. Users cannot make the server fetch an arbitrary URL. Links
taken from scraped pages or manual entry are stored only if they are https links
on the competitor's site, and are rendered only if they are http(s).
