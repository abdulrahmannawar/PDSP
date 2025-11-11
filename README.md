# PDSP – Product Data Structuring & Processing

This project parses selected **Binder** documents into a normalized SQLite schema and JSONL export, with a small CLI (`pdsp`) on top.

---

## Project Overview

### Inputs

- Binder **M12** series catalog PDFs (`Auto_Sensorik_Aktorik_M12_Serie_713_763.pdf`)
- Binder **CB-S 260** data sheet (`Data Sheet Model CB-S 260 en.pdf`)
- Binder **Technical Information** page for M12 codings (`43_Technische_Infos_01.20-2.pdf`)

### Outputs

- `products.sqlite` – normalized `products` + `specs` tables
- `products.jsonl` – line-delimited JSON dump of each product & its specs

### Data model (simplified)

- Each **product** = one connector variant / incubator model / M12 coding entry.
- Each **spec** = (`product_id`, `spec_key`, `spec_value_num` or `spec_value_text`, `unit`, `raw`).
- Specs are intentionally fine-grained and typed so they can be filtered from the CLI.

---

## Environment Setup

Requirements: **Python 3.10+**

```bash
git clone https://github.com/abdulrahmannawar/PDSP.git
cd PDSP

python -m venv .venv

.\.venv\Scripts\activate        # Windows OR
source .venv/bin/activate       # macOS / Linux

pip install -e .
```

### Optional: Camelot toggle (HIGHLY RECOMMENDED)

It is highly recommended to disable camelot as it did not work in the tested environments and may provide unexpected results.

```bash
# Disable Camelot-based table parsing
$env:PDSP_CAMELOT=off        # Windows OR
export PDSP_CAMELOT=off     # macOS / Linux
```

---

## Command-Line Usage

All commands are available via the `pdsp` entrypoint. Use `--help` when needed for commands for more information.

### 1. Process PDFs

Parse one or more PDFs into SQLite + JSONL:

```bash
pdsp process <PDF Folder> --db <DB NAME> --jsonl <JSONL NAME>
```

Behavior:

- Walks the given path.
- Detects supported PDFs and applies the corresponding parser:
  - Binder M12 catalog (713–763 series)
  - Binder CB-S 260 data sheet (CBS260-230V & CBS260UL-120V)
  - M12 Technical Information (M12 coding overview)
- Outputs the normalized data into the given SQLite DB and JSONL file.

_NOTE: Rerunning this command will not overwrite the previous tables if the same file names are given._

---

### 2. Inspect a Product

Show all specs for a specific product (by ordering code **or** model number):

```bash
pdsp inspect -c "M12-K"
```

Example (truncated):

```text
┃ product_id ┃ spec_key      ┃ spec_value_num ┃ spec_value_text                    ┃ unit ┃ raw                                                        ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 6          │ application   │ None           │ Spannungsversorgung / Power supply │      │ 12 A 630 V AC IP67 4+PE Spannungsversorgung / Power supply │
│ 6          │ contacts_max  │ 4.0            │ None                               │      │ 12 A 630 V AC IP67 4+PE Spannungsversorgung / Power supply │
│ 6          │ contacts_min  │ 4.0            │ None                               │      │ 12 A 630 V AC IP67 4+PE Spannungsversorgung / Power supply │
│ 6          │ contacts_text │ None           │ 4+PE                               │      │ 12 A 630 V AC IP67 4+PE Spannungsversorgung / Power supply 
```

---

### 3. Discover Available Spec Keys

Different product families expose different `spec_key`s.
Use `pdsp keys` to see what exists in a given slice of the catalog.

```bash
# All spec keys in the DB
pdsp keys --db products.sqlite

# Only for Binder M12 713–763 family
pdsp keys --family "M12 713 - 763"

# Only for CB-S incubators
pdsp keys --family "CB-S"

# Only for Technical Information (M12 codings)
pdsp keys --family "M12 coding"

# Only for one product / code
pdsp keys -c "CBS260-230V"
pdsp keys -c "99 0429 43 04"
```

This is the intended way to discover **which fields are meaningful** for
a given family/product.

---

### 4. Query by Numeric Spec

Filter products by numeric specs via `spec_value_num`.

Example Output:

```bash
# CB-S models with max temperature >= 50 °C
$> pdsp query by-spec --key unit_fuse_a --op ">" --value 10

                                                                               Products (spec filter)                                                                               
┏━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓      
┃ id ┃ brand  ┃ family ┃ model_no      ┃ ordering_code ┃ product_name                   ┃ spec_key    ┃ spec_value_num ┃ spec_value_text ┃ unit ┃ source_pdf                       ┃      
┡━━━━╇━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩      
│ 11 │ BINDER │ CB-S   │ CBS260UL-120V │ None          │ Model CB-S 260 | CO₂ incubator │ unit_fuse_a │ 16.0           │                 │ A    │ Data Sheet Model CB-S 260 en.pdf │      
└────┴────────┴────────┴───────────────┴───────────────┴────────────────────────────────┴─────────────┴────────────────┴─────────────────┴──────┴──────────────────────────────────┘
```

Supported operators:

- `=`, `!=`
- `>`, `>=`
- `<`, `<=`

If a spec is stored only as text (e.g. `ip_rating`, `application`), use
`pdsp inspect` or `by-spec-text` to explore it; `by-spec` operates on numeric fields.

---

### 5. Query by Text Spec Example

Filter products by textual specs via `spec_value_text`.

Example Output:

```bash
# M12 codings products with containing an "IP68' rating
$> pdsp query by-spec-text --key ip_rating --contains "IP68"
                                                                      Products (spec filter)                                                                      
┏━━━━┳━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ id ┃ brand  ┃ family     ┃ model_no ┃ ordering_code ┃ product_name ┃ spec_key  ┃ spec_value_num ┃ spec_value_text     ┃ unit ┃ source_pdf                      ┃
┡━━━━╇━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1  │ Binder │ M12 coding │ M12-A    │ None          │ M12 A-coding │ ip_rating │                │ IP67 / IP68 / IP69K │      │ 43_Technische_Infos_01.20-2.pdf │
│ 2  │ Binder │ M12 coding │ M12-B    │ None          │ M12 B-coding │ ip_rating │                │ IP67 / IP68         │      │ 43_Technische_Infos_01.20-2.pdf │
╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇
```
---
## What Gets Parsed (Overview)

### 1. Binder M12 Catalog (713–763)

For each connector variant (ordering code):

- Identity:
  - `brand`, `family`, `ordering_code`, `product_name`
  - `source_pdf`, `pages_covered`, `provenance`
- Geometry / configuration:
  - `contacts`
  - `cable_outlet_min_mm`, `cable_outlet_max_mm`
- Protection & ratings (per contact count where applicable):
  - `ip_rating`
  - `temp_min_c`, `temp_max_c`
  - `rated_voltage_v`, `rated_impulse_voltage_v`
  - `rated_current_a`
  - `pollution_degree`, `overvoltage_categorie`
- Mechanical & materials:
  - `connector_locking_system`
  - `termination`
  - `wire_gauge`
  - `mechanical_operation`
  - `material_of_contact`
  - `contact_plating`
  - `material_of_contact_body`
  - `material_of_housing`
  - `material_of_locking`
- All mapped via a contact-aware matrix parser to avoid hardcoding page layouts.

### 2. Binder CB-S 260 (CBS260-230V & CBS260UL-120V)

For both models, parsed from a single data sheet:

- Identity:
  - `model_no`, `article_number`, `product_name`, shared `family = "CB-S"`
- Temperature:
  - `temp_above_ambient_c` (e.g. +6 °C)
  - `temp_max_c` (e.g. 50 °C)
  - `temp_uniformity_c`, `temp_fluctuation_c`
- CO₂ & climate:
  - `co2_min_pct`, `co2_max_pct`
  - `humidity_min_pct_rh`, `humidity_max_pct_rh`
- Electrical:
  - `supply_voltage_min_v`, `supply_voltage_max_v`
  - `supply_freq_hz`
  - `nominal_power_w`
  - `unit_fuse_a`
- Dimensions & loads:
  - `interior_volume_l`
  - `external_dimensions_mm`
  - `weight_kg`
  - `shelves_count`, `load_per_rack_kg`, `permitted_load_kg`
- Plus a few environment/fixture fields where present.

### 3. M12 Technical Information (Codings)

The technical info page is treated as a set of virtual products:

- `M12-A`, `M12-B`, `M12-D`, `M12-X`, `M12-S`, `M12-K`, `M12-T`, `M12-L`, `M12-US-C`
- For each coding:
  - `current_min_a`, `current_max_a`
  - `voltage_min_v`, `voltage_max_v`
  - `ip_rating`
  - `contacts_min`, `contacts_max`, `contacts_text`
  - `application`
  - `voltage_ac` / `voltage_dc` stored as a textual boolean yes/no
- These are useful as design constraints or filters against connector variants.

---

## Approach Summary

1. **PDF → text / tables**
   - Use `pypdf` for robust text extraction.
   - Attempted to use Camelot where table structure is present (Failed).
   - Normalize whitespace, dashes, and number formats early to make the text regex-friendly.

2. **Heuristic + regex-based parsing**
   - Use targeted regular expressions to:
     - detect document type (M12 catalog, CB-S sheet, TI page),
     - locate section headers and technical blocks,
     - extract structured fields from bilingual / inline lines (e.g. `0 ...20 Vol.-% CO2`).
   - Regex is scoped and readable (no page-number hardcoding), so behavior is predictable and maintainable.

3. **Contact-aware table reconstruction (M12)**
   - Parse small ordering tables (contacts, cable outlet, ordering code).
   - Parse the large spec matrix once per page.
   - Build a `contacts → {spec_key: value}` map and join it back to each ordering code.
   - This avoids hardcoding positions and ensures each variant gets the correct ratings and materials.
   - _NOTE: Does not work fully due to the variance of all pages. A specific regex would be required for each page to accurately extract data._

4. **Model-paired parsing (CB-S 260)**
   - Many lines contain values for both models side by side.
   - Regex splits those into two parallel spec sets for:
     - `CBS260-230V`
     - `CBS260UL-120V`
   - Ensures both models stay in sync with the original sheet.

5. **Virtual products for M12 codings (TI page)**
   - Treat each coding (A, B, D, X, S, K, T, L, US-C) as a product-like entry.
   - Extract current, voltage, IP rating, contacts range, and application into specs.
   - Makes the generic coding rules queryable like any other product.

6. **Minimal, query-friendly schema**
   - Two core tables: `products` and `specs`.
   - All behavior exposed via:
     - `pdsp process` (build DB),
     - `pdsp inspect` (see one product),
     - `pdsp keys` (discover available spec_key values),
     - `pdsp query by-spec` (filter numerics).
