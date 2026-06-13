"""Adinkra identity data for Safety Rewards Tracker.

Departments are represented purely by real Adinkra symbols, names, meanings and
mottos -- there are no generic "Safety Team" labels anywhere in the product.

Every symbol image is a real file hosted on Wikimedia Commons. We reference each
file through the stable ``Special:FilePath`` endpoint, which 302-redirects to the
current upload URL, so the MVP always shows authentic symbols while keeping the
source attribution clear. Source and licence details live on each Commons file
page (https://commons.wikimedia.org/wiki/File:<filename>).
"""

from urllib.parse import quote

COMMONS_FILEPATH = "https://commons.wikimedia.org/wiki/Special:FilePath/"
COMMONS_FILE_PAGE = "https://commons.wikimedia.org/wiki/File:"


def symbol_url(commons_file, width=None):
    """Build a Wikimedia Commons Special:FilePath URL for a symbol file.

    ``width`` (optional) asks Commons for a scaled thumbnail, which keeps the
    UI light and gives every card a consistent symbol size.
    """
    url = COMMONS_FILEPATH + quote(commons_file)
    if width:
        url += "?width=%d" % int(width)
    return url


def file_page_url(commons_file):
    """Link back to the Commons file page for source + licence attribution."""
    return COMMONS_FILE_PAGE + quote(commons_file.replace(" ", "_"))


# Each entry is a real Adinkra symbol with a verified Wikimedia Commons file.
# Every Adinkra is the emblem of a real operational ``department`` -- so whenever
# a symbol is shown in the UI, its department is always attached to it.
# key -> name, meaning, motto, commons_file, department
DEPARTMENTS = [
    {
        "key": "akoben",
        "adinkra_name": "Akoben",
        "department": "HSE & Emergency Response",
        "meaning": "War horn — vigilance, alertness and a call to action",
        "motto": "We sound the alarm before harm.",
        "commons_file": "Akoben adinkra.png",
        "employee_count": 48,
    },
    {
        "key": "eban",
        "adinkra_name": "Eban",
        "department": "Security",
        "meaning": "Fence — safety, security and the protection of the home",
        "motto": "We fence out every hazard.",
        "commons_file": "Eban (Adinkra).png",
        "employee_count": 32,
    },
    {
        "key": "fihankra",
        "adinkra_name": "Fihankra",
        "department": "Site Services & Facilities",
        "meaning": "Compound house — safety, solidarity and brotherhood",
        "motto": "One compound, zero harm.",
        "commons_file": "Fihankra.png",
        "employee_count": 60,
    },
    {
        "key": "sankofa",
        "adinkra_name": "Sankofa",
        "department": "Training & Competency",
        "meaning": "Return and fetch it — learn from the past to protect the future",
        "motto": "We learn from every incident.",
        "commons_file": "Sankofa bird symbol.svg",
        "employee_count": 27,
    },
    {
        "key": "nkonsonkonson",
        "adinkra_name": "Nkonsonkonson",
        "department": "Logistics & Haulage",
        "meaning": "Chain links — unity and human relations; we are linked together",
        "motto": "Linked together, safe together.",
        "commons_file": "Nkonsonkonson.png",
        "employee_count": 41,
    },
    {
        "key": "dwennimmen",
        "adinkra_name": "Dwennimmen",
        "department": "Maintenance & Engineering",
        "meaning": "Ram's horns — strength tempered with humility",
        "motto": "Strong at work, humble in safety.",
        "commons_file": "Dwennimmen.svg",
        "employee_count": 35,
    },
    {
        "key": "nyansapo",
        "adinkra_name": "Nyansapo",
        "department": "Processing & Metallurgy",
        "meaning": "Wisdom knot — wisdom, ingenuity and patience",
        "motto": "Wise hands prevent harm.",
        "commons_file": "Nyansapo.svg",
        "employee_count": 22,
    },
    {
        "key": "adinkrahene",
        "adinkra_name": "Adinkrahene",
        "department": "Mining Operations",
        "meaning": "Chief of the Adinkra symbols — leadership, authority and charisma",
        "motto": "Leading safety from the front.",
        "commons_file": "Adinkrahene.svg",
        "employee_count": 18,
    },
]

# A couple of extra verified symbols kept for identity / branding use.
EXTRA_SYMBOLS = {
    "akoma_ntoso": {
        "adinkra_name": "Akoma Ntoaso",
        "meaning": "Linked hearts — understanding and agreement",
        "commons_file": "Akoma ntoso.svg",
    },
    "nkyinkyim": {
        "adinkra_name": "Nkyinkyim",
        "meaning": "Twisting — dynamism, adaptability and resourcefulness",
        "commons_file": "Nkyinkyim.svg",
    },
    "gye_nyame": {
        "adinkra_name": "Gye Nyame",
        "meaning": "Except God — the omnipotence and supremacy of God",
        "commons_file": "Gye Nyame (Adinkra Symbol).svg",
    },
    "funtunfunefu": {
        "adinkra_name": "Funtunfunefu Denkyemfunefu",
        "meaning": "Siamese crocodiles — unity in diversity, shared goals",
        "commons_file": "Funtunfunefu Denkyemfunefu.svg",
    },
}

# Brand mark for the app shell.
BRAND_SYMBOL = EXTRA_SYMBOLS["akoma_ntoso"]
