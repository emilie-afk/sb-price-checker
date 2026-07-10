import os
from flask import Flask
from .database import init_db

def create_app():
    app = Flask(__name__, template_folder='../templates', static_folder='../static')
    app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-change-in-prod')

    # PostgreSQL connection string (Supabase or any Postgres provider)
    app.config['DATABASE_URL'] = os.environ.get(
        'DATABASE_URL',
        'postgresql://localhost/succulents_price_checker'  # local dev fallback
    )

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
