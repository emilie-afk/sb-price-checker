import re
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher
from functools import lru_cache
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
ACCEPT_THRESHOLD = 70

# Products that are not a single comparable plant. An arrangement or gift set
# has no equivalent competitor product, so any price % would be meaningless.
EXCLUDE_KEYWORDS = (
    'arrangement', 'centerpiece', 'bouquet', 'wreath',
    'set of', 'pack of', 'bundle', 'kit', 'collection',
    'gift box', 'gift set', 'gift card', 'subscription',
    'planter', 'pot only', 'container', 'terrarium',
    'wedding', 'favor', 'party', 'birthday',
    'soil', 'fertilizer', 'tool', 'book', 'sticker', 'candle',
    'insurance', 'shipping', 'sample',
)


def is_comparable_plant(title, product_type=None):
    """False if this is an arrangement, gift set, bundle or non-plant item.

    These are excluded from matching entirely — there is no equivalent
    single-plant product at a competitor to compare them against.
    """
    t = (title or '').lower()
    if any(kw in t for kw in EXCLUDE_KEYWORDS):
        return False
    # Multi-packs written as a quantity: '3-pack', '2 pack', 'set of 3',
    # 'pack of 2', '3 pc', 'trio of', 'x2'
    if re.search(r'\b\d+\s*[-–]?\s*(pack|pk|pc|pcs|piece|count|ct)\b', t):
        return False
    if re.search(r'\b(pack|set|box|bundle|lot|trio|duo|pair)\s+of\s+\d+', t):
        return False
    if re.search(r'\b(trio|duo)\b', t):
        return False
    if re.search(r'\bx\s?\d+\b', t):
        return False
    pt = (product_type or '').lower()
    if any(kw in pt for kw in ('gift', 'accessor', 'supply', 'supplies', 'tool',
                                'pot', 'planter', 'subscription', 'book')):
        return False
    return True


def extract_size(text):
    """Extract pot/plant size in inches from a variant or product title.

    Handles: '2 inch', '2"', '2in', '2.5"', '4 in pot'.
    Returns a float (inches) or None if no size is present.
    """
    if not text:
        return None
    t = text.lower()
    m = re.search(r'(\d+(?:\.\d+)?)\s*(?:"|\'\'|inch|inches|in\b)', t)
    if m:
        try:
            size = float(m.group(1))
            # Sanity: plant pots are roughly 1"–14"
            if 1 <= size <= 14:
                return size
        except ValueError:
            pass
    return None


def is_bundle_variant(variant_title):
    """True if a VARIANT is a multi-pack rather than a single plant.

    Succulents Box sells the same plant as '2 INCH' and as '2-PACK - 2"',
    '4-PACK - 2"', '8-PACK - 2"'. A pack price must never be compared against
    a competitor's single plant — that alone produced bogus percentages.
    """
    t = (variant_title or '').lower()
    if re.search(r'\b\d+\s*[-–]?\s*(pack|pk|pc|pcs|piece|count|ct|set|tray)\b', t):
        return True
    if re.search(r'\b(pack|set|tray|bundle|lot)\s*of\s*\d+', t):
        return True
    if re.search(r'\bx\s?\d+\b', t):
        return True
    # Bare multi-unit words — a variant called 'Set' or 'Tray' is never one plant
    if re.search(r'\b(pack|packs|set|sets|tray|trays|bundle|combo|kit|trio|duo)\b', t):
        return True
    return False


def _comparable_sb_price(sb_variants, comp_variant_title, comp_product_title=''):
    """Pick the SB price to compare against a competitor variant.

    Returns (price, size, tier):
      ('exact')  both sides state a size and they agree — most trustworthy
      ('entry')  the competitor publishes no size at all, so we compare
                 entry prices: their listed/'from' price vs our cheapest
                 in-stock variant. Both are "cheapest way to buy this plant".
      (None)     the competitor states a size we don't stock — a genuine
                 mismatch, skip rather than compare 2" against 6".

    Some retailers (Mountain Crest, and any Shopify store using
    'Default Title') never expose a size, so requiring an exact size match
    would silently drop every one of their products.
    """
    # Single plants only — a 4-pack price is not comparable to one plant
    usable = [v for v in sb_variants
              if v.get('available') and v['price'] > 0
              and not is_bundle_variant(v['variant_title'])]
    if not usable:
        return None, None, None

    comp_size = extract_size(comp_variant_title) or extract_size(comp_product_title)

    if comp_size is not None:
        # Nearest SB size within tolerance. Retailers use slightly different
        # pot standards — MCG's 3.5" is the same product tier as a 4" — so
        # allow a small gap rather than dropping the comparison entirely.
        # 0.75" keeps 3.5"~4" and 2"~2.5" while still rejecting 2"~4".
        TOLERANCE = 0.75
        best, best_gap = None, None
        for v in usable:
            vs = extract_size(v['variant_title'])
            if vs is None:
                continue
            gap = abs(vs - comp_size)
            if gap <= TOLERANCE and (best_gap is None or gap < best_gap):
                best, best_gap = v, gap
        if best is not None:
            return best['price'], comp_size, 'exact'
        return None, None, None   # they state a size we don't carry

    # No explicit size. If the title advertises an upsized version, it is NOT
    # an entry-level product — comparing it to our cheapest variant reads as
    # "we're cheaper" when we're simply selling a smaller plant. Skip instead.
    #
    # Only bracketed markers ('[large]', '(XL)') or a trailing '- Large' count,
    # so cultivar names such as Echeveria 'Big Red' are not caught.
    blob = f'{comp_variant_title} {comp_product_title}'.lower()
    _UPSIZED = r'(large|larger|x-?large|xl|extra\s+large|jumbo|giant|oversized)'
    if (re.search(r'[\[\(]\s*' + _UPSIZED + r'\s*[\]\)]', blob)
            or re.search(r'[-–:]\s*' + _UPSIZED + r'\s*$', blob.strip())):
        return None, None, None

    cheapest = min(usable, key=lambda v: v['price'])
    return cheapest['price'], None, 'entry'


def _keyword_search_terms(title):
    words = re.findall(r"[a-zA-Z']+", title.lower())
    return [w for w in words if len(w) >= 4 and w not in STOP_WORDS]


@lru_cache(maxsize=100_000)
def _meaningful_words(title):
    return frozenset(w for w in re.findall(r'[a-z]+', title.lower())
                     if len(w) >= 3 and w not in STOP_WORDS)


# Latin species endings that vary between sellers for the same plant,
# e.g. paradox/paradoxa, cooperi/cooperii, fasciata/fasciatus.
_LATIN_ENDINGS = ('oides', 'ii', 'ae', 'us', 'um', 'is', 'a', 'i', 'e', 'o')


def _latin_stem(word):
    """Trim a variable Latin ending so 'paradox' and 'paradoxa' compare equal."""
    for end in _LATIN_ENDINGS:
        if word.endswith(end) and len(word) - len(end) >= 5:
            return word[:-len(end)]
    return word


@lru_cache(maxsize=100_000)
def _stemmed_words(title):
    return frozenset(_latin_stem(w) for w in _meaningful_words(title))


# Colour/size words that are not distinctive on their own. A shared pair like
# ('dark','rose') must not be treated as identifying the same plant.
DESCRIPTORS = {
    'dark', 'light', 'pale', 'deep', 'bright', 'giant', 'large', 'small',
    'baby', 'mini', 'dwarf', 'tall', 'short', 'wide', 'thin',
    'red', 'blue', 'green', 'gold', 'golden', 'silver', 'orange', 'pink',
    'yellow', 'white', 'black', 'purple', 'violet', 'bronze', 'copper',
    'variegated', 'striped', 'spotted', 'frosted', 'ghost', 'moon', 'rose',
    'star', 'sunset', 'sunrise', 'fire', 'ice', 'snow', 'ruby', 'jade',
    'hybrid', 'limited', 'grafted', 'assorted', 'mixed', 'special',
}


def _ordered_words(title):
    """Meaningful words in title order, Latin-stemmed."""
    words = [w for w in re.findall(r'[a-z]+', (title or '').lower())
             if len(w) >= 3 and w not in STOP_WORDS]
    return [_latin_stem(w) for w in words]


@lru_cache(maxsize=100_000)
def _bigrams(title):
    """Adjacent stemmed word pairs, e.g. 'Crassula ovata Jade' -> {('crassula','ovata'), ...}

    Only distinctive pairs are kept, so the intersection test is a plain
    set operation with no per-pair filtering at compare time.
    """
    ws = _ordered_words(title)
    return frozenset(p for p in ((ws[i], ws[i + 1]) for i in range(len(ws) - 1))
                     if _is_distinctive(p))


def _is_distinctive(pair):
    """True if a word pair is specific enough to identify a plant on its own.

    Requires at least one word that is 5+ characters and not a mere
    colour/size descriptor — so ('mammillaria','plumosa') counts but
    ('dark','rose') does not.
    """
    return any(len(w) >= 5 and w not in DESCRIPTORS for w in pair)


def shares_distinctive_bigram(title_a, title_b):
    """True if both titles contain the same distinctive adjacent word pair.

    This is the strongest practical signal for plants: retailers pad titles
    with different marketing words ('Plant', 'hybrid', '[large]', a common
    name) but the scientific binomial stays adjacent and intact. Catches
    'Haworthia Zebra - Haworthia fasciata Plant' == 'Haworthia fasciata'
    while still rejecting 'Sedum adolphi' vs 'Sedum nussbaumerianum'.
    """
    return not _bigrams(title_a).isdisjoint(_bigrams(title_b))


def _quoted_cultivars(title):
    """All quoted cultivar names in a title, lowercased."""
    return {m.lower().strip() for m in re.findall(r"['\"]([^'\"]{2,})['\"]", title or '')}


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

    # 2. Shared scientific binomial (or other distinctive adjacent pair).
    #    Strongest signal — survives the extra marketing words each retailer
    #    adds ('Plant', 'hybrid', '[large]', a common name alongside the latin).
    if shares_distinctive_bigram(sb_title, comp_title):
        return 90

    # 3. Cultivar guard — only reject when BOTH sides name a cultivar and they
    #    disagree. If the competitor simply doesn't quote one, fall through to
    #    word-overlap scoring rather than hard-rejecting: SB's quotes often hold
    #    a common name ("Feather Cactus") rather than a true cultivar.
    sb_cvs = _quoted_cultivars(sb_title)
    comp_cvs = _quoted_cultivars(comp_title)
    if sb_cvs and comp_cvs:
        sb_cv_words = {w for cv in sb_cvs for w in re.findall(r'[a-z]+', cv)}
        comp_cv_words = {w for cv in comp_cvs for w in re.findall(r'[a-z]+', cv)}
        if not (sb_cv_words & comp_cv_words):
            return 0   # both name a cultivar, and they're different plants

    # 4. Jaccard on meaningful words — computed twice, once on the raw words
    #    and once on Latin-stemmed words, taking whichever agrees more. This
    #    lets 'Rhipsalis Paradox' match 'Rhipsalis paradoxa'.
    #    Cheap set maths, so do it before the costly sequence comparison.
    wa = _meaningful_words(sb_title)
    wb = _meaningful_words(comp_title)
    union = wa | wb
    jaccard = len(wa & wb) / len(union) if union else 0

    sa = _stemmed_words(sb_title)
    sb_ = _stemmed_words(comp_title)
    s_union = sa | sb_
    if s_union:
        jaccard = max(jaccard, len(sa & sb_) / len(s_union))

    # Early exit: even a perfect sequence ratio couldn't reach the threshold
    # from here, so skip SequenceMatcher entirely for hopeless pairs.
    if (jaccard * 0.65 + 0.35) * 100 + 5 < ACCEPT_THRESHOLD:
        return round(jaccard * 65)

    a = re.sub(r'[^a-z0-9 ]', '', sb_title.lower())
    b = re.sub(r'[^a-z0-9 ]', '', comp_title.lower())

    # 5. Sequence ratio
    seq = SequenceMatcher(None, a, b).ratio()

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


def run_diagnostics(db_conn, max_samples=300):
    """Read-only pass that explains WHY candidate pairs were skipped.

    Mirrors run_matching's candidate selection and scoring but writes nothing.
    Returns three views:
      size_gaps    — competitor size vs our nearest size, with counts/samples
      outliers     — accepted-by-name pairs rejected for an extreme price gap
      near_misses  — pairs that scored just below the name threshold
    """
    cursor = db_conn.cursor()

    cursor.execute('SELECT id, title, product_type FROM sb_products '
                   'WHERE tracked=1 AND COALESCE(in_stock,1)=1')
    sb_products = cursor.fetchall()

    cursor.execute('SELECT product_id, variant_title, price, available FROM sb_variants')
    sb_variants_by_product = defaultdict(list)
    for r in cursor.fetchall():
        sb_variants_by_product[r[0]].append(
            {'variant_title': r[1], 'price': float(r[2] or 0), 'available': r[3]})

    cursor.execute('''
        SELECT cp.source, cp.title, cp.product_type, cp.url,
               cv.id, cv.variant_title, cv.price, cv.available
        FROM competitor_products cp
        JOIN competitor_variants cv ON cv.product_id = cp.id
        WHERE cv.price > 0 AND cv.available = 1
    ''')
    all_variants = cursor.fetchall()

    keyword_index = defaultdict(list)
    for row in all_variants:
        for word in re.findall(r"[a-zA-Z']+", row[1].lower()):
            if len(word) >= 4 and word not in STOP_WORDS:
                keyword_index[word].append(row)

    size_gaps = defaultdict(lambda: {'count': 0, 'samples': []})
    outliers, near_misses = [], []

    for sb_row in sb_products:
        sb_id, sb_title, sb_type = sb_row[0], sb_row[1], sb_row[2]
        if not is_comparable_plant(sb_title, sb_type):
            continue
        sb_variants = sb_variants_by_product.get(sb_id, [])
        usable = [v for v in sb_variants if v.get('available') and v['price'] > 0]
        if not usable:
            continue
        our_sizes = sorted({s for s in (extract_size(v['variant_title']) for v in usable)
                            if s is not None})

        terms = list(dict.fromkeys(_keyword_search_terms(sb_title)
                                   + list(synonym_keywords(sb_title))))
        terms.sort(key=lambda t: len(keyword_index.get(t, ())))
        seen, candidates = set(), []
        for term in terms[:8]:
            for row in keyword_index.get(term, []):
                if row[4] not in seen:
                    seen.add(row[4])
                    candidates.append(row)
            if len(candidates) >= 400:
                break

        for row in candidates:
            score = _score_match(sb_title, row[1], sb_type, row[2])

            if score < ACCEPT_THRESHOLD:
                # Only the plausible near-misses are interesting
                if score >= ACCEPT_THRESHOLD - 15 and len(near_misses) < max_samples:
                    near_misses.append({
                        'score': score, 'sb_title': sb_title,
                        'comp_title': row[1], 'source': row[0], 'url': row[3],
                    })
                continue

            if not is_comparable_plant(row[1], row[2]):
                continue

            comp_price = float(row[6] or 0)
            sb_price, matched_size, tier = _comparable_sb_price(sb_variants, row[5], row[1])

            if not sb_price or sb_price <= 0 or comp_price <= 0:
                comp_size = extract_size(row[5]) or extract_size(row[1])
                nearest = None
                if comp_size is not None and our_sizes:
                    nearest = min(our_sizes, key=lambda s: abs(s - comp_size))
                key = (comp_size, nearest)
                g = size_gaps[key]
                g['count'] += 1
                if len(g['samples']) < 5:
                    g['samples'].append({
                        'sb_title': sb_title, 'comp_title': row[1],
                        'source': row[0], 'our_sizes': our_sizes,
                        'comp_price': comp_price, 'url': row[3],
                    })
                continue

            diff = (comp_price - sb_price) / sb_price * 100
            if diff < -75 or diff > 300:
                if len(outliers) < max_samples:
                    outliers.append({
                        'sb_title': sb_title, 'comp_title': row[1], 'source': row[0],
                        'sb_price': sb_price, 'comp_price': comp_price,
                        'diff': diff, 'score': score,
                        'size': matched_size, 'tier': tier, 'url': row[3],
                    })

    gaps = []
    for (comp_size, nearest), g in size_gaps.items():
        gap = abs(comp_size - nearest) if (comp_size is not None and nearest is not None) else None
        gaps.append({'comp_size': comp_size, 'nearest': nearest, 'gap': gap,
                     'count': g['count'], 'samples': g['samples']})
    gaps.sort(key=lambda x: -x['count'])

    outliers.sort(key=lambda x: x['diff'])
    near_misses.sort(key=lambda x: -x['score'])
    return {'size_gaps': gaps, 'outliers': outliers, 'near_misses': near_misses}


def run_assortment_gaps(db_conn):
    """Plants a competitor sells that we don't carry at all.

    This is a NAME-only test on purpose: a product is a gap only if no SB
    product refers to the same plant. Size and price are ignored, so a plant
    we stock in a different size still counts as carried.
    """
    cursor = db_conn.cursor()

    cursor.execute('SELECT id, title, product_type FROM sb_products WHERE tracked=1')
    sb_rows = [r for r in cursor.fetchall() if is_comparable_plant(r[1], r[2])]

    # Index OUR catalogue so we can look up each competitor product against it
    sb_index = defaultdict(list)
    for r in sb_rows:
        terms = set(_keyword_search_terms(r[1])) | synonym_keywords(r[1])
        for w in terms:
            sb_index[w].append(r)

    # One row per competitor PRODUCT (cheapest available variant for context)
    cursor.execute('''
        SELECT cp.source, cp.title, cp.product_type, cp.url, MIN(cv.price) as price
        FROM competitor_products cp
        JOIN competitor_variants cv ON cv.product_id = cp.id
        WHERE cv.price > 0 AND cv.available = 1
        GROUP BY cp.id, cp.source, cp.title, cp.product_type, cp.url
    ''')
    comp_products = cursor.fetchall()

    gaps = defaultdict(list)     # source -> [ {title, url, price} ]
    carried = defaultdict(int)   # source -> count we do carry
    for source, title, ptype, url, price in comp_products:
        if not is_comparable_plant(title, ptype):
            continue

        terms = list(dict.fromkeys(_keyword_search_terms(title)
                                   + list(synonym_keywords(title))))
        terms.sort(key=lambda t: len(sb_index.get(t, ())))
        seen, best = set(), 0
        for term in terms[:8]:
            for r in sb_index.get(term, []):
                if r[0] in seen:
                    continue
                seen.add(r[0])
                s = _score_match(r[1], title, r[2], ptype)
                if s > best:
                    best = s
                    if best >= ACCEPT_THRESHOLD:
                        break
            if best >= ACCEPT_THRESHOLD:
                break

        if best >= ACCEPT_THRESHOLD:
            carried[source] += 1
        else:
            gaps[source].append({'title': title, 'url': url,
                                 'price': float(price or 0), 'best_score': best})

    for src in gaps:
        gaps[src].sort(key=lambda g: g['title'].lower())

    totals = {src: {'gaps': len(gaps.get(src, [])), 'carried': carried.get(src, 0)}
              for src in set(list(gaps.keys()) + list(carried.keys()))}
    return {'gaps': dict(gaps), 'totals': totals}


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

    # Diagnostic: how many variants per source actually state a size?
    # A source with 0% sized variants can only ever produce 'entry' comparisons.
    size_stats = defaultdict(lambda: [0, 0])   # source -> [sized, total]
    for row in all_variants:
        st = size_stats[row[0]]
        st[1] += 1
        if extract_size(row[5]) is not None or extract_size(row[1]) is not None:
            st[0] += 1
    for src, (sized, total) in sorted(size_stats.items()):
        pct = (sized / total * 100) if total else 0
        print(f'[match]   {src}: {sized}/{total} variants state a size ({pct:.0f}%)', flush=True)

    summary = {'matched': 0, 'skipped': 0,
               'exact': 0,             # same stated size
               'entry': 0,            # competitor lists no size, entry prices compared
               'skipped_name': 0,     # title similarity below threshold
               'skipped_size': 0,     # competitor states a size we don't stock
               'skipped_outlier': 0,  # diff outside -75%..+300%
               'excluded_products': 0}
    pending_inserts = []
    total_sb = len(sb_products)
    if task_id:
        _progress[task_id] = {'done': 0, 'total': total_sb, 'matched': 0, 'skipped': 0}

    for i, sb_row in enumerate(sb_products):
        sb_id, sb_title, sb_product_type = sb_row[0], sb_row[1], sb_row[2]

        # Skip arrangements, gift sets, bundles and non-plant items —
        # these have no comparable single-plant product at a competitor.
        if not is_comparable_plant(sb_title, sb_product_type):
            summary['excluded_products'] += 1
            continue

        # Fetch from pre-loaded in-memory dict (no extra DB query per product)
        sb_variants = sb_variants_by_product.get(sb_id, [])

        # Skip products with nothing in stock — we can't be compared on a price
        # we aren't actually selling at.
        if not any(v.get('available') and v['price'] > 0 for v in sb_variants):
            summary['skipped'] += 1
            continue

        # Find candidates via keyword index.
        # Also include synonym words so "Burro's Tail" finds "sedum morganianum" entries.
        search_terms = _keyword_search_terms(sb_title)
        extra_terms = synonym_keywords(sb_title)  # scientific/alt names from synonym groups
        all_terms = list(dict.fromkeys(search_terms + list(extra_terms)))  # deduplicated

        # Search the RAREST terms first. A common genus word like 'echeveria'
        # matches hundreds of variants, so leading with it and then capping the
        # list meant the real match was often never scored at all.
        all_terms.sort(key=lambda t: len(keyword_index.get(t, ())))

        seen_variant_ids = set()
        candidates_raw = []
        MAX_CANDIDATES = 400
        for term in all_terms[:8]:
            for row in keyword_index.get(term, []):
                vid = row[4]
                if vid not in seen_variant_ids and (sb_id, vid) not in existing_matches:
                    seen_variant_ids.add(vid)
                    candidates_raw.append(row)
            if len(candidates_raw) >= MAX_CANDIDATES:
                break

        if not candidates_raw:
            continue

        now = datetime.utcnow().isoformat()

        # Score each candidate with Python similarity
        for row in candidates_raw:
            score = _score_match(sb_title, row[1], sb_product_type, row[2])

            if score < ACCEPT_THRESHOLD:
                summary['skipped'] += 1
                summary['skipped_name'] += 1
                continue

            # Competitor product must also be a single comparable plant
            if not is_comparable_plant(row[1], row[2]):
                summary['skipped'] += 1
                continue

            comp_price = float(row[6] or 0)
            sb_price, matched_size, tier = _comparable_sb_price(sb_variants, row[5], row[1])
            if not sb_price or sb_price <= 0 or comp_price <= 0:
                summary['skipped'] += 1
                summary['skipped_size'] += 1
                continue

            price_diff_pct = (comp_price - sb_price) / sb_price * 100
            if price_diff_pct < -75 or price_diff_pct > 300:
                summary['skipped'] += 1
                summary['skipped_outlier'] += 1
                continue

            if price_diff_pct > 10:
                market_pos = 'above_market'   # competitor costs more → SB cheaper
            elif price_diff_pct < -10:
                market_pos = 'below_market'   # competitor costs less → SB pricier
            else:
                market_pos = 'near_market'

            status = 'accepted'
            # 'exact' = same stated size. 'comparable' = entry-price comparison,
            # shown in the UI so an approximate row is never mistaken for exact.
            relationship = 'exact' if tier == 'exact' else 'comparable'
            summary['exact' if tier == 'exact' else 'entry'] += 1

            method = 'synonym name match' if score == 88 else f'text similarity {score}/100'
            if tier == 'exact':
                reasoning = f'Matched via {method}, same {matched_size:g}" size'
            else:
                reasoning = (f'Matched via {method}; competitor lists no size, '
                             f'compared entry prices')

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

    # Final flush — multi-row INSERT in chunks.
    # pg8000's executemany() does one round-trip per row, so a few thousand
    # matches took minutes. Chunked multi-row VALUES is a single trip each.
    if pending_inserts:
        CHUNK = 300
        for start in range(0, len(pending_inserts), CHUNK):
            chunk = pending_inserts[start:start + CHUNK]
            ph = ', '.join(['(%s,%s,%s,%s,%s,%s,%s,%s,%s)'] * len(chunk))
            flat = [val for row in chunk for val in row]
            cursor.execute(f'''
                INSERT INTO matches
                (sb_product_id, competitor_variant_id, relationship, confidence, status,
                 ai_explanation, price_diff_pct, market_position, created_at)
                VALUES {ph}
            ''', flat)
        db_conn.commit()
        print(f'[match] inserted {len(pending_inserts)} matches', flush=True)

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
