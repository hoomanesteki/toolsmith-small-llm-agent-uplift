"""Word pools for world generation.

Literal lists rather than a generator library, for one reason: reproducibility.
``faker`` changes its corpora between releases, which would silently change a
world digest and therefore silently change ground truth. These lists are part of
the repository and part of the hash.

They are also deliberately international and gender-neutral in construction:
given names and family names are combined freely, so the population does not
encode assumptions about who works where.
"""

from __future__ import annotations

GIVEN_NAMES: tuple[str, ...] = (
    "Ada",
    "Amara",
    "Anders",
    "Anika",
    "Arun",
    "Asha",
    "Bao",
    "Beatriz",
    "Bram",
    "Camila",
    "Cato",
    "Chidi",
    "Dara",
    "Diego",
    "Eero",
    "Elif",
    "Emeka",
    "Esther",
    "Farid",
    "Freya",
    "Gabriel",
    "Giulia",
    "Hana",
    "Hugo",
    "Idris",
    "Ilana",
    "Imani",
    "Ines",
    "Jae",
    "Janan",
    "Jonas",
    "Juno",
    "Kaveh",
    "Keiko",
    "Kwame",
    "Lars",
    "Leila",
    "Linnea",
    "Lucia",
    "Mads",
    "Mai",
    "Malik",
    "Marta",
    "Mateo",
    "Mira",
    "Nadia",
    "Nico",
    "Nour",
    "Oona",
    "Oscar",
    "Paloma",
    "Petra",
    "Quinn",
    "Rafael",
    "Rania",
    "Reza",
    "Rosa",
    "Sanne",
    "Sasha",
    "Selin",
    "Sina",
    "Soren",
    "Tariq",
    "Tessa",
    "Thabo",
    "Tomas",
    "Uma",
    "Vera",
    "Viktor",
    "Wanjiru",
    "Xiulan",
    "Yara",
    "Yusuf",
    "Zaid",
    "Zofia",
)

FAMILY_NAMES: tuple[str, ...] = (
    "Adeyemi",
    "Ahmadi",
    "Almeida",
    "Andersson",
    "Bakker",
    "Barros",
    "Berger",
    "Bianchi",
    "Chen",
    "Correia",
    "Dahl",
    "Delacroix",
    "Dubois",
    "Eriksen",
    "Fernandes",
    "Fischer",
    "Gallo",
    "Gerber",
    "Halonen",
    "Hassan",
    "Hoffman",
    "Ibrahim",
    "Ilves",
    "Jansen",
    "Karlsson",
    "Kaur",
    "Keller",
    "Khoury",
    "Kovacs",
    "Laurent",
    "Lindqvist",
    "Lombardi",
    "Maalouf",
    "Marchetti",
    "Mbeki",
    "Meyer",
    "Moreau",
    "Nakamura",
    "Nascimento",
    "Novak",
    "Okafor",
    "Olsen",
    "Pereira",
    "Petrov",
    "Rahimi",
    "Ramos",
    "Reyes",
    "Rossi",
    "Saito",
    "Salazar",
    "Schneider",
    "Silva",
    "Sorensen",
    "Tanaka",
    "Tavares",
    "Thorne",
    "Vargas",
    "Virtanen",
    "Wagner",
    "Wallace",
    "Yilmaz",
    "Zhang",
    "Zielinski",
)

REGIONS: tuple[str, ...] = ("EMEA", "AMER", "APAC", "LATAM")

TIERS: tuple[str, ...] = ("free", "standard", "premium", "enterprise")

PRODUCTS: tuple[str, ...] = (
    "Anvil Desk Lamp",
    "Basalt Keyboard",
    "Cedar Monitor Arm",
    "Delta Webcam",
    "Ember Space Heater",
    "Fathom Headphones",
    "Granite Mousepad",
    "Harbour Dock",
    "Ivory Notebook",
    "Juniper Diffuser",
    "Kestrel Drone",
    "Lumen Ring Light",
    "Meridian Chair",
    "Nimbus Speaker",
    "Onyx Stylus",
    "Pallas Tripod",
    "Quill Pen Set",
    "Rivet Toolkit",
    "Solace Blanket",
    "Tundra Water Bottle",
    "Umber Backpack",
    "Vantage Standing Desk",
    "Willow Air Purifier",
    "Zephyr Fan",
)

ORDER_STATUSES: tuple[str, ...] = (
    "pending",
    "processing",
    "shipped",
    "delivered",
    "cancelled",
    "returned",
)

CARRIERS: tuple[str, ...] = ("Northline", "Skyward", "Pallas Freight", "Bluewater")

TICKET_SUBJECTS: tuple[str, ...] = (
    "Package arrived damaged",
    "Wrong item shipped",
    "Delivery is late",
    "Requesting a refund",
    "Cannot apply discount code",
    "Address change needed",
    "Item missing from box",
    "Duplicate charge on card",
    "Warranty question",
    "Return label not received",
)

TICKET_STATUSES: tuple[str, ...] = ("open", "pending_customer", "escalated", "resolved", "closed")
PRIORITIES: tuple[str, ...] = ("low", "normal", "high", "urgent")
AGENTS: tuple[str, ...] = ("r.okonkwo", "s.lindqvist", "m.haddad", "t.oyelaran", "j.park")

# ------------------------------------------------------------------ clinic ---

SPECIALTIES: tuple[str, ...] = (
    "cardiology",
    "dermatology",
    "endocrinology",
    "gastroenterology",
    "neurology",
    "oncology",
    "orthopaedics",
    "paediatrics",
    "pulmonology",
    "rheumatology",
)

APPOINTMENT_STATUSES: tuple[str, ...] = (
    "scheduled",
    "checked_in",
    "completed",
    "cancelled",
    "no_show",
)

VISIT_REASONS: tuple[str, ...] = (
    "annual review",
    "follow-up",
    "new symptom assessment",
    "medication review",
    "post-operative check",
    "imaging review",
    "vaccination",
    "referral consult",
)

CLINIC_SITES: tuple[str, ...] = ("Ashgrove", "Belmont", "Carrow", "Dunmore")

COVERAGE_PLANS: tuple[str, ...] = ("basic", "standard", "extended", "public")

REFERRAL_STATUSES: tuple[str, ...] = ("open", "awaiting_triage", "accepted", "declined", "closed")
