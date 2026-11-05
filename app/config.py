"""Runtime configuration and fail-closed credential checks.

The app treats every environment as PRODUCTION unless APP_ENV is explicitly
'development' or 'test'. Production refuses to start without a strong
APP_PASSWORD and a separate strong FLASK_SECRET_KEY.
"""
import os
from datetime import timedelta

DEV_ENVS = {'development', 'test'}

# Values that must never be accepted in production. Includes the literal
# fallbacks that were previously hardcoded in this public repository.
KNOWN_PLACEHOLDERS = {
    'succulents2026',
    'dev-secret-change-in-prod',
    'changeme', 'change-me', 'change_me', 'password', 'secret', 'admin',
    'replace-me', 'your-password-here', 'your-secret-key-here',
    'synthetic-test-password-123',
    'synthetic-test-signing-key-0123456789abcdef',
}

MIN_PASSWORD_LEN = 12
MIN_SECRET_KEY_LEN = 32


class ConfigError(RuntimeError):
    """Raised at startup when required security settings are missing or weak."""


def app_env():
    return (os.environ.get('APP_ENV') or 'production').strip().lower()


def is_production():
    return app_env() not in DEV_ENVS


def _check_secret(name, value, min_len, production):
    if value is None or not value.strip():
        raise ConfigError(f'{name} must be set')
    if production:
        if value.strip().lower() in KNOWN_PLACEHOLDERS or value.lower().startswith(('placeholder', 'example')):
            raise ConfigError(f'{name} is a known placeholder value; set a real secret')
        if len(value) < min_len:
            raise ConfigError(f'{name} must be at least {min_len} characters in production')


def load_settings(environ=None):
    """Validate the environment and return Flask config values.

    Never logs or returns secret values in error messages.
    """
    env = os.environ if environ is None else environ
    production = (env.get('APP_ENV') or 'production').strip().lower() not in DEV_ENVS

    password = env.get('APP_PASSWORD')
    secret_key = env.get('FLASK_SECRET_KEY')
    _check_secret('APP_PASSWORD', password, MIN_PASSWORD_LEN, production)
    _check_secret('FLASK_SECRET_KEY', secret_key, MIN_SECRET_KEY_LEN, production)
    if password == secret_key:
        raise ConfigError('APP_PASSWORD and FLASK_SECRET_KEY must be different values')

    database_url = env.get('DATABASE_URL')
    if production and not (database_url or '').strip():
        raise ConfigError('DATABASE_URL must be set')
    if not database_url:
        database_url = 'postgresql://localhost/succulents_price_checker'  # local dev only

    db_ssl = (env.get('DATABASE_SSL') or 'require').strip().lower()
    if db_ssl not in ('require', 'disable'):
        raise ConfigError("DATABASE_SSL must be 'require' or 'disable'")
    if production and db_ssl != 'require':
        raise ConfigError('DATABASE_SSL=disable is only allowed when APP_ENV is development or test')

    if production and env.get('FLASK_DEBUG', '').strip().lower() in ('1', 'true', 'yes'):
        raise ConfigError('FLASK_DEBUG must not be enabled in production')

    try:
        lifetime_h = float(env.get('SESSION_LIFETIME_HOURS') or 12)
    except ValueError:
        raise ConfigError('SESSION_LIFETIME_HOURS must be a number')
    if not (0.05 <= lifetime_h <= 24 * 7):
        raise ConfigError('SESSION_LIFETIME_HOURS must be between 0.05 and 168')

    try:
        proxy_hops = int(env.get('TRUSTED_PROXY_HOPS') or (1 if production else 0))
    except ValueError:
        raise ConfigError('TRUSTED_PROXY_HOPS must be an integer')

    return {
        'APP_ENV': (env.get('APP_ENV') or 'production').strip().lower(),
        'IS_PRODUCTION': production,
        'APP_PASSWORD': password,
        'SECRET_KEY': secret_key,
        'DATABASE_URL': database_url,
        'DATABASE_SSL': db_ssl,
        'DEBUG': False,
        'PROPAGATE_EXCEPTIONS': False,
        'SESSION_COOKIE_NAME': 'sbpc_session',
        'SESSION_COOKIE_HTTPONLY': True,
        'SESSION_COOKIE_SECURE': production,
        'SESSION_COOKIE_SAMESITE': 'Lax',
        'PERMANENT_SESSION_LIFETIME': timedelta(hours=lifetime_h),
        'SESSION_REFRESH_EACH_REQUEST': False,
        'WTF_CSRF_TIME_LIMIT': None,   # bound to the session, which has its own lifetime
        'WTF_CSRF_SSL_STRICT': production,
        'MAX_CONTENT_LENGTH': 64 * 1024,   # no endpoint needs large request bodies
        'TRUSTED_PROXY_HOPS': proxy_hops,
        'LOGIN_MAX_FAILURES_PER_IP': int(env.get('LOGIN_MAX_FAILURES_PER_IP') or 10),
        'LOGIN_MAX_FAILURES_GLOBAL': int(env.get('LOGIN_MAX_FAILURES_GLOBAL') or 100),
        'LOGIN_WINDOW_MINUTES': int(env.get('LOGIN_WINDOW_MINUTES') or 15),
    }
