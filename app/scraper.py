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
    except ImportError as e:
        raise RuntimeError(f'curl-cffi/beautifulsoup4 not installed: {e}')

    MCG_BASE = 'https://mountaincrestgardens.com'
    raw_products = []
    page = 1
    last_error = None

    while True:
        url = f'{MCG_BASE}/explore-all/?page={page}'
        try:
            resp = cf_requests.get(
                url,
                impersonate='chrome124',
                timeout=45,
                headers={
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.5',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'DNT': '1',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                }
            )

            if resp.status_code == 403:
                raise RuntimeError(f'MCG blocked by Cloudflare (403) on page {page}. curl-cffi impersonation failed.')
            if resp.status_code != 200:
                raise RuntimeError(f'MCG returned HTTP {resp.status_code} on page {page}')

            soup = BeautifulSoup(resp.text, 'html.parser')

            # Detect Cloudflare challenge page
            if 'challenge' in resp.text.lower() and len(resp.text) < 5000:
                raise RuntimeError('MCG returned Cloudflare challenge page — bot detection triggered')

            # BigCommerce Stencil: products are article.card inside .productGrid
            cards = soup.select('article.card')
            if not cards:
                cards = soup.select('[data-product-id]')
            if not cards:
                cards = soup.select('.productGrid li') or soup.select('.product-item')
            if not cards:
                # Log what we actually got for debugging
                title_tag = soup.find('title')
                page_title = title_tag.get_text() if title_tag else 'unknown'
                raise RuntimeError(f'No product cards found on MCG page {page}. Page title: "{page_title}". HTML snippet: {resp.text[:500]}')

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

        except RuntimeError:
            raise  # Surface these as real errors
        except Exception as e:
            raise RuntimeError(f'MCG scraping failed on page {page}: {e}')

    return raw_products


def sync_sb_products(db_conn):
    """Fetch SB products from succulentsbox.com and sync to db incrementally.

    First run: inserts everything.
    Subsequent runs: only updates prices that changed, inserts genuinely new products/variants.
    """
    cursor = db_conn.cursor()
    raw_products = fetch_shopify_products(SB_URL_TEMPLATE)
    now = datetime.utcnow().isoformat()
    new_products = 0
    price_changes = 0
    errors = 0

    print(f"[sync_sb] fetched {len(raw_products)} products from succulentsbox.com", flush=True)

    for i, raw in enumerate(raw_products):
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

            # Upsert product, get id back
            row = cursor.execute('''
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
                RETURNING id
            ''', (external_id, title, handle, product_type, url,
                  price_min, price_max, now, now)).fetchone()
            product_id = row[0]

            # Load existing variants for this product (one SELECT per product)
            existing_rows = cursor.execute(
                'SELECT id, external_variant_id, price FROM sb_variants WHERE product_id=%s',
                (product_id,)
            ).fetchall()
            existing = {r[1]: (r[0], r[2]) for r in existing_rows}

            new_variants = []
            for v in variants:
                ext_variant_id = str(v.get('id', ''))
                variant_title = v.get('title', '')
                try:
                    price = float(v.get('price', 0))
                except (ValueError, TypeError):
                    price = 0.0
                available = 1 if v.get('available', True) else 0
                sku = v.get('sku', '')

                if ext_variant_id in existing:
                    # Only update if price changed
                    variant_id, old_price = existing[ext_variant_id]
                    if price != old_price:
                        cursor.execute(
                            'UPDATE sb_variants SET price=%s, available=%s WHERE id=%s',
                            (price, available, variant_id)
                        )
                        price_changes += 1
                else:
                    new_variants.append((ext_variant_id, variant_title, price, available, sku))

            if new_variants:
                if not existing:
                    new_products += 1
                ph = ', '.join(['(%s, %s, %s, %s, %s, %s, %s)'] * len(new_variants))
                flat = [val for v in new_variants
                        for val in (product_id, v[0], v[1], v[2], v[3], v[4], now)]
                cursor.execute(
                    f'INSERT INTO sb_variants (product_id, external_variant_id, variant_title, price, available, sku, created_at) VALUES {ph} ON CONFLICT(external_variant_id) DO NOTHING',
                    flat
                )

            if (i + 1) % 50 == 0:
                db_conn.commit()
                print(f"[sync_sb] {i + 1}/{len(raw_products)} done — "
                      f"{new_products} new, {price_changes} price changes so far", flush=True)

        except Exception as e:
            print(f"[sync_sb] error on product {i}: {e}", flush=True)
            errors += 1

    db_conn.commit()
    msg = f'{len(raw_products)} products checked, {new_products} new, {price_changes} price changes'
    print(f"[sync_sb] done: {msg}, errors={errors}", flush=True)
    return {'synced': len(raw_products), 'new': new_products,
            'price_changes': price_changes, 'errors': errors}


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
    """Run incremental collection for all competitors.

    First run: inserts all products and variants, records initial price snapshots.
    Subsequent runs: only updates prices that changed, inserts genuinely new products/variants.
    """
    results = {}
    cursor = db_conn.cursor()

    for source_key in COMPETITORS:
        print(f"[collect] starting {source_key}", flush=True)
        try:
            raw_products = fetch_products(source_key)
            print(f"[collect] {source_key}: fetched {len(raw_products)} products from web", flush=True)
            now = datetime.utcnow().isoformat()
            new_products = 0
            price_changes = 0

            for i, raw in enumerate(raw_products):
                product, variants = parse_product(raw, source_key)

                # Upsert product metadata, get id back in one round trip
                row = cursor.execute('''
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
                    RETURNING id
                ''', (product['source'], product['external_id'], product['title'],
                      product['handle'], product['product_type'], product['description'],
                      product['url'], product['image_url'], now)).fetchone()

                if not row:
                    continue
                product_id = row[0]

                # Load existing variants for this product (one SELECT per product)
                existing_rows = cursor.execute(
                    'SELECT id, external_variant_id, price FROM competitor_variants WHERE product_id=%s',
                    (product_id,)
                ).fetchall()
                # ext_variant_id -> (db_id, stored_price)
                existing = {r[1]: (r[0], r[2]) for r in existing_rows}

                new_variants = []
                for v in variants:
                    ext_id = v['external_variant_id']
                    if ext_id in existing:
                        # Existing variant — only write if price changed
                        variant_id, old_price = existing[ext_id]
                        if v['price'] != old_price:
                            cursor.execute(
                                'UPDATE competitor_variants SET price=%s, available=%s, collected_at=%s WHERE id=%s',
                                (v['price'], v['available'], now, variant_id)
                            )
                            cursor.execute(
                                'INSERT INTO price_snapshots (variant_id, price, available, captured_at) VALUES (%s, %s, %s, %s)',
                                (variant_id, v['price'], v['available'], now)
                            )
                            price_changes += 1
                    else:
                        new_variants.append(v)

                if new_variants:
                    if not existing:
                        new_products += 1  # fully new product
                    # Batch insert new variants, get ids back
                    ph = ', '.join(['(%s, %s, %s, %s, %s, %s, %s)'] * len(new_variants))
                    flat = [val for v in new_variants for val in (
                        product_id, v['external_variant_id'], v['variant_title'],
                        v['price'], v['available'], v['sku'], now
                    )]
                    new_ids = [r[0] for r in cursor.execute(
                        f'INSERT INTO competitor_variants (product_id, external_variant_id, variant_title, price, available, sku, collected_at) VALUES {ph} RETURNING id',
                        flat
                    ).fetchall()]

                    # Batch insert initial price snapshots for new variants
                    if new_ids:
                        snap_ph = ', '.join(['(%s, %s, %s, %s)'] * len(new_ids))
                        snap_vals = [val for vid, v in zip(new_ids, new_variants)
                                     for val in (vid, v['price'], v['available'], now)]
                        cursor.execute(
                            f'INSERT INTO price_snapshots (variant_id, price, available, captured_at) VALUES {snap_ph}',
                            snap_vals
                        )

                # Commit and log progress every 50 products
                if (i + 1) % 50 == 0:
                    db_conn.commit()
                    print(f"[collect] {source_key}: {i + 1}/{len(raw_products)} done — "
                          f"{new_products} new, {price_changes} price changes so far", flush=True)

            db_conn.commit()
            msg = f'{len(raw_products)} products checked, {new_products} new, {price_changes} price changes'
            print(f"[collect] {source_key}: {msg}", flush=True)

            cursor.execute(
                'INSERT INTO collection_log (source, products_found, status, message, ran_at) VALUES (%s, %s, %s, %s, %s)',
                (source_key, len(raw_products), 'success', msg, now)
            )
            db_conn.commit()
            results[source_key] = {'status': 'success', 'products': len(raw_products),
                                    'new': new_products, 'price_changes': price_changes}

        except Exception as e:
            print(f"[collect] {source_key} ERROR: {e}", flush=True)
            import traceback; traceback.print_exc()
            cursor.execute(
                'INSERT INTO collection_log (source, products_found, status, message, ran_at) VALUES (%s, %s, %s, %s, %s)',
                (source_key, 0, 'error', str(e), datetime.utcnow().isoformat())
            )
            db_conn.commit()
            results[source_key] = {'status': 'error', 'message': str(e)}

    return results
