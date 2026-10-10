# Data dictionary — `sales_clean.csv`

One row per order line. Produced by DataBizex from the working dataset.
Only rows valid for KPIs are included (see "Row rules" below).

| Column | Type | Description |
|---|---|---|
| order_id | text | Order identifier from the source file. |
| order_date | date (YYYY-MM-DD) | Order date. Empty when the source date was missing or invalid. |
| year | integer | Year of `order_date`. Empty when the date is empty. |
| month | integer (1–12) | Month of `order_date`. Empty when the date is empty. |
| country | text | Sales country. Empty when missing in the source. |
| product | text | Product name. Empty when missing in the source. |
| category | text | Product category. |
| quantity | number | Units sold on the line. ≥ 0. |
| unit_price | number | List price per unit before discount (EUR). ≥ 0. |
| discount_pct | number | Discount in percent, 0–100. |
| unit_cost | number | Cost per unit (EUR). ≥ 0. |
| net_revenue | number | `quantity × unit_price × (1 − discount_pct / 100)`, rounded to 2 decimals. |
| gross_profit | number | `net_revenue − quantity × unit_cost`, rounded to 2 decimals. |

## Row rules

A source row is exported only when `quantity`, `unit_price`, `discount_pct`
and `unit_cost` are all present, numeric, not negative, and `discount_pct`
is between 0 and 100. Rows that fail are excluded, never repaired or set
to zero. The number of excluded rows is in `package_info.txt`.

Text columns are kept as they are (missing values stay empty).

## Measures (defined in Power BI, see POWER_BI_GUIDE.md)

| Measure | Definition |
|---|---|
| Net Revenue | Sum of `net_revenue` |
| Units Sold | Sum of `quantity` |
| Gross Profit | Sum of `gross_profit` |
| Gross Margin % | Gross Profit ÷ Net Revenue (blank when Net Revenue = 0) |

## Assumptions

Single currency, no returns, refunds or taxes. Gross profit is an estimate
based on unit cost only (no shipping, overheads or other costs).
