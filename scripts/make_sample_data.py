"""Generate a related, deliberately messy retail dataset for DataBizex.

    python scripts/make_sample_data.py OUTPUT_DIR [--customers 8000] [--products 600]
        [--sales-rows 25000] [--returns 3000] [--large-rows 500000] [--prefix retail_messy]
        [--format xlsx|csv] [--sales-csv]

Five related tables (stores, customers, products, sales, returns) and, unless
--large-rows is 0, one large sales CSV for scale tests (<prefix>_6_sales.large.csv;
the .large. name keeps it out of git). Everything is synthetic and seeded: the same
arguments give the same data on every run. manifest.json, written next to the files,
is the answer key: row counts and the exact number of rows given each planted problem.

Keys: sales.store_id -> stores, sales.customer_id -> customers, sales.product_id ->
products, returns.order_id -> sales. Planted join breakers (lower-case product ids,
customer ids with a leading space) and orphan keys (ids that exist nowhere) are counted
in the manifest.

Counting rules (also stated in manifest.json):
- Problem counts are measured BEFORE duplicate rows are appended; duplicates copy rows
  that may carry problems, so a file can show slightly more of a problem than counted.
- Problems on the same column never share a row, so each count is exact for its column.
  Problems on different columns can overlap on the same row.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("make_sample_data")

SEED = 20261015
START, END = pd.Timestamp("2025-01-01"), pd.Timestamp("2026-09-30")
FUTURE_DATE = "2027-03-15"
CSV_ENCODING = "utf-8-sig"  # like the app's CSV exports, so Excel opens them correctly

CITIES = {
    "Germany": ["Berlin", "Munich", "Hamburg", "Cologne", "Frankfurt"],
    "France": ["Paris", "Lyon", "Marseille", "Toulouse"],
    "Netherlands": ["Amsterdam", "Rotterdam", "Utrecht"],
    "Spain": ["Madrid", "Barcelona", "Valencia"],
    "Italy": ["Milan", "Rome", "Turin"],
}
CITY_VARIANTS = {
    "Munich": ["Munchen", "MUNICH", "munich "], "Cologne": ["Koln", "Köln"],
    "Berlin": ["berlin", "BERLIN", "Berlin "], "Paris": ["paris", "PARIS "],
    "Amsterdam": ["amsterdam", "Amsterdam "], "Barcelona": ["barcelona", "BCN"],
    "Milan": ["Milano", "milan"], "Rome": ["Roma", "rome"],
}
CATEGORIES = {
    "Electronics": ["Headphones", "Speaker", "Charger", "Keyboard", "Webcam"],
    "Home": ["Lamp", "Cushion", "Mug", "Candle", "Frame"],
    "Sports": ["Yoga Mat", "Bottle", "Dumbbell", "Rope", "Bag"],
    "Beauty": ["Cream", "Shampoo", "Perfume", "Soap", "Lotion"],
    "Toys": ["Puzzle", "Robot", "Cards", "Blocks", "Plush"],
    "Office": ["Notebook", "Pen Set", "Stapler", "Folder", "Planner"],
}
CATEGORY_VARIANTS = {"Electronics": ["electronics", "ELECTRONICS", "Electronics "],
                     "Home": ["home", "HOME "], "Sports": ["sports", "Sport"],
                     "Beauty": ["beauty", "BEAUTY"], "Toys": ["toys", "Toy"],
                     "Office": ["office", "Offic"]}
FIRST = ["Anna", "Luca", "Marta", "Jonas", "Sofia", "Pieter", "Elena", "Hugo", "Clara", "Mateo",
         "Nina", "Felix", "Lea", "Omar", "Ines", "Tomas", "Eva", "Paul", "Mia", "Leon"]
LAST = ["Schmidt", "Rossi", "Garcia", "Dubois", "Jansen", "Weber", "Moreau", "Bianchi", "Lopez",
        "Fischer", "Martin", "Visser", "Ferrari", "Romero", "Klein", "Bakker", "Conti", "Vidal"]
PAYMENTS = ["card", "cash", "paypal", "transfer"]
# Spellings of the same payment method: differ only in case or spacing, so the app's
# "Text variants" check can group them with the original value.
PAYMENT_VARIANTS = {"card": ["Card", "CARD", "card "], "cash": ["CASH", "Cash", " cash"],
                    "paypal": ["PayPal", "PAYPAL", "Paypal"], "transfer": ["Transfer", "TRANSFER ", "transfer "]}
CHANNELS = ["store", "online", "app"]
REASONS = ["Damaged", "Wrong size", "Changed mind", "Defective", "Late delivery"]
REASON_VARIANTS = {"Damaged": ["damaged", "DAMAGED", "Damaged "], "Defective": ["defective", "Defektiv"],
                   "Changed mind": ["changed mind", "Change of mind"]}

ORPHAN_PRODUCT, ORPHAN_CUSTOMER, ORPHAN_STORE, ORPHAN_ORDER = "P9999", "C999999", "S99", "O99999999"
TABLES = ["stores", "customers", "products", "sales", "returns"]


class Planter:
    """Chooses the rows that get a planted problem.

    Problems on the same column are drawn from disjoint rows, so one problem never
    overwrites another and every count stays exact for its column.
    """

    def __init__(self, rng: np.random.Generator, n: int) -> None:
        self.rng, self.n = rng, n
        self._used: dict[str, np.ndarray] = {}

    def rows(self, column: str, fraction: float, minimum: int = 1) -> np.ndarray:
        used = self._used.get(column, np.zeros(self.n, dtype=bool))
        free = np.flatnonzero(~used)
        k = min(len(free), max(minimum, int(self.n * fraction))) if self.n else 0
        chosen = self.rng.choice(free, size=k, replace=False) if k else np.array([], dtype=int)
        used = used.copy()
        used[chosen] = True
        self._used[column] = used
        return chosen


def _vary(rng: np.random.Generator, df: pd.DataFrame, rows: np.ndarray, column: str,
          variants: dict[str, list[str]]) -> int:
    """Replace df[column] on rows by a spelling variant of its own value; returns how many changed."""
    changed = 0
    for i in rows:
        options = variants.get(df.at[i, column])
        if options:
            df.at[i, column] = str(rng.choice(options))
            changed += 1
    return changed


def _append_duplicates(df: pd.DataFrame, count: int, random_state: int) -> pd.DataFrame:
    if count <= 0 or df.empty:
        return df
    return pd.concat([df, df.sample(min(count, len(df)), random_state=random_state)], ignore_index=True)


# ---------------------------------------------------------------------- tables
def make_stores(rng: np.random.Generator) -> tuple[pd.DataFrame, dict[str, int]]:
    rows = []
    sid = 1
    for country, cities in CITIES.items():
        for city in cities:
            for _ in range(2 if len(cities) > 3 else 1):
                opened = pd.Timestamp("2015-01-01") + pd.Timedelta(days=int(rng.integers(0, 3500)))
                rows.append({"store_id": f"S{sid:03d}", "store_name": f"{city} Store {sid}",
                             "city": city, "country": country,
                             "region": str(rng.choice(["North", "South", "East", "West", "Central"])),
                             "store_type": str(rng.choice(["Mall", "High street", "Outlet"])),
                             "opened_date": opened.strftime("%Y-%m-%d"),
                             "area_sqm": int(rng.integers(80, 900))})
                sid += 1
    df = pd.DataFrame(rows)
    plant = Planter(rng, len(df))
    problems = {"city spelling variants": _vary(rng, df, plant.rows("city", 0.15), "city", CITY_VARIANTS)}
    rows_ = plant.rows("region", 0.10)
    df.loc[rows_, "region"] = None
    problems["missing region"] = len(rows_)
    rows_ = plant.rows("area_sqm", 0.05)
    df.loc[rows_, "area_sqm"] = -1
    problems["negative area_sqm"] = len(rows_)
    return df, problems


def make_customers(rng: np.random.Generator, n: int) -> tuple[pd.DataFrame, dict[str, int]]:
    first, last = rng.choice(FIRST, n), rng.choice(LAST, n)
    country = rng.choice(list(CITIES), n)
    df = pd.DataFrame({
        "customer_id": [f"C{i:06d}" for i in range(1, n + 1)],
        "full_name": [f"{a} {b}" for a, b in zip(first, last)],
        "email": [f"{a.lower()}.{b.lower()}{i}@example.com" for i, (a, b) in enumerate(zip(first, last))],
        "city": [str(rng.choice(CITIES[c])) for c in country],
        "country": country,
        "signup_date": ((START - pd.Timedelta(days=900)) + pd.to_timedelta(rng.integers(0, 1500, n), unit="D"))
        .strftime("%Y-%m-%d"),
        "birth_year": rng.integers(1950, 2007, n).astype(object),
        "loyalty_tier": rng.choice(["Bronze", "Silver", "Gold"], n, p=[0.6, 0.3, 0.1]),
    })
    plant = Planter(rng, n)
    problems: dict[str, int] = {}
    rows = plant.rows("email", 0.06)
    df.loc[rows, "email"] = None
    problems["missing email"] = len(rows)
    rows = plant.rows("email", 0.01)
    df.loc[rows, "email"] = df.loc[rows, "email"].str.replace("@", "", regex=False)
    problems["email without @"] = len(rows)
    rows = plant.rows("birth_year", 0.004)
    df.loc[rows, "birth_year"] = rng.choice([1850, 1900, 2030, 0], len(rows))
    problems["implausible birth_year (1850, 1900, 2030, 0)"] = len(rows)
    rows = plant.rows("loyalty_tier", 0.08)
    df.loc[rows, "loyalty_tier"] = rng.choice(["gold", "GOLD", "silver ", "BRONZE"], len(rows))
    problems["loyalty_tier case/space variants"] = len(rows)
    rows = plant.rows("signup_date", 0.05)
    df.loc[rows, "signup_date"] = pd.to_datetime(df.loc[rows, "signup_date"]).dt.strftime("%d/%m/%Y")
    problems["signup_date as DD/MM/YYYY text"] = len(rows)
    problems["city variants"] = _vary(rng, df, plant.rows("city", 0.08), "city", CITY_VARIANTS)
    rows = plant.rows("full_name", 0.03)
    df.loc[rows, "full_name"] = df.loc[rows, "full_name"].str.upper()
    problems["upper-case names"] = len(rows)
    rows = plant.rows("full_name", 0.02)
    df.loc[rows, "full_name"] = "  " + df.loc[rows, "full_name"] + "  "
    problems["names padded with spaces"] = len(rows)
    return df, problems


def make_products(rng: np.random.Generator, n: int) -> tuple[pd.DataFrame, dict[str, int]]:
    rows = []
    for i in range(1, n + 1):
        category = str(rng.choice(list(CATEGORIES)))
        cost = round(float(rng.uniform(2, 180)), 2)
        rows.append({"product_id": f"P{i:04d}", "product_name": f"{rng.choice(CATEGORIES[category])} {i}",
                     "category": category, "brand": str(rng.choice(["Nordia", "Veltra", "Orbis", "Kaldo", "Lumio"])),
                     "unit_cost": cost, "list_price": round(cost * float(rng.uniform(1.25, 2.6)), 2),
                     "supplier": str(rng.choice(["Alpha Ltd", "Beta GmbH", "Gamma SA", "Delta BV"])),
                     "status": str(rng.choice(["active", "active", "active", "discontinued"]))})
    df = pd.DataFrame(rows)
    df["unit_cost"] = df["unit_cost"].astype(object)
    df["list_price"] = df["list_price"].astype(object)
    plant = Planter(rng, n)
    problems = {"category spelling/case variants": _vary(rng, df, plant.rows("category", 0.12), "category",
                                                         CATEGORY_VARIANTS)}
    rows_ = plant.rows("list_price", 0.03)
    df.loc[rows_, "list_price"] = [round(float(c) * 0.7, 2) for c in df.loc[rows_, "unit_cost"]]
    problems["list_price below unit_cost"] = len(rows_)
    rows_ = plant.rows("list_price", 0.01)
    df.loc[rows_, "list_price"] = 0
    problems["list_price = 0"] = len(rows_)
    rows_ = plant.rows("unit_cost", 0.02)
    df.loc[rows_, "unit_cost"] = None
    problems["missing unit_cost"] = len(rows_)
    rows_ = plant.rows("brand", 0.04)
    df.loc[rows_, "brand"] = None
    problems["missing brand"] = len(rows_)
    rows_ = plant.rows("status", 0.05)
    df.loc[rows_, "status"] = rng.choice(["Active", "ACTIVE", "inactive"], len(rows_))
    problems["status case variants"] = len(rows_)
    return df, problems


def make_sales(rng: np.random.Generator, n: int, n_customers: int, n_products: int, store_ids,
               first_order: int) -> tuple[pd.DataFrame, dict[str, int]]:
    dates = START + pd.to_timedelta(rng.integers(0, (END - START).days + 1, n), unit="D")
    quantity = rng.choice([1, 1, 1, 2, 2, 3, 4, 5, 10], n)
    price = np.round(rng.lognormal(3.4, 0.7, n).clip(2, 600), 2)
    df = pd.DataFrame({
        "order_id": [f"O{x:08d}" for x in range(first_order, first_order + n)],
        "order_date": dates.strftime("%Y-%m-%d").astype(object),
        "store_id": rng.choice(store_ids, n),
        "customer_id": [f"C{x:06d}" for x in rng.integers(1, n_customers + 1, n)],
        "product_id": [f"P{x:04d}" for x in rng.integers(1, n_products + 1, n)],
        "quantity": quantity.astype(object), "unit_price": price.astype(object),
        "discount_pct": rng.choice([0, 0, 0, 5, 10, 15, 20, 30], n).astype(object),
        "payment_method": rng.choice(PAYMENTS, n),
        "channel": rng.choice(CHANNELS, n, p=[0.55, 0.35, 0.10]),
    })
    plant = Planter(rng, n)
    problems: dict[str, int] = {}

    def set_rows(name: str, column: str, fraction: float, value) -> np.ndarray:
        rows = plant.rows(column, fraction)
        df.loc[rows, column] = value(rows) if callable(value) else value
        problems[name] = len(rows)
        return rows

    set_rows("negative quantity", "quantity", 0.02, -1)
    set_rows("zero quantity", "quantity", 0.01, 0)
    set_rows("missing quantity", "quantity", 0.015, None)
    set_rows("zero unit_price", "unit_price", 0.01, 0)
    set_rows("missing unit_price", "unit_price", 0.01, None)
    set_rows("price outlier x100", "unit_price", 0.003, lambda r: [round(float(x) * 100, 2) for x in price[r]])
    set_rows("discount outside 0-100", "discount_pct", 0.01, lambda r: rng.choice([110, 150, -10], len(r)))
    set_rows("missing discount", "discount_pct", 0.01, None)
    set_rows("date as DD/MM/YYYY", "order_date", 0.02,
             lambda r: pd.to_datetime(df.loc[r, "order_date"]).dt.strftime("%d/%m/%Y"))
    set_rows("future date", "order_date", 0.005, FUTURE_DATE)
    set_rows("missing date", "order_date", 0.01, None)
    set_rows("product_id lower case (join breaks)", "product_id", 0.01, lambda r: df.loc[r, "product_id"].str.lower())
    set_rows("orphan product_id", "product_id", 0.01, ORPHAN_PRODUCT)
    set_rows("customer_id leading space (join breaks)", "customer_id", 0.01,
             lambda r: " " + df.loc[r, "customer_id"])
    set_rows("orphan customer_id", "customer_id", 0.01, ORPHAN_CUSTOMER)
    set_rows("orphan store_id", "store_id", 0.005, ORPHAN_STORE)
    problems["payment_method variants"] = _vary(rng, df, plant.rows("payment_method", 0.02), "payment_method",
                                                PAYMENT_VARIANTS)
    set_rows("missing channel", "channel", 0.01, None)
    return df, problems


def make_returns(rng: np.random.Generator, sales: pd.DataFrame, n: int) -> tuple[pd.DataFrame, dict[str, int]]:
    """Returns of real past orders: sampled from sales rows with a valid ISO date up to END."""
    dates = pd.to_datetime(sales["order_date"], format="%Y-%m-%d", errors="coerce")
    usable = sales[dates.notna() & (dates <= END)]
    if n > len(usable):
        logger.warning("Only %d sales rows can be returned; writing %d returns instead of %d",
                       len(usable), len(usable), n)
        n = len(usable)
    picked = usable.sample(n, random_state=21).reset_index(drop=True)
    order_dates = pd.to_datetime(picked["order_date"], format="%Y-%m-%d")
    df = pd.DataFrame({
        "return_id": [f"R{i:06d}" for i in range(1, n + 1)],
        "order_id": picked["order_id"],
        "product_id": picked["product_id"].str.strip().str.upper(),
        "return_date": (order_dates + pd.to_timedelta(rng.integers(1, 45, n), unit="D")).dt.strftime("%Y-%m-%d"),
        "reason": rng.choice(REASONS, n),
        "refund_amount": np.round(pd.to_numeric(picked["unit_price"], errors="coerce").fillna(20).clip(upper=600)
                                  * rng.uniform(0.5, 1.0, n), 2).astype(object),
    })
    plant = Planter(rng, n)
    problems = {"reason variants": _vary(rng, df, plant.rows("reason", 0.10), "reason", REASON_VARIANTS)}
    rows = plant.rows("return_date", 0.02)
    df.loc[rows, "return_date"] = (order_dates.loc[rows] - pd.Timedelta(days=3)).dt.strftime("%Y-%m-%d")
    problems["return_date before order date"] = len(rows)
    rows = plant.rows("refund_amount", 0.02)
    df.loc[rows, "refund_amount"] = -5
    problems["negative refund_amount"] = len(rows)
    rows = plant.rows("refund_amount", 0.02)
    df.loc[rows, "refund_amount"] = None
    problems["missing refund_amount"] = len(rows)
    rows = plant.rows("order_id", 0.015)
    df.loc[rows, "order_id"] = ORPHAN_ORDER
    problems["orphan order_id"] = len(rows)
    return df, problems


# ---------------------------------------------------------------------- output
def generate(customers: int = 8000, products: int = 600, sales_rows: int = 25_000, returns: int = 3000,
             large_rows: int = 500_000) -> tuple[dict[str, pd.DataFrame], dict]:
    """All tables (plus "sales_large" unless large_rows is 0) and the manifest's per-table section."""
    if min(customers, products, sales_rows) < 1 or returns < 0 or large_rows < 0:
        raise ValueError("customers, products and sales rows must be at least 1; returns and large rows at least 0")
    rng = np.random.default_rng(SEED)
    tables: dict[str, pd.DataFrame] = {}
    info: dict[str, dict] = {}

    def add(name: str, df: pd.DataFrame, problems: dict[str, int], duplicates: int, random_state: int) -> None:
        before = len(df)
        df = _append_duplicates(df, duplicates, random_state)
        if name in ("sales", "sales_large"):
            df = df.sample(frac=1, random_state=13).reset_index(drop=True)  # duplicates are not all at the end
        tables[name] = df
        info[name] = {"rows": len(df), "rows_before_duplicates": before, "duplicate_rows": len(df) - before,
                      "problems": problems}

    stores, problems = make_stores(rng)
    add("stores", stores, problems, 2, 3)
    table, problems = make_customers(rng, customers)
    add("customers", table, problems, int(customers * 0.02), 5)
    table, problems = make_products(rng, products)
    add("products", table, problems, 3, 9)
    store_ids = stores["store_id"].unique()
    sales, problems = make_sales(rng, sales_rows, customers, products, store_ids, 1)
    add("sales", sales, problems, int(sales_rows * 0.01), 11)
    table, problems = make_returns(rng, tables["sales"], returns)
    add("returns", table, problems, int(len(table) * 0.01), 4)
    if large_rows:
        big, problems = make_sales(rng, large_rows, customers, products, store_ids, 1_000_000)
        add("sales_large", big, problems, int(large_rows * 0.01), 11)
    return tables, info


def file_names(prefix: str, fmt: str) -> dict[str, str]:
    names = {table: f"{prefix}_{i}_{table}.{fmt}" for i, table in enumerate(TABLES, start=1)}
    names["sales_large"] = f"{prefix}_6_sales.large.csv"
    return names


def write(out: Path, tables: dict[str, pd.DataFrame], info: dict, arguments: dict, prefix: str = "retail_messy",
          fmt: str = "xlsx", sales_csv: bool = False) -> dict:
    """Write the tables and manifest.json into out; returns the manifest."""
    out.mkdir(parents=True, exist_ok=True)
    names = file_names(prefix, fmt)
    files: dict[str, list[str]] = {}
    for name, df in tables.items():
        if name == "sales_large" or fmt == "csv":
            path = out / names[name]
            df.to_csv(path, index=False, encoding=CSV_ENCODING, lineterminator="\n")
        else:
            path = out / names[name]
            df.to_excel(path, index=False)
        files[name] = [path.name]
        logger.info("Wrote %s (%d rows)", path, len(df))
    if sales_csv and fmt == "xlsx":
        path = out / file_names(prefix, "csv")["sales"]
        tables["sales"].to_csv(path, index=False, encoding=CSV_ENCODING, lineterminator="\n")
        files["sales"].append(path.name)
        logger.info("Wrote %s (%d rows)", path, len(tables["sales"]))
    manifest = {
        "generator": "scripts/make_sample_data.py",
        "seed": SEED,
        "arguments": arguments,
        "synthetic": "All data is synthetic: generated names, ids and amounts, not real customers or sales.",
        "counting_rules": [
            "Problem counts are measured BEFORE duplicate rows are appended. Duplicates copy existing rows, "
            "which may carry problems, so a file can contain slightly more of a problem than counted.",
            "Problems on the same column never share a row, so each count is exact for its column. "
            "Problems on different columns can overlap on the same row.",
        ],
        "relations": {
            "sales.store_id": "stores.store_id", "sales.customer_id": "customers.customer_id",
            "sales.product_id": "products.product_id", "returns.order_id": "sales.order_id",
        },
        "orphan_values": {"product_id": ORPHAN_PRODUCT, "customer_id": ORPHAN_CUSTOMER, "store_id": ORPHAN_STORE,
                          "order_id": ORPHAN_ORDER},
        "tables": {name: {"files": files[name], **details} for name, details in info.items()},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the messy, related DataBizex sample tables.")
    parser.add_argument("out", type=Path, help="output folder (created if needed)")
    parser.add_argument("--customers", type=int, default=8000)
    parser.add_argument("--products", type=int, default=600)
    parser.add_argument("--sales-rows", type=int, default=25_000)
    parser.add_argument("--returns", type=int, default=3000)
    parser.add_argument("--large-rows", type=int, default=500_000, help="0 = no large sales file")
    parser.add_argument("--prefix", default="retail_messy", help="file name prefix")
    parser.add_argument("--format", choices=["xlsx", "csv"], default="xlsx", help="format of the five tables")
    parser.add_argument("--sales-csv", action="store_true", help="with --format xlsx: also write sales as CSV")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    tables, info = generate(args.customers, args.products, args.sales_rows, args.returns, args.large_rows)
    arguments = {"customers": args.customers, "products": args.products, "sales_rows": args.sales_rows,
                 "returns": args.returns, "large_rows": args.large_rows, "prefix": args.prefix,
                 "format": args.format, "sales_csv": args.sales_csv}
    manifest = write(args.out, tables, info, arguments, args.prefix, args.format, args.sales_csv)
    logger.info("Rows: %s", json.dumps({k: v["rows"] for k, v in manifest["tables"].items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
