"""Small fixtures with results that can be checked by hand."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

# Hand-verified KPI fixture (net = qty * price * (1 - disc/100); profit = net - qty * cost):
#   A1 Germany Chair Furniture  2 x 100, 10% -> net 180, cost 80, profit 100   (2024-01)
#   A2 France  Lamp  Lighting   1 x 50,   0% -> net  50, cost 20, profit  30   (2024-01)
#   A3 Germany Lamp  Lighting   4 x 50,  50% -> net 100, cost 80, profit  20   (2024-02)
#   A4 France  Chair Furniture -1 x 100       -> EXCLUDED (negative quantity)
#   A5 Spain   Mug   Kitchen    3 x 10,   0% -> net  30, cost 12, profit  18   (invalid date)
# Totals: net 360, units 10, profit 168, margin 168/360 = 0.4667
KPI_CSV = """order_id,order_date,country,product,category,quantity,unit_price,discount_pct,unit_cost
A1,2024-01-15,Germany,Chair,Furniture,2,100,10,40
A2,2024-01-20,France,Lamp,Lighting,1,50,0,20
A3,2024-02-03,Germany,Lamp,Lighting,4,50,50,20
A4,2024-02-10,France,Chair,Furniture,-1,100,0,40
A5,not-a-date,Spain,Mug,Kitchen,3,10,0,4
"""

# Quality fixture: 5 rows.
#   2 exact duplicates of B1, 2 missing cells (B2 order_date, B2 quantity),
#   B3: invalid date, non-numeric quantity, discount 150, negative unit_cost.
QUALITY_CSV = """order_id,order_date,country,quantity,unit_price,discount_pct,unit_cost
B1,2024-03-01,Italy,1,10,0,5
B1,2024-03-01,Italy,1,10,0,5
B2,,Spain,,20,5,8
B3,2024-99-01,France,abc,20,150,-3
B1,2024-03-01,Italy,1,10,0,5
"""


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def kpi_csv(tmp_path: Path) -> Path:
    return _write(tmp_path, "kpi.csv", KPI_CSV)


@pytest.fixture
def quality_csv(tmp_path: Path) -> Path:
    return _write(tmp_path, "quality.csv", QUALITY_CSV)


@pytest.fixture
def kpi_df(kpi_csv: Path) -> pd.DataFrame:
    from app.data.loader import load_file

    return load_file(kpi_csv).df


@pytest.fixture
def quality_df(quality_csv: Path) -> pd.DataFrame:
    from app.data.loader import load_file

    return load_file(quality_csv).df
