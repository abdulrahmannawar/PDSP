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

try:
    import camelot  # table extraction for M12 pages
except Exception:
    camelot = None
    
DISABLE_CAMELOT = os.environ.get("PDSP_CAMELOT", "").lower() in {"0","off","false","no"}
if DISABLE_CAMELOT:
    camelot = None


# project-local utils
from pdsp.normalize import to_snake_case, canonical_key, normalize_awg_or_mm2


# =====================================================
#                    PDF HELPERS
# =====================================================

def _read_text_all(pdf_path: str) -> str:
    """Extract plaintext from ALL pages via pdfplumber; empty string on failure."""
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

def _split_pages(pdf_path: str) -> List[str]:
    """Return text per page (index aligned to 0)."""
    if pdfplumber is None:
        return []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            return [p.extract_text() or "" for p in pdf.pages]
    except Exception:
        return []


# =====================================================
#                CLASSIFICATION (per file)
# =====================================================

def _keyword_score(text: str, positives: List[str], negatives: Optional[List[str]] = None) -> int:
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
    pattern = r"\b(?:9\d)\s?(?:\d{3,4}\s?){2,3}\d{2}\b"
    return len(re.findall(pattern, text))

def _classify_pdf_by_text_and_name(text: str, filename: str) -> str:
    """Return 'binder' | 'm12' | 'techinfo' | 'unknown'."""
    name = filename.lower()

    s_binder = _keyword_score(text, positives=["binder", "cb-s", "co2", "co₂", "incubator", "model cb-s"])
    s_m12 = _keyword_score(
        text,
        positives=["m12", "sensorik", "aktorik", "serie 713", "serie 763", "ordering-no", "ordering code", "bestell-nr."],
        negatives=["technische information", "technische informationen", "allgemeine hinweise"],
    )
    s_ti = _keyword_score(
        text,
        positives=["technische information", "technische informationen", "allgemeine hinweise", "awg"],
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
    ordered = sorted(scores.items(), key=lambda kv: (kv[1], kv[0] in ["binder", "m12", "techinfo"]), reverse=True)
    top, top_score = ordered[0]
    return top if top_score > 0 else "unknown"


# =====================================================
#                   BINDER (brief)
# =====================================================

def _parse_binder_cb_s_260(pdf_path: str, text: str) -> List[Dict[str, Any]]:
    """Simple extraction to keep binder covered; unchanged from earlier passes."""
    specs: List[Dict[str, Any]] = []
    m_temp = re.search(r"([\-+]?\d{1,3}).*?([\-+]?\d{1,3})\s*°C", text, flags=re.S|re.I)
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


# =====================================================
#                 TECHNICAL INFO (brief)
# =====================================================

def _parse_technical_info_pdf(pdf_path: str, text: str) -> List[Dict[str, Any]]:
    """Always emit one Reference Data product with whatever we can parse."""
    specs: List[Dict[str, Any]] = []
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
        "provenance": {"strategy": "technical_info_regex"},
        "specs": specs,
    }
    return [product]


# =====================================================
#              M12 (Camelot, page-aware join)
# =====================================================

HEADER_ALIASES = {
    "polzahl": "contacts",
    "contacts": "contacts",
    "kabeldurchlass": "cable_outlet",
    "cable_outlet": "cable_outlet",
    "kabellänge": "cable_length",
    "length_of_cable": "cable_length",
    "befestigungsgewinde": "fixing_thread",
    "fixing_thread": "fixing_thread",
    "bestell-nr": "ordering_no",
    "ordering_no": "ordering_no",
    "ordering-no": "ordering_no",
    "bestell_nr": "ordering_no",
    "bestellnr": "ordering_no",
    # matrix (left labels) -> canonical keys (kept flexible)
    "termination": "termination",
    "connector_locking_system": "connector_locking_system",
    "wire_gauge": "wire_gauge_max",
    "anschlussquerschnitt": "wire_gauge_max",
    "degree_of_protection": "ip_rating",
    "mechanical_operation": "mating_cycles",
    "upper_temperature": "temp_max_c",
    "lower_temperature": "temp_min_c",
    "rated_voltage": "rated_voltage_v",
    "rated_impulse_voltage": "rated_impulse_voltage_v",
    "pollution_degree": "pollution_degree",
    "overvoltage_categorie": "overvoltage_category",
    "material_group": "material_group",
    "rated_current_40_°c": "rated_current_40c_a",
    "rated_current_40_c": "rated_current_40c_a",
    "material_of_contact": "material_contact",
    "contact_plating": "contact_plating",
    "material_of_contact_body": "material_contact_body",
    "material_of_housing": "material_housing",
    "material_of_locking": "material_locking",
}

def _canon_header(s: str) -> str:
    s = to_snake_case(s)
    return HEADER_ALIASES.get(s, s)

def _is_contacts_number(token: str) -> bool:
    token = token.strip()
    return bool(re.fullmatch(r"\d{1,2}", token))

def _detect_series_and_coding(page_text: str) -> Tuple[Optional[str], str]:
    """Return (series_label, coding_text) where series ∈ {713, 763, 713 · 763}."""
    t = page_text.replace("\u00B7", "·")
    if re.search(r"\b713\s*[·/\-]\s*763\b", t):
        series = "713 · 763"
    elif re.search(r"\b713\b", t) and not re.search(r"\b763\b", t):
        series = "713"
    elif re.search(r"\b763\b", t) and not re.search(r"\b713\b", t):
        series = "763"
    else:
        m = re.search(r"\b(713|763)\b", t)
        series = m.group(1) if m else None
        if series == "713" and re.search(r"\b763\b", t):
            series = "713 · 763"
        elif series == "763" and re.search(r"\b713\b", t):
            series = "713 · 763"
    coding = "M12 A" if ("M12-A" in t or "M12 A" in t) else "M12"
    return series, coding

def _ffill_row(values: List[str]) -> List[str]:
    out, last = [], ""
    for v in values:
        v = (v or "").strip()
        if v == "":
            out.append(last)
        else:
            out.append(v)
            last = v
    return out

def _guess_block_descriptions(page_text: str) -> List[str]:
    """
    Pull the English component lines under the photos.
    Returns one or more descriptions in reading order; if one is found but multiple tables exist,
    we duplicate it for the extra tables.
    """
    lines = [ln.strip() for ln in page_text.splitlines()]
    descs: List[str] = []
    cur: List[str] = []
    for ln in lines:
        if re.search(r"(Polzahl|Contacts).*(Bestell|Ordering)", ln, re.I):
            if cur:
                descs.append(" ".join(cur).strip())
                cur = []
            break
        if re.search(r"\b(Male|Female|receptacle|panel|connector|locking|screw|solder|crimp|cable)\b", ln, re.I):
            cur.append(ln)
    if cur:
        descs.append(" ".join(cur).strip())
    return [d for d in descs if d]


def _tables_with_camelot(pdf_path: str, page_no_one_based: int):
    """
    Try Camelot lattice first (needs strong ruling lines / Ghostscript). If it returns
    no tables, try stream (works without Ghostscript). Return list of tables.
    """
    if camelot is None:
        return []
    page_str = str(page_no_one_based)
    try:
        # LATTICE first
        tbls = camelot.read_pdf(
            pdf_path,
            pages=page_str,
            flavor="lattice",
            line_scale=40,
            strip_text="\n",
        )
        if tbls and len(tbls) > 0:
            return tbls
    except Exception as e:
        if os.environ.get("PDSP_DEBUG") == "1":
            print(f"[pdsp] camelot lattice failed on page {page_str}: {e}")

    # STREAM fallback (no Ghostscript required)
    try:
        tbls = camelot.read_pdf(
            pdf_path,
            pages=page_str,
            flavor="stream",
            edge_tol=200,
            row_tol=10,
            column_tol=10,
            strip_text="\n",
        )
        return tbls or []
    except Exception as e:
        if os.environ.get("PDSP_DEBUG") == "1":
            print(f"[pdsp] camelot stream failed on page {page_str}: {e}")
        return []
    

def _classify_table(df_headers: List[str]) -> str:
    heads = [to_snake_case(h) for h in df_headers]
    has_order = any("ordering" in h or "bestell" in h for h in heads)
    has_contacts = any("polzahl" in h or "contacts" in h for h in heads)
    if len(heads) >= 3:
        contact_heads = [h for h in heads[1:] if re.fullmatch(r"\d{1,2}", h)]
        if contact_heads:
            return "matrix"
    if has_order and has_contacts:
        return "variant"
    return "other"

def _parse_variant_table(df) -> List[Dict[str, Any]]:
    headers = [_canon_header(str(h)) for h in df.columns.tolist()]
    rows = df.values.tolist()
    try:
        idx_contacts = headers.index("contacts")
        idx_order = headers.index("ordering_no")
    except ValueError:
        return []
    out: List[Dict[str, Any]] = []
    for r in rows:
        vals = [(str(v) if v is not None else "").strip() for v in r]
        if not any(vals):
            continue
        order_txt = vals[idx_order]
        if not re.search(r"\d{2}\s*\d{3,4}\s*\d{2}\s*\d{2}", order_txt):
            continue
        m_c = re.search(r"(\d{1,2})", vals[idx_contacts])
        if not m_c:
            continue
        contacts = int(m_c.group(1))
        row_kv: Dict[str, str] = {}
        for i, h in enumerate(headers):
            if i in (idx_contacts, idx_order):
                continue
            if vals[i]:
                row_kv[h] = vals[i]
        out.append({
            "contacts": contacts,
            "ordering_no": " ".join(re.findall(r"\d{2,4}", order_txt)),
            "row_kv": row_kv
        })
    return out

def _parse_matrix_table(df) -> Dict[int, Dict[str, str]]:
    headers = [str(h).strip() for h in df.columns.tolist()]
    contacts_headers: List[int] = []
    for h in headers[1:]:
        h_norm = h.strip()
        if _is_contacts_number(h_norm):
            contacts_headers.append(int(h_norm))
    if not contacts_headers:
        return {}
    matrix: Dict[int, Dict[str, str]] = {c: {} for c in contacts_headers}
    for _, row in df.iterrows():
        cells = [(str(x) if x is not None else "").strip() for x in row.tolist()]
        if not any(cells):
            continue
        left_label = _canon_header(cells[0])
        filled = _ffill_row(cells[1:])
        for i, c in enumerate(contacts_headers):
            val = filled[i] if i < len(filled) else ""
            if val != "":
                matrix[c][left_label] = val
    return matrix

def _fallback_m12_regex(pdf_path: str, full_text: str) -> List[Dict[str, Any]]:
    """
    Very safe fallback: one product per ordering code using whole-doc regex.
    Attempts series detection near each code; attaches only a few simple specs.
    """
    products: List[Dict[str, Any]] = []
    codes = re.finditer(r"\b(?:9\d)\s?(?:\d{3,4}\s?){2,3}\d{2}\b", full_text)
    text = full_text.replace("\u00B7", "·")
    for m in codes:
        code_raw = m.group(0)
        normalized = " ".join(re.findall(r"\d{2,4}", code_raw))
        # local series guess within ±300 chars
        start = max(0, m.start() - 300)
        end = min(len(text), m.end() + 300)
        seg = text[start:end]
        if re.search(r"\b713\s*[·/\-]\s*763\b", seg):
            series = "713 · 763"
        elif re.search(r"\b713\b", seg) and not re.search(r"\b763\b", seg):
            series = "713"
        elif re.search(r"\b763\b", seg) and not re.search(r"\b713\b", seg):
            series = "763"
        else:
            series = "713 · 763"

        specs = [{"spec_key": "coding", "spec_value_text": "M12 A", "raw": "M12 A"}]
        # grab any IP tokens near by
        ips = re.findall(r"\bIP6[7-9]K?\b", seg)
        if ips:
            specs.append({"spec_key": "ip_rating", "spec_value_text": ",".join(sorted(set(ips))), "raw": ",".join(ips)})

        products.append({
            "brand": None,
            "family": series,
            "model_no": None,
            "article_number": None,
            "ordering_code": normalized,
            "product_name": "M12 connector (variant)",
            "description": None,
            "interfaces": None,
            "source_pdf": os.path.basename(pdf_path),
            "pages_covered": [],
            "provenance": {"strategy": "m12_regex_fallback"},
            "specs": specs,
        })
    return products


def _parse_m12_with_camelot(pdf_path: str) -> List[Dict[str, Any]]:
    """Page-aware M12 parser using Camelot for tables and pdfplumber for text."""
    products: List[Dict[str, Any]] = []
    if pdfplumber is None:
        return products

    pages_text = _split_pages(pdf_path)
    if not pages_text:
        return products

    for i, ptxt in enumerate(pages_text):
        page_no = i + 1
        series, coding = _detect_series_and_coding(ptxt)
        block_descs = _guess_block_descriptions(ptxt) or []

        tables = _tables_with_camelot(pdf_path, page_no)
        if not tables:
            continue

        variant_tables = []
        matrix_tables = []
        for t in tables:
            df = t.df
            if df.shape[0] == 0:
                continue
            # promote first row to header if Camelot duplicated header as first row
            df.columns = df.iloc[0]
            df = df[1:].reset_index(drop=True)
            kind = _classify_table(df.columns.tolist())
            if kind == "variant":
                variant_tables.append(df)
            elif kind == "matrix":
                matrix_tables.append(df)

        matrix_by_contacts: Dict[int, Dict[str, str]] = {}
        for mt in matrix_tables:
            part = _parse_matrix_table(mt)
            for contact, kv in part.items():
                matrix_by_contacts.setdefault(contact, {}).update(kv)

        if not variant_tables:
            continue

        # align descriptions with tables
        if block_descs and len(block_descs) == 1 and len(variant_tables) > 1:
            block_descs = block_descs * len(variant_tables)
        while len(block_descs) < len(variant_tables):
            block_descs.append(block_descs[-1] if block_descs else "")

        for t_idx, vdf in enumerate(variant_tables):
            variants = _parse_variant_table(vdf)
            component_description = block_descs[t_idx] if t_idx < len(block_descs) else ""
            for var in variants:
                contacts = var["contacts"]
                ordering_no = var["ordering_no"]
                row_kv = var["row_kv"]

                specs: List[Dict[str, Any]] = []

                # variant-row key/values (store as text to preserve ranges/units)
                for k, v in row_kv.items():
                    key = canonical_key(k)
                    specs.append({"spec_key": key, "spec_value_text": v, "raw": v})

                # matrix values for this contacts count (coerce a few numeric)
                kvs = matrix_by_contacts.get(contacts, {})
                for k, v in kvs.items():
                    if not v:
                        continue
                    key = canonical_key(k)
                    if key in {"rated_voltage_v", "rated_impulse_voltage_v", "temp_min_c", "temp_max_c", "rated_current_40c_a"}:
                        mnum = re.search(r"[-+]?\d+(?:[.,]\d+)?", v)
                        if mnum:
                            num = float(mnum.group(0).replace(",", "."))
                            specs.append({"spec_key": key, "spec_value_num": num, "raw": v})
                            continue
                    if key == "ip_rating":
                        vr = ",".join(sorted(set([x.strip() for x in re.split(r"[,\s]+", v) if x.strip().upper().startswith("IP")])))
                        specs.append({"spec_key": "ip_rating", "spec_value_text": vr, "raw": v})
                        continue
                    specs.append({"spec_key": key, "spec_value_text": v, "raw": v})

                # coding + block description
                specs.append({"spec_key": "coding", "spec_value_text": coding, "raw": coding})
                if component_description:
                    specs.append({"spec_key": "component_description", "spec_value_text": component_description, "raw": component_description})

                products.append({
                    "brand": None,
                    "family": series or "713 · 763",
                    "model_no": None,
                    "article_number": None,
                    "ordering_code": ordering_no,
                    "product_name": "M12 connector (variant)",
                    "description": None,
                    "interfaces": None,
                    "source_pdf": os.path.basename(pdf_path),
                    "pages_covered": [page_no],
                    "provenance": {"strategy": "m12_camelot_page_join", "notes": [f"page={page_no}", f"table_index={t_idx}"]},
                    "specs": specs,
                })

    return products

def _parse_m12_catalog(pdf_path: str, full_text: str) -> List[Dict[str, Any]]:
    """
    Prefer Camelot page-aware parser; if it yields no rows (or Camelot missing),
    fall back to regex so the file is still represented.
    """
    used_camelot = False

    if camelot is not None:
        prods = _parse_m12_with_camelot(pdf_path)
        used_camelot = True
        if os.environ.get("PDSP_DEBUG") == "1":
            print(f"[pdsp] camelot result count: {len(prods)}")
        if prods:
            return prods

    # fallback if camelot absent or returned nothing
    if os.environ.get("PDSP_DEBUG") == "1":
        why = "camelot missing" if not used_camelot else "camelot returned 0 rows"
        print(f"[pdsp] falling back to regex because {why}")
    return _fallback_m12_regex(pdf_path, full_text)



# =====================================================
#                 PUBLIC ENTRYPOINT
# =====================================================

def extract_products(pdf_dir: str) -> List[Dict[str, Any]]:
    """
    Walk a directory of PDFs and extract structured product data.
    - Classification per file
    - M12: Camelot page-aware parser (fallbacks gracefully if Camelot unavailable)
    - Binder/TI: simple regex passes
    """
    products: List[Dict[str, Any]] = []
    if not os.path.isdir(pdf_dir):
        return products

    debug_mode = os.environ.get("PDSP_DEBUG") == "1"

    for name in sorted(os.listdir(pdf_dir)):
        if not name.lower().endswith(".pdf"):
            continue

        path = os.path.join(pdf_dir, name)
        text = _read_text_all(path)
        doc_type = _classify_pdf_by_text_and_name(text, name) if text else "unknown"

        if debug_mode:
            oc = _count_ordering_codes(text) if text else 0
            print(f"[pdsp] {doc_type.upper():8s} -> {name} (codes={oc})")

        if doc_type == "binder":
            products.extend(_parse_binder_cb_s_260(path, text))
        elif doc_type == "m12":
            # prefer Camelot path; if camelot missing, return empty and you can add your legacy regex fallback here
            products.extend(_parse_m12_catalog(path, text))
        elif doc_type == "techinfo":
            products.extend(_parse_technical_info_pdf(path, text))
        else:
            # placeholder so every PDF is represented
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
