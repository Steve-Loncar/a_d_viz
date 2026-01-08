
from datetime import datetime
import hashlib
import json
from typing import Optional

def format_http_status(code: Optional[int]) -> str:
    """
    Cosmetic helper: returns a short, user-friendly label for an HTTP code.
    Does not affect control-flow (pure display).
    """
    if code is None:
        return "No HTTP status (request did not complete)"
    try:
        c = int(code)
    except Exception:
        return f"HTTP {code}"

    if c == 200:
        return "HTTP 200 (OK)"
    if c == 201:
        return "HTTP 201 (Created)"
    if c == 202:
        return "HTTP 202 (Accepted)"
    if c == 400:
        return "HTTP 400 (Bad request)"
    if c == 401:
        return "HTTP 401 (Unauthorized)"
    if c == 403:
        return "HTTP 403 (Forbidden)"
    if c == 404:
        return "HTTP 404 (Not found)"
    if c == 429:
        return "HTTP 429 (Rate limited)"
    if c == 500:
        return "HTTP 500 (Server error)"
    if c == 502:
        return "HTTP 502 (Bad gateway)"
    if c == 503:
        return "HTTP 503 (Service unavailable)"
    if c == 504:
        return "HTTP 504 (Gateway timeout)"
    if c == 524:
        return "HTTP 524 (Gateway timeout - upstream may still finish)"
    return f"HTTP {c}"

def stable_hash(obj) -> str:
    """
    Deterministic hash for JSON-serializable payloads.
    Used to detect repeated writes / changes.
    """
    try:
        s = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        # Fall back to stringification if something non-serializable leaks in
        s = str(obj)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

# streamlit_echo_test.py - backup test 3 (working!)
import glob
import json
import os
import time
from collections import deque
from datetime import datetime
import uuid
import re
import unicodedata
import hashlib

import pandas as pd
import requests
import streamlit as st

# NOTE: nothing fancy; we will post a flat payload (numbers/strings only)

# ----------------------------
# Helpers: stable hashing
# ----------------------------
def stable_hash(obj) -> str:
    """
    Deterministic hash for payload de-dupe.
    - Stable across reruns and key-order differences
    - Works even if obj contains non-JSON-native types via default=str fallback
    """
    try:
        s = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        s = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

# ----------------------------
# App mode switch (v1 / v2)
# ----------------------------
if "app_mode" not in st.session_state:
    st.session_state["app_mode"] = "v1"

st.sidebar.markdown("### App mode")
st.sidebar.selectbox(
    "Choose mode",
    options=["v1", "v2 (experimental)"],
    index=0 if st.session_state["app_mode"] == "v1" else 1,
    key="app_mode",
    help="v2 runs from defence_agent_v2.py",
)

if st.session_state["app_mode"] != "v1":
    try:
        import defence_agent_v2
        defence_agent_v2.main()
    except Exception as e:
        st.error("Failed to load defence_agent_v2.py")
        st.exception(e)
    st.stop()

# ----------------------------
# Helpers: sanitization & flattening
# ----------------------------

def sanitize_llm_json_str(raw: str) -> str:
    """
    Normalize newlines and strip ASCII control characters so json.loads
    does not fail with 'Invalid control character'. Does NOT touch
    braces, quotes or backticks to avoid breaking JSON structure.
    """
    if not raw:
        return ""
    if not isinstance(raw, str):
        raw = str(raw)

    # Normalize all newline variants to a single space
    raw = raw.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")

    # Strip remaining ASCII control chars (0–31, 127)
    raw = re.sub(r"[\x00-\x1F\x7F]", " ", raw)

    # Collapse long runs of spaces
    raw = re.sub(r" {2,}", " ", raw)

    return raw
def _find_result(results_list, field_name):
    """
    results_list: list of { "field": "...", "value": ... }
    returns the 'value' dict for the first item whose field == field_name, else {}
    """
    if not isinstance(results_list, list):
        return {}
    for item in results_list:
        try:
            if item.get("field") == field_name:
                return item.get("value", {})
        except AttributeError:
            continue
    return {}

def flatten_llm_to_row(llm_parsed_str: str) -> dict:
    """
    Accepts the llm_output_parsed string (JSON), returns a flat dict with:
      - node identity: node_id, path, display_name, level, parent_id
      - full historical finance: FY15–FY25 (annual) revenue, EBITDA, EBITDA margin
      - comments/context and source metadata where available (as plain strings)
    No nested objects. No blobs. Numbers come through as numbers; text as strings.
    """
    row: dict = {}
    if not llm_parsed_str:
        return row
    try:
        # Accept either a dict (already parsed) or a JSON string
        if isinstance(llm_parsed_str, dict):
            data = llm_parsed_str
        else:
            safe = sanitize_llm_json_str(llm_parsed_str or "")

            if len(safe) > 49000:
                bad = ord(safe[48999])
                print("DEBUG before filter (flatten_llm_to_row), char[49000]:", bad)

            safe = "".join(
                ch if (32 <= ord(ch) <= 126 or ch == "\t") else " "
                for ch in safe
            )

            if len(safe) > 49000:
                bad2 = ord(safe[48999])
                print("DEBUG after filter (flatten_llm_to_row), char[49000]:", bad2)

            data = json.loads(safe)
    except Exception:
        return row

    # top-level basics
    row["node_id"] = data.get("node_id")
    row["path"] = data.get("path")
    row["display_name"] = data.get("display_name")
    row["level"] = data.get("level")
    row["parent_id"] = data.get("parent_id")

    # unpack results (can be dict or legacy list-of-{field,value})
    results = data.get("results", {})
    if isinstance(results, dict):
        node_fin = results.get("node_financials", {}) or {}
    else:
        node_fin = _find_result(results, "node_financials") or {}

    def _num(d: dict, k: str):
        if not isinstance(d, dict):
            return None
        v = d.get(k)
        try:
            return float(v) if v is not None else None
        except Exception:
            return None

    # Full historical revenue (USD bn)
    for y in ["15", "16", "17", "18", "19", "20", "21", "22", "23", "24", "25"]:
        row[f"segment_fy{y}_revenue_usd_bn"] = _num(node_fin, f"fy{y}_revenue_usd_bn")

    # Full historical EBITDA (USD bn) and margin (%)
    for y in ["15", "16", "17", "18", "19", "20", "21", "22", "23", "24", "25"]:
        row[f"segment_fy{y}_ebitda_usd_bn"] = _num(node_fin, f"fy{y}_ebitda_usd_bn")
        row[f"segment_fy{y}_ebitda_margin_pct"] = _num(node_fin, f"fy{y}_ebitda_margin_pct")

    # NOTE: CAGR fields are computed separately in the spreadsheet.
    # We deliberately do NOT include `segment_revenue_cagr_pct` or
    # `segment_ebitda_cagr_pct` in the Nodes payload to keep it
    # exactly aligned with the Nodes sheet schema.

    # Helper to pull narrative fields from either top-level or results{}
    def _field(key: str):
        if key in data:
            return data.get(key)
        if isinstance(results, dict) and key in results:
            return results.get(key)
        return None

    # Narrative & context fields
    row["financial_commentary"] = _field("financial_commentary")
    row["player_commentary"] = _field("player_commentary")
    row["proxy_commentary"] = _field("proxy_commentary")
    row["methodology_summary"] = _field("methodology_summary")
    row["scope_context"] = _field("scope_context")
    row["extra_node_context"] = _field("extra_node_context")
    row["taxonomy_reference"] = _field("taxonomy_reference")

    # Sources and source counts
    fin_sources = None
    player_sources = None
    if isinstance(results, dict):
        fin_sources = results.get("financial_sources")
        player_sources = results.get("player_sources")

    def _serialize_sources(s):
        if s is None:
            return None
        try:
            return json.dumps(s, ensure_ascii=False)
        except Exception:
            return str(s)

    row["financial_sources"] = _serialize_sources(fin_sources)
    row["player_sources"] = _serialize_sources(player_sources)

    row["source_count_financial"] = len(fin_sources) if isinstance(fin_sources, list) else None
    row["source_count_player"] = len(player_sources) if isinstance(player_sources, list) else None

    # Overall confidence
    conf_overall = data.get("confidence_overall")
    if conf_overall is None:
        conf_overall = data.get("confidence")
    row["confidence_overall"] = conf_overall

    return row


# -----------------------------------------------------------------
# NEW: flatten_full_llm_output – builds all sheet rows (node, players, proxies)
# -----------------------------------------------------------------
def flatten_full_llm_output(llm_parsed_str: str) -> dict:
    """
    Expand parsed LLM output into flat sets:
      nodes_row         → single dict
      players_rows      → list of dicts for top players (blank-filled up to schema)
      proxies_rows      → list of dicts for proxies / pure-plays (blank-filled up to schema)
      evidence_rows     → list of evidence dicts (one per evidence object, optional)
      evidence_map_rows → list of mappings from field → supporting evidence_ids (optional)
    """
    # --- Defensive normalization ---
    if not llm_parsed_str:
        return {}

    try:
        # Convert anything (str, pydantic object, streamlit state) into a plain dict
        if isinstance(llm_parsed_str, str):
            safe = sanitize_llm_json_str(llm_parsed_str or "")
            safe = "".join(
                ch if (32 <= ord(ch) <= 126 or ch == "\t") else " "
                for ch in safe
            )
            # If long enough, drop only the single bad char at 49000, keep the rest
            BAD_POS = 49000  # 1‑based
            if len(safe) >= BAD_POS:
                i = BAD_POS - 1  # 0‑based index
                safe = safe[:i] + safe[i+1:]
            data = json.loads(safe)
        elif hasattr(llm_parsed_str, "dict"):
            data = llm_parsed_str.dict()
        elif hasattr(llm_parsed_str, "to_dict"):
            data = llm_parsed_str.to_dict()
        elif isinstance(llm_parsed_str, dict):
            data = llm_parsed_str
        else:
            # Last-resort normalization for arbitrary objects
            tmp = json.dumps(llm_parsed_str, default=str)
            safe = sanitize_llm_json_str(tmp or "")
            safe = re.sub(r"[^\x20-\x7E\t]", " ", safe)
            data = json.loads(safe)
    except Exception:
        return {}

    node_id = data.get("node_id")

    # --- Node-level flat row (pass the normalized dict) ---
    node_row = flatten_llm_to_row(data)

    # --- Players (force up to 10) ---
    players = []
    results = data.get("results", [])
    # Handle both dict-style and list-style results
    if isinstance(results, dict):
        node_players = results.get("node_players", [])
    else:
        node_players = _find_result(results, "node_players") or []

    node_path = data.get("path", "")

    for i in range(10):
        p = node_players[i] if i < len(node_players) else {}
        players.append({
            "node_id": node_id or "",
            "path": node_path,
            "rank": p.get("rank", i + 1 if not p else p.get("rank")),
            "name": p.get("name", ""),
            "country": p.get("country", ""),
            "type": p.get("type", ""),
            # Match Players sheet columns; accept either new player_fyXX_* or legacy fyXX_node_* keys
            "player_fy23_revenue_usd_bn": p.get(
                "player_fy23_revenue_usd_bn",
                p.get("fy23_node_revenue_usd_bn", ""),
            ),
            "player_fy24_revenue_usd_bn": p.get(
                "player_fy24_revenue_usd_bn",
                p.get("fy24_node_revenue_usd_bn", ""),
            ),
            "player_fy25_revenue_usd_bn": p.get(
                "player_fy25_revenue_usd_bn",
                p.get("fy25_node_revenue_usd_bn", ""),
            ),
            "player_fy23_ebitda_usd_bn": p.get(
                "player_fy23_ebitda_usd_bn",
                "",
            ),
            "player_fy24_ebitda_usd_bn": p.get(
                "player_fy24_ebitda_usd_bn",
                "",
            ),
            "player_fy25_ebitda_usd_bn": p.get(
                "player_fy25_ebitda_usd_bn",
                "",
            ),
            "player_fy23_ebitda_margin_pct": p.get(
                "player_fy23_ebitda_margin_pct",
                "",
            ),
            "player_fy24_ebitda_margin_pct": p.get(
                "player_fy24_ebitda_margin_pct",
                "",
            ),
            "player_fy25_ebitda_margin_pct": p.get(
                "player_fy25_ebitda_margin_pct",
                "",
            ),
            "confidence_score": p.get("confidence_score", ""),
            "attribution_basis": p.get("attribution_basis", ""),
        })

    # --- Proxies (force 3) ---
    proxies = []
    # results already extracted above, reuse it
    if isinstance(results, dict):
        node_proxies = results.get("pure_play_estimates", [])
    else:
        node_proxies = _find_result(results, "pure_play_estimates") or []
    for i in range(3):
        pp = node_proxies[i] if i < len(node_proxies) else {}
        proxies.append({
            "node_id": node_id or "",
            "name": pp.get("name", ""),
            "country": pp.get("country", ""),
            "type": pp.get("type", ""),
            "proxy_reason": pp.get("proxy_reason", ""),
            # Match Proxies sheet columns; accept either new proxy_fyXX_* or legacy fyXX_* keys
            "proxy_fy23_revenue_usd_bn": pp.get(
                "proxy_fy23_revenue_usd_bn",
                pp.get("fy23_revenue_usd_bn", ""),
            ),
            "proxy_fy24_revenue_usd_bn": pp.get(
                "proxy_fy24_revenue_usd_bn",
                pp.get("fy24_revenue_usd_bn", ""),
            ),
            "proxy_fy25_revenue_usd_bn": pp.get(
                "proxy_fy25_revenue_usd_bn",
                pp.get("fy25_revenue_usd_bn", ""),
            ),
            "proxy_fy23_ebitda_usd_bn": pp.get(
                "proxy_fy23_ebitda_usd_bn",
                "",
            ),
            "proxy_fy24_ebitda_usd_bn": pp.get(
                "proxy_fy24_ebitda_usd_bn",
                "",
            ),
            "proxy_fy25_ebitda_usd_bn": pp.get(
                "proxy_fy25_ebitda_usd_bn",
                "",
            ),
            "proxy_fy23_ebitda_margin_pct": pp.get(
                "proxy_fy23_ebitda_margin_pct",
                "",
            ),
            "proxy_fy24_ebitda_margin_pct": pp.get(
                "proxy_fy24_ebitda_margin_pct",
                "",
            ),
            "proxy_fy25_ebitda_margin_pct": pp.get(
                "proxy_fy25_ebitda_margin_pct",
                "",
            ),
            "confidence_score": pp.get("confidence_score", ""),
        })

    # --- Evidence (optional, top-level) ---
    evidence_rows = []
    evidence_list = data.get("evidence") or []
    if isinstance(evidence_list, list):
        for ev in evidence_list:
            if not isinstance(ev, dict):
                continue
            evidence_rows.append(
                {
                    "node_id": node_id or "",
                    "evidence_id": ev.get("evidence_id"),
                    "title": ev.get("title"),
                    "url": ev.get("url"),
                    "type": ev.get("type"),
                    "snippet": ev.get("snippet"),
                    "confidence_score": ev.get("confidence_score"),
                }
            )

    # --- Evidence map (optional, top-level) ---
    evidence_map_rows = []
    evidence_map_list = data.get("evidence_map") or []
    if isinstance(evidence_map_list, list):
        for em in evidence_map_list:
            if not isinstance(em, dict):
                continue
            evidence_map_rows.append(
                {
                    "node_id": node_id or "",
                    "field": em.get("field"),
                    "supported_by": em.get("supported_by", []),
                }
            )

    return {
        "nodes_row": node_row,
        "players_rows": players,
        "proxies_rows": proxies,
        "evidence_rows": evidence_rows,
        "evidence_map_rows": evidence_map_rows,
    }

# -----------------------------------------------------------------------------
# n8n write-agent config (edit via .streamlit/secrets.toml in production)
# -----------------------------------------------------------------------------
N8N_WRITE_URL = st.secrets.get(
    "N8N_WRITE_URL",
    "https://fpgconsulting.app.n8n.cloud/webhook/write_agent",
)
WEBHOOK_SECRET = st.secrets.get("WEBHOOK_SECRET", "")

# Remember the initial write URL (live/secret) so we can restore it in Live mode
WRITE_URL_DEFAULT = N8N_WRITE_URL

st.set_page_config(page_title="n8n Echo Query Tester", layout="wide")
st.markdown(
    """
    <style>
    /* === Global dark theme override ===================================== */
    html, body, .stApp {
        background-color: #111418 !important;
        color: #e5e5e5 !important;
        font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    /* Root layout containers */
    .main, div.block-container {
        background-color: #111418 !important;
    }

    /* Sidebar */
    section[data-testid="stSidebar"] {
        background-color: #111418 !important;
        border-right: 1px solid #2a2d33 !important;
    }

    /* Remove stray decoration strip */
    div[data-testid="stDecoration"] {
        background-color: #111418 !important;
    }

    /* Headings */
    h1, h2, h3, h4, h5, h6 {
        color: #f2f2f2;
        font-weight: 600;
        letter-spacing: 0.02em;
    }

    /* Core form controls -------------------------------------------------- */
    /* Text & number inputs */
    [data-testid="stTextInput"] input,
    [data-testid="stNumberInput"] input {
        background-color: #181c22;
        color: #f5f7fa;
        border: 1px solid #2a2d33;
    }

    /* Text areas */
    [data-testid="stTextArea"] textarea {
        background-color: #181c22;
        color: #f5f7fa;
        border: 1px solid #2a2d33;
    }

    /* Select boxes & radios (outer shells) */
    [data-testid="stSelectbox"] > div > div,
    [data-testid="stRadio"] > div {
        background-color: #181c22;
        color: #f5f7fa;
        border-radius: 4px;
        border: 1px solid #2a2d33;
    }

    /* File uploader drop zone */
    div[data-testid="stFileUploaderDropzone"] {
        background-color: #181c22 !important;
        border: 1px dashed #3a3f4b !important;
        color: #e5e7eb !important;
    }

    /* File uploader label text */
    div[data-testid="stFileUploader"] label {
        color: #e5e7eb !important;
    }

    /* Dropdown menu body and options */
    div[data-baseweb="select"] {
        background-color: #181c22 !important;
        color: #f5f7fa !important;
    }
    ul[role="listbox"] li {
        background-color: #181c22 !important;
        color: #f5f7fa !important;
    }

    /* Buttons – keep Streamlit visual language, normalise radius */
    button[kind="secondary"],
    button[kind="primary"] {
        border-radius: 4px;
    }

    /* Generic soft cards on right-hand panel ------------------------------ */
    .soft-card {
        background: rgba(245, 247, 250, 0.04);
        border: 1px solid rgba(255,255,255,0.07);
        border-radius: 10px;
        padding: 0.75rem 0.85rem;
    }

    .badge {
        background: rgba(255,255,255,0.06);
        border: 1px solid rgba(255,255,255,0.12);
        padding: .15rem .45rem;
        border-radius: 8px;
        font-size: .8rem;
    }

    /* Tables */
    table.dataframe {
        border-collapse: collapse;
        width: 100%;
        background: #1d1f24;
    }
    table.dataframe th {
        background-color: #23262d;
        color: #f1f5f9;
        text-align: center;
        font-weight: 600;
        padding: 8px;
        border-bottom: 1px solid #2f323a;
    }
    table.dataframe td {
        text-align: center;
        padding: 6px;
        border-bottom: 1px solid #2a2d33;
        color: #e5e7eb;
    }
    table.dataframe tr:nth-child(even) {
        background-color: #202328;
    }

    /* Alerts / info boxes */
    div[data-testid="stAlert"] {
        background-color: #20252b !important;
        border-left: 4px solid #4683ea !important;
        color: #e5e5e5 !important;
    }

    /* Metrics & dividers */
    hr { border-top: 1px solid #2b2e35; }

    /* Two-pane layout helpers: left = controls, right = results ------------- */
    .left-pane {
        border-right: 1px solid rgba(255, 255, 255, 0.08);
        padding-right: 0.75rem;
        margin-right: 0.5rem;
    }

    .results-pane {
        background: rgba(245, 247, 250, 0.04);
        border: 1px solid rgba(255, 255, 255, 0.07);
        border-radius: 10px;
        padding: 0.75rem 0.9rem;
        box-sizing: border-box;
        /* let the card sit flush with the column */
    }
    .stMetric { background: none !important; }
    [data-testid="stMetricValue"] { color: #eaeaea; }

    /* Expander summary text */
    details summary {
        color: #dcdcdc;
        font-weight: 600;
    }
    /* ===== Button colour overrides (surgical) ===== */
    /* GREEN — Run query */
    div[data-testid="stButton"][data-streamlit-key="btn_run_query"] > button {
        background-color: #1f7a4d !important;
        border-color: #1f7a4d !important;
        color: #ffffff !important;
    }
    div[data-testid="stButton"][data-streamlit-key="btn_run_query"] > button:hover {
        background-color: #249463 !important;
    }

    /* AMBER — Check for completed result */
    div[data-testid="stButton"][data-streamlit-key="btn_check_completed_result"] > button {
        background-color: #b7791f !important;
        border-color: #b7791f !important;
        color: #ffffff !important;
    }
    div[data-testid="stButton"][data-streamlit-key="btn_check_completed_result"] > button:hover {
        background-color: #d69e2e !important;
    }

    /* BLUE — Secondary actions */
    div[data-testid="stButton"][data-streamlit-key="btn_load_selected_run"] > button,
    div[data-testid="stButton"][data-streamlit-key="btn_load_run_by_id"] > button,
    div[data-testid="stButton"][data-streamlit-key="qc_apply_button"] > button {
        background-color: #2b6cb0 !important;
        border-color: #2b6cb0 !important;
        color: #ffffff !important;
    }
    div[data-testid="stButton"][data-streamlit-key="btn_load_selected_run"] > button:hover,
    div[data-testid="stButton"][data-streamlit-key="btn_load_run_by_id"] > button:hover,
    div[data-testid="stButton"][data-streamlit-key="qc_apply_button"] > button:hover {
        background-color: #3182ce !important;
    }

    /* Disabled buttons stay muted */
    div[data-testid="stButton"] > button:disabled {
        opacity: 0.55 !important;
    }
    
    </style>
    """,
    unsafe_allow_html=True,
)

# === Configuration - replace with your webhook(s) ===
TEST_WEBHOOK_URL = "https://fpgconsulting.app.n8n.cloud/webhook-test/echo_agent"
LIVE_WEBHOOK_URL = "https://fpgconsulting.app.n8n.cloud/webhook/echo_agent"

# Explicit write-agent endpoints (do NOT derive by string replace)
WRITE_TEST_WEBHOOK_URL = "https://fpgconsulting.app.n8n.cloud/webhook-test/write_agent"
WRITE_LIVE_WEBHOOK_URL = "https://fpgconsulting.app.n8n.cloud/webhook/write_agent"

# Explicit status endpoints for async result retrieval (echo_status workflow)
STATUS_TEST_WEBHOOK_URL = "https://fpgconsulting.app.n8n.cloud/webhook-test/echo_status"
STATUS_LIVE_WEBHOOK_URL = "https://fpgconsulting.app.n8n.cloud/webhook/echo_status"

N8N_WEBHOOK_URL = LIVE_WEBHOOK_URL
N8N_STATUS_URL = STATUS_LIVE_WEBHOOK_URL

# === Secret retrieval (Streamlit Secrets preferred) ===
try:
    WEBHOOK_SECRET = st.secrets.get("WEBHOOK_SECRET", None)
except Exception:
    WEBHOOK_SECRET = None

if not WEBHOOK_SECRET:
    WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "WIBBLE")


def compute_nodes_to_query(df_tax: pd.DataFrame, selected_path_or_name: str, rel_depth: int):
    """
    df_tax: normalized DataFrame with columns like ['node_id','parent_id','level','path','display_name']
    selected_path_or_name: string (either path or display_name)
    rel_depth: int (0 => only selected node, 1 => include immediate children, etc)
    Returns: list of dicts: [{node_id, path, display_name, level, parent_id}, ...]
    """
    if df_tax is None or df_tax.empty:
        return []

    # Prefer exact path match, then exact display_name match, then substring match
    root_row = None
    if "path" in df_tax.columns:
        exact_path = df_tax[df_tax["path"] == selected_path_or_name]
        if not exact_path.empty:
            root_row = exact_path.iloc[0]

    if root_row is None and "display_name" in df_tax.columns:
        exact_name = df_tax[df_tax["display_name"] == selected_path_or_name]
        if not exact_name.empty:
            root_row = exact_name.iloc[0]

    if root_row is None and "display_name" in df_tax.columns:
        matches = df_tax[df_tax["display_name"].str.contains(selected_path_or_name, na=False, case=False)]
        if not matches.empty:
            root_row = matches.iloc[0]

    if root_row is None and "path" in df_tax.columns:
        # fallback: substring match on path
        matches = df_tax[df_tax["path"].str.contains(selected_path_or_name, na=False, case=False)]
        if not matches.empty:
            root_row = matches.iloc[0]

    if root_row is None:
        raise ValueError("Selected node not found in taxonomy dataframe")

    root_id = root_row.get("node_id")
    root_path = root_row.get("path", "")

    # Build parent -> children map if parent_id exists
    children_map = {}
    if "parent_id" in df_tax.columns and "node_id" in df_tax.columns:
        for _, r in df_tax.iterrows():
            pid = r.get("parent_id", None)
            nid = r.get("node_id")
            children_map.setdefault(pid, []).append(nid)
    else:
        children_map = None

    results = []

    if children_map:
        id_to_row = {r["node_id"]: r for _, r in df_tax.to_dict(orient="index").items() if r.get("node_id") is not None}
        q = deque()
        q.append((root_id, 0))
        visited = set()

        while q:
            current_id, depth = q.popleft()
            if current_id in visited:
                continue
            visited.add(current_id)
            row = df_tax[df_tax["node_id"] == current_id]
            if row.empty:
                continue
            row = row.iloc[0]
            results.append(
                {
                    "node_id": row.get("node_id"),
                    "path": row.get("path"),
                    "display_name": row.get("display_name"),
                    "level": int(row["level"]) if "level" in row and pd.notna(row["level"]) else None,
                    "parent_id": row.get("parent_id"),
                }
            )
            if depth < rel_depth:
                for child_id in children_map.get(current_id, []):
                    q.append((child_id, depth + 1))
    else:
        # Fallback: use path prefix matching and compute relative depth by separators
        prefix = root_path or selected_path_or_name
        matched = df_tax[df_tax["path"].str.startswith(prefix, na=False)]
        root_depth = prefix.count(" > ")

        def rel_d(row_path: str) -> int:
            return row_path.count(" > ") - root_depth

        for _, row in matched.iterrows():
            if rel_d(row.get("path", "")) <= rel_depth:
                results.append(
                    {
                        "node_id": row.get("node_id"),
                        "path": row.get("path"),
                        "display_name": row.get("display_name"),
                        "level": int(row["level"]) if "level" in row and pd.notna(row["level"]) else None,
                        "parent_id": row.get("parent_id"),
                    }
                )

    return results

# --- Top-level page title (full-width) ---------------------------------------
st.markdown("# Aerospace & Defence Research Agent")
st.caption(
    "End-to-end, auditable research workspace for Aerospace & Defence taxonomy nodes. "
    "Configure an analysis on the left, review structured output and evidence on the right, "
    "then commit clean rows to the database via n8n."
)
st.divider()

# --- Cosmetic: tint Step 1 card (first bordered container in left pane) --------
st.markdown(
    """
        <style>
            /* Step 1 = first bordered container inside left-pane */
            .left-pane div[data-testid="stVerticalBlockBorderWrapper"]:nth-of-type(1){
                border: 1px solid rgba(148, 163, 184, 0.22) !important;
                border-radius: 14px !important;
                box-shadow: 0 8px 22px rgba(0,0,0,0.25) !important;
                background: linear-gradient(180deg, rgba(30,58,138,0.18), rgba(15,23,42,0.02)) !important;
            }

            /* === Step 6 shading ============================================ */
            .step-6-anchor + div[data-testid="stVerticalBlockBorderWrapper"] {
                background: linear-gradient(
                    180deg,
                    rgba(16, 185, 129, 0.10),
                    rgba(16, 185, 129, 0.03)
                ) !important;
                border: 1px solid rgba(16, 185, 129, 0.35) !important;
                border-radius: 14px !important;
                box-shadow: 0 8px 22px rgba(0,0,0,0.25) !important;
            }
        </style>
    """,
    unsafe_allow_html=True,
)

# --- Balanced 50/50 layout for full app --------------------------------------
left_col, right_col = st.columns(2, gap="large")
 
# Keep all interactive setup and query logic strictly inside left_col.
# Wrap in a left-pane container so we can visually separate it from the results.
with left_col:

    st.markdown('<div class="left-pane">', unsafe_allow_html=True)

    # Track previous node selection so we can reset extra_context when node changes
    if "prev_selected_node" not in st.session_state:
        st.session_state.prev_selected_node = None

    # --- Left-side control surface ---
    st.markdown("### Control Panel")
    with st.container(border=True):
                def _do_write_now(payload_all: dict, payload_hash: str | None):
                    st.session_state["write_inflight"] = True
                    try:
                        headers = {"Content-Type": "application/json"}
                        if WEBHOOK_SECRET:
                            headers["X-Webhook-Secret"] = WEBHOOK_SECRET

                        env_mode = st.session_state.get("env_mode", "live")
                        target_write_url = WRITE_TEST_WEBHOOK_URL if env_mode == "test" else N8N_WRITE_URL

                        resp = requests.post(target_write_url, headers=headers, json=payload_all, timeout=60)
                        ok = 200 <= resp.status_code < 300
                        msg = (resp.text[:600] if resp.text else "OK")

                        st.session_state["write_status"] = {
                            "ok": ok,
                            "status_code": resp.status_code,
                            "message": msg,
                            "payload_hash": payload_hash,
                        }
                        if ok and payload_hash:
                            st.session_state["last_written_hash"] = payload_hash
                    except Exception as e:
                        st.session_state["write_status"] = {
                            "ok": False,
                            "status_code": None,
                            "message": str(e),
                            "payload_hash": payload_hash,
                        }
                    finally:
                        st.session_state["write_inflight"] = False
    st.markdown("#### Step 1: Select the taxonomy node to analyse")
    st.caption(
        "Select the node you wish to analyse from the pre-loaded taxonomy Excel file. "
        "Use the PATH column and the node selector / navigation buttons to move through the tree."
    )
    st.markdown("&nbsp;")

    st.caption(
        "Use this panel to select taxonomy nodes, adjust global and node-level context, "
        "choose the Perplexity model, set environment (Live/Test), and trigger new analyses "
        "via the n8n echo_agent workflow."
    )
    st.divider()

    # 1. Taxonomy Upload and Selection
    node_choice = None
    df = None

    LOCAL_TAXONOMY_FILE = None
    patterns = [
        "./Aero and Defence Taxonomy Structure for n8n.xlsx",
        "./Aero and Defence Taxonomy Structure for n8n DATA for charts.xlsx",
        "./taxonomy*.xlsx",
        "./data/*.xlsx",
        "./*.xlsx",
    ]
    for p in patterns:
        matches = glob.glob(p)
        if matches:
            LOCAL_TAXONOMY_FILE = matches[0]
            break

    if LOCAL_TAXONOMY_FILE:
        try:
            df = pd.read_excel(LOCAL_TAXONOMY_FILE, sheet_name=0)
            st.info(f"Auto-loaded taxonomy from repo file: {os.path.basename(LOCAL_TAXONOMY_FILE)}")
        except Exception as e:
            st.warning(f"Auto-load failed ({LOCAL_TAXONOMY_FILE}): {e}")
            df = None

        uploaded = st.file_uploader("Upload taxonomy Excel (.xlsx) - or leave blank to use auto-detected file", type=["xlsx"])

        # Only read the uploaded file if auto-load did not already populate df
        if uploaded and df is None:
            try:
                df = pd.read_excel(uploaded, sheet_name=0)
            except Exception as e:
                st.error(f"Failed to read Excel: {e}")
                df = None

        if df is not None:
            # Taxonomy loaded; let user pick which column holds node labels
            candidate_cols = [
                c
                for c in df.columns
                if c and str(c).lower() in ("hierarchy", "node", "node_path", "name", "title")
            ]
            default_col = candidate_cols[0] if candidate_cols else df.columns[0]

            # Prefer PATH as the default column when present (standard A&D taxonomy file)
            try:
                lower_map = {str(c).lower(): c for c in df.columns}
                if "path" in lower_map:
                    default_col = lower_map["path"]
            except Exception:
                # Fallback safely to previous behaviour
                default_col = candidate_cols[0] if candidate_cols else df.columns[0]
            selected_col = st.selectbox(
                "Which column contains the taxonomy node labels?",
                df.columns,
                index=list(df.columns).index(default_col),
            )
            try:
                sample_vals = df[selected_col].dropna().astype(str).unique().tolist()
            except Exception:
                sample_vals = df[selected_col].dropna().astype(str).head(100).tolist()

            # Maintain a stable index into the taxonomy list so you can step
            # through nodes without re-scrolling from the top each time.
            if "taxonomy_node_index" not in st.session_state:
                # Default to the military uav path when present
                default_index = 0
                try:
                    for i_val, val in enumerate(sample_vals):
                        s = str(val).lower()
                        # Heuristic match: any PATH containing both "military" and "uav"
                        if "military" in s and "uav" in s:
                            default_index = i_val
                            break
                except Exception:
                    default_index = 0

                st.session_state["taxonomy_node_index"] = default_index

            # Clamp index in case the list size changes (different file, filter, etc.)
            if sample_vals:
                st.session_state["taxonomy_node_index"] = min(
                    st.session_state["taxonomy_node_index"], len(sample_vals) - 1
                )
            else:
                st.session_state["taxonomy_node_index"] = 0

            nav_prev, nav_next = st.columns([1, 1])
            with nav_prev:
                if st.button("◀ Previous node"):
                    new_i = max(0, st.session_state["taxonomy_node_index"] - 1)
                    st.session_state["taxonomy_node_index"] = new_i
                    # force the selectbox widget to the new value
                    if sample_vals:
                        st.session_state["taxonomy_node_selectbox"] = sample_vals[new_i]
            with nav_next:
                if st.button("Next node ▶"):
                    new_i = min(len(sample_vals) - 1, st.session_state["taxonomy_node_index"] + 1)
                    st.session_state["taxonomy_node_index"] = new_i
                    # force the selectbox widget to the new value
                    if sample_vals:
                        st.session_state["taxonomy_node_selectbox"] = sample_vals[new_i]

            node_choice = st.selectbox(
                "Choose taxonomy node",
                sample_vals,
                index=st.session_state["taxonomy_node_index"],
                key="taxonomy_node_selectbox",
            )

            # If the user picks a node directly from the dropdown, sync the index immediately
            if node_choice in sample_vals:
                new_index = sample_vals.index(node_choice)
                if new_index != st.session_state["taxonomy_node_index"]:
                    st.session_state["taxonomy_node_index"] = new_index

            # If the node has changed since last render, clear any extra_context
            if node_choice is not None and node_choice != st.session_state.prev_selected_node:
                st.session_state["extra_context"] = ""
                st.session_state.prev_selected_node = node_choice
        else:
            st.info("Auto-detected no taxonomy file - upload an Excel file to select taxonomy nodes.")
            node_choice = st.text_input("Default taxonomy node (used when no Excel uploaded):", value="DEFAULT_NODE")

    st.markdown("&nbsp;")
    with st.container(border=True):
        st.markdown("#### Step 2: Review the default prompts (optional)")
        st.caption(
            "Review or tweak the global and core JSON prompts that drive the analysis. "
            "In most cases the defaults are sufficient; edit these only when you need to steer behaviour."
        )

        # === Query options / Prompt editor / Run ==="
        st.subheader("Query options")

        # Hidden defaults used by the workflow
        DEFAULT_REQUIRED_FIELDS = [
            "summary",
            "node_financials",
            "node_players",
            "pure_play_estimates",
            "methodology_summary",
            "financial_commentary",
            "player_commentary",
            "scope_context",
            "sources"
        ]
        DEFAULT_QUERY_DEPTH = 5

        # 1) Global context (editable, pre-filled)
        # Auto-reload global context prompt each rerun (no caching)
        def _load_global_context():
            try:
                with open("prompt_global_context.txt", "r", encoding="utf-8") as f:
                    return f.read()
            except Exception:
                return "ERROR: prompt_global_context.txt not found. Please add file."

        default_global_context = _load_global_context()
        with st.expander("Global context (applies to all nodes in this run)", expanded=False):
            global_context = st.text_area(
                "Global context (applies to all nodes in this run)",
                value=default_global_context,
                height=260
            )

        # 2. Prompt editor (default strict JSON prompt)
        st.markdown("### Prompt editor (core task – applies to all nodes)")
        st.markdown(
            "The core JSON-schema prompt controls the detailed structure of each response. "
            "Most runs can use the default; expand below only if you need to inspect or tweak it."
        )

        # Keep everything after this indented under `with left_col:`
        # Auto-reload core JSON prompt each rerun (no caching)
        def _load_core_prompt():
            try:
                with open("prompt_core_json.txt", "r", encoding="utf-8") as f:
                    return f.read()
            except Exception:
                return "ERROR: prompt_core_json.txt not found. Please add file."

        def _load_qc_prompt():
            try:
                with open("prompt_qc_critic.txt", "r", encoding="utf-8") as f:
                    return f.read()
            except Exception:
                return "ERROR: prompt_qc_critic.txt not found. Please add file."

        default_prompt = _load_core_prompt()




        with st.expander("Show / edit core JSON prompt", expanded=False):
            prompt_text = st.text_area(
                "Prompt text (core analytical instruction)",
                value=default_prompt,
                height=550
            )

    # 3. Extra context (node/run-level, shorter than global)
    # NOTE: the Extra context input is now positioned adjacent to the run controls
    # (immediately below the Environment selector) for better visibility and workflow.

    # 3. Model & technical settings (kept inside left column)
    st.markdown("&nbsp;")
    with st.container(border=True):
        st.markdown("#### Step 3: Choose model and run settings")
        st.caption(
            "Pick the Perplexity Sonar model, temperature, token budget, timeout, and environment for this run."
        )

        st.markdown("""
        Choose a **Perplexity Sonar** model variant and adjust parameters to balance speed, analytical depth, and cost.

        **Model Options**
        - **sonar (default)** - Fast, concise, best for summaries or debugging.
        - **sonar-pro** - Slower but performs multi-document reasoning for better numeric coherence.
        - **sonar-deep-research** - Most thorough, cross-validates sources and produces full analytical writeups.

        > *Note:* higher models and larger token counts may cost more per request.  
        See [Perplexity API pricing](https://docs.perplexity.ai/docs/pricing) for current rates.
        """)

        # Allow QC Apply to steer the default run settings for the *next* query
        model_variants = ["sonar-pro", "sonar", "sonar-deep-research"]
        qc_model = st.session_state.get("qc_override_model")
        qc_temp = st.session_state.get("qc_override_temperature")
        qc_tokens = st.session_state.get("qc_override_max_tokens")

        default_model_index = 0
        if isinstance(qc_model, str) and qc_model in model_variants:
            default_model_index = model_variants.index(qc_model)

        # --- Model selection with QC override support ---
        model_options = ["sonar-pro", "sonar", "sonar-deep-research"]

        # Allow QC critic to steer the default model for the *next* run when overrides are present
        qc_override_model = st.session_state.get("qc_override_model")
        default_model_index = 0
        if isinstance(qc_override_model, str) and qc_override_model in model_options:
            default_model_index = model_options.index(qc_override_model)

        _model_choice = st.selectbox(
            "Select model variant:",
            model_options,
            index=default_model_index,
            key="model_choice",
            help="Select a model tuned for your analysis depth. Deep-research is most capable but slower and costlier (e.g., $1+ per run)."
        )

        # Map to model identifiers
        if _model_choice.startswith("sonar-pro"):
            model_name = "sonar-pro"
        elif _model_choice.startswith("sonar-deep-research"):
            model_name = "sonar-deep-research"
        else:
            model_name = "sonar"

        # Updated robust defaults for analytical consistency
        DEFAULT_MODEL_NAME = model_name

        # Let QC critic adjust default temperature / max_tokens when available
        qc_override_temp = st.session_state.get("qc_override_temperature")
        qc_override_max_tokens = st.session_state.get("qc_override_max_tokens")

        DEFAULT_TEMPERATURE = 0.1
        if isinstance(qc_override_temp, (int, float)):
            # Clamp into slider range
            DEFAULT_TEMPERATURE = max(0.0, min(1.0, float(qc_override_temp)))

        DEFAULT_MAX_TOKENS = 10000
        try:
            if qc_override_max_tokens is not None:
                DEFAULT_MAX_TOKENS = int(qc_override_max_tokens)
        except Exception:
            # Fall back safely if QC suggestion is non-numeric
            DEFAULT_MAX_TOKENS = 10000

        temperature = st.slider(
            "Temperature (analytical creativity)",
            min_value=0.0, max_value=1.0, value=DEFAULT_TEMPERATURE, step=0.05,
            help="Higher = more flexible reasoning and interpolation. 0.35 is ideal for analytical estimation."
        )

        env_mode = st.session_state.get("env_mode", "live")
        if env_mode == "live" and temperature > 0.5:
            st.warning(
                "WARNING: You are in LIVE mode with a high temperature - "
                "results may vary and cost more. "
                "Use lower temperatures for reproducibility and lower cost."
            )

        max_tokens = st.number_input(
            "Max tokens (response length)",
            min_value=256, max_value=40000, value=DEFAULT_MAX_TOKENS,
            help="Higher allows longer structured analyses and full financial tables. Costs scale with token count."
        )

        # Persist model settings for downstream logging / write_agent
        st.session_state["model_name"] = model_name
        st.session_state["temperature"] = float(temperature)
        st.session_state["max_tokens"] = int(max_tokens)

        # === Timeout Configuration ===
        # Store recommended timeouts in milliseconds (used by n8n / HTTP node),
        # but expose them in the UI as seconds for readability.
        recommended_timeout_ms = {
            "sonar": 120000,
            "sonar-pro": 180000,
            "sonar-deep-research": 300000,  # deeper research needs a longer default
        }

        default_timeout_ms = recommended_timeout_ms.get(model_name, 120000)
        default_timeout_s = int(default_timeout_ms / 1000)

        timeout_s = st.number_input(
            "Timeout (seconds)",
            min_value=30,
            max_value=600,
            value=default_timeout_s,
            step=10,
            help=f"Recommended: {default_timeout_s} s for {model_name}. Deeper research takes longer and uses more tokens.",
        )

        # Convert back to ms for the payload we send to n8n / echo_agent
        timeout_ms = int(timeout_s * 1000)

        st.caption(
            "TIP: Increase this if your deeper research calls time out. "
            "Actual limits may still be capped by infrastructure (e.g. reverse proxy / n8n). "
            "Costs scale roughly with total tokens used."
        )

        priority = st.selectbox(
            "Priority",
            ["normal", "high", "low"],
            index=0,
            help="Tag the job priority; can be used by downstream schedulers or model routing."
        )

        # Environment selector (Live by default), positioned just above run controls
        env_label = st.radio(
            "Environment",
            ["Live", "Test"],
            index=0,
            horizontal=True,
            help="Choose whether to send this run to the Live or Test n8n endpoint.",
        )
        env_mode = "live" if env_label.lower().startswith("live") else "test"
        st.session_state["env_mode"] = env_mode

        st.markdown("**Extra context (optional, node-level)**")
        st.caption(
            "Provide additional analytical guidance or paste quality-control suggestions from a previous run. "
            "This affects only the current node and can materially improve differentiation and historical fidelity."
        )
        extra_context = st.text_area(
            " ",
            value=st.session_state.get("extra_context", ""),
            key="extra_context",
            height=160,
            placeholder=(
                "Add node-specific constraints, nuances, exclusions, scoping notes, or "
                "paste QC recommendations here."
            ),
        )

        if env_mode == "live":
            N8N_WEBHOOK_URL = LIVE_WEBHOOK_URL
            N8N_STATUS_URL = STATUS_LIVE_WEBHOOK_URL
            # In LIVE mode, keep using whatever write URL was configured (secret or default live)
            N8N_WRITE_URL = WRITE_URL_DEFAULT or WRITE_LIVE_WEBHOOK_URL
        else:
            N8N_WEBHOOK_URL = TEST_WEBHOOK_URL
            N8N_STATUS_URL = STATUS_TEST_WEBHOOK_URL
            # In TEST mode, always use the explicit test write_agent webhook
            N8N_WRITE_URL = WRITE_TEST_WEBHOOK_URL


    # Step 4 – Run and monitor status
    st.markdown("&nbsp;")
    with st.container(border=True):

        st.markdown("#### Step 4: Run the query and monitor status")
        st.caption(
            "Trigger a new analysis and poll for completed results before deciding whether to iterate again or commit to Sheets."
        )
        st.caption(
            "ℹ️ **About timeouts:** For deep or complex taxonomy nodes, you may see "
            "**HTTP 524 (Gateway timeout)**. This is expected and does *not* mean the run failed. "
            "The analysis often continues in the background — use **Load existing run** or "
            "**Check status** to retrieve results later."
        )




        
        # Run / Preview controls (side-by-side)
        run_col1, run_col2 = st.columns([1, 1])

        # Button to poll echo_status workflow for a completed result by run_id
        with run_col1:
            if st.button("Check for completed result", key="btn_check_completed_result"):
                last_run_id = st.session_state.get("last_run_id")
                if not last_run_id:
                    st.warning("No Run ID available. Trigger a query first to generate a run_id.")
                else:
                    st.info(f"Checking status for Run ID: `{last_run_id}`")
                    headers = {"X-Webhook-Secret": WEBHOOK_SECRET, "Content-Type": "application/json"}
                    try:
                        status_resp = requests.post(
                            N8N_STATUS_URL,
                            json={"run_id": last_run_id},
                            headers=headers,
                            timeout=30,
                        )
                        st.write("Status check HTTP code:", status_resp.status_code)
                        try:
                            sj = status_resp.json()
                        except Exception:
                            st.error("Status endpoint did not return JSON.")
                            st.text(status_resp.text[:1000])
                        else:
                            # Treat any non-pending status as a completed payload.
                            # echo_status typically returns either:
                            #   {"status":"pending"}  OR
                            #   {"status":"Completed"/"completed", "run_id":..., "result_raw":"<json string>", ...}
                            status_val = ""
                            if isinstance(sj, dict) and "status" in sj:
                                status_val = str(sj.get("status", "")).lower()

                            if isinstance(sj, dict) and status_val == "pending":
                                st.info("Analysis is still in progress inside n8n. Try again later.")
                            else:
                                # Store the entire response; the right column will unwrap result_raw if present.
                                st.session_state["latest_response"] = sj
                                st.session_state["last_response"] = (
                                    sj if not isinstance(sj, list) else sj[0]
                                )
                                st.success("Retrieved completed analysis from echo_status endpoint.")
                    except Exception as e:
                        st.error(f"Status check failed: {e}")

        with run_col2:
            if st.button("Run query", key="btn_run_query"):
                if not node_choice:
                    st.error("No taxonomy node selected. Upload an Excel and select a node first.")
                else:
                    # Resolve actual node fields from the uploaded taxonomy (best-effort)
                    node_id_val = None
                    node_path = str(node_choice)
                    display_name = str(node_choice)
                    level_val = None
                    parent_id_val = None
                    node_row = None

                    if df is not None:
                        try:
                            # 1) Prefer exact match on the user-selected column (selected_col) if available
                            if 'selected_col' in locals() and selected_col in df.columns:
                                matches = df[df[selected_col].astype(str) == str(node_choice)]
                                if not matches.empty:
                                    node_row = matches.iloc[0]

                            # 2) Try common column names for exact matches
                            if node_row is None:
                                for colname in ('path', 'display_name', 'name', 'title'):
                                    if colname in df.columns:
                                        matches = df[df[colname].astype(str) == str(node_choice)]
                                        if not matches.empty:
                                            node_row = matches.iloc[0]
                                            break

                            # 3) Substring fallback on the selected column
                            if node_row is None and 'selected_col' in locals() and selected_col in df.columns:
                                try:
                                    matches = df[df[selected_col].astype(str).str.contains(str(node_choice), na=False, case=False)]
                                    if not matches.empty:
                                        node_row = matches.iloc[0]
                                except Exception:
                                    pass

                            # 4) Path-prefix fallback
                            if node_row is None and 'path' in df.columns:
                                matched = df[df['path'].astype(str).str.startswith(str(node_choice))]
                                if not matched.empty:
                                    node_row = matched.iloc[0]

                            # 5) Extract fields if we found a row
                            if node_row is not None:
                                node_id_val = node_row.get('node_id') or node_row.get('id') or node_row.get('nodeId') or node_row.get('NodeID')
                                # If no explicit id column, derive a stable id from the dataframe index (best-effort)
                                if not node_id_val:
                                    try:
                                        idx = int(node_row.name)
                                        node_id_val = f"N{idx:05d}"
                                    except Exception:
                                        node_id_val = None

                                node_path = node_row.get('path') or node_path
                                if 'selected_col' in locals() and selected_col in node_row:
                                    display_name = node_row.get('display_name') or node_row.get(selected_col) or display_name
                                else:
                                    display_name = node_row.get('display_name') or display_name

                                try:
                                    level_val = int(node_row['level']) if 'level' in node_row and pd.notna(node_row['level']) else level_val
                                except Exception:
                                    level_val = level_val

                                # Be tolerant to different header spellings in the taxonomy sheet
                                parent_id_val = (
                                    node_row.get('parent_id')
                                    or node_row.get('Parent ID')
                                    or node_row.get('parent id')
                                    or node_row.get('parentId')
                                    or node_row.get('ParentId')
                                    or parent_id_val
                                )

                        except Exception as e:
                            # Non-fatal: keep defaults and surface a warning for debugging
                            st.warning(f"Node lookup warning: {e}")

                    # Final fallback: try deriving id from dataframe index where possible
                    if not node_id_val and df is not None and 'selected_col' in locals() and selected_col in df.columns:
                        try:
                            idxs = df[df[selected_col].astype(str) == str(node_choice)].index
                            if len(idxs) > 0:
                                node_id_val = f"N{int(idxs[0]):05d}"
                        except Exception:
                            node_id_val = None

                    # If still unresolved, leave node_id as None (better than a misleading placeholder)
                    if not node_id_val:
                        node_id_val = None

                    single_node = {
                        "node_id": node_id_val,
                        "path": node_path,
                        "display_name": display_name,
                        "level": level_val,
                        "parent_id": parent_id_val
                    }

                    # Generate a unique run_id for this analysis (always new per invocation)
                    run_id_val = f"run_{datetime.utcnow().strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex}"
                    st.session_state["run_id"] = run_id_val
                    st.session_state["last_run_id"] = run_id_val
                    st.caption(f"Run ID for this analysis: `{run_id_val}`")

                    # Append to in-session run history (most recent first, capped at 20)
                    run_history = st.session_state.get("run_history", [])
                    run_history.insert(
                        0,
                        {
                            "run_id": run_id_val,
                            "node": display_name or str(node_choice),
                            "model": model_name,
                            "env": env_mode,
                            "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
                        },
                    )
                    st.session_state["run_history"] = run_history[:20]

                    payload = {
                        "taxonomy_node": node_choice,
                        "nodes_to_query": [single_node],
                        "rel_depth": 2,
                        "query_depth": int(DEFAULT_QUERY_DEPTH),
                        "required_fields": DEFAULT_REQUIRED_FIELDS,
                        "global_context": global_context,
                        "extra_context": extra_context,
                        "prompt_text": prompt_text,
                        "qc_critic_prompt": _load_qc_prompt(),
                        "model_name": model_name,
                        "temperature": float(temperature),
                        "max_tokens": int(max_tokens),
                        "priority": priority,
                        "timeout_ms": timeout_ms,
                        "timestamp": datetime.utcnow().isoformat(),
                        "env_mode": st.session_state.get("env_mode", "live"),
                        "env_path": N8N_WEBHOOK_URL,   # explicit URL of the echo_agent endpoint used
                        "client_timestamp": time.time(),
                        "run_id": run_id_val,
                    }

                    headers = {"X-Webhook-Secret": WEBHOOK_SECRET, "Content-Type": "application/json"}

                    # Use the user-selected timeout (in ms), converted to seconds.
                    # Enforce a sensible minimum so we don't fail instantly on very low values.
                    http_timeout_s = max(timeout_ms / 1000.0, 30)

                    try:
                        resp = requests.post(
                            N8N_WEBHOOK_URL,
                            json=payload,
                            headers=headers,
                            timeout=http_timeout_s,
                        )
                        st.info(f"Run request returned: **{format_http_status(resp.status_code)}**")
                        # Handle Cloudflare 524 gateway timeout: n8n may still finish in the background
                        if resp.status_code == 524:
                            st.warning(
                                f"⚠️ **{format_http_status(524)}** from n8n / Cloudflare.\n\n"
                                "The deep research analysis may still be running in the background. "
                                "This does *not* mean the run failed.\n\n"
                                "You can safely check status or reload this run later."
                            )
                            # Keep last_run_id so the user can poll echo_status later
                            st.session_state["last_run_id"] = run_id_val
                        else:
                            try:
                                j = resp.json()
                                # Make the rest of the app aware of this response
                                st.session_state["latest_response"] = j
                                try:
                                    st.session_state["last_response"] = j[0] if isinstance(j, list) else j
                                except Exception:
                                    st.session_state["last_response"] = j

                                # Optional table rendering if your webhook returns rows
                                if isinstance(j, dict) and "rows" in j and isinstance(j["rows"], list) and len(j["rows"]) > 0:
                                    try:
                                        df_rows = pd.DataFrame(j["rows"])
                                        st.subheader("Rows (table)")
                                        st.dataframe(df_rows)
                                    except Exception:
                                        st.write("Rows present but failed to render as table.")
                            except Exception:
                                st.subheader("Raw response")
                                st.text(resp.text)
                    except Exception as e:
                        st.error(f"Request failed: {e}")

        # ----------------------------------------------------
        # Run History Panel (session)
        # ----------------------------------------------------
        st.markdown("### Run History (session)")
        run_history = st.session_state.get("run_history", [])
        if not run_history:
            st.caption("No runs recorded yet in this session.")
        else:
            hist_df = pd.DataFrame(
                [
                    {
                        "Run ID": r.get("run_id"),
                        "Node": r.get("node"),
                        "Model": r.get("model"),
                        "Env": r.get("env"),
                        "Timestamp (UTC)": r.get("timestamp"),
                    }
                    for r in run_history
                ]
            )
            st.dataframe(hist_df, use_container_width=True, height=220)

            selected_run_id = st.selectbox(
                "Select a Run ID to reload via echo_status",
                [r.get("run_id") for r in run_history],
                key="run_history_select",
            )

            if st.button("Load selected run via echo_status", key="btn_load_selected_run"):
                if not selected_run_id:
                    st.warning("No Run ID selected.")
                else:
                    headers = {"X-Webhook-Secret": WEBHOOK_SECRET, "Content-Type": "application/json"}
                    try:
                        status_resp = requests.post(
                            N8N_STATUS_URL,
                            json={"run_id": selected_run_id},
                            headers=headers,
                            timeout=30,
                        )
                        st.info(f"Status check returned: **{format_http_status(status_resp.status_code)}**")
                        try:
                            sj = status_resp.json()
                        except Exception:
                            st.error("Status endpoint did not return JSON.")
                            st.text(status_resp.text[:1000])
                        else:
                            status_val = ""
                            if isinstance(sj, dict) and "status" in sj:
                                status_val = str(sj.get("status", "")).lower()

                            if isinstance(sj, dict) and status_val == "pending":
                                st.info("Analysis is still in progress inside n8n. Try again later.")
                            else:
                                st.session_state["latest_response"] = sj
                                st.session_state["last_response"] = (
                                    sj if not isinstance(sj, list) else sj[0]
                                )
                                st.session_state["run_id"] = selected_run_id
                                st.session_state["last_run_id"] = selected_run_id
                                st.success(
                                    "Loaded completed analysis from echo_status for the selected run.",
                                )
                    except Exception as e:
                        st.error(f"Status check failed: {e}")

        # ----------------------------------------------------
        # Manual Run ID Loading 
        # ----------------------------------------------------
        st.markdown("### Load Previous Run")
        st.caption(
            "Manually enter a Run ID from a previous session to load its results. "
            "Useful for reviewing analyses from other sessions or sharing specific runs."
        )
        
        manual_run_id = st.text_input(
            "Enter Run ID to load:",
            key="manual_run_id_input",
            placeholder="run_20241203T143022_abc123def456...",
            help="Enter the full Run ID from a previous analysis to load its results"
        )
        
        if st.button("Load run by ID", key="btn_load_run_by_id"):
            if not manual_run_id or not manual_run_id.strip():
                st.warning("Please enter a Run ID to load.")
            else:
                run_id_to_load = manual_run_id.strip()
                st.info(f"Loading Run ID: `{run_id_to_load}`")
                headers = {"X-Webhook-Secret": WEBHOOK_SECRET, "Content-Type": "application/json"}
                try:
                    status_resp = requests.post(
                        N8N_STATUS_URL,
                        json={"run_id": run_id_to_load},
                        headers=headers,
                        timeout=30,
                    )
                    st.info(f"Manual load returned: **{format_http_status(status_resp.status_code)}**")
                    try:
                        sj = status_resp.json()
                    except Exception:
                        st.error("Status endpoint did not return JSON.")
                        st.text(status_resp.text[:1000])
                    else:
                        status_val = ""
                        if isinstance(sj, dict) and "status" in sj:
                            status_val = str(sj.get("status", "")).lower()

                        if isinstance(sj, dict) and status_val == "pending":
                            st.info("Analysis is still in progress inside n8n. Try again later.")
                        elif isinstance(sj, dict) and sj.get("error"):
                            st.error(f"Run not found or error: {sj.get('error')}")
                        else:
                            # Successfully loaded - update session state
                            st.session_state["latest_response"] = sj
                            st.session_state["last_response"] = (
                                sj if not isinstance(sj, list) else sj[0]
                            )
                            st.session_state["run_id"] = run_id_to_load
                            st.session_state["last_run_id"] = run_id_to_load
                            
                            st.success(f"✅ Successfully loaded analysis for Run ID: `{run_id_to_load}`")
                            st.info("Check the right panel to review the loaded analysis results.")
                except Exception as e:
                    st.error(f"Failed to load Run ID: {e}")

    # ----------------------------------------------------
    # QC Review (from n8n QC critic) – left column helper
    # ----------------------------------------------------

    st.markdown("&nbsp;")
    with st.container(border=True):
        st.markdown("#### Step 5: QC Review")
        st.caption(
            "Automated quality check of the latest analysis. "
            "Flags homogeneous margins, synthetic time series, and weak differentiation, "
            "and suggests how to improve the next run."
        )

        qc_source = None
        if "latest_response" in st.session_state and isinstance(st.session_state["latest_response"], dict):
            qc_source = st.session_state["latest_response"]
        elif "last_response" in st.session_state and isinstance(st.session_state["last_response"], dict):
            qc_source = st.session_state["last_response"]

        def _to_list_from_json(v):
            if isinstance(v, list):
                return v
            if isinstance(v, str):
                try:
                    parsed = json.loads(v)
                    return parsed if isinstance(parsed, list) else ([v] if v else [])
                except Exception:
                    return [v] if v else []
            return []

        if qc_source:
            issues = _to_list_from_json(
                qc_source.get("qc_issues_detected") or qc_source.get("qc_issues")
            )
            issue_summaries = _to_list_from_json(qc_source.get("qc_issue_summaries") or [])
            actions = _to_list_from_json(qc_source.get("qc_recommended_actions") or [])
            strengths = _to_list_from_json(qc_source.get("qc_strengths_summary") or [])
            optional = _to_list_from_json(qc_source.get("qc_optional_improvements") or [])

            rerun_recommended = bool(qc_source.get("qc_rerun_recommended"))
            suggested_model = qc_source.get("qc_suggested_model") or ""
            suggested_temp = qc_source.get("qc_suggested_temperature")
            suggested_tokens = qc_source.get("qc_suggested_max_tokens")
            suggested_extra = qc_source.get("qc_suggested_extra_context_append") or ""

            if not issues and not issue_summaries and not actions and not strengths and not optional:
                st.info("No QC feedback available yet for this run.")
            else:
                status_label = "Rerun Recommended" if rerun_recommended else "OK (No rerun required)"
                status_color = "#f97373" if rerun_recommended else "#4ade80"

                st.markdown(
                    f"""
                    <div style="
                        background-color:#1d2329;
                        border-radius:0.6rem;
                        border:1px solid #33363d;
                        padding:0.8rem 1.0rem;
                        font-size:0.88rem;
                        margin-bottom:0.6rem;">
                        <div style="margin-bottom:0.4rem;">
                            <span style="background:{status_color}; color:#000;
                                        padding:0.15rem 0.55rem; border-radius:999px;
                                        font-size:0.78rem; font-weight:600;">
                                {status_label}
                            </span>
                        </div>
                    """,
                    unsafe_allow_html=True,
                )

                if issue_summaries:
                    st.markdown("**Issues detected:**")
                    for s in issue_summaries:
                        st.markdown(f"- {s}")
                    st.markdown("&nbsp;")
                    st.divider()
                    st.caption("Use this panel to select taxonomy nodes, adjust global and node-level context, choose the Perplexity model, set environment (Live/Test), and trigger new analyses via the n8n echo_agent workflow.")

                if strengths:
                    st.markdown("**What's working well:**")
                    for s in strengths:
                        st.markdown(f"- {s}")

                if optional:
                    st.markdown("**Other things to consider (non-blocking):**")
                    for s in optional:
                        st.markdown(f"- {s}")

                if suggested_model or suggested_temp is not None or suggested_tokens is not None:
                    st.markdown("**Suggested run settings (from QC critic):**")
                    desc_bits = []
                    if suggested_model:
                        desc_bits.append(f"Model: `{suggested_model}`")
                    if suggested_temp is not None:
                        desc_bits.append(f"Temperature: `{suggested_temp}`")
                    if suggested_tokens is not None:
                        desc_bits.append(f"Max tokens: `{suggested_tokens}`")
                    if desc_bits:
                        st.markdown("- " + "; ".join(desc_bits))

                if actions:
                    st.markdown("**Recommended actions:**")
                    for a in actions:
                        st.markdown(f"- {a}")

                if suggested_extra:
                    with st.expander(
                        "QC packet (source) — click QC Apply to copy into Extra Context",
                        expanded=False,
                    ):
                        st.code(suggested_extra, language="markdown")

                    # Use a callback so we can safely mutate session_state for a widget key
                    def _apply_qc_recommendations() -> None:
                        # 1) OVERWRITE extra_context with QC packet (prevents drift + duplication across reruns)
                        existing_ctx = st.session_state.get("extra_context", "") or ""

                        # Optional: keep a backup so nothing is lost (useful for debugging)
                        if isinstance(existing_ctx, str) and existing_ctx.strip():
                            st.session_state["extra_context_prev"] = existing_ctx

                        # De-dupe: if it is already exactly applied, do nothing
                        if isinstance(existing_ctx, str) and existing_ctx.strip() == suggested_extra.strip():
                            return

                        st.session_state["extra_context"] = suggested_extra

                        # 2) Store QC-suggested run settings as overrides for the next run
                        if suggested_model:
                            st.session_state["qc_override_model"] = suggested_model
                            # Also drive the model selectbox immediately where possible
                            try:
                                # Map any detailed model id back onto our compact options
                                if suggested_model in model_options:
                                    st.session_state["model_choice"] = suggested_model
                                elif suggested_model.startswith("sonar-pro"):
                                    st.session_state["model_choice"] = "sonar-pro"
                                elif suggested_model.startswith("sonar-deep-research"):
                                    st.session_state["model_choice"] = "sonar-deep-research"
                                else:
                                    st.session_state["model_choice"] = "sonar"
                            except Exception:
                                # Non-fatal; we still keep the override for the next rerun
                                pass
                        if suggested_temp is not None:
                            try:
                                t = float(suggested_temp)
                                # clamp to a sane range; reject nonsensical values
                                if 0.0 <= t <= 1.5:
                                    st.session_state["qc_override_temperature"] = t
                            except Exception:
                                pass
                        if suggested_tokens is not None:
                            try:
                                mt = int(suggested_tokens)
                                # never allow 0 (or tiny) tokens; it will destroy output quality
                                if mt >= 512:
                                    st.session_state["qc_override_max_tokens"] = mt
                            except Exception:
                                pass

                    st.button("QC Apply", key="qc_apply_button", on_click=_apply_qc_recommendations)
        else:
            st.caption("No QC data available yet – run a query or check status first.")

        # end Step 5 card
    # ----------------------------------------------------
    # Step 6 (Control): Send to database (signal only)
    # ----------------------------------------------------
    st.markdown('<div class="step-6-anchor"></div>', unsafe_allow_html=True)
    st.markdown("&nbsp;")
    with st.container(border=True):
        st.markdown("#### Step 6: Commit to database")
        st.caption(
            "Send the currently reviewed analysis to Google Sheets via the write_agent. "
            "Payload construction and write execution occur after parsing."
        )



        payload_all = st.session_state.get("payload_all", {})
        inflight = bool(st.session_state.get("write_inflight"))

        payload_hash = stable_hash(payload_all) if payload_all else None
        already_written = bool(payload_hash and st.session_state.get("last_written_hash") == payload_hash)

        if payload_all == {}:
            st.info("Step 6 is disabled because no write payload has been built yet. Run a query and ensure the right panel parsed JSON successfully.")
        elif inflight:
            st.info("Step 6 is disabled because a write is currently in progress.")
        elif already_written:
            st.success("This exact payload has already been written (client-side dedupe).")


        if st.button(
            "Send full payload to n8n → Google Sheets",
            key="btn_request_write",
            disabled=(payload_all == {} or inflight or already_written),
        ):
            # Pattern A: signal-only. Actual write execution happens at end-of-file.
            st.session_state["write_requested"] = True
            st.session_state["write_requested_hash"] = payload_hash
            st.session_state["write_requested_at_utc"] = datetime.utcnow().isoformat()

        if inflight:
            st.info("Write in progress…")
        elif already_written:
            st.success("This exact payload has already been written (skipping).")

        write_status = st.session_state.get("write_status")
        if isinstance(write_status, dict):
            # Only show status if it corresponds to the currently displayed payload
            if payload_hash and write_status.get("payload_hash") == payload_hash:
                if write_status.get("ok"):
                    st.success(
                        f"✅ Write completed: **{format_http_status(write_status.get('status_code'))}**"
                    )
                    st.caption(
                        "Saved to Google Sheets via write_agent. "
                        "If you re-run this same node without changing the payload, Step 6 will show it as already written."
                    )
                else:
                    st.error(
                        f"❌ Write failed: **{format_http_status(write_status.get('status_code'))}**"
                    )
                    msg = write_status.get("message", "Unknown error")
                    if msg:
                        st.caption(msg[:300])
                    st.caption(
                        "Suggested next steps: verify env (Live/Test), check n8n logs for the write_agent run, "
                        "then retry Step 6 once the issue is resolved."
                    )
            # else: status is for a different payload/run; don't display it here

# (left_col block ends here)
# Right column - dedicated to response output (50/50 with left)
# This must remain *after* the with left_col: block ends

with right_col:
    # --- Right-side review surface ---
    # Wrap the entire review surface in a subtle card to emphasise separation
    st.markdown('<div class="results-pane">', unsafe_allow_html=True)

    st.markdown("### Output Review Panel")
    st.caption(
        "Review the structured node analysis returned from n8n/Perplexity: node summary, "
        "financial tables, player and proxy estimates, commentary, evidence coverage and "
        "confidence. Validate here before writing to Google Sheets via write_agent."
    )
    st.divider()
    data = None
    if "latest_response" in st.session_state and st.session_state["latest_response"]:
        j = st.session_state["latest_response"]
        st.session_state["last_response"] = j if not isinstance(j, list) else j[0]
        data = st.session_state["last_response"]
    elif "last_response" in st.session_state and st.session_state["last_response"]:
        data = st.session_state["last_response"]

    if not data:
        st.info("Awaiting response from n8n (no JSON parsed yet).")
        data = {}
        st.markdown("</div>", unsafe_allow_html=True)
    else:
        # === RESTORED: Robust JSON parsing & normalization ===
        # Helper functions for cross-platform LLM compatibility

        def _strip_invalid_control_chars(s: str) -> str:
            """
            Remove non-printable control characters that make otherwise-valid JSON
            fail to parse (e.g. ASCII < 32, excluding normal whitespace).
            This is a defensive fix for occasional stray tokens in llm_output_raw.
            """
            if not isinstance(s, str):
                return s
            out_chars = []
            for ch in s:
                code = ord(ch)
                if code >= 32 or ch in ("\n", "\r", "\t"):
                    out_chars.append(ch)
            return "".join(out_chars)
        def _strip_code_fences(s: str) -> str:
            """Remove ```json ... ``` or ``` ... ``` fences if present."""
            if not isinstance(s, str):
                return s

            m = re.search(r"```(?:json)?\s*([\\s\\S]*?)```", s, flags=re.IGNORECASE)
            return m.group(1).strip() if m else s

    def _strip_think_tags(s: str) -> str:
        """Remove <think>...</think> blocks that some models prepend."""
        if not isinstance(s, str):
            return s
        # Remove all <think>...</think> regions, then trim whitespace
        return re.sub(r"<think>[\s\S]*?</think>", "", s, flags=re.IGNORECASE).strip()

    def _as_dict(obj):
        return obj if isinstance(obj, dict) else {}

    def _normalize_results(res_obj):
        """
        Results sometimes arrive as an array of {field, value} items instead of
        an object keyed by field. Convert to an object if needed.
        """
        if isinstance(res_obj, dict):
            return res_obj
        if isinstance(res_obj, list):
            out = {}
            for item in res_obj:
                if isinstance(item, dict):
                    f = item.get("field")
                    v = item.get("value")
                    if f:
                        out[f] = v
            return out
        return {}

    # Multi-tier parsing with comprehensive fallback strategies
    parsed = {}
    results = {}
    parse_errors = []

    # --- Unwrap echo_status-style envelope, if present ---
    # If the top-level object looks like a status wrapper (has result_raw but no llm_output_*),
    # parse result_raw and merge its fields into `data` so the rest of the pipeline sees
    # the same shape as a normal echo_agent response.
    if (
        isinstance(data, dict)
        and "result_raw" in data
        and not data.get("llm_output_parsed")
        and not data.get("llm_output_raw")
    ):
        try:
            inner = json.loads(_strip_code_fences(str(data["result_raw"])))
            if isinstance(inner, dict):
                # Outer metadata (status, run_id, etc.) is retained;
                # inner keys (timestamp, model, llm_output_parsed, ...) take precedence.
                merged = {**data, **inner}
                data = merged
        except Exception as e:
            parse_errors.append(f"Failed to parse inner result_raw from status wrapper: {e}")

    # 1) Preferred: llm_output_parsed provided by n8n
    if isinstance(data, dict) and data.get("llm_output_parsed"):
        tmp = data["llm_output_parsed"]
        # tmp might already be a dict (good) or a JSON string
        if isinstance(tmp, str):
            cleaned = _strip_invalid_control_chars(
                _strip_think_tags(_strip_code_fences(tmp))
            )
            try:
                parsed = json.loads(cleaned)
            except Exception as e:
                parse_errors.append(f"llm_output_parsed JSON parse failed: {e}")
                # Attempt to handle double-escaped JSON strings (JSON encoded as a string)
                try:
                    inner = json.loads(cleaned)
                    if isinstance(inner, str):
                        inner_cleaned = _strip_think_tags(_strip_code_fences(inner))
                        parsed = json.loads(inner_cleaned)
                except Exception as e2:
                    parse_errors.append(f"llm_output_parsed double-unescape failed: {e2}")
                    parsed = {}
        elif isinstance(tmp, dict):
            parsed = tmp

    # 2) Next preference: llm_output_clean (JSON string after preprocessing in n8n)
    #    Still defensively strip think blocks, code fences, and illegal control chars.
    if not parsed and isinstance(data, dict) and data.get("llm_output_clean"):
        try:
            clean_str = str(data["llm_output_clean"])
            clean_str = _strip_invalid_control_chars(
                _strip_think_tags(_strip_code_fences(clean_str))
            )
            parsed = _as_dict(json.loads(clean_str))
        except Exception as e:
            parse_errors.append(f"llm_output_clean JSON parse failed: {e}")

    # 3) Fallback: try llm_output_raw as fenced or plain JSON

    if not parsed and isinstance(data, dict) and data.get("llm_output_raw"):
        raw_original = data["llm_output_raw"]
        # Strip <think> blocks and any surrounding code fences
        raw = _strip_think_tags(_strip_code_fences(raw_original))
        # Defensive cleanup: strip any illegal control characters that could cause
        # "Invalid control character" JSON errors even when the payload otherwise
        # looks like valid JSON.
        raw = _strip_invalid_control_chars(raw)

        # 2a) Direct JSON parse
        if isinstance(raw, str):
            try:
                parsed = _as_dict(json.loads(raw))
            except Exception as e:
                parse_errors.append(f"llm_output_raw direct JSON parse failed: {e}")
                # 2b) Second attempt: slice between first '{' and last '}' and parse that
                try:
                    start = raw.find("{")
                    end = raw.rfind("}") + 1
                    if start != -1 and end > start:
                        candidate = raw[start:end]
                        candidate = _strip_invalid_control_chars(candidate)
                        parsed = _as_dict(json.loads(candidate))
                    else:
                        parse_errors.append("Could not locate JSON object braces in llm_output_raw.")
                except Exception as e2:
                    parse_errors.append(f"Brace-sliced parse of llm_output_raw failed: {e2}")
                    parsed = {}
                # 2c) Third attempt: handle double-escaped JSON (JSON encoded as a string)
                if not parsed:
                    try:
                        inner = json.loads(raw)
                        if isinstance(inner, str):
                            inner_clean = _strip_think_tags(_strip_code_fences(inner))
                            parsed = _as_dict(json.loads(inner_clean))
                    except Exception as e3:
                        parse_errors.append(f"Double-unescape of llm_output_raw failed: {e3}")
                        parsed = {}
        elif isinstance(raw, dict):
            parsed = _as_dict(raw)

    # 4) Fallback: some n8n nodes put the JSON under "body"
    if not parsed and isinstance(data, dict) and isinstance(data.get("body"), dict):
        # If body contains "results" or any schema-like keys, use it
        body = data["body"]
        if "results" in body or "requested_fields" in body or "display_name" in body:
            parsed = body

    # 4) Fallback: if the top-level already looks like the schema, use it
    if not parsed and isinstance(data, dict) and ("results" in data or "requested_fields" in data):
        parsed = data

    # 5) If the parsed top-level is a list of {field, value} items, wrap it as results
    if isinstance(parsed, list):
        parsed = {"results": parsed}

    # Final: coerce results with normalization
    try:
        results = _normalize_results(_as_dict(parsed).get("results", {}))
    except Exception as e:
        results = {}
        st.warning(f"Could not normalize results: {e}")

    # If we hit parsing issues AND still have no results, surface them for debugging.
    # If results exist, we assume the fallbacks succeeded and keep the UI clean.
    if parse_errors and not results:
        with st.expander("LLM JSON parsing issues", expanded=False):
            st.write(
                "We received text from Perplexity via n8n and encountered JSON parsing "
                "errors. Structured panels may be empty or incomplete."
            )
            for err in parse_errors:
                st.write(f"- {err}")
            # Prefer showing the cleaned JSON string if available; fall back to raw text.
            if isinstance(data, dict) and data.get("llm_output_clean"):
                st.markdown("**Cleaned `llm_output_clean` from n8n (truncated):**")
                try:
                    st.code(str(data.get("llm_output_clean"))[:8000], language="json")
                except Exception:
                    st.code(str(data.get("llm_output_clean"))[:8000])
            elif isinstance(data, dict) and data.get("llm_output_raw"):
                st.markdown("**Raw `llm_output_raw` from n8n (truncated):**")
                try:
                    st.code(str(data['llm_output_raw'])[:8000], language='json')
                except Exception:
                    st.code(str(data.get('llm_output_raw'))[:8000])

    # Make the parsed object available to the write step later.
    #    This is the single most important line to ensure the writer sees real data.

    try:
        st.session_state["parsed_llm"] = parsed
    except Exception:
        pass


    # ALSO build the write payload immediately after a successful parse.
    # This ensures Step 6 (left column) can enable the button on the same rerun.
    try:
        payload_all = {}
        if isinstance(parsed, dict) and parsed:
            payload_all = flatten_full_llm_output(parsed)
    except Exception:
        # Do NOT crash or alter parsing behavior
        # Do not overwrite a previously valid payload with {}
        if not st.session_state.get("payload_all"):
            st.session_state["payload_all"] = {}

    # ALSO build the write payload immediately after a successful parse.
    # This ensures Step 6 (left column) can enable the button on the same rerun.
    try:
        payload_all = {}
        if isinstance(parsed, dict) and parsed:
            payload_all = flatten_full_llm_output(parsed)
        # Attach metadata once here so downstream write is deterministic.
        meta = {
            "reviewer": st.session_state.get("reviewer", "unknown"),
            "model_name": st.session_state.get("model_name"),
            "temperature": st.session_state.get("temperature"),
            "max_tokens": st.session_state.get("max_tokens"),
            "query_depth": st.session_state.get("query_depth"),
            "token_input": st.session_state.get("token_input"),
            "token_output": st.session_state.get("token_output"),
            "token_cost_usd": st.session_state.get("token_cost_usd"),
            "latency_sec": st.session_state.get("latency_sec"),
            "prompt_hash": st.session_state.get("prompt_hash"),
            "context_hash": st.session_state.get("context_hash"),
            "execution_env": st.session_state.get("execution_env", "streamlit_app"),
            "timestamp_utc": datetime.utcnow().isoformat(),
            "run_id": st.session_state.get("run_id"),
        }
        if isinstance(payload_all, dict) and payload_all.get("nodes_row"):
            payload_all["nodes_row"].update(meta)
        st.session_state["payload_all"] = payload_all
    except Exception:
        # Non-fatal: keep UI alive; Step 6 will remain disabled until payload is valid.
        # Do not overwrite a previously valid payload with {}
        if not st.session_state.get("payload_all"):
            st.session_state["payload_all"] = {}

    # --- Header with Path only (timestamp handled in summary card) ---
    st.markdown(f"#### {parsed.get('path', '—')}")

    # -------------------------------------------------------
    # Node & Run Summary Card (professional quick-reference)
    # -------------------------------------------------------
    st.markdown("### Node & Run Summary")
    node_summary = {
        "Path": parsed.get("path", "—"),
        "Display Name": parsed.get("display_name", "—"),
        "Node ID": parsed.get("node_id", "—"),
        "Level": parsed.get("level", "—"),
        "Parent ID": parsed.get("parent_id", "—"),
    }

    # Query / run metadata (merged into the same card)
    qd = {
        "Model": data.get("model") or parsed.get("model") or data.get("model_name") or "—",
        "Total Tokens": data.get("total_tokens") or data.get("token_output") or "—",
        "Cost (USD)": (
            f"${data.get('cost_usd', 0):.4f}"
            if isinstance(data.get("cost_usd"), (int, float))
            else (data.get("cost_usd") or "—")
        ),
        "Run Timestamp": data.get("timestamp") or parsed.get("timestamp") or "—",
    }

    st.markdown(
        f"""
        <div style="
            background-color:#1c1f25;
            border:1px solid #2a2d33;
            padding:0.8rem 1.0rem;
            border-radius:8px;
            font-size:0.9rem;
            line-height:1.35;
            margin-bottom:0.8rem;
        ">
        <b>Path:</b> {node_summary['Path']}<br>
        <b>Display Name:</b> {node_summary['Display Name']}<br>
        <b>Node ID:</b> {node_summary['Node ID']}<br>
        <b>Level:</b> {node_summary['Level']}<br>
        <b>Parent ID:</b> {node_summary['Parent ID']}<br>
        <hr style="border-top:1px solid #2a2d33; margin:0.4rem 0;" />
        <b>Model:</b> {qd['Model']}<br>
        <b>Total Tokens:</b> {qd['Total Tokens']}<br>
        <b>Cost (USD):</b> {qd['Cost (USD)']}<br>
        <b>Run Timestamp:</b> {qd['Run Timestamp']}<br>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # --- Segment Summary (no duplicate header) ---
    if results.get("summary"):
        st.write(results["summary"])

    # --- Taxonomy Reference (narrative only; node meta is in the summary card) ---
    # These fields belong to the TOP-LEVEL parsed schema, not results{}
    if parsed.get("taxonomy_reference"):
        st.markdown("### Taxonomy Reference")
        st.markdown(
            f"""
            <div style="
                background-color:#2b2b2b;
                color:#e8e8e8;
                padding:0.9rem 1.1rem;
                border-radius:0.6rem;
                border:1px solid #3a3a3a;
                font-size:0.95rem;
            ">
            {parsed["taxonomy_reference"]}
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Display scope context (new field)
    scope_context_entry = None
    extra_node_context = None
    if isinstance(results, dict):
        scope_context_entry = results.get("scope_context")
        # primary location per schema: results.extra_node_context
        extra_node_context = results.get("extra_node_context")
    if isinstance(parsed, dict):
        # tolerate mis-shaped outputs that put extra_node_context at the top level
        extra_node_context = parsed.get("extra_node_context") or extra_node_context

    from html import escape as _html_escape
    import re as _re

    if scope_context_entry:
        st.markdown("### Scope Context")

        # Light sentence-splitting to improve readability, purely for display
        sentences = _re.split(r"(?<=[.!?])\s+", str(scope_context_entry).strip())
        sentences = [s.strip() for s in sentences if s.strip()]

        if len(sentences) > 1:
            bullets_html = "<ul>" + "".join(
                f"<li>{_html_escape(s)}</li>" for s in sentences
            ) + "</ul>"
        else:
            bullets_html = _html_escape(str(scope_context_entry))

        st.markdown(
            f"""
            <div style="background-color:#2b2b2b; color:#e8e8e8; padding:0.9rem 1.1rem; border-radius:0.6rem; border:1px solid #3a3a3a; font-size:0.95rem;">
            {bullets_html}
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown("")  # spacing before next section

    # Additional narrative that doesn't fit into scope_context
    if extra_node_context:
        extra_sentences = _re.split(r"(?<=[.!?])\s+", str(extra_node_context).strip())
        extra_sentences = [s.strip() for s in extra_sentences if s.strip()]

        if len(extra_sentences) > 1:
            extra_html = "<ul>" + "".join(
                f"<li>{_html_escape(s)}</li>" for s in extra_sentences
            ) + "</ul>"
        else:
            extra_html = _html_escape(str(extra_node_context))

        st.markdown("### Additional Node Context")
        st.markdown(
            f"""
            <div style="background-color:#2b2b2b; color:#e8e8e8; padding:0.9rem 1.1rem; border-radius:0.6rem; border:1px solid #3a3a3a; font-size:0.95rem;">
            {extra_html}
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown("")  # spacing before next section

    # Methodology summary stacked with scope / extra context
    methodology_summary = None
    if isinstance(results, dict):
        methodology_summary = results.get("methodology_summary")

    if methodology_summary:
        meth_sentences = _re.split(r"(?<=[.!?])\s+", str(methodology_summary).strip())
        meth_sentences = [s.strip() for s in meth_sentences if s.strip()]

        if len(meth_sentences) > 1:
            meth_html = "<ul>" + "".join(
                f"<li>{_html_escape(s)}</li>" for s in meth_sentences
            ) + "</ul>"
        else:
            meth_html = _html_escape(str(methodology_summary))

        st.markdown("### Methodology Summary")
        st.markdown(
            f"""
            <div style="background-color:#2b2b2b; color:#e8e8e8; padding:0.9rem 1.1rem; border-radius:0.6rem; border:1px solid #3a3a3a; font-size:0.95rem;">
            {meth_html}
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown("")  # spacing before next section

    # --- Evidence overview (non-intrusive auditability summary) ---
    evidence_list: list = []
    evidence_map = None

    if isinstance(parsed, dict):
        # Prefer top-level evidence / evidence_map if present
        evidence_list = parsed.get("evidence") or parsed.get("results", {}).get("evidence") or []
        evidence_map = parsed.get("evidence_map") or parsed.get("results", {}).get("evidence_map")
    elif isinstance(results, dict):
        evidence_list = results.get("evidence", [])
        evidence_map = results.get("evidence_map")

    # High-level coverage only – detailed breakdown is in the mapping expanders
    total_evidence = len(evidence_list) if isinstance(evidence_list, list) else 0
    mapped_fields = 0
    if isinstance(evidence_map, dict):
        mapped_fields = len(evidence_map)
    elif isinstance(evidence_map, list):
        mapped_fields = len(evidence_map)

    if total_evidence or mapped_fields:
        st.markdown("### Evidence Coverage (preview)")
        st.markdown(
            f"""
            <div style="background-color:#22252b; color:#e8e8e8;
                        padding:0.8rem 1.0rem; border-radius:0.6rem;
                        border:1px solid #33363d; font-size:0.85rem;">
                <b>Total evidence items:</b> {total_evidence}<br>
                <b>Mapped fields (all types):</b> {mapped_fields}
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Confidence summary
    if parsed.get("confidence_overall") is not None:
        conf = parsed["confidence_overall"]
        if conf >= 0.9:
            label = "Very High"
            color = "#6ee7b7"
        elif conf >= 0.8:
            label = "High"
            color = "#a7f3d0"
        elif conf >= 0.7:
            label = "Moderate"
            color = "#fde68a"
        elif conf >= 0.5:
            label = "Low"
            color = "#fca5a5"
        else:
            label = "Very Low"
            color = "#f87171"

        st.markdown(f"""
        <div style="background-color:{color}; padding:0.5rem 0.9rem; border-radius:0.5rem; margin-top:0.3rem;">
        <b>Overall Confidence:</b> {conf:.2f} ({label})
        </div>
        """, unsafe_allow_html=True)

    st.divider()

    # Utility function for dark-mode-friendly table formatting
    def style_table(df):
        return (
            df.style
            .format(na_rep="—", precision=2)
            .set_properties(**{
                "background-color": "rgba(30,30,30,0.6)",
                "color": "#f5f7fa",
                "border": "1px solid rgba(255,255,255,0.1)",
                "font-size": "0.9rem",
                "padding": "4px"
            })
        )


    def _clean_commentary_text(text: str) -> str:
        """
        Light-touch normalisation for long commentary strings, purely for display.
        Does NOT mutate any stored structures.
        """
        if not isinstance(text, str):
            return text

        t = unicodedata.normalize("NFKC", text)
        # Normalise non-breaking spaces and collapse weird whitespace
        t = t.replace("\u00a0", " ")
        t = re.sub(r"[ \t]+", " ", t)

        # Ensure a space before [EVID:x] tags if the model jammed them onto the word
        t = re.sub(r"\s*\[EVID:", " [EVID:", t)

        # Fix a couple of common jammed numeric phrases (e.g. '42.8BinFY23')
        t = re.sub(r"(\d+(?:\.\d+)?B)(?=in\b)", r"\1 ", t)
        t = re.sub(r"(FY\d{2})(?=and\b)", r"\1 ", t)
        t = re.sub(r"(FY\d{2})(?=to\b)", r"\1 ", t)

        return t.strip()

    def _render_commentary_block(title: str, text: str):
        """
        Render long-form commentary in a consistent, bullet-style card for readability.
        """
        if not isinstance(text, str):
            return
        from html import escape as _html_escape_local
        import re as _re_local

        cleaned = _clean_commentary_text(text)
        if not cleaned:
            return

        # Split into sentences; if the model already used line breaks, preserve them as bullets
        sentences = _re_local.split(r"(?<=[.!?])\s+", cleaned.strip())
        sentences = [s.strip() for s in sentences if s.strip()]

        if len(sentences) > 1:
            bullets_html = "<ul>" + "".join(
                f"<li>{_html_escape_local(s)}</li>" for s in sentences
            ) + "</ul>"
        else:
            bullets_html = _html_escape_local(cleaned)

        st.markdown(f"### {title}")
        st.markdown(
            f"""
            <div style="background-color:#2b2b2b; color:#e8e8e8; padding:0.9rem 1.1rem; border-radius:0.6rem; border:1px solid #3a3a3a; font-size:0.95rem;">
            {bullets_html}
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown("")  # spacing after block

    # ----------------------
    # Segment Financials
    # ----------------------
    st.markdown("### Segment Financials")
    fin_data = results.get("node_financials", {})
    if fin_data:
        years = ["15", "16", "17", "18", "19", "20", "21", "22", "23", "24", "25"]
        fin_rows = []

        # Revenue row
        rev_row = ["Revenue (USD bn)"]
        for y in years:
            rev_row.append(fin_data.get(f"fy{y}_revenue_usd_bn"))
        rev_row.append(fin_data.get("revenue_cagr_pct"))
        fin_rows.append(rev_row)

        # EBITDA row
        e_row = ["EBITDA (USD bn)"]
        for y in years:
            e_row.append(fin_data.get(f"fy{y}_ebitda_usd_bn"))
        e_row.append(fin_data.get("ebitda_cagr_pct"))
        fin_rows.append(e_row)

        # Margin row
        m_row = ["EBITDA Margin (%)"]
        for y in years:
            m_row.append(fin_data.get(f"fy{y}_ebitda_margin_pct"))
        m_row.append("")
        fin_rows.append(m_row)

        columns = ["Metric"] + [f"FY{y}" for y in years] + ["CAGR %"]
        fin_df = pd.DataFrame(fin_rows, columns=columns).set_index("Metric")
        st.dataframe(style_table(fin_df), use_container_width=True)
    else:
        st.info("No financial data available for this segment.")

    if results.get("financial_commentary"):
        _render_commentary_block("Financial Commentary", results["financial_commentary"])

    # ----------------------
    # Node Financials → Evidence Mapping
    # ----------------------
    # Uses evidence_map to show which EVID items back each node-level financial field
    # (supports both dict-style and list-style evidence_map structures)
    node_evidence_rows = []
    try:
        if isinstance(evidence_map, dict):
            # dict-style: {"results.node_financials.fy23_revenue_usd_bn": ["EVID:1", ...], ...}
            for field_path, supported in evidence_map.items():
                if not isinstance(field_path, str):
                    continue
                if "results.node_financials" not in field_path:
                    continue

                # Normalise supported_by into a list of strings
                if isinstance(supported, (list, tuple, set)):
                    supported_list = [s for s in supported if s]
                else:
                    supported_list = [supported] if supported else []
                if not supported_list:
                    continue

                evid_strs = []
                for s in supported_list:
                    if isinstance(s, str):
                        evid_strs.append(s)
                    elif isinstance(s, (int, float)):
                        evid_strs.append(str(int(s)))
                    else:
                        evid_strs.append(str(s))

                field_short = field_path.replace("results.node_financials.", "")
                node_evidence_rows.append(
                    {"Field": field_short, "Evidence IDs": ", ".join(evid_strs)}
                )

        elif isinstance(evidence_map, list):
            # list-style: [{"field": "...", "supported_by": [...]}, ...]
            for em in evidence_map:
                if not isinstance(em, dict):
                    continue
                field_path = em.get("field")
                if not isinstance(field_path, str):
                    continue
                if "node_financials" not in field_path and not field_path.startswith("fy"):
                    continue

                supported = em.get("supported_by", [])
                if isinstance(supported, (list, tuple, set)):
                    supported_list = [s for s in supported if s]
                else:
                    supported_list = [supported] if supported else []
                if not supported_list:
                    continue

                evid_strs = []
                for s in supported_list:
                    if isinstance(s, str):
                        evid_strs.append(s)
                    elif isinstance(s, (int, float)):
                        evid_strs.append(str(int(s)))
                    else:
                        evid_strs.append(str(s))

                field_short = field_path.replace("results.node_financials.", "")
                node_evidence_rows.append(
                    {"Field": field_short, "Evidence IDs": ", ".join(evid_strs)}
                )
    except Exception:
        node_evidence_rows = []

    if node_evidence_rows:
        with st.expander("Node Financials \u2192 Evidence Mapping", expanded=False):
            map_df = pd.DataFrame(node_evidence_rows).set_index("Field")
            try:
                st.dataframe(style_table(map_df), use_container_width=True)
            except KeyError:
                # Safety: pandas Styler can throw KeyError on certain shapes/labels;
                # show unstyled table rather than crashing the app.
                st.dataframe(map_df, use_container_width=True)

    # ----------------------
    # Top Players (up to 10)
    # ----------------------
    st.markdown("### Top Players (up to 10)")
    players = results.get("node_players", [])
    if players:
        p_df = pd.DataFrame(players)
        # rename columns for readability (match your sheet style)
        p_df = p_df.rename(columns={
            "rank": "Rank",
            "name": "Company",
            "country": "Country",
            "type": "Type",
            "player_fy23_revenue_usd_bn": "FY23 Revenue (USD bn)",
            "player_fy24_revenue_usd_bn": "FY24 Revenue (USD bn)",
            "player_fy25_revenue_usd_bn": "FY25 Revenue (USD bn)",
            "player_fy23_ebitda_usd_bn": "FY23 EBITDA (USD bn)",
            "player_fy24_ebitda_usd_bn": "FY24 EBITDA (USD bn)",
            "player_fy25_ebitda_usd_bn": "FY25 EBITDA (USD bn)",
            "player_fy23_ebitda_margin_pct": "FY23 EBITDA Margin (%)",
            "player_fy24_ebitda_margin_pct": "FY24 EBITDA Margin (%)",
            "player_fy25_ebitda_margin_pct": "FY25 EBITDA Margin (%)",
            "confidence_score": "Confidence",
            "attribution_basis": "Attribution Basis",
        })
        # make Rank the index when present
        if "Rank" in p_df.columns:
            p_df = p_df.set_index("Rank")
        st.dataframe(style_table(p_df), use_container_width=True)
    else:
        st.info("No player data available.")

    if results.get("player_commentary"):
        _render_commentary_block("Player Commentary", results["player_commentary"])

    # ----------------------
    # Player Financials → Evidence Mapping
    # ----------------------
    # Uses evidence_map to show which EVID items back each player-level financial field
    player_evidence_rows = []
    try:
        # Helper: turn any supported value into a list of string IDs
        def _normalise_supported(supported_val):
            if isinstance(supported_val, (list, tuple, set)):
                vals = [v for v in supported_val if v]
            else:
                vals = [supported_val] if supported_val else []
            out = []
            for v in vals:
                if isinstance(v, str):
                    out.append(v)
                elif isinstance(v, (int, float)):
                    out.append(str(int(v)))
                else:
                    out.append(str(v))
            return out

        # For nicer labels, use the players list returned by the model
        players_for_labels = results.get("node_players", []) if isinstance(results, dict) else []

        if isinstance(evidence_map, dict):
            # dict-style: either "player:Name:metric" (new) or "...node_players[0].metric" (legacy)
            for field_path, supported in evidence_map.items():
                if not isinstance(field_path, str):
                    continue

                player_label = None
                metric_key = None

                if field_path.startswith("player:"):
                    # New schema: "player:<PLAYER_NAME>:<field_name>"
                    m = re.match(r"^player:(.+?):(.+)$", field_path)
                    if not m:
                        continue
                    player_label = m.group(1).strip() or None
                    metric_key = m.group(2).strip()
                elif ".node_players[" in field_path:
                    # Legacy schema: "...node_players[0].player_fy23_revenue_usd_bn"
                    m = re.search(r"\.node_players\[(\d+)\]\.(.+)$", field_path)
                    if not m:
                        continue
                    idx = int(m.group(1))
                    metric_key = m.group(2)
                    player_label = f"Player {idx+1}"
                    if isinstance(players_for_labels, list) and 0 <= idx < len(players_for_labels):
                        player_name = players_for_labels[idx].get("name")
                        if player_name:
                            player_label = player_name
                else:
                    continue

                evid_strs = _normalise_supported(supported)
                if not evid_strs or not metric_key:
                    continue

                # If we still don't have a label (edge cases), fall back to raw field_path
                if not player_label:
                    player_label = "Player"

                field_label = f"{player_label} \u2013 {metric_key}"
                player_evidence_rows.append(
                    {"Field": field_label, "Evidence IDs": ", ".join(evid_strs)}
                )

        elif isinstance(evidence_map, list):
            # list-style entries with either "player:Name:metric" or legacy node_players[] paths
            for em in evidence_map:
                if not isinstance(em, dict):
                    continue
                field_path = em.get("field")
                if not isinstance(field_path, str):
                    continue

                player_label = None
                metric_key = None

                if field_path.startswith("player:"):
                    m = re.match(r"^player:(.+?):(.+)$", field_path)
                    if not m:
                        continue
                    player_label = m.group(1).strip() or None
                    metric_key = m.group(2).strip()
                elif ".node_players[" in field_path:
                    m = re.search(r"\.node_players\[(\d+)\]\.(.+)$", field_path)
                    if not m:
                        continue
                    idx = int(m.group(1))
                    metric_key = m.group(2)

                    player_label = f"Player {idx+1}"
                    if isinstance(players_for_labels, list) and 0 <= idx < len(players_for_labels):
                        player_name = players_for_labels[idx].get("name")
                        if player_name:
                            player_label = player_name
                else:
                    continue

                evid_strs = _normalise_supported(em.get("supported_by", []))
                if not evid_strs or not metric_key:
                    continue

                if not player_label:
                    player_label = "Player"

                field_label = f"{player_label} \u2013 {metric_key}"
                player_evidence_rows.append(
                    {"Field": field_label, "Evidence IDs": ", ".join(evid_strs)}
                )
    except Exception:
        player_evidence_rows = []

    if player_evidence_rows:
        with st.expander("Player Financials \u2192 Evidence Mapping", expanded=False):
            p_map_df = pd.DataFrame(player_evidence_rows).set_index("Field")
            try:
                st.dataframe(style_table(p_map_df), use_container_width=True)
            except KeyError:
                # Safety: pandas Styler can throw KeyError on certain shapes/labels;
                # show unstyled table rather than crashing the app.
                st.dataframe(p_map_df, use_container_width=True)

    # ----------------------
    # Pure Play / Proxy Estimates
    # ----------------------
    st.markdown("### Pure-Play / Proxy Estimates")
    proxies = results.get("pure_play_estimates", [])
    if proxies:
        pp_df = pd.DataFrame(proxies)
        pp_df = pp_df.rename(columns={
            "name": "Company",
            "country": "Country",
            "type": "Type",
            "proxy_reason": "Proxy Reason",
            "fy25_revenue_usd_bn": "FY25 Revenue (USD bn)",
            "fy25_ebitda_usd_bn": "FY25 EBITDA (USD bn)",
            "fy25_ebitda_margin_pct": "EBITDA Margin (%)",
            "confidence_score": "Confidence",
        })
        st.dataframe(style_table(pp_df), use_container_width=True)
    else:
        st.info("No proxy estimates available.")

    # --- Pure-Play / Proxy Commentary (no deprecated fallback) ---
    pp_comm = results.get("pure_play_commentary") or results.get("proxy_commentary")

    if pp_comm:
        _render_commentary_block("Pure-play / Proxy Commentary", pp_comm)
    else:
        st.info("No pure-play / proxy commentary available.")

    # ----------------------
    # Pure-play / Proxy → Evidence Mapping
    # ----------------------
    proxy_evidence_rows = []
    try:
        proxies_for_labels = (
            results.get("pure_play_estimates", [])
            if isinstance(results, dict)
            else []
        )
        if isinstance(evidence_map, dict):
            # dict-style: either "proxy:Name:metric" (new) or "...pure_play_estimates[0].metric" (legacy)
            for field_path, supported in evidence_map.items():
                if not isinstance(field_path, str):
                    continue

                proxy_label = None
                metric_key = None

                if field_path.startswith("proxy:"):
                    # New schema: "proxy:<PROXY_NAME>:<field_name>"
                    m = re.match(r"^proxy:(.+?):(.+)$", field_path)
                    if not m:
                        continue
                    proxy_label = m.group(1).strip() or None
                    metric_key = m.group(2).strip()
                elif ".pure_play_estimates[" in field_path:
                    # Legacy schema: "...pure_play_estimates[0].fy25_revenue_usd_bn"
                    m = re.search(r"\.pure_play_estimates\[(\d+)\]\.(.+)$", field_path)
                    if not m:
                        continue
                    idx = int(m.group(1))
                    metric_key = m.group(2)

                    proxy_label = f"Proxy {idx+1}"
                    if isinstance(proxies_for_labels, list) and 0 <= idx < len(proxies_for_labels):
                        proxy_name = proxies_for_labels[idx].get("name")
                        if proxy_name:
                            proxy_label = proxy_name
                else:
                    continue

                evid_strs = _normalise_supported(supported)
                if not evid_strs or not metric_key:
                    continue

                if not proxy_label:
                    proxy_label = f"Proxy {idx+1}" if 'idx' in locals() else "Proxy"
                if isinstance(proxies_for_labels, list) and 'idx' in locals() and 0 <= idx < len(proxies_for_labels):
                    proxy_name = proxies_for_labels[idx].get("name")
                    if proxy_name:
                        proxy_label = proxy_name

                field_label = f"{proxy_label} \u2013 {metric_key}"
                proxy_evidence_rows.append(
                    {"Field": field_label, "Evidence IDs": ", ".join(evid_strs)}
                )

        elif isinstance(evidence_map, list):
            # list-style entries with either "proxy:Name:metric" or legacy pure_play_estimates[] paths
            for em in evidence_map:
                if not isinstance(em, dict):
                    continue
                field_path = em.get("field")
                if not isinstance(field_path, str):
                    continue

                proxy_label = None
                metric_key = None

                if field_path.startswith("proxy:"):
                    m = re.match(r"^proxy:(.+?):(.+)$", field_path)
                    if not m:
                        continue
                    proxy_label = m.group(1).strip() or None
                    metric_key = m.group(2).strip()
                elif ".pure_play_estimates[" in field_path:
                    m = re.search(r"\.pure_play_estimates\[(\d+)\]\.(.+)$", field_path)
                    if not m:
                        continue
                    idx = int(m.group(1))
                    metric_key = m.group(2)
                else:
                    continue

                evid_strs = _normalise_supported(em.get("supported_by", []))
                if not evid_strs or not metric_key:
                    continue

                if not proxy_label:
                    proxy_label = f"Proxy {idx+1}" if 'idx' in locals() else "Proxy"
                if isinstance(proxies_for_labels, list) and 'idx' in locals() and 0 <= idx < len(proxies_for_labels):
                    proxy_name = proxies_for_labels[idx].get("name")
                    if proxy_name:
                        proxy_label = proxy_name

                field_label = f"{proxy_label} \u2013 {metric_key}"
                proxy_evidence_rows.append(
                    {"Field": field_label, "Evidence IDs": ", ".join(evid_strs)}
                )
    except Exception:
        proxy_evidence_rows = []

    if proxy_evidence_rows:
        with st.expander("Pure-play / Proxy \u2192 Evidence Mapping", expanded=False):
            proxy_map_df = pd.DataFrame(proxy_evidence_rows).set_index("Field")
            st.dataframe(style_table(proxy_map_df), use_container_width=True)

    # ----------------------
    # FY25 Coverage Check (Node vs Players)
    # ----------------------
    st.markdown("### FY25 Coverage Check (Node vs Top Players)")

    # Node FY25 revenue (USD bn)
    node_fy25_rev = None
    try:
        if isinstance(fin_data, dict):
            val = fin_data.get("fy25_revenue_usd_bn")
            node_fy25_rev = float(val) if val is not None else None
    except Exception:
        node_fy25_rev = None

    # Sum of player FY25 revenues (USD bn)
    total_player_fy25_rev = 0.0
    player_count_with_rev = 0
    if players:
        for p in players:
            # Support both new and legacy field names
            val = p.get("player_fy25_revenue_usd_bn", p.get("fy25_node_revenue_usd_bn"))
            try:
                if val is not None:
                    total_player_fy25_rev += float(val)
                    player_count_with_rev += 1
            except Exception:
                continue

    if node_fy25_rev and node_fy25_rev > 0 and player_count_with_rev > 0:
        coverage_pct = (total_player_fy25_rev / node_fy25_rev) * 100.0
        msg = f"Top players cover approximately **{coverage_pct:.1f}%** of FY25 node revenue."

        # Comfort band: 50–110% (outside this, flag for review)
        if coverage_pct < 50.0 or coverage_pct > 110.0:
            st.warning(
                msg
                + " This is outside the 50–110% comfort range; review node sizing and player attribution."
            )
        else:
            st.info(
                msg
                + " This is within the 50–110% comfort range and broadly consistent with a realistic long tail."
            )
    else:
        st.caption(
            "Coverage check unavailable: missing FY25 node revenue or player FY25 revenues."
        )

    # --- Fallback for sources population ---
    # Prefer parsed["sources"], then results["sources"], then top-level citations
    sources = []
    if parsed.get("sources"):
        sources = parsed["sources"]
    elif results.get("sources"):
        sources = results["sources"]
    elif data.get("citations"):
        sources = [{"title": url, "url": url} for url in data["citations"]]

    # ----------------------
    # Evidence (EVID:x)
    # ----------------------
    evidence_items = []
    if results.get("evidence"):
        evidence_items = results["evidence"]
    elif parsed.get("evidence"):
        evidence_items = parsed["evidence"]

    if evidence_items:
        st.markdown("### Evidence (EVID:x)")
        for ev in evidence_items:
            eid = ev.get("evidence_id") or ev.get("id")
            # Normalise evidence label to EVID:x
            if isinstance(eid, str) and eid.upper().startswith("EVID:"):
                eid_label = eid.upper()
                eid_clean = eid.split(":", 1)[1]
            else:
                eid_clean = str(eid)
                eid_label = f"EVID:{eid_clean}"

            title = ev.get("title") or "Untitled"
            url = ev.get("url") or ""
            snippet = ev.get("snippet") or ""
            source_type = ev.get("type") or ev.get("source_type")
            year = ev.get("year")

            meta_bits = []
            if source_type:
                meta_bits.append(str(source_type))
            if year:
                meta_bits.append(str(year))
            pub = ev.get("publisher")
            if pub:
                meta_bits.append(str(pub))
            meta = " • ".join(meta_bits)

            if url:
                line = f"**{eid_label}** — [{title}]({url})"
            else:
                line = f"**{eid_label}** — {title}"

            if meta:
                line += f"  \n<small>{meta}</small>"

            st.markdown(line, unsafe_allow_html=True)
            if snippet:
                st.caption(snippet)
    else:
        st.caption("No structured evidence items returned.")

    # ----------------------
    # Sources (Top Sources, deduplicated bibliography)
    # ----------------------
    financial_sources = []
    player_sources = []
    general_sources = []

    if results.get("financial_sources"):
        financial_sources = results["financial_sources"]
    if results.get("player_sources"):
        player_sources = results["player_sources"]

    # Fallback to general / generic sources
    if parsed.get("sources"):
        general_sources = parsed["sources"]
    elif results.get("sources"):
        general_sources = results["sources"]

    # Build unified, de-duplicated source list
    all_sources_raw = []
    if isinstance(financial_sources, list):
        all_sources_raw.extend(financial_sources)
    if isinstance(player_sources, list):
        all_sources_raw.extend(player_sources)
    if isinstance(general_sources, list):
        all_sources_raw.extend(general_sources)

    seen = set()
    deduped_sources = []
    for s in all_sources_raw:
        if not isinstance(s, dict):
            continue
        title = s.get("title") or s.get("publisher") or "Source"
        url = s.get("url") or ""
        year = s.get("year")
        pub = s.get("publisher")
        sig = (title, url, year, pub)
        if sig in seen:
            continue
        seen.add(sig)
        deduped_sources.append(s)

    if deduped_sources:
        st.markdown("### Top Sources")
        for src in deduped_sources:
            title = src.get("title") or src.get("publisher") or "Source"
            url = (src.get("url") or "").strip()
            year = src.get("year")
            pub = src.get("publisher")

            # Only show items that have a concrete URL so every bullet is a real link
            if not url:
                continue

            line = f"- [{title}]({url})"
            if pub or year:
                line += f" <small>({pub or ''}{', ' if pub and year else ''}{year or ''})</small>"

            st.markdown(line, unsafe_allow_html=True)
    else:
        st.caption("No primary sources listed.")

    # --- Additional Citations (from broader retrieval set) ---
    if data.get("citations"):
        with st.expander("Additional Citations"):
            for c in data["citations"]:
                st.markdown(f"- [{c}]({c})")

    # Confidence reference table (if confidence was displayed)
    if parsed.get("confidence_overall") is not None:
        st.markdown("#### Confidence Scale Reference")
        conf_data = [
            {
                "Score Range": "0.9 – 1.0",
                "Qualitative Label": "Very High",
                "Interpretation": "Data derived directly from primary company filings, multiple corroborating sources, or consistent across time.",
                "Suggested Action": "Safe to treat as validated baseline."
            },
            {
                "Score Range": "0.8 – 0.89",
                "Qualitative Label": "High",
                "Interpretation": "Model confident based on strong but not exhaustive evidence; minor extrapolations.",
                "Suggested Action": "Accept for production; flag if high-level aggregation."
            },
            {
                "Score Range": "0.7 – 0.79",
                "Qualitative Label": "Moderate",
                "Interpretation": "Confidence acceptable but some inputs inferred (e.g., peer ratios, missing disclosures).",
                "Suggested Action": "Use with caution; seek supporting evidence if critical node."
            },
            {
                "Score Range": "0.5 – 0.69",
                "Qualitative Label": "Low",
                "Interpretation": "Gaps in source coverage or high reliance on derived/secondary metrics.",
                "Suggested Action": "Review or re-run prompt with more context."
            },
            {
                "Score Range": "< 0.5",
                "Qualitative Label": "Very Low",
                "Interpretation": "Speculative estimate or minimal evidence.",
                "Suggested Action": "Don't commit to DB without human validation."
            }
        ]
        conf_df = pd.DataFrame(conf_data)
        st.table(conf_df)


# ============================================================
# Optional: Webhook Receiver
# ============================================================
# If you plan to make the Streamlit app directly handle POSTs from n8n
# (e.g. in production with public URLs), you'll use st.experimental_connection
# or a small FastAPI wrapper here. For now, the app just displays data
# from st.session_state['last_response'] which n8n can POST into via REST.

# -----------------------------------------------------------------------------
# Real database write (send latest analysis results to Google Sheets via n8n)
# -----------------------------------------------------------------------------
if False:
    st.divider()
    st.subheader("Send latest analysis to database (write_agent)")

# === CLEAN FLAT WRITE SECTION (debug-verified) ===
#
# --- Always use the latest response object — safest and most predictable ---
llm_source = st.session_state.get("parsed_llm")

######################################################################
# Safety guard: do NOT halt the whole app on fresh load (F5).
# Keep Step 6 visible but disabled until parsed_llm exists.
# Safety guard: only build payload when parsed_llm exists

payload_all = {}

if llm_source and isinstance(llm_source, dict) and len(llm_source.keys()) > 0:
    payload_all = flatten_full_llm_output(llm_source)

# Preflight: ensure everything is JSON-serializable (no NaN / Timestamp surprises)
try:
    json.dumps(payload_all)
except Exception as e:
    st.error(f"Payload contains non-serializable types: {e}")
    st.stop()

# Add lightweight metadata
# Metadata matching the Nodes sheet columns.
# Unknown values are left as None → blank cells in Google Sheets.
meta = {
    "reviewer": st.session_state.get("reviewer", "unknown"),
    "model_name": st.session_state.get("model_name"),
    "temperature": st.session_state.get("temperature"),
    "max_tokens": st.session_state.get("max_tokens"),
    "query_depth": st.session_state.get("query_depth"),
    "token_input": st.session_state.get("token_input"),
    "token_output": st.session_state.get("token_output"),
    "token_cost_usd": st.session_state.get("token_cost_usd"),
    "latency_sec": st.session_state.get("latency_sec"),
    "prompt_hash": st.session_state.get("prompt_hash"),
    "context_hash": st.session_state.get("context_hash"),
    "execution_env": st.session_state.get("execution_env", "streamlit_app"),
    "timestamp_utc": datetime.utcnow().isoformat(),
    "run_id": st.session_state.get("run_id"),
}

if "nodes_row" in payload_all:
    payload_all["nodes_row"].update(meta)

    # Stable source of truth for Step 6 UI:
    # IMPORTANT: do NOT overwrite a previously-built payload with {} on reruns.
    if payload_all:
        st.session_state["payload_all"] = payload_all


payload_hash = stable_hash(payload_all) if payload_all else None
#
# Write execution is ONLY triggered via Step 6 button handler.

# Pattern A: Write execution is ONLY triggered here (end-of-file),
# after Step 6 sets write_requested=True.
#
if st.session_state.get("write_requested"):
    # Consume the request immediately to prevent duplicate posts on reruns
    st.session_state["write_requested"] = False

    # Optional: record hash for display/diagnostics only
    try:
        st.session_state["last_write_hash"] = stable_hash(payload_all) if payload_all else None
    except Exception:
        st.session_state["last_write_hash"] = None

    if payload_all:
        _do_write_now(payload_all, payload_hash)
        # Force one refresh so Step 6 immediately reflects status / dedupe state
        st.rerun()
    else:
        # Request is stale or payload not ready
        st.session_state["write_status"] = {
            "ok": False,
            "status_code": None,
            "message": "Write request ignored: payload not ready (no parsed rows available).",
            "payload_hash": payload_hash,
        }

# --- Optional: preview payload in a collapsible section (hidden) ---
if False:
    with st.expander("Show write payload preview", expanded=False):
        st.markdown("**Validation preview:**")
        st.write(f"Node fields: {len(payload_all.get('nodes_row',{}))}")
        st.write(f"Players: {len(payload_all.get('players_rows',[]))}")
        st.write(f"Proxies: {len(payload_all.get('proxies_rows',[]))}")

        st.code(json.dumps(payload_all, indent=2), language="json")
