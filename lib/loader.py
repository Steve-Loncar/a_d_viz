from __future__ import annotations

import pandas as pd

def load_workbook_path(path: str) -> dict[str, pd.DataFrame]:
    xls = pd.ExcelFile(path, engine="openpyxl")
    return {name: pd.read_excel(xls, sheet_name=name, engine="openpyxl") for name in xls.sheet_names}

def load_workbook_bytes(xlsx_bytes: bytes) -> dict[str, pd.DataFrame]:
    xls = pd.ExcelFile(xlsx_bytes, engine="openpyxl")
    return {name: pd.read_excel(xls, sheet_name=name, engine="openpyxl") for name in xls.sheet_names}
