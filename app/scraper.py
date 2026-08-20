import os
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
    'the_sill': {
        'name': 'The Sill',
        'base_url': 'https://www.thesill.com',
        'url_template': 'https://www.thesill.com/products.json?limit=250&page={page}',
        'platform': 'shopify',
    },
    'bloomscape': {
        'name': 'Bloomscape',
        'base_url': 'https://bloomscape.com',
        'platform': 'manual',  # can't auto-scrape; manual entry only
    },
}

SB_URL_TEMPLATE = 'https://succulentsbox.com/products.json?limit=250&page={page}'

HEADERS = {
    'User-Agent': 'SucculentsBox-PriceChecker/1.0 (internal pricing research tool)'
}


def _strip_html(html):
    text = re.sub(r'<[^>]+>', ' ', html or '')
    return re.sub(r'\s+', ' ', text).strip()[:500]


def bulk_update(cursor, table, rows, columns, cast_types):
    """Update many rows in ONE query using UPDATE ... FROM (VALUES ...).

    pg8000's executemany() sends a separate round-trip per row, which made
    updating a few thousand prices take minutes. This does it in one statement.

    rows:       list of tuples, with the row id LAST
    columns:    column names to set, in tuple order (excluding the trailing id)
    cast_types: postgres casts for each value including the id, e.g.
                ('double precision', 'integer', 'integer')
    """
    if not rows:
        return 0
    CHUNK = 500
    total = 0
    set_clause = ', '.join(f'{c} = v.{c}' for c in columns)
    col_list = ', '.join([*columns, 'row_id'])
    one_row = '(' + ', '.join(f'%s::{t}' for t in cast_types) + ')'
    for start in range(0, len(rows), CHUNK):
        chunk = rows[start:start + CHUNK]
        ph = ', '.join([one_row] * len(chunk))
        flat = [val for r in chunk for val in r]
        cursor.execute(
            f'UPDATE {table} AS t SET {set_clause} '
            f'FROM (VALUES {ph}) AS v({col_list}) '
            f'WHERE t.id = v.row_id',
            flat
        )
        total += len(chunk)
    return total


def _fetch_shopify_page(url_template, page, since_param):
    """Fetch one Shopify products.json page. Returns (page, products list)."""
    url = url_template.format(page=page) + since_param
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            return page, []
        return page, resp.json().get('products', [])
    except Exception:
        return page, []


def fetch_shopify_products(url_template, since=None):
    """Paginate through a Shopify products.json endpoint. Returns list of raw products.

    Pages are fetched concurrently in waves — the public products.json endpoint
    gives no total count, so we request a wave of pages at a time and stop at
    the first empty one. Much faster than a serial loop with a sleep between
    every page.
    """
    from concurrent.futures import ThreadPoolExecutor

    # NOTE: updated_at_min is an Admin API parameter; the public products.json
    # endpoint ignores it, so every run is effectively a full catalog fetch.
    since_param = ''
    products = []
    WAVE = 5          # pages requested in parallel
    page = 1
    while True:
        pages = list(range(page, page + WAVE))
        with ThreadPoolExecutor(max_workers=WAVE) as pool:
            results = list(pool.map(
                lambda p: _fetch_shopify_page(url_template, p, since_param), pages
            ))
        results.sort(key=lambda r: r[0])

        hit_end = False
        for pnum, batch in results:
            if not batch:
                hit_end = True
                break
            products.extend(batch)
        print(f'[shopify] pages {pages[0]}-{pages[-1]}: total {len(products)} products', flush=True)

        if hit_end:
            break
        page += WAVE
    return products


def _parse_mcg_page(html, page, MCG_BASE):
    """Parse a single MCG HTML page, returning (products_list, found_any)."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')
    products = []

    cards = soup.select('article.card')
    if not cards:
        cards = soup.select('[data-product-id]') or soup.select('.productGrid li') or soup.select('.product-item')

    if not cards and page == 1:
        # Debug: save raw HTML so we can inspect the actual structure
        debug_path = os.path.join(os.path.dirname(__file__), '..', 'mcg_debug.html')
        with open(debug_path, 'w', encoding='utf-8') as f:
            f.write(html)
        print(f'[mcg] DEBUG: 0 cards found — saved raw HTML to mcg_debug.html', flush=True)
        print(f'[mcg] DEBUG: HTML preview: {repr(html[:300])}', flush=True)
        # Print all top-level tag names to help identify structure
        body = soup.find('body')
        if body:
            tags = [c.name for c in body.children if hasattr(c, 'name') and c.name]
            print(f'[mcg] DEBUG: body children: {tags[:20]}', flush=True)
        # Show first element matching each candidate selector
        for sel in ['article.card', 'li.product', 'div.product', 'article', '.card',
                    '[class*="product"]', '.productCard', '.grid-item']:
            els = soup.select(sel)
            if els:
                print(f'[mcg] DEBUG: selector {sel!r} → {len(els)} results. First: {str(els[0])[:300]}', flush=True)
                break
        else:
            print(f'[mcg] DEBUG: No known selectors matched. Try checking mcg_debug.html.', flush=True)

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

        # Price. On sale items MCG renders both the sale price and an
        # "MSRP: $X" / struck-through regular price. We want what the customer
        # actually pays, so ignore RRP/MSRP nodes and take the lowest remaining.
        price_els = card.select('.price--withoutTax') or card.select('.price')
        candidates = []
        for el in price_els:
            cls = ' '.join(el.get('class') or [])
            if 'rrp' in cls.lower() or 'non-sale' in cls.lower():
                continue   # struck-through regular price
            txt = el.get_text(strip=True)
            if 'msrp' in txt.lower():
                continue
            m = re.search(r'[\d]+\.?\d*', txt.replace(',', ''))
            if m:
                candidates.append(float(m.group()))

        if not candidates:
            price_el = card.select_one('[data-product-price]')
            txt = price_el.get_text(strip=True) if price_el else ''
            m = re.search(r'[\d]+\.?\d*', txt.replace(',', ''))
            if m:
                candidates.append(float(m.group()))

        price = min(candidates) if candidates else 0.0

        if price == 0:
            continue

        products.append({
            'id': slug, 'title': title, 'handle': slug,
            'product_type': '', 'body_html': '', 'images': [],
            # title is overwritten with the real pot size (e.g. '2.0" Pot')
            # by _fetch_mcg_size_map() after all pages are collected
            'variants': [{'id': f'{slug}-default',
                          'title': 'Default',
                          'price': str(price), 'available': True, 'sku': ''}],
            '_url_override': href,
        })

    # Pagination
    next_link = (soup.select_one('.pagination-item--next a') or
                 soup.select_one('a[aria-label="Next"]') or
                 soup.select_one('.pagination-next a'))
    has_next = bool(next_link)
    return products, has_next


# Mountain Crest exposes a "Product Size" facet on /explore-all/. Each MCG
# product is a single SKU at one fixed pot size (the 3.5" version of a plant is
# a separate listing, usually suffixed "[large]"), so the facet tells us the
# exact size for every product — no need to fetch ~700 product pages.
# Values are the facet labels exactly as MCG spells them, including duplicates
# that differ only by quote style.
MCG_SIZE_FACETS = [
    '1.2" Plug',
    '1.5" Plug',
    '2" Pot',
    '2.0" Pot',
    "2.0'' Pot",
    '2.5" Pot',
    '3.0" Pot',
    '3.5',
    '3.5" Pot',
    "3.5'' Pot",
]


def _normalize_mcg_size(label):
    """Turn a raw MCG facet label into a form extract_size() can read.

    MCG spells the same size several ways ('2.0" Pot', "2.0'' Pot", '2" Pot')
    and one facet is a bare number ('3.5'). Normalize all to '<n>" <kind>'.
    """
    m = re.search(r'(\d+(?:\.\d+)?)', label or '')
    if not m:
        return label
    num = m.group(1)
    kind = 'Plug' if 'plug' in (label or '').lower() else 'Pot'
    return f'{num}" {kind}'


def _fetch_mcg_size_map(fetch_page, MCG_BASE):
    """Return {product_slug: size_label} by walking each Product Size facet.

    Pagination note: MCG's pagination links drop the _bc_fsnf=1 parameter but
    keep the size filter, so we build page URLs as ?Product+Size=...&page=N.
    """
    from bs4 import BeautifulSoup
    from urllib.parse import quote_plus

    size_map = {}
    for label in MCG_SIZE_FACETS:
        encoded = quote_plus(label)
        page = 1
        found_for_label = 0
        while True:
            url = f'{MCG_BASE}/explore-all/?Product+Size={encoded}&page={page}'
            try:
                status, html = fetch_page(url)
                if status != 200 or not html:
                    break
                soup = BeautifulSoup(html, 'html.parser')
                cards = soup.select('article.card') or soup.select('[data-product-id]')
                if not cards:
                    break
                new_on_page = 0
                for card in cards:
                    a = card.select_one('a[href]')
                    if not a:
                        continue
                    href = a.get('href', '')
                    if any(x in href for x in ['/explore-all', '/categories', 'javascript', '#']):
                        continue
                    slug = href.rstrip('/').split('/')[-1]
                    if slug and slug not in size_map:
                        size_map[slug] = _normalize_mcg_size(label)
                        new_on_page += 1
                found_for_label += new_on_page
                # Last page reached when fewer than a full grid came back
                if len(cards) < 36:
                    break
                page += 1
                if page > 30:      # safety valve
                    break
                time.sleep(0.4)
            except Exception as e:
                print(f'[mcg] size facet {label!r} page {page} failed: {e}', flush=True)
                break
        print(f'[mcg] size "{label}": {found_for_label} products', flush=True)
    print(f'[mcg] size map built: {len(size_map)} products have a known pot size', flush=True)
    return size_map


def fetch_mcg_bigcommerce():
    """Fetch MCG products via BigCommerce HTML scraping.
    Tries curl-cffi (Chrome TLS fingerprint) first, then falls back to requests.
    """
    from bs4 import BeautifulSoup

    MCG_BASE = 'https://mountaincrestgardens.com'
    raw_products = []
    page = 1

    # Try curl_cffi with Chrome impersonation first; fall back to plain requests
    def _decode_response(resp):
        """Decode response content, handling gzip/brotli if not auto-decompressed."""
        import gzip as _gzip
        content = resp.content
        # Detect gzip magic bytes (0x1f 0x8b)
        if content[:2] == b'\x1f\x8b':
            try:
                return _gzip.decompress(content).decode('utf-8', errors='replace')
            except Exception:
                pass
        # Detect brotli (try brotli library if available)
        if content[:1] == b'\x1b' or content[:1] == b'\x0b':
            try:
                import brotli
                return brotli.decompress(content).decode('utf-8', errors='replace')
            except Exception:
                pass
        # Fall back to resp.text (already decoded string)
        return resp.text

    def _fetch_page(url):
        try:
            from curl_cffi import requests as cf_requests
            # Do NOT manually set Accept-Encoding — let curl_cffi handle
            # decompression automatically via Chrome impersonation
            resp = cf_requests.get(
                url,
                impersonate='chrome131',
                timeout=45,
                headers={
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Cache-Control': 'no-cache',
                    'Pragma': 'no-cache',
                    'Upgrade-Insecure-Requests': '1',
                    'Sec-Fetch-Dest': 'document',
                    'Sec-Fetch-Mode': 'navigate',
                    'Sec-Fetch-Site': 'none',
                    'Sec-Fetch-User': '?1',
                }
            )
            if resp.status_code == 200:
                html = _decode_response(resp)
                print(f'[mcg] curl_cffi ok: {len(html)} chars, starts: {repr(html[:60])}', flush=True)
                if len(html) > 10000:
                    return resp.status_code, html
        except Exception as e:
            print(f'[mcg] curl_cffi failed: {e}', flush=True)

        # Fallback: plain requests (auto-decompresses gzip/deflate, not brotli)
        resp = requests.get(url, timeout=30, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate',  # no 'br' — requests can't decode brotli
            'Upgrade-Insecure-Requests': '1',
        })
        html = _decode_response(resp)
        print(f'[mcg] requests fallback: {len(html)} chars, starts: {repr(html[:60])}', flush=True)
        return resp.status_code, html

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

    # Enrich with real pot sizes from the Product Size facet, so MCG prices can
    # be compared size-for-size instead of falling back to entry-price matching.
    try:
        size_map = _fetch_mcg_size_map(_fetch_page, MCG_BASE)
        sized = 0
        for p in raw_products:
            label = size_map.get(p['handle'])
            if label:
                # extract_size() reads e.g. '2.0" Pot' -> 2.0
                p['variants'][0]['title'] = label
                sized += 1
        pct = (sized / len(raw_products) * 100) if raw_products else 0
        print(f'[mcg] tagged {sized}/{len(raw_products)} products with a pot size ({pct:.0f}%)',
              flush=True)
    except Exception as e:
        # Size enrichment is best-effort — never fail the whole scrape over it
        print(f'[mcg] size enrichment skipped: {e}', flush=True)

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

    # ---------------------------------------------------------------- #
    # Bulk sync: a handful of queries instead of ~2 per product.        #
    # Every product previously cost an INSERT..RETURNING plus a SELECT, #
    # so 1,000 products meant 2,000+ round-trips to Supabase.           #
    # ---------------------------------------------------------------- #

    # 1. Build the product rows in memory
    product_rows = []      # (external_id, title, handle, ptype, url, min, max, in_stock)
    variants_by_ext = {}   # product external_id -> list of variant dicts
    for i, raw in enumerate(raw_products):
        try:
            external_id = str(raw.get('id', ''))
            if not external_id:
                continue
            title = raw.get('title', '').strip()
            handle = raw.get('handle', '')
            product_type = raw.get('product_type', '')
            url = f"https://succulentsbox.com/products/{handle}" if handle else None

            variants = raw.get('variants', [])
            # Only count IN-STOCK variants toward price_min/price_max.
            # A sold-out variant's price is not a price we can actually be
            # compared on, and including it produced bogus price diffs
            # (e.g. a sold-out $215 Hoya being compared against competitors).
            in_stock_prices = []
            for v in variants:
                try:
                    p = float(v.get('price', 0))
                except (ValueError, TypeError):
                    continue
                if p > 0 and v.get('available', True):
                    in_stock_prices.append(p)

            price_min = min(in_stock_prices) if in_stock_prices else 0
            price_max = max(in_stock_prices) if in_stock_prices else 0
            # in_stock = 0 means every variant is sold out → hidden from reports
            in_stock = 1 if in_stock_prices else 0

            product_rows.append((external_id, title, handle, product_type, url,
                                 price_min, price_max, in_stock))
            variants_by_ext[external_id] = variants
        except Exception as e:
            print(f"[sync_sb] error preparing product {i}: {e}", flush=True)
            errors += 1

    if not product_rows:
        print('[sync_sb] nothing to sync', flush=True)
        return {'synced': 0, 'new': 0, 'price_changes': 0, 'errors': errors}

    # 2. Bulk upsert products in chunks, getting external_id -> id back
    ext_to_id = {}
    CHUNK = 200
    for start in range(0, len(product_rows), CHUNK):
        chunk = product_rows[start:start + CHUNK]
        ph = ', '.join(['(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)'] * len(chunk))
        flat = [val for r in chunk for val in (*r, now, now)]
        rows = cursor.execute(f'''
            INSERT INTO sb_products (external_id, title, handle, product_type, url,
                                     price_min, price_max, in_stock, synced_at, created_at)
            VALUES {ph}
            ON CONFLICT(external_id) DO UPDATE SET
                title=EXCLUDED.title,
                handle=EXCLUDED.handle,
                product_type=EXCLUDED.product_type,
                url=EXCLUDED.url,
                price_min=EXCLUDED.price_min,
                price_max=EXCLUDED.price_max,
                in_stock=EXCLUDED.in_stock,
                synced_at=EXCLUDED.synced_at
            RETURNING id, external_id
        ''', flat).fetchall()
        for r in rows:
            ext_to_id[str(r[1])] = r[0]
        print(f'[sync_sb] upserted {min(start + CHUNK, len(product_rows))}/{len(product_rows)} products',
              flush=True)
    db_conn.commit()

    # 3. Load ALL existing variants for these products in one query
    product_ids = list(ext_to_id.values())
    existing = {}   # external_variant_id -> (id, price, available)
    for start in range(0, len(product_ids), 500):
        chunk = product_ids[start:start + 500]
        ph = ','.join(['%s'] * len(chunk))
        for r in cursor.execute(
            f'SELECT id, external_variant_id, price, available FROM sb_variants WHERE product_id IN ({ph})',
            chunk
        ).fetchall():
            existing[str(r[1])] = (r[0], r[2], r[3])

    # 4. Sort every variant into "needs update" or "needs insert"
    to_update = []   # (price, available, id)
    to_insert = []   # (product_id, ext_id, title, price, available, sku, now)
    for ext_pid, variants in variants_by_ext.items():
        product_id = ext_to_id.get(ext_pid)
        if not product_id:
            continue
        for v in variants:
            ext_vid = str(v.get('id', ''))
            if not ext_vid:
                continue
            try:
                price = float(v.get('price', 0))
            except (ValueError, TypeError):
                price = 0.0
            available = 1 if v.get('available', True) else 0
            if ext_vid in existing:
                vid, old_price, old_avail = existing[ext_vid]
                # Update when price OR stock status changed
                if price != old_price or available != old_avail:
                    to_update.append((price, available, vid))
                    if price != old_price:
                        price_changes += 1
            else:
                to_insert.append((product_id, ext_vid, v.get('title', ''),
                                  price, available, v.get('sku', ''), now))

    # 5. Apply all variant changes in batches
    if to_update:
        bulk_update(cursor, 'sb_variants', to_update,
                    columns=('price', 'available'),
                    cast_types=('double precision', 'integer', 'integer'))
        print(f'[sync_sb] updated {len(to_update)} variants', flush=True)

    if to_insert:
        for start in range(0, len(to_insert), CHUNK):
            chunk = to_insert[start:start + CHUNK]
            ph = ', '.join(['(%s,%s,%s,%s,%s,%s,%s)'] * len(chunk))
            flat = [val for v in chunk for val in v]
            cursor.execute(
                f'INSERT INTO sb_variants (product_id, external_variant_id, variant_title, '
                f'price, available, sku, created_at) VALUES {ph} '
                f'ON CONFLICT(external_variant_id) DO NOTHING', flat
            )
        print(f'[sync_sb] inserted {len(to_insert)} new variants', flush=True)

    # A product is "new" if we just created its first variants
    new_products = len({v[0] for v in to_insert})

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

            # ------------------------------------------------------------ #
            # Bulk collection: a handful of queries instead of ~2 per      #
            # product. Previously each product cost an INSERT..RETURNING   #
            # plus a SELECT, so 900 products meant 1,800+ round-trips.     #
            # ------------------------------------------------------------ #
            CHUNK = 200

            # 1. Parse everything into memory first
            prod_rows = []           # tuples for the products insert
            variants_by_ext = {}     # product external_id -> [variant dicts]
            for raw in raw_products:
                product, variants = parse_product(raw, source_key)
                ext = product['external_id']
                if not ext:
                    continue
                prod_rows.append((
                    product['source'], ext, product['title'], product['handle'],
                    product['product_type'], product['description'],
                    product['url'], product['image_url'], now,
                ))
                variants_by_ext[ext] = variants

            if not prod_rows:
                print(f'[collect] {source_key}: nothing to write', flush=True)
                raise RuntimeError(f'{source_key} returned 0 usable products')

            # 2. Bulk upsert products, getting external_id -> id back
            ext_to_id = {}
            for start in range(0, len(prod_rows), CHUNK):
                chunk = prod_rows[start:start + CHUNK]
                ph = ', '.join(['(%s,%s,%s,%s,%s,%s,%s,%s,%s)'] * len(chunk))
                flat = [val for r in chunk for val in r]
                for r in cursor.execute(f'''
                    INSERT INTO competitor_products
                    (source, external_id, title, handle, product_type, description, url, image_url, collected_at)
                    VALUES {ph}
                    ON CONFLICT(source, external_id) DO UPDATE SET
                        title=EXCLUDED.title,
                        product_type=EXCLUDED.product_type,
                        description=EXCLUDED.description,
                        url=EXCLUDED.url,
                        image_url=EXCLUDED.image_url,
                        collected_at=EXCLUDED.collected_at
                    RETURNING id, external_id
                ''', flat).fetchall():
                    ext_to_id[str(r[1])] = r[0]
                print(f'[collect] {source_key}: upserted '
                      f'{min(start + CHUNK, len(prod_rows))}/{len(prod_rows)} products', flush=True)
            db_conn.commit()

            # 3. Load ALL existing variants for these products in one pass
            product_ids = list(ext_to_id.values())
            existing = {}   # external_variant_id -> (db_id, stored_price)
            for start in range(0, len(product_ids), 500):
                chunk = product_ids[start:start + 500]
                ph = ','.join(['%s'] * len(chunk))
                for r in cursor.execute(
                    f'SELECT id, external_variant_id, price FROM competitor_variants '
                    f'WHERE product_id IN ({ph})', chunk
                ).fetchall():
                    existing[str(r[1])] = (r[0], r[2])

            # 4. Sort every variant into update vs insert
            to_update = []    # (price, available, now, id)
            snap_rows = []    # (variant_id, price, available, now) for known ids
            to_insert = []    # (product_id, ext_vid, title, price, available, sku, now)
            new_meta = {}     # ext_vid -> (price, available) for snapshot after insert
            for ext_pid, variants in variants_by_ext.items():
                product_id = ext_to_id.get(ext_pid)
                if not product_id:
                    continue
                for v in variants:
                    ext_vid = str(v['external_variant_id'])
                    if not ext_vid:
                        continue
                    if ext_vid in existing:
                        vid, old_price = existing[ext_vid]
                        if v['price'] != old_price:
                            to_update.append((v['price'], v['available'], now, vid))
                            snap_rows.append((vid, v['price'], v['available'], now))
                            price_changes += 1
                    else:
                        to_insert.append((product_id, ext_vid, v['variant_title'],
                                          v['price'], v['available'], v['sku'], now))
                        new_meta[ext_vid] = (v['price'], v['available'])

            # 5. Apply price updates in one batch
            if to_update:
                bulk_update(cursor, 'competitor_variants', to_update,
                            columns=('price', 'available', 'collected_at'),
                            cast_types=('double precision', 'integer', 'text', 'integer'))
                print(f'[collect] {source_key}: updated {len(to_update)} prices', flush=True)

            # 6. Insert new variants, collecting their ids for snapshots
            for start in range(0, len(to_insert), CHUNK):
                chunk = to_insert[start:start + CHUNK]
                ph = ', '.join(['(%s,%s,%s,%s,%s,%s,%s)'] * len(chunk))
                flat = [val for v in chunk for val in v]
                for r in cursor.execute(
                    f'INSERT INTO competitor_variants (product_id, external_variant_id, '
                    f'variant_title, price, available, sku, collected_at) VALUES {ph} '
                    f'RETURNING id, external_variant_id', flat
                ).fetchall():
                    meta = new_meta.get(str(r[1]))
                    if meta:
                        snap_rows.append((r[0], meta[0], meta[1], now))
            if to_insert:
                print(f'[collect] {source_key}: inserted {len(to_insert)} new variants', flush=True)

            # 7. Write all price snapshots in batches
            for start in range(0, len(snap_rows), CHUNK):
                chunk = snap_rows[start:start + CHUNK]
                ph = ', '.join(['(%s,%s,%s,%s)'] * len(chunk))
                flat = [val for s in chunk for val in s]
                cursor.execute(
                    f'INSERT INTO price_snapshots (variant_id, price, available, captured_at) '
                    f'VALUES {ph}', flat
                )

            new_products = len({v[0] for v in to_insert})
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
