# Changelog

All notable changes to DataBizex are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Detection of regional number formats per column: decimal comma (`43,92`), dot or other
  thousands separators (`1.234,56`) and US-style thousands commas (`1,234.56`).
- Detection of date formats per column: `YYYY-MM-DD`, `DD.MM.YYYY`, `DD/MM/YYYY` and
  `MM/DD/YYYY`. Ambiguous day/month order is read as DD/MM and flagged.
- **Import Options** dialog after loading a file whose delimiter, encoding, number or date
  format is not the default. It states what was detected, and every setting can be
  overridden. Changing the delimiter or encoding reads the file again.
- Numbers and dates are converted in the working copy only (`app/data/formats.py`). Values
  that do not fit the chosen format keep their original text, so quality checks still
  report them.
- Import settings and converted columns are shown in the source line and written to the
  Power BI `package_info.txt`.

### Changed

- `load_file()` accepts optional `delimiter` and `encoding` overrides and records which
  ones were used.

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
