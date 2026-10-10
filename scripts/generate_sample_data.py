"""Generate the SYNTHETIC retail sales sample files used by DataBizex.

All data is randomly generated with a fixed seed. Product names are generic
and no real businesses, customers or personal data are included.

Outputs (written to sample_data/):
    retail_sales_clean.csv  1,500 valid orders.
    retail_sales_demo.csv   The same orders with deliberately injected issues
                            (counts listed in DEMO_ISSUES below and in
                            sample_data/README.md).

Run from the project root:
    .venv\\Scripts\\python scripts\\generate_sample_data.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

SEED = 20240101
N_ORDERS = 1500
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "sample_data"

COUNTRIES = ["Germany", "France", "Spain", "Italy", "Netherlands", "Belgium"]
COUNTRY_WEIGHTS = [0.26, 0.24, 0.15, 0.15, 0.11, 0.09]

# (product, category, unit_price EUR, unit_cost EUR) - generic, invented names.
PRODUCTS = [
    ("Oak Dining Table", "Furniture", 349.00, 182.00),
    ("Fabric Armchair", "Furniture", 229.00, 118.00),
    ("Office Chair", "Furniture", 149.00, 79.00),
    ("Two-Seat Sofa", "Furniture", 499.00, 265.00),
    ("Bedside Table", "Furniture", 59.00, 27.00),
    ("Floor Lamp", "Lighting", 79.00, 34.00),
    ("Desk Lamp", "Lighting", 29.00, 12.50),
    ("Pendant Light", "Lighting", 49.00, 21.00),
    ("LED Strip 2m", "Lighting", 19.00, 7.80),
    ("Wall Spotlight", "Lighting", 24.00, 10.20),
    ("Chef Knife", "Kitchen", 34.00, 13.00),
    ("Cookware Set", "Kitchen", 119.00, 58.00),
    ("Glass Storage Jars (3)", "Kitchen", 14.00, 5.10),
    ("Ceramic Dinner Plate", "Kitchen", 4.50, 1.60),
    ("Cutting Board", "Kitchen", 12.00, 4.40),
    ("Cotton Duvet Cover", "Textiles", 39.00, 16.50),
    ("Wool Throw", "Textiles", 45.00, 19.00),
    ("Bath Towel", "Textiles", 9.00, 3.30),
    ("Blackout Curtains", "Textiles", 49.00, 20.50),
    ("Floor Rug 160x230", "Textiles", 129.00, 61.00),
    ("Bookcase", "Storage", 89.00, 43.00),
    ("Storage Box Set", "Storage", 19.00, 7.20),
    ("Wardrobe", "Storage", 299.00, 158.00),
    ("Shoe Cabinet", "Storage", 69.00, 32.00),
    ("Wall Shelf", "Storage", 25.00, 10.40),
]

DISCOUNTS = [0, 0, 0, 0, 5, 10, 10, 15, 20, 25]

# Issues injected into the demo file. Every issue hits a different row and no
# duplicated row also carries another issue, so each count below is exact.
DEMO_ISSUES = {
    "exact_duplicate_rows": 25,
    "missing_values": 18,          # 6 country, 4 product, 4 quantity, 4 discount_pct
    "invalid_dates": 8,
    "invalid_numeric_values": 6,   # text in quantity / unit_price / unit_cost
    "discount_out_of_range": 5,
    "negative_values": 6,          # 2 each in quantity, unit_price, unit_cost
}


def build_clean_orders(rng: np.random.Generator) -> pd.DataFrame:
    product_idx = rng.integers(0, len(PRODUCTS), size=N_ORDERS)
    start, end = pd.Timestamp("2024-01-01"), pd.Timestamp("2025-12-31")
    day_offsets = rng.integers(0, (end - start).days + 1, size=N_ORDERS)
    dates = (start + pd.to_timedelta(np.sort(day_offsets), unit="D")).strftime("%Y-%m-%d")

    rows = []
    for i in range(N_ORDERS):
        name, category, price, cost = PRODUCTS[product_idx[i]]
        # Cheaper items sell in larger quantities.
        max_qty = 3 if price > 200 else (6 if price > 50 else 12)
        rows.append(
            {
                "order_id": f"ORD-{i + 1:05d}",
                "order_date": dates[i],
                "country": rng.choice(COUNTRIES, p=COUNTRY_WEIGHTS),
                "product": name,
                "category": category,
                "quantity": int(rng.integers(1, max_qty + 1)),
                "unit_price": price,
                "discount_pct": int(rng.choice(DISCOUNTS)),
                "unit_cost": cost,
            }
        )
    return pd.DataFrame(rows)


def build_demo_orders(clean: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    demo = clean.astype(object).copy()
    n_issue_rows = (
        DEMO_ISSUES["missing_values"]
        + DEMO_ISSUES["invalid_dates"]
        + DEMO_ISSUES["invalid_numeric_values"]
        + DEMO_ISSUES["discount_out_of_range"]
        + DEMO_ISSUES["negative_values"]
    )
    picked = rng.choice(len(demo), size=n_issue_rows + DEMO_ISSUES["exact_duplicate_rows"], replace=False)
    issue_rows = list(picked[:n_issue_rows])
    duplicate_rows = sorted(picked[n_issue_rows:])

    def take(n: int) -> list[int]:
        return [issue_rows.pop() for _ in range(n)]

    # Missing values (left blank in the CSV).
    for col, n in [("country", 6), ("product", 4), ("quantity", 4), ("discount_pct", 4)]:
        demo.loc[take(n), col] = None

    # Invalid dates.
    bad_dates = ["2024-13-45", "31/02/2025", "not a date", "2025-02-30",
                 "TBD", "2024-00-10", "20245-01-01", "yesterday"]
    demo.loc[take(len(bad_dates)), "order_date"] = bad_dates

    # Invalid numeric values (text where a number is expected).
    for col, values in [("quantity", ["two", "3 pcs"]),
                        ("unit_price", ["#REF!", "49,00 EUR"]),
                        ("unit_cost", ["unknown", "-"])]:
        demo.loc[take(len(values)), col] = values

    # Discount outside 0-100.
    demo.loc[take(5), "discount_pct"] = [120, 150, -5, 250, 101]

    # Negative values.
    for col in ["quantity", "unit_price", "unit_cost"]:
        rows = take(2)
        demo.loc[rows, col] = -pd.to_numeric(demo.loc[rows, col])

    # Exact duplicates: copy clean rows and place each copy right after its original.
    dupes = demo.loc[duplicate_rows].copy()
    dupes.index = dupes.index + 0.5
    demo = pd.concat([demo, dupes]).sort_index().reset_index(drop=True)
    return demo


def main() -> None:
    rng = np.random.default_rng(SEED)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    clean = build_clean_orders(rng)
    demo = build_demo_orders(clean, rng)

    clean_path = OUTPUT_DIR / "retail_sales_clean.csv"
    demo_path = OUTPUT_DIR / "retail_sales_demo.csv"
    clean.to_csv(clean_path, index=False)
    demo.to_csv(demo_path, index=False)

    print(f"SYNTHETIC data written (seed {SEED}):")
    print(f"  {clean_path}  rows={len(clean)}")
    print(f"  {demo_path}  rows={len(demo)}")
    for issue, count in DEMO_ISSUES.items():
        print(f"    {issue}: {count}")


if __name__ == "__main__":
    main()
