# Messy multi-table sample (synthetic)

Five related retail tables with deliberately planted data problems, for trying the
DataBizex quality checks and guided fixes on something closer to a real export.
**All data is synthetic**: names, e-mail addresses, ids and amounts are generated and
belong to no real person or company.

Open any file from the app with **File > Open sample dataset**, or click **Try sample
data** on the empty Import & Preview tab (it opens the sales table). The files load
through the normal import pipeline, so the import options, column mapping and quality
checks behave exactly as for your own files.

## Files and relations

| File | Rows | Key | Points to |
|---|---:|---|---|
| `retail_messy_1_stores.xlsx` | 29 | store_id | |
| `retail_messy_2_customers.xlsx` | 1,020 | customer_id | |
| `retail_messy_3_products.xlsx` | 123 | product_id | |
| `retail_messy_4_sales.xlsx` | 3,030 | order_id | store_id -> stores, customer_id -> customers, product_id -> products |
| `retail_messy_4_sales.csv` | 3,030 | order_id | the same sales data as CSV |
| `retail_messy_5_returns.xlsx` | 303 | return_id | order_id -> sales |

`manifest.json` is the answer key: the generator arguments, row counts and the exact
number of rows given each planted problem.

How to read the counts:

- Counts are measured **before** duplicate rows are appended. Duplicates copy existing
  rows, which may carry problems, so a file can contain slightly more of a problem than
  counted (at most the number of duplicate rows).
- Problems on the same column never share a row, so each count is exact for its column.
  Problems on **different** columns can overlap on the same row.

## Planted problems

### Sales (3,000 rows + 30 duplicates)

| Problem | Rows | Flagged by the default rules |
|---|---:|---|
| negative quantity (-1) | 60 | Negative values: quantity (61, one duplicated copy) |
| zero quantity | 30 | not flagged: 0 passes the ">= 0" rule |
| missing quantity | 45 | Missing values: quantity (46) |
| zero unit_price | 30 | not flagged: 0 passes the ">= 0" rule |
| missing unit_price | 30 | Missing values: unit_price (30) |
| unit_price x100 outlier | 9 | Outliers (IQR): unit_price (warning; also flags other high prices) |
| discount outside 0-100 (110, 150, -10) | 30 | discount_pct outside 0-100 (30) |
| missing discount | 30 | Missing values: discount_pct (31) |
| order_date as DD/MM/YYYY text | 60 | Invalid dates: order_date (60) |
| future order_date (2027-03-15) | 15 | not flagged: no default rule checks for future dates |
| missing order_date | 30 | Missing values: order_date (30) |
| product_id in lower case (join breaks) | 30 | Text variants: product_id (31) |
| orphan product_id (P9999) | 30 | not flagged: the rules check one table at a time |
| customer_id with a leading space (join breaks) | 30 | not flagged |
| orphan customer_id (C999999) | 30 | not flagged |
| orphan store_id (S99) | 15 | not flagged |
| payment_method case/space variants | 60 | Text variants: payment_method (60 in the .xlsx) |
| missing channel | 30 | Missing values: channel (30) |
| exact duplicate rows | 30 | Exact duplicate rows (30), Duplicate order_id (30) |

The CSV reader trims spaces at the start of a value, so in `retail_messy_4_sales.csv` the
customer ids with a leading space and the payment methods such as `" cash"` arrive
trimmed (Text variants: payment_method shows 57 there). The `.xlsx` keeps them.

### Stores (27 rows + 2 duplicates)

city spelling variants 2, missing region 2, negative area_sqm 1.

### Customers (1,000 rows + 20 duplicates)

missing email 60, email without @ 10, implausible birth_year (1850, 1900, 2030, 0) 4,
loyalty_tier case/space variants 80, signup_date as DD/MM/YYYY text 50, city variants 32,
upper-case names 30, names padded with spaces 20.

### Products (120 rows + 3 duplicates)

category spelling/case variants 14, list_price below unit_cost 3, list_price = 0 1,
missing unit_cost 2, missing brand 4, status case variants 6.

### Returns (300 rows + 3 duplicates)

reason variants 14, return_date before the order date 6, negative refund_amount 6,
missing refund_amount 6, orphan order_id (O99999999) 4.

## Regenerate

The generator is seeded: the same arguments give the same data on every run.

```bat
python scripts\make_sample_data.py sample_data\messy --customers 1000 --products 120 ^
    --sales-rows 3000 --returns 300 --large-rows 0 --sales-csv
```

For a large file to test performance (not committed; `sample_data/large/` and
`*.large.*` files are ignored by git):

```bat
python scripts\make_sample_data.py sample_data\large --large-rows 500000
```

This writes the five tables at their default sizes plus `retail_messy_6_sales.large.csv`
(505,000 rows, about 30 MB).
