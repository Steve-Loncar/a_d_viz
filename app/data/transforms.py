from __future__ import annotations

import json
import re
from typing import Any

import numpy as np
import pandas as pd


FY_START = 2015
FY_END = 2025


def safe_num(x: Any) -> float:
    """
    Coerce to float safely. Returns np.nan if not parseable.
    """
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
    """
    Derive parent_path and parent_node_id from the `path` column.
    Does not require `parent_id` to be present in source data.
    """
    df = nodes.copy()
    df["path"] = df["path"].astype(str)

    # parent_path: remove last " > segment"
    def parent_path(p: str) -> str:
        if " > " not in p:
            return ""
        return p.rsplit(" > ", 1)[0]

    df["parent_path"] = df["path"].map(parent_path)

    # Map path -> node_id for lookup
    path_to_id = dict(zip(df["path"].astype(str), df["node_id"].astype(str)))
    df["parent_node_id"] = df["parent_path"].map(lambda p: path_to_id.get(str(p), ""))

    # Depth: count separators
    df["depth"] = df["path"].map(lambda p: 0 if not p else p.count(" > "))

    return df


def clean_players(players: pd.DataFrame) -> pd.DataFrame:
    """
    Players sheet often contains many empty template rows.
    Keep only rows with node_id and rank (and ideally name).
    """
    if players is None or players.empty:
        return pd.DataFrame()

    df = players.copy()
    # Normalise column names a bit
    for c in ["node_id", "rank", "name"]:
        if c in df.columns:
            df[c] = df[c].where(~df[c].isna(), None)

    # Drop fully empty template rows
    if "node_id" in df.columns:
        df = df[df["node_id"].notna()]
    if "rank" in df.columns:
        df = df[df["rank"].notna()]

    # Optional: if name exists, prefer non-empty
    if "name" in df.columns:
        df = df[df["name"].astype(str).str.strip().ne("")]

    # Coerce numerics if columns exist
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
        df["name"] = df["name"].astype(str)
    num_cols = [c for c in df.columns if re.search(r"(fy\d{2}.*_(usd_bn|pct))$", c)]
    for c in num_cols:
        df[c] = df[c].map(safe_num)
    return df.reset_index(drop=True)


def make_series_long(nodes: pd.DataFrame) -> pd.DataFrame:
    """
    Convert wide FY columns in Nodes into a long series table:
    node_id | path | metric_type | fiscal_year | value
    """
    if nodes is None or nodes.empty:
        return pd.DataFrame(columns=["node_id", "path", "metric_type", "fiscal_year", "value"])

    df = nodes.copy()
    base_cols = ["node_id", "path", "display_name"]
    base_cols = [c for c in base_cols if c in df.columns]

    rows = []
    for fy in range(FY_START, FY_END + 1):
        rev_col = f"segment_fy{fy}_revenue_usd_bn"
        ebitda_col = f"segment_fy{fy}_ebitda_usd_bn"
        m_col = f"segment_fy{fy}_ebitda_margin_pct"

        for metric_type, col in [
            ("revenue", rev_col),
            ("ebitda", ebitda_col),
            ("margin", m_col),
        ]:
            if col not in df.columns:
                continue
            tmp = df[base_cols + [col]].copy()
            tmp["metric_type"] = metric_type
            tmp["fiscal_year"] = fy
            tmp["value"] = tmp[col].map(safe_num)
            tmp = tmp.drop(columns=[col])
            rows.append(tmp)

    if not rows:
        return pd.DataFrame(columns=["node_id", "path", "display_name", "metric_type", "fiscal_year", "value"])

    out = pd.concat(rows, ignore_index=True)
    out["node_id"] = out["node_id"].astype(str)
    out["path"] = out["path"].astype(str)
    return out
