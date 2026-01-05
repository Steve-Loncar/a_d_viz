from __future__ import annotations

import pandas as pd


def load_workbook_path(path: str) -> dict[str, pd.DataFrame]:
    """
    Load all sheets from an Excel workbook.
    Returns a mapping of sheet_name -> DataFrame.
    """
    xls = pd.ExcelFile(path, engine="openpyxl")
    out: dict[str, pd.DataFrame] = {}
    for name in xls.sheet_names:
        out[name] = pd.read_excel(xls, sheet_name=name, engine="openpyxl")
    return out


def load_workbook_bytes(xlsx_bytes: bytes) -> dict[str, pd.DataFrame]:
    """
    Same as load_workbook_path but reads from in-memory bytes (Streamlit uploader).
    """
    xls = pd.ExcelFile(xlsx_bytes, engine="openpyxl")
    out: dict[str, pd.DataFrame] = {}
    for name in xls.sheet_names:
        out[name] = pd.read_excel(xls, sheet_name=name, engine="openpyxl")
    return out
