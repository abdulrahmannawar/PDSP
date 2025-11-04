from __future__ import annotations
from typing import Dict, Any, List
import os


def extract_products(pdf_dir: str) -> List[Dict[str, Any]]:
    """
    Minimal extractor: one placeholder product per PDF file.
    """
    products: List[Dict[str, Any]] = []
    if not os.path.isdir(pdf_dir):
        return products

    for name in sorted(os.listdir(pdf_dir)):
        if not name.lower().endswith(".pdf"):
            continue
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
            "pages_covered": [1],   # placeholder
            "provenance": {"strategy": "placeholder_per_pdf"},
            "specs": [],
        })
    return products
