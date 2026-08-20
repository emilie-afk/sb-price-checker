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
from .matcher import run_matching, _progress as _match_progress

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

    # Count products by the same min-based position rule used on the products page:
    # any competitor cheaper → SB Pricier; all more expensive → SB Cheaper
    _pos_query = '''
        SELECT
          SUM(CASE WHEN min_diff < -10 THEN 1 ELSE 0 END) as below,
          SUM(CASE WHEN min_diff >= -10 AND max_diff > 10 THEN 1 ELSE 0 END) as above,
          SUM(CASE WHEN min_diff >= -10 AND max_diff <= 10 THEN 1 ELSE 0 END) as near
        FROM (
          SELECT MIN(m.price_diff_pct) as min_diff, MAX(m.price_diff_pct) as max_diff
          FROM matches m
          JOIN sb_products p ON p.id = m.sb_product_id
          WHERE m.status='accepted' AND m.price_diff_pct IS NOT NULL
            AND COALESCE(p.in_stock, 1) = 1   -- exclude sold-out products
          GROUP BY m.sb_product_id
        ) sub
    '''
    _pos = db.execute(_pos_query).fetchone()
    below = _pos[0] or 0
    above = _pos[1] or 0
    near  = _pos[2] or 0

    by_source = db.execute('''
        SELECT source, COUNT(*) as products FROM competitor_products GROUP BY source
    ''').fetchall()

    _raw_log = db.execute('''
        SELECT source, products_found, status, message, ran_at
        FROM collection_log ORDER BY ran_at DESC LIMIT 20
    ''').fetchall()

    # Convert ran_at from UTC → Vietnam time (GMT+7) for display
    from datetime import timedelta
    _vn_offset = timedelta(hours=7)
    def _to_vn(ts_str):
        if not ts_str:
            return ts_str
        try:
            dt = datetime.strptime(ts_str[:19], '%Y-%m-%dT%H:%M:%S') + _vn_offset
            return dt.strftime('%Y-%m-%dT%H:%M:%S')
        except Exception:
            return ts_str
    recent_log = [
        (r[0], r[1], r[2], r[3], _to_vn(r[4]))
        for r in _raw_log
    ]

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
        non_plant_types=non_plant_types,
        today_date=(datetime.utcnow() + timedelta(hours=7)).strftime('%Y-%m-%d'))


@bp.route('/products')
def products():
    db = get_db()
    position = request.args.get('position')  # e.g. below_market, above_market, near_market

    # Position rule: if ANY competitor is cheaper (min diff < -10%) → SB Pricier.
    # Only "SB Cheaper" if every competitor with data charges more (min diff > 10%).
    _pos_case = """
        CASE
          WHEN MIN(CASE WHEN m.status='accepted' THEN m.price_diff_pct END) < -10 THEN 'below_market'
          WHEN MAX(CASE WHEN m.status='accepted' THEN m.price_diff_pct END) > 10  THEN 'above_market'
          WHEN COUNT(CASE WHEN m.status='accepted' THEN m.price_diff_pct END) > 0 THEN 'near_market'
          ELSE NULL
        END
    """
    base_query = f'''
        SELECT p.id, p.title, p.product_type, p.price_min, p.price_max,
               COUNT(CASE WHEN m.status='accepted' THEN 1 END) as accepted,
               {_pos_case} as position,
               MIN(CASE WHEN m.status='accepted' THEN m.price_diff_pct END) as min_diff
        FROM sb_products p
        LEFT JOIN matches m ON m.sb_product_id = p.id
        WHERE p.tracked=1
          AND COALESCE(p.in_stock, 1) = 1   -- hide fully sold-out products
        GROUP BY p.id
    '''
    if position:
        rows = db.execute(
            base_query + f" HAVING ({_pos_case}) = %s"
                       + " ORDER BY MIN(CASE WHEN m.status='accepted' THEN m.price_diff_pct END) ASC",
            (position,)
        ).fetchall()
    else:
        rows = db.execute(base_query + ' ORDER BY p.title ASC').fetchall()

    # Fixed competitor display order and labels
    source_labels = {
        'mountain_crest':   'MCG',
        'planet_desert':    'Planet Desert',
        'house_plant_shop': 'House Plant Shop',
        'the_sill':         'The Sill',
        'bloomscape':       'Bloomscape',
    }
    competitor_order = list(source_labels.keys())  # column order

    # Which competitors actually have accepted matches?
    active_sources = {r[0] for r in db.execute(
        "SELECT DISTINCT cp.source FROM matches m "
        "JOIN competitor_variants cv ON cv.id=m.competitor_variant_id "
        "JOIN competitor_products cp ON cp.id=cv.product_id "
        "WHERE m.status='accepted'"
    ).fetchall()}
    competitors = [(src, source_labels[src]) for src in competitor_order if src in active_sources]

    # Per-competitor avg diff: product_id -> {source_key: avg_diff}
    source_diffs_rows = db.execute('''
        SELECT m.sb_product_id, cp.source, AVG(m.price_diff_pct) as avg_diff
        FROM matches m
        JOIN competitor_variants cv ON cv.id = m.competitor_variant_id
        JOIN competitor_products cp ON cp.id = cv.product_id
        JOIN sb_products p ON p.id = m.sb_product_id
        WHERE m.status='accepted' AND m.price_diff_pct IS NOT NULL
          AND COALESCE(p.in_stock, 1) = 1
        GROUP BY m.sb_product_id, cp.source
    ''').fetchall()

    source_diffs = {}   # product_id -> {source_key: diff}
    for r in source_diffs_rows:
        pid, src, diff = r[0], r[1], r[2]
        source_diffs.setdefault(pid, {})[src] = diff

    return render_template('products.html', products=rows, position_filter=position,
                           competitors=competitors, source_diffs=source_diffs)


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
        result = run_collection(db, sources=src)
        # Always auto-refresh price diffs after collection —
        # no need to re-run name matching, just recalculate from current prices
        print('[collect] collection done — refreshing price diffs', flush=True)
        cur = db.cursor()
        cur.execute('''
            UPDATE matches
            SET price_diff_pct = (
                SELECT (cv.price - p.price_min) / p.price_min * 100
                FROM competitor_variants cv
                JOIN sb_products p ON p.id = matches.sb_product_id
                WHERE cv.id = matches.competitor_variant_id
                  AND cv.price > 0 AND p.price_min > 0
            ),
            market_position = CASE
                WHEN (SELECT (cv.price - p.price_min) / p.price_min * 100
                      FROM competitor_variants cv
                      JOIN sb_products p ON p.id = matches.sb_product_id
                      WHERE cv.id = matches.competitor_variant_id
                        AND cv.price > 0 AND p.price_min > 0) > 10  THEN 'above_market'
                WHEN (SELECT (cv.price - p.price_min) / p.price_min * 100
                      FROM competitor_variants cv
                      JOIN sb_products p ON p.id = matches.sb_product_id
                      WHERE cv.id = matches.competitor_variant_id
                        AND cv.price > 0 AND p.price_min > 0) < -10 THEN 'below_market'
                WHEN (SELECT (cv.price - p.price_min) / p.price_min * 100
                      FROM competitor_variants cv
                      JOIN sb_products p ON p.id = matches.sb_product_id
                      WHERE cv.id = matches.competitor_variant_id
                        AND cv.price > 0 AND p.price_min > 0) IS NOT NULL THEN 'near_market'
                ELSE market_position
            END
            WHERE status = 'accepted'
        ''')
        updated = cur.rowcount
        db.commit()
        print(f'[collect] refreshed price diffs for {updated} matches', flush=True)
        result['prices_refreshed'] = updated
        return result

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


@bp.route('/size-gaps')
def size_gaps():
    """Size coverage: which sizes we offer that competitors don't, and vice versa.

    Compares, per matched plant, the set of pot sizes we stock against the
    sizes each competitor stocks for the same plant.
    """
    from .matcher import extract_size, is_comparable_plant
    db = get_db()

    source_labels = {
        'planet_desert': 'Planet Desert',
        'house_plant_shop': 'House Plant Shop',
        'the_sill': 'The Sill',
        'mountain_crest': 'MCG',
    }

    # Our sizes per product (in-stock variants only)
    sb_rows = db.execute('''
        SELECT p.id, p.title, p.product_type, v.variant_title, v.price
        FROM sb_products p
        JOIN sb_variants v ON v.product_id = p.id
        WHERE p.tracked=1 AND COALESCE(p.in_stock,1)=1
          AND v.available=1 AND v.price > 0
    ''').fetchall()

    sb_sizes = {}    # pid -> {size: price}
    sb_titles = {}   # pid -> (title, product_type)
    for pid, title, ptype, vtitle, price in sb_rows:
        if not is_comparable_plant(title, ptype):
            continue
        size = extract_size(vtitle)
        if size is None:
            continue
        sb_titles[pid] = (title, ptype)
        sb_sizes.setdefault(pid, {})[size] = float(price or 0)

    # Competitor sizes per (product, source) via accepted matches
    comp_rows = db.execute('''
        SELECT m.sb_product_id, cp.source, cv.variant_title, cp.title, cv.price
        FROM matches m
        JOIN competitor_variants cv ON cv.id = m.competitor_variant_id
        JOIN competitor_products cp ON cp.id = cv.product_id
        WHERE m.status='accepted' AND cv.available=1 AND cv.price > 0
    ''').fetchall()

    comp_sizes = {}  # pid -> {source: {size: price}}
    comp_variants_seen = 0
    comp_variants_sized = 0
    for pid, source, vtitle, ptitle, price in comp_rows:
        comp_variants_seen += 1
        size = extract_size(vtitle) or extract_size(ptitle)
        if size is None:
            continue
        comp_variants_sized += 1
        comp_sizes.setdefault(pid, {}).setdefault(source, {})[size] = float(price or 0)

    # Build per-plant gap rows
    rows = []
    for pid, sources in comp_sizes.items():
        ours = sb_sizes.get(pid)
        if not ours:
            continue
        title, ptype = sb_titles.get(pid, ('', ''))
        our_set = set(ours)
        theirs_union = set()
        per_source = {}
        for src, sizes in sources.items():
            per_source[src] = sorted(sizes)
            theirs_union |= set(sizes)
        rows.append({
            'id': pid,
            'title': title,
            'product_type': ptype or '',
            'our_sizes': sorted(our_set),
            'per_source': per_source,
            'we_only': sorted(our_set - theirs_union),      # we offer, they don't
            'they_only': sorted(theirs_union - our_set),    # they offer, we don't
            'shared': sorted(our_set & theirs_union),
        })

    # Sort: biggest gaps first
    rows.sort(key=lambda r: (-(len(r['they_only']) + len(r['we_only'])), r['title']))

    # Aggregate: how often is each size missing on our side / their side
    from collections import Counter
    missing_for_us = Counter()    # size -> count of plants where a competitor has it and we don't
    exclusive_to_us = Counter()   # size -> count of plants where only we have it
    for r in rows:
        for s in r['they_only']:
            missing_for_us[s] += 1
        for s in r['we_only']:
            exclusive_to_us[s] += 1

    # Size offering per retailer (across all matched plants)
    size_by_retailer = {}   # label -> Counter(size -> n plants)
    sb_counter = Counter()
    for r in rows:
        for s in r['our_sizes']:
            sb_counter[s] += 1
        for src, sizes in r['per_source'].items():
            label = source_labels.get(src, src)
            size_by_retailer.setdefault(label, Counter())
            for s in sizes:
                size_by_retailer[label][s] += 1
    size_by_retailer = {'Succulents Box': sb_counter, **size_by_retailer}

    all_sizes = sorted({s for c in size_by_retailer.values() for s in c})

    active_sources = sorted({src for r in rows for src in r['per_source']})
    competitors = [(s, source_labels.get(s, s)) for s in active_sources]

    return render_template('size_gaps.html',
        rows=rows,
        competitors=competitors,
        missing_for_us=sorted(missing_for_us.items(), key=lambda x: -x[1]),
        exclusive_to_us=sorted(exclusive_to_us.items(), key=lambda x: -x[1]),
        size_by_retailer=size_by_retailer,
        all_sizes=all_sizes,
        comp_variants_seen=comp_variants_seen,
        comp_variants_sized=comp_variants_sized)


@bp.route('/match-progress/<task_id>')
def match_progress(task_id):
    p = _match_progress.get(task_id)
    if not p:
        return jsonify({'done': 0, 'total': 0, 'pct': 0})
    pct = int(p['done'] / p['total'] * 100) if p['total'] else 0
    return jsonify({**p, 'pct': pct})


@bp.route('/match', methods=['POST'])
def run_match():
    data = request.get_json(silent=True) or {}
    plant_types = data.get('plant_types') or None  # None = all types
    sources = data.get('sources') or None           # None = all competitors
    clear_pending = data.get('clear_pending', False)

    task_id = uuid.uuid4().hex[:8]

    def _run(db, pt, src, do_clear):
        if do_clear:
            cur = db.cursor()
            cur.execute("DELETE FROM matches WHERE status='pending'")
            db.commit()
            print('[match] cleared all pending matches', flush=True)
        return run_matching(db, plant_types=pt, sources=src, task_id=task_id)

    _run_in_background(task_id, _run, plant_types, sources, clear_pending)
    return jsonify({'success': True, 'task_id': task_id})


@bp.route('/refresh-prices', methods=['POST'])
def refresh_prices():
    """Recalculate price_diff_pct for all accepted matches from current prices.

    This is much faster than re-running matching — just one SQL UPDATE.
    Run this after every price collection instead of re-matching from scratch.
    """
    def _run(db):
        cur = db.cursor()
        cur.execute('''
            UPDATE matches
            SET price_diff_pct = (
                SELECT (cv.price - p.price_min) / p.price_min * 100
                FROM competitor_variants cv
                JOIN sb_products p ON p.id = matches.sb_product_id
                WHERE cv.id = matches.competitor_variant_id
                  AND cv.price > 0 AND p.price_min > 0
            ),
            market_position = CASE
                WHEN (
                    SELECT (cv.price - p.price_min) / p.price_min * 100
                    FROM competitor_variants cv
                    JOIN sb_products p ON p.id = matches.sb_product_id
                    WHERE cv.id = matches.competitor_variant_id
                      AND cv.price > 0 AND p.price_min > 0
                ) > 10  THEN 'above_market'
                WHEN (
                    SELECT (cv.price - p.price_min) / p.price_min * 100
                    FROM competitor_variants cv
                    JOIN sb_products p ON p.id = matches.sb_product_id
                    WHERE cv.id = matches.competitor_variant_id
                      AND cv.price > 0 AND p.price_min > 0
                ) < -10 THEN 'below_market'
                WHEN (
                    SELECT (cv.price - p.price_min) / p.price_min * 100
                    FROM competitor_variants cv
                    JOIN sb_products p ON p.id = matches.sb_product_id
                    WHERE cv.id = matches.competitor_variant_id
                      AND cv.price > 0 AND p.price_min > 0
                ) IS NOT NULL THEN 'near_market'
                ELSE market_position
            END
            WHERE status = 'accepted'
        ''')
        updated = cur.rowcount
        db.commit()
        msg = f'Refreshed prices for {updated} matches'
        print(f'[refresh] {msg}', flush=True)
        return msg

    task_id = uuid.uuid4().hex[:8]
    _run_in_background(task_id, _run)
    return jsonify({'success': True, 'task_id': task_id})
