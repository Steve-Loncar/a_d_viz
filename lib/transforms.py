from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

FY_START = 2015
FY_END = 2025

def safe_num(x: Any) -> float:
    if x is None:
        return np.nan
    if isinstance(x, (int, float, np.number)) and not pd.isna(x):
        return float(x)
    try:
        s = str(x).strip()
        if s == "" or s.lower() in {"nan", "none", "null"}:
            return np.nan
        return float(s)
    except Exception:
        return np.nan

def derive_hierarchy(nodes: pd.DataFrame) -> pd.DataFrame:
    df = nodes.copy()
    df["path"] = df["path"].astype(str)

    def parent_path(p: str) -> str:
        return "" if " > " not in p else p.rsplit(" > ", 1)[0]

    df["parent_path"] = df["path"].map(parent_path)
    path_to_id = dict(zip(df["path"].astype(str), df["node_id"].astype(str)))
    df["parent_node_id"] = df["parent_path"].map(lambda p: path_to_id.get(str(p), ""))
    df["depth"] = df["path"].map(lambda p: 0 if not p else p.count(" > "))
    return df

def clean_players(players: pd.DataFrame) -> pd.DataFrame:
    if players is None or players.empty:
        return pd.DataFrame()
    df = players.copy()
    if "node_id" in df.columns:
        df = df[df["node_id"].notna()]
    if "rank" in df.columns:
        df = df[df["rank"].notna()]
    if "name" in df.columns:
        df = df[df["name"].astype(str).str.strip().ne("")]
    num_cols = [c for c in df.columns if re.search(r"(fy\d{2}.*_(usd_bn|pct))$", c)]
    for c in num_cols:
        df[c] = df[c].map(safe_num)
    return df.reset_index(drop=True)

def clean_proxies(proxies: pd.DataFrame) -> pd.DataFrame:
    if proxies is None or proxies.empty:
        return pd.DataFrame()
    df = proxies.copy()
    if "node_id" in df.columns:
        df = df[df["node_id"].notna()]
    if "name" in df.columns:
        df = df[df["name"].astype(str).str.strip().ne("")]
    num_cols = [c for c in df.columns if re.search(r"(fy\d{2}.*_(usd_bn|pct))$", c)]
    for c in num_cols:
        df[c] = df[c].map(safe_num)
    return df.reset_index(drop=True)

def make_series_long(nodes: pd.DataFrame) -> pd.DataFrame:
    expected_cols = ["node_id", "path", "display_name", "metric_type", "fiscal_year", "value"]

    if nodes is None or nodes.empty:
        return pd.DataFrame(columns=expected_cols)

    base_cols = [c for c in ["node_id", "path", "display_name"] if c in nodes.columns]
    rows = []

    for fy in range(FY_START, FY_END + 1):
        candidates = [
            ("revenue", f"segment_fy{fy}_revenue_usd_bn"),
            ("ebitda", f"segment_fy{fy}_ebitda_usd_bn"),

            ("margin", f"segment_fy{fy}_ebitda_margin_pct"),

        ]
