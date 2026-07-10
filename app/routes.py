import csv
import io
from datetime import datetime
from flask import (Blueprint, render_template, jsonify, current_app,
                   redirect, url_for, make_response, request)
from .database import get_db, execute_db
from .scraper import run_collection, sync_sb_products
from .matcher import run_matching

bp = Blueprint('main', __name__)


@bp.route('/')
def dashboard():
    db = get_db()

    sb_count = db.execute('SELECT COUNT(*) FROM sb_products WHERE tracked=1').fetchone()[0]
    sb_synced = db.execute("SELECT MAX(synced_at) FROM sb_products WHERE synced_at IS NOT NULL").fetchone()[0]
    products_count = db.execute('SELECT COUNT(*) FROM competitor_products').fetchone()[0]
    variants_count = db.execute('SELECT COUNT(*) FROM competitor_variants').fetchone()[0]

    matches_pending = db.execute("SELECT COUNT(*) FROM matches WHERE status='pending'").fetchone()[0]
    matches_accepted = db.execute("SELECT COUNT(*) FROM matches WHERE status='accepted'").fetchone()[0]

    above = db.execute("SELECT COUNT(DISTINCT sb_product_id) FROM matches WHERE market_position='above_market' AND status='accepted'").fetchone()[0]
    near = db.execute("SELECT COUNT(DISTINCT sb_product_id) FROM matches WHERE market_position='near_market' AND status='accepted'").fetchone()[0]
    below = db.execute("SELECT COUNT(DISTINCT sb_product_id) FROM matches WHERE market_position='below_market' AND status='accepted'").fetchone()[0]

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
    product = db.execute('SELECT * FROM sb_products WHERE id=?', (product_id,)).fetchone()
    if not product:
        return redirect(url_for('main.products'))

    sb_variants = db.execute(
        'SELECT variant_title, price, available FROM sb_variants WHERE product_id=? ORDER BY price ASC',
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
        WHERE m.sb_product_id = ?
        ORDER BY m.confidence DESC
    ''', (product_id,)).fetchall()

    return render_template('product_detail.html',
                           product=product, sb_variants=sb_variants, matches=matches)


@bp.route('/matches/<int:match_id>/accept', methods=['POST'])
def accept_match(match_id):
    db = get_db()
    db.execute("UPDATE matches SET status='accepted', reviewed_at=? WHERE id=?",
               (datetime.utcnow().isoformat(), match_id))
    db.commit()
    return jsonify({'success': True})


@bp.route('/matches/<int:match_id>/reject', methods=['POST'])
def reject_match(match_id):
    db = get_db()
    db.execute("UPDATE matches SET status='rejected', reviewed_at=? WHERE id=?",
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
    db = get_db()
    try:
        result = sync_sb_products(db)
        return jsonify({'success': True, 'result': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/collect', methods=['POST'])
def collect():
    db = get_db()
    try:
        results = run_collection(db)
        return jsonify({'success': True, 'results': results})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@bp.route('/match', methods=['POST'])
def run_match():
    db = get_db()
    try:
        summary = run_matching(db)
        return jsonify({'success': True, 'summary': summary})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})
