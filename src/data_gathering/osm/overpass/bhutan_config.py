# config_bhutan.py
#
# Bhutan / Thimphu Thromde configuration for CGT care-supply extraction.
# To support another country, copy this file and change the values.

CONFIG = {
    "country": "Bhutan",
    "pilot_area": "Thimphu Thromde",

    # Bounding box: (south, west, north, east)
    # Rough Thimphu Thromde box; swap for the official boundary polygon later.
    "bbox": (27.40, 89.60, 27.50, 89.68),

    # Each category defines how to find it BY TAG and BY NAME.
    # tags: list of (key, value) pairs. value=None means "key exists, any value".
    # name_patterns: regex fragments matched against the name (case-insensitive).
    "categories": {
        "eccd": {
            "tags": [
                ("amenity", "kindergarten"),
                ("amenity", "childcare"),
            ],
            "name_patterns": [
                "ECCD", "creche", "crèche", "day.?care",
                "child.?care", "early.?childhood", "montessori", "pre.?school",
            ],
        },
        "elderly_care": {
            "tags": [
                ("social_facility", "nursing_home"),
                ("social_facility", "assisted_living"),
                ("social_facility", "group_home"),
            ],
            "name_patterns": [
                "elderly", "old.?age", "senior", "geriatric", "aged.?care",
            ],
        },
        "disability_services": {
            "tags": [
                ("healthcare", "rehabilitation"),
                ("social_facility:for", "disabled"),
            ],
            "name_patterns": [
                "disabilit", "disabled", "rehabilitation", "special.?need",
                "draktsho", "ability",  # known Bhutan disability orgs
            ],
        },
        "community_care": {
            "tags": [
                ("amenity", "social_facility"),
                ("amenity", "social_centre"),
                ("amenity", "community_centre"),
            ],
            "name_patterns": [],  # leave empty where name search adds only noise
        },
    },

    # Tags to keep from each result, if present.
    "fields_of_interest": [
        "name", "name:en", "name:dz",
        "operator", "operator:type",
        "amenity", "healthcare", "social_facility", "social_facility:for",
        "capacity", "phone", "opening_hours",
        "wheelchair", "addr:full", "addr:street", "addr:city",
    ],
}