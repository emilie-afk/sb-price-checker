import pathlib
import re

import pytest

from app.config import load_settings, ConfigError

STRONG = {
    'APP_ENV': 'production',
    'APP_PASSWORD': 'Correct-Horse-Battery-77',
    'FLASK_SECRET_KEY': 'k' * 16 + 'Q9' * 16,
    'DATABASE_URL': 'postgresql://user:pw@db.example.invalid:5432/app',
}


def settings(**changes):
    env = dict(STRONG)
    for k, v in changes.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    return load_settings(env)


def test_strong_production_config_is_accepted():
    s = settings()
    assert s['IS_PRODUCTION'] is True
    assert s['SESSION_COOKIE_SECURE'] is True
    assert s['SESSION_COOKIE_HTTPONLY'] is True
    assert s['SESSION_COOKIE_SAMESITE'] == 'Lax'
    assert s['DEBUG'] is False
    assert s['DATABASE_SSL'] == 'require'


def test_unset_app_env_means_production():
    s = settings(APP_ENV=None)
    assert s['IS_PRODUCTION'] is True


@pytest.mark.parametrize('name', ['APP_PASSWORD', 'FLASK_SECRET_KEY'])
@pytest.mark.parametrize('value', [None, '', '   '])
def test_missing_or_empty_credentials_refuse_startup(name, value):
    with pytest.raises(ConfigError) as e:
        settings(**{name: value})
    assert name in str(e.value)


@pytest.mark.parametrize('name,value', [
    ('APP_PASSWORD', 'succulents2026'),                 # the old public fallback
    ('FLASK_SECRET_KEY', 'dev-secret-change-in-prod'),  # the old public fallback
    ('APP_PASSWORD', 'changeme'),
    ('APP_PASSWORD', 'short-pw'),
    ('FLASK_SECRET_KEY', 'too-short-secret'),
    ('FLASK_SECRET_KEY', 'placeholder-' + 'x' * 40),
])
def test_placeholder_or_weak_credentials_refuse_production(name, value):
    with pytest.raises(ConfigError) as e:
        settings(**{name: value})
    assert value not in str(e.value)          # never echo the secret


def test_password_and_signing_key_must_differ():
    with pytest.raises(ConfigError):
        settings(APP_PASSWORD=STRONG['FLASK_SECRET_KEY'])


def test_production_rejects_debug_and_plaintext_db():
    with pytest.raises(ConfigError):
        settings(FLASK_DEBUG='1')
    with pytest.raises(ConfigError):
        settings(DATABASE_SSL='disable')
    with pytest.raises(ConfigError):
        settings(DATABASE_URL=None)


def test_dev_config_allows_synthetic_short_values():
    s = settings(APP_ENV='test', APP_PASSWORD='devpw', FLASK_SECRET_KEY='devkey',
                 DATABASE_SSL='disable')
    assert s['IS_PRODUCTION'] is False
    assert s['SESSION_COOKIE_SECURE'] is False


def test_dev_config_still_requires_credentials():
    with pytest.raises(ConfigError):
        settings(APP_ENV='development', APP_PASSWORD=None)


def test_create_app_refuses_to_start_without_password(env):
    from app import create_app
    env.delenv('APP_PASSWORD')
    env.setenv('APP_ENV', 'production')
    with pytest.raises(ConfigError):
        create_app()


def test_create_app_refuses_old_public_fallbacks_in_production(env):
    from app import create_app
    env.setenv('APP_ENV', 'production')
    env.setenv('APP_PASSWORD', 'succulents2026')
    env.setenv('FLASK_SECRET_KEY', 'dev-secret-change-in-prod')
    with pytest.raises(ConfigError):
        create_app()


def test_no_literal_credential_fallbacks_in_source():
    root = pathlib.Path(__file__).resolve().parent.parent
    pattern = re.compile(r"environ\.get\(\s*['\"](APP_PASSWORD|FLASK_SECRET_KEY)['\"]\s*,")
    for path in list((root / 'app').glob('*.py')) + [root / 'wsgi.py']:
        assert not pattern.search(path.read_text()), path
    assert 'secret_key = ' not in (root / 'app' / '__init__.py').read_text()


def test_render_yaml_keeps_secrets_out_of_source():
    root = pathlib.Path(__file__).resolve().parent.parent
    text = (root / 'render.yaml').read_text()
    assert re.search(r'key: FLASK_SECRET_KEY\s+generateValue: true', text)
    assert re.search(r'key: APP_PASSWORD\s+sync: false', text)
    assert 'value:' not in text.split('APP_PASSWORD')[1].split('- key')[0]
