"""
Plant name synonyms — common name ↔ scientific name mappings.

Each group is a set of equivalent names for the same plant.
All entries must be lowercase. Add more as you find mismatches.
"""

SYNONYM_GROUPS = [

    # ── Aloe ──────────────────────────────────────────────────────────────────
    {'aloe vera', 'true aloe', 'medicinal aloe', 'burn plant', 'first aid plant',
     'barbados aloe', 'aloe barbadensis'},
    {'aloe aristata', 'lace aloe', 'torch aloe', 'guinea fowl aloe'},
    {'aloe brevifolia', 'short-leaved aloe', 'crocodile aloe', 'blue aloe'},
    {'aloe humilis', 'spider aloe', 'hedgehog aloe'},

    # ── Echeveria ────────────────────────────────────────────────────────────
    {'echeveria subsessilis', 'morning beauty'},
    {'echeveria pulvinata', 'chenille plant', 'ruby echeveria'},
    {'echeveria runyonii', 'topsy turvy', 'mexican hens'},
    {'echeveria perle von nurnberg', 'pearl of nurnberg', 'perle von nurnberg',
     'pearl of nuremberg'},
    {'echeveria black prince', 'black prince succulent', 'dark echeveria'},
    {'echeveria cubic frost', 'cubic frost echeveria'},

    # ── Haworthia ────────────────────────────────────────────────────────────
    {'haworthia fasciata', 'zebra plant', 'zebra haworthia', 'zebra succulent',
     'zebra cactus'},
    {'haworthia attenuata', 'zebra plant', 'zebra haworthia', 'zebra wart',
     'star window plant'},
    {'haworthia cooperi', 'pearl succulent', 'cooperative haworthia',
     'window haworthia', 'transparent haworthia'},
    {'haworthia limifolia', 'fairy washboard', 'file leaved haworthia'},
    {'haworthia retusa', 'star cactus', 'window plant'},
    {'haworthiopsis fasciata', 'zebra plant', 'zebra haworthia'},
    {'haworthiopsis attenuata', 'zebra wart', 'star window plant'},

    # ── Sedum ────────────────────────────────────────────────────────────────
    {'sedum morganianum', "burro's tail", 'donkey tail', 'lamb tail',
     'burros tail', 'donkeys tail'},
    {'sedum rubrotinctum', 'jelly bean plant', 'pork and beans',
     'christmas cheer sedum'},
    {'sedum adolphi', 'golden sedum', "coppertone stonecrop", 'adolphi sedum'},
    {'sedum pachyphyllum', 'jelly beans', 'many fingers sedum'},
    {'sedum dasyphyllum', 'corsican stonecrop', 'blue stonecrop'},
    {'sedum hispanicum', 'spanish stonecrop'},

    # ── Crassula ─────────────────────────────────────────────────────────────
    {'crassula ovata', 'jade plant', 'jade tree', 'money plant', 'lucky plant',
     'friendship tree', 'dollar plant', 'money tree jade'},
    {'crassula perforata', 'string of buttons', 'necklace vine'},
    {'crassula capitella', 'campfire crassula', 'campfire plant', 'red pagoda'},
    {'crassula muscosa', 'watch chain', 'lizard tail', 'zipper plant'},
    {'crassula tetragona', 'miniature pine tree', 'pine tree crassula'},
    {'crassula coccinea', 'red crassula', 'scarlet paintbrush'},

    # ── Senecio / Curio ──────────────────────────────────────────────────────
    {'senecio rowleyanus', 'curio rowleyanus', 'string of pearls',
     'string of beads pearls'},
    {'senecio herreianus', 'curio herreianus', 'string of beads',
     'string of watermelons', 'string of raindrops'},
    {'senecio radicans', 'curio radicans', 'string of bananas',
     'string of fishhooks'},
    {'senecio serpens', 'blue chalksticks', 'blue chalk fingers'},
    {'senecio vitalis', 'narrow leaf chalksticks', 'blue senecio'},
    {'senecio mandraliscae', 'blue chalksticks', 'curio cylindricus'},

    # ── Portulacaria ─────────────────────────────────────────────────────────
    {'portulacaria afra', 'elephant bush', 'elephant food', 'dwarf jade',
     'porkbush', 'rainbow elephant bush', 'variegated elephant bush'},

    # ── Graptopetalum ────────────────────────────────────────────────────────
    {'graptopetalum paraguayense', 'ghost plant', 'mother of pearl plant',
     'ghost echeveria'},
    {'graptopetalum amethystinum', 'lavender pebbles', 'jewel leaf plant'},

    # ── Sansevieria / Dracaena ───────────────────────────────────────────────
    {'sansevieria trifasciata', 'dracaena trifasciata', 'snake plant',
     "mother in law's tongue", 'mother in laws tongue', 'viper bowstring hemp',
     'good luck plant', 'saint george sword'},
    {'sansevieria cylindrica', 'dracaena angolensis', 'cylindrical snake plant',
     'african spear', 'spear sansevieria', 'spear dracaena'},
    {'sansevieria moonshine', 'moonshine snake plant', 'silver moonshine'},
    {'sansevieria zeylanica', 'bowstring hemp', 'ceylon bowstring hemp'},
    {'sansevieria hahnii', 'bird nest sansevieria', 'bird nest snake plant'},

    # ── Pothos / Epipremnum ──────────────────────────────────────────────────
    {'epipremnum aureum', 'pothos', 'golden pothos', "devil's ivy",
     'devils ivy', 'money plant pothos', 'silver vine'},
    {'epipremnum pinnatum', 'dragon tail plant', 'centipede tongavine'},

    # ── Monstera ─────────────────────────────────────────────────────────────
    {'monstera deliciosa', 'swiss cheese plant', 'split leaf philodendron',
     'ceriman', 'mexican breadfruit'},
    {'monstera adansonii', 'swiss cheese vine', 'five holes plant',
     'adanson monstera', 'obliqua monstera'},
    {'monstera obliqua', 'swiss cheese vine', 'monstera adansonii'},

    # ── Ficus ────────────────────────────────────────────────────────────────
    {'ficus lyrata', 'fiddle leaf fig', 'fiddle fig', 'banjo fig'},
    {'ficus elastica', 'rubber plant', 'rubber tree', 'rubber fig',
     'indian rubber tree'},
    {'ficus benjamina', 'weeping fig', 'benjamin fig', 'ficus tree'},

    # ── Hoya ─────────────────────────────────────────────────────────────────
    {'hoya carnosa', 'wax plant', 'wax flower', 'porcelain flower'},
    {'hoya kerrii', 'sweetheart hoya', 'valentine hoya', 'lucky heart',
     'sweetheart plant'},
    {'hoya pubicalyx', 'silver pink vine hoya', 'hoya silver pink'},

    # ── Schlumbergera ────────────────────────────────────────────────────────
    {'schlumbergera bridgesii', 'christmas cactus', 'holiday cactus'},
    {'schlumbergera truncata', 'thanksgiving cactus', 'crab cactus',
     'holiday cactus'},

    # ── Cacti ────────────────────────────────────────────────────────────────
    {'echinocactus grusonii', 'golden barrel cactus', 'golden ball cactus',
     "mother in law's cushion"},
    {'mammillaria', 'pincushion cactus', 'nipple cactus'},
    {'mammillaria elongata', 'ladyfinger cactus', 'gold lace cactus'},
    {'mammillaria hahniana', 'old lady cactus', 'birthday cake cactus'},
    {'opuntia', 'prickly pear', 'paddle cactus', 'prickly pear cactus'},
    {'opuntia microdasys', 'bunny ears cactus', 'angel wings cactus'},
    {'cereus peruvianus', 'peruvian apple cactus', 'column cactus'},
    {'gymnocalycium mihanovichii', 'moon cactus', 'ruby ball cactus',
     'ruby cactus', 'hibotan cactus'},
    {'ferocactus', 'barrel cactus', 'fish hook cactus'},
    {'astrophytum myriostigma', 'bishop cap cactus', 'bishop hat cactus'},
    {'notocactus leninghausii', 'golden ball cactus', 'lemon ball cactus'},

    # ── Euphorbia ────────────────────────────────────────────────────────────
    {'euphorbia trigona', 'african milk tree', 'cathedral cactus',
     'abyssinian euphorbia', 'good luck cactus'},
    {'euphorbia tirucalli', 'pencil cactus', 'pencil plant', 'milk bush',
     'finger tree', 'sticks on fire'},
    {'euphorbia lactea', 'mottled spurge', 'dragon bones', 'frilled fan'},
    {'euphorbia obesa', 'baseball plant', 'sea urchin plant'},
    {'euphorbia ingens', 'candelabra tree', 'naboom'},

    # ── Agave ────────────────────────────────────────────────────────────────
    {'agave americana', 'century plant', 'american aloe', 'maguey'},
    {'agave attenuata', 'foxtail agave', 'soft agave', "lion's tail agave"},
    {'agave parryi', 'mescal agave', "parry's agave", 'artichoke agave'},

    # ── Gasteria ─────────────────────────────────────────────────────────────
    {'gasteria', 'ox tongue plant', 'ox tongue succulent', 'lawyer tongue'},

    # ── Lithops ──────────────────────────────────────────────────────────────
    {'lithops', 'living stones', 'pebble plant', 'flowering stones',
     'mimicry plant'},

    # ── Fenestraria ──────────────────────────────────────────────────────────
    {'fenestraria rhopalophylla', 'baby toes', 'window plant fenestraria'},

    # ── Kalanchoe ────────────────────────────────────────────────────────────
    {'kalanchoe blossfeldiana', 'flaming katy', 'florist kalanchoe',
     'madagascar widow thrill', 'christmas kalanchoe'},
    {'kalanchoe tomentosa', 'panda plant', 'panda bear plant',
     'velvet plant kalanchoe', 'pussy ears'},
    {'kalanchoe daigremontiana', 'mother of thousands', 'alligator plant',
     'devil backbone', 'mexican hat plant'},
    {'kalanchoe delagoensis', 'chandelier plant', 'mother of millions'},

    # ── Aeonium ──────────────────────────────────────────────────────────────
    {'aeonium arboreum', 'tree aeonium', 'irish rose', 'tree houseleek'},
    {'aeonium haworthii', 'pinwheel aeonium', 'kiwi aeonium'},

    # ── Other succulents ─────────────────────────────────────────────────────
    {'adromischus cristatus', 'crinkle leaf plant', 'key lime pie plant'},
    {'cotyledon orbiculata', "pig's ear", 'round-leafed navelwort',
     'silver crown cotyledon'},
    {'delosperma', 'ice plant', 'hardy ice plant', 'trailing ice plant'},
    {'oscularia deltoides', 'pink ice plant', 'deltoid dew plant'},
    {'lampranthus', 'ice plant', 'trailing ice plant', 'vygie'},
    {'corpuscularia lehmannii', 'ice plant', 'deltoid-leaved dew plant'},
    {'dudleya', 'live forever', 'chalk dudleya', 'chalk liveforever'},
    {'pachyphytum oviferum', 'moonstones', 'sugared almonds plant'},
    {'stapelia grandiflora', 'starfish cactus', 'carrion plant', 'starfish plant'},
    {'rhipsalis', 'mistletoe cactus', 'chain cactus'},
    # 'orchid cactus' spans Disocactus and Epiphyllum, so it stays ambiguous
    # on purpose. 'fishbone cactus' is handled by the species-level
    # Selenicereus/Disocactus anthonyanus group further down.
    {'epiphyllum hookeri', 'leaf cactus', 'night blooming cereus'},

    # ── Tillandsia / Air plants ───────────────────────────────────────────────
    {'tillandsia ionantha', 'blushing bride air plant', 'sky plant'},
    {'tillandsia xerographica', 'queen of air plants', 'xerographica air plant'},
    {'tillandsia usneoides', 'spanish moss', 'old man beard'},
    {'tillandsia caput-medusae', 'medusa air plant', 'octopus plant'},
    {'tillandsia bulbosa', 'bulbous air plant', 'bulb air plant'},

    # ── Common houseplants ───────────────────────────────────────────────────
    {'zamioculcas zamiifolia', 'zz plant', 'zanzibar gem', 'eternity plant',
     'aroid palm'},
    {'pachira aquatica', 'money tree', 'guiana chestnut', 'malabar chestnut'},
    {'spathiphyllum', 'peace lily', 'white sails', 'spathe flower'},
    {'chlorophytum comosum', 'spider plant', 'ribbon plant', 'hen and chickens'},
    {'tradescantia zebrina', 'wandering jew', 'inch plant', 'silver inch plant'},
    {'tradescantia pallida', 'purple heart', 'purple queen', 'wandering jew purple'},
    {'peperomia obtusifolia', 'baby rubber plant', 'american rubber plant',
     'pepper face'},
    {'peperomia caperata', 'ripple peperomia', 'emerald ripple peperomia'},
    {'pilea peperomioides', 'chinese money plant', 'ufo plant', 'pancake plant',
     'missionary plant'},
    {'calathea', 'prayer plant', 'peacock plant', 'cathedral windows plant'},
    {'maranta leuconeura', 'prayer plant', 'herringbone plant'},
    {'philodendron hederaceum', 'heartleaf philodendron', 'sweetheart plant',
     'velvet leaf philodendron'},
    {'philodendron bipinnatifidum', 'split leaf philodendron', 'lacy tree philodendron',
     'selloum philodendron'},
    {'alocasia', 'elephant ear', 'african mask plant', 'kris plant'},
    {'colocasia', 'taro', 'elephant ear'},
    {'caladium', 'angel wings', 'heart of jesus', 'elephant ear caladium'},

    # ── Vining houseplants ───────────────────────────────────────────────────
    {'scindapsus pictus', 'satin pothos', 'silver pothos', 'silver vine',
     'silvery philodendron'},
    {'monstera adansonii', 'swiss cheese vine', 'monkey mask monstera',
     'adansons monstera', "adanson's monstera"},
    {'rhaphidophora tetrasperma', 'mini monstera', 'ginny philodendron',
     'philodendron piccolo', 'dwarf monstera'},
    {'syngonium podophyllum', 'arrowhead plant', 'arrowhead vine',
     'goosefoot plant', 'nephthytis'},
    {'ceropegia woodii', 'string of hearts', 'rosary vine', 'chain of hearts',
     'sweetheart vine'},
    {'dischidia nummularia', 'string of nickels', 'button orchid'},
    {'peperomia prostrata', 'string of turtles'},
    {'peperomia argyreia', 'watermelon peperomia'},
    {'xerosicyos danguyi', 'silver dollar vine', 'dollar vine'},
    {'epipremnum pinnatum', 'dragon tail plant', 'centipede tongavine'},

    # ── Hoya ──────────────────────────────────────────────────────────────────
    {'hoya carnosa', 'wax plant', 'porcelain flower', 'honey plant'},
    {'hoya kerrii', 'sweetheart hoya', 'valentine hoya', 'lucky heart hoya',
     'heart leaf hoya'},
    {'hoya pubicalyx', 'shooting star hoya'},
    {'hoya linearis', 'angel hair hoya'},

    # ── Foliage houseplants ──────────────────────────────────────────────────
    {'anthurium andraeanum', 'flamingo flower', 'flamingo lily',
     "painter's palette", 'tailflower'},
    {'dieffenbachia seguine', 'dumb cane', 'spotted dumb cane'},
    {'aspidistra elatior', 'cast iron plant', 'bar room plant'},
    {'chamaedorea elegans', 'parlor palm', 'parlour palm', 'neanthe bella palm'},
    {'beaucarnea recurvata', 'ponytail palm', "elephant's foot palm",
     'elephants foot palm', 'bottle palm'},
    {'cycas revoluta', 'sago palm', 'king sago', 'japanese sago palm'},
    {'zamia furfuracea', 'cardboard palm', 'cardboard cycad', 'jamaican sago'},
    {'yucca elephantipes', 'spineless yucca', 'giant yucca', 'stick yucca'},
    {'schefflera arboricola', 'dwarf umbrella tree', 'hawaiian umbrella plant'},
    {'fittonia albivenis', 'nerve plant', 'mosaic plant', 'painted net leaf'},
    {'hypoestes phyllostachya', 'polka dot plant', 'freckle face plant'},
    {'oxalis triangularis', 'purple shamrock', 'false shamrock', 'love plant'},
    {'stromanthe sanguinea', 'triostar stromanthe', 'magenta stromanthe'},
    {'ctenanthe burle-marxii', 'fishbone prayer plant', 'never never plant'},
    {'begonia maculata', 'polka dot begonia', 'spotted begonia',
     'angel wing begonia'},
    {'strelitzia reginae', 'orange bird of paradise', 'crane flower'},
    {'strelitzia nicolai', 'giant white bird of paradise', 'wild banana'},

    # ── Ferns ─────────────────────────────────────────────────────────────────
    {'nephrolepis exaltata', 'boston fern', 'sword fern'},
    {'asplenium nidus', "bird's nest fern", 'birds nest fern'},
    {'platycerium bifurcatum', 'staghorn fern', 'elkhorn fern'},

    # ── Trailing succulents ──────────────────────────────────────────────────
    {'curio radicans', 'senecio radicans', 'string of bananas',
     'fishhook senecio', 'banana vine'},
    {'curio herreanus', 'senecio herreianus', 'string of beads',
     'string of watermelons', 'gooseberry plant'},
    {'crassula perforata', 'string of buttons', 'necklace vine',
     'baby necklace'},
    {'othonna capensis', 'ruby necklace', 'string of rubies', 'little pickles'},

    # ── Kalanchoe / Cotyledon / Adromischus ──────────────────────────────────
    {'kalanchoe tomentosa', 'panda plant', 'chocolate soldier',
     'pussy ears plant'},
    {'kalanchoe daigremontiana', 'mother of thousands', 'mexican hat plant'},
    {'kalanchoe delagoensis', 'chandelier plant', 'mother of millions'},
    {'cotyledon tomentosa', "bear's paw", 'bears paw succulent'},
    {'adromischus cristatus', 'key lime pie succulent', 'crinkle leaf plant'},

    # ── Mesembs / mimicry ────────────────────────────────────────────────────
    {'faucaria tigrina', 'tiger jaws', 'tigers jaw succulent'},
    {'pleiospilos nelii', 'split rock', 'split rock succulent', 'cleft stone'},
    {'anacampseros rufescens', 'sand rose succulent'},
    {'delosperma cooperi', 'hardy ice plant', 'trailing ice plant',
     'purple ice plant'},
    {'aptenia cordifolia', 'baby sun rose', 'heartleaf iceplant'},
    {'sempervivum tectorum', 'common houseleek', 'hens and chicks',
     'hen and chicks'},

    # ── Stapeliads ───────────────────────────────────────────────────────────
    {'stapelia gigantea', 'carrion flower', 'starfish flower',
     'zulu giant'},
    {'huernia zebrina', 'lifesaver plant', 'owl eyes succulent'},

    # ── Epiphytic cacti ──────────────────────────────────────────────────────
    {'selenicereus anthonyanus', 'disocactus anthonyanus', 'fishbone cactus',
     'ric rac cactus', 'zigzag cactus'},
    {'hatiora salicornioides', "drunkard's dream", 'dancing bones cactus',
     'bottle cactus'},
    {'rhipsalis baccifera', 'mistletoe cactus', 'spaghetti cactus'},

    # ── Desert cacti ─────────────────────────────────────────────────────────
    {'echinocactus grusonii', 'golden barrel cactus',
     "mother-in-law's cushion"},
    {'astrophytum myriostigma', "bishop's cap cactus", 'bishops cap cactus',
     'bishops miter cactus'},
    {'astrophytum asterias', 'sand dollar cactus', 'sea urchin cactus',
     'star cactus astrophytum'},
    {'gymnocalycium mihanovichii', 'moon cactus', 'chin cactus',
     'hibotan cactus'},
    {'cephalocereus senilis', 'old man cactus', 'old man of mexico'},
    {'espostoa lanata', 'peruvian old man cactus', 'snowball cactus'},
    {'cleistocactus straussii', 'silver torch cactus', 'wooly torch cactus'},
    {'myrtillocactus geometrizans', 'blue myrtle cactus', 'blue candle cactus',
     'bilberry cactus'},
    {'pilosocereus pachycladus', 'blue columnar cactus', 'blue torch cactus'},
    {'opuntia microdasys', 'bunny ears cactus', 'polka dot cactus',
     'angel wings cactus'},
    {'mammillaria elongata', 'ladyfinger cactus', 'gold lace cactus'},
    {'mammillaria plumosa', 'feather cactus', 'feather ball cactus'},

    # ── Euphorbia ────────────────────────────────────────────────────────────
    {'euphorbia tirucalli', 'firesticks', 'pencil cactus', 'sticks on fire',
     'pencil tree'},
    {'euphorbia trigona', 'african milk tree', 'cathedral cactus',
     'friendship cactus'},
    {'euphorbia lactea', 'coral cactus', 'mottled spurge', 'dragon bones'},

    # ── More Aloe / Haworthia / Gasteria ─────────────────────────────────────
    {'aloe juvenna', 'tiger tooth aloe'},
    {'aloe polyphylla', 'spiral aloe'},
    {'haworthia truncata', "horse's teeth succulent", 'horses teeth succulent'},
    {'gasteria bicolor', 'ox tongue plant', 'lawyer tongue plant'},

    # ── More Echeveria / Sedum / Crassula / Aeonium ──────────────────────────
    {'echeveria elegans', 'mexican snowball', 'white mexican rose',
     'mexican gem'},
    {'echeveria agavoides', 'lipstick echeveria', 'molded wax agave'},
    {'echeveria setosa', 'mexican firecracker'},
    {'graptopetalum paraguayense', 'ghost plant succulent',
     'mother of pearl plant'},
    {'sedum rubrotinctum', 'jelly bean plant', 'pork and beans succulent',
     'christmas cheer sedum'},
    {'sedum nussbaumerianum', 'coppertone stonecrop', 'coppertone sedum'},
    {'crassula arborescens', 'silver jade plant', 'silver dollar jade',
     'chinese jade'},
    {'crassula muscosa', 'watch chain succulent', 'princess pine succulent',
     'zipper plant'},
    {'aeonium arboreum', 'tree aeonium', 'irish rose succulent'},

]


# ── Index build ────────────────────────────────────────────────────────────────

# Bare genus names must never be indexed on their own. A genus covers dozens of
# different species, so indexing 'mammillaria' made every Mammillaria match
# every other Mammillaria. Common names that merely look like one word
# ('moonstones', 'porkbush', 'taro') are fine and stay indexed.
GENUS_ONLY = {
    'alocasia', 'caladium', 'calathea', 'colocasia', 'crassula', 'delosperma',
    'disocactus', 'dudleya', 'echeveria', 'epiphyllum', 'euphorbia',
    'ferocactus', 'gasteria', 'graptopetalum', 'haworthia', 'haworthiopsis',
    'kalanchoe', 'lampranthus', 'lithops', 'mammillaria', 'opuntia',
    'pachyphytum', 'peperomia', 'philodendron', 'pothos', 'rhipsalis',
    'sansevieria', 'sedum', 'sempervivum', 'senecio', 'spathiphyllum',
    'adromischus', 'agave', 'aloe', 'anthurium', 'cereus', 'cotyledon',
    'curio', 'dracaena', 'monstera', 'portulacaria', 'rebutia', 'schlumbergera',
    # genera introduced by the houseplant/cactus groups below
    'aeonium', 'aglaonema', 'aptenia', 'anacampseros', 'asplenium', 'aspidistra',
    'astrophytum', 'beaucarnea', 'begonia', 'caladium', 'cephalocereus',
    'ceropegia', 'chamaedorea', 'chlorophytum', 'cleistocactus', 'colocasia',
    'conophytum', 'ctenanthe', 'cycas', 'dieffenbachia', 'dischidia',
    'disocactus', 'echinocactus', 'echinopsis', 'epipremnum', 'espostoa',
    'faucaria', 'fenestraria', 'ficus', 'fittonia', 'gymnocalycium',
    'hatiora', 'hoya', 'huernia', 'hypoestes', 'maranta', 'myrtillocactus',
    'nephrolepis', 'othonna', 'oxalis', 'pachira', 'parodia', 'pilea',
    'pilosocereus', 'platycerium', 'pleiospilos', 'rhaphidophora',
    'scindapsus', 'selenicereus', 'stapelia', 'strelitzia', 'stromanthe',
    'syngonium', 'tillandsia', 'tradescantia', 'xerosicyos', 'yucca',
    'zamia', 'zamioculcas',
}


def _is_binomial(phrase):
    """True if a phrase looks like a scientific name ('hoya carnosa').

    Two words whose first is a known genus. Common names such as
    'elephant ear' or 'zebra plant' do not qualify — which matters, because
    two plants sharing a common name may genuinely be different species,
    whereas two entries sharing a binomial are certainly the same plant.
    """
    parts = phrase.split()
    return len(parts) == 2 and parts[0] in GENUS_ONLY and len(parts[1]) >= 3


def _merge_duplicate_groups(groups):
    """Union groups that share a scientific binomial.

    The data file is hand-maintained, so the same plant can end up listed in
    two places. Without merging, the shared binomial looks 'ambiguous' and gets
    dropped from the index — silently losing the very name we rely on most.
    Common-name overlaps are deliberately NOT merged.
    """
    parent = list(range(len(groups)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    seen = {}
    for gid, group in enumerate(groups):
        for name in group:
            if _is_binomial(name):
                if name in seen:
                    union(seen[name], gid)
                else:
                    seen[name] = gid

    merged = {}
    for gid, group in enumerate(groups):
        root = find(gid)
        merged.setdefault(root, set()).update(group)
    return list(merged.values())


def _build_index():
    """Build a dict: phrase → group_index for fast lookup.

    Only indexes phrases that are unambiguous — i.e. appear in exactly one
    synonym group. This prevents genus-only words like 'haworthia' (which
    appear across many species groups) from creating false matches.
    Multi-word phrases are always preferred; single words are only indexed
    if they uniquely identify one species (e.g. 'lithops').
    """
    from collections import defaultdict

    # Merge duplicate entries for the same plant first, so a shared scientific
    # name isn't mistaken for an ambiguity and discarded.
    groups = _merge_duplicate_groups(SYNONYM_GROUPS)

    # First pass: map each phrase to all groups it appears in
    phrase_groups = defaultdict(set)
    for gid, group in enumerate(groups):
        for name in group:
            phrase_groups[name].add(gid)

    # Second pass: keep a phrase only if it is unambiguous (exactly one group)
    # AND is not a bare genus name (which would match unrelated species).
    index = {}
    for phrase, gids in phrase_groups.items():
        if len(gids) != 1:
            continue
        if ' ' not in phrase and phrase in GENUS_ONLY:
            continue
        index[phrase] = next(iter(gids))
    return index, groups


_INDEX, _GROUPS = _build_index()


from functools import lru_cache


@lru_cache(maxsize=100_000)
def synonym_group_ids(title):
    """Return the set of synonym group indices that this title belongs to.

    Cached: matching compares the same titles thousands of times, and this
    scans every indexed phrase, so it dominated runtime uncached.
    """
    t = title.lower()
    hits = set()
    for phrase, gid in _INDEX.items():
        if phrase in t:
            hits.add(gid)
    return frozenset(hits)


def synonym_keywords(title):
    """Return all meaningful words from every synonym group this title belongs to.

    Use this to expand a product title's search terms so that common-name titles
    (e.g. "Burro's Tail") also search against scientific-name index entries
    (e.g. "sedum", "morganianum").
    """
    import re as _re
    gids = synonym_group_ids(title)
    words = set()
    for gid in gids:
        # _GROUPS is the merged view that _INDEX's ids refer to
        for phrase in _GROUPS[gid]:
            for w in _re.findall(r'[a-z]+', phrase):
                if len(w) >= 4:
                    words.add(w)
    return words


def shares_synonym_group(title_a, title_b):
    """Return True if both titles refer to the same plant via synonym lookup."""
    groups_a = synonym_group_ids(title_a)
    groups_b = synonym_group_ids(title_b)
    return bool(groups_a & groups_b)
