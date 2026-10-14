import pytest

from app.data.loader import DataLoadError, load_file
from app.paths import sample_data_path


def test_load_valid_csv(kpi_csv):
    result = load_file(kpi_csv)
    assert result.df.shape == (5, 9)
    assert list(result.df.columns)[:3] == ["order_id", "order_date", "country"]
    assert result.warnings == []


def test_load_semicolon_csv(tmp_path):
    path = tmp_path / "semi.csv"
    path.write_text("order_id;quantity\nX1;2\nX2;3\n", encoding="utf-8")
    df = load_file(path).df
    assert list(df.columns) == ["order_id", "quantity"]
    assert df["quantity"].sum() == 5


def test_load_cp1252_csv(tmp_path):
    path = tmp_path / "latin.csv"
    path.write_bytes("country,city\nFrance,Orléans\n".encode("cp1252"))
    assert load_file(path).df.loc[0, "city"] == "Orléans"


def test_empty_file_raises(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("", encoding="utf-8")
    with pytest.raises(DataLoadError, match="empty"):
        load_file(path)


def test_header_only_file_raises(tmp_path):
    path = tmp_path / "header.csv"
    path.write_text("order_id,quantity\n", encoding="utf-8")
    with pytest.raises(DataLoadError, match="no data rows"):
        load_file(path)


def test_unsupported_extension_raises(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("hello", encoding="utf-8")
    with pytest.raises(DataLoadError, match="Unsupported"):
        load_file(path)


def test_xls_explains_alternative(tmp_path):
    path = tmp_path / "old.xls"
    path.write_bytes(b"\xd0\xcf\x11\xe0")
    with pytest.raises(DataLoadError, match="xlsx"):
        load_file(path)


def test_corrupt_xlsx_raises(tmp_path):
    path = tmp_path / "broken.xlsx"
    path.write_bytes(b"this is not a zip file")
    with pytest.raises(DataLoadError):
        load_file(path)


def test_fake_xlsx_explains_excel_problem(tmp_path):
    path = tmp_path / "fake.xlsx"
    path.write_text("order_id,quantity\nX1,2\n", encoding="utf-8")
    with pytest.raises(DataLoadError) as info:
        load_file(path)
    message = str(info.value)
    assert message.startswith("The Excel file could not be opened.")
    assert "password-protected" in message
    assert "\nTechnical detail: " in message


def test_undecodable_csv_explains_text_problem(tmp_path):
    path = tmp_path / "binary.csv"
    junk = b"\x81\x8d\x8f\x90\x9d"  # undefined in Windows-1252 and invalid UTF-8
    path.write_bytes(b"order_id,city\nX1," + junk * 4 + b"\nX2," + junk + b"\n")
    with pytest.raises(DataLoadError) as info:
        load_file(path)
    message = str(info.value)
    assert "not readable as text" in message
    assert "Technical detail:" in message


def test_inconsistent_csv_row_names_line(tmp_path):
    path = tmp_path / "ragged.csv"
    path.write_text("order_id,quantity,price\nX1,2,9.5\nX2,3,4.0\nX3,1,2,5,extra\n", encoding="utf-8")
    with pytest.raises(DataLoadError) as info:
        load_file(path, delimiter=",")
    message = str(info.value)
    assert "Line 4 of the file" in message
    assert "different number of columns than the header row" in message
    assert "\nTechnical detail: " in message


def test_missing_file_raises(tmp_path):
    with pytest.raises(DataLoadError, match="not found"):
        load_file(tmp_path / "nope.csv")


def test_headerless_csv_gets_generated_names(tmp_path):
    path = tmp_path / "noheader.csv"
    path.write_text("1,2024-01-01,5.5\n2,2024-01-02,6.5\n", encoding="utf-8")
    result = load_file(path)
    assert list(result.df.columns) == ["column_1", "column_2", "column_3"]
    assert len(result.df) == 2  # first row kept as data
    assert result.warnings


def test_xlsx_roundtrip(tmp_path, kpi_df):
    path = tmp_path / "kpi.xlsx"
    kpi_df.to_excel(path, index=False)
    df = load_file(path).df
    assert df.shape == (5, 9)


def test_bundled_sample_loads():
    df = load_file(sample_data_path()).df
    assert df.shape == (1525, 9)


def test_loading_does_not_modify_source(kpi_csv):
    before = kpi_csv.read_bytes()
    load_file(kpi_csv)
    assert kpi_csv.read_bytes() == before
