# src/pdsp/normalize.py
from __future__ import annotations
import re
from typing import Tuple, Optional

def to_snake_case(s: str) -> str:
    """
    Convert a label/header into snake_case.
    Keeps ASCII only where possible; collapses whitespace and dashes.
    """
    s = s.strip()
    # normalize common punctuation variants
    s = s.replace("–", "-").replace("—", "-").replace("·", ".")
    s = re.sub(r"[^\w\-/\. ]+", " ", s, flags=re.UNICODE)  # remove odd glyphs, keep basic separators
    s = s.replace("/", " ").replace("\\", " ")
    s = s.replace(".", " ")  # treat middle dot or dot separators as spaces for headers
    s = s.replace("-", " ")
    s = re.sub(r"\s+", " ", s).strip().lower()
    s = s.replace(" ", "_")
    return s

def canonical_key(s: str) -> str:
    """
    Hook for future aliasing beyond basic snake-case.
    Currently just calls to_snake_case. Kept separate so callers don't depend on implementation detail.
    """
    return to_snake_case(s)

def normalize_awg_or_mm2(raw: str) -> Tuple[Optional[float], Optional[str], str]:
    """
    Try to normalize a gauge expression to mm² (numeric).

    Returns: (mm2_value or None, unit_str or None, raw_string)
    - If expression is already mm², parse number and return (value, "mm2", raw)
    - If expression is AWG N, return approximate mm² using a standard table (limited set)
    - Otherwise, return (None, None, raw)
    """
    text = (raw or "").strip()
    # direct mm² like "0,75 mm²" or "0.75 mm2"
    m_mm2 = re.search(r"([0-9]+(?:[.,][0-9]+)?)\s*mm(?:2|²)\b", text, flags=re.I)
    if m_mm2:
        val = float(m_mm2.group(1).replace(",", "."))
        return val, "mm2", raw

    # AWG mapping (common values around this catalog)
    awg_map = {
        24: 0.205, 23: 0.258, 22: 0.326, 21: 0.410, 20: 0.519,
        19: 0.653, 18: 0.823
    }
    m_awg = re.search(r"\bawg\s*([0-9]{1,2})\b", text, flags=re.I)
    if m_awg:
        n = int(m_awg.group(1))
        if n in awg_map:
            return float(awg_map[n]), "mm2_est", raw
        return None, "awg", raw

    return None, None, raw
