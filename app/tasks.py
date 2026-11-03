"""Durable background-task registry with execution exclusivity in PostgreSQL.

Exclusivity
-----------
Each job kind (sync / collect / match / refresh) has a PostgreSQL *session*
advisory lock. A job may only run while it holds that lock, and it holds it
on the very connection it uses for all of its work:

* The lock is taken (pg_try_advisory_lock) before the task row is written and
  before the job function is called; a failed claim never runs the job.
* The job's reads, writes and internal commits all go through that same
  connection, and the final status update is written on it before it closes.
* The lock is released only when that connection closes — when the job
  really ends, or when the process/connection dies. In the second case the
  dead connection can no longer write anything, so a replacement job can
  never overlap with writes from the old one (fenced writes).
* Nothing releases exclusivity because a job is slow or a status is old.

The app_tasks table records what happened for the dashboard. A queued/running
row whose kind lock is not held by anyone is reported as interrupted and is
closed out the next time a job of that kind is claimed or the app starts.

Requires a direct or session-mode PostgreSQL connection (not a
transaction-mode pooler), which is also what pg8000 needs.
"""
import json
import re
import threading
import time
import traceback
import uuid

from .database import _connect
from .security import redact, error_reference

KINDS = ('sync', 'collect', 'match', 'refresh')
LOCK_NAMESPACE = 0x5BC7          # first int4 key of the two-key advisory lock
KIND_LOCK_IDS = {'sync': 1, 'collect': 2, 'match': 3, 'refresh': 4}
LONG_RUNNING_SECONDS = 2 * 60 * 60   # informational only; never releases the lock

TASK_ID_RE = re.compile(r'^[0-9a-f]{32}$')

INTERRUPTED = 'Interrupted — the server restarted or the worker stopped before this finished'


class PublicTaskError(RuntimeError):
    """A task failure whose message is safe to show to the signed-in user."""


def _lock_args(kind):
    return (LOCK_NAMESPACE, KIND_LOCK_IDS[kind])


def _try_lock(db, kind):
    return bool(db.execute('SELECT pg_try_advisory_lock(%s, %s)', _lock_args(kind)).fetchone()[0])


def lock_held(db, kind):
    """True if any session currently holds the execution lock for `kind`."""
    row = db.execute('''
        SELECT EXISTS (
            SELECT 1 FROM pg_locks
             WHERE locktype = 'advisory' AND granted
               AND classid = %s AND objid = %s AND objsubid = 2
               AND database = (SELECT oid FROM pg_database WHERE datname = current_database()))
    ''', _lock_args(kind)).fetchone()
    return bool(row[0])


def _holds_own_lock(db, kind):
    row = db.execute('''
        SELECT EXISTS (
            SELECT 1 FROM pg_locks
             WHERE locktype = 'advisory' AND granted AND pid = pg_backend_pid()
               AND classid = %s AND objid = %s AND objsubid = 2)
    ''', _lock_args(kind)).fetchone()
    return bool(row[0])


def _close_out_orphans(db, kind):
    """Caller must hold the kind lock: no live job of this kind exists."""
    db.execute('''UPDATE app_tasks SET status='error', finished_at=now(), error=%s
                  WHERE kind=%s AND status IN ('queued', 'running')''', (INTERRUPTED, kind))


def recover_interrupted(app):
    """At startup, close out rows whose job is no longer running anywhere."""
    with app.app_context():
        for kind in KINDS:
            db = _connect()
            try:
                if _try_lock(db, kind):          # nobody is running this kind
                    _close_out_orphans(db, kind)
                    db.commit()
            finally:
                db.close()                        # releases the probe lock


def _public_error(e):
    from .scraper import CollectionError
    if isinstance(e, (PublicTaskError, CollectionError)):
        return str(e)[:300]
    ref = error_reference()
    print(f'[task] error ref {ref}', flush=True)
    return f'Task failed (ref {ref}) — see server logs'


def start_task(app, kind, fn, *args, params=None, before_start=None):
    """Run fn(db, *args) in a background thread unless a `kind` job is running.

    Returns (task_id, started). started=False means a job of the same kind is
    running (in this or another worker) and its id is returned instead.
    """
    if kind not in KINDS:
        raise ValueError('unknown task kind')
    task_id = uuid.uuid4().hex
    with app.app_context():
        db = _connect()
        try:
            if not _try_lock(db, kind):
                existing = _active_task_id(db, kind)
                db.close()
                if existing:
                    return existing, False
                raise PublicTaskError(f'A {kind} job is already running — try again shortly')
            # We hold the execution lock: any queued/running row is an orphan.
            _close_out_orphans(db, kind)
            row = db.execute('''
                INSERT INTO app_tasks (id, kind, status, params, started_at, heartbeat_at)
                VALUES (%s, %s, 'running', %s, now(), now())
                RETURNING id
            ''', (task_id, kind, json.dumps(params or {}, default=str)[:2000])).fetchone()
            db.commit()
            if row is None or row[0] != task_id:
                raise PublicTaskError('Could not start task, please retry')
        except Exception:
            db.close()
            raise

    if before_start:
        before_start(task_id)
    try:
        threading.Thread(target=_worker, args=(app, db, task_id, kind, fn, args),
                         daemon=True).start()
    except Exception:
        db.close()
        raise
    return task_id, True


def _active_task_id(db, kind):
    # The lock holder commits its row before releasing the claim transaction,
    # but allow a brief moment for that commit to become visible.
    for _ in range(10):
        row = db.execute("SELECT id FROM app_tasks WHERE kind=%s AND status='running' "
                         "ORDER BY created_at DESC LIMIT 1", (kind,)).fetchone()
        db.commit()
        if row:
            return row[0]
        time.sleep(0.05)
    return None


def _worker(app, db, task_id, kind, fn, args):
    """Runs on the connection that holds the kind lock; closing it releases the lock."""
    with app.app_context():
        status, result, error = 'error', None, None
        try:
            if not _holds_own_lock(db, kind):
                raise PublicTaskError('Task lost its execution lock before starting')
            print(f'[task {task_id}] starting {kind}', flush=True)
            result = fn(db, *args)
            db.commit()
            status = 'done'
            print(f'[task {task_id}] done', flush=True)
        except Exception as e:
            error = _public_error(e)
            print(f'[task {task_id}] ERROR: {redact(e)}\n{redact(traceback.format_exc())}', flush=True)
            try:
                db.rollback()
            except Exception:
                pass
        try:
            # Written on the lock-holding connection, so it cannot land after a
            # replacement job has claimed the lock.
            db.execute('''UPDATE app_tasks SET status=%s, result=%s, error=%s, finished_at=now(),
                                 heartbeat_at=now()
                          WHERE id=%s AND status='running' ''',
                       (status, json.dumps(result, default=str) if result is not None else None,
                        error, task_id))
            db.commit()
        except Exception as e:
            print(f'[task {task_id}] could not record completion: {type(e).__name__}', flush=True)
        finally:
            db.close()


def get_status(task_id, db):
    """Status dict compatible with the dashboard's polling code."""
    if not isinstance(task_id, str) or not TASK_ID_RE.match(task_id):
        return None
    row = db.execute('''SELECT status, result, error, kind,
                               started_at < now() - %s * interval '1 second'
                        FROM app_tasks WHERE id=%s''', (LONG_RUNNING_SECONDS, task_id)).fetchone()
    if row is None:
        return {'status': 'unknown'}
    status, result, error, kind, long_running = row
    if status in ('queued', 'running'):
        if kind not in KINDS or not lock_held(db, kind):
            return {'status': 'error', 'error': INTERRUPTED}
        out = {'status': 'running'}
        if long_running:
            out['warning'] = 'Still running after more than 2 hours'
        return out
    out = {'status': status}
    if status == 'done':
        out['result'] = json.loads(result) if result else None
    elif status == 'error':
        out['error'] = error or 'Task failed'
    return out
