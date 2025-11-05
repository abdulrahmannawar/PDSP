# SQLite Schema

## products
id (PK), brand, family, model_no, article_number, ordering_code, product_name, description,
interfaces (comma-joined), source_pdf, pages_covered (comma-joined), provenance (JSON).

Indexes: brand, model_no, ordering_code.

## specs
id (PK), product_id (FK→products.id), spec_key, spec_value_num, spec_value_text, unit, raw, applies_to (JSON).

Index: (spec_key, spec_value_num) for numeric filters.
