# Power BI guide — DataBizex sales package

This guide builds a small sales report from `sales_clean.csv`.
DataBizex does **not** create a `.pbix` file; you build the report yourself
in Power BI Desktop (free, Windows) in about 15 minutes.

`sales_clean.csv` contains only rows that passed the DataBizex quality rules
(valid, non-negative quantity, price and cost; discount 0–100).
`net_revenue` and `gross_profit` are already calculated per row.
See `package_info.txt` for how many rows were exported and excluded.

## 1. Load the CSV

1. Open Power BI Desktop → **Home → Get data → Text/CSV**.
2. Select `sales_clean.csv` → **Transform Data**.
3. Check the column types in Power Query:

   | Column | Type |
   |---|---|
   | order_date | Date |
   | year, month, quantity | Whole number |
   | unit_price, discount_pct, unit_cost, net_revenue, gross_profit | Decimal number |
   | order_id, country, product, category | Text |

4. **Close & Apply**. Rename the table to `Sales` if needed.

Rows with an invalid date in the source have an empty `order_date`.
They count in totals but not in the monthly trend (same rule as in DataBizex).

## 2. Create measures

**Modeling → New measure**, one per line:

```DAX
Net Revenue = SUM ( Sales[net_revenue] )
Gross Profit = SUM ( Sales[gross_profit] )
Units Sold = SUM ( Sales[quantity] )
Gross Margin % = DIVIDE ( [Gross Profit], [Net Revenue] )
Order Lines = COUNTROWS ( Sales )
```

Format `Net Revenue` and `Gross Profit` as currency (€) and
`Gross Margin %` as a percentage.

## 3. KPI cards

1. Add a **Card** visual for each of: `Net Revenue`, `Units Sold`,
   `Gross Profit`, `Gross Margin %`.
2. Place them in a row at the top of the page.
3. Compare the values with the KPI cards in DataBizex (SQL & KPIs tab).
   They should match when the same working data was exported.

## 4. Country and category chart

1. Add a **Clustered bar chart**.
2. Y-axis: `country`. X-axis: `Net Revenue`.
3. Sort by `Net Revenue` descending (… menu → Sort axis).
4. Duplicate the visual and replace `country` with `category`,
   or add `category` as a **Slicer** to filter the country chart.

## 5. Monthly revenue trend

1. Add a **Line chart**.
2. X-axis: `order_date` → use the date hierarchy and keep **Year** and **Month**
   (or use the `year` and `month` columns).
3. Y-axis: `Net Revenue`.
4. Filter out blank `order_date` values in the visual's filter pane
   so rows without a valid date do not appear as "(Blank)".

## 6. Save and refresh

- Save the report as a `.pbix` file in the same folder.
- When DataBizex produces a new package, overwrite `sales_clean.csv`
  and click **Home → Refresh**.

## Assumptions

- Amounts are in one currency (EUR in the sample data); no conversion.
- No returns, refunds or taxes are modelled.
- Gross profit is an estimate: net revenue minus quantity × unit cost.
