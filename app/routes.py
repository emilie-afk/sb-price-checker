import csv
import io
import os
import threading
import uuid
from datetime import datetime
from flask import (Blueprint, render_template, jsonify, current_app,
                   redirect, url_for, make_response, request, session)
from .database import get_db, execute_db, _connect
from .scraper import run_collection, sync_sb_products
from .matcher import run_matching

bp = Blueprint('main', __name__)

# In-memory task tracker for background jobs
_tasks: dict = {}


def _run_in_background(task_id: str, fn, *args):
    """Run fn(*args) in a background thread, storing result in _tasks."""
    app = current_app._get_current_object()

    def worker():
        with app.app_context():
            try:
                db = _connect()
                result = fn(db, *args)
                db.commit()
                db.close()
                _tasks[task_id] = {'status': 'done', 'result': result}
            except Exception as e:
                _tasks[task_id] = {'status': 'error', 'error': str(e)}

    _tasks[task_id] = {'status': 'running'}
    threading.Thread(target=worker, daemon=True).start()


@bp.route('/task-status/<task_id>')
def task_status(task_id):
    return jsonify(_tasks.get(task_id, {'status': 'unknown'}))


@bp.route('/robots.txt')
def robots():
    from flask import Response
    return Response('User-agent: *\nDisallow: /\n', mimetype='text/plain')


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password', '')
        app_password = os.environ.get('APP_PASSWORD', 'succulents2026')
        if password == app_password:
            session['authenticated'] = True
            return redirect(url_for('main.dashboard'))
        return render_template('login.html', error=True)
    return render_template('login.html', error=False)


@bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('main.login'))


@bp.route('/')
def dashboard():
    db = get_db()

    sb_count = db.execute('SELECT COUNT(*) as cnt FROM sb_products WHERE tracked=1').fetchone()[0]
    sb_synced = db.execute("SELECT MAX(synced_at) FROM sb_products WHERE synced_at IS NOT NULL").fetchone()[0]
    products_count = db.execute('SELECT COUNT(*) as cnt FROM competitor_products').fetchone()[0]
    variants_count = db.execute('SELECT COUNT(*) as cnt FROM competitor_variants').fetchone()[0]

    matches_pending = db.execute("SELECT COUNT(*) as cnt FROM matches WHERE status='pending'").fetchone()[0]
    matches_accepted = db.execute("SELECT COUNT(*) as cnt FROM matches WHERE status='accepted'").fetchone()[0]

    above = db.execute("SELECT COUNT(DISTINCT sb_product_id) as cnt FROM matches WHERE market_position='above_market' AND status='accepted'").fetchone()[0]
    near  = db.execute("SELECT COUNT(DISTINCT sb_product_id) as cnt FROM matches WHERE market_position='near_market'  AND status='accepted'").fetchone()[0]
    below = db.execute("SELECT COUNT(DISTINCT sb_product_id) as cnt FROM matches WHERE market_position='below_market' AND status='accepted'").fetchone()[0]

    by_source = db.execute('''
        SELECT source, COUNT(*) as products FROM competitor_products GROUP BY source
    ''').fetchall()

    recent_log = db.execute('''
        SELECT source, products_found, status, message, ran_at
        FROM collection_log ORDER BY ran_at DESC LIMIT 6
    ''').fetchall()

    return render_template('dashboard.html',
        sb_count=sb_count,
        sb_synced=sb_synced,
        products_count=products_count,
        variants_count=variants_count,
        matches_pending=matches_pending,
        matches_accepted=matches_accepted,
        above_market=above, near_market=near, below_market=below,
        by_source=by_source,
        recent_log=recent_log)


@bp.route('/products')
def products():
    db = get_db()
    rows = db.execute('''
        SELECT p.id, p.title, p.product_type, p.price_min, p.price_max,
               COUNT(CASE WHEN m.status='accepted' THEN 1 END) as accepted,
               COUNT(CASE WHEN m.status='pending' THEN 1 END) as pending,
               MAX(CASE WHEN m.status='accepted' THEN m.market_position END) as position,
               AVG(CASE WHEN m.status='accepted' THEN m.price_diff_pct END) as avg_diff
        FROM sb_products p
        LEFT JOIN matches m ON m.sb_product_id = p.id
        WHERE p.tracked=1
        GROUP BY p.id
        ORDER BY p.title ASC
    ''').fetchall()
    return render_template('products.html', products=rows)


@bp.route('/products/<int:product_id>')
def product_detail(product_id):
    db = get_db()
    product = db.execute('SELECT * FROM sb_products WHERE id=%s', (product_id,)).fetchone()
    if not product:
        return redirect(url_for('main.products'))

    sb_variants = db.execute(
        'SELECT variant_title, price, available FROM sb_variants WHERE product_id=%s ORDER BY price ASC',
        (product_id,)
    ).fetchall()

    matches = db.execute('''
        SELECT m.id, m.relationship, m.confidence, m.status, m.ai_explanation,
               m.price_diff_pct, m.market_position, m.created_at,
               cp.title, cp.source, cp.url, cp.image_url,
               cv.variant_title, cv.price, cv.available
        FROM matches m
        JOIN competitor_variants cv ON cv.id = m.competitor_variant_id
        JOIN competitor_products cp ON cp.id = cv.product_id
        WHERE m.sb_product_id = %s
        ORDER BY m.confidence DESC
    ''', (product_id,)).fetchall()

    return render_template('product_detail.html',
                           product=product, sb_variants=sb_variants, matches=matches)


@bp.route('/matches/<int:match_id>/accept', methods=['POST'])
def accept_match(match_id):
    db = get_db()
    db.execute("UPDATE matches SET status='accepted', reviewed_at=%s WHERE id=%s",
               (datetime.utcnow().isoformat(), match_id))
    db.commit()
    return jsonify({'success': True})


@bp.route('/matches/<int:match_id>/reject', methods=['POST'])
def reject_match(match_id):
    db = get_db()
    db.execute("UPDATE matches SET status='rejected', reviewed_at=%s WHERE id=%s",
               (datetime.utcnow().isoformat(), match_id))
    db.commit()
    return jsonify({'success': True})


@bp.route('/review')
def review_queue():
    db = get_db()
    matches = db.execute('''
        SELECT m.id, m.confidence, m.relationship, m.ai_explanation, m.market_position,
               m.price_diff_pct,
               p.title as sb_title, p.price_min, p.price_max,
               cp.title as comp_title, cp.source, cp.url,
               cv.variant_title, cv.price
        FROM matches m
        JOIN sb_products p ON p.id = m.sb_product_id
        JOIN competitor_variants cv ON cv.id = m.competitor_variant_id
        JOIN competitor_products cp ON cp.id = cv.product_id
        WHERE m.status = 'pending'
        ORDER BY m.confidence DESC
    ''').fetchall()
    return render_template('review_queue.html', matches=matches)


@bp.route('/export.csv')
def export_csv():
    db = get_db()
    rows = db.execute('''
        SELECT p.title as sb_title, p.product_type, p.price_min, p.price_max,
               cp.source, cp.title as comp_title, cp.url,
               cv.variant_title, cv.price,
               m.price_diff_pct,
               m.relationship, m.confidence, m.market_position, m.status, m.created_at
        FROM matches m
        JOIN sb_products p ON p.id = m.sb_product_id
        JOIN competitor_variants cv ON cv.id = m.competitor_variant_id
        JOIN competitor_products cp ON cp.id = cv.product_id
        WHERE m.status = 'accepted'
        ORDER BY p.title ASC
    ''').fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['SB Product', 'Type', 'SB Price Min', 'SB Price Max',
                     'Competitor', 'Competitor Product', 'URL',
                     'Comp Variant', 'Comp Price', 'Price Diff %',
                     'Relationship', 'Confidence', 'Market Position', 'Status', 'Matched At'])
    for row in rows:
        writer.writerow([
            row[0], row[1], f'${row[2]:.2f}' if row[2] else '',
            f'${row[3]:.2f}' if row[3] else '',
            row[4], row[5], row[6], row[7],
            f'${row[8]:.2f}' if row[8] else '',
            f'{row[9]:+.1f}%' if row[9] is not None else '',
            row[10], row[11], row[12], row[13], row[14]
        ])

    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv'
    response.headers['Content-Disposition'] = 'attachment; filename=price_comparison.csv'
    return response


@bp.route('/sync-sb', methods=['POST'])
def sync_sb():
    task_id = uuid.uuid4().hex[:8]
    _run_in_background(task_id, sync_sb_products)
    return jsonify({'success': True, 'task_id': task_id})


@bp.route('/collect', methods=['POST'])
def collect():
    task_id = uuid.uuid4().hex[:8]
    _run_in_background(task_id, run_collection)
    return jsonify({'success': True, 'task_id': task_id})


@bp.route('/match', methods=['POST'])
def run_match():
    task_id = uuid.uuid4().hex[:8]
    _run_in_background(task_id, run_matching)
    return jsonify({'success': True, 'task_id': task_id})
