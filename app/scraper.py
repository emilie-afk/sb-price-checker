import requests
import time
import re
from datetime import datetime

COMPETITORS = {
    'planet_desert': {
        'name': 'Planet Desert',
        'base_url': 'https://planetdesert.com',
        'collection': 'succulents',
    },
    'mountain_crest': {
        'name': 'Mountain Crest Gardens',
        'base_url': 'https://mountaincrestgardens.com',
        'collection': 'succulents',
    },
    'house_plant_shop': {
        'name': 'House Plant Shop',
        'base_url': 'https://houseplantshop.com',
        'url_template': 'https://houseplantshop.com/products.json?limit=250&page={page}',
    },
}

SB_URL_TEMPLATE = 'https://succulentsbox.com/products.json?limit=250&page={page}'

HEADERS = {
    'User-Agent': 'SucculentsBox-PriceChecker/1.0 (internal pricing research tool)'
}


def _strip_html(html):
    text = re.sub(r'<[^>]+>', ' ', html or '')
    return re.sub(r'\s+', ' ', text).strip()[:500]


def fetch_shopify_products(url_template):
    """Paginate through a Shopify products.json endpoint. Returns list of raw products."""
    products = []
    page = 1
    while True:
        url = url_template.format(page=page)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            if resp.status_code != 200:
                break
            batch = resp.json().get('products', [])
            if not batch:
                break
            products.extend(batch)
            page += 1
            time.sleep(1)
        except Exception:
            break
    return products


def sync_sb_products(db_conn):
    """Fetch all SB products from succulentsbox.com and store/update in db.
    Returns {'synced': N, 'errors': N}."""
    cursor = db_conn.cursor()
    raw_products = fetch_shopify_products(SB_URL_TEMPLATE)

    synced = 0
    errors = 0
    now = datetime.utcnow().isoformat()

    for raw in raw_products:
        try:
            external_id = str(raw.get('id', ''))
            title = raw.get('title', '').strip()
            handle = raw.get('handle', '')
            product_type = raw.get('product_type', '')
            url = f"https://succulentsbox.com/products/{handle}" if handle else None

            variants = raw.get('variants', [])
            prices = []
            for v in variants:
                try:
                    prices.append(float(v.get('price', 0)))
                except (ValueError, TypeError):
                    pass
            price_min = min(prices) if prices else 0
            price_max = max(prices) if prices else 0

            # Upsert product
            cursor.execute('''
                INSERT INTO sb_products (external_id, title, handle, product_type, url,
                                         price_min, price_max, synced_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(external_id) DO UPDATE SET
                    title=excluded.title,
                    handle=excluded.handle,
                    product_type=excluded.product_type,
                    url=excluded.url,
                    price_min=excluded.price_min,
                    price_max=excluded.price_max,
                    synced_at=excluded.synced_at
            ''', (external_id, title, handle, product_type, url,
                  price_min, price_max, now, now))

            # Get product id
            product_id = cursor.execute(
                'SELECT id FROM sb_products WHERE external_id=?', (external_id,)
            ).fetchone()[0]

            # Upsert variants
            for v in variants:
                ext_variant_id = str(v.get('id', ''))
                variant_title = v.get('title', '')
                try:
                    price = float(v.get('price', 0))
                except (ValueError, TypeError):
                    price = 0.0
                available = 1 if v.get('available', True) else 0
                sku = v.get('sku', '')

                cursor.execute('''
                    INSERT INTO sb_variants (product_id, external_variant_id, variant_title,
                                             price, available, sku)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(external_variant_id) DO UPDATE SET
                        variant_title=excluded.variant_title,
                        price=excluded.price,
                        available=excluded.available,
                        sku=excluded.sku
                ''', (product_id, ext_variant_id, variant_title, price, available, sku))

            synced += 1
        except Exception:
            errors += 1

    db_conn.commit()
    return {'synced': synced, 'errors': errors}


def fetch_products(source_key):
    """Fetch all products from a competitor. Returns list of raw Shopify product dicts."""
    competitor = COMPETITORS[source_key]
    if 'url_template' in competitor:
        url_template = competitor['url_template']
    else:
        url_template = (
            f"{competitor['base_url']}/collections/{competitor['collection']}"
            f"/products.json?limit=250&page={{page}}"
        )
    return fetch_shopify_products(url_template)


def parse_product(raw, source_key):
    """Extract standardized fields from a Shopify product JSON."""
    competitor = COMPETITORS[source_key]
    images = raw.get('images', [])
    image_url = images[0]['src'] if images else None
    desc = _strip_html(raw.get('body_html', ''))
    handle = raw.get('handle', '')
    url = f"{competitor['base_url']}/products/{handle}" if handle else None

    product = {
        'source': source_key,
        'external_id': str(raw.get('id', '')),
        'title': raw.get('title', ''),
        'handle': handle,
        'product_type': raw.get('product_type', ''),
        'description': desc,
        'url': url,
        'image_url': image_url,
    }

    variants = []
    for v in raw.get('variants', []):
        try:
            price = float(v.get('price', 0))
        except (ValueError, TypeError):
            price = 0.0
        variants.append({
            'external_variant_id': str(v.get('id', '')),
            'variant_title': v.get('title', ''),
            'price': price,
            'available': 1 if v.get('available', True) else 0,
            'sku': v.get('sku', ''),
        })

    return product, variants


def run_collection(db_conn):
    """Run full collection for all competitors. Returns summary dict."""
    results = {}
    cursor = db_conn.cursor()

    for source_key in COMPETITORS:
        try:
            raw_products = fetch_products(source_key)
            count = 0
            now = datetime.utcnow().isoformat()

            for raw in raw_products:
                product, variants = parse_product(raw, source_key)

                cursor.execute('''
                    INSERT INTO competitor_products
                    (source, external_id, title, handle, product_type, description, url, image_url, collected_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source, external_id) DO UPDATE SET
                        title=excluded.title,
                        product_type=excluded.product_type,
                        description=excluded.description,
                        url=excluded.url,
                        image_url=excluded.image_url,
                        collected_at=excluded.collected_at
                ''', (product['source'], product['external_id'], product['title'],
                      product['handle'], product['product_type'], product['description'],
                      product['url'], product['image_url'], now))

                cursor.execute('SELECT id FROM competitor_products WHERE source=? AND external_id=?',
                               (source_key, product['external_id']))
                row = cursor.fetchone()
                if not row:
                    continue
                product_id = row[0]

                for v in variants:
                    cursor.execute('''
                        INSERT OR IGNORE INTO competitor_variants
                        (product_id, external_variant_id, variant_title, price, available, sku, collected_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (product_id, v['external_variant_id'], v['variant_title'],
                          v['price'], v['available'], v['sku'], now))

                    vrow = cursor.execute(
                        'SELECT id FROM competitor_variants WHERE external_variant_id=? AND product_id=?',
                        (v['external_variant_id'], product_id)
                    ).fetchone()
                    if vrow:
                        cursor.execute(
                            'INSERT INTO price_snapshots (variant_id, price, available) VALUES (?, ?, ?)',
                            (vrow[0], v['price'], v['available'])
                        )
                count += 1

            db_conn.commit()

            cursor.execute(
                'INSERT INTO collection_log (source, products_found, status, message) VALUES (?, ?, ?, ?)',
                (source_key, count, 'success', f'Collected {count} products')
            )
            db_conn.commit()
            results[source_key] = {'status': 'success', 'products': count}

        except Exception as e:
            cursor.execute(
                'INSERT INTO collection_log (source, products_found, status, message) VALUES (?, ?, ?, ?)',
                (source_key, 0, 'error', str(e))
            )
            db_conn.commit()
            results[source_key] = {'status': 'error', 'message': str(e)}

    return results
