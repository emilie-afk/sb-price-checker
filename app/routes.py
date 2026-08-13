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
                print(f"[task {task_id}] starting {fn.__name__}", flush=True)
                db = _connect()
                print(f"[task {task_id}] DB connected", flush=True)
                result = fn(db, *args)
                db.commit()
                db.close()
                print(f"[task {task_id}] done: {result}", flush=True)
                _tasks[task_id] = {'status': 'done', 'result': result}
            except Exception as e:
                import traceback
                print(f"[task {task_id}] ERROR: {e}\n{traceback.format_exc()}", flush=True)
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

    mcg_queue = db.execute(
        "SELECT status, message, completed_at FROM scrape_queue WHERE source='mountain_crest' ORDER BY requested_at DESC LIMIT 1"
    ).fetchone()

    # Distinct plant types — split into plants vs non-plants
    _non_plant_keywords = {
        'accessory', 'accessories', 'gift', 'card', 'candle', 'bookmark',
        'calendar', 'organizer', 'planner', 'sticker', 'notepad', 'soap',
        'tray', 'tool', 'supply', 'supplies', 'fertilizer', 'subscription',
        'coloring', 'book', 'greeting', 'custom', 'pot', 'pots', 'planter',
    }
    def _is_plant(pt):
        pt_lower = pt.lower()
        return not any(kw in pt_lower for kw in _non_plant_keywords)

    all_types = [r[0] for r in db.execute(
        "SELECT DISTINCT product_type FROM sb_products WHERE product_type IS NOT NULL AND product_type != '' ORDER BY product_type"
    ).fetchall()]
    plant_types = [pt for pt in all_types if _is_plant(pt)]
    non_plant_types = [pt for pt in all_types if not _is_plant(pt)]

    return render_template('dashboard.html',
        sb_count=sb_count,
        sb_synced=sb_synced,
        products_count=products_count,
        variants_count=variants_count,
        matches_pending=matches_pending,
        matches_accepted=matches_accepted,
        above_market=above, near_market=near, below_market=below,
        by_source=by_source,
        recent_log=recent_log,
        mcg_queue=mcg_queue,
        plant_types=plant_types,
        non_plant_types=non_plant_types)


@bp.route('/products')
def products():
    db = get_db()
    position = request.args.get('position')  # e.g. below_market, above_market, near_market

    base_query = '''
        SELECT p.id, p.title, p.product_type, p.price_min, p.price_max,
               COUNT(CASE WHEN m.status='accepted' THEN 1 END) as accepted,
               COUNT(CASE WHEN m.status='pending' THEN 1 END) as pending,
               MAX(CASE WHEN m.status='accepted' THEN m.market_position END) as position,
               AVG(CASE WHEN m.status='accepted' THEN m.price_diff_pct END) as avg_diff
        FROM sb_products p
        LEFT JOIN matches m ON m.sb_product_id = p.id
        WHERE p.tracked=1
        GROUP BY p.id
    '''
    if position:
        rows = db.execute(
            base_query + " HAVING MAX(CASE WHEN m.status='accepted' THEN m.market_position END) = %s"
                       + " ORDER BY AVG(CASE WHEN m.status='accepted' THEN m.price_diff_pct END) ASC",
            (position,)
        ).fetchall()
    else:
        rows = db.execute(base_query + ' ORDER BY p.title ASC').fetchall()

    return render_template('products.html', products=rows, position_filter=position)


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


@bp.route('/products/<int:product_id>/manual-match', methods=['POST'])
def manual_match(product_id):
    import re as _re
    data = request.get_json(silent=True) or {}
    source = data.get('source', '').strip()
    comp_title = data.get('title', '').strip()
    variant_title = data.get('variant_title', '').strip() or 'Default'
    try:
        price = float(data.get('price', 0))
    except (ValueError, TypeError):
        price = 0.0
    url = data.get('url', '').strip() or None

    if not source or not comp_title or price <= 0:
        return jsonify({'success': False, 'error': 'Missing required fields'}), 400

    db = get_db()
    now = datetime.utcnow().isoformat()

    # Stable external_id so re-entering same product doesn't duplicate
    ext_id = 'manual-' + _re.sub(r'[^a-z0-9]+', '-', comp_title.lower()).strip('-')

    # Upsert competitor product
    row = db.execute('''
        INSERT INTO competitor_products (source, external_id, title, url, collected_at)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT(source, external_id) DO UPDATE SET
            title=EXCLUDED.title, url=COALESCE(EXCLUDED.url, competitor_products.url),
            collected_at=EXCLUDED.collected_at
        RETURNING id
    ''', (source, ext_id, comp_title, url, now)).fetchone()
    comp_product_id = row[0]

    # Upsert variant
    ext_variant_id = f'{ext_id}-{_re.sub(r"[^a-z0-9]+", "-", variant_title.lower())}'
    vrow = db.execute('''
        INSERT INTO competitor_variants (product_id, external_variant_id, variant_title, price, available, collected_at)
        VALUES (%s, %s, %s, %s, 1, %s)
        ON CONFLICT DO NOTHING
        RETURNING id
    ''', (comp_product_id, ext_variant_id, variant_title, price, now)).fetchone()

    if not vrow:
        # Already exists — update price
        vrow = db.execute(
            'SELECT id FROM competitor_variants WHERE external_variant_id=%s', (ext_variant_id,)
        ).fetchone()
        db.execute('UPDATE competitor_variants SET price=%s, collected_at=%s WHERE id=%s',
                   (price, now, vrow[0]))

    variant_id = vrow[0]

    # Record price snapshot
    db.execute('INSERT INTO price_snapshots (variant_id, price, available, captured_at) VALUES (%s,%s,1,%s)',
               (variant_id, price, now))

    # Calculate price diff vs SB
    sb_variants = db.execute(
        'SELECT price FROM sb_variants WHERE product_id=%s AND price>0 AND available=1 ORDER BY price ASC',
        (product_id,)
    ).fetchall()
    sb_price = sb_variants[0][0] if sb_variants else None
    price_diff_pct = ((price - sb_price) / sb_price * 100) if sb_price else None

    if price_diff_pct is None:
        market_pos = 'unknown'
    elif price_diff_pct > 10:
        market_pos = 'above_market'
    elif price_diff_pct < -10:
        market_pos = 'below_market'
    else:
        market_pos = 'near_market'

    # Upsert match (replace if same variant already matched)
    db.execute('''
        INSERT INTO matches
          (sb_product_id, competitor_variant_id, relationship, confidence, status,
           ai_explanation, price_diff_pct, market_position, created_at)
        VALUES (%s, %s, 'comparable', 95, 'accepted', 'Manually added', %s, %s, %s)
        ON CONFLICT DO NOTHING
    ''', (product_id, variant_id, price_diff_pct, market_pos, now))
    db.commit()

    return jsonify({'success': True, 'market_position': market_pos,
                    'price_diff_pct': round(price_diff_pct, 1) if price_diff_pct is not None else None})


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
    position = request.args.get('position')
    where = "WHERE m.status = 'accepted'"
    params = []
    if position:
        where += " AND m.market_position = %s"
        params.append(position)
    rows = db.execute(f'''
        SELECT p.title as sb_title, p.product_type, p.price_min, p.price_max,
               cp.source, cp.title as comp_title, cp.url,
               cv.variant_title, cv.price,
               m.price_diff_pct,
               m.relationship, m.confidence, m.market_position, m.status, m.created_at
        FROM matches m
        JOIN sb_products p ON p.id = m.sb_product_id
        JOIN competitor_variants cv ON cv.id = m.competitor_variant_id
        JOIN competitor_products cp ON cp.id = cv.product_id
        {where}
        ORDER BY m.price_diff_pct ASC
    ''', params or ()).fetchall()

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

    filename = f'sb_pricier.csv' if position == 'below_market' else \
               f'sb_cheaper.csv' if position == 'above_market' else \
               f'near_market.csv' if position == 'near_market' else 'price_comparison.csv'
    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv'
    response.headers['Content-Disposition'] = f'attachment; filename={filename}'
    return response


@bp.route('/sync-sb', methods=['POST'])
def sync_sb():
    task_id = uuid.uuid4().hex[:8]
    _run_in_background(task_id, sync_sb_products)
    return jsonify({'success': True, 'task_id': task_id})


@bp.route('/collect', methods=['POST'])
def collect():
    data = request.get_json(silent=True) or {}
    sources = data.get('sources') or None  # None = all competitors

    def _run(db, src):
        return run_collection(db, sources=src)

    task_id = uuid.uuid4().hex[:8]
    _run_in_background(task_id, _run, sources)
    return jsonify({'success': True, 'task_id': task_id})


@bp.route('/collect/<source>', methods=['POST'])
def collect_source(source):
    """Run collection for a single competitor source."""
    from .scraper import COMPETITORS
    if source not in COMPETITORS:
        return jsonify({'success': False, 'error': f'Unknown source: {source}'}), 400

    def _run_one(db, src):
        from .scraper import run_collection_one
        return run_collection_one(db, src)

    task_id = uuid.uuid4().hex[:8]
    _run_in_background(task_id, _run_one, source)
    return jsonify({'success': True, 'task_id': task_id})


@bp.route('/trigger-mcg', methods=['POST'])
def trigger_mcg():
    db = get_db()
    existing = db.execute(
        "SELECT id, status FROM scrape_queue WHERE source='mountain_crest' AND status IN ('pending','running') LIMIT 1"
    ).fetchone()
    if existing:
        return jsonify({'success': False, 'error': f'Already {existing[1]} — check your Terminal'})
    now = datetime.utcnow().isoformat()
    db.execute(
        "INSERT INTO scrape_queue (source, status, requested_at) VALUES (%s, %s, %s)",
        ('mountain_crest', 'pending', now)
    )
    db.commit()
    return jsonify({'success': True})


@bp.route('/mcg-status')
def mcg_status():
    db = get_db()
    row = db.execute(
        "SELECT status, message, completed_at FROM scrape_queue WHERE source='mountain_crest' ORDER BY requested_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return jsonify({'status': 'idle'})
    return jsonify({'status': row[0], 'message': row[1], 'completed_at': row[2]})


@bp.route('/match', methods=['POST'])
def run_match():
    data = request.get_json(silent=True) or {}
    plant_types = data.get('plant_types') or None  # None = all types
    sources = data.get('sources') or None           # None = all competitors

    def _run(db, pt, src):
        return run_matching(db, plant_types=pt, sources=src)

    task_id = uuid.uuid4().hex[:8]
    _run_in_background(task_id, _run, plant_types, sources)
    return jsonify({'success': True, 'task_id': task_id})
