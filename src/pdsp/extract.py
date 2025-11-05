from __future__ import annotations
from typing import Dict, Any, List, Optional, Tuple
import os
import re

try:
    import pdfplumber
except Exception as e:  # pragma: no cover
    pdfplumber = None


# ----------------------------
# Helpers (internal use only)
# ----------------------------

def _read_text_sample(pdf_path: str, pages: int = 6) -> str:
    """
    Extract plain text from the first `pages` pages using pdfplumber.
    Returns empty string if pdfplumber is unavailable or file can't be read.
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


def _looks_like_binder_sheet(text: str) -> bool:
    """
    Cheap heuristic to recognize the BINDER CB-S sheet.
    """
    t = text.lower()
    return ("binder" in t) and ("cb-s" in t or "cb-s" in t or "cb s" in t) and ("co2" in t or "co₂" in t)


def _rx_first(pattern: str, text: str, flags: int = re.I) -> Optional[re.Match]:
    m = re.search(pattern, text, flags)
    return m


def _try_parse_float(s: str) -> Optional[float]:
    try:
        return float(s.replace(",", "."))
    except Exception:
        return None


def _parse_temperature_range(text: str) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    """
    Tries multiple patterns to extract a temperature range.
    Returns (pretty_text, min_c, max_c)
    """
    # Pattern like: "Temperature range: +6 °C to +50 °C"
    m = _rx_first(r"Temperature\s*range\s*:\s*\+?(\d{1,3})\s*°C.*?\+?(\d{1,3})\s*°C", text)
    if m:
        a, b = m.group(1), m.group(2)
        pretty = f"{a}–{b}"
        return pretty, _try_parse_float(a), _try_parse_float(b)

    # Generic pattern "from +X °C to +Y °C"
    m = _rx_first(r"from\s*\+?(\d{1,3})\s*°C\s*(?:to|–|-)\s*\+?(\d{1,3})\s*°C", text)
    if m:
        a, b = m.group(1), m.group(2)
        pretty = f"{a}–{b}"
        return pretty, _try_parse_float(a), _try_parse_float(b)

    return None, None, None


def _parse_co2_range(text: str) -> Optional[str]:
    # Examples: "CO2 ... 0 to 20 vol.%", "CO₂ range 0–20 %"
    m = _rx_first(r"CO\s*2[^%\n]*?(?:range|:)?\s*0\s*(?:to|–|-)\s*([0-9]{1,2})\s*(?:vol\.?%|%)", text)
    if m:
        return f"0–{m.group(1)}"
    return None


def _parse_nominal_power_kw(text: str) -> Optional[float]:
    # Example: "Nominal power: 0.9 kW", "Power 1,2 kW"
    m = _rx_first(r"(?:Nominal\s*power|Power)\s*:?\s*([0-9]+[.,]?[0-9]*)\s*kW", text)
    if m:
        return _try_parse_float(m.group(1))
    return None


def _parse_dimensions_mm(text: str) -> List[Tuple[str, float]]:
    """
    Very naive: grab first three mm values and map as width/height/depth if feasible.
    """
    nums = re.findall(r"(\d{2,4})\s*mm", text)
    out: List[Tuple[str, float]] = []
    if len(nums) >= 3:
        try:
            w = float(nums[0]); h = float(nums[1]); d = float(nums[2])
            out.append(("width_mm", w))
            out.append(("height_mm", h))
            out.append(("depth_mm", d))
        except Exception:
            pass
    return out


# ----------------------------
# Public API used by CLI
# ----------------------------

def extract_products(pdf_dir: str) -> List[Dict[str, Any]]:
    """
    Directory walker. For each PDF:
      - If it looks like a BINDER CB-S 260 sheet, parse it via text heuristics.
      - Otherwise, create a placeholder product row (to keep pipeline robust).
    """
    products: List[Dict[str, Any]] = []
    if not os.path.isdir(pdf_dir):
        return products

    for name in sorted(os.listdir(pdf_dir)):
        if not name.lower().endswith(".pdf"):
            continue

        path = os.path.join(pdf_dir, name)
        text = _read_text_sample(path, pages=6)

        if text and _looks_like_binder_sheet(text):
            products.extend(_parse_binder_cb_s_260(path, text))
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


def _parse_binder_cb_s_260(pdf_path: str, text: str) -> List[Dict[str, Any]]:
    """
    Heuristic text parsing for the BINDER Model CB-S 260 sheet.
    Safe-to-fail: missing matches simply won't create those specs.
    """
    brand = "BINDER"
    family = "CB-S"
    # We'll use this coherent model string for searching; later we could split voltage variants if needed.
    model_no = "CBS260-230V"

    specs: List[Dict[str, Any]] = []

    # Temperature range
    temp_pretty, tmin, tmax = _parse_temperature_range(text)
    if temp_pretty:
        specs.append({
            "spec_key": "temp_range_c",
            "spec_value_text": temp_pretty,
            "unit": "°C",
            "raw": temp_pretty
        })
    if tmin is not None:
        specs.append({
            "spec_key": "temp_min_c",
            "spec_value_num": tmin,
            "unit": "°C",
            "raw": f"{tmin} °C"
        })
    if tmax is not None:
        specs.append({
            "spec_key": "temp_max_c",
            "spec_value_num": tmax,
            "unit": "°C",
            "raw": f"{tmax} °C"
        })

    # CO2 range
    co2 = _parse_co2_range(text)
    if co2:
        specs.append({
            "spec_key": "co2_range_percent",
            "spec_value_text": co2,
            "unit": "%",
            "raw": co2.replace("–", " to ")
        })

    # Nominal power
    p_kw = _parse_nominal_power_kw(text)
    if p_kw is not None:
        specs.append({
            "spec_key": "nominal_power_kw",
            "spec_value_num": p_kw,
            "unit": "kW",
            "raw": f"{p_kw} kW"
        })

    # Dimensions (W/H/D) if we can spot three mm values
    for key, val in _parse_dimensions_mm(text):
        specs.append({
            "spec_key": key,
            "spec_value_num": val,
            "unit": "mm",
            "raw": f"{val} mm"
        })

    product: Dict[str, Any] = {
        "brand": brand,
        "family": family,
        "model_no": model_no,
        "article_number": None,
        "ordering_code": None,
        "product_name": "Model CB-S 260 | CO2 incubator",
        "description": None,
        "interfaces": None,  # add later if we confidently parse interface names
        "source_pdf": os.path.basename(pdf_path),
        "pages_covered": [1, 2, 3, 4, 5, 6],
        "provenance": {
            "strategy": "binder_cb_s_260_text",
            "notes": [
                "regex over first 6 pages via pdfplumber",
                "safe fail — missing fields skipped",
            ],
        },
        "specs": specs,
    }
    return [product]
