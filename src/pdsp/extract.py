from __future__ import annotations
from typing import Dict, Any, List, Optional
import os
import re
import collections

try:
    import pdfplumber
except Exception:
    pdfplumber = None

try:
    import camelot
except Exception:
    camelot = None

DISABLE_CAMELOT = os.environ.get("PDSP_CAMELOT", "").lower() in {"0", "off", "false", "no"}
if DISABLE_CAMELOT:
    camelot = None

from pdsp.normalize import (
    parse_mm_range,        # NEW
    parse_ip_code,         # NEW
    parse_temp_block,      # NEW
    build_contact_value_map,
    parse_contact_header,
)

# ----------------------------------------------------
# Public API
# ----------------------------------------------------

def extract_products(pdf_dir: str) -> List[Dict[str, Any]]:
    pdfs = [
        os.path.join(pdf_dir, f)
        for f in os.listdir(pdf_dir)
        if f.lower().endswith(".pdf")
    ]
    products: List[Dict[str, Any]] = []
    for pdf_path in pdfs:
        text_all = _read_text_all(pdf_path)
        kind = _classify_pdf_by_text_and_name(text_all, os.path.basename(pdf_path))
        if kind == "binder":
            products.extend(_parse_binder_cb_s_260(pdf_path, text_all))
        elif kind == "techinfo":
            products.extend(_parse_technical_info_pdf(pdf_path, text_all))
        elif kind == "m12":
            products.extend(_parse_m12_binder_713_763(pdf_path))
        else:
            # unknown -> no-op
            pass
    return products

# ----------------------------------------------------
# Helpers: text
# ----------------------------------------------------

def _read_text_all(pdf_path: str) -> str:
    if pdfplumber is None:
        return ""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception:
        return ""

def _split_pages(pdf_path: str) -> List[str]:
    if pdfplumber is None:
        return []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            return [p.extract_text() or "" for p in pdf.pages]
    except Exception:
        return []

def _keyword_score(text: str, positives: List[str], negatives: Optional[List[str]] = None) -> int:
    t = text.lower()
    score = sum(1 for k in positives if k.lower() in t)
    if negatives:
        score -= sum(1 for k in negatives if k.lower() in t)
    return score

def _count_ordering_codes(text: str) -> int:
    # matches e.g. "99 0429 14 04" and variants with optional spaces
    return len(re.findall(r"\b(?:9\d)\s?(?:\d{3,4}\s?){2,3}\d{2}\b", text))

def _classify_pdf_by_text_and_name(text: str, filename: str) -> str:
    name = filename.lower()
    s_binder = _keyword_score(text, ["binder", "cb-s", "co2", "co₂", "incubator", "model cb-s"])
    s_m12 = _keyword_score(
        text,
        ["m12", "sensorik", "aktorik", "serie 713", "serie 763", "ordering-no", "ordering code", "bestell-nr."],
        negatives=["technische information", "technische informationen", "allgemeine hinweise"],
    )
    s_ti = _keyword_score(
        text,
        ["technische information", "technische informationen", "allgemeine hinweise", "awg"],
        negatives=["serie 713", "serie 763", "ordering-no", "ordering code", "bestell-nr.", "m12"],
    )
    oc = _count_ordering_codes(text)
    s_m12 += min(oc, 100)
    s_ti  -= min(oc, 100)

    if "serie_713_763" in name or "m12" in name:
        s_m12 += 5
    if "technische_infos" in name or "technische_info" in name:
        s_ti += 5

    scores = {"binder": s_binder, "m12": s_m12, "techinfo": s_ti, "unknown": 0}
    top, top_score = max(scores.items(), key=lambda kv: kv[1])
    return top if top_score > 0 else "unknown"

# ----------------------------------------------------
# Existing parsers (binder, techinfo)
# ----------------------------------------------------
def _parse_binder_cb_s_260(pdf_path: str, text: str) -> List[Dict[str, Any]]:
    """
    Parse technical information for BOTH models on the CB-S 260 data sheet:
    - CBS260-230V
    - CBS260UL-120V
    Returns two product dicts with per-model specs.
    """
    def norm(s: str) -> str:
        s = s.replace("\xa0", " ")
        s = re.sub(r"[–−—]", "-", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    t = norm(text)

    # Prepare two products
    products = {
        "CBS260-230V": {
            "brand": "BINDER",
            "family": "CB-S",
            "model_no": "CBS260-230V",
            "article_number": None,
            "ordering_code": None,
            "product_name": "Model CB-S 260 | CO₂ incubator",
            "description": None,
            "interfaces": None,
            "source_pdf": os.path.basename(pdf_path),
            "pages_covered": [],
            "provenance": {"strategy": "binder_cb_s_260_text"},
            "specs": [],
        },
        "CBS260UL-120V": {
            "brand": "BINDER",
            "family": "CB-S",
            "model_no": "CBS260UL-120V",
            "article_number": None,
            "ordering_code": None,
            "product_name": "Model CB-S 260 | CO₂ incubator",
            "description": None,
            "interfaces": None,
            "source_pdf": os.path.basename(pdf_path),
            "pages_covered": [],
            "provenance": {"strategy": "binder_cb_s_260_text"},
            "specs": [],
        },
    }

    def add(model: str, key: str, *, num: float | None = None, text: str | None = None, unit: str | None = None, raw: str | None = None):
        products[model]["specs"].append({
            "spec_key": key,
            "spec_value_num": float(num) if num is not None else None,
            "spec_value_text": text,
            "unit": unit,
            "raw": raw if raw is not None else (text if text is not None else (str(num) if num is not None else "")),
        })

    def fnum(s: str) -> float:
        s = s.replace(",", ".")
        s = re.sub(r"[^0-9+.\-eE]", "", s)
        return float(s)

    # ---------------------- Header mappings ----------------------
    # Article numbers
    m = re.search(r"Article\s*Number\s+(\d{4}-\d{4})\s+(\d{4}-\d{4})", t, re.I)
    if m:
        products["CBS260-230V"]["article_number"] = m.group(1)
        products["CBS260UL-120V"]["article_number"] = m.group(2)

    # ---------------------- Helper to capture pair values ----------------------
    def pair_numbers(label_regex: str, value_pat: str, flags=re.I | re.S) -> tuple[str, str] | None:
        """
        Find 'label ... v1  v2' where each value matches value_pat.
        Returns raw strings (v1, v2) or None.
        """
        pat = rf"{label_regex}\s+{value_pat}\s+{value_pat}"
        m = re.search(pat, t, flags)
        if not m:
            return None
        # two capturing groups expected
        return (m.group(1), m.group(2))

    # ---------- Temperature range (+6 °C above ambient temperature to 50 °C) ----------
    m = re.search(
        r"Temperature\s*range\s+"
        r"\+?\s*([0-9]+(?:[.,][0-9]+)?)\s*°C\s*above\s*ambient(?:\s*temperature)?"
        r"\s*(?:to|–|-|…)\s*([0-9]+(?:[.,][0-9]+)?)\s*°C",
        t,
        re.I,
    )
    if m:
        lo = fnum(m.group(1))
        hi = fnum(m.group(2))
        for model in products:
            add(model, "temp_above_ambient_c", num=lo, unit="°C", raw=m.group(0))
            add(model, "temp_max_c", num=hi, unit="°C", raw=m.group(0))


    # Uniformity / fluctuation @37°C (format like "0.4 ± K")
    m = pair_numbers(r"Temperature\s*uniformity\s*at\s*37\s*°C", r"([0-9]+(?:[.,][0-9]+)?)\s*±\s*K")
    if m:
        add("CBS260-230V", "temp_uniformity_c", num=fnum(m[0]), unit="°C", raw=m[0] + " ±K")
        add("CBS260UL-120V", "temp_uniformity_c", num=fnum(m[1]), unit="°C", raw=m[1] + " ±K")

    m = pair_numbers(r"Temperature\s*fluctuation\s*at\s*37\s*°C", r"([0-9]+(?:[.,][0-9]+)?)\s*±\s*K")
    if m:
        add("CBS260-230V", "temp_fluctuation_c", num=fnum(m[0]), unit="°C", raw=m[0] + " ±K")
        add("CBS260UL-120V", "temp_fluctuation_c", num=fnum(m[1]), unit="°C", raw=m[1] + " ±K")

    m = pair_numbers(r"Recovery\s*time\s*after\s*door\s*was\s*opened\s*for\s*30\s*s\s*at\s*37\s*°C", r"([0-9]+(?:[.,][0-9]+)?)\s*min")
    if m:
        add("CBS260-230V", "temp_recovery_min", num=fnum(m[0]), unit="min", raw=m[0] + " min")
        add("CBS260UL-120V", "temp_recovery_min", num=fnum(m[1]), unit="min", raw=m[1] + " min")

        # ---------- Climate ----------
    # Humidity range 90 ...95 % RH  90 ...95 % RH
    m = re.search(
        r"Humidity\s*range\s+"
        r"([0-9]+(?:[.,][0-9]+)?)\s*\.\.\.\s*([0-9]+(?:[.,][0-9]+)?)\s*%\s*RH\s+"
        r"([0-9]+(?:[.,][0-9]+)?)\s*\.\.\.\s*([0-9]+(?:[.,][0-9]+)?)\s*%\s*RH",
        t,
        re.I,
    )
    if m:
        lo1, hi1, lo2, hi2 = m.groups()
        raw_h = m.group(0)
        add("CBS260-230V", "humidity_min_pct_rh", num=fnum(lo1), unit="%RH", raw=raw_h)
        add("CBS260-230V", "humidity_max_pct_rh", num=fnum(hi1), unit="%RH", raw=raw_h)
        add("CBS260UL-120V", "humidity_min_pct_rh", num=fnum(lo2), unit="%RH", raw=raw_h)
        add("CBS260UL-120V", "humidity_max_pct_rh", num=fnum(hi2), unit="%RH", raw=raw_h)


    # ---------- CO₂ ----------
    # CO₂ range 0 ...20 Vol.-% CO2   0 ...20 Vol.-% CO2
    m = re.search(
        r"CO[₂2]\s*range\s+"
        r"([0-9]+(?:[.,][0-9]+)?)\s*\.\.\.\s*([0-9]+(?:[.,][0-9]+)?)\s*(?:Vol\.-?%|%)\s*CO2?\s+"
        r"([0-9]+(?:[.,][0-9]+)?)\s*\.\.\.\s*([0-9]+(?:[.,][0-9]+)?)\s*(?:Vol\.-?%|%)\s*CO2?",
        t,
        re.I,
    )
    if m:
        lo1, hi1, lo2, hi2 = m.groups()
        raw_co2 = m.group(0)
        add("CBS260-230V", "co2_min_pct", num=fnum(lo1), unit="%", raw=raw_co2)
        add("CBS260-230V", "co2_max_pct", num=fnum(hi1), unit="%", raw=raw_co2)
        add("CBS260UL-120V", "co2_min_pct", num=fnum(lo2), unit="%", raw=raw_co2)
        add("CBS260UL-120V", "co2_max_pct", num=fnum(hi2), unit="%", raw=raw_co2)


    m = pair_numbers(r"CO[₂2]\s*measuring\s*technology", r"(IR|NDIR)")
    if m:
        add("CBS260-230V", "co2_sensor", text=m[0], raw=m[0])
        add("CBS260UL-120V", "co2_sensor", text=m[1], raw=m[1])

    # CO₂ recovery time (label split over two lines in some PDFs)
    m = re.search(r"CO[₂2]\s*recovery\s*time.*?for\s*30\s*s.*?(?:\r?\n)?\s*([0-9]+(?:[.,][0-9]+)?)\s*min\s+([0-9]+(?:[.,][0-9]+)?)\s*min", t, re.I | re.S)
    if m:
        add("CBS260-230V", "co2_recovery_min", num=fnum(m.group(1)), unit="min", raw=m.group(0))
        add("CBS260UL-120V", "co2_recovery_min", num=fnum(m.group(2)), unit="min", raw=m.group(0))

    # ---------- Electrical ----------
    # Rated Voltage 200...230 V 100...120 V
    m = re.search(
        r"Rated\s*Voltage\s+"
        r"([0-9]{2,3})\s*\.\.\.\s*([0-9]{2,3})\s*V\s+"
        r"([0-9]{2,3})\s*\.\.\.\s*([0-9]{2,3})\s*V",
        t,
        re.I,
    )
    if m:
        lo1, hi1, lo2, hi2 = m.groups()
        raw_rv = m.group(0)
        add("CBS260-230V", "supply_voltage_min_v", num=fnum(lo1), unit="V", raw=raw_rv)
        add("CBS260-230V", "supply_voltage_max_v", num=fnum(hi1), unit="V", raw=raw_rv)
        add("CBS260UL-120V", "supply_voltage_min_v", num=fnum(lo2), unit="V", raw=raw_rv)
        add("CBS260UL-120V", "supply_voltage_max_v", num=fnum(hi2), unit="V", raw=raw_rv)

    m = pair_numbers(r"Power\s*frequency", r"(50/60|50|60)\s*Hz")
    if m:
        add("CBS260-230V", "supply_freq_hz", text=m[0].replace(" ", ""), raw=m[0])
        add("CBS260UL-120V", "supply_freq_hz", text=m[1].replace(" ", ""), raw=m[1])

    m = pair_numbers(r"Nominal\s*power", r"([0-9]+(?:[.,][0-9]+)?)\s*kW")
    if m:
        add("CBS260-230V", "nominal_power_w", num=fnum(m[0])*1000.0, unit="W", raw=m[0] + " kW")
        add("CBS260UL-120V", "nominal_power_w", num=fnum(m[1])*1000.0, unit="W", raw=m[1] + " kW")

    m = pair_numbers(r"Unit\s*fuse", r"([0-9]+(?:[.,][0-9]+)?)\s*A")
    if m:
        add("CBS260-230V", "unit_fuse_a", num=fnum(m[0]), unit="A", raw=m[0]+" A")
        add("CBS260UL-120V", "unit_fuse_a", num=fnum(m[1]), unit="A", raw=m[1]+" A")

    m = pair_numbers(r"Phase\s*\(Nominal\s*voltage\)", r"([0-9]~)")
    if m:
        add("CBS260-230V", "phase", text=m[0], raw=m[0])
        add("CBS260UL-120V", "phase", text=m[1], raw=m[1])

    # ---------- Measures / dimensions / weights ----------
    m = pair_numbers(r"Interior\s*volume", r"([0-9]+(?:[.,][0-9]+)?)\s*L")
    if m:
        add("CBS260-230V", "interior_volume_l", num=fnum(m[0]), unit="L", raw=m[0]+" L")
        add("CBS260UL-120V", "interior_volume_l", num=fnum(m[1]), unit="L", raw=m[1]+" L")

    m = pair_numbers(r"Net\s*weight.*?unit.*?\(empty\)", r"([0-9]+(?:[.,][0-9]+)?)\s*kg")
    if m:
        add("CBS260-230V", "weight_kg", num=fnum(m[0]), unit="kg", raw=m[0]+" kg")
        add("CBS260UL-120V", "weight_kg", num=fnum(m[1]), unit="kg", raw=m[1]+" kg")

    m = pair_numbers(r"Load\s*per\s*rack", r"([0-9]+(?:[.,][0-9]+)?)\s*kg")
    if m:
        add("CBS260-230V", "shelf_max_load_kg", num=fnum(m[0]), unit="kg", raw=m[0]+" kg")
        add("CBS260UL-120V", "shelf_max_load_kg", num=fnum(m[1]), unit="kg", raw=m[1]+" kg")

    m = pair_numbers(r"Permitted\s*load", r"([0-9]+(?:[.,][0-9]+)?)\s*kg")
    if m:
        add("CBS260-230V", "permitted_load_kg", num=fnum(m[0]), unit="kg", raw=m[0]+" kg")
        add("CBS260UL-120V", "permitted_load_kg", num=fnum(m[1]), unit="kg", raw=m[1]+" kg")

    m = pair_numbers(r"Wall\s*clearance\s*back", r"([0-9]+)\s*mm")
    if m:
        add("CBS260-230V", "clearance_back_mm", num=fnum(m[0]), unit="mm", raw=m[0]+" mm")
        add("CBS260UL-120V", "clearance_back_mm", num=fnum(m[1]), unit="mm", raw=m[1]+" mm")

    m = pair_numbers(r"Wall\s*clearance\s*sidewise", r"([0-9]+)\s*mm")
    if m:
        add("CBS260-230V", "clearance_side_mm", num=fnum(m[0]), unit="mm", raw=m[0]+" mm")
        add("CBS260UL-120V", "clearance_side_mm", num=fnum(m[1]), unit="mm", raw=m[1]+" mm")

    # ---------- External dimensions from Width/Height/Depth net ----------
    # Lines look like:
    # Width net 740 mm 740 mm
    # Height net 1,020 mm 1,020 mm
    # Depth net 785 mm 785 mm
    m = re.search(
        r"Width\s*net\s+([0-9]{2,4}[0-9,]*)\s*mm\s+([0-9]{2,4}[0-9,]*)\s*mm.*?"
        r"Height\s*net\s+([0-9]{2,4}[0-9,]*)\s*mm\s+([0-9]{2,4}[0-9,]*)\s*mm.*?"
        r"Depth\s*net\s+([0-9]{2,4}[0-9,]*)\s*mm\s+([0-9]{2,4}[0-9,]*)\s*mm",
        t,
        re.I | re.S,
    )
    if m:
        w1, w2, h1, h2, d1, d2 = m.groups()
        dims1 = f"{int(fnum(w1))}×{int(fnum(h1))}×{int(fnum(d1))} mm"
        dims2 = f"{int(fnum(w2))}×{int(fnum(h2))}×{int(fnum(d2))} mm"
        raw_dims = m.group(0)
        add("CBS260-230V", "external_dimensions_mm", text=dims1, raw=raw_dims)
        add("CBS260UL-120V", "external_dimensions_mm", text=dims2, raw=raw_dims)

    # Internal dimensions
    miW = pair_numbers(r"Interior\s*width", r"([0-9]{2,4})\s*mm")
    miH = pair_numbers(r"Interior\s*height", r"([0-9]{2,4})\s*mm")
    miD = pair_numbers(r"Interior\s*depth", r"([0-9]{2,4})\s*mm")
    if miW and miH and miD:
        for ix, model in enumerate(("CBS260-230V", "CBS260UL-120V")):
            dims = f"{norm(miW[ix])}×{norm(miH[ix])}×{norm(miD[ix])} mm"
            add(model, "interior_dimensions_mm", text=dims, raw=dims)

    # Doors / fixtures / shelves
    m = pair_numbers(r"Inner\s*doors", r"([0-9]+)")
    if m:
        add("CBS260-230V", "inner_doors", num=fnum(m[0]), raw=m[0])
        add("CBS260UL-120V", "inner_doors", num=fnum(m[1]), raw=m[1])

    m = pair_numbers(r"Unit\s*doors", r"([0-9]+)")
    if m:
        add("CBS260-230V", "unit_doors", num=fnum(m[0]), raw=m[0])
        add("CBS260UL-120V", "unit_doors", num=fnum(m[1]), raw=m[1])

    # Shelves std/max "2/8"
    m = pair_numbers(r"Number\s*of\s*shelves.*?\(std\.\s*/\s*max\.\)", r"([0-9]+/[0-9]+)")
    if m:
        for ix, model in enumerate(("CBS260-230V", "CBS260UL-120V")):
            std, mx = m[ix].split("/")
            add(model, "shelves_count", num=fnum(std), raw=m[ix])
            add(model, "shelves_max", num=fnum(mx), raw=m[ix])

    # Environment / energy / sound
    m = pair_numbers(r"Sound-pressure\s*level", r"([0-9]+(?:[.,][0-9]+)?)\s*dB\(A\)")
    if m:
        add("CBS260-230V", "noise_db_a", num=fnum(m[0]), unit="dB(A)", raw=m[0]+" dB(A)")
        add("CBS260UL-120V", "noise_db_a", num=fnum(m[1]), unit="dB(A)", raw=m[1]+" dB(A)")

    m = pair_numbers(r"Energy\s*consumption\s*at\s*37\s*°C", r"([0-9]+(?:[.,][0-9]+)?)\s*Wh/h")
    if m:
        add("CBS260-230V", "energy_consumption_wh_per_h", num=fnum(m[0]), unit="Wh/h", raw=m[0]+" Wh/h")
        add("CBS260UL-120V", "energy_consumption_wh_per_h", num=fnum(m[1]), unit="Wh/h", raw=m[1]+" Wh/h")

    # Return both products
    return [products["CBS260-230V"], products["CBS260UL-120V"]]

def _parse_technical_info_pdf(pdf_path: str, text: str) -> List[Dict[str, Any]]:
    """
    Parse Binder M12 technical information page:
    Creates one virtual product per coding:
      M12-A, M12-B, M12-D, M12-X, M12-S, M12-K, M12-T, M12-L, M12-US-C
    Only technical fields: current, voltage, IP, contacts, application.
    """
    def norm(s: str) -> str:
        s = s.replace("\xa0", " ")
        s = re.sub(r"[–−—]", "-", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def parse_coding_row(line: str) -> Dict[str, Any]:
        line = norm(line)
        out: Dict[str, Any] = {}

        # --- IP block ---
        ip_match = re.search(r"IP\d+[0-9K]?(?:\s*/\s*IP\d+[0-9K]?)*", line)
        if ip_match:
            ip_raw = norm(ip_match.group(0))
            out["ip_rating"] = ip_raw
            left = line[: ip_match.start()].strip()
            right = line[ip_match.end():].strip()
        else:
            left = line
            right = ""

        # --- current & voltage (left side) ---
        curr_match = re.match(r"([0-9.,\s/\-]+A(?:\s*/\s*[0-9.,\s]+A)*)\s*(.*)", left)
        volt_part = ""
        if curr_match:
            curr_part = curr_match.group(1).strip()
            volt_part = curr_match.group(2).strip()
            curr_nums = [float(n.replace(",", ".")) for n in re.findall(r"\d+(?:[.,]\d+)?", curr_part)]
            if curr_nums:
                out["current_min_a"] = min(curr_nums)
                out["current_max_a"] = max(curr_nums)
                out["current_raw"] = curr_part
        else:
            volt_part = left.strip()

        if volt_part:
            v_nums = [float(n.replace(",", ".")) for n in re.findall(r"\d+(?:[.,]\d+)?", volt_part)]
            if v_nums:
                out["voltage_min_v"] = min(v_nums)
                out["voltage_max_v"] = max(v_nums)
                out["voltage_raw"] = volt_part
            if "dc" in volt_part.lower():
                out["voltage_dc"] = True
            if "ac" in volt_part.lower():
                out["voltage_ac"] = True

        # --- contacts & application (right side) ---
        if right:
            m_ct = re.match(r"([0-9+\-PEF,\s]+)", right)
            if m_ct:
                contacts_txt = m_ct.group(1).strip()
                out["contacts_text"] = contacts_txt
                nums = [int(n) for n in re.findall(r"\d+", contacts_txt)]
                if nums:
                    out["contacts_min"] = min(nums)
                    out["contacts_max"] = max(nums)
                app = right[m_ct.end():].strip()
            else:
                app = right.strip()
            if app:
                out["application"] = app

        return out

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    results: List[Dict[str, Any]] = []

    i = 0
    while i < len(lines):
        m = re.match(r"M12\s+(.+?)-KODIERUNG", lines[i], flags=re.I)
        if not m:
            i += 1
            continue

        coding_label = m.group(1).strip()  # e.g. 'A', 'B', 'D', 'X', 'S', 'K', 'T', 'L', 'US-/C'
        # normalise to a stable model_no
        coding_norm = coding_label.upper().replace(" ", "")
        coding_norm = coding_norm.replace("US-/C", "US-C")
        model_no = f"M12-{coding_norm}"

        # seek the value line after the header ("Strom / Current ...")
        j = i + 1
        while j < len(lines) and "Strom / Current" not in lines[j]:
            j += 1
        if j + 1 >= len(lines):
            i = j
            continue

        value_line = lines[j + 1]
        parsed = parse_coding_row(value_line)
        if not parsed:
            i = j + 1
            continue

        specs: List[Dict[str, Any]] = []
        raw_all = value_line

        for key, val in parsed.items():
            if key.endswith("_raw"):
                continue

            # booleans: stores as yes/no text
            if isinstance(val, bool):
                specs.append({
                    "spec_key": key,
                    "spec_value_text": "yes" if val else "no",
                    "raw": raw_all,
                })
                continue

            # numeric values
            if isinstance(val, (int, float)):
                unit = None
                if key.endswith("_a"):
                    unit = "A"
                elif key.endswith("_v"):
                    unit = "V"
                specs.append({
                    "spec_key": key,
                    "spec_value_num": float(val),
                    "unit": unit,
                    "raw": raw_all,
                })
                continue

            # everything else as text
            specs.append({
                "spec_key": key,
                "spec_value_text": str(val),
                "raw": raw_all,
            })


        results.append({
            "brand": "Binder",
            "family": "M12 coding",
            "model_no": model_no,
            "article_number": None,
            "ordering_code": None,
            "product_name": f"M12 {coding_label}-coding",
            "description": None,
            "interfaces": None,
            "source_pdf": os.path.basename(pdf_path),
            "pages_covered": [1],
            "provenance": {"strategy": "m12_technical_info"},
            "specs": specs,
        })

        i = j + 2

    return results


# ----------------------------------------------------
# NEW: M12 713/763 parser
# ----------------------------------------------------

def _parse_m12_binder_713_763(pdf_path: str) -> List[Dict[str, Any]]:
    pages = _split_pages(pdf_path)
    out: List[Dict[str, Any]] = []
    for idx, page_text in enumerate(pages):
        if not _page_looks_like_m12(page_text):
            continue

        page_desc = _extract_variant_description(page_text)
        small_table_rows = _extract_small_tables(page_text, pdf_path, idx)
        
        if not small_table_rows:
            continue
        
        # collect unique contact counts present on this page
        page_contacts = sorted(
            {r.get("contacts") for r in small_table_rows if r.get("contacts") is not None}
        )

        # build spec map: {contact_count: {spec_key: english_value}}
        contact_spec_map = build_contact_value_map(page_text, page_contacts)

        for row in small_table_rows:
            contacts = row.get("contacts")
            cable_outlet = row.get("cable_outlet")
            ordering_code = row.get("ordering_code")

            specs = []
            # normalize cable outlet (e.g., "4–6 mm")
            mm_min, mm_max = parse_mm_range(cable_outlet)
            if mm_min is not None or mm_max is not None:
                if mm_min is not None:
                    specs.append({"spec_key": "cable_outlet_min_mm", "spec_value_num": mm_min, "unit": "mm", "raw": cable_outlet})
                if mm_max is not None:
                    specs.append({"spec_key": "cable_outlet_max_mm", "spec_value_num": mm_max, "unit": "mm", "raw": cable_outlet})
            else:
                specs.append({"spec_key": "cable_outlet_text", "spec_value_text": cable_outlet, "raw": cable_outlet})

            if contacts is not None:
                specs.append({"spec_key": "contacts", "spec_value_num": float(contacts), "raw": str(contacts)})

            # merge shared specs
            # page-level ip / temp
            ip = parse_ip_code(page_text)
            if ip:
                specs.append({"spec_key": "ip_rating", "spec_value_text": ip, "raw": ip})

            tmin, tmax = parse_temp_block(page_text)
            if tmax is not None:
                specs.append({"spec_key": "temp_max_c", "spec_value_num": tmax, "unit": "°C", "raw": str(tmax)})
            if tmin is not None:
                specs.append({"spec_key": "temp_min_c", "spec_value_num": tmin, "unit": "°C", "raw": str(tmin)})

            # contact-specific specs from big table
            if contact_spec_map:
                contact_specs = contact_spec_map.get(contacts, contact_spec_map.get(0, {}))
                for k, v in contact_specs.items():
                    if not v:
                        continue
                    specs.append({
                        "spec_key": k,
                        "spec_value_text": v,
                        "raw": v,
                    })

            # dedupe specs
            seen = set()
            unique_specs = []
            for s in specs:
                sig = (s["spec_key"], s.get("spec_value_text"), s.get("spec_value_num"))
                if sig in seen:
                    continue
                seen.add(sig)
                unique_specs.append(s)
            specs = unique_specs


            out.append({
                "brand": "Binder",
                "family": "M12 713 - 763",
                "model_no": None,
                "article_number": None,
                "ordering_code": ordering_code,
                "product_name": "M12 connector (variant)",
                "description": page_desc,
                "interfaces": None,
                "source_pdf": os.path.basename(pdf_path),
                "pages_covered": [idx + 1],
                "provenance": {
                    "strategy": "m12_page_regex" if camelot is None else "m12_camelot_or_regex",
                    "page": idx + 1
                },
                "specs": specs,
            })
    return out

# ----------------------------------------------------
# M12 Helpers
# ----------------------------------------------------

def _page_looks_like_m12(text: str) -> bool:
    t = (text or "").lower()
    has_table_hdr = (("polzahl" in t or "contacts" in t) and ("bestell" in t or "ordering-no" in t or "ordering no" in t))
    return "m12" in t and has_table_hdr


def _extract_variant_description(text: str) -> Optional[str]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in lines[:80]:
        low = ln.lower()
        # Prefer obvious English descriptors
        if any(w in low for w in ("male", "female", "connector", "angled")) and "," in ln:
            # NEW: keep only ASCII to avoid German leakage; collapse spaces
            en = re.sub(r"[^\x20-\x7E]+", " ", ln)
            en = re.sub(r"\s+", " ", en).strip()
            return en if en else ln
    return None


def _extract_small_tables(page_text: str, pdf_path: str, page_index: int) -> List[Dict[str, Any]]:
    """
    Prefer Camelot if available; otherwise use regex to pair rows like:
      Contacts: 4
      Cable outlet: 4–6 mm
      Ordering-No.: 99 0429 14 04
    """
    rows: List[Dict[str, Any]] = []

    if camelot is not None:
        try:
            tables = camelot.read_pdf(pdf_path, pages=str(page_index + 1), flavor="stream")
            for tbl in tables:
                df = tbl.df
                headers = " ".join(df.iloc[0].astype(str).tolist()).lower()
                if ("contacts" in headers and "cable" in headers and "ordering" in headers) or \
                   ("polzahl" in headers and "kabeldurchlass" in headers and "bestell" in headers):
                    # normalize rows
                    for r in range(1, len(df)):
                        cells = [c.strip() for c in df.iloc[r].astype(str).tolist()]
                        if len(cells) < 3:
                            continue
                        cts = _coerce_int(cells[0])
                        outlet = cells[1]
                        code = _extract_ordering_code(" ".join(cells[2:]))
                        if code:
                            rows.append({"contacts": cts, "cable_outlet": outlet, "ordering_code": code})
        except Exception:
            pass

    # Fallback (or supplement): regex extraction from text block
    # 1) chunk by occurrence of the headers
        # Fallback (or supplement): line-based parser for side-by-side small tables
        # Fallback (text-only): parse side-by-side small tables using local windows
        # Fallback: robust line-based parser for 1..N side-by-side ordering tables
        # Fallback: robust line-based parser for 1..N side-by-side ordering tables
    if not rows:
        lines = page_text.splitlines()

        # bound the block: after last small-table header, before spec header
        start = None
        end = None
        for i, ln in enumerate(lines):
            if "Contacts Cable outlet Ordering-No." in ln:
                start = i + 1
            if start is not None and "Polzahl Number of contacts" in ln:
                end = i
                break

        if start is None or end is None or end <= start:
            return rows

        mm_code_pattern = re.compile(
            r"([0-9,.\-–]+\s*mm)\s+((?:9\d)(?:\s?\d+){3,4})"
        )

        rows = []
        current_contacts: list[int] = []
        i = start
        while i < end:
            ln = lines[i].strip()
            if not ln:
                i += 1
                continue

            # pure contacts line: "3", "4 5 8 12", etc.
            if re.fullmatch(r"(?:\d+\s+)+\d+", ln):
                current_contacts = [int(x) for x in ln.split()]
                i += 1
                continue

            # find one or more "<mm> <order>" pairs on this line
            pairs = mm_code_pattern.findall(ln)
            if pairs:
                # inline contacts at start of line, e.g. "5 6–8 mm 99 0487 12 08"
                inline_nums: list[int] = []
                m_inline = re.match(r"((?:\d+\s+)+)([0-9,.\-–]+\s*mm)\s+((?:9\d)(?:\s?\d+){3,4})", ln)
                if m_inline:
                    inline_nums = [int(x) for x in m_inline.group(1).split()]

                lookahead_nums: list[int] = []
                # if no inline and no current, treat next pure-digits line as contacts for this line
                # if next line is pure digits, treat it as contacts for THIS line
                if i + 1 < end:
                    nxt = lines[i + 1].strip()
                    if re.fullmatch(r"(?:\d+\s+)+\d+", nxt):
                        lookahead_nums = [int(x) for x in nxt.split()]


                def pick_contacts() -> list[int]:
                    if inline_nums:
                        return inline_nums
                    if lookahead_nums:
                        return lookahead_nums
                    return current_contacts

                used = pick_contacts()

                def expand(used_contacts: list[int], n: int) -> list[int | None]:
                    if not used_contacts:
                        return [None] * n
                    if len(used_contacts) == n:
                        return used_contacts
                    if len(used_contacts) == 1:
                        return used_contacts * n
                    # ambiguous (more contacts than pairs): best-effort, repeat first
                    return [used_contacts[0]] * n

                contact_list = expand(used, len(pairs))

                for (mm, order), c in zip(pairs, contact_list):
                    rows.append({
                        "contacts": int(c) if c is not None else None,
                        "cable_outlet": mm.replace("–", "-").strip(),
                        "ordering_code": _extract_ordering_code(order),
                    })

                # if we consumed a lookahead contacts line, advance past it
                if lookahead_nums:
                    current_contacts = lookahead_nums
                    i += 2
                else:
                    i += 1
                continue

            i += 1
    
    # ---- post-process: fill missing contacts by nearest contact anchor ----
    if rows:
        # collect standalone contact anchor lines and their char positions
        contact_anchors = []
        for m in re.finditer(r'(?m)^\s*(\d{1,2}(?:\s+\d{1,2})*)\s*$', page_text):
            nums = [int(x) for x in re.findall(r'\d{1,2}', m.group(1))]
            contact_anchors.append((m.start(), nums))

        # assign missing contacts by finding the nearest anchor to the ordering code
        for row in rows:
            if row.get("contacts") is not None:
                continue

            ordering = row.get("ordering_code") or ""
            if not ordering:
                continue

            ordering_spaced = ordering
            ordering_compact = ordering.replace(" ", "")

            # try spaced form first (what exists in page_text), then compact
            pos = page_text.find(ordering_spaced)
            if pos == -1:
                pos = page_text.find(ordering_compact)

            if pos == -1:
                # still nothing; skip this row
                continue


            if pos != -1 and contact_anchors:
                nearest = min(contact_anchors, key=lambda a: abs(a[0] - pos))
                nums = nearest[1]
                if len(nums) == 1:
                    chosen = nums[0]
                else:
                    # If anchor has multiple numbers (e.g. "3 4 5 8 12"),
                    # try to be smarter: map by pair-index on same line if possible.
                    # Find all ordering codes on that anchor's surrounding area:
                    anchor_pos = nearest[0]
                    window = page_text[max(0, anchor_pos - 400): anchor_pos + 400]
                    # build list of ordering codes (compact) found in the window
                    found_orders = [o.replace(" ", "") for o in re.findall(r"(?:9\d)(?:\s?\d+){3,4}", window)]
                    if found_orders:
                        # try to find this row's ordering within the found orders to get an index
                        try:
                            idx = found_orders.index(ordering)
                            # clamp idx to nums length
                            chosen = nums[min(idx, len(nums) - 1)]
                        except ValueError:
                            chosen = nums[0]
                    else:
                        chosen = nums[0]
                row["contacts"] = int(chosen)

        # final fallback: if still missing any, iterate header contacts (parse_contact_header)
        if any(r.get("contacts") is None for r in rows):
            header_nums = parse_contact_header(page_text)
            if header_nums:
                it = iter(header_nums)
                for r in rows:
                    if r.get("contacts") is None:
                        try:
                            r["contacts"] = next(it)
                        except StopIteration:
                            it = iter(header_nums)
                            r["contacts"] = next(it)
    # ---- end post-process ----

    # --- second-pass: learn contact from inline-tagged rows and override ambiguous ones ---
    g2g3_to_contact: dict[tuple[str, str], int] = {}
    g2_counts: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)

    # learn from lines that explicitly begin with a contact number and contain an ordering code
    for line in page_text.splitlines():
        m = re.match(r"\s*(\d{1,2})\s+.*?(?:9\d)\s+(\d+)\s+(\d+)\s+\d{2}\b", line)
        if m:
            c = int(m.group(1))
            g2 = m.group(2)  # series block (e.g., 0429 / 0437 / 0487 / 0491)
            g3 = m.group(3)  # variant block (e.g., 07 / 14 / 314 / 12 ...)
            g2g3_to_contact[(g2, g3)] = c
            g2_counts[g2][c] += 1

    # override/complete per-row contacts using the learned map
    for r in rows:
        oc = r.get("ordering_code") or ""
        m = re.search(r"(?:9\d)\s+(\d+)\s+(\d+)\s+(\d{2})\b", oc)
        if not m:
            continue
        g2, g3 = m.group(1), m.group(2)
        key = (g2, g3)

        if key in g2g3_to_contact:
            r["contacts"] = g2g3_to_contact[key]
            continue

        # fallback: choose the most common contact seen for this series (g2)
        if g2 in g2_counts and g2_counts[g2]:
            common = g2_counts[g2].most_common()
            if len(common) > 1 and common[0][1] == common[1][1]:
                # tie-break preference for 4 if present (avoids mis-mapping 04 -> 3 on this layout)
                r["contacts"] = 4 if 4 in g2_counts[g2] else common[0][0]
            else:
                r["contacts"] = common[0][0]
    # --- end second-pass ---

    # de-dup
    seen = set()
    uniq: List[Dict[str, Any]] = []
    for r in rows:
        key = (r.get("contacts"), r.get("cable_outlet"), r.get("ordering_code"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    return uniq


def _extract_ordering_code(s: str) -> Optional[str]:
    m = re.search(r"\b((?:9\d)\s?(?:\d{2,4}\s?){3,4})\b", s)
    if not m:
        return None
    digits = re.sub(r"\s+", "", m.group(1))

    # NEW: handle the 11-digit Binder format: 2-4-3-2  (e.g., 99 1525 812 04)
    if len(digits) == 11:
        return f"{digits[0:2]} {digits[2:6]} {digits[6:9]} {digits[9:11]}"

    if len(digits) == 12:   # 2-4-4-2
        return f"{digits[0:2]} {digits[2:6]} {digits[6:10]} {digits[10:12]}"
    if len(digits) == 10:   # 2-4-2-2
        return f"{digits[0:2]} {digits[2:6]} {digits[6:8]} {digits[8:10]}"

    # fallback (unchanged)
    return " ".join(re.findall(r".{1,4}", digits))


def _coerce_int(s: str) -> Optional[int]:
    try:
        return int(re.findall(r"\d+", s)[0])
    except Exception:
        return None