"""Master printing catalogue for Client-RaQ (from Client RaQ service PDFs).
Companies enable items and set their own prices — no universal rates.
"""
from __future__ import annotations

# pricing_method: piece | sqft | sqin | page | sheet | tier | setup_unit | custom
MASTER_CATALOG: list[dict] = [
    # Large format
    {"category": "Large Format", "name": "Flex / Banner Printing", "pricing_method": "sqft", "unit": "per sq ft", "description": "Frontlit, backlit, mesh, blockout; eyelets, hemming, rope, pole pocket."},
    {"category": "Large Format", "name": "Mesh Banner", "pricing_method": "sqft", "unit": "per sq ft", "description": "Wind-permeable outdoor mesh; hemming and eyelets."},
    {"category": "Large Format", "name": "Backlit Flex", "pricing_method": "sqft", "unit": "per sq ft", "description": "Lightbox-compatible backlit media."},
    {"category": "Large Format", "name": "SAV / Self-Adhesive Vinyl", "pricing_method": "sqft", "unit": "per sq ft", "description": "White/clear vinyl; gloss/matte; lamination and contour cutting."},
    {"category": "Large Format", "name": "One-Way Vision Film", "pricing_method": "sqft", "unit": "per sq ft", "description": "Perforated window film for vehicles and storefronts."},
    {"category": "Large Format", "name": "Frosted / Privacy Window Film", "pricing_method": "sqft", "unit": "per sq ft", "description": "Frosted/etched privacy film; cutting and installation."},
    {"category": "Large Format", "name": "Clear Window Film", "pricing_method": "sqft", "unit": "per sq ft", "description": "Clear adhesive film for transparent graphics."},
    {"category": "Large Format", "name": "Wallpaper / Wall Graphics", "pricing_method": "sqft", "unit": "per sq ft", "description": "Wall media; installation optional."},
    {"category": "Large Format", "name": "Roll-up Banner", "pricing_method": "piece", "unit": "per piece", "description": "Printed media + cassette/stand."},
    {"category": "Large Format", "name": "X-Banner", "pricing_method": "piece", "unit": "per piece", "description": "Printed banner + X-frame."},
    {"category": "Large Format", "name": "L-Banner / Teardrop Flag", "pricing_method": "piece", "unit": "per piece", "description": "Flag or L-banner with pole/base."},
    {"category": "Large Format", "name": "Canvas Print", "pricing_method": "sqft", "unit": "per sq ft", "description": "Canvas media; stretching and framing options."},
    {"category": "Large Format", "name": "Billboard Printing", "pricing_method": "custom", "unit": "custom quote", "description": "Outdoor billboard media; project quote."},
    # Stickers
    {"category": "Stickers & Labels", "name": "Standard Sticker / SAV", "pricing_method": "sqin", "unit": "per sq ft", "description": "Vinyl/paper stickers; size in inches uses ÷144 when rate is per sq ft."},
    {"category": "Stickers & Labels", "name": "Die-Cut Sticker", "pricing_method": "piece", "unit": "per piece", "description": "Full cut through material and liner."},
    {"category": "Stickers & Labels", "name": "Kiss-Cut Sticker", "pricing_method": "piece", "unit": "per piece", "description": "Cut face, backing retained."},
    {"category": "Stickers & Labels", "name": "Clear / Transparent Sticker", "pricing_method": "sqin", "unit": "per sq ft", "description": "Clear vinyl; optional white underprint."},
    {"category": "Stickers & Labels", "name": "Product / Bottle Label", "pricing_method": "piece", "unit": "per piece", "description": "Product labels; quantity tiers optional."},
    {"category": "Stickers & Labels", "name": "Holographic Sticker", "pricing_method": "piece", "unit": "per piece", "description": "Holographic stock; die-cut or kiss-cut."},
    {"category": "Stickers & Labels", "name": "Reflective Sticker", "pricing_method": "sqft", "unit": "per sq ft", "description": "Reflective vinyl for safety/vehicles."},
    {"category": "Stickers & Labels", "name": "Floor Sticker", "pricing_method": "sqft", "unit": "per sq ft", "description": "Anti-slip laminated floor media."},
    # Digital / paper
    {"category": "Paper & Digital Print", "name": "Flyer / Leaflet", "pricing_method": "tier", "unit": "per piece", "description": "A6–A3; single/double sided; GSM options."},
    {"category": "Paper & Digital Print", "name": "Business Card", "pricing_method": "tier", "unit": "per piece", "description": "Standard/premium stock; finishing options."},
    {"category": "Paper & Digital Print", "name": "Brochure", "pricing_method": "tier", "unit": "per piece", "description": "Bi-fold, tri-fold, gate-fold, multi-page."},
    {"category": "Paper & Digital Print", "name": "Poster", "pricing_method": "piece", "unit": "per piece", "description": "A4–A0 and custom sizes."},
    {"category": "Paper & Digital Print", "name": "Letterhead", "pricing_method": "tier", "unit": "per piece", "description": "Corporate letterheads."},
    {"category": "Paper & Digital Print", "name": "Invitation / Certificate", "pricing_method": "piece", "unit": "per piece", "description": "Card stock; foil/emboss optional."},
    {"category": "Paper & Digital Print", "name": "Menu Card", "pricing_method": "piece", "unit": "per piece", "description": "Restaurant/event menus."},
    {"category": "Paper & Digital Print", "name": "NCR / Invoice Book", "pricing_method": "piece", "unit": "per piece", "description": "Duplicate/triplicate carbonless forms."},
    # Books
    {"category": "Books & Binding", "name": "Exercise Book / Notebook", "pricing_method": "piece", "unit": "per piece", "description": "School/custom branded exercise books."},
    {"category": "Books & Binding", "name": "Jotter / Notepad", "pricing_method": "piece", "unit": "per piece", "description": "Branded jotters; foil/UV optional."},
    {"category": "Books & Binding", "name": "Perfect-Bound Book", "pricing_method": "custom", "unit": "custom quote", "description": "Page count + cover + binding."},
    {"category": "Books & Binding", "name": "Spiral / Wire-O Book", "pricing_method": "piece", "unit": "per piece", "description": "Spiral or wire binding."},
    {"category": "Books & Binding", "name": "Programme / Booklet", "pricing_method": "piece", "unit": "per piece", "description": "Event/church/funeral programmes."},
    # Frames
    {"category": "Frames & Mounting", "name": "Picture Frame", "pricing_method": "piece", "unit": "per piece", "description": "Wood/plastic/metal/acrylic frames; fixed sizes."},
    {"category": "Frames & Mounting", "name": "Acrylic Frame / Plaque", "pricing_method": "piece", "unit": "per piece", "description": "Acrylic frames, plaques, awards."},
    {"category": "Frames & Mounting", "name": "Snap / Lightbox Frame", "pricing_method": "piece", "unit": "per piece", "description": "Snap frames and lightboxes."},
    {"category": "Frames & Mounting", "name": "Foam Board / PVC Mount", "pricing_method": "sqft", "unit": "per sq ft", "description": "Mounting on foam/PVC/sunboard."},
    {"category": "Frames & Mounting", "name": "Photo Printing", "pricing_method": "piece", "unit": "per piece", "description": "Photo paper prints by size."},
    # UV / specialty
    {"category": "UV & Specialty", "name": "UV Flatbed Printing", "pricing_method": "sqft", "unit": "per sq ft", "description": "Print on acrylic, wood, glass, metal, etc."},
    {"category": "UV & Specialty", "name": "UV DTF Transfer", "pricing_method": "sqin", "unit": "per sq ft", "description": "Hard-surface UV DTF transfers."},
    {"category": "UV & Specialty", "name": "Laser Engraving", "pricing_method": "piece", "unit": "per piece", "description": "Wood, acrylic, leather, metal engraving."},
    {"category": "UV & Specialty", "name": "Laser / CNC Cutting", "pricing_method": "custom", "unit": "custom quote", "description": "Cut acrylic, wood, board; project-based."},
    # Garments
    {"category": "Garment Printing", "name": "Screen Print T-Shirt", "pricing_method": "setup_unit", "unit": "per piece", "description": "Setup + per garment; multi-colour options."},
    {"category": "Garment Printing", "name": "DTF T-Shirt / Hoodie", "pricing_method": "piece", "unit": "per piece", "description": "DTF transfer + press on garments."},
    {"category": "Garment Printing", "name": "DTF Transfer Only", "pricing_method": "sqin", "unit": "per sq ft", "description": "Transfers by area or gang sheet."},
    {"category": "Garment Printing", "name": "DTG Printing", "pricing_method": "piece", "unit": "per piece", "description": "Direct-to-garment photo-quality prints."},
    {"category": "Garment Printing", "name": "Flock / Flex (HTV)", "pricing_method": "piece", "unit": "per piece", "description": "Heat-transfer vinyl; names and numbers."},
    {"category": "Garment Printing", "name": "Embroidery", "pricing_method": "piece", "unit": "per piece", "description": "Caps, polos, jackets; stitch-based."},
    {"category": "Garment Printing", "name": "Cap Branding", "pricing_method": "piece", "unit": "per piece", "description": "Embroidery, DTF or flex on caps."},
    {"category": "Garment Printing", "name": "Uniform / Jersey Branding", "pricing_method": "piece", "unit": "per piece", "description": "Workwear and sportswear branding."},
    {"category": "Garment Printing", "name": "Sublimation Apparel", "pricing_method": "piece", "unit": "per piece", "description": "Polyester garments and sportswear."},
    # Bags
    {"category": "Bags & Packaging", "name": "Nylon / Poly Bag Print", "pricing_method": "tier", "unit": "per piece", "description": "Shopping and takeaway bag branding."},
    {"category": "Bags & Packaging", "name": "Paper / Gift Bag", "pricing_method": "piece", "unit": "per piece", "description": "Branded paper and gift bags."},
    {"category": "Bags & Packaging", "name": "Packaging Box", "pricing_method": "custom", "unit": "custom quote", "description": "Product/gift boxes; dimensions required."},
    {"category": "Bags & Packaging", "name": "Product Label Roll", "pricing_method": "piece", "unit": "per piece", "description": "Labels for packaging runs."},
    # Vehicle
    {"category": "Vehicle Branding", "name": "Full / Partial Vehicle Wrap", "pricing_method": "custom", "unit": "custom quote", "description": "Wrap vinyl + install; project quote."},
    {"category": "Vehicle Branding", "name": "Vehicle Decals / Door Branding", "pricing_method": "sqft", "unit": "per sq ft", "description": "Doors, bonnet, partial branding."},
    {"category": "Vehicle Branding", "name": "Reflective Vehicle Graphics", "pricing_method": "sqft", "unit": "per sq ft", "description": "Reflective fleet graphics."},
    # Signage
    {"category": "Signage", "name": "Acrylic Sign", "pricing_method": "sqft", "unit": "per sq ft", "description": "Acrylic signs; thickness and mounting."},
    {"category": "Signage", "name": "PVC / Foam / Sunboard Sign", "pricing_method": "sqft", "unit": "per sq ft", "description": "Board signs for indoor/outdoor."},
    {"category": "Signage", "name": "ACP / Aluminium Sign", "pricing_method": "sqft", "unit": "per sq ft", "description": "Aluminium composite panel signs."},
    {"category": "Signage", "name": "3D Lettering", "pricing_method": "custom", "unit": "custom quote", "description": "3D acrylic/metal letters."},
    {"category": "Signage", "name": "LED / Lightbox / Neon Sign", "pricing_method": "custom", "unit": "custom quote", "description": "Illuminated signage projects."},
    {"category": "Signage", "name": "Shop Front / Office Signage", "pricing_method": "custom", "unit": "custom quote", "description": "Fascia, reception, door signs."},
    {"category": "Signage", "name": "Real Estate / Site Board", "pricing_method": "piece", "unit": "per piece", "description": "For-sale, for-rent, construction boards."},
    # Events
    {"category": "Events & Wedding", "name": "Event Backdrop", "pricing_method": "sqft", "unit": "per sq ft", "description": "Step-and-repeat and photo backdrops."},
    {"category": "Events & Wedding", "name": "Wedding Invitation Set", "pricing_method": "piece", "unit": "per piece", "description": "Invites, RSVP, programmes."},
    {"category": "Events & Wedding", "name": "Funeral / Memorial Print", "pricing_method": "piece", "unit": "per piece", "description": "Programmes, posters, banners."},
    {"category": "Events & Wedding", "name": "Church Programme / Branding", "pricing_method": "piece", "unit": "per piece", "description": "Programmes, banners, IDs."},
    # ID & promo
    {"category": "ID & Accessories", "name": "PVC ID Card", "pricing_method": "piece", "unit": "per piece", "description": "Staff/student/membership cards."},
    {"category": "ID & Accessories", "name": "Lanyard", "pricing_method": "piece", "unit": "per piece", "description": "Custom logo lanyards."},
    {"category": "ID & Accessories", "name": "Wristband", "pricing_method": "piece", "unit": "per piece", "description": "Tyvek, silicone, fabric wristbands."},
    {"category": "Promotional Products", "name": "Branded Pen", "pricing_method": "piece", "unit": "per piece", "description": "Screen/UV/laser on pens."},
    {"category": "Promotional Products", "name": "Mug / Tumbler / Bottle", "pricing_method": "piece", "unit": "per piece", "description": "Sublimation, UV, or laser branding."},
    {"category": "Promotional Products", "name": "Keyholder / USB / Power Bank", "pricing_method": "piece", "unit": "per piece", "description": "Promotional tech and keyholders."},
    {"category": "Promotional Products", "name": "Surprise Box / Gift Packaging", "pricing_method": "custom", "unit": "custom quote", "description": "Custom gift boxes and packaging."},
    # Plotter
    {"category": "Plotter & CAD", "name": "CAD / Architectural Plot", "pricing_method": "piece", "unit": "per piece", "description": "A0–A3 plots; B&W or colour."},
    {"category": "Plotter & CAD", "name": "Plotter Vinyl Cutting", "pricing_method": "sqft", "unit": "per sq ft", "description": "Lettering, logos, stencils."},
]


def catalog_key(category: str, name: str) -> str:
    import re
    raw = f"{category}:{name}".lower()
    return re.sub(r"[^a-z0-9]+", "-", raw).strip("-")[:120]


CATEGORIES = sorted({item["category"] for item in MASTER_CATALOG})

# Merge frame fixed-size templates
try:
    from app.data.size_templates import FRAME_CATALOG_EXTRAS
    _keys = {f"{i['category']}:{i['name']}" for i in MASTER_CATALOG}
    for extra in FRAME_CATALOG_EXTRAS:
        k = f"{extra['category']}:{extra['name']}"
        if k not in _keys:
            MASTER_CATALOG.append(extra)
        else:
            for i, item in enumerate(MASTER_CATALOG):
                if f"{item['category']}:{item['name']}" == k:
                    MASTER_CATALOG[i] = {**item, **extra}
                    break
    CATEGORIES = sorted({item["category"] for item in MASTER_CATALOG})
except Exception:
    pass
