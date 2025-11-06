from __future__ import annotations
from typing import Any, Dict, Iterable, List, Sequence, Tuple
import sqlite3
import json


def get_connection(db_path: str) -> sqlite3.Connection:
    """
    Open a SQLite connection with sensible defaults:
    - foreign keys ON
    - row_factory returns dict-like rows
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """
    Create tables and indexes if they don't exist.
    """
    # products table
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY,
            brand TEXT,
            family TEXT,
            model_no TEXT,
            article_number TEXT,
            ordering_code TEXT,
            product_name TEXT,
            description TEXT,
            interfaces TEXT,          -- comma-joined e.g., "RS-232,USB"
            source_pdf TEXT,          -- filename of the PDF
            pages_covered TEXT,       -- comma-joined page numbers
            provenance TEXT           -- JSON blob describing how/where fields were derived
        );
        """
    )

    # specs table
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS specs (
            id INTEGER PRIMARY KEY,
            product_id INTEGER NOT NULL,
            spec_key TEXT NOT NULL,          -- normalized snake_case key
            spec_value_num REAL,             -- numeric representation if applicable
            spec_value_text TEXT,            -- textual representation if not numeric
            unit TEXT,                       -- canonical unit label (e.g., V, A, °C, mm, mm2, %)
            raw TEXT,                        -- raw original text fragment
            applies_to TEXT,                 -- JSON: e.g., {"contacts": 4}
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
        );
        """
    )

    # indexes for common lookups
    conn.execute("CREATE INDEX IF NOT EXISTS idx_products_brand ON products(brand);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_products_model ON products(model_no);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_products_ordering ON products(ordering_code);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_specs_key_num ON specs(spec_key, spec_value_num);")

    conn.commit()


def insert_products(conn: sqlite3.Connection, products: Iterable[Dict[str, Any]]) -> List[int]:
    """
    Insert a batch of product dicts.
    Each product may include a 'specs' list to be inserted after we get the product_id.
    Returns list of inserted product IDs in the same order as input.
    """
    inserted_ids: List[int] = []
    for p in products:
        # Prepare basic fields
        brand = p.get("brand")
        family = p.get("family")
        model_no = p.get("model_no")
        article_number = p.get("article_number")
        ordering_code = p.get("ordering_code")
        product_name = p.get("product_name")
        description = p.get("description")
        interfaces = ",".join(p.get("interfaces") or []) if p.get("interfaces") else None
        source_pdf = p.get("source_pdf")
        pages_covered = ",".join(map(str, p.get("pages_covered") or [])) if p.get("pages_covered") else None
        provenance = p.get("provenance")
        provenance_json = json.dumps(provenance, ensure_ascii=False) if provenance else None

        cur = conn.execute(
            """
            INSERT INTO products
                (brand, family, model_no, article_number, ordering_code,
                 product_name, description, interfaces, source_pdf, pages_covered, provenance)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            [
                brand, family, model_no, article_number, ordering_code,
                product_name, description, interfaces, source_pdf, pages_covered, provenance_json,
            ],
        )
        product_id = cur.lastrowid
        inserted_ids.append(product_id)

        # Insert specs if present
        for s in p.get("specs", []):
            insert_spec(conn, product_id, s)

    conn.commit()
    return inserted_ids


def insert_spec(conn: sqlite3.Connection, product_id: int, spec: Dict[str, Any]) -> int:
    """
    Insert a single spec row for a product.
    spec keys expected:
      spec_key (str), spec_value_num (float|None), spec_value_text (str|None), unit (str|None),
      raw (str|None), applies_to (dict|None)
    """
    applies_to = spec.get("applies_to")
    applies_to_json = json.dumps(applies_to, ensure_ascii=False) if applies_to else None

    cur = conn.execute(
        """
        INSERT INTO specs
            (product_id, spec_key, spec_value_num, spec_value_text, unit, raw, applies_to)
        VALUES (?, ?, ?, ?, ?, ?, ?);
        """,
        [
            product_id,
            spec.get("spec_key"),
            spec.get("spec_value_num"),
            spec.get("spec_value_text"),
            spec.get("unit"),
            spec.get("raw"),
            applies_to_json,
        ],
    )
    return cur.lastrowid


def query_by_model(conn: sqlite3.Connection, model: str) -> List[sqlite3.Row]:
    """
    Exact-match on model_no OR ordering_code (case-insensitive).
    """
    rows = conn.execute(
        """
        SELECT *
        FROM products
        WHERE lower(coalesce(model_no, '')) = lower(?)
           OR lower(coalesce(ordering_code, '')) = lower(?);
        """,
        [model, model],
    ).fetchall()
    return rows


def query_by_brand(conn: sqlite3.Connection, brand: str) -> List[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT *
        FROM products
        WHERE lower(coalesce(brand, '')) = lower(?);
        """,
        [brand],
    ).fetchall()
    return rows


def query_by_spec(
    conn: sqlite3.Connection,
    key: str,
    op: str,
    value: float,
) -> List[sqlite3.Row]:
    """
    Filter products by numeric spec (e.g., rated_voltage >= 24).
    Supported ops: =, !=, <, <=, >, >=
    Returns joined rows (products + matching spec columns).
    """
    allowed_ops = {"=", "!=", "<", "<=", ">", ">="}
    if op not in allowed_ops:
        raise ValueError(f"unsupported operator: {op}")

    sql = f"""
        SELECT
            p.*,
            s.spec_key,
            s.spec_value_num,
            s.spec_value_text,
            s.unit
        FROM specs AS s
        JOIN products AS p ON p.id = s.product_id
        WHERE lower(s.spec_key) = lower(?)
          AND s.spec_value_num {op} ?;
    """
    rows = conn.execute(sql, [key, value]).fetchall()
    return rows

from typing import List, Dict, Any, Optional
import sqlite3

# ... keep existing imports and functions ...


def query_specs_for_code(conn: sqlite3.Connection, code: str) -> List[Dict[str, Any]]:
    """
    Return all specs for the product whose ordering_code == code
    (or model_no == code as fallback).
    """
    sql = """
    SELECT s.product_id, s.spec_key, s.spec_value_num, s.spec_value_text, s.unit, s.raw
    FROM specs s
    JOIN products p ON p.id = s.product_id
    WHERE (p.ordering_code = ? OR p.model_no = ?)
    ORDER BY s.spec_key
    """
    cur = conn.execute(sql, (code, code))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def audit_spec_coverage(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """
    Count how many rows each spec_key appears on, and how many are numeric.
    """
    sql = """
    SELECT
      spec_key,
      COUNT(*) AS total_rows,
      SUM(CASE WHEN spec_value_num IS NOT NULL THEN 1 ELSE 0 END) AS numeric_rows
    FROM specs
    GROUP BY spec_key
    ORDER BY total_rows DESC, spec_key ASC
    """
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def query_by_spec_text(conn: sqlite3.Connection, key: str, contains: Optional[str] = None, equals: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Text-based spec filter. Either 'contains' (ILIKE) or 'equals' (case-insensitive exact).
    """
    if contains:
        sql = """
        SELECT p.*, s.spec_key, s.spec_value_text, s.unit
        FROM products p
        JOIN specs s ON s.product_id = p.id
        WHERE s.spec_key = ? AND s.spec_value_text LIKE ?
        """
        cur = conn.execute(sql, (key, f"%{contains}%"))
    elif equals:
        sql = """
        SELECT p.*, s.spec_key, s.spec_value_text, s.unit
        FROM products p
        JOIN specs s ON s.product_id = p.id
        WHERE s.spec_key = ? AND LOWER(s.spec_value_text) = LOWER(?)
        """
        cur = conn.execute(sql, (key, equals))
    else:
        return []
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]
