-- 001_security_tables.sql
-- Additive only: creates new tables/indexes; does not alter, delete or
-- recalculate any existing table or row. The app also runs these statements
-- (idempotently) at startup via app/database.py:init_db(), so applying this
-- file by hand before deploying is optional but lets you review/verify first.
BEGIN;

CREATE TABLE IF NOT EXISTS app_security (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS auth_revoked_sessions (
    sid        TEXT PRIMARY KEY,
    revoked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS login_attempts (
    id           BIGSERIAL PRIMARY KEY,
    bucket       TEXT NOT NULL,
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS login_attempts_bucket_time ON login_attempts (bucket, attempted_at);

CREATE TABLE IF NOT EXISTS app_tasks (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    status       TEXT NOT NULL,
    params       TEXT,
    result       TEXT,
    error        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at   TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS app_tasks_one_active_per_kind
    ON app_tasks (kind) WHERE status IN ('queued', 'running');

COMMIT;

-- Rollback (only if the release is abandoned; the previous app version ignores these tables):
-- DROP TABLE IF EXISTS app_tasks, login_attempts, auth_revoked_sessions, app_security;
