from __future__ import annotations
from typing import Dict, Any, List, Optional, Tuple
import os
import re

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
    parse_mating_cycles,
    english_tail,
    extract_row_by_english_label,
    parse_rated_voltage_pair,
    parse_rated_impulse_voltage_pair,
    parse_rated_current_pair,
    normalize_awg_or_mm2,
    parse_mm_range,        # NEW
    parse_ip_code,         # NEW
    parse_temp_block,      # NEW
    parse_voltage_block,   # NEW
    parse_current_block,   # NEW
    build_contact_value_map,
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
            # unknown -> no-op (or minimal stub if you want)
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
# Existing parsers (binder, techinfo) – unchanged stubs
# ----------------------------------------------------

def _parse_binder_cb_s_260(pdf_path: str, text: str) -> List[Dict[str, Any]]:
    specs: List[Dict[str, Any]] = []
    m_temp = re.search(r"([\-+]?\d{1,3}).*?([\-+]?\d{1,3})\s*°C", text, flags=re.S | re.I)
    if m_temp:
        lo, hi = sorted(map(int, m_temp.groups()))
        specs.append({"spec_key": "temp_min_c", "spec_value_num": float(lo), "raw": str(lo)})
        specs.append({"spec_key": "temp_max_c", "spec_value_num": float(hi), "raw": str(hi)})

    return [{
        "brand": "BINDER",
        "family": "CB-S",
        "model_no": "CBS260-230V",
        "article_number": None,
        "ordering_code": None,
        "product_name": "Model CB-S 260 | CO2 incubator",
        "description": None,
        "interfaces": None,
        "source_pdf": os.path.basename(pdf_path),
        "pages_covered": [],
        "provenance": {"strategy": "binder_cb_s_260_text"},
        "specs": specs,
    }]

def _parse_technical_info_pdf(pdf_path: str, text: str) -> List[Dict[str, Any]]:
    specs: List[Dict[str, Any]] = []
    for m in re.finditer(r"(?:bis|up to)\s*([0-9]{2,4})\s*V", text, flags=re.I):
        specs.append({"spec_key": "reference_voltage_v", "spec_value_num": float(m.group(1)), "unit": "V", "raw": m.group(0)})
    return [{
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
        "provenance": {"strategy": "technical_info_regex"},
        "specs": specs,
    }]

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
                        # page-level ip / temp (safe to add per row)
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
                "family": "713 - 763",
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
    if not rows:
        lines = page_text.splitlines()

        # find bounds: after header, before spec block
        start = None
        end = None
        for i, ln in enumerate(lines):
            if "Contacts Cable outlet Ordering-No." in ln:
                start = i + 1
            if start is not None and (
                "schrauben/screw" in ln
                or "Connector locking system" in ln
            ):
                end = i
                break

        if start is not None and end is not None and end > start:
            last_nums: list[int] | None = None
            i = start

            while i < end:
                ln = lines[i].strip()
                if not ln:
                    i += 1
                    continue

                # mm + ordering-code pairs on this line (left & right table)
                pairs = re.findall(
                    r"([0-9,.\-–]+ ?mm)\s+((?:9\d)(?:\s?\d{2,4}){3,4})",
                    ln,
                )

                # digits-only line like "4 4" or "5 5" or "4 5"
                if not pairs and re.fullmatch(r"(?:\d+\s+)+\d+", ln):
                    nums = [int(x) for x in ln.split()]
                    last_nums = nums  # used for following mm-lines
                    i += 1
                    continue

                if pairs:
                    # look ahead: if next line is digits, use that for THIS line
                    nums: list[int] | None = None
                    if i + 1 < end:
                        nxt = lines[i + 1].strip()
                        if re.fullmatch(r"(?:\d+\s+)+\d+", nxt):
                            nums = [int(x) for x in nxt.split()]

                    # helper: add one row
                    def add_row(mm: str, code: str, contact: int | None):
                        rows.append(
                            {
                                "contacts": contact,
                                "cable_outlet": mm.replace("–", "-").strip(),
                                "ordering_code": _extract_ordering_code(code),
                            }
                        )

                    k = len(pairs)

                    if nums:
                        # exact match: one contact per pair
                        if len(nums) == k:
                            for (mm, code), c in zip(pairs, nums):
                                add_row(mm, code, c)
                        # single contact -> all pairs
                        elif len(nums) == 1:
                            c = nums[0]
                            for mm, code in pairs:
                                add_row(mm, code, c)
                        else:
                            # fallback: assign first number to all pairs
                            c = nums[0]
                            for mm, code in pairs:
                                add_row(mm, code, c)
                        last_nums = nums
                        i += 2  # consumed next line as digits
                        continue

                    # no inline digits: use last_nums if sensible
                    if last_nums:
                        if len(last_nums) == k:
                            for (mm, code), c in zip(pairs, last_nums):
                                add_row(mm, code, c)
                        elif len(last_nums) == 1:
                            c = last_nums[0]
                            for mm, code in pairs:
                                add_row(mm, code, c)
                        else:
                            for mm, code in pairs:
                                add_row(mm, code, None)
                    else:
                        for mm, code in pairs:
                            add_row(mm, code, None)

                    i += 1
                    continue

                i += 1



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