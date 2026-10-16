"""재무제표 분석기.

실행: streamlit run app.py
"""
import datetime as dt
import html
import inspect
import os

import pandas as pd
import requests
import streamlit as st

import analysis
import dart
import price as price_mod
import sec

APP_VERSION = "2026-10-15 v13"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(APP_DIR, "api_key.txt")
DATA_KEY_FILE = os.path.join(APP_DIR, "data_key.txt")
SEC_CONTACT_FILE = os.path.join(APP_DIR, "sec_contact.txt")
SEC_TICKERS = os.path.join(APP_DIR, "sec_tickers.csv")
CORP_CACHE = os.path.join(APP_DIR, "corp_codes.csv")

st.set_page_config(page_title="재무제표 분석기", page_icon="📊", layout="wide")

# ------------------------------------------------------------------ 스타일
st.markdown("""
<style>
.block-container {padding-top: 3.2rem; max-width: 1400px;}
.co-title {font-size: 1.5rem; font-weight: 700; margin: 0;}
.co-meta {color: #9AA4B2; font-size: .85rem; margin: .2rem 0 .8rem;}
.kpi-grid {display: grid; grid-template-columns: 1fr 1fr; gap: 10px;}
.kpi {background: #2A2F36; border-radius: 12px; padding: 12px 14px;}
.kpi .l {color: #9AA4B2; font-size: .78rem;}
.kpi .v {font-size: 1.45rem; font-weight: 700; margin-top: 2px; font-variant-numeric: tabular-nums;}
.kpi .s {color: #9AA4B2; font-size: .72rem;}
.chips {display: flex; flex-wrap: wrap; gap: 8px; margin: .4rem 0 1rem;}
.chip {background: #2A2F36; border-radius: 10px; padding: 8px 12px; font-size: .85rem; border-left: 4px solid #9AA4B2;}
.chip b {color: #9AA4B2; font-weight: 600; margin-right: 6px;}
.lv-good {border-left-color: #4FC3A1 !important;}
.lv-neutral {border-left-color: #9AA4B2 !important;}
.lv-warn {border-left-color: #E8B04B !important;}
.lv-bad {border-left-color: #E8705E !important;}
.fs-wrap {overflow-x: auto; background: #2A2F36; border-radius: 12px; padding: 6px 4px;}
table.fs {width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; font-size: .95rem;}
table.fs th {color: #9AA4B2; font-weight: 500; font-size: .8rem; text-align: right; padding: 8px 14px; white-space: nowrap;}
table.fs th:first-child {text-align: left;}
table.fs td {padding: 7px 14px; text-align: right; border-top: 1px solid #353B44; white-space: nowrap;}
table.fs td:first-child {text-align: left;}
table.fs tr.l0 td {font-weight: 700;}
table.fs tr.l1 td:first-child {padding-left: 28px; color: #C9CFD8;}
table.fs tr.l2 td:first-child {padding-left: 44px; color: #9AA4B2;}
table.fs tr.ratio td {color: #5EC4E8;}
table.fs tr.ratio td:first-child {cursor: help;}
table.fs tr.sep td {border-top: 2px solid #4A515C;}
table.fs td.neg {color: #E8705E;}
table.fs a {color: #5EC4E8; text-decoration: none;}
table.fs a:hover {text-decoration: underline;}
table.disc td, table.disc th {text-align: left !important; white-space: normal;}
.tag {display: inline-block; font-size: .75rem; padding: 2px 8px; border-radius: 999px; background: #353B44; color: #C9CFD8;}
.cm {background: #2A2F36; border-radius: 10px; padding: 10px 14px; margin: 8px 0; border-left: 4px solid #9AA4B2;}
.cm .t {font-weight: 700; margin-bottom: 3px;}
.cm .b {color: #C9CFD8; font-size: .9rem; line-height: 1.55;}
</style>
""", unsafe_allow_html=True)

UNITS = {"억": 1e8, "백만": 1e6, "만": 1e4, "원": 1}
UNITS_US = ["백만달러", "십억달러", "억원(환산)"]

ROWS = {
    "BS": [("assets", "자산총계", 0), ("cur_assets", "유동자산", 1), ("cash", "현금및현금성자산", 2),
           ("inventory", "재고자산", 2), ("noncur_assets", "비유동자산", 1), ("ppe", "유형자산", 2),
           ("intangible", "무형자산", 2), ("liab", "부채총계", 0), ("cur_liab", "유동부채", 1),
           ("noncur_liab", "비유동부채", 1), ("equity", "자본총계", 0)],
    "IS": [("revenue", "매출액", 0), ("cogs", "매출원가", 1), ("gross", "매출총이익", 0),
           ("sga", "판매비와관리비", 1), ("op", "영업이익", 0), ("interest", "이자·금융비용", 1),
           ("net", "당기순이익", 0)],
    "CF": [("cash_begin", "기초현금및현금성자산", 1), ("ocf", "영업활동현금흐름", 0),
           ("icf", "투자활동현금흐름", 0), ("fcf_fin", "재무활동현금흐름", 0),
           ("cash_end", "기말현금및현금성자산", 1)],
}
RATIO_ROWS = {
    "BS": [("debt_ratio", "부채비율 (%)", "%", "부채총계 ÷ 자본총계. 장기 안정성. 80~120%가 적정"),
           ("current_ratio", "유동비율", "x", "유동자산 ÷ 유동부채. 단기 안정성(1년 안에 망하는지). 1~1.6 정상"),
           ("simple_asset_ratio", "단순자산비율", "x", "비유동자산 ÷ 유동자산"),
           ("detail_asset_ratio", "상세자산비율", "x", "유형자산(공장) ÷ 재고자산. 값보다 일정하게 유지되는지가 중요")],
    "IS": [("gross_margin", "매출총이익률 (%)", "%", "매출총이익 ÷ 매출. 마진율"),
           ("op_margin", "영업이익률 (%)", "%", "영업이익 ÷ 매출. 관리비까지 빼고 남는 비율"),
           ("net_margin", "당기순이익률 (%)", "%", "당기순이익 ÷ 매출. 일회성 손익까지 반영"),
           ("roe", "ROE (%)", "%", "당기순이익 ÷ 자본총계. 주주 돈으로 번 수익률"),
           ("roa", "ROA (%)", "%", "당기순이익 ÷ 자산총계. 빚 포함 전체 자산으로 번 수익률")],
    "CF": [],
}
DUPONT = [("net_margin", "순이익률 (%)", "%"), ("asset_turnover", "자산회전율 (회)", "x2"),
          ("leverage", "레버리지 (자산÷자본, 배)", "x2"), ("roe", "= ROE (%)", "%")]


# ------------------------------------------------------------------ 데이터 접근 (캐시)
def _read_key(name, path, session_name):
    k = None
    try:
        k = st.secrets.get(name)
    except Exception:
        pass
    k = k or os.environ.get(name)
    if not k and os.path.exists(path):
        k = open(path, encoding="utf-8").read().strip()
    return k or st.session_state.get(session_name)


def get_api_key():
    return _read_key("DART_API_KEY", KEY_FILE, "api_key")


def get_data_key():
    return _read_key("DATA_GO_KR_KEY", DATA_KEY_FILE, "data_key")


@st.cache_data(ttl=86400, show_spinner=False)
def c_fetch(key, corp, year, reprt, fs):
    return dart.fetch_fs(key, corp, year, reprt, fs)


@st.cache_data(ttl=7 * 86400, show_spinner="상장사 목록을 받는 중 (처음 한 번만)...")
def c_corps(key):
    return dart.load_corp_codes(key, CORP_CACHE)


@st.cache_data(ttl=86400, show_spinner=False)
def c_company(key, corp):
    return dart.company_info(key, corp)


@st.cache_data(ttl=86400, show_spinner=False)
def c_shares_div(key, corp, year):
    try:
        shares = dart.fetch_latest_shares(key, corp)
    except Exception:
        shares = (None, None)
    try:
        div = dart.fetch_dividend_yield(key, corp, year)
    except Exception:
        div = None
    return shares, div


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def c_disclosures(key, corp):
    try:
        return dart.fetch_disclosures(key, corp), None
    except Exception as e:
        return [], str(e)


@st.cache_data(ttl=7 * 86400, show_spinner="미국 상장사 목록을 받는 중 (처음 한 번만)...")
def c_sec_tickers(contact):
    return sec.load_tickers(SEC_TICKERS, contact)


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def c_sec_facts(cik, contact):
    return sec.company_facts(cik, contact)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def c_sec_disclosures(cik, contact):
    try:
        return sec.disclosures(cik, contact), None
    except Exception as e:
        return [], str(e)


@st.cache_data(ttl=900, show_spinner=False)
def c_price_us(ticker):
    return price_mod.get_price_us(ticker)


@st.cache_data(ttl=3600, show_spinner=False)
def c_fx():
    return price_mod.usd_krw()


@st.cache_data(ttl=900, show_spinner=False)
def c_price(stock_code, corp_cls, data_key):
    return price_mod.get_price(stock_code, corp_cls, data_key)


# ------------------------------------------------------------------ 표시 도우미
def fmt_amt(v, div):
    return "N/A" if v is None else f"{v / div:,.0f}"


def fmt_ratio(v, kind):
    if v is None:
        return "N/A"
    if kind == "%":
        return f"{v:.1f}%"
    if kind == "x2":
        return f"{v:.2f}"
    return f"{v:.1f}"


def fmt_money(v):
    if v is None:
        return "N/A"
    if abs(v) >= 1e12:
        return f"{v / 1e12:,.1f}조"
    return f"{v / 1e8:,.0f}억"


def render_table(periods, rs, sj, unit, roe_label, div, overrides=None):
    overrides = overrides or {}
    head = "".join(f"<th>{html.escape(p['label'])} ({unit})</th>" for p in periods)
    body = []
    for key, label, lvl in ROWS[sj]:
        label = overrides.get(key, label)
        cells = []
        for p in periods:
            v = p[sj].get(key)
            cls = ' class="neg"' if v is not None and v < 0 else ""
            cells.append(f"<td{cls}>{fmt_amt(v, div)}</td>")
        body.append(f'<tr class="l{lvl}"><td>{label}</td>{"".join(cells)}</tr>')
    for i, (key, label, kind, tip) in enumerate(RATIO_ROWS[sj]):
        if key == "roe":
            label = roe_label
        sep = " sep" if i == 0 else ""
        cells = "".join(f"<td>{fmt_ratio(r.get(key), kind)}</td>" for r in rs)
        body.append(f'<tr class="ratio{sep}"><td title="{html.escape(tip)}">{label}</td>{cells}</tr>')
    st.markdown(f'<div class="fs-wrap"><table class="fs"><thead><tr><th>계정</th>{head}</tr></thead>'
                f'<tbody>{"".join(body)}</tbody></table></div>', unsafe_allow_html=True)


def render_comments(items):
    for it in items:
        st.markdown(f'<div class="cm lv-{it["level"]}"><div class="t">{html.escape(it["title"])}</div>'
                    f'<div class="b">{html.escape(it["body"])}</div></div>', unsafe_allow_html=True)


def render_kpis(k):
    cards = [
        ("현재가", k.get("price_str") or (f"{k['price']:,.0f}원" if k.get("price") else "N/A"), k.get("price_src", "")),
        ("시가총액", k.get("mcap_str") or fmt_money(k.get("mcap")), k.get("mcap_sub", "")),
        ("PER", f"{k['per']:.2f}" if k.get("per") and k["per"] > 0 else ("적자" if k.get("per") else "N/A"),
         k.get("per_basis", "")),
        ("PBR", f"{k['pbr']:.2f}" if k.get("pbr") else "N/A", "지배주주 자본 기준"),
        ("배당수익률", f"{k['div']:.2f}%" if k.get("div") else "N/A", ""),
        ("ROE", f"{k['roe']:.1f}%" if k.get("roe") is not None else "N/A", "최근 연간"),
    ]
    st.markdown('<div class="kpi-grid">' + "".join(
        f'<div class="kpi"><div class="l">{l}</div><div class="v">{v}</div><div class="s">{s}</div></div>'
        for l, v, s in cards) + "</div>", unsafe_allow_html=True)


def render_chart(series, ticker, empty_msg="주가 데이터를 가져오지 못했습니다", currency="원"):
    if series is None or not len(series):
        st.markdown('<div class="kpi" style="height:290px;display:flex;align-items:center;justify-content:center;'
                    f'color:#9AA4B2;text-align:center">{empty_msg}</div>', unsafe_allow_html=True)
        return
    try:
        import plotly.graph_objects as go
        fig = go.Figure(go.Scatter(x=series.index, y=series.values, mode="lines",
                                   line=dict(color="#5EC4E8", width=2),
                                   hovertemplate=("%{x|%Y-%m-%d}<br>종가 $%{y:,.2f}<extra></extra>" if currency == "$"
                                                  else "%{x|%Y-%m-%d}<br>종가 %{y:,.0f}원<extra></extra>")))
        fig.update_layout(height=290, margin=dict(l=10, r=10, t=30, b=10),
                          paper_bgcolor="#2A2F36", plot_bgcolor="#2A2F36", font=dict(color="#9AA4B2"),
                          title=dict(text=ticker, font=dict(size=12), x=0.02),
                          xaxis=dict(showgrid=False), yaxis=dict(gridcolor="#353B44", tickformat=","))
        cfg = {"displayModeBar": False}
        if "width" in inspect.signature(st.plotly_chart).parameters:
            st.plotly_chart(fig, width="stretch", config=cfg)
        else:
            st.plotly_chart(fig, use_container_width=True, config=cfg)
    except ImportError:
        st.line_chart(series)


# ------------------------------------------------------------------ 사이드바
def _secret(name):
    try:
        return st.secrets.get(name)
    except Exception:
        return None


# 웹에 올린 버전(Streamlit Cloud)은 키를 Secrets에 넣으므로, 키 입력·저장 화면을 숨긴다
PUBLIC_MODE = bool(_secret("DART_API_KEY"))
api_key = get_api_key()
with st.sidebar:
    st.markdown("### 설정")
    if not api_key and not PUBLIC_MODE:
        st.caption("OpenDART API 키를 넣으면 모든 국내 상장사를 조회할 수 있습니다.")
        k_in = st.text_input("OpenDART API 키", type="password")
        if k_in:
            st.session_state["api_key"] = k_in.strip()
            if st.button("이 컴퓨터에 키 저장"):
                with open(KEY_FILE, "w", encoding="utf-8") as f:
                    f.write(k_in.strip())
                st.success("저장했습니다.")
            api_key = k_in.strip()
    data_key = get_data_key()
    with (st.container() if PUBLIC_MODE else st.expander("주가 설정 (공공데이터포털)", expanded=False)):
        if not PUBLIC_MODE:
            st.caption("Yahoo 주가가 안 받아질 때 공공데이터포털 전일 종가로 대신 보여줍니다. "
                       "data.go.kr에서 '금융위원회_주식시세정보' 활용신청 후 받은 인증키를 넣으세요.")
            if data_key:
                st.caption("✅ 키가 등록되어 있습니다.")
            else:
                d_in = st.text_input("공공데이터포털 인증키", type="password")
                if d_in:
                    st.session_state["data_key"] = d_in.strip()
                    data_key = d_in.strip()
                    if st.button("이 컴퓨터에 공공데이터 키 저장"):
                        with open(DATA_KEY_FILE, "w", encoding="utf-8") as f:
                            f.write(d_in.strip())
                        st.success("저장했습니다.")
    sec_contact = _secret("SEC_CONTACT") or (
        open(SEC_CONTACT_FILE, encoding="utf-8").read().strip() if os.path.exists(SEC_CONTACT_FILE) else None)
    with (st.container() if PUBLIC_MODE else st.expander("미국 주식 설정 (SEC)", expanded=False)):
        if not PUBLIC_MODE:
            st.caption("미국 재무제표는 SEC EDGAR에서 받습니다. 키는 필요 없어요. "
                       "SEC가 접속을 거부할 때만 연락용 이메일을 넣으면 됩니다 (SEC 규정상 요청 헤더에만 쓰입니다).")
            if sec_contact:
                st.caption(f"✅ 등록됨: {sec_contact}")
            else:
                c_in = st.text_input("연락용 이메일 (선택)")
                if c_in and st.button("저장", key="save_sec"):
                    with open(SEC_CONTACT_FILE, "w", encoding="utf-8") as f:
                        f.write(c_in.strip())
                    sec_contact = c_in.strip()
                    st.success("저장했습니다.")
    inv_label = st.radio("재고를 쌓아두고 파는 회사인가요?", ["자동 판단", "예 (제조·유통)", "아니오 (주문생산·서비스)"],
                         help="자동 판단은 재고가 자산의 10% 이상이면 '예'로 봅니다.")
    inv_mode = {"자동 판단": "auto", "예 (제조·유통)": "yes", "아니오 (주문생산·서비스)": "no"}[inv_label]
    with st.expander("재무제표 보는 순서", expanded=False):
        st.markdown("""
0. **기간부터** 확인 (언제 기준? 결산월은?)
1. **부채비율**: 장기 안정성. 80~120% 적정, 금리 오르면 적정선 ↓
2. **유동비율**: 단기 안정성. 1 미만이 계속이면 '갑'
3. **상세자산비율**: 재고형 회사만. 일정하게 유지되는지
4. **매출**이 늘고 있는지
5. **마진율·영업이익률**: 매출↑인데 이익률도↑면 최고
6. **투자활동 현금흐름**: 마이너스가 정상 (투자 중), 플러스면 주석 확인
7. 마지막으로 **재무형 주식인지, 기대형 주식인지** 판단

공시 시차: 분기 45일, 연간 90일
""")

# ------------------------------------------------------------------ 상단 컨트롤
st.markdown("#### 📊 재무제표 분석기")
st.caption("국내·미국 상장사의 재무제표를 공시 원문(DART·SEC) 그대로 보고, 주요 재무비율을 자동으로 계산해 줍니다. "
           "투자 권유가 아닌 공부·참고용입니다.")
c_mkt, c_search, c_freq, c_n, c_unit, c_fs = st.columns([1.3, 3.4, 1.4, 1, 1.3, 1.4])
with c_mkt:
    market = st.segmented_control("시장", ["국내", "미국"], default="국내", key="market") or "국내"
is_us = market == "미국"
with c_freq:
    freq = st.segmented_control("주기", ["연간", "분기"], default="연간") or "연간"
with c_n:
    n = st.selectbox("기간", [3, 5, 8, 10], index=1)
with c_unit:
    unit = st.selectbox("단위", UNITS_US if is_us else list(UNITS), index=0, key=f"unit_{market}")
with c_fs:
    fs_label = st.segmented_control("재무제표", ["연결", "별도"], default="연결", disabled=is_us) or "연결"
is_q = freq == "분기"

periods, kpi, title, meta, notes = [], {}, "", "", []
price_series, ticker, currency = None, None, "원"
row_overrides, discs, derr, disc_caption = {}, [], None, ""


def _ttm_from(q4, key):
    vals = [p["IS"].get(key) for p in q4]
    return sum(vals) if len(vals) == 4 and all(v is not None for v in vals) else None


if is_us:
    # ================================================================ 미국 (SEC EDGAR)
    currency = "$"
    try:
        tick = c_sec_tickers(sec_contact)
    except Exception as e:
        st.error(f"SEC 종목 목록을 받지 못했습니다: {e}")
        st.stop()
    with c_search:
        q = st.text_input("티커 또는 영문 회사명", value=st.session_state.get("last_q_us", "MRVL"), key="q_us")
    q = q.strip()
    if not q:
        st.info("티커(예: MRVL)나 영문 회사명을 입력하세요.")
        st.stop()
    st.session_state["last_q_us"] = q
    m = tick[(tick["ticker"].str.upper() == q.upper()) | tick["title"].str.contains(q, case=False, regex=False)].copy()
    if m.empty:
        st.warning(f"'{q}'에 해당하는 미국 상장사가 없습니다. 티커나 영문 이름으로 검색해 주세요.")
        st.stop()
    m["rank"] = (m["ticker"].str.upper() != q.upper()).astype(int) * 1000 + m["title"].str.len()
    m = m.sort_values("rank").drop_duplicates("cik").head(30)
    if len(m) > 1:
        opts = [f"{r.title} ({r.ticker})" for r in m.itertuples()]
        row = m.iloc[opts.index(st.selectbox("검색 결과", opts, index=0))]
    else:
        row = m.iloc[0]
    cik, us_ticker, us_name = row["cik"], row["ticker"], row["title"]
    try:
        with st.spinner("SEC에서 재무제표를 가져오는 중..."):
            facts = c_sec_facts(cik, sec_contact)
    except Exception as e:
        st.error(f"SEC 오류: {e}")
        st.stop()
    if not facts or not (facts.get("facts") or {}).get("us-gaap"):
        st.warning("이 회사는 미국 회계기준(US-GAAP) 재무 데이터가 없습니다. 외국 기업(20-F 제출, 예: TSMC ADR)은 아직 지원하지 않아요.")
        st.stop()
    periods = sec.quarterly_periods(facts, n) if is_q else sec.annual_periods(facts, n)
    if not periods:
        st.warning("SEC에 공시된 재무제표를 찾지 못했습니다.")
        st.stop()
    annual = sec.annual_periods(facts, 1)
    q4 = periods[:4] if is_q else sec.quarterly_periods(facts, 4)

    fx = c_fx()
    div = {"백만달러": 1e6, "십억달러": 1e9}.get(unit) or (1e8 / fx if fx else None)
    if div is None:
        notes.append("환율을 받지 못해 백만달러로 표시합니다.")
        unit, div = "백만달러", 1e6
    if unit == "억원(환산)":
        notes.append(f"원화 환산은 현재 환율(1달러 = {fx:,.0f}원)을 모든 기간에 똑같이 적용했습니다.")

    pinfo = c_price_us(us_ticker)
    price_series, ticker = pinfo["series"], pinfo["source"]
    price = float(price_series.iloc[-1]) if price_series is not None and len(price_series) else None
    shares = sec.shares_outstanding(facts)
    mcap = price * shares if price and shares else None
    a0 = annual[0] if annual else None
    ttm = _ttm_from(q4, "net_parent")
    if ttm is None:
        ttm = _ttm_from(q4, "net")
    net_a = ttm if ttm is not None else (a0 and (a0["IS"].get("net_parent") or a0["IS"].get("net")))
    eq_l = (q4[0]["BS"] if q4 else a0["BS"]) if (q4 or a0) else {}
    eq_a = eq_l.get("equity_parent") or eq_l.get("equity")
    dps = sec.dividends_ttm(facts)
    kpi = {
        "price": price,
        "price_str": f"${price:,.2f}" if price else "N/A",
        "price_src": (f"{price_series.index[-1]:%m/%d} 기준" + (f" · 약 {price * fx / 1e4:,.1f}만원" if fx else "")) if price else "",
        "mcap": mcap,
        "mcap_str": (f"${mcap / 1e9:,.1f}B" if mcap else "N/A"),
        "mcap_sub": (f"약 {mcap * fx / 1e12:,.1f}조원" if mcap and fx else ""),
        "div": (dps / price * 100) if dps and price else None,
        "per": (mcap / net_a) if mcap and net_a else None,
        "pbr": (mcap / eq_a) if mcap and eq_a else None,
        "roe": analysis.ratios(a0)["roe"] if a0 else None,
        "per_basis": "최근 4분기 순이익 기준" if ttm is not None else "최근 연간 순이익 기준",
    }
    title = f"{us_name} ({us_ticker})"
    meta = (f"결산일 {periods[0]['end']} · 연결 · {freq} · SEC {'10-Q' if is_q else '10-K'} 기준 · "
            f"열 이름은 결산 연월")
    notes.append("미국 기업은 연구개발비(R&D)를 판관비와 따로 적기 때문에, '영업비용' 칸에 판관비와 R&D를 합친 "
                 "영업비용 전체를 넣었습니다. 무형자산에는 영업권을 포함했습니다.")
    if is_q:
        notes.append("분기 보기의 손익·현금흐름은 해당 분기 3개월 값(4분기는 연간 − 3분기 누적), ROE·ROA는 ×4 연환산입니다.")
    row_overrides = {"sga": "영업비용 (판관비+R&D)", "intangible": "무형자산 (영업권 포함)", "interest": "이자비용"}
    discs, derr = c_sec_disclosures(cik, sec_contact)
    disc_caption = ("최근 1년 연간보고서(10-K)·분기보고서(10-Q)·수시공시(8-K)입니다. 누르면 SEC 원문(영문)이 새 창으로 열립니다.")

else:
    # ================================================================ 국내 (DART)
    div = UNITS[unit]
    if not api_key:
        st.info("왼쪽 설정에 OpenDART API 키를 넣으면 국내 상장사를 조회할 수 있습니다. "
                "키는 opendart.fss.or.kr에서 무료로 발급받을 수 있어요.")
        st.stop()
    try:
        corps = c_corps(api_key)
    except dart.DartError as e:
        st.error(f"DART 오류: {e}")
        st.stop()
    except requests.RequestException:
        st.error("DART 서버에 연결하지 못했습니다. 인터넷 연결을 확인해주세요.")
        st.stop()
    with c_search:
        q = st.text_input("기업명 또는 종목코드", value=st.session_state.get("last_q", "삼성전기"), key="q_kr")
    q = q.strip()
    if not q:
        st.info("기업명이나 종목코드를 입력하세요.")
        st.stop()
    st.session_state["last_q"] = q
    m = corps[corps["corp_name"].str.contains(q, case=False, regex=False) | (corps["stock_code"] == q)].copy()
    if m.empty:
        st.warning(f"'{q}'에 해당하는 상장사가 없습니다. 미국 주식이면 위에서 '미국'을 선택하세요.")
        st.stop()
    m["rank"] = (m["corp_name"] != q).astype(int) * 1000 + m["corp_name"].str.len()
    m = m.sort_values("rank").head(30)
    if len(m) > 1:
        opts = [f"{r.corp_name} ({r.stock_code})" for r in m.itertuples()]
        chosen = st.selectbox("검색 결과", opts, index=0)
        row = m.iloc[opts.index(chosen)]
    else:
        row = m.iloc[0]
    corp_code, corp_name, stock_code = row["corp_code"], row["corp_name"], row["stock_code"]

    fetch = lambda c, y, r, f: c_fetch(api_key, c, y, r, f)
    fs_div = "CFS" if fs_label == "연결" else "OFS"
    try:
        with st.spinner("DART에서 재무제표를 가져오는 중..."):
            info = c_company(api_key, corp_code)
            builder = dart.quarterly_periods if is_q else dart.annual_periods
            periods = builder(fetch, corp_code, n, fs_div)
            if not periods and fs_div == "CFS":
                fs_div = "OFS"
                periods = builder(fetch, corp_code, n, fs_div)
                if periods:
                    notes.append("연결재무제표가 없는 회사라 별도 기준으로 보여드립니다.")
            annual = periods if not is_q else dart.annual_periods(fetch, corp_code, 1, fs_div)
    except dart.DartError as e:
        st.error(f"DART 오류: {e}")
        st.stop()
    except requests.RequestException:
        st.error("DART 서버에 연결하지 못했습니다. 잠시 후 다시 시도해주세요.")
        st.stop()
    if not periods:
        st.warning("DART에 공시된 재무제표가 없습니다 (금융회사·신규상장사 등은 형식이 달라 안 나올 수 있어요).")
        st.stop()

    pinfo = c_price(stock_code, info.get("corp_cls"), data_key)
    price_series, ticker = pinfo["series"], pinfo["source"]
    price = float(price_series.iloc[-1]) if price_series is not None and len(price_series) else None
    a0 = annual[0] if annual else None
    (issued, outstanding), divy = c_shares_div(api_key, corp_code, a0["year"]) if a0 else ((None, None), None)
    mcap = price * issued if price and issued else pinfo.get("mcap")          # 시가총액: 발행주식 기준
    mcap_out = price * outstanding if price and outstanding else mcap        # PER·PBR용: 자기주식 제외 (= 주가 ÷ EPS)
    try:
        q4 = periods[:4] if is_q else dart.quarterly_periods(fetch, corp_code, 4, fs_div)
    except Exception:
        q4 = []
    ttm = _ttm_from(q4, "net_parent")
    if ttm is None:
        ttm = _ttm_from(q4, "net")
    net_a = ttm if ttm is not None else (a0 and (a0["IS"].get("net_parent") or a0["IS"].get("net")))
    eq_a = a0 and (a0["BS"].get("equity_parent") or a0["BS"].get("equity"))
    kpi = {
        "price": price, "price_src": (f"{price_series.index[-1]:%m/%d} 기준 · KRX 정규장"
                      + (" · 15~20분 지연" if pinfo["source"].startswith("Yahoo") else " 종가")) if price else "",
        "mcap": mcap, "div": divy,
        "per": (mcap_out / net_a) if mcap_out and net_a else None,
        "pbr": (mcap_out / eq_a) if mcap_out and eq_a else None,
        "roe": analysis.ratios(a0)["roe"] if a0 else None,
        "per_basis": "최근 4분기 지배주주 순이익 기준" if ttm is not None else "최근 연간 순이익 기준",
    }
    acc_mt = (info.get("acc_mt") or "").lstrip("0")
    title = f"{corp_name} ({stock_code})"
    meta = (f"결산월 {acc_mt or '?'}월 · {'연결' if fs_div == 'CFS' else '별도'} · {freq} · "
            f"최신 {periods[0]['label']} 보고서 기준")
    if acc_mt and acc_mt != "12":
        notes.append(f"12월 결산이 아닌 회사({acc_mt}월 결산)입니다. 연도 표기가 실제 기간과 다를 수 있으니 주의하세요.")
    if is_q:
        notes.append("분기 보기의 손익·현금흐름은 해당 분기 3개월 값, ROE·ROA는 ×4 연환산입니다.")
    discs, derr = c_disclosures(api_key, corp_code)
    disc_caption = ("최근 1년 정기보고서·주요사항보고서와, 거래소 공시 중 증자·병합·합병·공급계약·실적 관련 공시만 보여줍니다. "
                    "누르면 DART 원문이 새 창으로 열립니다. 분석 코멘트에 '주석 확인'이 나오면 가장 최근 정기공시를 열어보세요.")

# ------------------------------------------------------------------ 본문
st.markdown(f'<p class="co-title">{html.escape(title)}</p><p class="co-meta">{html.escape(meta)}</p>',
            unsafe_allow_html=True)
for n_ in notes:
    st.caption("ⓘ " + n_)

left, right = st.columns([2, 1.15])
with left:
    render_chart(price_series, ticker, currency=currency)
    if price_series is None and pinfo["errors"]:
        with st.expander("주가를 못 가져온 이유"):
            for e in pinfo["errors"]:
                st.caption("· " + e)
with right:
    render_kpis(kpi)

items, summary = analysis.diagnose(periods, kpi, inv_mode=inv_mode, is_quarterly=is_q)
if summary:
    st.markdown("##### 한눈에 보기")
    st.markdown('<div class="chips">' + "".join(
        f'<div class="chip lv-{lv}"><b>{k}</b>{html.escape(t)}</div>' for k, (lv, t) in summary.items())
        + "</div>", unsafe_allow_html=True)
render_comments([i for i in items if i["section"] == "overall"])

rs = [analysis.ratios(p, 4 if is_q else 1) for p in periods]
roe_label = "ROE (%, 연환산)" if is_q else "ROE (%)"
t_bs, t_is, t_cf, t_disc = st.tabs(["재무상태표", "손익계산서", "현금흐름표", "공시 목록"])
with t_bs:
    render_table(periods, rs, "BS", unit, roe_label, div, row_overrides)
    st.markdown("###### 분석 코멘트")
    render_comments([i for i in items if i["section"] == "bs"])
with t_is:
    render_table(periods, rs, "IS", unit, roe_label, div, row_overrides)
    with st.expander("ROE 쪼개보기 (듀폰 분석): 장사 실력인지, 빚의 힘인지"):
        df = pd.DataFrame({p["label"]: [fmt_ratio(r.get(k), kind) for k, _, kind in DUPONT]
                           for p, r in zip(periods, rs)}, index=[lbl for _, lbl, _ in DUPONT])
        st.dataframe(df)
        st.caption("순이익률과 자산회전율은 장사 실력, 레버리지는 빚을 얼마나 썼는지입니다. "
                   "레버리지가 2.5배를 넘는데 ROE가 높다면 빚으로 끌어올린 부분이 큽니다.")
    st.markdown("###### 분석 코멘트")
    render_comments([i for i in items if i["section"] == "is"])
with t_cf:
    render_table(periods, rs, "CF", unit, roe_label, div, row_overrides)
    st.markdown("###### 분석 코멘트")
    cf_items = [i for i in items if i["section"] == "cf"]
    if cf_items:
        render_comments(cf_items)
    else:
        st.caption("현금흐름 데이터가 없습니다.")
with t_disc:
    if derr:
        st.caption(f"공시 목록을 불러오지 못했습니다: {derr}")
    elif not discs:
        st.caption("최근 1년간 표시할 공시가 없습니다.")
    else:
        rows_html = "".join(
            f'<tr><td>{x["date"][:4]}-{x["date"][4:6]}-{x["date"][6:]}</td><td><span class="tag">{x["type"]}</span></td>'
            f'<td><a href="{html.escape(x["url"])}" target="_blank" rel="noopener">{html.escape(x["name"])}</a></td></tr>'
            for x in discs[:40])
        st.markdown(f'<div class="fs-wrap"><table class="fs disc"><thead><tr><th>날짜</th><th>구분</th><th>보고서</th></tr></thead>'
                    f'<tbody>{rows_html}</tbody></table></div>', unsafe_allow_html=True)
        st.caption(disc_caption)

st.caption("데이터: 금융감독원 OpenDART, 미국 SEC EDGAR, 주가 Yahoo Finance. 자동 진단은 일반적인 재무비율 해석 기준에 따른 참고용이며 투자 권유가 아닙니다. · 버전 " + APP_VERSION)
