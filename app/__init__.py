import os
from flask import Flask
from .database import init_db

def create_app():
    app = Flask(__name__, template_folder='../templates', static_folder='../static')
    app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-change-in-prod')

    # DB path priority:
    # 1. Explicit env var override (useful for testing)
    # 2. Render persistent disk at /data
    # 3. Local dev: use /tmp so SQLite locking works even on network/NTFS mounts
    if os.environ.get('DATABASE_PATH'):
        db_path = os.environ['DATABASE_PATH']
    elif os.path.isdir('/data'):
        db_path = '/data/price_checker.db'
    else:
        db_path = '/tmp/succulents_price_checker.db'

    app.config['DATABASE'] = db_path

    with app.app_context():
        init_db()

    from .routes import bp
    app.register_blueprint(bp)

    @app.before_request
    def require_login():
        from flask import request, session, redirect, url_for
        if request.endpoint in ('main.login', 'main.logout', 'main.robots', 'static'):
            return
        if not session.get('authenticated'):
            return redirect(url_for('main.login'))

    return app
