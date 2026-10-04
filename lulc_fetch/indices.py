"""Spectral index catalog and a safe formula evaluator.

Formulas use Sentinel-2 band names (B02 = blue, B03 = green, B04 = red, B05-B07 = red edge,
B08 = NIR, B8A = narrow NIR, B11/B12 = SWIR) on surface reflectance (0-1). Every catalog entry
is just a formula string, so built-in indices and user formulas go through the same evaluator.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

import numpy as np

S2_NAMES = ("B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B09", "B11", "B12")
SAR_NAMES = ("VV", "VH", "VVVH")  # Sentinel-1 backscatter in dB; VVVH = VV - VH (dB)
BAND_NAMES = S2_NAMES + SAR_NAMES

# Reference info shown in the UI: name -> (common name, short name, centre wavelength nm, equivalents elsewhere)
BAND_INFO = {
    "B01": ("Coastal aerosol", "Coastal", 443, "Landsat 8/9 SR_B1"),
    "B02": ("Blue", "Blue", 490, "Landsat SR_B2 · NAIP/RGB band 3"),
    "B03": ("Green", "Green", 560, "Landsat SR_B3 · NAIP/RGB band 2"),
    "B04": ("Red", "Red", 665, "Landsat SR_B4 · NAIP/RGB band 1"),
    "B05": ("Red edge 1", "RE1", 705, "Sentinel-2 only"),
    "B06": ("Red edge 2", "RE2", 740, "Sentinel-2 only"),
    "B07": ("Red edge 3", "RE3", 783, "Sentinel-2 only"),
    "B08": ("Near infrared (NIR)", "NIR", 842, "Landsat SR_B5 · NAIP band 4"),
    "B8A": ("Narrow NIR", "NIR2", 865, "Sentinel-2 only"),
    "B09": ("Water vapour", "WV", 945, "Sentinel-2 only"),
    "B11": ("Shortwave infrared 1 (SWIR1)", "SWIR1", 1610, "Landsat SR_B6"),
    "B12": ("Shortwave infrared 2 (SWIR2)", "SWIR2", 2190, "Landsat SR_B7"),
    "VV": ("Radar VV backscatter (dB)", "VV", None, "Sentinel-1 co-polarisation"),
    "VH": ("Radar VH backscatter (dB)", "VH", None, "Sentinel-1 cross-polarisation"),
    "VVVH": ("VV / VH ratio (dB difference)", "VV/VH", None, "Sentinel-1, computed on import"),
}

# Friendly names accepted in formulas, case-insensitive.
ALIASES = {
    "COASTAL": "B01", "BLUE": "B02", "GREEN": "B03", "RED": "B04", "RE1": "B05", "RE2": "B06",
    "RE3": "B07", "REDEDGE": "B05", "NIR": "B08", "NIR2": "B8A", "NARROWNIR": "B8A", "WV": "B09",
    "SWIR1": "B11", "SWIR2": "B12",
}

COLORMAPS = {
    "RdYlGn": ["#a50026", "#d73027", "#f46d43", "#fdae61", "#fee08b", "#ffffbf",
               "#d9ef8b", "#a6d96a", "#66bd63", "#1a9850", "#006837"],
    "BrBu": ["#8c510a", "#d8b365", "#f6e8c3", "#f5f5f5", "#c6dbef", "#6baed6", "#2171b5", "#08306b"],
    "YlOrRd": ["#ffffcc", "#ffeda0", "#fed976", "#feb24c", "#fd8d3c", "#fc4e2a", "#e31a1c", "#bd0026", "#800026"],
    "Radar": ["#000000", "#1f1f1f", "#4d4d4d", "#808080", "#b3b3b3", "#e0e0e0", "#ffffff"],
    "Blues": ["#f7fbff", "#deebf7", "#c6dbef", "#9ecae1", "#6baed6", "#4292c6", "#2171b5", "#08519c", "#08306b"],
    "Viridis": ["#440154", "#482878", "#3e4989", "#31688e", "#26828e", "#1f9e89", "#35b779", "#6ece58", "#b5de2b", "#fde725"],
    "Magma": ["#000004", "#1c1044", "#4f127b", "#812581", "#b5367a", "#e55064", "#fb8761", "#fec287", "#fcfdbf"],
    "Spectral": ["#9e0142", "#d53e4f", "#f46d43", "#fdae61", "#fee08b", "#ffffbf",
                 "#e6f598", "#abdda4", "#66c2a5", "#3288bd", "#5e4fa2"],
    "Greys": ["#000000", "#ffffff"],
    # India's AQI categories (CPCB) over 0–500 in steps of 50: Good · Satisfactory · Moderate · Poor · Very poor · Severe
    "AQI": ["#009966", "#009966", "#9ccc3c", "#ffde33", "#ffde33", "#ff9933", "#ff9933", "#e53935", "#e53935", "#7e0023", "#7e0023"],
}


@dataclass(frozen=True)
class Index:
    name: str
    title: str
    formula: str
    category: str
    description: str
    vmin: float = -1.0
    vmax: float = 1.0
    cmap: str = "RdYlGn"


_CATALOG = [
    # --- vegetation
    Index("NDVI", "Normalized Difference Vegetation Index", "(B08 - B04) / (B08 + B04)", "Vegetation",
          "Overall greenness / vegetation vigour. <0 water, 0–0.2 bare/built, 0.2–0.5 sparse, >0.5 dense vegetation.",
          -0.2, 0.9),
    Index("SAVI", "Soil Adjusted Vegetation Index", "1.5 * (B08 - B04) / (B08 + B04 + 0.5)", "Vegetation",
          "NDVI corrected for soil brightness. Better where vegetation cover is sparse (L = 0.5).", -0.2, 0.8),
    Index("MSAVI", "Modified Soil Adjusted Vegetation Index",
          "(2 * B08 + 1 - sqrt((2 * B08 + 1) ** 2 - 8 * (B08 - B04))) / 2", "Vegetation",
          "SAVI with a self-adjusting soil factor. Good for early crop stages and arid areas.", -0.2, 0.8),
    Index("OSAVI", "Optimized Soil Adjusted Vegetation Index", "(B08 - B04) / (B08 + B04 + 0.16)", "Vegetation",
          "SAVI variant (L = 0.16) tuned for agricultural canopies.", -0.2, 0.8),
    Index("EVI", "Enhanced Vegetation Index", "2.5 * (B08 - B04) / (B08 + 6 * B04 - 7.5 * B02 + 1)", "Vegetation",
          "Less saturated than NDVI over dense canopy, and corrects for atmosphere using the blue band.", -0.2, 0.8),
    Index("EVI2", "Two-band Enhanced Vegetation Index", "2.5 * (B08 - B04) / (B08 + 2.4 * B04 + 1)", "Vegetation",
          "EVI without the blue band. Works with any red + NIR imagery.", -0.2, 0.8),
    Index("GNDVI", "Green Normalized Difference Vegetation Index", "(B08 - B03) / (B08 + B03)", "Vegetation",
          "Uses green instead of red. More sensitive to chlorophyll concentration.", -0.2, 0.9),
    Index("NDRE", "Normalized Difference Red Edge", "(B08 - B05) / (B08 + B05)", "Vegetation",
          "Red-edge index for crop nitrogen / chlorophyll. Keeps working in dense canopy where NDVI saturates.", -0.2, 0.7),
    Index("CIre", "Chlorophyll Index (red edge)", "B08 / B05 - 1", "Vegetation",
          "Proportional to canopy chlorophyll content.", 0, 6),
    Index("ARVI", "Atmospherically Resistant Vegetation Index",
          "(B08 - (2 * B04 - B02)) / (B08 + (2 * B04 - B02))", "Vegetation",
          "NDVI corrected for aerosol scattering using the blue band. Useful in hazy imagery.", -0.2, 0.9),
    Index("NDCI", "Normalized Difference Chlorophyll Index", "(B05 - B04) / (B05 + B04)", "Water",
          "Chlorophyll-a / algal blooms in water bodies.", -0.2, 0.4, "Viridis"),
    # --- RGB-only (drones, NAIP, any RGB image)
    Index("VARI", "Visible Atmospherically Resistant Index", "(B03 - B04) / (B03 + B04 - B02)", "RGB only",
          "Vegetation from RGB only. Good for drone and phone imagery.", -0.5, 0.5),
    Index("GLI", "Green Leaf Index", "(2 * B03 - B04 - B02) / (2 * B03 + B04 + B02)", "RGB only",
          "RGB greenness index for vegetation / leaf cover.", -0.3, 0.3),
    Index("ExG", "Excess Green", "2 * B03 - B04 - B02", "RGB only",
          "Simple RGB vegetation separation. Values are in reflectance units.", -0.1, 0.2),
    # --- water / moisture
    Index("NDWI", "Normalized Difference Water Index (McFeeters)", "(B03 - B08) / (B03 + B08)", "Water",
          "Open water. Values > 0 usually indicate water.", -0.6, 0.6, "BrBu"),
    Index("MNDWI", "Modified NDWI", "(B03 - B11) / (B03 + B11)", "Water",
          "Water index using SWIR. Separates water from built-up areas better than NDWI.", -0.6, 0.6, "BrBu"),
    Index("AWEI", "Automated Water Extraction Index (no shadow)", "4 * (B03 - B11) - (0.25 * B08 + 2.75 * B12)", "Water",
          "Water extraction that suppresses non-water dark surfaces. Values > 0 indicate water.", -1, 0.5, "BrBu"),
    Index("NDMI", "Normalized Difference Moisture Index", "(B08 - B11) / (B08 + B11)", "Water",
          "Vegetation water content / drought stress.", -0.5, 0.6, "BrBu"),
    # --- built-up / soil
    Index("NDBI", "Normalized Difference Built-up Index", "(B11 - B08) / (B11 + B08)", "Built-up & soil",
          "Built-up areas and bare soil have positive values.", -0.5, 0.4, "YlOrRd"),
    Index("UI", "Urban Index", "(B12 - B08) / (B12 + B08)", "Built-up & soil",
          "Urban / impervious surfaces, using the long SWIR band.", -0.6, 0.4, "YlOrRd"),
    Index("BSI", "Bare Soil Index", "((B11 + B04) - (B08 + B02)) / ((B11 + B04) + (B08 + B02))", "Built-up & soil",
          "Bare soil and fallow land have high values.", -0.4, 0.4, "YlOrRd"),
    Index("NDTI", "Normalized Difference Tillage Index", "(B11 - B12) / (B11 + B12)", "Built-up & soil",
          "Crop residue cover / tillage practice.", -0.1, 0.3, "Viridis"),
    # --- fire / burn
    Index("NBR", "Normalized Burn Ratio", "(B08 - B12) / (B08 + B12)", "Fire",
          "Burned areas show low values. Compare dates (dNBR) for burn severity.", -0.5, 0.8),
    Index("NBR2", "Normalized Burn Ratio 2", "(B11 - B12) / (B11 + B12)", "Fire",
          "Post-fire recovery and burn sensitivity using both SWIR bands.", -0.2, 0.4),
    # --- snow
    Index("NDSI", "Normalized Difference Snow Index", "(B03 - B11) / (B03 + B11)", "Snow",
          "Snow and ice. Values > 0.4 typically indicate snow.", -0.5, 1, "Blues"),
    # --- radar (Sentinel-1 backscatter in dB; 10 ** (x / 10) converts dB to linear power)
    Index("RVI", "Radar Vegetation Index (dual-pol)", "4 * 10 ** (VH / 10) / (10 ** (VV / 10) + 10 ** (VH / 10))",
          "Radar (SAR)", "Volume scattering from vegetation. Higher in forests and crops, low over water, bare soil "
          "and cities. Works through clouds.", 0, 1),
    Index("CPR", "Cross-polarisation ratio VH − VV (dB)", "VH - VV", "Radar (SAR)",
          "Higher (less negative) for vegetation, lower for bare soil, built-up areas and water.", -20, -4, "Viridis"),
    Index("NDPI", "Normalized Difference Polarisation Index",
          "(10 ** (VV / 10) - 10 ** (VH / 10)) / (10 ** (VV / 10) + 10 ** (VH / 10))", "Radar (SAR)",
          "Contrast between co- and cross-polarised backscatter. High for bare / urban surfaces, lower for vegetation.",
          0, 1, "Viridis"),
    Index("VVdB", "VV backscatter (dB)", "VV", "Radar (SAR)",
          "Calibrated sigma0 VV. Open water is very dark (< −18 dB); cities are bright.", -25, 0, "Radar"),
    Index("VHdB", "VH backscatter (dB)", "VH", "Radar (SAR)",
          "Calibrated sigma0 VH. Sensitive to vegetation volume. Water is very dark (< −24 dB).", -30, -5, "Radar"),
]

CATALOG: dict[str, Index] = {i.name: i for i in _CATALOG}
CATEGORIES = list(dict.fromkeys(i.category for i in _CATALOG))

# Indices appended to Sentinel-2 downloads by default (`--indices`).
INDICES = ["NDVI", "NDWI", "MNDWI", "NDBI", "BSI", "SAVI"]


# ------------------------------------------------------------------ safe formula evaluation

_FUNCS = {"sqrt": np.sqrt, "abs": np.abs, "log": np.log, "log10": np.log10, "exp": np.exp,
          "min": np.fmin, "max": np.fmax}
_BINOPS = {ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply, ast.Div: np.divide, ast.Pow: np.power}


def normalize_band(name: str) -> str | None:
    """'b8' / 'B8' / 'nir' / 'B08' -> 'B08'. None if not a band."""
    up = name.upper()
    if up in ALIASES:
        return ALIASES[up]
    if up in SAR_NAMES:
        return up
    m = re.fullmatch(r"B0?(\d{1,2})(A?)", up)
    if m:
        cand = "B8A" if m.group(1) == "8" and m.group(2) else f"B{int(m.group(1)):02d}"
        return cand if cand in S2_NAMES else None
    return None


def parse_formula(formula: str) -> tuple[ast.Expression, set[str]]:
    """Validate a formula and return (tree, required bands). Raises ValueError with a readable message."""
    formula = formula.replace("^", "**").strip()
    if not formula:
        raise ValueError("The formula is empty")
    if len(formula) > 500:
        raise ValueError("The formula is too long")
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"Formula syntax error near character {e.offset}: {formula}") from None
    bands: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if node.id in _FUNCS:
                continue
            band = normalize_band(node.id)
            if not band:
                raise ValueError(f"Unknown name '{node.id}'. Use band names like B04, B08 or RED, NIR, SWIR1.")
            bands.add(band)
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS or node.keywords:
                raise ValueError(f"Only these functions are allowed: {', '.join(_FUNCS)}")
        elif isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
                raise ValueError("Only numbers are allowed as constants")
        elif not isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Load, ast.USub, ast.UAdd,
                                   *_BINOPS)):
            raise ValueError(f"'{type(node).__name__}' is not allowed in a formula")
    if not bands:
        raise ValueError("The formula must use at least one band")
    return tree, bands


def evaluate(formula: str, bands: dict[str, np.ndarray]) -> np.ndarray:
    """Evaluate a formula on band arrays (keys are S2 names). Division by zero / invalid -> NaN."""
    tree, needed = parse_formula(formula)
    missing = sorted(needed - set(bands))
    if missing:
        raise ValueError(f"This image has no {', '.join(missing)} band — the formula can't be computed")

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            return bands[normalize_band(node.id)]
        if isinstance(node, ast.UnaryOp):
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.BinOp):
            return _BINOPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.Call):
            return _FUNCS[node.func.id](*[ev(a) for a in node.args])
        raise ValueError("Unsupported expression")

    with np.errstate(all="ignore"):
        out = np.asarray(ev(tree), dtype="float32")
    if out.ndim == 0:
        raise ValueError("The formula must use at least one band")
    out[~np.isfinite(out)] = np.nan
    return out


def required_bands(formula: str) -> set[str]:
    return parse_formula(formula)[1]


def compute_indices(bands: dict[str, np.ndarray], names=None) -> tuple[list[np.ndarray], list[str]]:
    """Compute every named index whose input bands are available. Returns (arrays, names)."""
    out, labels = [], []
    for name in names or INDICES:
        idx = CATALOG[name]
        if required_bands(idx.formula) <= set(bands):
            out.append(evaluate(idx.formula, bands))
            labels.append(name)
    return out, labels
