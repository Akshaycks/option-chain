"""
Option Chain Pro - Clean UI (v5.3)
- Better Symbol chips (no search box)
- No full page blink on refresh
- Bid/Ask multi-key fallback (NSE field names change)
"""
import time
from datetime import datetime
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

st.set_page_config(
    page_title="Option Chain Pro",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed"
)

REFRESH_SECONDS = 60
NSE_HOME = "https://www.nseindia.com"
NSE_API_V3 = f"{NSE_HOME}/api/option-chain-v3"
NSE_CONTRACT_INFO = f"{NSE_HOME}/api/option-chain-contract-info"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/option-chain",
}

POPULAR_SYMBOLS = [
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY",
    "RELIANCE", "SBIN", "HDFCBANK", "ICICIBANK", "INFY",
    "TCS", "BHARTIARTL", "ITC", "LT", "AXISBANK", "KOTAKBANK"
]

COLS_COMPACT = [
    ("IV", "impliedVolatility"),
    ("OI", "openInterest"),
    ("OI Chg", "changeinOpenInterest"),
    ("Vol", "totalTradedVolume"),
    ("LTP", "lastPrice"),
    ("Chg%", "pChange"),
]

COLS_FULL = [
    ("IV", "impliedVolatility"),
    ("OI", "openInterest"),
    ("OI Chg", "changeinOpenInterest"),
    ("Vol", "totalTradedVolume"),
    ("LTP", "lastPrice"),
    ("Chg%", "pChange"),
    ("Bid", "bid"),   # special handling below
    ("Ask", "ask"),   # special handling below
]

GREEN = "#00a05a"
RED = "#e0445a"


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get(NSE_HOME, timeout=8)
        s.get(f"{NSE_HOME}/option-chain", timeout=8)
        time.sleep(0.3)
    except Exception:
        pass
    return s


def get_instrument_type(symbol: str) -> str:
    indices = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}
    return "Indices" if symbol.upper() in indices else "Equity"


@st.cache_data(ttl=300, show_spinner=False)
def fetch_expiries(symbol: str):
    last_err = None
    for _ in range(3):
        try:
            s = _new_session()
            r = s.get(f"{NSE_CONTRACT_INFO}?symbol={symbol.upper()}", timeout=10)
            r.raise_for_status()
            exp = r.json().get("expiryDates")
            if exp:
                return exp
        except Exception as e:
            last_err = e
            time.sleep(1.2)
    raise RuntimeError(f"Expiry list nahi mili ({symbol}): {last_err}")


@st.cache_data(ttl=45, show_spinner=False)
def fetch_nse_data(symbol: str, expiry: str):
    last_err = None
    inst_type = get_instrument_type(symbol)
    for _ in range(3):
        try:
            s = _new_session()
            r = s.get(
                NSE_API_V3,
                params={"type": inst_type, "symbol": symbol.upper(), "expiry": expiry},
                timeout=12,
            )
            r.raise_for_status()
            data = r.json()
            if data and "records" in data and data["records"].get("data"):
                return data["records"], datetime.now()
        except Exception as e:
            last_err = e
            time.sleep(1.5)
    raise RuntimeError(f"NSE se data nahi aaya ({symbol}): {last_err}")


def _normalize_expiry(exp: str) -> str:
    if not exp:
        return ""
    return str(exp).strip().upper().replace(" ", "").replace("-", "").replace("/", "")


def _get_bid_ask(side_data: dict, which: str):
    """NSE kabhi different keys use karta hai. Sab try karo."""
    if which == "bid":
        keys = ["bidprice", "bidPrice", "buyPrice1", "bid", "bestBid", "buyprice1"]
    else:
        keys = ["askPrice", "askprice", "sellPrice1", "ask", "bestAsk", "sellprice1"]
    for k in keys:
        val = side_data.get(k)
        if val is not None and val != "" and val != "-":
            try:
                f = float(val)
                if f != 0:
                    return f
            except (TypeError, ValueError):
                pass
    return 0


def build_chain(records: dict, expiry: str) -> pd.DataFrame:
    rows = []
    target = _normalize_expiry(expiry)
    data_list = records.get("data", [])
    if not data_list:
        return pd.DataFrame()

    for item in data_list:
        ce = item.get("CE") or {}
        pe = item.get("PE") or {}
        item_exp = item.get("expiryDate") or ce.get("expiryDate") or pe.get("expiryDate") or ""
        if target and _normalize_expiry(item_exp) and _normalize_expiry(item_exp) != target:
            continue
        strike = item.get("strikePrice")
        if strike is None:
            continue
        row = {"Strike": float(strike)}
        for name, key in COLS_FULL:
            if name == "Bid":
                row[f"C {name}"] = _get_bid_ask(ce, "bid")
                row[f"P {name}"] = _get_bid_ask(pe, "bid")
            elif name == "Ask":
                row[f"C {name}"] = _get_bid_ask(ce, "ask")
                row[f"P {name}"] = _get_bid_ask(pe, "ask")
            else:
                row[f"C {name}"] = ce.get(key) or 0
                row[f"P {name}"] = pe.get(key) or 0
        row["C OI"] = ce.get("openInterest") or 0
        row["P OI"] = pe.get("openInterest") or 0
        row["C dOI"] = ce.get("changeinOpenInterest") or 0
        row["P dOI"] = pe.get("changeinOpenInterest") or 0
        row["C Vol"] = ce.get("totalTradedVolume") or 0
        row["P Vol"] = pe.get("totalTradedVolume") or 0
        rows.append(row)

    # Fallback: agar expiry filter se kuch nahi mila to saari rows le lo
    if not rows:
        for item in data_list:
            ce = item.get("CE") or {}
            pe = item.get("PE") or {}
            strike = item.get("strikePrice")
            if strike is None:
                continue
            row = {"Strike": float(strike)}
            for name, key in COLS_FULL:
                if name == "Bid":
                    row[f"C {name}"] = _get_bid_ask(ce, "bid")
                    row[f"P {name}"] = _get_bid_ask(pe, "bid")
                elif name == "Ask":
                    row[f"C {name}"] = _get_bid_ask(ce, "ask")
                    row[f"P {name}"] = _get_bid_ask(pe, "ask")
                else:
                    row[f"C {name}"] = ce.get(key) or 0
                    row[f"P {name}"] = pe.get(key) or 0
            row["C OI"] = ce.get("openInterest") or 0
            row["P OI"] = pe.get("openInterest") or 0
            row["C dOI"] = ce.get("changeinOpenInterest") or 0
            row["P dOI"] = pe.get("changeinOpenInterest") or 0
            row["C Vol"] = ce.get("totalTradedVolume") or 0
            row["P Vol"] = pe.get("totalTradedVolume") or 0
            rows.append(row)

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("Strike").reset_index(drop=True)


def calculate_max_pain(df: pd.DataFrame) -> float:
    if df.empty or "Strike" not in df.columns:
        return 0.0
    strikes = df["Strike"].values
    call_oi = df["C OI"].values
    put_oi = df["P OI"].values
    pain = []
    for s in strikes:
        call_pain = sum(max(0, k - s) * oi for k, oi in zip(strikes, call_oi))
        put_pain = sum(max(0, s - k) * oi for k, oi in zip(strikes, put_oi))
        pain.append(call_pain + put_pain)
    return float(strikes[pain.index(min(pain))])


# -------------------- CSS --------------------
st.markdown("""
<style>
.main .block-container {
    padding-top: 1.2rem;
    padding-bottom: 2rem;
    max-width: 1400px;
}
.main-title {
    font-size: 1.7rem;
    font-weight: 700;
    color: #111827;
    margin-bottom: 2px;
}
.main-subtitle {
    font-size: 0.88rem;
    color: #6b7280;
    margin-bottom: 1.2rem;
}
.symbol-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 1.3rem;
}
.chip {
    padding: 7px 16px;
    border-radius: 20px;
    font-size: 0.88rem;
    font-weight: 600;
    cursor: pointer;
    border: 1.5px solid #e2e8f0;
    background: #f8fafc;
    color: #374151;
    transition: all 0.15s;
}
.chip:hover {
    border-color: #93c5fd;
    background: #eff6ff;
}
.chip-active {
    background: #1a3cff !important;
    color: white !important;
    border-color: #1a3cff !important;
}
div[data-testid="stMetric"] {
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 10px;
    padding: 12px 14px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.04);
}
div[data-testid="stMetric"] label {
    font-size: 0.78rem !important;
    color: #6b7280 !important;
}
div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
    font-size: 1.3rem !important;
    font-weight: 700 !important;
}
.section-title {
    font-size: 1.05rem;
    font-weight: 650;
    color: #111827;
    margin: 1.4rem 0 0.7rem 0;
}
.ocw {
    overflow-x: auto;
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    margin-bottom: 6px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}
table.oc {
    width: 100%;
    border-collapse: separate;
    border-spacing: 0;
    font-size: 12.5px;
    color: #111827;
    font-variant-numeric: tabular-nums;
    min-width: 900px;
}
table.oc th {
    position: sticky;
    z-index: 2;
    color: #fff;
    font-weight: 700;
    padding: 7px 6px;
    text-align: center;
    white-space: nowrap;
}
table.oc thead tr:first-child th { top: 0; height: 32px; font-size: 13px; }
table.oc thead tr:nth-child(2) th { top: 32px; height: 28px; font-size: 11.5px; }
table.oc td {
    padding: 6px 7px;
    text-align: center;
    border-bottom: 1px solid #f1f5f9;
    border-right: 1px solid #f8fafc;
    white-space: nowrap;
}
.hc  { background: #c62828; }
.hc2 { background: #e53935; }
.hs  { background: #1a3cff; }
.hp  { background: #0a7d3b; }
.hp2 { background: #0f9d58; }
td.stk {
    background: #eef2ff;
    font-weight: 700;
    color: #1a3cff;
    font-size: 13px;
}
td.stk.atm {
    background: #1a3cff;
    color: #fff;
}
.tag {
    background: #ffd400;
    color: #111;
    font-size: 9px;
    border-radius: 3px;
    padding: 1px 5px;
    margin-left: 5px;
    font-weight: 800;
}
tr.atmrow td {
    border-top: 2px solid #1a3cff;
    border-bottom: 2px solid #1a3cff;
}
td.pos { color: #0a8f3c; font-weight: 600; }
td.neg { color: #d32f2f; font-weight: 600; }
td.mxc { box-shadow: inset 0 0 0 2px #d32f2f; font-weight: 800; }
td.mxp { box-shadow: inset 0 0 0 2px #0a8f3c; font-weight: 800; }
.lg {
    display: flex;
    gap: 14px;
    flex-wrap: wrap;
    margin: 8px 2px 4px;
    font-size: 12px;
    color: #6b7280;
}
.lg span.b {
    display: inline-block;
    width: 12px;
    height: 11px;
    border-radius: 3px;
    margin-right: 5px;
    vertical-align: -2px;
    border: 1px solid #cbd5e1;
}
.info-text {
    font-size: 0.84rem;
    color: #6b7280;
    margin: 6px 0 4px 0;
}
</style>
""", unsafe_allow_html=True)


def _fmt(val, name):
    if val is None or val == 0:
        return "-"
    if name in ("IV", "Chg%", "LTP", "Bid", "Ask"):
        return f"{val:,.2f}"
    return f"{val:,.0f}"


def _cell(side, name, val, itm, col_max, is_max_oi):
    bg = "#fff3c4" if itm else "#ffffff"
    style = f"background:{bg};"
    txt = _fmt(val, name)
    cls = ""
    if name in ("OI Chg", "Chg%"):
        cls = "pos" if val > 0 else ("neg" if val < 0 else "")
    if name in ("OI", "Vol"):
        pct = min(max(val / col_max * 100, 0), 100) if col_max > 0 else 0
        rgb = "224,68,90" if side == "C" else "0,160,90"
        direction = "to left" if side == "C" else "to right"
        style = (
            f"background:linear-gradient({direction},rgba({rgb},.35) {pct:.1f}%,"
            f"transparent {pct:.1f}%),{bg};"
        )
    if name == "OI" and is_max_oi:
        cls += " mxc" if side == "C" else " mxp"
    return f'<td class="{cls.strip()}" style="{style}">{txt}</td>'


def render_table(view: pd.DataFrame, spot: float, atm: float, cols: list) -> str:
    call_names = [n for n, _ in cols]
    put_names = list(reversed(call_names))
    col_max = {}
    for s in ("C", "P"):
        for n in ("OI", "Vol"):
            key = f"{s} {n}"
            if key in view.columns:
                col_max[key] = max(float(view[key].max()), 1.0)
    max_c = view.loc[view["C OI"].idxmax(), "Strike"] if not view.empty else 0
    max_p = view.loc[view["P OI"].idxmax(), "Strike"] if not view.empty else 0
    h = ['<div class="ocw"><table class="oc"><thead>']
    h.append(
        f'<tr><th colspan="{len(call_names)}" class="hc">CALLS</th>'
        f'<th rowspan="2" class="hs">Strike</th>'
        f'<th colspan="{len(put_names)}" class="hp">PUTS</th></tr>'
    )
    h.append(
        "<tr>"
        + "".join(f'<th class="hc2">{n}</th>' for n in call_names)
        + "".join(f'<th class="hp2">{n}</th>' for n in put_names)
        + "</tr></thead><tbody>"
    )
    for _, r in view.iterrows():
        k = r["Strike"]
        call_itm = k < spot
        put_itm = k > spot
        row_cls = "atmrow" if k == atm else ""
        cells = [
            _cell("C", n, r.get(f"C {n}", 0), call_itm, col_max.get(f"C {n}", 1), k == max_c)
            for n in call_names
        ]
        if k == atm:
            cells.append(f'<td class="stk atm">{k:,.0f}<span class="tag">ATM</span></td>')
        else:
            cells.append(f'<td class="stk">{k:,.0f}</td>')
        cells += [
            _cell("P", n, r.get(f"P {n}", 0), put_itm, col_max.get(f"P {n}", 1), k == max_p)
            for n in put_names
        ]
        h.append(f'<tr class="{row_cls}">' + "".join(cells) + "</tr>")
    h.append("</tbody></table></div>")
    h.append(
        '<div class="lg">'
        '<div><span class="b" style="background:#fff3c4"></span>ITM</div>'
        '<div><span class="b" style="background:#fff"></span>OTM</div>'
        '<div><span class="b" style="background:#1a3cff"></span>ATM</div>'
        '<div><span class="b" style="box-shadow:inset 0 0 0 2px #d32f2f"></span>Max Call OI</div>'
        '<div><span class="b" style="box-shadow:inset 0 0 0 2px #0a8f3c"></span>Max Put OI</div>'
        "</div>"
    )
    return "".join(h)


def grouped_chart(view, spot, c_col, p_col, title, show_zero=False):
    fig = go.Figure()
    fig.add_bar(x=view["Strike"], y=view[p_col], name="Put", marker_color=GREEN, marker_line=dict(width=0),
                hovertemplate="Put: %{y:,.0f}<extra></extra>")
    fig.add_bar(x=view["Strike"], y=view[c_col], name="Call", marker_color=RED, marker_line=dict(width=0),
                hovertemplate="Call: %{y:,.0f}<extra></extra>")
    fig.add_vline(x=spot, line_dash="dash", line_color="#111827", line_width=1.5,
                  annotation_text=f"Spot {spot:,.0f}", annotation_position="top", annotation_font=dict(size=11))
    if show_zero:
        fig.add_hline(y=0, line_color="#94a3b8", line_width=1, line_dash="dot")
    fig.update_layout(
        title=dict(text=title, font=dict(size=14, color="#111827"), x=0.01, xanchor="left"),
        barmode="group", bargap=0.15, bargroupgap=0.06,
        template="plotly_white", height=340,
        margin=dict(l=40, r=15, t=45, b=45),
        legend=dict(orientation="h", y=1.12, x=1, xanchor="right", bgcolor="rgba(255,255,255,0.9)", borderwidth=0),
        xaxis=dict(tickmode="array", tickvals=list(view["Strike"]),
                   ticktext=[f"{int(s):,}" for s in view["Strike"]], tickangle=-40, tickfont=dict(size=10), showgrid=False),
        yaxis=dict(tickformat=".3s", tickfont=dict(size=10), gridcolor="#f1f5f9"),
        hovermode="x unified", plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
    )
    return fig


def total_oi_chart(call_oi, put_oi, pcr):
    fig = go.Figure(go.Bar(
        x=["Put OI", "Call OI"], y=[put_oi, call_oi],
        marker_color=[GREEN, RED],
        text=[f"{put_oi:,.0f}", f"{call_oi:,.0f}"], textposition="outside", textfont=dict(size=12), width=0.5,
        hovertemplate="%{x}: %{y:,.0f}<extra></extra>",
    ))
    fig.update_layout(
        title=dict(text=f"Total OI  |  PCR {pcr:.2f}", font=dict(size=13, color="#111827"), x=0.5, xanchor="center"),
        template="plotly_white", height=340,
        margin=dict(l=30, r=15, t=45, b=30),
        yaxis=dict(tickformat=".3s", gridcolor="#f1f5f9", range=[0, max(put_oi, call_oi) * 1.2]),
        xaxis=dict(showgrid=False), showlegend=False,
        plot_bgcolor="#ffffff", paper_bgcolor="#ffffff",
    )
    return fig


# -------------------- Main App --------------------
st.markdown('<div class="main-title">Option Chain Pro</div>', unsafe_allow_html=True)
st.markdown('<div class="main-subtitle">Live NSE data • Auto refresh every 60 seconds</div>', unsafe_allow_html=True)

# ---------- Symbol Selection ----------
if "symbol" not in st.session_state:
    st.session_state.symbol = "NIFTY"

st.markdown("**Select Symbol**")
cols = st.columns(8)
for idx, sym in enumerate(POPULAR_SYMBOLS):
    col = cols[idx % 8]
    with col:
        if st.button(
            sym,
            key=f"btn_{sym}",
            use_container_width=True,
            type="primary" if st.session_state.symbol == sym else "secondary"
        ):
            st.session_state.symbol = sym
            st.rerun()

symbol = st.session_state.symbol


# ---------- Data Section (Fragment) ----------
@st.fragment(run_every=REFRESH_SECONDS)
def data_section():
    try:
        expiries = fetch_expiries(symbol)
    except Exception as e:
        st.error(str(e))
        st.info("Symbol check karo ya thodi der baad try karo.")
        return

    c1, c2, c3 = st.columns([2, 1.6, 1.8])
    expiry = c1.selectbox("Expiry Date", expiries, index=0, key=f"expiry_{symbol}")
    n_strikes = c2.slider("Strikes around ATM", 6, 30, 12, key=f"n_strikes_{symbol}")
    view_mode = c3.radio("Table View", ["Compact", "Full (Bid/Ask)"], horizontal=True, key=f"view_{symbol}")
    cols_list = COLS_COMPACT if view_mode.startswith("Compact") else COLS_FULL

    try:
        records, fetched_at = fetch_nse_data(symbol, expiry)
    except Exception as e:
        st.error(str(e))
        st.info("Data nahi mila. Network check karo.")
        return

    underlying = records.get("underlyingValue")
    if not underlying:
        for it in records.get("data", []):
            underlying = (it.get("CE") or it.get("PE") or {}).get("underlyingValue")
            if underlying:
                break
    spot = float(underlying or 0)
    nse_time = records.get("timestamp", "")

    full = build_chain(records, expiry)
    if full.empty or "Strike" not in full.columns:
        st.warning(f"{symbol} ke is expiry ka data available nahi hai.")
        return

    atm_pos = int((full["Strike"] - spot).abs().idxmin())
    atm = float(full.loc[atm_pos, "Strike"])
    max_pain = calculate_max_pain(full)
    call_oi = float(full["C OI"].sum())
    put_oi = float(full["P OI"].sum())
    pcr = put_oi / call_oi if call_oi else 0.0
    resistance = full.loc[full["C OI"].idxmax(), "Strike"]
    support = full.loc[full["P OI"].idxmax(), "Strike"]

    # Metrics
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Spot", f"{spot:,.2f}")
    m2.metric("ATM", f"{atm:,.0f}")
    m3.metric("PCR", f"{pcr:.2f}")
    m4.metric("Support", f"{support:,.0f}")
    m5.metric("Resistance", f"{resistance:,.0f}")
    m6.metric("Max Pain", f"{max_pain:,.0f}")

    st.markdown(
        f'<div class="info-text">NSE Time: {nse_time} &nbsp;•&nbsp; Last refresh: {fetched_at.strftime("%H:%M:%S")} &nbsp;•&nbsp; Auto every {REFRESH_SECONDS}s</div>',
        unsafe_allow_html=True
    )

    # Bid/Ask availability note
    has_bid_ask = False
    if "C Bid" in full.columns and "C Ask" in full.columns:
        has_bid_ask = (full["C Bid"].sum() + full["C Ask"].sum() + full["P Bid"].sum() + full["P Ask"].sum()) > 0
    if view_mode.startswith("Full") and not has_bid_ask:
        st.caption("⚠️ NSE public API currently does not send Bid/Ask in option-chain response. Columns will fill automatically if NSE restores the data.")

    # Table
    st.markdown('<div class="section-title">Option Chain</div>', unsafe_allow_html=True)
    lo = max(atm_pos - n_strikes, 0)
    hi = min(atm_pos + n_strikes + 1, len(full))
    view = full.iloc[lo:hi].reset_index(drop=True)
    st.markdown(render_table(view, spot, atm, cols_list), unsafe_allow_html=True)

    # Charts
    st.markdown('<div class="section-title">Open Interest & Volume</div>', unsafe_allow_html=True)
    ca, cb = st.columns([3.1, 1.1])
    with ca:
        st.plotly_chart(
            grouped_chart(view, spot, "C OI", "P OI", f"Open Interest — {symbol} {expiry}"),
            use_container_width=True, theme=None, config={"displayModeBar": False}
        )
    with cb:
        st.plotly_chart(
            total_oi_chart(call_oi, put_oi, pcr),
            use_container_width=True, theme=None, config={"displayModeBar": False}
        )
    st.plotly_chart(
        grouped_chart(view, spot, "C dOI", "P dOI", f"Change in OI — {symbol} {expiry}", show_zero=True),
        use_container_width=True, theme=None, config={"displayModeBar": False}
    )
    st.plotly_chart(
        grouped_chart(view, spot, "C Vol", "P Vol", f"Volume — {symbol} {expiry}"),
        use_container_width=True, theme=None, config={"displayModeBar": False}
    )


data_section()
