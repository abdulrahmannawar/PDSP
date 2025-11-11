from __future__ import annotations
import re
from typing import Tuple, Optional, List, Dict

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


def extract_spec_values(page_text: str, expected_count: int) -> list[str]:
    lines = page_text.splitlines()

    # 1) Find the 'Polzahl Number of contacts' spec header
    start = None
    for i, line in enumerate(lines):
        if "Polzahl" in line and "Number of contacts" in line:
            start = i
            break
    if start is None:
        return []

    # 2) Walk label lines (DE+EN) to find the end of the header block
    label_pattern = re.compile(
        r"(.+?)\s+([A-Z][A-Za-z0-9 ().,°/%+-]*(?:\s+[A-Za-z][A-Za-z0-9 ().,°/%+-]*)*)$"
    )
    last_label_idx = None

    for j in range(start + 1, len(lines)):
        line = lines[j].strip()
        if not line:
            continue
        if label_pattern.match(line):
            last_label_idx = j
            continue
        # after seen at least one label, the first non-matching line ends the block
        if last_label_idx is not None:
            break

    if last_label_idx is None:
        return []

    # 3) Everything after the label block (non-empty lines) are value rows
    values: list[str] = []
    for line in lines[last_label_idx + 1:]:
        line = line.strip()
        if not line:
            continue

        if not values:
            # skip obvious blueprint/legend noise:
            #  - pure numbers or tiny tokens
            #  - tokens like "Ø", "1 x", "21M", "SW 18mm", "3 4 5 8 12"
            if re.fullmatch(r"[\d\s,~xØ°A-Za-z]+", line) and len(line) <= 11:
                continue

        values.append(line)
        if len(values) >= expected_count:
            break


    return values


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
        # English side for bilingual cells
        val = normalize_bilingual_value(raw_val)
        
        raw_lower = raw_val.lower()
        contact_set = sorted(contacts) if contacts else []

        
        # termination: "schrauben/screw löten/solder" → first 4 screw, last solder
        if (
            contacts
            and contact_set == [3, 4, 5, 8, 12]
            and key == "termination"
            and ("screw" in raw_lower)
            and ("solder" in raw_lower)
        ):
            for c in contacts[:-1]:
                per_contact.setdefault(c, {})[key] = "screw"
            per_contact.setdefault(contacts[-1], {})[key] = "solder"
            continue

        # mechanical operation: "> 50 ...  > 100 ..." → 3/4/5 : >50, 8/12 : >100
        if (
            contacts
            and contact_set == [3, 4, 5, 8, 12]
            and key == "mechanical_operation"
            and ("> 50" in raw_lower or "≥ 50" in raw_lower)
            and ("> 100" in raw_lower or "≥ 100" in raw_lower)
        ):
            for c in contacts[:3]:
                per_contact.setdefault(c, {})[key] = "> 50 mating cycles"
            for c in contacts[3:]:
                per_contact.setdefault(c, {})[key] = "> 100 mating cycles"
            continue

        # contact plating: "CuSnZn (Optalloy/optalloy) Au (Gold/gold)" → 3/4/5 optalloy, 8/12 gold
        if (
            contacts
            and contact_set == [3, 4, 5, 8, 12]
            and key == "contact_plating"
            and ("optalloy" in raw_lower)
            and ("au" in raw_lower)
        ):
            opt = "CuSnZn (Optalloy/optalloy)"
            gold = "Au (gold)"
            for c in contacts[:3]:
                per_contact.setdefault(c, {})[key] = opt
            for c in contacts[3:]:
                per_contact.setdefault(c, {})[key] = gold
            continue

        # tokens like '250 V', '60 V', '4 A', '8 mm', 'IP67'
        tokens = re.findall(
            r"\d+(?:[.,]\d+)?\s*(?:V|A|mm|°C|VDC|VAC|IP[0-9A-Z]+)",
            raw_val,
        )

        if contacts and tokens:
            n_contacts = len(contacts)
            n_tokens = len(tokens)

            # Used for rated_voltage, rated_impulse_voltage, rated_current_40_c.
            # Special case for binder summary layout: 5 contacts (3,4,5,8,12) & 3 tokens
            if contact_set == [3, 4, 5, 8, 12] and n_tokens == 3 and n_contacts == 5:
                if key in ("rated_voltage", "rated_impulse_voltage"):
                    groups = [2, 1, 2]   # 3&4 → first; 5 → middle; 8&12 → last
                elif key == "rated_current_40_c":
                    groups = [3, 1, 1]   # 3&4&5 → first; 8 → middle; 12 → last
                else:
                    groups = None

                if groups:
                    idx = 0
                    for tok, g in zip(tokens, groups):
                        for c in contacts[idx : idx + g]:
                            per_contact.setdefault(c, {})[key] = tok.strip()
                        idx += g
                    continue

            if n_tokens == n_contacts and n_tokens > 1:
                # 1:1 mapping
                for c, tok in zip(contacts, tokens):
                    per_contact.setdefault(c, {})[key] = tok.strip()

            elif 1 < n_tokens < n_contacts:
                # Generic grouped mapping: distribute as evenly as possible
                base = n_contacts // n_tokens
                extra = n_contacts % n_tokens
                groups = []
                for i in range(n_tokens):
                    size = base + (1 if i < extra else 0)
                    groups.append(max(size, 1))

                idx = 0
                for tok, g in zip(tokens, groups):
                    for c in contacts[idx : idx + g]:
                        per_contact.setdefault(c, {})[key] = tok.strip()
                    idx += g

            elif n_tokens > n_contacts and n_contacts > 1:
                # Too many tokens: best-effort positional
                for c, tok in zip(contacts, tokens[:n_contacts]):
                    per_contact.setdefault(c, {})[key] = tok.strip()

            else:
                # Single token or no sensible split: shared
                for c in contacts:
                    per_contact.setdefault(c, {}).setdefault(key, val)
        else:
            # No numeric cues: treat as shared spec if not already set
            if contacts:
                for c in contacts:
                    per_contact.setdefault(c, {}).setdefault(key, val)
            else:
                shared_only[key] = val


    return per_contact or ({0: shared_only} if shared_only else {})