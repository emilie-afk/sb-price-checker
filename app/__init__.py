import click
from flask import Flask, redirect, request, url_for
from flask_wtf.csrf import CSRFProtect, CSRFError
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import load_settings
from .database import init_db, _connect

csrf = CSRFProtect()


def create_app():
    # Fails closed: raises ConfigError if APP_PASSWORD / FLASK_SECRET_KEY are
    # missing, empty, weak or known placeholders in production.
    settings = load_settings()

    app = Flask(__name__, template_folder='../templates', static_folder='../static')
    app.config.update(settings)

    hops = app.config['TRUSTED_PROXY_HOPS']
    if hops:
        # Render terminates TLS at its proxy; trust exactly that hop for client IP/scheme
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=0)

    with app.app_context():
        init_db()

    from .tasks import recover_interrupted
    recover_interrupted(app)

    from .routes import bp
    app.register_blueprint(bp)

    from . import security

    app.jinja_env.filters['safe_url'] = security.safe_url

    @app.before_request
    def require_login():
        if request.endpoint in security.PUBLIC_ENDPOINTS:
            return None
        if security.session_is_valid():
            return None
        if security.is_api_request():
            return security.json_error('Authentication required', 401)
        nxt = request.full_path if request.query_string else request.path
        return redirect(url_for('main.login', next=security.safe_next(nxt)))

    # Registered after require_login so anonymous calls get a clear 401 first
    csrf.init_app(app)

    @app.after_request
    def harden(response):
        return security.apply_security_headers(response)

    @app.errorhandler(CSRFError)
    def csrf_failed(e):
        if request.endpoint == 'main.login':
            return redirect(url_for('main.login'))
        return security.json_error('Security token missing or expired — reload the page', 400)

    @app.errorhandler(404)
    def not_found(e):
        if security.is_api_request():
            return security.json_error('Not found', 404)
        return 'Not found', 404

    @app.errorhandler(405)
    def not_allowed(e):
        if security.is_api_request():
            return security.json_error('Method not allowed', 405)
        return 'Method not allowed', 405

    @app.errorhandler(413)
    def too_large(e):
        return security.json_error('Request too large', 413)

    @app.errorhandler(500)
    def server_error(e):
        ref = security.error_reference()
        print(f'[error] unhandled exception ref {ref}', flush=True)
        if security.is_api_request():
            return security.json_error(f'Server error (ref {ref})', 500)
        return f'Server error (ref {ref})', 500

    @app.cli.command('revoke-sessions')
    def revoke_sessions():
        """Sign out every browser by bumping the session epoch."""
        db = _connect()
        try:
            security.bump_epoch(db)
        finally:
            db.close()
        click.echo('All existing sessions have been revoked.')

    return app
