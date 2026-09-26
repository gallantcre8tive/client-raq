"""Default size/price templates companies can enable and edit.
Prices are examples only — each company sets its own.
"""
from __future__ import annotations

# (label, example_price_ngn)
FRAME_NORMAL = [
    ("5x7", 2600), ("6x8", 3200), ("8x10", 3500), ("8x12", 4000),
    ("10x12", 4700), ("12x16", 5700), ("12x18", 7500), ("16x20", 8500),
    ("16x24", 10000), ("20x24", 11000), ("24x30", 16000), ("24x36", 17000),
]
FRAME_FRAMELESS = [
    ("5x7", 2700), ("8x10", 5000), ("8x12", 6000), ("10x12", 7000),
    ("12x16", 8500), ("16x20", 12500), ("16x24", 14000), ("20x24", 17000),
    ("24x36", 26000),
]
FRAME_ACRYLIC = [
    ("8x10", 6500), ("8x12", 7000), ("10x12", 8500), ("12x16", 10000),
    ("12x18", 12500), ("16x20", 17000), ("16x24", 18000), ("20x24", 21000),
    ("20x30", 29000), ("18x40", 40000),
]
NYLON_BAG = [
    ("Small 8x11 in", 0), ("Medium 12x16 in", 0), ("Large 16x20 in", 0),
    ("Custom size", 0),
]
ROLLUP = [
    ("33x80 in", 0), ("33x86 in", 0), ("Standard roll-up", 0),
]

# catalog_key -> list of (label, example_price)
SIZE_TEMPLATES: dict[str, list[tuple[str, float]]] = {
    "frames-mounting:picture-frame": FRAME_NORMAL,
    "frames-mounting:frameless-frame": FRAME_FRAMELESS,
    "frames-mounting:acrylic-frame-plaque": FRAME_ACRYLIC,
    "bags-packaging:nylon-poly-bag-print": NYLON_BAG,
    "large-format:roll-up-banner": ROLLUP,
}

# Extra master catalog entries for frame subtypes
FRAME_CATALOG_EXTRAS = [
    {
        "category": "Frames & Mounting",
        "name": "Picture Frame",
        "pricing_method": "fixed_size",
        "unit": "per piece",
        "flow_type": "frame",
        "description": "Standard frames by size (5x7 up to 24x36). Enable sizes and set your prices.",
    },
    {
        "category": "Frames & Mounting",
        "name": "Frameless Frame",
        "pricing_method": "fixed_size",
        "unit": "per piece",
        "flow_type": "frame",
        "description": "Frameless / clip style by size. Set each size price.",
    },
    {
        "category": "Frames & Mounting",
        "name": "Acrylic Frame / Plaque",
        "pricing_method": "fixed_size",
        "unit": "per piece",
        "flow_type": "frame",
        "description": "Acrylic frames and plaques by size.",
    },
]
