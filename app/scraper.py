import requests
import time
import re
from datetime import datetime

COMPETITORS = {
    'planet_desert': {
        'name': 'Planet Desert',
        'base_url': 'https://planetdesert.com',
        'collection': 'succulents',
        'platform': 'shopify',
    },
    'mountain_crest': {
        'name': 'Mountain Crest Gardens',
        'base_url': 'https://mountaincrestgardens.com',
        'platform': 'bigcommerce',
        'catalog_url': 'https://mountaincrestgardens.com/explore-all/',
    },
    'house_plant_shop': {
        'name': 'House Plant Shop',
        'base_url': 'https://houseplantshop.com',
        'url_template': 'https://houseplantshop.com/products.json?limit=250&page={page}',
        'platform': 'shopify',
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


def fetch_mcg_bigcommerce():
    """Fetch MCG products via BigCommerce HTML scraping.
    Uses curl-cffi for Chrome TLS fingerprint to bypass Cloudflare bot protection.
    Returns list of standardized product dicts compatible with parse_product."""
    try:
        from curl_cffi import requests as cf_requests
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    MCG_BASE = 'https://mountaincrestgardens.com'
    raw_products = []
    page = 1

    while True:
        url = f'{MCG_BASE}/explore-all/?page={page}'
        try:
            resp = cf_requests.get(
                url,
                impersonate='chrome120',
                timeout=30,
                headers={'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'}
            )
            if resp.status_code != 200:
                break

            soup = BeautifulSoup(resp.text, 'html.parser')

            # BigCommerce Stencil: products are article.card inside .productGrid
            cards = soup.select('article.card')
            if not cards:
                cards = soup.select('.product-item') or soup.select('.productGrid .product')
            if not cards:
                break

            found_any = False
            for card in cards:
                link = (card.select_one('.card-title a') or
                        card.select_one('h3 a') or
                        card.select_one('h4 a') or
                        card.select_one('a[href*="/"]'))
                if not link:
                    continue

                title = link.get_text(strip=True)
                href = link.get('href', '')
                if not href or href.startswith('#'):
                    continue
                if not href.startswith('http'):
                    href = MCG_BASE + href

                if any(x in href for x in ['/explore-all', '/categories', 'javascript']):
                    continue

                slug = href.rstrip('/').split('/')[-1]

                price_el = (card.select_one('.price--withoutTax') or
                            card.select_one('.price') or
                            card.select_one('[data-product-price]'))
                price_text = price_el.get_text(strip=True) if price_el else ''
                price_match = re.search(r'[\d,]+\.?\d*', price_text.replace(',', ''))
                price = float(price_match.group().replace(',', '')) if price_match else 0.0
                is_from = 'from' in price_text.lower()

                if not title or price == 0:
                    continue

                raw_products.append({
                    'id': slug,
                    'title': title,
                    'handle': slug,
                    'product_type': '',
                    'body_html': '',
                    'images': [],
                    'variants': [{
                        'id': f'{slug}-default',
                        'title': 'From' if is_from else 'Default',
                        'price': str(price),
                        'available': True,
                        'sku': '',
                    }],
                    '_url_override': href,
                })
                found_any = True

            if not found_any:
                break

            next_link = (soup.select_one('.pagination-item--next a') or
                         soup.select_one('a[aria-label="Next"]') or
                         soup.select_one('.pagination-next a'))
            if not next_link:
                break

            page += 1
            time.sleep(1.5)

        except Exception:
            break

    return raw_products


def sync_sb_products(db_conn):
    """Fetch all SB products from succulentsbox.com and store/update in db."""
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

            cursor.execute('''
                INSERT INTO sb_products (external_id, title, handle, product_type, url,
                                         price_min, price_max, synced_at, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(external_id) DO UPDATE SET
                    title=EXCLUDED.title,
                    handle=EXCLUDED.handle,
                    product_type=EXCLUDED.product_type,
                    url=EXCLUDED.url,
                    price_min=EXCLUDED.price_min,
                    price_max=EXCLUDED.price_max,
                    synced_at=EXCLUDED.synced_at
            ''', (external_id, title, handle, product_type, url,
                  price_min, price_max, now, now))

            product_id = cursor.execute(
                'SELECT id FROM sb_products WHERE external_id=%s', (external_id,)
            ).fetchone()[0]

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
                                             price, available, sku, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(external_variant_id) DO UPDATE SET
                        variant_title=EXCLUDED.variant_title,
                        price=EXCLUDED.price,
                        available=EXCLUDED.available,
                        sku=EXCLUDED.sku
                ''', (product_id, ext_variant_id, variant_title, price, available, sku, now))

            synced += 1
        except Exception:
            errors += 1

    db_conn.commit()
    return {'synced': synced, 'errors': errors}


def fetch_products(source_key):
    """Fetch all products from a competitor."""
    competitor = COMPETITORS[source_key]

    if competitor.get('platform') == 'bigcommerce':
        return fetch_mcg_bigcommerce()

    if 'url_template' in competitor:
        url_template = competitor['url_template']
    else:
        url_template = (
            f"{competitor['base_url']}/collections/{competitor['collection']}"
            f"/products.json?limit=250&page={{page}}"
        )
    return fetch_shopify_products(url_template)


def parse_product(raw, source_key):
    """Extract standardized fields from a Shopify or BigCommerce product dict."""
    competitor = COMPETITORS[source_key]
    images = raw.get('images', [])
    image_url = images[0]['src'] if images else None
    desc = _strip_html(raw.get('body_html', ''))
    handle = raw.get('handle', '')
    url = raw.get('_url_override') or (f"{competitor['base_url']}/products/{handle}" if handle else None)

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
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(source, external_id) DO UPDATE SET
                        title=EXCLUDED.title,
                        product_type=EXCLUDED.product_type,
                        description=EXCLUDED.description,
                        url=EXCLUDED.url,
                        image_url=EXCLUDED.image_url,
                        collected_at=EXCLUDED.collected_at
                ''', (product['source'], product['external_id'], product['title'],
                      product['handle'], product['product_type'], product['description'],
                      product['url'], product['image_url'], now))

                cursor.execute('SELECT id FROM competitor_products WHERE source=%s AND external_id=%s',
                               (source_key, product['external_id']))
                row = cursor.fetchone()
                if not row:
                    continue
                product_id = row[0]

                for v in variants:
                    cursor.execute('''
                        INSERT INTO competitor_variants
                        (product_id, external_variant_id, variant_title, price, available, sku, collected_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                    ''', (product_id, v['external_variant_id'], v['variant_title'],
                          v['price'], v['available'], v['sku'], now))

                    vrow = cursor.execute(
                        'SELECT id FROM competitor_variants WHERE external_variant_id=%s AND product_id=%s',
                        (v['external_variant_id'], product_id)
                    ).fetchone()
                    if vrow:
                        cursor.execute(
                            'INSERT INTO price_snapshots (variant_id, price, available, captured_at) VALUES (%s, %s, %s, %s)',
                            (vrow[0], v['price'], v['available'], now)
                        )
                count += 1

            db_conn.commit()

            cursor.execute(
                'INSERT INTO collection_log (source, products_found, status, message, ran_at) VALUES (%s, %s, %s, %s, %s)',
                (source_key, count, 'success', f'Collected {count} products', now)
            )
            db_conn.commit()
            results[source_key] = {'status': 'success', 'products': count}

        except Exception as e:
            cursor.execute(
                'INSERT INTO collection_log (source, products_found, status, message, ran_at) VALUES (%s, %s, %s, %s, %s)',
                (source_key, 0, 'error', str(e), datetime.utcnow().isoformat())
            )
            db_conn.commit()
            results[source_key] = {'status': 'error', 'message': str(e)}

    return results
