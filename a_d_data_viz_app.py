import pandas as pd
import streamlit as st

import plotly.express as px

from lib.loader import load_workbook_bytes, load_workbook_path
from lib.transforms import derive_hierarchy, safe_num, clean_players, clean_proxies

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

    wb_out = dict(wb)
    wb_out["Nodes"] = nodes
    wb_out["Players"] = players
    wb_out["Proxies"] = proxies

    return wb_out

def main() -> None:
    st.sidebar.header("Dataset")

    DEFAULT_PATH = "data/a_d_database.xlsx"

    uploaded = st.sidebar.file_uploader(
        "Optional: upload a different workbook",
        type=["xlsx"],
    )

    if uploaded is not None:
        wb = load_all_from_bytes(uploaded.read())
        label = f"Uploaded: {uploaded.name}"
    else:
        wb = load_all_from_path(DEFAULT_PATH)
        label = f"Default dataset: {DEFAULT_PATH}"

    wb = postprocess(wb)
    nodes = wb["Nodes"]
    players_all = wb["Players"]
    proxies_all = wb["Proxies"]

    st.title("A&D Market Explorer (v2)")
    st.caption(label)

    # ----------------------------
    # Taxonomy selector
    # ----------------------------
    st.sidebar.header("Taxonomy")
    q = st.sidebar.text_input("Search node", value="")

    nodes_view = nodes
    if q.strip():
        nodes_view = nodes[
            nodes["display_name"]
            .astype(str)
            .str.contains(q, case=False, na=False)
        ]

    if nodes_view.empty:
        st.sidebar.warning("No nodes match your search.")
        st.stop()

    labels = (
        nodes_view["display_name"].astype(str)
        + "  ·  "
        + nodes_view["path"].astype(str)
    )
    options = list(nodes_view.index)

    selected_idx = st.sidebar.selectbox(
        "Select node",
        options=options,
        format_func=lambda i: labels.loc[i],
    )

    node = nodes.loc[selected_idx]

    # ----------------------------
    # KPI helper
    # ----------------------------
    def kpis(fy: int = 2025):
        rev = safe_num(node.get(f"segment_fy{fy}_revenue_usd_bn"))
        ebitda = safe_num(node.get(f"segment_fy{fy}_ebitda_usd_bn"))
        margin = safe_num(node.get(f"segment_fy{fy}_ebitda_margin_pct"))

        c1, c2, c3 = st.columns(3)
        c1.metric(
            f"FY{fy} Revenue (USD bn)",
            f"{rev:,.3g}" if pd.notna(rev) else "—",
        )
        c2.metric(
            f"FY{fy} EBITDA (USD bn)",
            f"{ebitda:,.3g}" if pd.notna(ebitda) else "—",
        )
        c3.metric(
            f"FY{fy} EBITDA Margin (%)",
            f"{margin:,.3g}" if pd.notna(margin) else "—",
        )

    tab1, tab2 = st.tabs(["Overview", "Node Dashboard"])

    with tab1:
        st.subheader(node.get("display_name", ""))
        st.caption(node.get("path", ""))
        kpis()

        scope = str(node.get("scope_context", "") or "").strip()
        fin = str(node.get("financial_commentary", "") or "").strip()
        method = str(node.get("methodology_summary", "") or "").strip()

        if scope:
            st.text_area("Scope", value=scope, height=180, disabled=True)
        if fin:
            st.text_area(
                "Financial commentary",
                value=fin,
                height=180,
                disabled=True,
            )
        if method:
            st.text_area(
                "Methodology",
                value=method,
                height=180,
                disabled=True,
            )

    with tab2:
        st.subheader(node.get("display_name", ""))
        kpis()

        YEARS = list(range(15, 26))  # fy15 → fy25

        metric = st.radio(
            "Metric",
            ["Revenue", "EBITDA", "EBITDA Margin"],
            horizontal=True,
        )

        if metric == "Revenue":
            cols = [f"segment_fy{y}_revenue_usd_bn" for y in YEARS]
            y_label = "Revenue (USD bn)"
        elif metric == "EBITDA":
            cols = [f"segment_fy{y}_ebitda_usd_bn" for y in YEARS]
            y_label = "EBITDA (USD bn)"
        else:
            cols = [f"segment_fy{y}_ebitda_margin_pct" for y in YEARS]
            y_label = "EBITDA Margin (%)"

        df = pd.DataFrame(
            {
                "Fiscal Year": [2000 + y for y in YEARS],
                "Value": [safe_num(node.get(c)) for c in cols],
            }
        )

        fig = px.line(
            df,
            x="Fiscal Year",
            y="Value",
            markers=True,
            title=y_label,
        )
        fig.update_layout(
            margin=dict(l=10, r=10, t=40, b=10)
        )

        st.plotly_chart(fig, use_container_width=True)

        st.markdown("### Players & Proxies")

        pid = str(node.get("node_id"))
        p = (
            players_all[players_all["node_id"].astype(str) == pid].copy()
            if (not players_all.empty and "node_id" in players_all.columns)
            else pd.DataFrame()
        )
        pr = (
            proxies_all[proxies_all["node_id"].astype(str) == pid].copy()
            if (not proxies_all.empty and "node_id" in proxies_all.columns)
            else pd.DataFrame()
        )

        c1, c2 = st.columns(2)

        with c1:
            st.subheader("Top Players")
            if p.empty:
                st.caption("No player rows for this node yet.")
            else:
                p = p.sort_values("rank", ascending=True)
                cols = [
                    "rank",
                    "name",
                    "country",
                    "type",
                    "player_fy25_revenue_usd_bn",
                    "player_fy25_ebitda_usd_bn",
                    "player_fy25_ebitda_margin_pct",
                    "confidence_score",
                    "attribution_basis",
                ]
                cols = [c for c in cols if c in p.columns]
                st.dataframe(p[cols].head(10), use_container_width=True, hide_index=True)

        with c2:
            st.subheader("Proxies")
            if pr.empty:
                st.caption("No proxy rows for this node yet.")
            else:
                cols = [
                    "name",
                    "country",
                    "type",
                    "proxy_reason",
                    "proxy_fy25_revenue_usd_bn",
                    "proxy_fy25_ebitda_usd_bn",
                    "proxy_fy25_ebitda_margin_pct",
                    "confidence_score",
                ]
                cols = [c for c in cols if c in pr.columns]
                if "rank" in pr.columns:
                    pr = pr.sort_values("rank", ascending=True)
                st.dataframe(pr[cols].head(10), use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()

