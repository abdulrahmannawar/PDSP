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
