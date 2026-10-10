# Changelog

All notable changes to DataBizex are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-10-11

### Added

- Detection of regional number and date formats per column: decimal comma (`43,92`),
  thousands separators (`1.234,56`, and US-style `1,234.56`), and `YYYY-MM-DD`,
  `DD.MM.YYYY`, `DD/MM/YYYY` and `MM/DD/YYYY` dates. Ambiguous day/month order is read
  as DD/MM and flagged.
- **Import Options** dialog after loading a file whose delimiter, encoding, number or date
  format is not the default. It states what was detected, and every setting can be
  overridden; changing the delimiter or encoding reads the file again. Numbers and dates
  are converted in the working copy only (`app/data/formats.py`), and values that do not
  fit keep their text so the quality checks still report them. The settings and the
  converted columns are shown in the source line and written to the Power BI
  `package_info.txt`.
- Source line numbers: every working row keeps its line in the source file (header =
  line 1; for Excel, the sheet row) through import conversion, column mapping, duplicate
  removal and normalization. The number never reaches KPI queries or the Power BI package.
- **Failed rows** view on the Data Quality tab: a dropdown of every rule with failures
  (with counts) plus "All failed rows" (error rules only), and a table of the matching
  working rows with `source_row` first and a `failed_rules` column listing every rule the
  row failed. "Missing values (all columns)" is left out of `failed_rules` when a
  column-specific "Missing values: <column>" check already lists the row. Shows up to 500
  rows, states the full count, and refreshes after duplicate removal, normalization, reset
  and loading a rule set.
- **Export failed rows to CSV...** on the Data Quality and Export & Reporting tabs. It
  writes every matching row (not only the 500 shown) and records the export in the
  export log.
- Text normalization with preview (`app/data/normalize.py`). Values that differ only in
  case or spacing are grouped per categorical-looking text column (at most 200 distinct
  values, fewer than half the rows, not an identifier or `order_id`, not mostly numbers).
  The canonical form is trimmed with single internal spaces, in the letter case of the
  most frequent variant (ties: alphabetical after trimming). Values with stray spaces are
  cleaned even when they have no other spelling ("Whitespace only"). There is no fuzzy
  matching: "Deutschland" and "Germany" are never merged.
- **Normalize text...** button and dialog on the Data Quality tab, showing each column's
  groups (variant, rows, result). Nothing is applied until Apply is clicked; KPIs on screen
  are recalculated. **Reset working copy to original** undoes it, the before/after table
  shows the distinct-value count per normalized column, and the data-state text (source
  line, `package_info.txt`) lists the normalized columns.
- Quality check "Text variants: <column>" (rule type `text_variants`, severity warning,
  not part of the quality score) in the default rule set. It appears in the failed-rows
  dropdown and lists the rows normalization would change.
- Header guessing in the column mapping dialog for German, French and Dutch headers
  (e.g. Bestellnummer, Menge, Einzelpreis; Numéro de commande, Quantité, Prix unitaire;
  Bestelnummer, Aantal, Prijs), next to the English keywords. Accents and case are
  ignored ("Stückzahl" matches "stuckzahl"), each file column is still used once, and
  unit_cost is now guessed before unit_price so "Einkaufspreis" is never taken as the
  selling price. Unmatched fields stay open in the dialog for the user to pick.
- Synthetic German webshop export `sample_data/messy webshop export de.csv` (made-up
  data, no real business or personal information).

### Changed

- Numeric statistics on the Import & Preview tab include columns where at least 80% of the
  non-empty values are numbers (same parser as the quality checks). They are computed on
  the values that parse, using import-converted values; new `non_numeric` and `note`
  columns show how many values were ignored. The data itself is not changed.
- `remove_exact_duplicates()` now keeps the original index instead of renumbering the rows,
  so rows keep their source line numbers.
- `load_file()` accepts optional `delimiter` and `encoding` overrides and records the ones
  used, plus the source line number of every row (`LoadResult.source_rows`).
- `change_summary()` can compare distinct-value counts for chosen columns.
- The default rule set has 25 rules (the `text_variants` rule was added).

### Fixed

- The `note` column of the numeric statistics table was cut off; columns are now sized
  to their contents.
- With a column mapping, the Before / after table listed each check twice (file headers on
  the original side, app field names on the working side), leaving the working and change
  cells blank. Each row now has one label built from the app field name, with the file
  header in parentheses when it differs (e.g. "Missing values: order_id (Bestellnummer)"),
  and the original value is computed through the mapping.

## [0.2.0] - 2026-10-10

### Added

- Rule-based validation engine (`app/data/rules.py`). Quality checks are read from a
  JSON rule set instead of being hard-coded, so any tabular data (HR, finance, ...) can be
  validated. JSON was chosen over YAML to avoid adding a dependency.
- Rule types: `not_null`, `dtype` (numeric / date), `range`, `allowed_values`, `regex`,
  `unique` (on chosen key columns; default is exact match on every column), `cross_column`
  (e.g. `unit_price >= unit_cost`, `ship_date >= order_date`) and `outlier`.
- Each rule has a severity (`error` or `warning`). `validate()` also returns one boolean
  flag per rule and row, showing which rows failed which rule.
- Default rule set `rules/retail_default.json`, reproducing every previous retail check
  with identical counts. It adds a `unit_price below unit_cost` warning and outlier
  warnings for quantity and unit_price (IQR with a 3.0x multiplier, since retail prices
  are right-skewed; the reasoning is in the rule file's `comment` field).
- Default rule `Duplicate order_id` (unique on `order_id`, severity error). It catches an
  order_id repeated with different values (a broken export or bad join), which whole-row
  duplicate detection cannot. Exact duplicate rows are counted by it too.
- Optional `comment` field on rules, for notes that are not shown in the app.
- Outlier detection with the IQR (configurable `factor`) and z-score methods. Outliers are
  counted and flagged as warnings only, never removed or excluded from KPIs.
- Data quality score: completeness, validity, uniqueness and consistency as percentages,
  plus an overall score from 0 to 100. The formula is documented in `quality_score()`.
- Data Quality tab: compact scorecard row, **Load rule set...** button, and a severity
  column in the quality summary table.
- Column mapping dialog ("Map Your Columns") for files whose headers do not match the
  expected fields, with best-guess preselection. Only the working copy is renamed, and
  unmapped source columns are kept.
- Mapped files without `discount_pct` use 0 for it, and this is stated in the KPI note.
  Without `order_date`, the monthly trend shows n/a.
- This changelog.

### Changed

- `run_quality_checks()` and `change_summary()` run on the rule engine. They take an
  optional rule set and add a `severity` column to the quality summary.
- After the column mapping is confirmed, KPI queries run automatically.
- The source line shows "KPIs: same as working data" instead of repeating the
  data-state text.
- The quality report export uses the active rule set.
- `build_windows.bat` bundles the `rules/` folder, and the exe smoke test reports the
  number of rules loaded.

### Fixed

- Without a mapped `unit_cost`, gross profit and gross margin are shown as n/a instead
  of being computed with zero cost. Net revenue and units sold still compute.

### Removed

- The hard-coded check helpers in `app/data/cleaner.py` (`count_invalid_dates`,
  `count_invalid_numeric`, `count_discount_out_of_range`, `count_negative`), now covered by
  the default rule set.

## [0.1.0] - 2026-10-10

### Added

- First release: CSV/Excel import and preview, data quality checks, exact duplicate removal
  on a working copy, DuckDB KPI queries and charts, CSV/Excel exports, Power BI package,
  and a Windows build script.
