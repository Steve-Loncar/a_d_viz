import pandas as pd
import math
import streamlit as st
import textwrap
from typing import List

import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import re
import html

from lib.loader import load_workbook_bytes, load_workbook_path
from lib.transforms import derive_hierarchy, safe_num, clean_players, clean_proxies

st.set_page_config(page_title="A&D Market Explorer (v2)\", layout="wide")

def _hierarchy_cols(nodes: pd.DataFrame) -> List[str]:
    """Return hierarchy columns in left->right order."""
    if nodes is None or nodes.empty:
        return []
    cols = [c for c in nodes.columns if isinstance(c, str) and c.startswith("Hierarchy -")]
    # Keep dataframe order (already left->right in your template)
    return cols

def _pick_node_index(nodes: pd.DataFrame, filters: dict) -> int | None:
    """Pick a node index matching filters (best-effort)."""
    if nodes is None or nodes.empty:
        return None
    df = nodes
    for k, v in filters.items():
        if k in df.columns and v is not None and str(v).strip() != "":
            df = df[df[k].astype(str).str.strip() == str(v).strip()]
    if df.empty:
        return None
    return int(df.index[0])

def render_taxonomy_architecture(nodes: pd.DataFrame) -> None:
    st.subheader("Taxonomy architecture")
    hcols = _hierarchy_cols(nodes)
    if not hcols:
        st.info("No hierarchy columns found (expected columns starting with 'Hierarchy -').")
        return

    # Simple drill-down selector (fast + robust)
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        lvl1 = st.selectbox("Main category", sorted([x for x in nodes[hcols[0]].dropna().astype(str).unique() if x.strip()]), index=0)
    df1 = nodes[nodes[hcols[0]].astype(str).str.strip() == str(lvl1).strip()]

    with c2:
        lvl2_opts = sorted([x for x in df1[hcols[1]].dropna().astype(str).unique() if x.strip()]) if len(hcols) > 1 and hcols[1] in df1.columns else []
        lvl2 = st.selectbox("Sector", lvl2_opts, index=0) if lvl2_opts else None
    df2 = df1
    if lvl2 and len(hcols) > 1 and hcols[1] in df2.columns:
        df2 = df2[df2[hcols[1]].astype(str).str.strip() == str(lvl2).strip()]

    with c3:
        lvl3_opts = sorted([x for x in df2[hcols[2]].dropna().astype(str).unique() if x.strip()]) if len(hcols) > 2 and hcols[2] in df2.columns else []
        lvl3 = st.selectbox("Subsector", lvl3_opts, index=0) if lvl3_opts else None
    df3 = df2
    if lvl3 and len(hcols) > 2 and hcols[2] in df3.columns:
        df3 = df3[df3[hcols[2]].astype(str).str.strip() == str(lvl3).strip()]

    with c4:
        lvl4_opts = sorted([x for x in df3[hcols[3]].dropna().astype(str).unique() if x.strip()]) if len(hcols) > 3 and hcols[3] in df3.columns else []
        lvl4 = st.selectbox("Sub-sub-sector", lvl4_opts, index=0) if lvl4_opts else None

    st.markdown("---")
    st.caption("Click a node to jump the left-side node selector.")

    # Show matching leaf nodes as buttons
    df_leaf = df3.copy()
    if lvl4 and len(hcols) > 3 and hcols[3] in df_leaf.columns:
        df_leaf = df_leaf[df_leaf[hcols[3]].astype(str).str.strip() == str(lvl4).strip()]

    if df_leaf.empty:
        st.info("No nodes found for this selection.")
        return

    # Display buttons using display_name + path
    for _, r in df_leaf.head(80).iterrows():
        name = str(r.get("display_name", "")).strip()
        path = str(r.get("path", "")).strip()
        label = name if name else path
        if st.button(f"{label}  ·  {path}", key=f"tax_pick_{r.name}"):
            st.session_state["selected_idx"] = int(r.name)
            st.rerun()


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

def _parse_evidence_snippet(snippet: str) -> tuple[str, str, str]:
    """
    Deterministic parser for the evidence memo format.
    We standardise around these labels:
      - 'Source reference:'
      - 'Quote:'
    Returns (source_reference, quote, remainder).
    If labels aren't present, quote falls back to the full snippet.
    """
    if not snippet:
        return ("", "", "")

    s = str(snippet).strip()
    if not s:
        return ("", "", "")

    # Normalise newlines
    s = re.sub(r"\r\n?", "\n", s)

    # Try to extract "Source reference:" and "Quote:" blocks
    # We keep it intentionally simple and tolerant to whitespace.
    src = ""
    quote = ""
    remainder = s

    m_src = re.search(r"(?is)\bSource\s+reference\s*:\s*(.*?)(?:\n\s*\bQuote\s*:|$)", s)
    if m_src:
        src = m_src.group(1).strip()

    m_q = re.search(r"(?is)\bQuote\s*:\s*(.*)$", s)
    if m_q:
        quote = m_q.group(1).strip()

    # If we extracted anything, compute a clean remainder (optional)
    if m_src or m_q:
        remainder = s
        if m_src:
            remainder = re.sub(r"(?is)\bSource\s+reference\s*:\s*.*?(?=\n\s*\bQuote\s*:|$)", "", remainder).strip()
        if m_q:
            remainder = re.sub(r"(?is)\bQuote\s*:\s*.*$", "", remainder).strip()

    # Hard fallback: if no Quote extracted, treat entire snippet as the quote body.
    if not quote:
        quote = s

    return (src, quote, remainder)


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

def _get_sheet(wb: dict[str, pd.DataFrame], names: list[str]) -> pd.DataFrame:
    """
    Return the first matching sheet as a DataFrame.
    IMPORTANT: don't use `df or other_df` because pandas DataFrames cannot be
    evaluated as booleans (raises ValueError: ambiguous truth value).
    """
    for name in names:
        df = wb.get(name)
        if isinstance(df, pd.DataFrame):
            return df
    return pd.DataFrame()

def _split_supported_by(x) -> list[str]:
    """
    Evidence_Map.supported_by is often stored as a string (e.g. "EVID:1, EVID:2")
    or sometimes a JSON-ish list. Keep it deterministic and forgiving.
    """
    if x is None:
        return []
    if isinstance(x, float) and pd.isna(x):
        return []
    if isinstance(x, (list, tuple, set)):
        return [str(i).strip() for i in x if str(i).strip()]
    s = str(x).strip()
    if not s:
        return []
    # Try JSON list first
    if s.startswith("[") and s.endswith("]"):
        try:
            import json
            arr = json.loads(s)
            if isinstance(arr, list):
                return [str(i).strip() for i in arr if str(i).strip()]
        except Exception:
            pass
    # Fallback: comma/semicolon separated
    parts = re.split(r"[;,]\s*|\s{2,}", s)
    return [p.strip() for p in parts if p.strip()]

def humanise_support_field(field: str) -> str:
    """
    Translate internal support fields into client-readable claims.
    Examples:
      fy25_revenue_usd_bn -> FY2025 Node Revenue
      player:Textron_Aviation:fy24_revenue_usd_bn -> FY2024 Textron Aviation Revenue
    """
    if not field:
        return field

    s = field.strip()

    # Player-level: player:Name:fyXX_metric
    if s.startswith("player:"):
        try:
            _, player, metric = s.split(":", 2)
            player = player.replace("_", " ")
            return f"{_humanise_fy_metric(metric)} ({player})"
        except Exception:
            return s

    # Node-level metric
    return _humanise_fy_metric(s)

def _humanise_fy_metric(metric: str) -> str:
    m = metric.lower()

    # Extract FY
    fy_match = re.search(r"fy(\d{2})", m)
    fy = f"FY20{fy_match.group(1)}" if fy_match else ""

    if "revenue" in m:
        label = "Revenue"
    elif "ebitda_margin" in m or "margin" in m:
        label = "EBITDA Margin"
    elif "ebitda" in m:
        label = "EBITDA"
    else:
        return metric

    scope = "Node"
    return f"{fy} {scope} {label}".strip()

def humanise_column_name(col: str) -> str:
    """
    Turn raw dataframe column names into client-friendly headers.
    Examples:
      player_fy25_revenue_usd_bn -> FY2025 Revenue (USD bn)
      proxy_fy24_ebitda_margin_pct -> FY2024 EBITDA Margin (%)
      confidence_score -> Confidence
      attribution_basis -> Attribution basis
    """
    if not col:
        return col
    s = str(col).strip()

    # Known non-metric columns
    static = {
        "rank": "Rank",
        "name": "Name",
        "country": "Country",
        "type": "Type",
        "proxy_reason": "Proxy rationale",
        "confidence_score": "Confidence",
        "attribution_basis": "Attribution basis",
        "node_id": "Node ID",
        "player_id": "Player ID",
        "proxy_id": "Proxy ID",
    }
    if s in static:
        return static[s]

    # Strip leading entity prefixes for readability in tables
    # player_fy25_revenue_usd_bn -> fy25_revenue_usd_bn
    # proxy_fy25_ebitda_usd_bn -> fy25_ebitda_usd_bn
    s2 = re.sub(r"^(player|proxy)_", "", s)

    # FY metric patterns
    m = re.match(r"^fy(\d{2})_(.+)$", s2.lower())
    if m:
        yy = m.group(1)
        rest = m.group(2)
        fy = f"FY20{yy}"

        if "revenue" in rest:
            return f"{fy} Revenue (USD bn)"
        if "ebitda_margin" in rest or rest.endswith("margin_pct") or "margin" in rest:
            return f"{fy} EBITDA Margin (%)"
        if "ebitda" in rest:
            return f"{fy} EBITDA (USD bn)"

    # Fallback: prettify snake_case
    return s.replace("_", " ").strip().title()

def render_node_header(node: pd.Series) -> None:
    """Consistent header at the top of every node-level tab."""
    name = str(node.get("display_name", "") or "").strip()
    path = str(node.get("path", "") or "").strip()
    if name:
        st.header(name)
    if path:
        st.caption(path)

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
    evidence_all = _get_sheet(wb, ["Evidence", "EVIDENCE", "evidence"])
    evidence_map_all = _get_sheet(wb, ["Evidence_Map", "EVIDENCE_MAP", "evidence_map", "Evidence map"])

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
            nodes["display_name"].astype(str).str.contains(q, case=False, na=False)
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

    # Allow other parts of the app (taxonomy architecture) to set the selection
    if "selected_idx" not in st.session_state:
        st.session_state["selected_idx"] = int(options[0])
    if st.session_state["selected_idx"] not in options:
        st.session_state["selected_idx"] = int(options[0])

    selected_idx = st.sidebar.selectbox(
        "Select node",
        options=options,
        index=options.index(st.session_state["selected_idx"]),
        format_func=lambda i: labels.loc[i],
    )
    st.session_state["selected_idx"] = int(selected_idx)

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

    top_node, top_tax = st.tabs(["Node-level analysis", "Overall taxonomy analysis"])

    with top_node:
        tab1, tab2, tab3, tab4 = st.tabs(
            ["Overview", "Node Financials", "Players & Proxies", "Evidence mapping"]
        )

        with tab1:
            render_node_header(node)
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
            render_node_header(node)
            kpis()

            YEARS = list(range(15, 26))  # fy15 → fy25
            year_labels = [2000 + y for y in YEARS]

            # Build three series directly from the Nodes wide columns
            rev_cols = [f"segment_fy{y}_revenue_usd_bn" for y in YEARS]
            ebitda_cols = [f"segment_fy{y}_ebitda_usd_bn" for y in YEARS]
            margin_cols = [f"segment_fy{y}_ebitda_margin_pct" for y in YEARS]

            rev = [safe_num(node.get(c)) for c in rev_cols]
            ebitda = [safe_num(node.get(c)) for c in ebitda_cols]
            margin = [safe_num(node.get(c)) for c in margin_cols]

            # Combo chart: clustered bars (Revenue, EBITDA) + margin line on secondary axis
            x = year_labels
            fig = make_subplots(specs=[[{"secondary_y": True}]])

            fig.add_trace(
            go.Bar(
                name="Revenue (USD bn)",
                x=x,
                y=rev,
                offsetgroup="rev",
                marker_color="#7CC7FF",
            ),
            secondary_y=False,
            )

            fig.add_trace(
            go.Bar(
                name="EBITDA (USD bn)",
                x=x,
                y=ebitda,
                offsetgroup="ebitda",
                marker_color="#2E7BEF",
            ),
            secondary_y=False,
            )

            fig.add_trace(
            go.Scatter(
                name="EBITDA Margin (%)",
                x=x,
                y=margin,
                mode="lines+markers",
                line=dict(color="#FF6B6B", width=2),
                marker=dict(color="#FF6B6B"),
            ),
            secondary_y=True,
            )

            fig.update_layout(
            barmode="group",
            title=dict(
                text="Revenue, EBITDA and Margin",
                x=0.0,
                xanchor="left",
                y=0.98,
                yanchor="top",
            ),
            margin=dict(l=10, r=10, t=55, b=10),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            )

            fig.update_xaxes(title_text="Fiscal Year", showgrid=False)

            # Kill horizontal gridlines (and all gridlines) - cleaner for clients
            fig.update_yaxes(title_text="USD bn", secondary_y=False, showgrid=False, zeroline=False)
            fig.update_yaxes(title_text="Margin (%)", secondary_y=True, showgrid=False, zeroline=False)

            st.plotly_chart(fig, use_container_width=True)

            # FY table underneath (all three metrics)
            st.markdown("### FY15–FY25 table")

            # Transposed layout: years as columns (matches left→right chart flow)
            year_cols = [f"FY{y}" for y in year_labels]  # FY2015 ... FY2025
            table_t = pd.DataFrame(
            {
                "Metric": ["Revenue (USD bn)", "EBITDA (USD bn)", "EBITDA Margin (%)"],
                **{year_cols[i]: [rev[i], ebitda[i], margin[i]] for i in range(len(year_cols))},
            }
            )

            # Light formatting for readability
            table_fmt = table_t.copy()
            for i, y in enumerate(year_cols):
                try:
                    table_fmt.loc[table_fmt["Metric"] == "Revenue (USD bn)", y] = (
                        "" if pd.isna(table_t.loc[0, y]) else f"{float(table_t.loc[0, y]):,.3f}"
                    )
                except Exception:
                    table_fmt.loc[table_fmt["Metric"] == "Revenue (USD bn)", y] = ""
                try:
                    table_fmt.loc[table_fmt["Metric"] == "EBITDA (USD bn)", y] = (
                        "" if pd.isna(table_t.loc[1, y]) else f"{float(table_t.loc[1, y]):,.3f}"
                    )
                except Exception:
                    table_fmt.loc[table_fmt["Metric"] == "EBITDA (USD bn)", y] = ""
                try:
                    table_fmt.loc[table_fmt["Metric"] == "EBITDA Margin (%)", y] = (
                        "" if pd.isna(table_t.loc[2, y]) else f"{float(table_t.loc[2, y]):,.1f}"
                    )
                except Exception:
                    table_fmt.loc[table_fmt["Metric"] == "EBITDA Margin (%)", y] = ""

            st.dataframe(table_fmt, use_container_width=True, hide_index=True)

            # Financial commentary (below table)
            fin = str(node.get("financial_commentary", "") or "").strip()
            if fin:
                render_card("Financial commentary", fin)

        with tab3:
            render_node_header(node)
            st.markdown("### Players & Proxies")

            pid = str(node.get("node_id"))
            players = (
                players_all[players_all["node_id"].astype(str) == pid].copy()
                if (not players_all.empty and "node_id" in players_all.columns)
                else pd.DataFrame()
            )
            proxies = (
                proxies_all[proxies_all["node_id"].astype(str) == pid].copy()
                if (not proxies_all.empty and "node_id" in proxies_all.columns)
                else pd.DataFrame()
            )

            # ----------------------------
            # 1) Aggregated view (v1-style)
            # ----------------------------
            fy_pick = st.radio("Fiscal year", ["FY23", "FY24", "FY25"], index=2, horizontal=True)
            fy2 = int(fy_pick.replace("FY", ""))  # 23/24/25

            rev_col = f"player_fy{fy2}_revenue_usd_bn"
            ebitda_col = f"player_fy{fy2}_ebitda_usd_bn"
            mgn_col = f"player_fy{fy2}_ebitda_margin_pct"

            if players.empty:
                st.warning("No players found for this node.")
            else:
                p = players.copy()
                # Ensure numeric
                p[rev_col] = p[rev_col].map(safe_num)
                p[ebitda_col] = p[ebitda_col].map(safe_num)
                p[mgn_col] = p[mgn_col].map(safe_num)

                # Prefer rank ordering if present; otherwise revenue desc
                if "rank" in p.columns and p["rank"].notna().any():
                    p = p.sort_values("rank", ascending=True)
                else:
                    p = p.sort_values(rev_col, ascending=False)

                # Limit to keep charts readable (still "all players", but practical)
                p_chart = p.head(25).copy()

                c1, c2, c3 = st.columns(3)
                with c1:
                    fig1 = px.bar(
                        p_chart,
                        x=rev_col,
                        y="name",
                        orientation="h",
                        title=f"Revenue (USD bn) — {fy_pick}",
                        hover_data=["country", "type"],
                    )
                    fig1.update_layout(margin=dict(l=10, r=10, t=45, b=10))
                    fig1.update_yaxes(categoryorder="total ascending")
                    st.plotly_chart(fig1, use_container_width=True)

                with c2:
                    fig2 = px.bar(
                        p_chart,
                        x=ebitda_col,
                        y="name",
                        orientation="h",
                        title=f"EBITDA (USD bn) — {fy_pick}",
                        hover_data=["country", "type"],
                    )
                    fig2.update_layout(margin=dict(l=10, r=10, t=45, b=10))
                    fig2.update_yaxes(categoryorder="total ascending")
                    st.plotly_chart(fig2, use_container_width=True)

                with c3:
                    fig3 = px.bar(
                        p_chart,
                        x=mgn_col,
                        y="name",
                        orientation="h",
                        title=f"EBITDA Margin (%) — {fy_pick}",
                        hover_data=["country", "type"],
                    )
                    fig3.update_layout(margin=dict(l=10, r=10, t=45, b=10))
                    fig3.update_yaxes(categoryorder="total ascending")
                    st.plotly_chart(fig3, use_container_width=True)

                st.caption("Charts show top 25 for readability; tables below include full rows for the node.")

                # Optional: full table directly beneath aggregated charts
                st.markdown("### Players table")
                p_tbl = p[[
                    "rank", "name", "country", "type",
                    "player_fy23_revenue_usd_bn", "player_fy24_revenue_usd_bn", "player_fy25_revenue_usd_bn",
                    "player_fy23_ebitda_usd_bn", "player_fy24_ebitda_usd_bn", "player_fy25_ebitda_usd_bn",
                    "player_fy23_ebitda_margin_pct", "player_fy24_ebitda_margin_pct", "player_fy25_ebitda_margin_pct",
                    "confidence_score", "attribution_basis"
                ]].copy()
                p_tbl = p_tbl.rename(columns={c: humanise_column_name(c) for c in p_tbl.columns})
                st.dataframe(p_tbl, use_container_width=True, hide_index=True)

            # ----------------------------
            # 2) Commentary
            # ----------------------------
            player_comm = str(node.get("player_commentary", "") or "").strip()
            proxy_comm = str(node.get("proxy_commentary", "") or "").strip()
            if player_comm:
                render_card("Player commentary", player_comm)
            if proxy_comm:
                render_card("Proxy commentary", proxy_comm)

            # ----------------------------
            # 3) Top 10 combo charts (FY23–FY25)
            # ----------------------------
            if not players.empty:
                st.markdown("### Top 10 players — FY23–FY25")
                p10 = players.copy()
                if "rank" in p10.columns and p10["rank"].notna().any():
                    p10 = p10.sort_values("rank", ascending=True)
                else:
                    p10 = p10.sort_values("player_fy25_revenue_usd_bn", ascending=False)
                p10 = p10.head(10)

                years = [2023, 2024, 2025]
                cols = st.columns(2)
                for i, (_, r) in enumerate(p10.iterrows()):
                    rev = [safe_num(r.get(f"player_fy{y%100}_revenue_usd_bn")) for y in years]
                    ebt = [safe_num(r.get(f"player_fy{y%100}_ebitda_usd_bn")) for y in years]
                    mgn = [safe_num(r.get(f"player_fy{y%100}_ebitda_margin_pct")) for y in years]

                    fig = make_subplots(specs=[[{"secondary_y": True}]])
                    fig.add_trace(go.Bar(name="Revenue (USD bn)", x=years, y=rev, offsetgroup="rev"), secondary_y=False)
                    fig.add_trace(go.Bar(name="EBITDA (USD bn)", x=years, y=ebt, offsetgroup="ebt"), secondary_y=False)
                    fig.add_trace(go.Scatter(name="Margin (%)", x=years, y=mgn, mode="lines+markers"), secondary_y=True)
                    fig.update_layout(
                        barmode="group",
                        title=dict(text=str(r.get("name", "")), x=0.0, xanchor="left"),
                        margin=dict(l=10, r=10, t=55, b=10),
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
                    )
                    fig.update_xaxes(title_text="Fiscal Year", showgrid=False)
                    fig.update_yaxes(title_text="USD bn", secondary_y=False, showgrid=False, zeroline=False)
                    fig.update_yaxes(title_text="Margin (%)", secondary_y=True, showgrid=False, zeroline=False)

                    with cols[i % 2]:
                        st.plotly_chart(fig, use_container_width=True)

            # Proxies table (simple for now; we can add proxy charts next)
            st.markdown("### Proxies table")
            if proxies.empty:
                st.caption("No proxies for this node.")
            else:
                pr_tbl = proxies[[
                    "name", "country", "type", "proxy_reason",
                    "proxy_fy23_revenue_usd_bn", "proxy_fy24_revenue_usd_bn", "proxy_fy25_revenue_usd_bn",
                    "proxy_fy23_ebitda_usd_bn", "proxy_fy24_ebitda_usd_bn", "proxy_fy25_ebitda_usd_bn",
                    "proxy_fy23_ebitda_margin_pct", "proxy_fy24_ebitda_margin_pct", "proxy_fy25_ebitda_margin_pct",
                    "confidence_score"
                ]].copy()
                pr_tbl = pr_tbl.rename(columns={c: humanise_column_name(c) for c in pr_tbl.columns})
                st.dataframe(pr_tbl, use_container_width=True, hide_index=True)

        with tab4:
            render_node_header(node)
            st.markdown("### Supporting evidence")

            pid = str(node.get("node_id", "") or "")
            if evidence_all.empty:
                st.warning("Evidence sheet is empty or missing from the workbook.")
                st.stop()

            # Expect evidence rows to include node_id + evidence_id
            if "node_id" not in evidence_all.columns or "evidence_id" not in evidence_all.columns:
                st.warning("Evidence sheet missing required columns: node_id and/or evidence_id.")
                st.stop()

            ev_pick = evidence_all[evidence_all["node_id"].astype(str) == pid].copy()
            if ev_pick.empty:
                st.info("No evidence rows found for this node_id.")
                st.stop()

            # Build an evidence_id -> [fields supported] mapping from Evidence_Map
            supports_by_evid: dict[str, list[str]] = {}
            if not evidence_map_all.empty and "node_id" in evidence_map_all.columns:
                em = evidence_map_all[evidence_map_all["node_id"].astype(str) == pid].copy()
                # Require at least field + supported_by
                if not em.empty and "field" in em.columns and "supported_by" in em.columns:
                    rows = []
                    for _, r in em.iterrows():
                        field = str(r.get("field", "") or "").strip()
                        evids = _split_supported_by(r.get("supported_by"))
                        for e in evids:
                            rows.append((e.strip(), field))
                    if rows:
                        tmp = pd.DataFrame(rows, columns=["evidence_id", "field"])
                        tmp["evidence_id"] = tmp["evidence_id"].astype(str).str.strip()
                        tmp["field"] = tmp["field"].astype(str).str.strip()
                        for evid, g in tmp.groupby("evidence_id"):
                            fields = [f for f in g["field"].tolist() if f]
                            # de-dupe preserving order
                            seen = set()
                            out = []
                            for f in fields:
                                if f not in seen:
                                    out.append(f)
                                    seen.add(f)
                            supports_by_evid[evid] = out

            def _clean(x):
                if x is None:
                    return ""
                if isinstance(x, float) and pd.isna(x):
                    return ""
                return str(x).strip()

            # Render evidence in a "research memo" style (like your screenshot)
            for _, r in ev_pick.iterrows():
                evid = _clean(r.get("evidence_id") or r.get("id") or "")
                title = _clean(r.get("title") or r.get("source_title") or "")
                url = _clean(r.get("url") or r.get("source_url") or "")

                # Optional richer fields (if present in other projects / future schema)
                dataset = _clean(r.get("dataset") or r.get("dataset_name") or "")
                src_type = _clean(r.get("type") or r.get("source_type") or "")
                supports = _clean(r.get("supports") or r.get("supports_field") or "")
                strength = _clean(r.get("strength") or r.get("strength_score") or "")
                conf = _clean(r.get("confidence_score") or "")

                # Canonical: parse from snippet so it is consistent across all evidence
                snippet = _clean(r.get("snippet") or r.get("excerpt") or "")
                source_ref, quote, _ = _parse_evidence_snippet(snippet)

                # What this evidence supports (from Evidence_Map)
                supports_fields = supports_by_evid.get(evid, [])
                supports_html = ""
                if supports_fields:
                    # Translate internal fields to client-readable claims
                    translated = [humanise_support_field(f) for f in supports_fields]

                    show_n = 8
                    shown = translated[:show_n]
                    more = len(supports_fields) - len(shown)
                    bullet_lis = "".join(f"<li>{html.escape(f)}</li>" for f in shown)
                    suffix = f"<div style='opacity:0.7; margin-top:6px;'>+{more} more</div>" if more > 0 else ""
                    supports_html = f"<div style='margin-top:10px;'><span style='font-weight:650;'>Supports:</span><ul style='margin:6px 0 0 1.1rem;'>{bullet_lis}</ul>{suffix}</div>"

                # Title line: clickable if URL available
                if title and url:
                    header_html = f"<a href='{html.escape(url)}' target='_blank' style='text-decoration:none; color: inherit;'>{html.escape(title)}</a>"
                elif title:
                    header_html = html.escape(title)
                else:
                    header_html = html.escape(evid or "Evidence item")

                # Prefix with evidence ID like "E16 — …"
                if evid:
                    header_html = f"<span style='opacity:0.85'>{html.escape(evid)}</span> — {header_html}"

                meta_parts = []
                if dataset:
                    meta_parts.append(dataset)
                if src_type:
                    meta_parts.append(src_type)
                if strength:
                    meta_parts.append(f"strength={strength}")
                if conf:
                    meta_parts.append(f"confidence={conf}")
                if supports:
                    meta_parts.append(f"supports: {supports}")
                meta = " · ".join(meta_parts)

                # Build sections: Source reference + Quote
                sr_html = ""
                if source_ref:
                    sr_html = f"<div style='margin-top: 8px;'><span style='font-weight:650;'>Source reference:</span> {html.escape(source_ref)}</div>"

                quote_html = ""
                if quote:
                    quote_html = f"<div style='margin-top: 6px;'><span style='font-weight:650;'>Quote:</span> {html.escape(quote)}</div>"

                st.markdown(
                    f"""
                    <div style="
                        border: 1px solid rgba(255,255,255,0.08);
                        border-radius: 12px;
                        padding: 16px 18px;
                        margin: 14px 0 16px 0;
                        background: rgba(255,255,255,0.02);
                    ">
                      <div style="font-weight: 750; font-size: 1.05rem; margin-bottom: 6px;">
                        {header_html}
                      </div>
                      {'<div style="opacity:0.75; font-size: 0.92rem; margin-bottom: 10px;">' + html.escape(meta) + '</div>' if meta else ''}
                      {sr_html}
                      {quote_html}
                      {supports_html}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

    with top_tax:
        st.subheader("Overall taxonomy analysis")
        tax1, tax2, tax3 = st.tabs(
            ["Taxonomy architecture", "Custom heatmaps", "Total heatmap"]
        )

        with tax1:
            render_taxonomy_architecture(nodes)

        with tax2:
            st.markdown("### Custom heatmaps")
            st.caption("Goal: user-defined heatmaps (like the v1 app).")
            st.info(
                "Shell only. Next: allow selecting a metric + FY + taxonomy depth, "
                "then render a heatmap across nodes."
            )
            st.markdown(
                "- **Inputs:** metric selector (Revenue / EBITDA / Margin), FY (15–25), node subset\n"
                "- **Outputs:** heatmap grid (taxonomy rows/cols depending on chosen grouping)\n"
                "- **UX:** click a cell → open that node in Node-level analysis"
            )

        with tax3:
            st.markdown("### Total heatmap")
            st.caption("Goal: one default 'big picture' heatmap (like the v1 app).")
            st.info(
                "Shell only. Next: a preconfigured heatmap view (e.g., FY25 Revenue) "
                "over the taxonomy, with drill-down."
            )
            st.markdown(
                "- **Inputs:** one canonical metric (default FY25 Revenue) + taxonomy grouping\n"
                "- **Outputs:** full taxonomy heatmap with drilldown\n"
                "- **UX:** simple, immediate 'wow' view for clients"
            )


if __name__ == "__main__":
    main()
