import threading
import time

from tests.conftest import post_json, db_query, db_exec


def wait(client, task_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        s = client.get('/task-status/' + task_id).get_json()
        if s['status'] in ('done', 'error'):
            return s
        time.sleep(0.05)
    raise AssertionError('task did not finish')


def test_duplicate_submissions_share_one_job(app, auth_client, monkeypatch):
    import app.routes as routes
    release, calls = threading.Event(), []

    def slow_sync(db):
        calls.append(1)
        release.wait(5)
        return {'synced': 3}
    monkeypatch.setattr(routes, 'sync_sb_products', slow_sync)

    first = post_json(auth_client, '/sync-sb').get_json()
    second = post_json(auth_client, '/sync-sb').get_json()
    assert first['success'] and second['success']
    assert second['task_id'] == first['task_id'] and second.get('already_running') is True
    release.set()
    assert wait(auth_client, first['task_id']) == {'status': 'done', 'result': {'synced': 3}}
    assert len(calls) == 1
    # once finished, a new run can start
    third = post_json(auth_client, '/sync-sb').get_json()
    assert third['task_id'] != first['task_id']
    wait(auth_client, third['task_id'])


def test_duplicate_detection_spans_workers(make_app, app, auth_client, monkeypatch):
    """A second app instance (another gunicorn worker) sees the same active job."""
    import app.routes as routes
    release = threading.Event()
    monkeypatch.setattr(routes, 'sync_sb_products', lambda db: release.wait(5) and {'ok': 1})
    t1 = post_json(auth_client, '/sync-sb').get_json()['task_id']
    from app.tasks import start_task
    other = make_app()
    t2, started = start_task(other, 'sync', lambda db: None)
    assert (t2, started) == (t1, False)
    release.set()
    wait(auth_client, t1)


def test_per_source_retry_does_not_overlap_full_collection(app, auth_client, monkeypatch):
    import app.scraper as scraper
    import app.routes as routes
    release = threading.Event()

    def fake_collection(db, sources=None):
        release.wait(5)
        return {s: {'status': 'success', 'products': 0} for s in (sources or ['planet_desert'])}
    monkeypatch.setattr(routes, 'run_collection', fake_collection)
    monkeypatch.setattr(scraper, 'run_collection', fake_collection)
    full = post_json(auth_client, '/collect', {'sources': ['planet_desert', 'the_sill']}).get_json()
    retry = post_json(auth_client, '/collect/the_sill').get_json()
    assert retry['task_id'] == full['task_id'] and retry['already_running']
    release.set()
    wait(auth_client, full['task_id'])


def test_failures_are_reported_without_internal_details(app, auth_client, monkeypatch, capsys):
    import app.routes as routes

    def broken(db):
        raise ValueError('boom at postgresql://svc:TopSecret99@db.internal:5432/prod')
    monkeypatch.setattr(routes, 'sync_sb_products', broken)
    tid = post_json(auth_client, '/sync-sb').get_json()['task_id']
    s = wait(auth_client, tid)
    assert s['status'] == 'error'
    assert 'TopSecret99' not in s['error'] and 'postgresql' not in s['error']
    assert 'ref' in s['error']
    out = capsys.readouterr()
    assert 'TopSecret99' not in out.out + out.err


def test_expected_collection_errors_keep_their_message(app, auth_client, monkeypatch):
    import app.routes as routes
    from app.scraper import CollectionError

    def blocked(db):
        raise CollectionError('MCG blocked (403) on page 1')
    monkeypatch.setattr(routes, 'sync_sb_products', blocked)
    tid = post_json(auth_client, '/sync-sb').get_json()['task_id']
    assert wait(auth_client, tid) == {'status': 'error', 'error': 'MCG blocked (403) on page 1'}


def _blocking_task(app, kind='sync', name='old'):
    """Start a job that records its backend pid, waits, then writes and commits."""
    from app.tasks import start_task
    state = {'release': threading.Event(), 'pid': None, 'ready': threading.Event(),
             'wrote': False, 'write_error': None}

    def job(db):
        state['pid'] = db.execute('SELECT pg_backend_pid()').fetchone()[0]
        state['ready'].set()
        state['release'].wait(10)
        try:
            # an internal commit, like the scrapers make mid-job
            db.execute("INSERT INTO collection_log (source, products_found, status, message, ran_at) "
                       "VALUES (%s, 0, 'success', 'write', '2026-01-01')", (name,))
            db.commit()
            state['wrote'] = True
        except Exception as e:
            state['write_error'] = type(e).__name__
            raise
        return {'worker': name}
    tid, started = start_task(app, kind, job)
    assert started
    assert state['ready'].wait(5)
    return tid, state


def test_slow_or_stale_looking_job_keeps_exclusivity(make_app, app, auth_client):
    """An old heartbeat/start time must not let a second job of the same kind start."""
    from app.tasks import start_task, recover_interrupted
    tid, state = _blocking_task(app)
    db_exec(app, "UPDATE app_tasks SET started_at = now() - interval '5 hours', "
                 "heartbeat_at = now() - interval '5 hours' WHERE id=%s", (tid,))
    other = make_app()                        # a restarted / second worker
    recover_interrupted(other)
    assert start_task(other, 'sync', lambda db: None) == (tid, False)
    s = auth_client.get('/task-status/' + tid).get_json()
    assert s['status'] == 'running' and 'warning' in s
    state['release'].set()
    assert wait(auth_client, tid)['status'] == 'done'
    assert state['wrote'] is True


def test_replacement_cannot_overlap_writes_of_a_dead_worker(app, auth_client):
    """If the old worker's connection dies, a replacement may start — and the old
    worker can no longer write anything, including internal commits."""
    from app.tasks import start_task
    old_tid, old = _blocking_task(app, name='old-worker')
    assert post_json(auth_client, '/sync-sb').get_json()['task_id'] == old_tid   # still exclusive

    db_exec(app, 'SELECT pg_terminate_backend(%s)', (old['pid'],))
    time.sleep(0.2)
    assert auth_client.get('/task-status/' + old_tid).get_json()['status'] == 'error'

    new_tid, new = _blocking_task(app, name='new-worker')
    assert new_tid != old_tid
    old['release'].set()                      # the old thread tries to keep working
    time.sleep(0.5)
    new['release'].set()
    assert wait(auth_client, new_tid)['status'] == 'done'

    assert old['wrote'] is False and old['write_error'] is not None
    sources = [r[0] for r in db_query(app, "SELECT source FROM collection_log WHERE message='write'")]
    assert sources == ['new-worker']
    rows = dict(db_query(app, 'SELECT id, status FROM app_tasks'))
    assert rows[old_tid] == 'error' and rows[new_tid] == 'done'


def test_concurrent_claims_start_exactly_one_job(make_app, app):
    from app.tasks import start_task
    apps = [app, make_app()]
    release, calls, results = threading.Event(), [], []
    barrier = threading.Barrier(8)

    def job(db):
        calls.append(1)
        release.wait(5)

    def claim(i):
        barrier.wait()
        try:
            results.append(start_task(apps[i % 2], 'refresh', job))
        except Exception as e:                 # brief claim window: reported, never run
            results.append(('busy', type(e).__name__))
    threads = [threading.Thread(target=claim, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    started = [r for r in results if r[1] is True]
    assert len(started) == 1
    assert all(r[0] == started[0][0] for r in results if r[1] in (True, False))
    time.sleep(0.2)
    assert len(calls) == 1
    release.set()


def test_worker_without_the_lock_never_runs_the_job(app):
    from app.database import _connect
    from app.tasks import _worker
    called = []
    with app.app_context():
        db = _connect()
        db.execute("INSERT INTO app_tasks (id, kind, status) VALUES (%s, 'match', 'running')", ('f' * 32,))
        db.commit()
    _worker(app, db, 'f' * 32, 'match', lambda db: called.append(1), ())
    assert called == []
    row = db_query(app, 'SELECT status, error FROM app_tasks WHERE id=%s', ('f' * 32,))[0]
    assert row[0] == 'error' and 'lock' in row[1]


def test_orphaned_rows_are_reported_and_closed_out(app, make_app, auth_client):
    tid = 'c' * 32
    db_exec(app, "INSERT INTO app_tasks (id, kind, status, started_at) "
                 "VALUES (%s, 'match', 'running', now() - interval '1 minute')", (tid,))
    s = auth_client.get('/task-status/' + tid).get_json()
    assert s['status'] == 'error' and 'Interrupted' in s['error']     # nobody holds the lock
    make_app()                                                         # startup recovery
    assert db_query(app, 'SELECT status FROM app_tasks WHERE id=%s', (tid,))[0][0] == 'error'
    from app.tasks import start_task
    new_id, started = start_task(app, 'match', lambda db: {'matched': 0})
    assert started
    wait(auth_client, new_id)


def test_mcg_queue_is_not_duplicated(app, auth_client):
    assert post_json(auth_client, '/trigger-mcg').get_json()['success'] is True
    r = post_json(auth_client, '/trigger-mcg').get_json()
    assert r['success'] is False
    assert db_query(app, 'SELECT COUNT(*) FROM scrape_queue')[0][0] == 1
    assert auth_client.get('/mcg-status').get_json()['status'] == 'pending'


def test_match_job_end_to_end_with_progress(app, auth_client):
    r = post_json(auth_client, '/match', {'plant_types': None, 'sources': None,
                                          'clear_pending': False}).get_json()
    s = wait(auth_client, r['task_id'])
    assert s['status'] == 'done' and s['result']['matched'] == 10
    assert auth_client.get('/match-progress/' + r['task_id']).get_json()['pct'] == 0
