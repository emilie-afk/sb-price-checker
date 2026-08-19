import re
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher
from app.plant_names import shares_synonym_group, synonym_keywords

# Words that don't help distinguish plant species
STOP_WORDS = {
    'the', 'a', 'an', 'and', 'or', 'of', 'in', 'for', 'with',
    'plant', 'plants', 'live', 'inch', 'pot', 'set', 'gift', 'card',
    'pack', 'wrapped', 'size', 'mini', 'large', 'small', 'medium',
    'indoor', 'outdoor', 'fresh', 'rooted', 'cutting', 'rare',
    'easy', 'care', 'grow', 'house', 'home', 'garden', 'succulent',
    'cactus', 'tropical', 'beautiful', 'perfect', 'great', 'each',
}

# Minimum score to accept a match — everything below is skipped, nothing goes to review
ACCEPT_THRESHOLD = 82


def _closest_sb_price(sb_variants, comp_variant_title):
    if not sb_variants:
        return None
    comp_lower = (comp_variant_title or '').lower()
    comp_size = re.search(r'(\d+)', comp_lower)
    comp_num = comp_size.group(1) if comp_size else None
    if comp_num:
        for v in sb_variants:
            if comp_num in str(v['variant_title']):
                return v['price']
    available = [v for v in sb_variants if v.get('available') and v['price'] > 0]
    if available:
        return min(available, key=lambda v: v['price'])['price']
    prices = [v['price'] for v in sb_variants if v['price'] > 0]
    return min(prices) if prices else None


def _keyword_search_terms(title):
    words = re.findall(r"[a-zA-Z']+", title.lower())
    return [w for w in words if len(w) >= 4 and w not in STOP_WORDS]


def _meaningful_words(title):
    return {w for w in re.findall(r'[a-z]+', title.lower())
            if len(w) >= 3 and w not in STOP_WORDS}


def _extract_cultivar(title):
    """Extract cultivar name from title, e.g. "Echeveria 'Arco'" → 'arco'.
    Returns lowercase string or None."""
    m = re.search(r"['\"]([^'\"]+)['\"]", title)
    if m:
        return m.group(1).lower().strip()
    return None


def _score_match(sb_title, comp_title, sb_type=None, comp_type=None):
    """Return a 0–100 similarity score for two product titles.

    Combines three signals:
    1. Synonym lookup  — exact cross-name match (e.g. 'Zebra Plant' == 'Haworthia fasciata')
    2. Word overlap (Jaccard) — how many meaningful words are shared
    3. Sequence similarity  — catches partial title matches

    Cultivar guard: if SB title has a cultivar name in quotes (e.g. 'Arco'),
    the competitor must also contain that cultivar name — otherwise score is
    capped at 50 (below threshold) to prevent genus-level false matches.
    """
    # 1. Synonym lookup — if both titles refer to the same plant, score is high
    if shares_synonym_group(sb_title, comp_title):
        return 88  # strong match; will be auto-accepted

    # 2. Cultivar guard — if SB has a specific cultivar, competitor must too
    sb_cultivar = _extract_cultivar(sb_title)
    if sb_cultivar:
        comp_lower = comp_title.lower()
        comp_cultivar = _extract_cultivar(comp_title)
        # Check if cultivar words appear anywhere in competitor title
        cultivar_words = set(re.findall(r'[a-z]+', sb_cultivar))
        comp_words = set(re.findall(r'[a-z]+', comp_lower))
        if not cultivar_words & comp_words:
            return 0  # competitor doesn't have this cultivar — hard skip

    a = re.sub(r'[^a-z0-9 ]', '', sb_title.lower())
    b = re.sub(r'[^a-z0-9 ]', '', comp_title.lower())

    # 3. Sequence ratio
    seq = SequenceMatcher(None, a, b).ratio()

    # 4. Jaccard on meaningful words
    wa = _meaningful_words(sb_title)
    wb = _meaningful_words(comp_title)
    union = wa | wb
    jaccard = len(wa & wb) / len(union) if union else 0

    # Combine: word overlap is the stronger signal for plant names
    score = (jaccard * 0.65 + seq * 0.35) * 100

    # Small bonus if product types match (e.g. both 'Succulent')
    if sb_type and comp_type and sb_type.lower() == comp_type.lower():
        score = min(100, score + 5)

    return round(score)


def _determine_relationship(score, sb_variant_title, comp_variant_title):
    """Decide 'exact' or 'comparable' based on score + size match."""
    if score >= 85:
        sb_nums = set(re.findall(r'\d+', sb_variant_title or ''))
        co_nums = set(re.findall(r'\d+', comp_variant_title or ''))
        if sb_nums and co_nums and sb_nums & co_nums:
            return 'exact'
    return 'comparable'


_progress: dict = {}   # task_id → {done, total, matched, skipped}


def run_matching(db_conn, plant_types=None, sources=None, task_id=None):
    """Match SB products against competitor products using Python similarity.

    plant_types: list of product_type strings to include (None = all)
    sources:     list of competitor source keys to include (None = all)
    """
    cursor = db_conn.cursor()

    cursor.execute('SELECT id, title, product_type FROM sb_products WHERE tracked=1')
    sb_products = cursor.fetchall()

    # Filter by plant type if specified
    if plant_types:
        pt_lower = {pt.lower() for pt in plant_types}
        sb_products = [p for p in sb_products if (p[2] or '').lower() in pt_lower]
        print(f'[match] Filtered to plant types {plant_types}: {len(sb_products)} products', flush=True)

    # Pre-load existing matches to avoid duplicates
    cursor.execute('SELECT sb_product_id, competitor_variant_id FROM matches')
    existing_matches = set((r[0], r[1]) for r in cursor.fetchall())

    # Pre-load ALL SB variants in one query (avoids 1 DB round-trip per product)
    cursor.execute('SELECT product_id, variant_title, price, available FROM sb_variants')
    _all_sb_variants = cursor.fetchall()
    sb_variants_by_product = defaultdict(list)
    for r in _all_sb_variants:
        sb_variants_by_product[r[0]].append(
            {'variant_title': r[1], 'price': float(r[2] or 0), 'available': r[3]}
        )

    # Load all competitor variants into memory
    if sources:
        placeholders = ','.join(['%s'] * len(sources))
        cursor.execute(f'''
            SELECT cp.source, cp.title, cp.product_type, cp.url,
                   cv.id, cv.variant_title, cv.price, cv.available
            FROM competitor_products cp
            JOIN competitor_variants cv ON cv.product_id = cp.id
            WHERE cv.price > 0 AND cv.available = 1
            AND cp.source IN ({placeholders})
        ''', sources)
        print(f'[match] Filtering competitors to: {sources}', flush=True)
    else:
        cursor.execute('''
            SELECT cp.source, cp.title, cp.product_type, cp.url,
                   cv.id, cv.variant_title, cv.price, cv.available
            FROM competitor_products cp
            JOIN competitor_variants cv ON cv.product_id = cp.id
            WHERE cv.price > 0 AND cv.available = 1
        ''')
    all_variants = cursor.fetchall()
    # cols: source[0] title[1] product_type[2] url[3]
    #       cv.id[4] variant_title[5] price[6] available[7]

    # Build keyword index: word -> [variant_row, ...]
    keyword_index = defaultdict(list)
    for row in all_variants:
        for word in re.findall(r"[a-zA-Z']+", row[1].lower()):
            if len(word) >= 4 and word not in STOP_WORDS:
                keyword_index[word].append(row)

    print(f'[match] {len(sb_products)} SB products, {len(existing_matches)} existing matches, '
          f'{len(all_variants)} competitor variants in memory', flush=True)

    summary = {'matched': 0, 'skipped': 0}
    pending_inserts = []
    total_sb = len(sb_products)
    if task_id:
        _progress[task_id] = {'done': 0, 'total': total_sb, 'matched': 0, 'skipped': 0}

    for i, sb_row in enumerate(sb_products):
        sb_id, sb_title, sb_product_type = sb_row[0], sb_row[1], sb_row[2]

        # Skip non-plant products
        skip_types = {'gift cards', 'gift card'}
        if (sb_product_type or '').lower() in skip_types:
            continue
        if any(w in sb_title.lower() for w in ('gift card', 'insurance', 'shipping')):
            continue

        # Fetch from pre-loaded in-memory dict (no extra DB query per product)
        sb_variants = sb_variants_by_product.get(sb_id, [])

        # Find candidates via keyword index.
        # Also include synonym words so "Burro's Tail" finds "sedum morganianum" entries.
        search_terms = _keyword_search_terms(sb_title)
        extra_terms = synonym_keywords(sb_title)  # scientific/alt names from synonym groups
        all_terms = list(dict.fromkeys(search_terms + list(extra_terms)))  # deduplicated

        seen_variant_ids = set()
        candidates_raw = []
        MAX_CANDIDATES = 30
        for term in all_terms[:8]:
            for row in keyword_index.get(term, []):
                vid = row[4]
                if vid not in seen_variant_ids and (sb_id, vid) not in existing_matches:
                    seen_variant_ids.add(vid)
                    candidates_raw.append(row)
                    if len(candidates_raw) >= MAX_CANDIDATES:
                        break  # enough candidates — stop scanning this term
            if len(candidates_raw) >= MAX_CANDIDATES:
                break  # enough across all terms

        if not candidates_raw:
            continue

        now = datetime.utcnow().isoformat()

        # Score each candidate with Python similarity
        for row in candidates_raw:
            score = _score_match(sb_title, row[1], sb_product_type, row[2])

            if score < ACCEPT_THRESHOLD:
                summary['skipped'] += 1
                continue

            status = 'accepted'
            relationship = _determine_relationship(score, '', row[5])

            comp_price = float(row[6] or 0)
            sb_price = _closest_sb_price(sb_variants, row[5])
            price_diff_pct = None
            if sb_price and sb_price > 0 and comp_price > 0:
                price_diff_pct = (comp_price - sb_price) / sb_price * 100
                # Sanity check: >300% more expensive or >75% cheaper almost always
                # means a size mismatch (e.g. 2" cutting vs 6" plant). Skip these.
                if price_diff_pct < -75 or price_diff_pct > 300:
                    summary['skipped'] += 1
                    continue
                if price_diff_pct > 10:
                    market_pos = 'above_market'   # competitor costs more → SB cheaper
                elif price_diff_pct < -10:
                    market_pos = 'below_market'   # competitor costs less → SB pricier
                else:
                    market_pos = 'near_market'
            else:
                market_pos = 'unknown'

            method = 'synonym name match' if score == 88 else f'text similarity score {score}/100'
            reasoning = f'Matched via {method}'

            pending_inserts.append((
                sb_id, row[4], relationship, score, status,
                reasoning, price_diff_pct, market_pos, now
            ))
            existing_matches.add((sb_id, row[4]))
            summary['matched'] += 1

        if task_id:
            _progress[task_id] = {'done': i + 1, 'total': total_sb,
                                   'matched': summary['matched'], 'skipped': summary['skipped']}
        if (i + 1) % 100 == 0:
            print(f'[match] {i+1}/{total_sb} done — '
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

    if task_id and task_id in _progress:
        del _progress[task_id]

    msg = f'matched {summary["matched"]} pairs, skipped {summary["skipped"]}'
    # Log to collection_log so it shows in Recent Activity
    try:
        cursor.execute(
            "INSERT INTO collection_log (source, products_found, status, message, ran_at) VALUES (%s, %s, %s, %s, %s)",
            ('matching', summary['matched'], 'success', msg, now)
        )
        db_conn.commit()
    except Exception:
        pass

    print(f'[match] done: {summary}', flush=True)
    return summary
