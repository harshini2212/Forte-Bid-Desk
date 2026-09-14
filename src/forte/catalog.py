"""Forte Construction's operational data — a calibrated demo of the systems the
Bid Desk plugs into (the cost library + resource availability, the owners Forte
bids to and their project history, and the inbox of bid solicitations).

Not real owner data and not random noise: a curated, *representative* slice of a
New York general contractor's precon business, with **planted, findable
challenges** the agent has to earn its way through — an ambiguous "same as the
last station" line that needs project history, a superseded jack assembly that
needs an alternate, a deep negotiated rate that trips the margin floor, an owner
on a prequalification hold, a discontinued door spec. The demo is only
convincing if the agent visibly solves something.

Everything is deterministic (hash-seeded), so a fresh clone always yields the
same library, rates, and estimates — the UI runs instantly, offline, no API key.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict


def stable_hash(s: str) -> int:
    """Process-independent hash (builtin hash() is salted per run) — keeps the
    library, availability, and query set bit-identical on every run/host."""
    return int(hashlib.sha1(s.encode("utf-8")).hexdigest()[:8], 16)


# --- core records ---------------------------------------------------------------

@dataclass
class Part:
    """A cost-library assembly: a priced unit of scope (a material, a
    subcontracted assembly, or a field-ops line) Forte estimates against."""
    sku: str
    name: str
    brand: str             # the vendor / subcontractor / crew that delivers it
    category: str          # CSI-style division
    specs: dict            # spec signals the matcher reasons over
    uom: str               # "each" | "sf" | "lf" | "ton" | "case" | "month"
    unit_cost: float       # Forte's cost (sub quote / crew + material)
    list_price: float      # Forte's schedule rate (pre owner rate discount)
    status: str = "active"  # "active" | "discontinued"
    keywords: list = field(default_factory=list)   # search surface (student signal)
    mwbe: str = ""          # "" | "MBE" | "WBE" | "SDVOB" — vendor certification

    def margin_at(self, net_price: float) -> float:
        return (net_price - self.unit_cost) / net_price if net_price else 0.0


@dataclass
class StockLine:
    """Availability of an assembly at a Forte resource location — yard stock for
    materials, bench capacity for subcontracted assemblies, crew slots for
    field ops. `on_hand` is the number of units deliverable this bid cycle."""
    sku: str
    warehouse: str
    on_hand: int
    lead_time_days: int


@dataclass
class Customer:
    """An owner Forte bids to (a public agency or a private developer)."""
    id: str
    name: str
    contact: str
    email: str
    tier: str              # "A" (deepest negotiated rate) | "B" | "C"
    discount: float        # fraction off Forte's schedule rate at this tier
    credit_status: str     # "ok" | "hold"  (prequalification / bonding standing)
    terms: str             # payment / retainage label
    note: str = ""
    mwbe_goal: float = 0.0  # owner's M/WBE participation goal (0 = none)


@dataclass
class OrderHistoryLine:
    """A scope line Forte delivered for this owner before — how the owner's PM
    referred to it, which assembly it was, and on what project."""
    customer_id: str
    sku: str
    last_desc: str
    qty: int
    ordered_on: str


@dataclass
class RFQLine:
    raw: str               # the scope line as the owner's PM wrote it
    qty: int
    gold_sku: str | None   # the intended assembly (for scoring; never shown as the answer)
    challenge: str = ""    # what makes this line hard (for the demo narrative)


@dataclass
class RFQ:
    """An inbound bid solicitation / scope request from an owner."""
    id: str
    customer_id: str
    subject: str
    received_at: str
    body: str
    lines: list            # [RFQLine]
    headline: str          # one-line "what's interesting about this one"


@dataclass
class Company:
    name: str
    warehouses: list       # resource locations
    parts: list            # [Part]
    stock: list            # [StockLine]
    customers: list        # [Customer]
    history: list          # [OrderHistoryLine]
    rfqs: list             # [RFQ]

    def __post_init__(self):
        self._parts = {p.sku: p for p in self.parts}
        self._cust = {c.id: c for c in self.customers}

    def part(self, sku: str) -> Part | None:
        return self._parts.get(sku)

    def customer(self, cid: str) -> Customer | None:
        return self._cust.get(cid)

    def on_hand(self, sku: str) -> int:
        return sum(s.on_hand for s in self.stock if s.sku == sku)

    def stock_for(self, sku: str) -> list:
        return [s for s in self.stock if s.sku == sku and s.on_hand > 0]

    def history_for(self, cid: str) -> list:
        return [h for h in self.history if h.customer_id == cid]


# Forte's real footprint: HQ in Islandia, a warehouse in Holbrook, an office in NYC.
WAREHOUSES = ["Holbrook Warehouse (926 Lincoln Ave)",
              "Islandia HQ Yard (1770 Motor Pkwy)",
              "NYC Field Office (385 5th Ave)"]


# --- the cost library — a curated slice across the divisions Forte self-performs
# or subcontracts on transit, design-build and building work -------------------
# (sku, name, brand, category, specs, uom, unit_cost, list_price, status, keywords, mwbe)
_PARTS_RAW = [
    # vertical transportation (Forte's signature scope: elevators + ADA)
    ("ELV-HYD-MOD-2S", "Hydraulic Elevator Modernization, 2-stop", "LiftWorks Elevator", "Vertical Transportation",
     {"type": "hydraulic", "stops": "2", "capacity": "3500 lb", "code": "ASME A17.1"}, "each", 148000, 236000, "active",
     ["elevator", "hydraulic", "modernization", "2-stop", "ada", "lift"], "WBE"),
    ("ELV-TRC-NEW-3S", "Traction Elevator, New Install, 3-stop", "LiftWorks Elevator", "Vertical Transportation",
     {"type": "MRL traction", "stops": "3", "capacity": "3500 lb", "code": "ASME A17.1"}, "each", 265000, 415000, "active",
     ["elevator", "traction", "mrl", "new", "3-stop", "ada", "install"], "WBE"),
    ("ELV-HJ-40", "Hydraulic Jack Assembly, Model HJ-40", "LiftWorks Elevator", "Vertical Transportation",
     {"model": "HJ-40", "type": "in-ground jack", "stroke": "40 ft", "fits": "HJ-series hydraulic"}, "each", 21000, 34500, "active",
     ["hydraulic", "jack", "hj-40", "hj40", "cylinder", "plunger"], "WBE"),
    ("ELV-HJ-45", "Hydraulic Jack Assembly, Model HJ-45", "LiftWorks Elevator", "Vertical Transportation",
     {"model": "HJ-45", "type": "in-ground jack", "stroke": "45 ft", "supersedes": "HJ-40"}, "each", 22400, 36800, "active",
     ["hydraulic", "jack", "hj-45", "hj45", "cylinder", "plunger"], "WBE"),
    ("ESC-MOD-30", "Escalator Modernization, 30 ft rise", "LiftWorks Elevator", "Vertical Transportation",
     {"type": "escalator", "rise": "30 ft", "code": "ASME A17.1"}, "each", 310000, 480000, "active",
     ["escalator", "modernization", "30", "rise", "ada"], "WBE"),

    # structural steel / canopies / stairs (the ambiguous "same as last station" target)
    ("CAN-PLT-EXT-40", "Platform Canopy Extension, 40 LF", "Empire Steel", "Structural Steel",
     {"type": "canopy extension", "length": "40 LF", "material": "galvanized steel, standing seam"}, "each", 96000, 158000, "active",
     ["platform", "canopy", "extension", "40", "lf", "steel", "roof"], ""),
    ("CAN-PLT-EXT-60", "Platform Canopy Extension, 60 LF", "Empire Steel", "Structural Steel",
     {"type": "canopy extension", "length": "60 LF", "material": "galvanized steel, standing seam"}, "each", 139000, 228000, "active",
     ["platform", "canopy", "extension", "60", "lf", "steel", "roof"], ""),
    ("STL-STR-STA-NEW", "Station Stair, New Steel, 2-flight", "Empire Steel", "Structural Steel",
     {"type": "stair", "flights": "2", "material": "galvanized steel, checker plate"}, "each", 118000, 189000, "active",
     ["stair", "staircase", "steel", "2-flight", "station", "new", "treads"], ""),
    ("STL-W12-26", "Structural Steel, W12x26 Beam, Furnish & Erect", "Empire Steel", "Structural Steel",
     {"shape": "W12x26", "grade": "A992", "finish": "shop primed"}, "ton", 3900, 6400, "active",
     ["steel", "beam", "w12x26", "w12", "erect", "structural", "ton"], ""),

    # concrete
    ("CON-SLB-4000", "Concrete Slab on Grade, 4000 psi, 6in", "Metro Concrete", "Concrete",
     {"strength": "4000 psi", "thickness": "6in", "finish": "broom"}, "sf", 9.40, 15.80, "active",
     ["concrete", "slab", "4000", "psi", "6in", "grade", "pour"], ""),
    ("CON-FDN-ELV", "Elevator Pit & Foundation, Reinforced", "Metro Concrete", "Concrete",
     {"type": "elevator pit", "depth": "6 ft", "waterproofing": "bentonite"}, "each", 38000, 62000, "active",
     ["elevator", "pit", "foundation", "concrete", "reinforced", "waterproof"], ""),
    ("CON-FRM-REL-DR", "Form Release Agent (55 gal drum)", "Metro Concrete", "Concrete",
     {"pack": "55 gal drum", "type": "reactive release"}, "drum", 410, 690, "active",
     ["form", "release", "agent", "drum", "concrete", "oil"], ""),

    # masonry / finishes
    ("MAS-CMU-8", "CMU Wall, 8in, Reinforced & Grouted", "Bronx Masonry", "Masonry",
     {"block": "8in CMU", "reinforcing": "#5 @ 32in", "grout": "solid"}, "sf", 24.50, 41.00, "active",
     ["cmu", "block", "masonry", "8in", "wall", "grouted", "reinforced"], "MBE"),
    ("MAS-TILE-PLT", "Platform Tile, Porcelain, ADA Detectable Edge", "Bronx Masonry", "Finishes",
     {"type": "porcelain tile + detectable warning", "standard": "ADA 705"}, "sf", 31.00, 52.00, "active",
     ["tile", "platform", "porcelain", "detectable", "ada", "edge", "warning"], "MBE"),

    # electrical
    ("ELE-CND-EMT-075", "EMT Conduit 3/4in, Installed", "VoltLine Electrical", "Electrical",
     {"type": "EMT", "size": "3/4in", "conn": "set-screw"}, "lf", 9.20, 15.60, "active",
     ["emt", "conduit", "3/4", "electrical", "raceway"], "MBE"),
    ("ELE-CND-EMT-100", "EMT Conduit 1in, Installed", "VoltLine Electrical", "Electrical",
     {"type": "EMT", "size": "1in", "conn": "set-screw"}, "lf", 11.80, 19.90, "active",
     ["emt", "conduit", "1", "inch", "electrical", "raceway"], "MBE"),
    ("ELE-PNL-400", "Distribution Panel 400A, 3-Phase", "VoltLine Electrical", "Electrical",
     {"amps": "400A", "phase": "3", "voltage": "208Y/120V"}, "each", 8600, 14200, "active",
     ["panel", "400a", "distribution", "3-phase", "electrical", "switchboard"], "MBE"),
    ("ELE-LTG-PLT-LED", "Platform LED Lighting, Vandal-Resistant", "VoltLine Electrical", "Electrical",
     {"type": "LED linear", "rating": "IK10 / IP66"}, "each", 640, 1050, "active",
     ["led", "lighting", "platform", "vandal", "fixture", "luminaire"], "MBE"),

    # plumbing / fire protection
    ("PLB-PIP-CU-200", "Copper Pipe Type L 2in, Installed", "HarborFlow Mechanical", "Plumbing",
     {"material": "copper type L", "size": "2in", "joint": "press"}, "lf", 54.00, 89.00, "active",
     ["copper", "pipe", "2", "inch", "type l", "plumbing"], "SDVOB"),
    ("PLB-DRN-PIT", "Elevator Pit Sump Pump & Drain, Oil-Minder", "HarborFlow Mechanical", "Plumbing",
     {"type": "sump + oil-minder", "hp": "1/2 hp"}, "each", 3900, 6500, "active",
     ["sump", "pump", "pit", "drain", "oil", "minder"], "SDVOB"),
    ("FPR-SPR-HD", "Fire Sprinkler Head, Quick Response", "HarborFlow Mechanical", "Fire Protection",
     {"type": "pendent QR", "rating": "155F"}, "each", 96, 165, "active",
     ["sprinkler", "head", "fire", "quick", "response", "pendent"], "SDVOB"),

    # doors & glazing (the discontinued-spec target)
    ("DOR-FR-20", "Fire-Rated Door Assembly, Model FR-20 (90 min)", "Guardian Openings", "Doors & Glazing",
     {"model": "FR-20", "rating": "90 min", "size": "3-0 x 7-0", "replaced_by": "FR-22"}, "each", 1850, 3100, "discontinued",
     ["fire", "rated", "door", "fr-20", "fr20", "90 min"], ""),
    ("DOR-FR-22", "Fire-Rated Door Assembly, Model FR-22 (90 min)", "Guardian Openings", "Doors & Glazing",
     {"model": "FR-22", "rating": "90 min", "size": "3-0 x 7-0", "replaces": "FR-20"}, "each", 1920, 3250, "active",
     ["fire", "rated", "door", "fr-22", "fr22", "90 min"], ""),
    ("GLZ-CW-ALU", "Aluminum Curtain Wall, Thermally Broken", "Guardian Openings", "Doors & Glazing",
     {"system": "unitized", "glazing": "1in IGU low-e"}, "sf", 118, 196, "active",
     ["curtain", "wall", "aluminum", "glazing", "unitized", "glass"], ""),

    # site work / field ops
    ("SIT-SDW-CONC", "Sidewalk Restoration, NYC DOT Spec", "Metro Concrete", "Site Work",
     {"spec": "NYC DOT H-1042", "thickness": "4in"}, "sf", 18.50, 31.00, "active",
     ["sidewalk", "restoration", "dot", "concrete", "curb"], ""),
    ("ADA-RAMP-CONC", "ADA Ramp, Concrete w/ Handrails", "Metro Concrete", "Site Work",
     {"slope": "1:12", "rails": "both sides", "standard": "ADA 405"}, "each", 14500, 24000, "active",
     ["ada", "ramp", "concrete", "handrail", "accessible", "slope"], ""),
    ("SIT-MPT-MO", "Maintenance & Protection of Traffic, per month", "Forte Field Ops", "Site Work",
     {"type": "MPT", "includes": "flaggers, barriers, signage"}, "month", 24000, 39000, "active",
     ["mpt", "traffic", "maintenance", "protection", "flaggers", "barriers"], ""),

    # safety / PPE consumables
    ("SAF-CONE-28-CS", "Traffic Cones 28in Orange (case of 12)", "GuardTex Safety", "Safety / PPE",
     {"height": "28in", "color": "orange", "pack": "case/12"}, "case", 120, 205, "active",
     ["cones", "traffic", "orange", "28in", "safety", "case"], ""),
    ("SAF-VEST-CL2", "Hi-Vis Safety Vest Class 2 (box of 25)", "GuardTex Safety", "Safety / PPE",
     {"class": "ANSI Class 2", "pack": "box/25"}, "case", 190, 340, "active",
     ["vest", "hi-vis", "safety", "class 2", "ppe", "reflective"], ""),
    ("SAF-HARD-HAT", "Hard Hat Type II Vented (box of 20)", "GuardTex Safety", "Safety / PPE",
     {"type": "Type II", "pack": "box/20"}, "case", 260, 460, "active",
     ["hard", "hat", "helmet", "type ii", "ppe", "vented"], ""),
]


def _build_parts() -> list:
    return [Part(sku, name, brand, cat, specs, uom, cost, price, status, kw, mwbe)
            for (sku, name, brand, cat, specs, uom, cost, price, status, kw, mwbe) in _PARTS_RAW]


# --- availability: mostly deliverable, with two deliberate zero-availability lines
# HJ-40 jacks are on factory backorder everywhere (forces the HJ-45 alternate);
# FR-20 doors are discontinued (also forces an alternate). Everything else is available.
_ZERO_STOCK = {"ELV-HJ-40", "DOR-FR-20"}
_STOCK_OVERRIDE = {
    # sku: {location_index: units deliverable this cycle}
    "ELV-HYD-MOD-2S": {2: 4, 1: 2},
    "CAN-PLT-EXT-40": {1: 3, 2: 1},
    "ELV-HJ-45": {0: 6},
    "DOR-FR-22": {0: 28, 1: 12},
    "SAF-CONE-28-CS": {0: 210, 1: 180, 2: 140},
    "SIT-MPT-MO": {2: 24},
}


def _build_stock(parts: list) -> list:
    out = []
    for p in parts:
        if p.sku in _ZERO_STOCK:
            continue  # no availability lines -> on_hand == 0
        override = _STOCK_OVERRIDE.get(p.sku)
        if override:
            for wi, qty in override.items():
                out.append(StockLine(p.sku, WAREHOUSES[wi], qty, 5 if wi == 0 else 10))
        else:
            # a stable default spread from the sku hash (deterministic across runs)
            base = 40 + (stable_hash(p.sku) % 220)
            if p.uom in ("sf", "lf"):
                base *= 100          # bulk units of area / length
            out.append(StockLine(p.sku, WAREHOUSES[stable_hash(p.sku) % 3], base, 7))
    return out


# --- owners: one per standing/tier situation the guardrails need to exercise ------
_CUSTOMERS = [
    Customer("OWN-MTA", "MTA Construction & Development", "Dana Ruiz, Project Manager",
             "d.ruiz@mtacd.example", "B", 0.18, "ok", "Net 30 · 5% retainage",
             "Forte's anchor client: ADA design-build packages, elevator replacements, "
             "station renewals. Repeat scope across stations.", mwbe_goal=0.30),
    Customer("OWN-SCA", "NYC School Construction Authority", "Marcus Lee, Procurement",
             "m.lee@nycsca.example", "A", 0.30, "ok", "Net 45 · 5% retainage",
             "Deepest negotiated schedule (term contract). Margin discipline matters here.",
             mwbe_goal=0.30),
    Customer("OWN-DDC", "NYC Dept. of Design & Construction", "Priya Nair, Program Director",
             "p.nair@nycddc.example", "C", 0.10, "hold", "Net 30 · 10% retainage (BID HOLD)",
             "PASSPort prequalification renewal is pending, so Estimating placed new "
             "DDC submissions on hold until the chief estimator clears it.", mwbe_goal=0.30),
    Customer("OWN-ISLANDIA", "Islandia Medical Office Partners", "Sam Okafor, Owner's Rep",
             "s.okafor@imop.example", "B", 0.16, "ok", "Net 30 · 5% retainage",
             "Private developer, two-story medical office in Islandia. Clean, repeat work.",
             mwbe_goal=0.0),
]


# --- project history: gives the "same as the last station" line something to resolve to
_HISTORY = [
    OrderHistoryLine("OWN-MTA", "CAN-PLT-EXT-40", "platform canopy extension 40 LF, Kings Highway", 2, "2026-03-14"),
    OrderHistoryLine("OWN-MTA", "ELV-HYD-MOD-2S", "2-stop hydraulic elevator modernization", 3, "2026-03-14"),
    OrderHistoryLine("OWN-MTA", "SAF-CONE-28-CS", "orange traffic cones", 6, "2026-01-22"),
    OrderHistoryLine("OWN-SCA", "MAS-CMU-8", "8in reinforced CMU wall", 3200, "2026-05-02"),
    OrderHistoryLine("OWN-SCA", "DOR-FR-20", "FR-20 fire-rated doors", 12, "2025-11-30"),
    OrderHistoryLine("OWN-ISLANDIA", "STL-W12-26", "W12x26 steel beams furnished and erected", 30, "2026-04-11"),
]


# --- the inbox: solicitations, each exercising a different challenge -------------
def _build_rfqs() -> list:
    return [
        RFQ(
            id="BID-3101", customer_id="OWN-MTA",
            subject="Package 7 · ADA upgrades, 167th St — scope pricing needed by Monday",
            received_at="2026-09-11 08:12",
            body=(
                "Hi — can you price the following for the 167th Street station package? "
                "Need it by Monday for the design-build cost model.\n\n"
                "  - 2x hydraulic elevator modernization, 2-stop\n"
                "  - 1x the platform canopy extension we did at Kings Highway\n"
                "  - 3x Model HJ-40 hydraulic jack assembly\n"
                "  - 4x orange traffic cones\n\n"
                "Same terms as the Package 3 work. Thanks — Dana, MTA C&D"
            ),
            lines=[
                RFQLine("2x hydraulic elevator modernization, 2-stop", 2, "ELV-HYD-MOD-2S", "clean match"),
                RFQLine("1x the platform canopy extension we did at Kings Highway", 1, "CAN-PLT-EXT-40",
                        "ambiguous — must resolve against this owner's project history"),
                RFQLine("3x Model HJ-40 hydraulic jack assembly", 3, "ELV-HJ-40",
                        "on factory backorder everywhere — needs an alternate (HJ-45 supersedes it)"),
                RFQLine("4x orange traffic cones", 4, "SAF-CONE-28-CS", "clean match"),
            ],
            headline="The everyday solicitation: one clean line, one ambiguous 'same as Kings Highway', one backordered assembly needing an alternate.",
        ),
        RFQ(
            id="BID-3102", customer_id="OWN-SCA",
            subject="PS 118 addition · masonry, doors, curtain wall",
            received_at="2026-09-11 09:40",
            body=(
                "Marcus here. Please price under our term contract:\n\n"
                "  - 2400 sf 8in reinforced CMU wall\n"
                "  - 10x FR-20 fire-rated door assemblies (same as our Nov order)\n"
                "  - 1800 sf aluminum curtain wall, unitized\n\n"
                "Term-contract schedule rates apply. — SCA Procurement"
            ),
            lines=[
                RFQLine("2400 sf 8in reinforced CMU wall", 2400, "MAS-CMU-8",
                        "clean match, but the term-contract rate pushes the blended margin below the floor"),
                RFQLine("10x FR-20 fire-rated door assemblies", 10, "DOR-FR-20",
                        "discontinued spec — replaced by FR-22; needs an alternate"),
                RFQLine("1800 sf aluminum curtain wall, unitized", 1800, "GLZ-CW-ALU", "clean match"),
            ],
            headline="Term-contract owner: a discontinued spec to swap, and a deep negotiated rate that tests the margin floor.",
        ),
        RFQ(
            id="BID-3103", customer_id="OWN-DDC",
            subject="urgent: Bronx library sitework pricing",
            received_at="2026-09-11 10:05",
            body=(
                "Need these today if possible:\n\n"
                "  - 3000 sf sidewalk restoration to DOT spec\n"
                "  - 2x ADA concrete ramp with handrails\n"
                "  - 1200 lf 3/4in EMT conduit\n\n"
                "Standard DDC terms. — Priya, DDC"
            ),
            lines=[
                RFQLine("3000 sf sidewalk restoration to DOT spec", 3000, "SIT-SDW-CONC", "clean match"),
                RFQLine("2x ADA concrete ramp with handrails", 2, "ADA-RAMP-CONC", "clean match"),
                RFQLine("1200 lf 3/4in EMT conduit", 1200, "ELE-CND-EMT-075", "clean match"),
            ],
            headline="Every line resolves cleanly, but the owner is on a prequalification hold, so the agent must escalate instead of auto-submit.",
        ),
        RFQ(
            id="BID-3104", customer_id="OWN-ISLANDIA",
            subject="Medical office · structure & core pricing",
            received_at="2026-09-11 11:20",
            body=(
                "Sam at IMOP. Please price:\n\n"
                "  - 40 ton W12x26 structural steel, furnish and erect\n"
                "  - 12000 sf 4000 psi concrete slab on grade, 6in\n"
                "  - 2x 400A 3-phase distribution panel\n"
                "  - 120x quick-response fire sprinkler heads\n\n"
                "Thanks!"
            ),
            lines=[
                RFQLine("40 ton W12x26 structural steel, furnish and erect", 40, "STL-W12-26", "clean match"),
                RFQLine("12000 sf 4000 psi concrete slab on grade, 6in", 12000, "CON-SLB-4000", "clean match"),
                RFQLine("2x 400A 3-phase distribution panel", 2, "ELE-PNL-400", "clean match"),
                RFQLine("120x quick-response fire sprinkler heads", 120, "FPR-SPR-HD", "clean match"),
            ],
            headline="The happy path: everything deliverable, margins healthy, guardrails clear, and the agent executes end-to-end on its own.",
        ),
    ]


# --- illustrative precon pipeline (for the command-center charts) -----------------
# Deterministic, clearly synthetic: monthly bid volume + a backlog series. The
# estimates themselves are computed by the worker; this only feeds the trend visuals.
BID_HISTORY = [
    {"month": "2026-04", "bid_value": 41_200_000, "bids": 6},
    {"month": "2026-05", "bid_value": 58_900_000, "bids": 7},
    {"month": "2026-06", "bid_value": 36_400_000, "bids": 5},
    {"month": "2026-07", "bid_value": 104_000_000, "bids": 8},   # Package 7 award month
    {"month": "2026-08", "bid_value": 62_700_000, "bids": 7},
    {"month": "2026-09", "bid_value": 71_300_000, "bids": 9},
]

# committed backlog by month (history) + a forecast band as bids convert.
def backlog_series() -> dict:
    hist, fc = [], []
    base = 312.0  # $M
    months_h = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06",
                "2026-07", "2026-08", "2026-09"]
    steps = [0, -9, -7, 14, 22, -11, 96, -12, 18]
    v = base
    for m, s in zip(months_h, steps):
        v += s
        hist.append({"date": m, "balance_usd": round(v * 1e6)})
    months_f = ["2026-10", "2026-11", "2026-12", "2027-01", "2027-02", "2027-03"]
    for i, m in enumerate(months_f):
        p50 = v + 9 * (i + 1) - 4 * i
        spread = 11 * (i + 1)
        fc.append({"date": m, "p50_usd": round(p50 * 1e6),
                   "p10_usd": round((p50 - spread) * 1e6), "p90_usd": round((p50 + spread) * 1e6)})
    return {"history": hist, "forecast": fc}


_COMPANY: Company | None = None


def company() -> Company:
    """The one shared workspace (built once per process)."""
    global _COMPANY
    if _COMPANY is None:
        parts = _build_parts()
        _COMPANY = Company(
            name="Forte Construction Corp.",
            warehouses=list(WAREHOUSES),
            parts=parts,
            stock=_build_stock(parts),
            customers=list(_CUSTOMERS),
            history=list(_HISTORY),
            rfqs=_build_rfqs(),
        )
    return _COMPANY


# --- small serializers the web layer reuses -------------------------------------

def part_json(p: Part, on_hand: int | None = None) -> dict:
    d = asdict(p)
    if on_hand is not None:
        d["on_hand"] = on_hand
    return d


def rfq_summary(d: Company, r: RFQ) -> dict:
    c = d.customer(r.customer_id)
    return {"id": r.id, "customer": c.name if c else r.customer_id,
            "customer_id": r.customer_id, "subject": r.subject,
            "received_at": r.received_at, "n_lines": len(r.lines),
            "tier": c.tier if c else None, "credit_status": c.credit_status if c else None,
            "headline": r.headline}
