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


def fetch_shopify_products(url_template, since=None):
    """Paginate through a Shopify products.json endpoint. Returns list of raw products.

    If `since` is an ISO timestamp string, appends updated_at_min so Shopify
    only returns products modified after that time — drastically reducing
    pages fetched on subsequent runs.
    """
    products = []
    page = 1
    since_param = f'&updated_at_min={since}' if since else ''
    if since:
        print(f'[shopify] incremental fetch — only products updated since {since[:19]}', flush=True)
    while True:
        url = url_template.format(page=page) + since_param
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            if resp.status_code != 200:
                break
            batch = resp.json().get('products', [])
            if not batch:
                break
            products.extend(batch)
            print(f'[shopify] page {page}: {len(batch)} products (total {len(products)})', flush=True)
            page += 1
            time.sleep(1)
        except Exception:
            break
    return products


def _parse_mcg_page(html, page, MCG_BASE):
    """Parse a single MCG HTML page, returning (products_list, found_any)."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')
    products = []

    cards = soup.select('article.card')
    if not cards:
        cards = soup.select('[data-product-id]') or soup.select('.productGrid li') or soup.select('.product-item')

    for card in cards:
        # Title: h3.card-title text (a is a sibling of h3, not child of it)
        h3 = card.select_one('h3.card-title') or card.select_one('h3')
        title = h3.get_text(strip=True) if h3 else ''

        # Link: first plain <a> in the card
        link = card.select_one('a[href]')
        if not link:
            continue
        href = link.get('href', '')
        if not href.startswith('http'):
            href = MCG_BASE + href
        if any(x in href for x in ['/explore-all', '/categories', 'javascript', '#']):
            continue
        slug = href.rstrip('/').split('/')[-1]

        if not title:
            title = link.get_text(strip=True)
        if not title:
            continue

        # Price
        price_el = (card.select_one('.price--withoutTax') or
                    card.select_one('.price') or
                    card.select_one('[data-product-price]'))
        price_text = price_el.get_text(strip=True) if price_el else ''
        price_match = re.search(r'[\d]+\.?\d*', price_text.replace(',', ''))
        price = float(price_match.group()) if price_match else 0.0
        is_from = 'from' in price_text.lower()

        if price == 0:
            continue

        products.append({
            'id': slug, 'title': title, 'handle': slug,
            'product_type': '', 'body_html': '', 'images': [],
            'variants': [{'id': f'{slug}-default',
                          'title': 'From' if is_from else 'Default',
                          'price': str(price), 'available': True, 'sku': ''}],
            '_url_override': href,
        })

    # Pagination
    next_link = (soup.select_one('.pagination-item--next a') or
                 soup.select_one('a[aria-label="Next"]') or
                 soup.select_one('.pagination-next a'))
    has_next = bool(next_link)
    return products, has_next


def fetch_mcg_bigcommerce():
    """Fetch MCG products via BigCommerce HTML scraping.
    Tries curl-cffi (Chrome TLS fingerprint) first, then falls back to requests.
    """
    from bs4 import BeautifulSoup

    MCG_BASE = 'https://mountaincrestgardens.com'
    raw_products = []
    page = 1

    # Try curl_cffi with Chrome impersonation first; fall back to plain requests
    def _fetch_page(url):
        try:
            from curl_cffi import requests as cf_requests
            resp = cf_requests.get(
                url,
                impersonate='chrome131',
                timeout=45,
                headers={
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Cache-Control': 'no-cache',
                    'Pragma': 'no-cache',
                    'Upgrade-Insecure-Requests': '1',
                    'Sec-Fetch-Dest': 'document',
                    'Sec-Fetch-Mode': 'navigate',
                    'Sec-Fetch-Site': 'none',
                    'Sec-Fetch-User': '?1',
                }
            )
            if resp.status_code == 200 and len(resp.text) > 10000:
                return resp.status_code, resp.text
        except Exception as e:
            print(f'[mcg] curl_cffi failed: {e}', flush=True)

        # Fallback: plain requests with browser headers
        resp = requests.get(url, timeout=30, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Upgrade-Insecure-Requests': '1',
        })
        return resp.status_code, resp.text

    while True:
        url = f'{MCG_BASE}/explore-all/?page={page}'
        try:
            status, html = _fetch_page(url)

            if status == 403:
                raise RuntimeError(f'MCG blocked (403) on page {page} — Cloudflare is blocking Render\'s server IP')
            if status != 200:
                raise RuntimeError(f'MCG returned HTTP {status} on page {page}')

            soup_title = BeautifulSoup(html, 'html.parser').find('title')
            page_title = soup_title.get_text() if soup_title else 'unknown'

            if 'challenge' in html.lower() and len(html) < 8000:
                raise RuntimeError(f'MCG returned Cloudflare challenge on page {page} (title: "{page_title}")')

            page_products, has_next = _parse_mcg_page(html, page, MCG_BASE)

            if not page_products:
                print(f'[mcg] page {page}: 0 products parsed. title="{page_title}" html_len={len(html)}', flush=True)
                break

            raw_products.extend(page_products)
            print(f'[mcg] page {page}: {len(page_products)} products (total {len(raw_products)})', flush=True)

            if not has_next:
                break

            page += 1
            time.sleep(1.5)

        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f'MCG scraping failed on page {page}: {e}')

    return raw_products


def run_collection_one(db_conn, source_key):
    """Run incremental collection for a single competitor source."""
    return run_collection(db_conn, sources=[source_key])


def sync_sb_products(db_conn):
    """Fetch SB products from succulentsbox.com and sync to db incrementally.

    First run: inserts everything.
    Subsequent runs: only updates prices that changed, inserts genuinely new products/variants.
    """
    cursor = db_conn.cursor()

    # Use last sync time so Shopify only returns products updated since then
    row = cursor.execute(
        "SELECT MAX(synced_at) FROM sb_products WHERE synced_at IS NOT NULL"
    ).fetchone()
    last_sync = row[0] if row and row[0] else None

    raw_products = fetch_shopify_products(SB_URL_TEMPLATE, since=last_sync)
    now = datetime.utcnow().isoformat()
    new_products = 0
    price_changes = 0
    errors = 0

    if last_sync:
        print(f"[sync_sb] {len(raw_products)} products changed since {last_sync[:19]}", flush=True)
    else:
        print(f"[sync_sb] first run — fetched {len(raw_products)} products from succulentsbox.com", flush=True)

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


def fetch_products(source_key, since=None):
    """Fetch products from a competitor. `since` is an ISO timestamp for incremental Shopify fetches."""
    competitor = COMPETITORS[source_key]

    if competitor.get('platform') == 'bigcommerce':
        return fetch_mcg_bigcommerce()  # BigCommerce HTML scrape — no since support

    if 'url_template' in competitor:
        url_template = competitor['url_template']
    else:
        url_template = (
            f"{competitor['base_url']}/collections/{competitor['collection']}"
            f"/products.json?limit=250&page={{page}}"
        )
    return fetch_shopify_products(url_template, since=since)


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


def run_collection(db_conn, sources=None):
    """Run incremental collection for all (or specified) competitors.

    First run: inserts all products and variants, records initial price snapshots.
    Subsequent runs: only updates prices that changed, inserts genuinely new products/variants.
    """
    results = {}
    cursor = db_conn.cursor()

    for source_key in (sources or COMPETITORS):
        print(f"[collect] starting {source_key}", flush=True)
        try:
            # For Shopify sources, fetch only products updated since last successful run
            last_row = cursor.execute(
                "SELECT MAX(ran_at) FROM collection_log WHERE source=%s AND status='success'",
                (source_key,)
            ).fetchone()
            last_collected = last_row[0] if last_row and last_row[0] else None

            raw_products = fetch_products(source_key, since=last_collected)
            if last_collected and COMPETITORS[source_key].get('platform') != 'bigcommerce':
                print(f"[collect] {source_key}: {len(raw_products)} products changed since {last_collected[:19]}", flush=True)
            else:
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
