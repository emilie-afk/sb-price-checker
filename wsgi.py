import os

from app import create_app

app = create_app()

if __name__ == '__main__':
    # Local development only. Debug mode is never enabled unless APP_ENV is
    # explicitly 'development'; production runs under gunicorn (render.yaml).
    debug = os.environ.get('APP_ENV') == 'development'
    app.run(debug=debug, host='127.0.0.1', port=int(os.environ.get('PORT', 5000)))
