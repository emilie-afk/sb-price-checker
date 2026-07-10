import os
import json
import re
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
    """Format SB variant prices as a readable string, e.g. '4\" Pot: $14.00 | 6\" Pot: $28.00'"""
    parts = [f'{v["variant_title"]}: ${v["price"]:.2f}' for v in sb_variants if v['price'] > 0]
    return ' | '.join(parts) if parts else 'price unknown'


def _closest_sb_price(sb_variants, comp_variant_title):
    """Find the SB variant price that best matches the competitor variant size."""
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


def classify_match(sb_product, sb_variants, product_info, variant_info):
    """Call Claude to classify a potential match. Returns dict with relationship, confidence, reasoning."""
    sb_prices_str = _format_sb_prices(sb_variants)

    prompt = f"""Succulents Box product: "{sb_product['title']}" (type: {sb_product.get('product_type', 'plant')})
SB prices: {sb_prices_str}

Competitor product:
- Source: {product_info['source']}
- Title: {product_info['title']}
- Variant: {variant_info['variant_title']} at ${variant_info['price']:.2f}
- Type: {product_info.get('product_type', 'unknown')}
- Description: {(product_info.get('description') or '')[:300]}

Classify this match and return JSON only:
{{
  "relationship": "exact|comparable|category_benchmark|not_comparable",
  "confidence": 0-100,
  "reasoning": "one concise sentence explaining the match or mismatch",
  "market_position": "above_market|near_market|below_market|unknown"
}}

Rules:
- exact: same specific plant (same species/cultivar, same size range)
- comparable: same plant but different size or slightly different variety
- category_benchmark: similar plant type but clearly a different species
- not_comparable: clearly different plants or non-plant items (gift cards, insurance, etc.)
- confidence 90+ = strong match, 75-89 = likely match, below 75 = weak/wrong
- For market_position: compare competitor price to the closest matching SB size
  - above_market = competitor charges MORE than SB (SB is the better deal)
  - below_market = competitor charges LESS than SB (competitor is the better deal)
  - near_market = within 10% of SB price"""

    try:
        response = get_client().messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=300,
            system='You are a plant product matcher. Return only valid JSON, no other text.',
            messages=[{'role': 'user', 'content': prompt}]
        )
        text = response.content[0].text.strip()
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return json.loads(match.group())
        return json.loads(text)
    except Exception as e:
        return {
            'relationship': 'not_comparable',
            'confidence': 0,
            'reasoning': f'Classification failed: {str(e)}',
            'market_position': 'unknown'
        }


def _keyword_search_terms(title):
    """Extract meaningful search words from a product title."""
    stop = {'the', 'a', 'an', 'and', 'or', 'of', 'in', 'for', 'with', 'plant',
            'live', 'inch', 'pot', 'set', 'gift', 'card', 'pack', 'wrapped'}
    words = re.findall(r"[a-zA-Z']+", title.lower())
    return [w for w in words if len(w) >= 4 and w not in stop]


def run_matching(db_conn):
    """Match all tracked SB products against collected competitor products."""
    cursor = db_conn.cursor()

    cursor.execute('SELECT id, title, product_type FROM sb_products WHERE tracked=1')
    sb_products = cursor.fetchall()

    summary = {'matched': 0, 'skipped': 0, 'errors': 0}

    for sb_row in sb_products:
        sb_id, sb_title, sb_product_type = sb_row

        # Skip non-plant products
        skip_types = {'gift cards', 'gift card'}
        if (sb_product_type or '').lower() in skip_types:
            continue
        if any(w in sb_title.lower() for w in ('gift card', 'insurance', 'shipping')):
            continue

        sb_info = {'title': sb_title, 'product_type': sb_product_type or 'plant'}

        # Get SB variants for price comparison
        sb_variants_rows = cursor.execute(
            'SELECT variant_title, price, available FROM sb_variants WHERE product_id=?',
            (sb_id,)
        ).fetchall()
        sb_variants = [dict(v) for v in sb_variants_rows]

        search_terms = _keyword_search_terms(sb_title)
        candidate_ids = set()
        for term in search_terms[:4]:
            cursor.execute('''
                SELECT cv.id
                FROM competitor_products cp
                JOIN competitor_variants cv ON cv.product_id = cp.id
                WHERE LOWER(cp.title) LIKE LOWER(?)
                AND cv.price > 0 AND cv.available = 1
            ''', (f'%{term}%',))
            for row in cursor.fetchall():
                candidate_ids.add(row[0])

        if not candidate_ids:
            continue

        placeholders = ','.join('?' * len(candidate_ids))
        cursor.execute(f'''
            SELECT cp.id, cp.source, cp.title, cp.product_type, cp.description, cp.url,
                   cv.id, cv.variant_title, cv.price, cv.available
            FROM competitor_products cp
            JOIN competitor_variants cv ON cv.product_id = cp.id
            WHERE cv.id IN ({placeholders})
        ''', list(candidate_ids))
        candidates = cursor.fetchall()

        for c in candidates:
            (prod_id, source, comp_title, prod_type, desc, url,
             variant_id, variant_title, price, available) = c

            cursor.execute(
                'SELECT id FROM matches WHERE sb_product_id=? AND competitor_variant_id=?',
                (sb_id, variant_id)
            )
            if cursor.fetchone():
                summary['skipped'] += 1
                continue

            product_info = {'source': source, 'title': comp_title,
                            'product_type': prod_type, 'description': desc}
            variant_info = {'variant_title': variant_title, 'price': price}

            try:
                result = classify_match(sb_info, sb_variants, product_info, variant_info)

                relationship = result.get('relationship', 'not_comparable')
                confidence = int(result.get('confidence', 0))
                reasoning = result.get('reasoning', '')
                market_pos = result.get('market_position', 'unknown')

                if relationship == 'not_comparable' or confidence < 60:
                    summary['skipped'] += 1
                    continue

                status = 'accepted' if confidence >= 90 else 'pending'

                # price_diff_pct: (competitor - SB) / SB * 100
                # Positive = competitor more expensive than SB
                # Negative = competitor cheaper than SB
                sb_price = _closest_sb_price(sb_variants, variant_title)
                price_diff_pct = None
                if sb_price and sb_price > 0:
                    price_diff_pct = (price - sb_price) / sb_price * 100

                cursor.execute('''
                    INSERT INTO matches
                    (sb_product_id, competitor_variant_id, relationship, confidence, status,
                     ai_explanation, price_diff_pct, market_position, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (sb_id, variant_id, relationship, confidence, status,
                      reasoning, price_diff_pct, market_pos))
                db_conn.commit()
                summary['matched'] += 1

            except Exception:
                summary['errors'] += 1

    return summary
