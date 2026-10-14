# Changelog

All notable changes to DataBizex are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] - 2026-10-15

### Added

- **Guided fixes.** Select a failed rule on the Data Quality tab and choose **Fix...** (or
  right-click the rule). The dialog explains the problem and offers the actions that fit the
  rule: exclude rows, set missing, map values, fill a constant, trim and standardize case, or
  remove duplicates keeping the first occurrence. A preview shows up to 20 rows before and
  after, the number of rows and cells that will change and the quality score before and after,
  all computed on a copy; nothing changes until **Apply**. Outlier rules stay warnings only.
- **Change log with undo.** Every fix, as well as **Remove exact duplicates** and **Normalize
  text...**, is recorded with its rule, action, parameters, counts, affected source rows and
  sample rows, and the quality score and KPI-valid rows before and after. **Edit > Undo last
  fix** (Ctrl+Z) restores the working copy exactly for the last 10 fixes. **Reset working copy
  to original** asks before discarding logged fixes. The log can be exported as a Markdown
  audit report or CSV, and is added to the Excel export (a "Change log" sheet) and the Power BI
  package (`change_log.csv`, `change_log.md`). It lives in memory for the session.
- **Month-over-month and year-over-year growth** in the monthly revenue query (`MoM %`,
  `YoY %`). Year-over-year compares each month with the same calendar month a year earlier,
  so missing months are never compared with the wrong month.
- **Pareto analysis of products:** a "Top products (Pareto)" query with each product's share
  and cumulative share of net revenue, the smallest set of top products reaching 80%, the line
  "N of M products make 80% of net revenue." and a Pareto chart with an 80% guide.
- **Read-only custom SQL editor** on the SQL & KPIs tab: one `SELECT` or `WITH` query on table
  `sales`, run with **Run** or Ctrl+Enter. A guard rejects anything that could change data
  (more than one statement, `INSERT`, `DELETE`, `DROP`, `ATTACH`, `COPY`, `SET` and similar) with
  a plain-language reason. Up to 1,000 rows are shown with the full row count, and **Export
  result to CSV...** writes every row.
- **Light and dark themes** under **View > Theme** (Light, Dark, or System, the default, which
  follows Windows), applied live including the charts and remembered between sessions.
  Themed quality score bars, KPI cards and tables, readable in both modes.
- **Application icon** for the window, the taskbar and the executable.
- **Layout memory:** the confirmed import settings and column mapping are remembered per file
  layout (the exact column names) and pre-fill the dialogs the next time a file with the same
  columns is opened.
- **File > Open Recent** with the last 10 files; a file that no longer exists is removed from
  the list when clicked.
- **Failed-cell highlighting:** in the failed rows table, the cells that caused each failure
  have a light red background.
- **Messy multi-table sample data** in `sample_data/messy/`: five related synthetic tables
  (stores, customers, products, sales as `.xlsx` and `.csv`, returns) with planted problems and
  an answer key (`manifest.json`, `README.md`). Open them with **File > Open sample dataset**,
  or click **Try messy sample data** on the empty Import & Preview tab. They load through the
  normal import pipeline.
- `scripts/make_sample_data.py`: the seeded generator for this sample, with options for table
  sizes, file names, xlsx or CSV, and a large sales CSV for performance tests (git-ignored).
- **Fix button hints:** when **Fix...** is disabled, its tooltip says why, and a short tip under
  the rule list explains how to start.
- Continuous integration: the test suite runs on every push (GitHub Actions).

### Changed

- **SQL & KPIs tab layout:** separate Predefined queries and Custom SQL views, so each result
  table gets the full height, charts in a scrolling column, and Pareto labels for the top 10
  products only. The window has a minimum size of 1100 x 700.
- **Whole numbers** in float columns (e.g. quantities with empty cells) are shown as `6`
  instead of `6.0` in the preview, failed rows and before/after tables. The data is unchanged.
- **File loading errors** are explained in plain language, with the technical detail on a
  separate line: a row with a different number of columns than the header names the line, a
  file that cannot be read as text suggests re-saving it from Excel, and an Excel file that
  cannot be opened may be corrupted, password-protected or not a real `.xlsx` file.
- **Clearer sample data labels:** the header button and File menu item are now **Load demo
  data** (the single-table `retail_sales_demo.csv`); tooltips explain the difference from the
  messy sample tables.
- Bundled sample files are not added to the recent files list.

### Fixed

- **Status bar messages clear themselves.** "Working" messages such as "Looking for text
  variants..." or "Running your SQL query in DuckDB..." stayed on screen after the operation
  ended, even while other dialogs were open. They are now always removed, whether the
  operation succeeded, failed, was cancelled or found nothing to do. Results clear after 9 s
  and errors after 15 s, as one line without technical tracebacks.

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
