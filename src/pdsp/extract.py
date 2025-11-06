from __future__ import annotations
from typing import Dict, Any, List, Optional, Tuple
import os
import re
from pdsp.normalize import canonical_key, normalize_awg_or_mm2

# Try importing pdfplumber; fall back gracefully if not installed
try:
    import pdfplumber
except Exception:
    pdfplumber = None


# =====================================================
#                SHARED UTILITY FUNCTIONS
# =====================================================

def _read_text_sample(pdf_path: str, pages: int = 6) -> str:
    """
    Reads text from the first few pages of a PDF using pdfplumber.
    Returns empty string if the library is missing or reading fails.
    """
    if pdfplumber is None:
        return ""
    try:
        out: List[str] = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages[:pages]:
                out.append(page.extract_text() or "")
        return "\n".join(out)
    except Exception:
        return ""


def _rx_first(pattern: str, text: str, flags: int = re.I) -> Optional[re.Match]:
    """
    Safe regex search returning the first match object or None.
    """
    return re.search(pattern, text, flags)


def _try_parse_float(s: str) -> Optional[float]:
    """
    Attempt to parse a string into float, accepting commas as decimal separators.
    Returns None on failure.
    """
    try:
        return float(s.replace(",", "."))
    except Exception:
        return None


# =====================================================
#                BINDER SHEET PARSING
# =====================================================

def _looks_like_binder_sheet(text: str) -> bool:
    """
    Detect whether a PDF's text likely belongs to the Binder CB-S 260 datasheet.
    Heuristic: contains 'Binder', 'CB-S', and 'CO2' keywords.
    """
    t = text.lower()
    return ("binder" in t) and ("cb-s" in t or "cb s" in t) and ("co2" in t or "co₂" in t)


def _parse_temperature_range(text: str) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    """
    Extract a temperature range, e.g. 'Temperature range: +6 °C to +50 °C'.
    Returns (pretty_text, min_C, max_C).
    """
    m = _rx_first(r"Temperature\s*range\s*:\s*\+?(\d{1,3})\s*°C.*?\+?(\d{1,3})\s*°C", text)
    if m:
        a, b = m.group(1), m.group(2)
        return f"{a}–{b}", _try_parse_float(a), _try_parse_float(b)

    # fallback pattern
    m = _rx_first(r"from\s*\+?(\d{1,3})\s*°C\s*(?:to|–|-)\s*\+?(\d{1,3})\s*°C", text)
    if m:
        a, b = m.group(1), m.group(2)
        return f"{a}–{b}", _try_parse_float(a), _try_parse_float(b)

    return None, None, None


def _parse_co2_range(text: str) -> Optional[str]:
    """
    Extract CO₂ concentration range, e.g. '0 to 20 vol.%'.
    Returns '0–20' or None if not found.
    """
    m = _rx_first(r"CO\s*2[^%\n]*?(?:range|:)?\s*0\s*(?:to|–|-)\s*([0-9]{1,2})\s*(?:vol\.?%|%)", text)
    if m:
        return f"0–{m.group(1)}"
    return None


def _parse_nominal_power_kw(text: str) -> Optional[float]:
    """
    Extract nominal power in kW, e.g. 'Power 0.9 kW'.
    """
    m = _rx_first(r"(?:Nominal\s*power|Power)\s*:?\s*([0-9]+[.,]?[0-9]*)\s*kW", text)
    if m:
        return _try_parse_float(m.group(1))
    return None


def _parse_dimensions_mm(text: str) -> List[Tuple[str, float]]:
    """
    Grab the first three 'NNN mm' values and interpret as width, height, depth.
    This is approximate but useful for basic dimension parsing.
    """
    nums = re.findall(r"(\d{2,4})\s*mm", text)
    out: List[Tuple[str, float]] = []
    if len(nums) >= 3:
        try:
            w, h, d = float(nums[0]), float(nums[1]), float(nums[2])
            out += [("width_mm", w), ("height_mm", h), ("depth_mm", d)]
        except Exception:
            pass
    return out


def _parse_binder_cb_s_260(pdf_path: str, text: str) -> List[Dict[str, Any]]:
    """
    Parse the Binder Model CB-S 260 datasheet into one structured product.
    Returns a list with a single product dict.
    """
    brand = "BINDER"
    family = "CB-S"
    model_no = "CBS260-230V"  # chosen variant string for consistency in queries

    specs: List[Dict[str, Any]] = []

    # --- Temperature range ---
    temp_pretty, tmin, tmax = _parse_temperature_range(text)
    if temp_pretty:
        specs.append({"spec_key": "temp_range_c", "spec_value_text": temp_pretty, "unit": "°C", "raw": temp_pretty})
    if tmin is not None:
        specs.append({"spec_key": "temp_min_c", "spec_value_num": tmin, "unit": "°C", "raw": f"{tmin} °C"})
    if tmax is not None:
        specs.append({"spec_key": "temp_max_c", "spec_value_num": tmax, "unit": "°C", "raw": f"{tmax} °C"})

    # --- CO₂ range ---
    co2 = _parse_co2_range(text)
    if co2:
        specs.append({
            "spec_key": "co2_range_percent",
            "spec_value_text": co2,
            "unit": "%",
            "raw": co2.replace("–", " to ")
        })

    # --- Power ---
    p_kw = _parse_nominal_power_kw(text)
    if p_kw is not None:
        specs.append({"spec_key": "nominal_power_kw", "spec_value_num": p_kw, "unit": "kW", "raw": f"{p_kw} kW"})

    # --- Dimensions ---
    for key, val in _parse_dimensions_mm(text):
        specs.append({"spec_key": key, "spec_value_num": val, "unit": "mm", "raw": f"{val} mm"})

    product: Dict[str, Any] = {
        "brand": brand,
        "family": family,
        "model_no": model_no,
        "article_number": None,
        "ordering_code": None,
        "product_name": "Model CB-S 260 | CO2 incubator",
        "description": None,
        "interfaces": None,
        "source_pdf": os.path.basename(pdf_path),
        "pages_covered": [1, 2, 3, 4, 5, 6],
        "provenance": {
            "strategy": "binder_cb_s_260_text",
            "notes": [
                "regex extraction across first 6 pages",
                "fields skipped safely if missing"
            ],
        },
        "specs": specs,
    }
    return [product]


# =====================================================
#                M12 CATALOG PARSING
# =====================================================

def _looks_like_m12_catalog(text: str) -> bool:
    """
    Detect M12 catalog by checking for domain-specific keywords.
    """
    t = text.lower()
    hints = ["m12", "serie 713", "serie 763", "sensorik", "aktorik"]
    return any(h in t for h in hints)


def _parse_m12_catalog(pdf_path: str, text: str) -> List[Dict[str, Any]]:
    """
    Parse the M12 Sensor/Aktorik catalog into structured variants.
    - Uses regex only (no table parsing).
    - One product per ordering code if found.
    """
    products: List[Dict[str, Any]] = []
    base_specs: List[Dict[str, Any]] = []

    # --- Ordering codes ---
    # Ordering codes like "99 0429 43 04" or "9904294304" with optional spaces
    ordering_codes = re.findall(r"(\d{2}\s?\d{4}\s?\d{2}\s?\d{2})", text)
    if not ordering_codes:
        ordering_codes = re.findall(r"(\d{2}[-\s]?\d{4}[-\s]?\d{2}[-\s]?\d{2})", text)


    # --- IP rating ---
    ip_m = _rx_first(r"(IP\s*6[7-9]K?)", text)
    if ip_m:
        base_specs.append({"spec_key": "ip_rating", "spec_value_text": ip_m.group(1), "raw": ip_m.group(0)})

    # --- Temperature bounds ---
    temp_vals = re.findall(r"([-+]?\d{1,3})\s*°C", text)
    if temp_vals:
        nums = [int(v) for v in temp_vals]
        base_specs.append({"spec_key": "temp_min_c", "spec_value_num": float(min(nums)), "unit": "°C", "raw": str(nums)})
        base_specs.append({"spec_key": "temp_max_c", "spec_value_num": float(max(nums)), "unit": "°C", "raw": str(nums)})

    # --- Wire gauge (normalized through shared helper) ---
    wg_m = _rx_first(r"(AWG\s*\d{1,2}|\d+(?:[.,]\d+)?\s*mm(?:2|²))", text)
    if wg_m:
        val, unit, raw = normalize_awg_or_mm2(wg_m.group(1))
        if val is not None:
            base_specs.append({"spec_key": "wire_gauge_mm2", "spec_value_num": val, "unit": unit or "mm2", "raw": raw})
        else:
            base_specs.append({"spec_key": "wire_gauge_mm2", "spec_value_text": raw, "unit": unit or "mm2", "raw": raw})

    # --- Build product entries ---
    if ordering_codes:
        # multiple variant rows
        for code in ordering_codes:
            products.append({
                "brand": None,
                "family": "M12 Series",
                "model_no": None,
                "article_number": None,
                "ordering_code": code,
                "product_name": "M12 cable connector (variant)",
                "description": None,
                "interfaces": None,
                "source_pdf": os.path.basename(pdf_path),
                "pages_covered": [1, 2, 3, 4, 5, 6],
                "provenance": {"strategy": "m12_catalog_regex", "notes": ["ordering codes + base specs"]},
                "specs": list(base_specs),
            })
    else:
        # fallback single record
        products.append({
            "brand": None,
            "family": "M12 Series",
            "model_no": None,
            "article_number": None,
            "ordering_code": None,
            "product_name": "M12 connector (catalog fallback)",
            "description": None,
            "interfaces": None,
            "source_pdf": os.path.basename(pdf_path),
            "pages_covered": [1, 2, 3, 4, 5, 6],
            "provenance": {"strategy": "m12_catalog_regex", "notes": ["no ordering codes found"]},
            "specs": list(base_specs),
        })

    return products

# =====================================================
#            TECHNICAL INFORMATION PARSING
# =====================================================

def _looks_like_technical_info_pdf(text: str) -> bool:
    """
    Detects the general technical information PDF (often contains German headers
    like 'Technische Informationen' or 'Allgemeine Hinweise').
    """
    t = text.lower()
    hints = ["technische", "informationen", "hinweise", "querschnitt", "awg"]
    return any(h in t for h in hints)


def _parse_technical_info_pdf(pdf_path: str, text: str) -> List[Dict[str, Any]]:
    """
    Parse the 'Technische Informationen' PDF to extract generic reference data.
    These rows are stored as 'Reference Data' products for lookup use.
    """
    products: List[Dict[str, Any]] = []
    specs: List[Dict[str, Any]] = []

    # --- AWG↔mm² table extraction ---
    # Pattern like: "AWG 24 = 0,205 mm²"
    for m in re.finditer(r"AWG\s*(\d{1,2})\s*=\s*([0-9]+[.,]?[0-9]*)\s*mm", text, flags=re.I):
        awg_num = int(m.group(1))
        mm2 = float(m.group(2).replace(",", "."))
        specs.append({
            "spec_key": f"awg_{awg_num}_mm2",
            "spec_value_num": mm2,
            "unit": "mm2",
            "raw": m.group(0)
        })

    # --- Material / Temperature info ---
    # Example: "PVC: -25 °C ... +70 °C"
    for m in re.finditer(r"(PVC|PUR|TPE)[^°\n]*?([-+]?\d{1,3}).*?([-+]?\d{1,3})\s*°C", text, flags=re.I):
        mat = m.group(1).upper()
        tmin, tmax = int(m.group(2)), int(m.group(3))
        specs.append({
            "spec_key": f"{mat.lower()}_temp_min_c",
            "spec_value_num": tmin,
            "unit": "°C",
            "raw": m.group(0)
        })
        specs.append({
            "spec_key": f"{mat.lower()}_temp_max_c",
            "spec_value_num": tmax,
            "unit": "°C",
            "raw": m.group(0)
        })

    # --- Voltage info (generic insulation ratings) ---
    # Example: "Spannung bis 250 V" or "up to 250 V"
    for m in re.finditer(r"(?:bis|up to)\s*([0-9]{2,4})\s*V", text, flags=re.I):
        specs.append({
            "spec_key": "reference_voltage_v",
            "spec_value_num": float(m.group(1)),
            "unit": "V",
            "raw": m.group(0)
        })

    # Only add one "Reference Data" product, bundling all specs
    if not specs:
        return [{
            "brand": None,
            "family": "Reference Data",
            "model_no": None,
            "article_number": None,
            "ordering_code": None,
            "product_name": "General Technical Information (empty)",
            "description": "No reference specs were parsed from this document",
            "interfaces": None,
            "source_pdf": os.path.basename(pdf_path),
            "pages_covered": [1, 2, 3, 4, 5, 6],
            "provenance": {"strategy": "technical_info_regex", "notes": ["no matches found"]},
            "specs": [],
        }]

    return [{
        "brand": None,
        "family": "Reference Data",
        "model_no": None,
        "article_number": None,
        "ordering_code": None,
        "product_name": "General Technical Information",
        "description": "Extracted normalization reference values",
        "interfaces": None,
        "source_pdf": os.path.basename(pdf_path),
        "pages_covered": [1, 2, 3, 4, 5, 6],
        "provenance": {"strategy": "technical_info_regex", "notes": ["reference lookup data"]},
        "specs": specs,
    }]

# =====================================================
#                LOOKS LIKE ALT
# =====================================================


def _keyword_score(text: str, positives: list[str], negatives: list[str] | None = None) -> int:
    """
    Returns a simple integer score: +1 per positive keyword present, -1 per negative.
    Case-insensitive, substring match.
    """
    t = text.lower()
    score = 0
    for k in positives:
        if k.lower() in t:
            score += 1
    if negatives:
        for k in negatives:
            if k.lower() in t:
                score -= 1
    return score


def _classify_pdf(text: str) -> str:
    """
    Decide between 'binder', 'm12', 'techinfo', or 'unknown' using keyword scores.
    Highest score wins; ties broken by a priority order (binder > m12 > techinfo).
    """
    # Binder cues
    s_binder = _keyword_score(
        text,
        positives=["binder", "cb-s", "co2", "co₂", "incubator", "model cb-s"],
        negatives=[]
    )

    # M12 catalog cues (German/English)
    s_m12 = _keyword_score(
        text,
        positives=[
            "m12", "sensorik", "aktorik",
            "serie 713", "serie 763",
            "bestell", "ordering code", "steckverbinder", "kabelstecker"
        ],
        negatives=["technische information", "technische informationen", "allgemeine hinweise"]
    )

    # Technical info cues (make this strict so it doesn’t swallow M12)
    s_ti = _keyword_score(
        text,
        positives=["technische information", "technische informationen", "allgemeine hinweise", "awg"],
        negatives=["serie 713", "serie 763", "m12", "ordering code", "bestell"]
    )

    scores = {
        "binder": s_binder,
        "m12": s_m12,
        "techinfo": s_ti,
        "unknown": 0
    }

    # pick highest; deterministic tie-break (binder > m12 > techinfo > unknown)
    ordered = sorted(scores.items(), key=lambda kv: (kv[1], kv[0] in ["binder", "m12", "techinfo"]), reverse=True)
    top, top_score = ordered[0]
    return top if top_score > 0 else "unknown"


# =====================================================
#                MAIN PUBLIC ENTRYPOINT
# =====================================================

def extract_products(pdf_dir: str) -> List[Dict[str, Any]]:
    """
    Walk a directory of PDFs and extract structured product data.
    - Detect Binder or M12 documents heuristically.
    - Unknown PDFs become generic placeholders.
    """
    products: List[Dict[str, Any]] = []
    if not os.path.isdir(pdf_dir):
        return products

    for name in sorted(os.listdir(pdf_dir)):
        if not name.lower().endswith(".pdf"):
            continue

        path = os.path.join(pdf_dir, name)
        # read more pages to improve classification on large catalogs
        text = _read_text_sample(path, pages=12)

        doc_type = _classify_pdf(text) if text else "unknown"

        if doc_type == "binder":
            products.extend(_parse_binder_cb_s_260(path, text))
        elif doc_type == "m12":
            products.extend(_parse_m12_catalog(path, text))
        elif doc_type == "techinfo":
            products.extend(_parse_technical_info_pdf(path, text))
        else:
            products.append({
                "brand": None,
                "family": None,
                "model_no": None,
                "article_number": None,
                "ordering_code": None,
                "product_name": os.path.splitext(name)[0],
                "description": None,
                "interfaces": None,
                "source_pdf": name,
                "pages_covered": [1],
                "provenance": {"strategy": "placeholder_per_pdf"},
                "specs": [],
            })


    return products

