import os
import json
import re
from datetime import datetime
from anthropic import Anthropic

client = None


def get_client():
    global client
    if client is None:
        api_key = os.environ.get('ANTHROPIC_API_KEY')
        if not api_key:
            raise ValueError('ANTHROPIC_API_KEY environment variable not set')
        client = Anthropic(api_key=api_key)
    return client


def _format_sb_prices(sb_variants):
    parts = [f'{v["variant_title"]}: ${v["price"]:.2f}' for v in sb_variants if v['price'] > 0]
    return ' | '.join(parts) if parts else 'price unknown'


def _closest_sb_price(sb_variants, comp_variant_title):
    if not sb_variants:
        return None
    comp_lower = comp_variant_title.lower()
    comp_size = re.search(r'(\d+)', comp_lower)
    comp_num = comp_size.group(1) if comp_size else None
    if comp_num:
        for v in sb_variants:
            if comp_num in v['variant_title']:
                return v['price']
    available = [v for v in sb_variants if v.get('available') and v['price'] > 0]
    if available:
        return min(available, key=lambda v: v['price'])['price']
    prices = [v['price'] for v in sb_variants if v['price'] > 0]
    return min(prices) if prices else None


def _keyword_search_terms(title):
    stop = {'the', 'a', 'an', 'and', 'or', 'of', 'in', 'for', 'with', 'plant',
            'live', 'inch', 'pot', 'set', 'gift', 'card', 'pack', 'wrapped'}
    words = re.findall(r"[a-zA-Z']+", title.lower())
    return [w for w in words if len(w) >= 4 and w not in stop]


def classify_batch(sb_product, sb_variants, candidates):
    """Classify all competitor candidates for one SB product in a single Claude call.

    candidates: list of dicts with keys: id, source, title, product_type, description,
                variant_title, price
    Returns: list of result dicts in same order, each with relationship/confidence/reasoning/market_position.
    """
    sb_prices_str = _format_sb_prices(sb_variants)

    candidates_text = '\n'.join(
        f'{i+1}. [{c["source"]}] "{c["title"]}" — {c["variant_title"]} at ${c["price"]:.2f}'
        + (f' ({c["description"][:120]})' if c.get('description') else '')
        for i, c in enumerate(candidates)
    )

    prompt = f"""SB product: "{sb_product['title']}" (type: {sb_product.get('product_type', 'plant')})
SB prices: {sb_prices_str}

Classify each competitor product below. Return a JSON array with one object per item, in order.

{candidates_text}

Return ONLY a JSON array:
[
  {{"relationship":"exact|comparable|category_benchmark|not_comparable","confidence":0-100,"reasoning":"one sentence","market_position":"above_market|near_market|below_market|unknown"}},
  ...
]

Rules:
- exact: same species/cultivar, same size range
- comparable: same plant, different size or slight variety difference
- category_benchmark: similar plant type but clearly different species
- not_comparable: different plant, gift card, insurance, accessory, etc.
- above_market = competitor charges MORE than SB (SB is cheaper)
- below_market = competitor charges LESS than SB (competitor is cheaper)
- near_market = within 10% of nearest SB size price"""

    try:
        response = get_client().messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=150 * len(candidates),
            system='You are a plant product matcher. Return only valid JSON array, no other text.',
            messages=[{'role': 'user', 'content': prompt}]
        )
        text = response.content[0].text.strip()
        match = re.search(r'\[.*\]', text, re.DOTALL)
        results = json.loads(match.group() if match else text)
        if isinstance(results, list) and len(results) == len(candidates):
            return results
        # Pad with fallbacks if lengths don't match
        while len(results) < len(candidates):
            results.append({'relationship': 'not_comparable', 'confidence': 0,
                            'reasoning': 'No result returned', 'market_position': 'unknown'})
        return results[:len(candidates)]
    except Exception as e:
        return [{'relationship': 'not_comparable', 'confidence': 0,
                 'reasoning': f'Classification failed: {e}', 'market_position': 'unknown'}
                for _ in candidates]


def run_matching(db_conn):
    """Match all tracked SB products against collected competitor products."""
    cursor = db_conn.cursor()

    cursor.execute('SELECT id, title, product_type FROM sb_products WHERE tracked=1')
    sb_products = cursor.fetchall()

    # Pre-load all existing matches to skip per-candidate SELECT checks
    cursor.execute('SELECT sb_product_id, competitor_variant_id FROM matches')
    existing_matches = set((r[0], r[1]) for r in cursor.fetchall())
    print(f'[match] {len(sb_products)} SB products, {len(existing_matches)} existing matches', flush=True)

    summary = {'matched': 0, 'skipped': 0, 'errors': 0}
    pending_inserts = []  # batch up inserts, commit every 20

    for i, sb_row in enumerate(sb_products):
        sb_id = sb_row[0]
        sb_title = sb_row[1]
        sb_product_type = sb_row[2]

        skip_types = {'gift cards', 'gift card'}
        if (sb_product_type or '').lower() in skip_types:
            continue
        if any(w in sb_title.lower() for w in ('gift card', 'insurance', 'shipping')):
            continue

        sb_info = {'title': sb_title, 'product_type': sb_product_type or 'plant'}

        sb_variants_rows = cursor.execute(
            'SELECT variant_title, price, available FROM sb_variants WHERE product_id=%s',
            (sb_id,)
        ).fetchall()
        sb_variants = [v._asdict() for v in sb_variants_rows]

        # Find candidate competitor variants by keyword search
        search_terms = _keyword_search_terms(sb_title)
        candidate_ids = set()
        for term in search_terms[:4]:
            cursor.execute('''
                SELECT cv.id
                FROM competitor_products cp
                JOIN competitor_variants cv ON cv.product_id = cp.id
                WHERE LOWER(cp.title) LIKE LOWER(%s)
                AND cv.price > 0 AND cv.available = 1
            ''', (f'%{term}%',))
            for row in cursor.fetchall():
                candidate_ids.add(row[0])

        if not candidate_ids:
            continue

        # Filter out already-matched candidates
        new_candidate_ids = [cid for cid in candidate_ids
                             if (sb_id, cid) not in existing_matches]
        if not new_candidate_ids:
            summary['skipped'] += len(candidate_ids)
            continue

        placeholders = ','.join(['%s'] * len(new_candidate_ids))
        cursor.execute(f'''
            SELECT cp.id, cp.source, cp.title, cp.product_type, cp.description, cp.url,
                   cv.id, cv.variant_title, cv.price, cv.available
            FROM competitor_products cp
            JOIN competitor_variants cv ON cv.product_id = cp.id
            WHERE cv.id IN ({placeholders})
        ''', new_candidate_ids)
        candidates_raw = cursor.fetchall()

        if not candidates_raw:
            continue

        # Build candidate dicts for the batch Claude call (max 20 per call)
        candidates = [{
            'id': c[6], 'source': c[1], 'title': c[2],
            'product_type': c[3], 'description': c[4],
            'variant_title': c[7], 'price': c[8], 'available': c[9],
        } for c in candidates_raw][:20]

        try:
            results = classify_batch(sb_info, sb_variants, candidates)
        except Exception as e:
            summary['errors'] += len(candidates)
            continue

        now = datetime.utcnow().isoformat()
        for c, result in zip(candidates, results):
            relationship = result.get('relationship', 'not_comparable')
            confidence = int(result.get('confidence', 0))
            reasoning = result.get('reasoning', '')
            market_pos = result.get('market_position', 'unknown')

            if relationship == 'not_comparable' or confidence < 60:
                summary['skipped'] += 1
                continue

            status = 'accepted' if confidence >= 90 else 'pending'
            variant_id = c['id']
            sb_price = _closest_sb_price(sb_variants, c['variant_title'])
            price_diff_pct = None
            if sb_price and sb_price > 0:
                price_diff_pct = (c['price'] - sb_price) / sb_price * 100

            pending_inserts.append((sb_id, variant_id, relationship, confidence, status,
                                    reasoning, price_diff_pct, market_pos, now))
            existing_matches.add((sb_id, variant_id))
            summary['matched'] += 1

            # Commit every 20 inserts
            if len(pending_inserts) >= 20:
                cursor._cur.executemany('''
                    INSERT INTO matches
                    (sb_product_id, competitor_variant_id, relationship, confidence, status,
                     ai_explanation, price_diff_pct, market_position, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''', pending_inserts)
                db_conn.commit()
                pending_inserts.clear()

        if (i + 1) % 50 == 0:
            print(f'[match] {i+1}/{len(sb_products)} SB products processed — '
                  f'matched={summary["matched"]} skipped={summary["skipped"]}', flush=True)

    # Final flush
    if pending_inserts:
        cursor._cur.executemany('''
            INSERT INTO matches
            (sb_product_id, competitor_variant_id, relationship, confidence, status,
             ai_explanation, price_diff_pct, market_position, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', pending_inserts)
        db_conn.commit()

    print(f'[match] done: {summary}', flush=True)
    return summary
