from __future__ import annotations
import re
from typing import Tuple, Optional, List, Dict
import itertools

def to_snake_case(s: str) -> str:
    s = s.strip()
    s = s.replace("–", "-").replace("—", "-").replace("·", ".")
    s = re.sub(r"[^\w\-/\. ]+", " ", s, flags=re.UNICODE)
    s = s.replace("/", " ").replace("\\", " ")
    s = s.replace(".", " ")
    s = s.replace("-", " ")
    s = re.sub(r"\s+", " ", s).strip().lower()
    s = s.replace(" ", "_")
    return s

def canonical_key(s: str) -> str:
    return to_snake_case(s)

def normalize_awg_or_mm2(raw: str) -> Tuple[Optional[float], Optional[str], str]:
    text = (raw or "").strip()
    m_mm2 = re.search(r"([0-9]+(?:[.,][0-9]+)?)\s*mm(?:2|²)\b", text, flags=re.I)
    if m_mm2:
        val = float(m_mm2.group(1).replace(",", "."))
        return val, "mm2", raw

    awg_map = {24: 0.205, 23: 0.258, 22: 0.326, 21: 0.410, 20: 0.519, 19: 0.653, 18: 0.823}
    m_awg = re.search(r"\bawg\s*([0-9]{1,2})\b", text, flags=re.I)
    if m_awg:
        n = int(m_awg.group(1))
        if n in awg_map:
            return float(awg_map[n]), "mm2_est", raw
        return None, "awg", raw
    return None, None, raw

# -------- NEW helpers below --------

def parse_mm_range(text: Optional[str]) -> Tuple[Optional[float], Optional[float]]:
    """
    Accepts '4–6 mm' or '4-6,5 mm' -> (4.0, 6.5)
    """
    if not text:
        return None, None
    t = text.replace("–", "-")
    m = re.search(r"([0-9]+(?:[.,][0-9]+)?)\s*-\s*([0-9]+(?:[.,][0-9]+)?)\s*mm\b", t)
    if not m:
        return None, None
    lo = float(m.group(1).replace(",", "."))
    hi = float(m.group(2).replace(",", "."))
    return lo, hi

def parse_ip_code(page_text: str) -> Optional[str]:
    m = re.search(r"\bIP\d{2}(?:[A-ZK])?(?:,\s*Outdoor\s*IP\d{2}[A-ZK]?)?", page_text, flags=re.I)
    return m.group(0).replace(" ", "") if m else None

def parse_temp_block(page_text: str) -> Tuple[Optional[float], Optional[float]]:
    """
    Extract 'Upper temperature' / 'Lower temperature' like +85 °C / –40 °C
    """
    tmin = None
    tmax = None
    # Upper
    m_up = re.search(r"(?:Upper temperature|Obere Grenztemperatur)[^\n]*?([+\-–]?\s*\d{1,3})\s*°C", page_text, flags=re.I)
    if m_up:
        tmax = float(re.sub(r"\s+", "", m_up.group(1).replace("–", "-").replace("+", "")))
    # Lower
    m_lo = re.search(r"(?:Lower temperature|Untere Grenztemperatur)[^\n]*?([+\-–]?\s*\d{1,3})\s*°C", page_text, flags=re.I)
    if m_lo:
        tmin = float(re.sub(r"\s+", "", m_lo.group(1).replace("–", "-").replace("+", "")))
    return tmin, tmax

def parse_voltage_block(page_text: str) -> List[Dict[str, object]]:
    """
    Capture common triplet '250 V / 60 V / 30 V' on these pages.
    """
    out: List[Dict[str, object]] = []
    for m in re.finditer(r"\b(\d{2,4})\s*V\b", page_text):
        out.append({"spec_key": "rated_voltage_v", "spec_value_num": float(m.group(1)), "unit": "V", "raw": m.group(0)})
    return out[:3]  # keep it light; usually three values appear

def parse_current_block(page_text: str) -> List[Dict[str, object]]:
    """
    Examples include lines like '4 A 2 A 1,5 A' or '8 A ... 2 A'.
    """
    out: List[Dict[str, object]] = []
    amps = re.findall(r"(\d{1,2}(?:[.,]\d)?)\s*A\b", page_text)
    for a in amps[:3]:
        out.append({"spec_key": "rated_current_a", "spec_value_num": float(a.replace(",", ".")), "unit": "A", "raw": a + " A"})
    return out

# ADD near other helpers
def english_tail(label: str) -> str:
    """
    Ensure we only keep the English part at the end of a combined 'DE EN' label.
    If it already looks English-only, this is a no-op.
    """
    m = re.match(
        r"(.+?)\s+([A-Z][A-Za-z0-9 ().,°/%+-]*(?:\s+[A-Za-z][A-Za-z0-9 ().,°/%+-]*)*)$",
        label.strip(),
    )
    if m:
        return m.group(2).strip()
    return label.strip()


def extract_row_by_english_label(page_text: str, label_en: str) -> str | None:
    # Find the line that ends with the English label and return the English value part
    for line in page_text.splitlines():
        if re.search(rf"(?i)\b{re.escape(label_en)}\b", line):
            # drop the label itself, keep what's before, then take the English half
            head = re.sub(rf"(?i)\b{re.escape(label_en)}\b", "", line).strip(" :\t")
            head = re.sub(r"\s+", " ", head).strip()
            return english_tail(head) or None
    return None

def _parse_contact_pair_line(page_text: str, de_label: str, en_label: str, unit_pat: str) -> tuple[str | None, str | None]:
    # Expect a line with: <DE label> ... <val_for_4> ... <val_for_5> ... <EN label>
    rx = rf"(?im)^{re.escape(de_label)}\s+(?P<c4>.+?)\s+(?P<c5>.+?)\s+{re.escape(en_label)}\s*$"
    m = re.search(rx, page_text)
    if not m:
        return None, None
    def pick(s: str) -> str | None:
        u = re.search(unit_pat, s)
        return u.group(0).strip() if u else None
    return pick(m.group("c4")), pick(m.group("c5"))

def parse_rated_voltage_pair(page_text: str) -> tuple[str | None, str | None]:
    return _parse_contact_pair_line(page_text, "Bemessungsspannung", "Rated voltage", r"\d{2,4}\s*V")

def parse_rated_impulse_voltage_pair(page_text: str) -> tuple[str | None, str | None]:
    return _parse_contact_pair_line(page_text, "Bemessungs-Stoßspannung", "Rated impulse voltage", r"\d{3,5}\s*V")

def parse_rated_current_pair(page_text: str) -> tuple[str | None, str | None]:
    return _parse_contact_pair_line(page_text, "Bemessungsstrom \(40°C\)", "Rated current \(40 °C\)", r"\d{1,2}(?:[.,]\d)?\s*A")


def parse_mating_cycles(page_text: str) -> Optional[int]:
    m = re.search(r"(?i)(?:mechanische lebensdauer|mating cycles)[^\n>]*>\s*([0-9]{1,5})", page_text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None



def normalize_bilingual_value(val: str) -> str:
    val = val.strip()

    # Pattern: "CuSn (Bronze/bronze)" -> "CuSn (bronze)"
    m = re.match(r"^(.*?\()([^/]+)/([^)]*)\)$", val)
    if m:
        prefix, _, en = m.groups()
        return f"{prefix}{en.strip()})"

    # Generic "left/right" translation pattern
    if "/" in val:
        left, right = val.rsplit("/", 1)
        left = left.strip()
        right = right.strip()

        # Case: "PA, Zinkdruckguss vernickelt/zinc diecasting nickel plated"
        # Keep stable prefix before comma, swap trailing phrase to English.
        if "," in left:
            prefix = left.split(",", 1)[0].strip()
            return f"{prefix}, {right}".strip()

        # Default: assume right-hand side is English
        return right

    return val











# ADD: detect the contact columns order from the header line
# REPLACE the old detect_contact_columns with this
def detect_contact_columns(page_text: str) -> list[int]:
    lines = page_text.splitlines()

    # primary: look around "Number of contacts"
    for idx, ln in enumerate(lines):
        if re.search(r"(?i)\bnumber of contacts\b", ln):
            window = " ".join(lines[max(0, idx - 4): idx + 2])
            nums = re.findall(r"\b([1-9]|1[0-2])\b", window)
            cols = [int(n) for n in nums if n.isdigit()]
            cols = [c for c in cols if 2 <= c <= 16]
            if cols:
                seen, ordered = set(), []
                for c in cols:
                    if c not in seen:
                        seen.add(c); ordered.append(c)
                return ordered

    # fallback: look forward after "Polzahl"
    for idx, ln in enumerate(lines):
        if re.search(r"(?i)\bpolzahl\b", ln):
            window = " ".join(lines[idx: idx + 6])
            nums = re.findall(r"\b([1-9]|1[0-2])\b", window)
            cols = [int(n) for n in nums if n.isdigit()]
            cols = [c for c in cols if 2 <= c <= 16]
            if cols:
                seen, ordered = set(), []
                for c in cols:
                    if c not in seen:
                        seen.add(c); ordered.append(c)
                return ordered

    return []


# ADD: split middle area into K cells using spacing heuristics
def split_cells(middle: str, k: int) -> list[str]:
    text = middle.replace("\u2009", " ").strip()

    # try 3+ spaces as hard column gaps
    parts = [p.strip() for p in re.split(r"\s{3,}", text) if p.strip()]
    if k > 1 and len(parts) == k:
        return parts

    # try 2+ spaces
    parts = [p.strip() for p in re.split(r"\s{2,}", text) if p.strip()]
    if k > 1 and len(parts) == k:
        return parts

    # fallback: unit-aware bucketing (keeps numbers with their units)
    tokens = re.findall(r"[^\s]+(?:\s?(?:V|A|mm|°C|K|PA|PEEK|IP[0-9A-Z]+))?", text)
    if k > 1 and len(tokens) >= k:
        buckets = [[] for _ in range(k)]
        for i, tok in enumerate(tokens):
            buckets[i % k].append(tok)
        return [" ".join(b).strip() for b in buckets]

    return [text] if text else []


# ADD: parse any DE/.../EN matrix row into english key + per-contact values
def parse_contact_matrix(page_text: str) -> tuple[list[int], list[tuple[str, list[str]]]]:
    cols = detect_contact_columns(page_text)
    k = max(len(cols), 1)

    rows: list[tuple[str, list[str]]] = []
    # line ends with English label; middle holds per-contact cells; allow ragged spacing
    rx = re.compile(r"(?m)^(?P<de>.+?)\s{2,}(?P<middle>.+?)\s{2,}(?P<en>[A-Za-z][A-Za-z0-9 ().,°/%+-]+)\s*$")
    for m in rx.finditer(page_text):
        en = m.group("en").strip()
        if re.search(r"(?i)\bnumber of contacts\b", en):
            continue  # skip the header row
        key = to_snake_case(en)
        middle = m.group("middle")

        cells = split_cells(middle, k)
        # enforce English half when a cell contains "de/en"
        cells = [c.split("/")[-1].strip() for c in cells]

        # pad/trim to k columns
        if len(cells) < k:
            cells += [""] * (k - len(cells))
        elif len(cells) > k:
            cells = cells[:k]

        rows.append((key, cells))

    return cols, rows

def extract_spec_labels(page_text: str) -> list[str]:
    labels: list[str] = []
    lines = page_text.splitlines()
    in_block = False

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if not in_block:
            if "polzahl" in line.lower() and "number of contacts" in line.lower():
                in_block = True
            continue

        m = re.match(
            r"(.+?)\s+([A-Z][A-Za-z0-9 ().,°/%+-]*(?:\s+[A-Za-z][A-Za-z0-9 ().,°/%+-]*)*)$",
            line,
        )
        if not m:
            break

        # NEW: only keep the English tail
        en_part = m.group(2).strip()
        labels.append(en_part)

    return labels


# ADD
def extract_spec_values(page_text: str, expected_count: int) -> list[str]:
    lines = page_text.splitlines()
    start = 0

    # start just after the last small-table header
    for i, line in enumerate(lines):
        if "Contacts Cable outlet Ordering-No." in line:
            start = i + 1

    values: list[str] = []
    started = False

    for line in lines[start:]:
        line = line.strip()
        if not line:
            continue

        # detect ordering / small-table noise
        has_code = re.search(r"(?:9\d)\s+\d{3,4}\s+\d{3,4}\s+\d{2}", line)
        polzahl_row = re.fullmatch(r"(?:\d+\s+)+\d+", line)
        mm_and_code = ("mm" in line) and has_code

        if not started and (has_code or polzahl_row or mm_and_code):
            # still in ordering-table block, skip
            continue

        # first non-table line = first spec value
        if not started:
            started = True

        values.append(line)
        if len(values) >= expected_count:
            break

    return values


def build_contact_value_map(page_text: str, table_contacts: list[int]) -> dict[int, dict[str, str]]:
    labels = extract_spec_labels(page_text)
    if not labels:
        return {}

    values = extract_spec_values(page_text, len(labels))
    if len(values) != len(labels):
        return {}

    header_contacts = parse_contact_header(page_text)
    # Prefer the header (true matrix columns); fall back to contacts seen in small tables
    if header_contacts:
        contacts = header_contacts
    else:
        contacts = sorted({c for c in table_contacts if c is not None})

    per_contact: dict[int, dict[str, str]] = {c: {} for c in contacts} if contacts else {}
    shared_only: dict[str, str] = {}

    for label, raw_val in zip(labels, values):
        key = to_snake_case(english_tail(label))
        raw_val = raw_val.strip()
        if not raw_val:
            continue

        # English side for bilingual cells
        val = normalize_bilingual_value(raw_val)


        # tokens like '250 V', '60 V', '4 A', '8 mm', 'IP67'
        tokens = re.findall(
            r"\d+(?:[.,]\d+)?\s*(?:V|A|mm|°C|VDC|VAC|IP[0-9A-Z]+)",
            raw_val,
        )

        if contacts and len(tokens) == len(contacts) and len(tokens) > 1:
            # map positionally: first token -> first contact, etc.
            for c, tok in zip(contacts, tokens):
                per_contact.setdefault(c, {})[key] = tok.strip()
        else:
            # shared value: same for all contacts
            if per_contact:
                for c in contacts:
                    per_contact[c].setdefault(key, val)
            else:
                shared_only[key] = val

    return per_contact or ({0: shared_only} if shared_only else {})



def infer_contacts_from_polzahl_block(page_text: str) -> list[int]:
    """
    Infer contact counts from the Polzahl/ordering tables.

    Looks between the 'Polzahl Kabeldurchlass Bestell-Nr.' header
    and the start of the spec rows and collects numbers like 3,4,5,8,12.
    """
    lines = page_text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if "Polzahl Kabeldurchlass Bestell-Nr." in line:
            start = i
    if start is None:
        return []

    nums = set()
    for line in lines[start + 1 : start + 15]:
        line = line.strip()
        # stop when we reach the spec header block
        if "Connector locking system" in line:
            break
        # collect plausible Polzahl values
        for n in re.findall(r"\b([3-9]|1[0-2])\b", line):
            nums.add(int(n))

    return sorted(nums)

def parse_contact_header(page_text: str) -> list[int]:
    """
    Extract contact column numbers from the Polzahl header row, e.g.
      'Polzahl 4 5 Number of contacts'  -> [4, 5]
    """
    for line in page_text.splitlines():
        if "Number of contacts" in line:
            nums = re.findall(r"\b(\d{1,2})\b", line)
            return [int(n) for n in nums]
    return []
