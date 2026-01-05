from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Optional, Tuple

import pandas as pd
import streamlit as st
import plotly.express as px

from .data.loader import load_workbook_bytes, load_workbook_path
from .data.transforms import (
    clean_players,
    clean_proxies,
    derive_hierarchy,
    make_series_long,
    safe_num,
)


@dataclass(frozen=True)
class AppData:
    nodes: pd.DataFrame
    players: pd.DataFrame
    proxies: pd.DataFrame
    evidence: pd.DataFrame
    evidence_map: pd.DataFrame
    echo_results: pd.DataFrame
    series_long: pd.DataFrame


st.set_page_config(
    page_title="A&D Market Explorer (v2)",
    layout="wide",
)


def _default_data_path() -> str:
    return "data/a_d_database.xlsx"


@st.cache_data(show_spinner=True)
def _load_all_from_path(path: str) -> AppData:
    wb = load_workbook_path(path)
    return _postprocess(wb)


@st.cache_data(show_spinner=True)
def _load_all_from_bytes(xlsx_bytes: bytes) -> AppData:
    wb = load_workbook_bytes(xlsx_bytes)
    return _postprocess(wb)


def _postprocess(wb: dict[str, pd.DataFrame]) -> AppData:
    nodes = wb.get("Nodes", pd.DataFrame()).copy()
    players = wb.get("Players", pd.DataFrame()).copy()
    proxies = wb.get("Proxies", pd.DataFrame()).copy()
    evidence = wb.get("Evidence", pd.DataFrame()).copy()
    evidence_map = wb.get("Evidence_Map", pd.DataFrame()).copy()
    echo_results = wb.get("Echo_Results", pd.DataFrame()).copy()

    # Minimal required columns sanity
    for col in ["node_id", "path", "display_name"]:
        if col not in nodes.columns:
            raise ValueError(f"Nodes sheet missing required column: {col}")

    nodes = derive_hierarchy(nodes)
    players = clean_players(players)
    proxies = clean_proxies(proxies)

    series_long = make_series_long(nodes)

    return AppData(
        nodes=nodes,
        players=players,
        proxies=proxies,
        evidence=evidence,
        evidence_map=evidence_map,
        echo_results=echo_results,
        series_long=series_long,
    )


def _select_dataset() -> Tuple[AppData, str]:
    st.sidebar.header("Dataset")
    uploaded = st.sidebar.file_uploader(
        "Upload workbook (.xlsx)",
        type=["xlsx"],
        help="Use your structured output workbook (Nodes/Players/Proxies/Evidence/Evidence_Map/Echo_Results).",
    )

    if uploaded is not None:
        xlsx_bytes = uploaded.read()
        data = _load_all_from_bytes(xlsx_bytes)
        label = f"Uploaded: {uploaded.name}"
        return data, label

    path = st.sidebar.text_input("Or local path", value=_default_data_path())
    data = _load_all_from_path(path)
    label = f"Local: {path}"
    return data, label


def _taxonomy_selector(nodes: pd.DataFrame) -> pd.Series:
    st.sidebar.header("Taxonomy")

    query = st.sidebar.text_input("Search node", value="")
    nodes_view = nodes
    if query.strip():
        q = query.strip().lower()
        nodes_view = nodes[nodes["display_name"].astype(str).str.lower().str.contains(q)]

    # Build a nice label that makes duplicates less confusing
    labels = (
        nodes_view["display_name"].astype(str)
        + "  ·  "
        + nodes_view["path"].astype(str)
    )
    # Use index-based selection to avoid duplicate display_name collisions
    options = list(nodes_view.index)
    format_func = lambda idx: labels.loc[idx]  # noqa: E731

    if not options:
        st.sidebar.warning("No nodes match your search.")
        return nodes.iloc[0]

    selected_idx = st.sidebar.selectbox(
        "Select node",
        options=options,
        format_func=format_func,
        index=0,
    )
    return nodes.loc[selected_idx]


def _kpi_row(node_row: pd.Series) -> None:
    fy = 2025
    rev = safe_num(node_row.get(f"segment_fy{fy}_revenue_usd_bn"))
    ebitda = safe_num(node_row.get(f"segment_fy{fy}_ebitda_usd_bn"))
    margin = safe_num(node_row.get(f"segment_fy{fy}_ebitda_margin_pct"))

    c1, c2, c3 = st.columns(3)
    c1.metric(f"FY{fy} Revenue (USD bn)", f"{rev:,.3g}" if pd.notna(rev) else "—")
    c2.metric(f"FY{fy} EBITDA (USD bn)", f"{ebitda:,.3g}" if pd.notna(ebitda) else "—")
    c3.metric(f"FY{fy} EBITDA Margin (%)", f"{margin:,.3g}" if pd.notna(margin) else "—")


def _series_chart(series_long: pd.DataFrame, node_id: str) -> None:
    df = series_long[series_long["node_id"] == node_id].copy()
    if df.empty:
        st.info("No time series found for this node.")
        return

    metric = st.radio(
        "Metric",
        options=["revenue", "ebitda", "margin"],
        horizontal=True,
    )
    dff = df[df["metric_type"] == metric].copy()
    y_title = {
        "revenue": "Revenue (USD bn)",
        "ebitda": "EBITDA (USD bn)",
        "margin": "EBITDA Margin (%)",
    }[metric]

    fig = px.line(
        dff,
        x="fiscal_year",
        y="value",
        markers=True,
        title=y_title,
    )
    fig.update_layout(margin=dict(l=10, r=10, t=50, b=10))
    st.plotly_chart(fig, use_container_width=True)


def _node_summary(node_row: pd.Series) -> None:
    st.subheader("Summary")
    st.caption(node_row.get("path", ""))

    scope = str(node_row.get("scope_context", "") or "").strip()
    method = str(node_row.get("methodology_summary", "") or "").strip()
    fin = str(node_row.get("financial_commentary", "") or "").strip()

    if scope:
        st.markdown("**Scope**")
        st.write(scope)
    if fin:
        st.markdown("**Financial commentary**")
        st.write(fin)
    if method:
        st.markdown("**Methodology**")
        st.write(method)


def main() -> None:
    data, ds_label = _select_dataset()
    nodes = data.nodes

    st.title("A&D Market Explorer (v2)")
    st.caption(ds_label)

    node_row = _taxonomy_selector(nodes)
    node_id = str(node_row["node_id"])

    tab_overview, tab_dashboard = st.tabs(["Overview", "Node Dashboard"])

    with tab_overview:
        st.subheader(node_row.get("display_name", ""))
        _kpi_row(node_row)
        _node_summary(node_row)

    with tab_dashboard:
        st.subheader(node_row.get("display_name", ""))
        _kpi_row(node_row)
        _series_chart(data.series_long, node_id=node_id)


if __name__ == "__main__":
    main()
