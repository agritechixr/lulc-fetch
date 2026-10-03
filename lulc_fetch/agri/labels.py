"""Friendly names for crops and model class labels (from the disease repository's labels.py).

Folder names and model labels stay unchanged as keys; these helpers only make the text people read."""

from __future__ import annotations

import re

# Crops whose photo model identifies varieties, not diseases (no disease photos yet)
VARIETY_MODELS = {"Mulberry"}

# Crops added from LeafNet and field photos: the knowledge base only describes symptoms (no treatments)
LIMITED_KB = {"Tomato", "Maize", "Coffee", "Sugarcane", "Grape", "Cassava", "Potato", "Tea", "Cucumber", "Black_Pepper", "Peach",
              "Citrus", "Apricot", "Betel", "Bitter_gourd", "Bottle_gourd", "Chrysanthemum", "Cucurbit", "Fig", "Loquat", "Pear",
              "Ridge_gourd", "Snake_gourd", "Walnut", "Rice", "Soybean"}

# Leaves the added-crops detector recognises (so they aren't taken for another crop) but no disease model diagnoses
RECOGNISED_ONLY = {"Pepper", "Raspberry", "Sorghum", "Squash"}

CROP_DISPLAY = {
    "Black_Pepper": "Black Pepper",
    "Citrus": "Citrus (Orange / Lemon)",
    "Cucurbit": "Cucurbit (Pumpkin / Melon)",
    "pomrgranet": "Pomegranate",
    "okra": "Okra",
    "Custard_apple": "Custard Apple",
}

LABEL_DISPLAY = {
    "Apple/Apple_Scab_rust": "Apple Scab & Rust",
    "Apple/Apple___scab": "Apple Scab",
    "Apple/marsonina_blotch": "Marssonina Blotch",
    "Apple/root_rot_collar_rot": "Root Rot / Collar Rot",
    "Banana/Augmented Banana Insect Pest Disease": "Insect Pest Damage",
    "Banana/Banana_panama_disease": "Panama Disease (Fusarium Wilt)",
    "Brinjal/eggplant__FB": "Shoot and Fruit Borer",
    "Brinjal/eggplant__MIT_EB": "MIT/EB Pest Damage",
    "Cashew/Cashew_gumosis": "Gummosis",
    "Coconut/CCI_Caterpillars": "Coconut Caterpillar Infestation (caterpillars)",
    "Coconut/CCI_Leaflets": "Coconut Caterpillar Infestation (leaflet damage)",
    "Coconut/Healthy_Leaves": "Healthy",
    "Coconut/WCLWD_DryingofLeaflets": "Weligama Coconut Leaf Wilt — drying of leaflets",
    "Coconut/WCLWD_Flaccidity": "Weligama Coconut Leaf Wilt — flaccidity",
    "Coconut/WCLWD_Yellowing": "Weligama Coconut Leaf Wilt — yellowing",
    "Custard_apple/Athracnose": "Anthracnose",
    "Custard_apple/Blank Canker": "Black Canker",
    "Custard_apple/Custurd_apple Stressed": "Stressed Plant",
    "Custard_apple/Leaf spot on Leaves": "Leaf Spot (leaves)",
    "Custard_apple/Leaf spot on fruit": "Leaf Spot (fruit)",
    "Guava/Guava_Rust_leaf_spot": "Rust / Leaf Spot",
    "Guava/Tea_Mosquito_Bug_Thrips": "Tea Mosquito Bug / Thrips",
    "Guava/multiple": "Multiple Diseases",
    "Guava/yld": "YLD",
    "Mulberry/05 TaiwanStraberry": "Taiwan Strawberry (variety)",
    "Papaya/BacterialSpot": "Bacterial Spot",
    "Papaya/Curl": "Leaf Curl",
    "Rose/Rose___slug_sawfly": "Rose Slug (Sawfly)",
    "okra/Leaf curly virus": "Leaf Curl Virus",
}

_CROP_WORDS = {
    "Brinjal": {"eggplant", "brinjal"},
    "Custard_apple": {"custard", "custurd", "apple"},
}

# Everyday names for a crop (English, Hindi), for the crop search box
CROP_ALIASES = {
    "Maize": ["corn", "makka", "makkai"],
    "Rice": ["paddy", "dhan", "chawal"],
    "Brinjal": ["eggplant", "aubergine", "baingan"],
    "okra": ["lady finger", "ladyfinger", "bhindi"],
    "pomrgranet": ["pomegranate", "anar"],
    "Citrus": ["orange", "lemon", "lime", "mosambi", "kinnow", "santra", "nimbu"],
    "Cucurbit": ["pumpkin", "melon", "muskmelon", "squash"],
    "Bitter_gourd": ["karela"],
    "Bottle_gourd": ["lauki", "ghiya"],
    "Ridge_gourd": ["turai", "torai", "tori"],
    "Snake_gourd": ["chichinda"],
    "Soybean": ["soya", "soy", "soyabean"],
    "Sugarcane": ["ganna"],
    "Potato": ["aloo", "alu"],
    "Tomato": ["tamatar"],
    "Mango": ["aam"],
    "Guava": ["amrud", "amrood"],
    "Black_Pepper": ["pepper", "kali mirch"],
    "Betel": ["paan", "pan leaf"],
    "Custard_apple": ["sitaphal", "sharifa"],
    "Cucumber": ["kheera", "khira"],
    "Coconut": ["nariyal"],
    "Banana": ["kela"],
    "Grape": ["angoor"],
}

# crop-detector spellings that differ from the crop folder names
_CROP_KEY_ALIASES = {"castardapple": "custardapple", "pomegranate": "pomrgranet"}


def crop_display(crop: str) -> str:
    if not crop:
        return crop
    return CROP_DISPLAY.get(crop, crop.replace("_", " ").strip().title())


def label_display(crop: str, label: str) -> str:
    """'Apple___gray_spot' → 'Gray Spot', 'Augmented Banana Moko Disease' → 'Moko Disease'."""
    if not label:
        return label
    if f"{crop}/{label}" in LABEL_DISPLAY:
        return LABEL_DISPLAY[f"{crop}/{label}"]
    words = label.replace("_", " ").split()
    if words and words[0].lower() == "augmented":
        words = words[1:]
    crop_words = _CROP_WORDS.get(crop, {crop.lower().replace("_", " ")})
    while len(words) > 1 and words[0].lower() in crop_words:
        words = words[1:]
    if crop in VARIETY_MODELS and words and re.fullmatch(r"\d+", words[0]):
        return " ".join(words[1:]) + " (variety)"
    text = " ".join(w if any(c.isupper() for c in w[1:]) else w.capitalize() for w in words)
    return "Healthy" if text.lower().startswith("healthy") else text


def is_healthy(label: str) -> bool:
    return "healthy" in (label or "").lower()


def _key(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalpha())


def match_crop(predicted: str, crops) -> str | None:
    """A crop detector's label → crop folder name (case, spaces, underscores and known misspellings ignored)."""
    by_key = {_key(c): c for c in crops}
    key = _key(predicted)
    key = _CROP_KEY_ALIASES.get(key, key)
    if key in by_key:
        return by_key[key]
    reverse = {v: k for k, v in _CROP_KEY_ALIASES.items()}
    return by_key.get(reverse.get(key, ""))
