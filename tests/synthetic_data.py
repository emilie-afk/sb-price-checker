"""Synthetic catalogue used by the test-suite and the matching parity check.

Every title, price and URL here is invented. Nothing comes from the real
Succulents Box catalogue, sales data or competitor scrapes.
"""

NOW = '2026-01-01T00:00:00'

# (external_id, title, product_type, [(variant_ext_id, variant_title, price, available)])
SB_PRODUCTS = [
    ('sb-1', "Haworthia Zebra - Haworthia fasciata Plant", 'Succulents',
     [('sb-1-a', '2 INCH', 6.00, 1), ('sb-1-b', '4 INCH', 12.00, 1), ('sb-1-c', '4-PACK - 2"', 20.00, 1)]),
    ('sb-2', "Jade Plant Crassula ovata", 'Succulents',
     [('sb-2-a', '2 INCH', 5.50, 1), ('sb-2-b', '6 INCH', 24.00, 1)]),
    ('sb-3', "String of Pearls Senecio rowleyanus", 'Houseplants',
     [('sb-3-a', '4 INCH', 14.00, 1)]),
    ('sb-4', "Echeveria 'Lola'", 'Succulents',
     [('sb-4-a', '2 INCH', 6.50, 1)]),
    ('sb-5', "Burro's Tail Sedum", 'Succulents',
     [('sb-5-a', '2 INCH', 5.00, 1), ('sb-5-b', '4 INCH', 11.00, 0)]),
    ('sb-6', "Succulent Gift Box Set of 6", 'Gift',
     [('sb-6-a', 'Default Title', 40.00, 1)]),
    ('sb-7', "Rhipsalis Paradox", 'Houseplants',
     [('sb-7-a', '4 INCH', 16.00, 1)]),
    ('sb-8', "Sold Out Hoya kerrii", 'Houseplants',
     [('sb-8-a', '4 INCH', 215.00, 0)]),
    ('sb-9', "Mammillaria plumosa Feather Cactus", 'Cacti',
     [('sb-9-a', '2 INCH', 7.00, 1)]),
    ('sb-10', '=HYPERLINK("https://example.invalid","x") Aloe aristata', 'Succulents',
     [('sb-10-a', '2 INCH', 6.00, 1)]),
]

# (source, external_id, title, product_type, url, [(ext_vid, variant_title, price, available)])
COMPETITOR_PRODUCTS = [
    ('planet_desert', 'pd-1', 'Haworthia fasciata', 'Succulent',
     'https://planetdesert.com/products/haworthia-fasciata',
     [('pd-1-a', '2"', 5.00, 1), ('pd-1-b', '4"', 13.50, 1)]),
    ('planet_desert', 'pd-2', 'Crassula ovata Jade', 'Succulent',
     'https://planetdesert.com/products/crassula-ovata',
     [('pd-2-a', '2"', 4.00, 1)]),
    ('planet_desert', 'pd-3', "Echeveria 'Lola'", 'Succulent',
     'https://planetdesert.com/products/echeveria-lola',
     [('pd-3-a', '2"', 7.50, 1)]),
    ('planet_desert', 'pd-4', 'Mammillaria plumosa', 'Cactus',
     'https://planetdesert.com/products/mammillaria-plumosa',
     [('pd-4-a', '2"', 6.00, 1)]),
    ('planet_desert', 'pd-5', 'Aloe aristata Lace Aloe', 'Succulent',
     'https://planetdesert.com/products/aloe-aristata',
     [('pd-5-a', '2"', 5.00, 1)]),
    ('house_plant_shop', 'hps-1', 'String of Pearls', 'Houseplant',
     'https://houseplantshop.com/products/string-of-pearls',
     [('hps-1-a', '4" Pot', 18.00, 1), ('hps-1-b', '6" Pot', 30.00, 1)]),
    ('house_plant_shop', 'hps-2', 'Rhipsalis paradoxa', 'Houseplant',
     'https://houseplantshop.com/products/rhipsalis-paradoxa',
     [('hps-2-a', 'Default Title', 15.00, 1)]),
    ('house_plant_shop', 'hps-3', 'Monstera deliciosa', 'Houseplant',
     'https://houseplantshop.com/products/monstera',
     [('hps-3-a', '6" Pot', 35.00, 1)]),
    ('the_sill', 'sill-1', 'Jade Plant', 'Plant',
     'https://www.thesill.com/products/jade-plant',
     [('sill-1-a', 'Small', 32.00, 1)]),
    ('the_sill', 'sill-2', 'Burros Tail', 'Plant',
     'https://www.thesill.com/products/burros-tail',
     [('sill-2-a', 'Default Title', 9.00, 1)]),
    ('the_sill', 'sill-3', '<img src=x onerror=alert(1)> Pilea peperomioides', 'Plant',
     'javascript:alert(1)',
     [('sill-3-a', '4"', 25.00, 1)]),
    ('mountain_crest', 'mcg-1', 'Haworthia fasciata [large]', '',
     'https://mountaincrestgardens.com/haworthia-fasciata-large/',
     [('mcg-1-a', 'Default', 9.00, 1)]),
    ('mountain_crest', 'mcg-2', 'Sedum morganianum', '',
     'https://mountaincrestgardens.com/sedum-morganianum/',
     [('mcg-2-a', '2.0" Pot', 4.50, 1)]),
]


def seed(conn):
    """Insert the synthetic catalogue using an open (wrapped) DB connection."""
    cur = conn.cursor()
    for ext, title, ptype, variants in SB_PRODUCTS:
        in_stock = [v[2] for v in variants if v[3]]
        pid = cur.execute(
            'INSERT INTO sb_products (external_id, title, handle, product_type, url, '
            'price_min, price_max, tracked, in_stock, synced_at, created_at) '
            'VALUES (%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s) RETURNING id',
            (ext, title, ext, ptype, f'https://succulentsbox.com/products/{ext}',
             min(in_stock) if in_stock else 0, max(in_stock) if in_stock else 0,
             1 if in_stock else 0, NOW, NOW)).fetchone()[0]
        for vext, vtitle, price, avail in variants:
            cur.execute(
                'INSERT INTO sb_variants (product_id, external_variant_id, variant_title, '
                'price, available, sku, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                (pid, vext, vtitle, price, avail, '', NOW))
    for source, ext, title, ptype, url, variants in COMPETITOR_PRODUCTS:
        pid = cur.execute(
            'INSERT INTO competitor_products (source, external_id, title, handle, '
            'product_type, description, url, image_url, collected_at) '
            'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id',
            (source, ext, title, ext, ptype, '', url, None, NOW)).fetchone()[0]
        for vext, vtitle, price, avail in variants:
            vid = cur.execute(
                'INSERT INTO competitor_variants (product_id, external_variant_id, '
                'variant_title, price, available, sku, collected_at) '
                'VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id',
                (pid, vext, vtitle, price, avail, '', NOW)).fetchone()[0]
            cur.execute('INSERT INTO price_snapshots (variant_id, price, available, captured_at) '
                        'VALUES (%s,%s,%s,%s)', (vid, price, avail, NOW))
    cur.execute("INSERT INTO collection_log (source, products_found, status, message, ran_at) "
                "VALUES ('planet_desert', 5, 'success', 'synthetic', %s)", (NOW,))
    conn.commit()
