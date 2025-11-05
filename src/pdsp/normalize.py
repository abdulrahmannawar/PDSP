from __future__ import annotations
from typing import Dict, Optional, Tuple
import re

# --- Canonical key mapping ---
# Map multilingual / variant labels to stable snake_case keys
KEY_ALIASES: Dict[str, str] = {
    # german/english common fields seen in technical PDFs
    "bemessungsspannung": "rated_voltage",
    "rated_voltage": "rated_voltage",
    "bemessungsstrom_40_c": "rated_current_40c",
    "rated_current": "rated_current",
    "schutzart": "ip_rating",
    "degree_of_protection": "ip_rating",
    "polzahl": "contacts",
    "number_of_contacts": "contacts",
    "anschlussart": "termination_type",
    "termination": "termination_type",
    "anschlussquerschnitt": "wire_gauge_mm2",
    "wire_gauge": "wire_gauge_mm2",
    "kabeldurchlass": "cable_outlet_diameter_mm_range",
    "temperature_min": "temp_min_c",
    "temperature_max": "temp_max_c",
    "materials_contact": "materials_contact",
    "contact_plating": "contact_plating",
    "bemessungs_stossspannung": "rated_impulse_voltage",
}

def to_snake_case(s: str) -> str:
    s = s.strip()
    s = re.sub(r"[^0-9a-zA-Z]+", "_", s)
    s = re.sub(r"_+", "_", s)
    return s.lower().strip("_")

def canonical_key(k: str) -> str:
    """
    Convert a raw spec label to a canonical snake_case key using KEY_ALIASES.
    Falls back to snake_case of the input if no alias is found.
    """
    sk = to_snake_case(k)
    return KEY_ALIASES.get(sk, sk)

# --- Units & numbers ---

def parse_numeric_with_unit(text: str) -> Tuple[Optional[float], Optional[str]]:
    """
    Pull the first number and an optional trailing unit from a string.
    Normalizes decimal comma to dot. Returns (value, unit|None).
    Examples: '230 V' -> (230.0, 'V'), '0,75 mm2' -> (0.75, 'mm2')
    """
    if text is None:
        return None, None
    t = text.replace(",", ".").strip()
    m = re.search(r"([-+]?\d+(?:\.\d+)?)\s*([%°a-zA-Z²]+)?", t)
    if not m:
        return None, None
    val = float(m.group(1))
    unit = m.group(2) or None
    if unit:
        u = unit.strip()
        # normalize a couple of common variants
        if u.lower() in {"vol.%", "vol%"}:
            unit = "%"
        elif u in {"mm²"}:
            unit = "mm2"
        else:
            unit = u
    return val, unit

# --- AWG ↔ mm² ---

_AWG_TO_MM2 = {28: 0.081, 26: 0.129, 24: 0.205, 22: 0.326, 20: 0.518, 18: 0.823, 16: 1.31, 14: 2.08}

def normalize_awg_or_mm2(fragment: str) -> Tuple[Optional[float], Optional[str], str]:
    """
    Convert 'AWG N' or 'x[.y] mm2/mm²' into numeric mm² where possible.
    Returns (value_mm2, unit, raw_string).
    If conversion fails, returns (None, None, raw).
    """
    raw = (fragment or "").strip()
    if not raw:
        return None, None, raw

    # AWG pattern
    m_awg = re.search(r"AWG\s*(\d{1,2})", raw, flags=re.I)
    if m_awg:
        awg = int(m_awg.group(1))
        mm2 = _AWG_TO_MM2.get(awg)
        if mm2 is not None:
            return mm2, "mm2", raw

    # plain mm² pattern
    m_mm = re.search(r"(\d+(?:[.,]\d+)?)\s*mm(?:2|²)", raw, flags=re.I)
    if m_mm:
        val = float(m_mm.group(1).replace(",", "."))
        return val, "mm2", raw

    return None, None, raw
