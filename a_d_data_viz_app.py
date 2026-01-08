import pandas as pd
import math
import streamlit as st

import plotly.express as px
import re
import html

from lib.loader import load_workbook_bytes, load_workbook_path
from lib.transforms import derive_hierarchy, safe_num, clean_players, clean_proxies


st.set_page_config(page_title="A&D Market Explorer (v2)", layout="wide")


def _to_bullets(text: str) -> str:
    """
    Turn dense text into bullets.
    - If the text already contains newline-delimited bullets, keep them.
    - Otherwise split into sentences and bullet them.
    Returns HTML (escaped) inside <ul>.
    """
    if not text:
        return ""

    raw = str(text).strip()
    if not raw:
        return ""

    # If user/agent already gave newline-ish bullets, respect them
    lines = [ln.strip() for ln in re.split(r"\r?\n+", raw) if ln.strip()]
    looks_like_bullets = sum(1 for ln in lines if ln.startswith(("-", "•", "*"))) >= 2

    items: list[str] = []
    if looks_like_bullets:
        for ln in lines:
            ln2 = ln.lstrip("-•*").strip()
            if ln2:
                items.append(ln2)
    else:
        # Sentence split (good enough for now)
        sents = re.split(r"(?<=[.!?])\s+", raw)
        sents = [s.strip() for s in sents if s and s.strip()]
        # If it’s just one long sentence, keep as a single paragraph bullet
        items = sents if len(sents) > 1 else [raw]

    lis = "".join(f"<li>{html.escape(i)}</li>" for i in items)
    return f"<ul style='margin: 0.25rem 0 0.25rem 1.1rem;'>{lis}</ul>"


def render_card(title: str, text: str) -> None:
    """
    Non-scrolling “card” with bullet formatting for readability.
    """
    if not text:
        return
    body_html = _to_bullets(text)
    if not body_html:
        return

    st.markdown(
        f"""
        <div style="
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 10px;
            padding: 14px 16px;
            margin: 10px 0 18px 0;
            background: rgba(255,255,255,0.03);
        ">
          <div style="font-weight: 650; margin-bottom: 8px;">{html.escape(title)}</div>
          <div style="opacity: 0.92; line-height: 1.45;">{body_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


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
    def kpis(fy: int = 25):
        # Spreadsheet uses two-digit FY columns: segment_fy15 ... segment_fy25
        fy2 = fy % 100 if fy > 100 else fy
        fy_label = 2000 + fy2  # FY25 -> 2025 for display

        rev = safe_num(node.get(f"segment_fy{fy2:02d}_revenue_usd_bn"))
        ebitda = safe_num(node.get(f"segment_fy{fy2:02d}_ebitda_usd_bn"))
        margin = safe_num(node.get(f"segment_fy{fy2:02d}_ebitda_margin_pct"))

        c1, c2, c3 = st.columns(3)
        c1.metric(
            f"FY{fy_label} Revenue (USD bn)",
            f"{rev:,.3g}" if pd.notna(rev) else "—",
        )
        c2.metric(
            f"FY{fy_label} EBITDA (USD bn)",
            f"{ebitda:,.3g}" if pd.notna(ebitda) else "—",
        )
        c3.metric(
            f"FY{fy_label} EBITDA Margin (%)",
            f"{margin:,.3g}" if pd.notna(margin) else "—",
        )

    tab1, tab2 = st.tabs(["Overview", "Node Dashboard"])

    with tab1:
        st.subheader(node.get("display_name", ""))
        st.caption(node.get("path", ""))
        kpis()

        # Overview content (no scroll boxes)
        # NOTE: financial_commentary moved to Tab 2 in the next step
        desc = str(node.get("node_description", "") or node.get("description", "") or "").strip()
        scope = str(node.get("scope_context", "") or "").strip()
        method = str(node.get("methodology_summary", "") or "").strip()

        if desc:
            render_card("Node description", desc)
        if scope:
            render_card("Scope", scope)
        if method:
            render_card("Methodology", method)

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

