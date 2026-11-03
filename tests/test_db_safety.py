import pytest

from tests.db_safety import (validate_test_target, drop_disposable_database,
                             UnsafeTestDatabase, OPT_IN_VAR)

OK_ENV = {OPT_IN_VAR: '1'}


def never_connect(**kw):
    raise AssertionError('a connection was attempted for an unsafe target')


@pytest.mark.parametrize('url', [
    'postgresql://postgres:pw@db.abcd.supabase.co:5432/postgres',
    'postgresql://user:pw@dpg-xyz.oregon-postgres.render.com/sb_price',
    'postgresql://postgres@10.0.0.5:5432/sbpc_test',
    'postgresql://postgres@192.168.1.20/sbpc_test',
    'postgresql://postgres@localhost/sbpc_test',          # names can be remapped; IPs only
    'postgresql://postgres@db.internal/sbpc_test',
    'postgresql://postgres@127.0.0.1:5432/postgres',
    'postgresql://postgres@127.0.0.1:5432/succulents_price_checker',
    'postgresql://postgres@127.0.0.1:5432/sbpc_test;DROP',
    'postgresql://postgres@127.0.0.1:5432/sbpc_test?host=db.example.com',
    'mysql://root@127.0.0.1/sbpc_test',
    'not a url',
])
def test_unsafe_targets_are_refused_before_connecting(url):
    with pytest.raises(UnsafeTestDatabase):
        validate_test_target(url, OK_ENV)


def test_opt_in_is_required():
    with pytest.raises(UnsafeTestDatabase) as e:
        validate_test_target('postgresql://postgres@127.0.0.1:5432/sbpc_test', {})
    assert OPT_IN_VAR in str(e.value)
    with pytest.raises(UnsafeTestDatabase):
        validate_test_target('postgresql://postgres@127.0.0.1:5432/sbpc_test', {OPT_IN_VAR: 'yes'})


def test_safe_loopback_targets_accepted():
    t = validate_test_target('postgresql://postgres:pw@127.0.0.1:55432/sbpc_test', OK_ENV)
    assert t == {'host': '127.0.0.1', 'port': 55432, 'user': 'postgres', 'password': 'pw',
                 'prefix': 'sbpc_test'}
    assert validate_test_target('postgresql://postgres@[::1]/sbpc_test_ci', OK_ENV)['host'] == '::1'


def test_only_databases_created_by_this_session_can_be_dropped():
    t = validate_test_target('postgresql://postgres@127.0.0.1:5432/sbpc_test', OK_ENV)
    for name in ('sbpc_test', 'postgres', 'sbpc_test_other', 'prod'):
        with pytest.raises(UnsafeTestDatabase):
            drop_disposable_database(t, name, never_connect, created={'sbpc_test_ab12cd34'})


def test_suite_runs_on_its_own_disposable_database(_database):
    name = _database.rsplit('/', 1)[1]
    assert name.startswith('sbpc_test') and len(name) > len('sbpc_test_')
