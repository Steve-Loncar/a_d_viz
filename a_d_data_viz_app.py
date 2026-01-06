from __future__ import annotations

import pandas as pd
import streamlit as st
import plotly.express as px

from lib.loader import load_workbook_bytes, load_workbook_path
from lib.transforms import derive_hierarchy, make_series_long, safe_num, clean_players, clean_proxies

st.set_page_config(page_title="A&D Market Explorer (v2)", layout="wide")

@st.cache_data(show_spinner=True)
def load_all_from_path(path: str) -> dict[str, pd.DataFrame]:
    return load_workbook_path(path)

@st.cache_data(show_spinner=True)
def load_all_from_bytes(xlsx_bytes: bytes) -> dict[str, pd.DataFrame]:
    return load_workbook_bytes(xlsx_bytes)

def postprocess(wb: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    nodes = wb.get("Nodes", pd.DataFrame()).copy()
    players = wb.get("Players", pd.DataFrame()).copy()
    proxies = wb.get("Proxies", pd.DataFrame()).copy()

    for col in ["node_id", "path", "display_name"]:
        if col not in nodes.columns:
            raise ValueError(f"Nodes sheet missing required column: {col}")

    nodes = derive_hierarchy(nodes)
    players = clean_players(players)
    proxies = clean_proxies(proxies)
    series_long = make_series_long(nodes)

    wb_out = dict(wb)
    wb_out["Nodes"] = nodes
    wb_out["Players"] = players
    wb_out["Proxies"] = proxies
    wb_out["SeriesLong"] = series_long
    return wb_out

def main() -> None:
    st.sidebar.header("Dataset")
    uploaded = st.sidebar.file_uploader("Upload workbook (.xlsx)", type=["xlsx"])

    if uploaded is not None:
        wb = load_all_from_bytes(uploaded.read())
        label = f"Uploaded: {uploaded.name}"
    else:
        path = st.sidebar.text_input("Or local path", value="data/a_d_database.xlsx")
        wb = load_all_from_path(path)
        label = f"Local: {path}"

    wb = postprocess(wb)
    nodes = wb["Nodes"]
    series_long = wb["SeriesLong"]

    st.title("A&D Market Explorer (v2)")
    st.caption(label)

    st.sidebar.header("Taxonomy")
    q = st.sidebar.text_input("Search node", value="")
    nodes_view = nodes
    if q.strip():
        nodes_view = nodes[nodes["display_name"].astype(str).str.contains(q, case=False, na=False)]

    if nodes_view.empty:
        st.sidebar.warning("No nodes match your search.")
        st.stop()

    labels = nodes_view["display_name"].astype(str) + "  ·  " + nodes_view["path"].astype(str)
    options = list(nodes_view.index)
    selected_idx = st.sidebar.selectbox("Select node", options=options, format_func=lambda i: labels.loc[i])
    node = nodes.loc[selected_idx]
    node_id = str(node["node_id"])

    def kpis(fy: int = 2025):
        rev = safe_num(node.get(f"segment_fy{fy}_revenue_usd_bn"))
        ebitda = safe_num(node.get(f"segment_fy{fy}_ebitda_usd_bn"))
        margin = safe_num(node.get(f"segment_fy{fy}_ebitda_margin_pct"))
        c1, c2, c3 = st.columns(3)
        c1.metric(f"FY{fy} Revenue (USD bn)", f"{rev:,.3g}" if pd.notna(rev) else "—")
        c2.metric(f"FY{fy} EBITDA (USD bn)", f"{ebitda:,.3g}" if pd.notna(ebitda) else "—")
        c3.metric(f"FY{fy} EBITDA Margin (%)", f"{margin:,.3g}" if pd.notna(margin) else "—")

    tab1, tab2 = st.tabs(["Overview", "Node Dashboard"])

    with tab1:
        st.subheader(node.get("display_name", ""))
        st.caption(node.get("path", ""))
        kpis()
        scope = str(node.get("scope_context", "") or "").strip()
        fin = str(node.get("financial_commentary", "") or "").strip()
        method = str(node.get("methodology_summary", "") or "").strip()
        if scope:
            st.markdown("**Scope**")
            st.write(scope)
        if fin:
            st.markdown("**Financial commentary**")
            st.write(fin)
        if method:
            st.markdown("**Methodology**")
            st.write(method)

    with tab2:
        st.subheader(node.get("display_name", ""))
        kpis()
        df = series_long[series_long["node_id"] == node_id].copy()
        if df.empty:
            st.info("No time series found for this node.")
            return
        metric = st.radio("Metric", ["revenue", "ebitda", "margin"], horizontal=True)
        dff = df[df["metric_type"] == metric]
        title = {"revenue": "Revenue (USD bn)", "ebitda": "EBITDA (USD bn)", "margin": "EBITDA Margin (%)"}[metric]
        fig = px.line(dff, x="fiscal_year", y="value", markers=True, title=title)
        fig.update_layout(margin=dict(l=10, r=10, t=50, b=10))
        st.plotly_chart(fig, use_container_width=True)

if __name__ == "__main__":
    main()
