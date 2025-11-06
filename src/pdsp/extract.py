# src/pdsp/extract.py
from __future__ import annotations
from typing import Dict, Any, List, Optional, Tuple
import os
import re

# third-party (graceful if missing)
try:
    import pdfplumber
except Exception:
    pdfplumber = None

# project
from pdsp.normalize import canonical_key, normalize_awg_or_mm2


# =====================================================
#                PDF TEXT EXTRACTION
# =====================================================

def _read_text_all(pdf_path: str) -> str:
    """
    Extract plaintext from ALL pages using pdfplumber.
    Returns "" on any failure or if pdfplumber is unavailable.
    """
    if pdfplumber is None:
        return ""
    try:
        parts: List[str] = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                parts.append(page.extract_text() or "")
        return "\n".join(parts)
    except Exception:
        return ""


# =====================================================
#                CLASSIFICATION HELPERS
# =====================================================

def _keyword_score(text: str, positives: List[str], negatives: Optional[List[str]] = None) -> int:
    """
    Simple keyword scoring: +1 for each positive present, -1 for each negative present.
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


def _count_ordering_codes(text: str) -> int:
    """
    Count strings that look like 'ordering codes'.
    Accepts optional spacing between blocks; matches formats like:
      '99 0429 43 04', '99 1491 812 12', or even '9904294304'.
    """
    pattern = r"\b(?:9\d)\s?(?:\d{3,4}\s?){2,3}\d{2}\b"
    return len(re.findall(pattern, text))


def _classify_pdf_by_text_and_name(text: str, filename: str) -> str:
    """
    Choose one of: 'binder' | 'm12' | 'techinfo' | 'unknown'
    Based on keyword scores, ordering-code density, and filename bias.
    Ties break as binder > m12 > techinfo > unknown.
    """
    name = filename.lower()

    s_binder = _keyword_score(
        text,
        positives=["binder", "cb-s", "co2", "co₂", "incubator", "model cb-s"],
        negatives=[],
    )

    s_m12 = _keyword_score(
        text,
        positives=[
            "m12", "sensorik", "aktorik",
            "serie 713", "serie 763",
            "ordering-no", "ordering code", "bestell-nr.", "steckverbinder", "kabelstecker"
        ],
        negatives=["technische information", "technische informationen", "allgemeine hinweise"],
    )

    s_ti = _keyword_score(
        text,
        positives=["technische information", "technische informationen", "allgemeine hinweise", "awg"],
        negatives=["serie 713", "serie 763", "ordering-no", "ordering code", "bestell-nr.", "m12"],
    )

    oc = _count_ordering_codes(text)
    s_m12 += min(oc, 100)   # lots of codes => strongly M12
    s_ti  -= min(oc, 100)   # TI should not accumulate many codes

    # filename bias
    if "serie_713_763" in name or "m12" in name:
        s_m12 += 5
    if "technische_infos" in name or "technische_info" in name:
        s_ti += 5

    scores = {"binder": s_binder, "m12": s_m12, "techinfo": s_ti, "unknown": 0}
    ordered = sorted(
        scores.items(),
        key=lambda kv: (kv[1], kv[0] in ["binder", "m12", "techinfo"]),
        reverse=True,
    )
    top, top_score = ordered[0]
    return top if top_score > 0 else "unknown"


# =====================================================
#                BINDER SHEET PARSING
# =====================================================

def _parse_temperature_range(text: str) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    """
    Extract temperature range 'X–Y' and numeric min/max °C.
    """
    m = re.search(r"Temperature\s*range\s*:\s*\+?(\d{1,3})\s*°C.*?\+?(\d{1,3})\s*°C", text, flags=re.I)
    if m:
        a, b = m.group(1), m.group(2)
        return f"{a}–{b}", float(a), float(b)
    m = re.search(r"from\s*\+?(\d{1,3})\s*°C\s*(?:to|–|-)\s*\+?(\d{1,3})\s*°C", text, flags=re.I)
    if m:
        a, b = m.group(1), m.group(2)
        return f"{a}–{b}", float(a), float(b)
    return None, None, None


def _parse_co2_range(text: str) -> Optional[str]:
    """
    Extract CO₂ range '0–N'.
    """
    m = re.search(r"CO\s*2[^%\n]*?(?:range|:)?\s*0\s*(?:to|–|-)\s*([0-9]{1,2})\s*(?:vol\.?%|%)", text, flags=re.I)
    if m:
        return f"0–{m.group(1)}"
    return None


def _parse_nominal_power_kw(text: str) -> Optional[float]:
    """
    Extract nominal power in kW.
    """
    m = re.search(r"(?:Nominal\s*power|Power)\s*:?\s*([0-9]+[.,]?[0-9]*)\s*kW", text, flags=re.I)
    if m:
        return float(m.group(1).replace(",", "."))
    return None


def _parse_dimensions_mm(text: str) -> List[Tuple[str, float]]:
    """
    Grab first 3 'NNN mm' as width/height/depth (heuristic).
    """
    nums = re.findall(r"(\d{2,4})\s*mm", text, flags=re.I)
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
    Parse Binder CB-S 260 datasheet into one product with several specs.
    """
    brand = "BINDER"
    family = "CB-S"
    model_no = "CBS260-230V"  # chosen variant string for demo queries

    specs: List[Dict[str, Any]] = []

    temp_pretty, tmin, tmax = _parse_temperature_range(text)
    if temp_pretty:
        specs.append({"spec_key": "temp_range_c", "spec_value_text": temp_pretty, "unit": "°C", "raw": temp_pretty})
    if tmin is not None:
        specs.append({"spec_key": "temp_min_c", "spec_value_num": tmin, "unit": "°C", "raw": f"{tmin} °C"})
    if tmax is not None:
        specs.append({"spec_key": "temp_max_c", "spec_value_num": tmax, "unit": "°C", "raw": f"{tmax} °C"})

    co2 = _parse_co2_range(text)
    if co2:
        specs.append({"spec_key": "co2_range_percent", "spec_value_text": co2, "unit": "%", "raw": co2.replace("–", " to ")})

    p_kw = _parse_nominal_power_kw(text)
    if p_kw is not None:
        specs.append({"spec_key": "nominal_power_kw", "spec_value_num": p_kw, "unit": "kW", "raw": f"{p_kw} kW"})

    for key, val in _parse_dimensions_mm(text):
        specs.append({"spec_key": key, "spec_value_num": val, "unit": "mm", "raw": f"{val} mm"})

    return [{
        "brand": brand,
        "family": family,
        "model_no": model_no,
        "article_number": None,
        "ordering_code": None,
        "product_name": "Model CB-S 260 | CO2 incubator",
        "description": None,
        "interfaces": None,
        "source_pdf": os.path.basename(pdf_path),
        "pages_covered": [],
        "provenance": {
            "strategy": "binder_cb_s_260_text",
            "notes": ["regex across full document; skip missing fields safely"],
        },
        "specs": specs,
    }]


# =====================================================
#                M12 CATALOG PARSING
# =====================================================

def _find_all_ordering_codes(text: str) -> List[re.Match]:
    """
    Return match objects for ordering codes across the whole doc.
    Accepts optional spaces and 3/4-digit middle blocks.
    """
    pattern = r"\b(?:9\d)\s?(?:\d{3,4}\s?){2,3}\d{2}\b"
    return list(re.finditer(pattern, text))


def _nearest_series(text: str, idx: int, window: int = 400) -> Optional[str]:
    """
    Return the series label nearest to position `idx` as one of:
      "713", "763", or "713 · 763".
    Handles variants: "713 · 763", "713·763", "713-763", "713/763" (with/without spaces).
    If both 713 and 763 appear but not as a combined token, pick whichever is closest.
    """
    start = max(0, idx - window)
    end = min(len(text), idx + window)
    seg = text[start:end]

    # 1) exact combined series patterns (prefer these if found)
    combined_pat = r"\b713\s*[·\u00B7\-/]\s*763\b"
    if re.search(combined_pat, seg):
        return "713 · 763"

    # 2) individual series occurrences with distance scoring
    choices: list[tuple[str, int]] = []

    for m in re.finditer(r"\b713\b", seg):
        # absolute distance of the token's center to idx
        tok_pos = start + m.start() + (m.end() - m.start()) // 2
        choices.append(("713", abs(tok_pos - idx)))

    for m in re.finditer(r"\b763\b", seg):
        tok_pos = start + m.start() + (m.end() - m.start()) // 2
        choices.append(("763", abs(tok_pos - idx)))

    if not choices:
        return None

    # pick the closest token
    choices.sort(key=lambda x: x[1])
    return choices[0][0]


def _extract_nearby_kv(text: str, idx: int, window: int = 300) -> Dict[str, Any]:
    """
    Scrape a neighborhood around the match (±window) for:
    contacts (Polzahl), IP rating, temps, rated voltage, wire gauge.
    """
    start = max(0, idx - window)
    end = min(len(text), idx + window)
    seg = text[start:end]

    out: Dict[str, Any] = {}

    # contacts / Polzahl
    m_contacts = re.search(r"\b(?:Polzahl|Contacts)\b[^\n]*?(\d{1,2})", seg, flags=re.I)
    if m_contacts:
        out["contacts"] = int(m_contacts.group(1))

    # ip rating (collect distinct)
    ips = re.findall(r"\bIP6[7-9]K?\b", seg)
    if ips:
        out["ip_rating"] = ",".join(sorted(set(ips)))

    # temperatures (upper/lower limit)
    up = re.search(r"(?:Obere\s+Grenztemperatur|Upper\s+temperature)[^\n]*?([-+]?\d{1,3})\s*°C", seg, flags=re.I)
    lo = re.search(r"(?:Untere\s+Grenztemperatur|Lower\s+temperature)[^\n]*?([-+]?\d{1,3})\s*°C", seg, flags=re.I)
    if up:
        out["temp_max_c"] = float(up.group(1))
    if lo:
        out["temp_min_c"] = float(lo.group(1))

    # rated voltage (keep max as conservative headline)
    volts = re.findall(r"(\d{2,4})\s*V\b", seg)
    if volts:
        out["rated_voltage_v"] = float(max(int(v) for v in volts))

    # wire gauge mm² (normalized helper)
    wg = re.search(r"(AWG\s*\d{1,2}|\d+(?:[.,]\d+)?\s*mm(?:2|²))", seg, flags=re.I)
    if wg:
        val, unit, raw = normalize_awg_or_mm2(wg.group(1))
        if val is not None:
            out["wire_gauge_mm2"] = val
        else:
            out["wire_gauge_text"] = raw

    return out


def _parse_m12_catalog(pdf_path: str, text: str) -> List[Dict[str, Any]]:
    """
    Parse the entire M12 catalog:
    - One product per ordering code
    - Attach local context (series 713/763, contacts, IP, temps, wire gauge)
    """
    products: List[Dict[str, Any]] = []
    matches = _find_all_ordering_codes(text)

    if not matches:
        return [{
            "brand": None,
            "family": "M12 Series",
            "model_no": None,
            "article_number": None,
            "ordering_code": None,
            "product_name": "M12 connector (catalog fallback)",
            "description": "No ordering codes found by regex",
            "interfaces": None,
            "source_pdf": os.path.basename(pdf_path),
            "pages_covered": [],
            "provenance": {"strategy": "m12_catalog_regex_allpages", "notes": ["no codes found"]},
            "specs": [],
        }]

    for m in matches:
        code_raw = m.group(0)
        # normalize block spacing to a canonical display form
        normalized = " ".join(re.findall(r"\d{2,4}", code_raw))
        series = _nearest_series(text, m.start())
        family = series if series else "713 · 763"  # default combined if unknown

        nearby = _extract_nearby_kv(text, m.start())
        specs: List[Dict[str, Any]] = []

        # fold nearby fields into specs
        for k in ["contacts", "rated_voltage_v", "temp_min_c", "temp_max_c", "wire_gauge_mm2", "wire_gauge_text", "ip_rating"]:
            if k in nearby:
                v = nearby[k]
                if isinstance(v, (int, float)):
                    specs.append({"spec_key": k, "spec_value_num": float(v), "raw": str(v)})
                else:
                    specs.append({"spec_key": k, "spec_value_text": str(v), "raw": str(v)})
                    
        # add coding info as a spec (so 'family' stays clean)
        specs.append({"spec_key": "coding", "spec_value_text": "M12 A", "raw": "M12 A"})

        products.append({
            "brand": None,
            "family": family,
            "model_no": None,
            "article_number": None,
            "ordering_code": normalized,
            "product_name": "M12 cable connector (variant)",
            "description": None,
            "interfaces": None,
            "source_pdf": os.path.basename(pdf_path),
            "pages_covered": [],
            "provenance": {"strategy": "m12_catalog_regex_allpages", "notes": ["per-code neighborhood features"]},
            "specs": specs,
        })

    return products


# =====================================================
#                TECHNICAL INFO PARSING
# =====================================================

def _parse_technical_info_pdf(pdf_path: str, text: str) -> List[Dict[str, Any]]:
    """
    Parse the 'Technische Informationen' PDF to extract generic reference data.
    Always emits at least one 'Reference Data' product (even if empty).
    """
    specs: List[Dict[str, Any]] = []

    # AWG ↔ mm² pairs like "AWG 24 = 0,205 mm²"
    for m in re.finditer(r"AWG\s*(\d{1,2})\s*=\s*([0-9]+[.,]?[0-9]*)\s*mm", text, flags=re.I):
        awg_num = int(m.group(1))
        mm2 = float(m.group(2).replace(",", "."))
        specs.append({
            "spec_key": f"awg_{awg_num}_mm2",
            "spec_value_num": mm2,
            "unit": "mm2",
            "raw": m.group(0),
        })

    # Material temp ranges: "PVC: -25 °C ... +70 °C" etc.
    for m in re.finditer(r"(PVC|PUR|TPE)[^°\n]*?([-+]?\d{1,3}).*?([-+]?\d{1,3})\s*°C", text, flags=re.I):
        mat = m.group(1).upper()
        tmin, tmax = int(m.group(2)), int(m.group(3))
        specs.append({"spec_key": f"{mat.lower()}_temp_min_c", "spec_value_num": float(tmin), "unit": "°C", "raw": m.group(0)})
        specs.append({"spec_key": f"{mat.lower()}_temp_max_c", "spec_value_num": float(tmax), "unit": "°C", "raw": m.group(0)})

    # Generic voltage notes: "bis 250 V" / "up to 250 V"
    for m in re.finditer(r"(?:bis|up to)\s*([0-9]{2,4})\s*V", text, flags=re.I):
        specs.append({"spec_key": "reference_voltage_v", "spec_value_num": float(m.group(1)), "unit": "V", "raw": m.group(0)})

    product = {
        "brand": None,
        "family": "Reference Data",
        "model_no": None,
        "article_number": None,
        "ordering_code": None,
        "product_name": "General Technical Information" if specs else "General Technical Information (empty)",
        "description": "Extracted normalization reference values" if specs else "No reference specs were parsed from this document",
        "interfaces": None,
        "source_pdf": os.path.basename(pdf_path),
        "pages_covered": [],
        "provenance": {"strategy": "technical_info_regex", "notes": ["reference lookup data"] if specs else ["no matches found"]},
        "specs": specs,
    }
    return [product]


# =====================================================
#                PUBLIC ENTRYPOINT
# =====================================================

def extract_products(pdf_dir: str) -> List[Dict[str, Any]]:
    """
    Walk a directory of PDFs and extract structured product data.
    - Full-document classification (binder/m12/techinfo/unknown)
    - Full-document parsing for M12 (per ordering code)
    - Safe fallbacks (emit placeholder or empty reference row)
    """
    products: List[Dict[str, Any]] = []
    if not os.path.isdir(pdf_dir):
        return products

    debug_mode = os.environ.get("PDSP_DEBUG") == "1"

    for name in sorted(os.listdir(pdf_dir)):
        if not name.lower().endswith(".pdf"):
            continue

        path = os.path.join(pdf_dir, name)
        text = _read_text_all(path)  # read ALL pages for robust decisions
        doc_type = _classify_pdf_by_text_and_name(text, name) if text else "unknown"

        if debug_mode:
            oc = _count_ordering_codes(text) if text else 0
            print(f"[pdsp] {doc_type.upper():8s} -> {name} (codes={oc})")

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
