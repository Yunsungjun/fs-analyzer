"""OpenDART 데이터 수집 + 계정 매핑.

DART 원본 응답(fnlttSinglAcntAll)을 받아서 주요 계정만 골라낸다.
금액 단위는 전부 '원'으로 통일한다.
"""
import datetime as dt
import io
import os
import re
import time
import zipfile
import xml.etree.ElementTree as ET

import pandas as pd
import requests

BASE = "https://opendart.fss.or.kr/api"
# 보고서 코드: 1분기, 반기, 3분기, 사업보고서
REPRT = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}


class DartError(Exception):
    pass


DART_STATUS = {
    "010": "등록되지 않은 API 키입니다. 키를 다시 확인해주세요.",
    "011": "사용할 수 없는 API 키입니다.",
    "012": "접근할 수 없는 IP입니다.",
    "020": "오늘 DART 요청 한도(2만 건)를 넘었습니다. 내일 다시 시도해주세요.",
    "100": "요청 값이 잘못되었습니다.",
    "800": "DART 시스템 점검 중입니다.",
    "900": "DART 쪽 알 수 없는 오류입니다.",
}


def _get(path, params):
    r = requests.get(f"{BASE}/{path}", params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    status = data.get("status")
    if status == "013":  # 조회된 데이터 없음
        return None
    if status != "000":
        raise DartError(DART_STATUS.get(status, f"{status}: {data.get('message')}"))
    return data


# ---------------------------------------------------------------- 기업 목록
def load_corp_codes(api_key, cache_path):
    """상장사 목록(corp_code, corp_name, stock_code). 7일간 파일로 캐시."""
    if os.path.exists(cache_path) and time.time() - os.path.getmtime(cache_path) < 7 * 86400:
        return pd.read_csv(cache_path, dtype=str)
    r = requests.get(f"{BASE}/corpCode.xml", params={"crtfc_key": api_key}, timeout=60)
    r.raise_for_status()
    try:
        z = zipfile.ZipFile(io.BytesIO(r.content))
    except zipfile.BadZipFile:
        m = re.search(r"<status>(\d+)</status>", r.text) or re.search(r'"status"\s*:\s*"(\d+)"', r.text)
        code = m.group(1) if m else "?"
        raise DartError(DART_STATUS.get(code, f"기업 목록을 받지 못했습니다 ({code})"))
    root = ET.fromstring(z.read(z.namelist()[0]))
    rows = []
    for el in root.iter("list"):
        sc = (el.findtext("stock_code") or "").strip()
        if sc:
            rows.append({
                "corp_code": el.findtext("corp_code").strip(),
                "corp_name": el.findtext("corp_name").strip(),
                "stock_code": sc,
            })
    df = pd.DataFrame(rows)
    df.to_csv(cache_path, index=False)
    return df


def company_info(api_key, corp_code):
    d = _get("company.json", {"crtfc_key": api_key, "corp_code": corp_code})
    return d or {}


def fetch_fs(api_key, corp_code, year, reprt_code, fs_div):
    d = _get("fnlttSinglAcntAll.json", {
        "crtfc_key": api_key, "corp_code": corp_code,
        "bsns_year": str(year), "reprt_code": reprt_code, "fs_div": fs_div,
    })
    return d["list"] if d else None


def fetch_shares(api_key, corp_code, year, reprt_code="11011"):
    """보통주 (발행주식총수, 유통주식수=자기주식 제외). 없으면 (None, None)."""
    d = _get("stockTotqySttus.json", {
        "crtfc_key": api_key, "corp_code": corp_code, "bsns_year": str(year), "reprt_code": reprt_code})
    if not d:
        return None, None
    rows = d["list"]
    # se 예: "의결권이 있는주식(보통주)", "보통주", "합계"
    for want in ("보통", "합계"):
        for row in rows:
            issued = _num(row.get("istc_totqy"))
            if want in (row.get("se") or "") and issued:
                return issued, _num(row.get("distb_stock_co")) or issued
    return None, None


def fetch_latest_shares(api_key, corp_code, today=None):
    """가장 최근 보고서의 주식 수. 주식병합·분할·증자가 있었던 회사도 현재 주식 수를 쓰기 위함."""
    today = today or dt.date.today()
    y, q = today.year, (today.month - 1) // 3 + 1
    for _ in range(8):
        q -= 1
        if q == 0:
            y, q = y - 1, 4
        try:
            issued, out = fetch_shares(api_key, corp_code, y, REPRT[q])
        except DartError:
            issued = None
        if issued:
            return issued, out
    return None, None


def fetch_dividend_yield(api_key, corp_code, year):
    d = _get("alotMatter.json", {
        "crtfc_key": api_key, "corp_code": corp_code, "bsns_year": str(year), "reprt_code": "11011"})
    if not d:
        return None
    for row in d["list"]:
        if "현금배당수익률" in (row.get("se") or "") and "보통" in (row.get("stock_knd") or "보통"):
            v = (row.get("thstrm") or "").replace(",", "").strip()
            try:
                return float(v)
            except ValueError:
                return None
    return None


# ---------------------------------------------------------------- 공시 목록
DISCLOSURE_TYPES = {"A": "정기공시", "B": "주요사항", "I": "거래소공시"}
# 거래소공시는 양이 많아서 재무에 영향이 큰 것만 남김
EXCHANGE_KEYWORDS = ("병합", "분할", "증자", "감자", "합병", "공급계약", "실적", "영업(잠정)", "손익구조",
                     "자기주식", "소각", "전환사채", "배당", "감사보고서")


def fetch_disclosures(api_key, corp_code, days=365):
    """최근 1년 공시 (정기·주요사항·주요 거래소공시). 최신순 list of dict."""
    end = dt.date.today()
    bgn = end - dt.timedelta(days=days)
    out = []
    for ty, label in DISCLOSURE_TYPES.items():
        d = _get("list.json", {"crtfc_key": api_key, "corp_code": corp_code, "bgn_de": bgn.strftime("%Y%m%d"),
                               "end_de": end.strftime("%Y%m%d"), "pblntf_ty": ty, "last_reprt_at": "Y",
                               "page_count": 100})
        for r in (d or {}).get("list", []):
            name = (r.get("report_nm") or "").strip()
            if ty == "I" and not any(k in name for k in EXCHANGE_KEYWORDS):
                continue
            out.append({"date": r.get("rcept_dt", ""), "type": label, "name": " ".join(name.split()),
                        "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r.get('rcept_no')}"})
    out.sort(key=lambda x: x["date"], reverse=True)
    return out


# ---------------------------------------------------------------- 계정 매핑
# (키, sj_div 후보, IFRS 계정ID 후보, 계정명 후보(공백 제거), 계정명 포함 조건)
ITEMS = {
    "BS": [
        ("assets", ["BS"], ["ifrs-full_Assets"], ["자산총계"], None),
        ("cur_assets", ["BS"], ["ifrs-full_CurrentAssets"], ["유동자산"], None),
        ("cash", ["BS"], ["ifrs-full_CashAndCashEquivalents"], ["현금및현금성자산"], None),
        ("inventory", ["BS"], ["ifrs-full_Inventories"], ["재고자산"], None),
        ("noncur_assets", ["BS"], ["ifrs-full_NoncurrentAssets"], ["비유동자산"], None),
        ("ppe", ["BS"], ["ifrs-full_PropertyPlantAndEquipment"], ["유형자산"], None),
        ("intangible", ["BS"], ["ifrs-full_IntangibleAssetsOtherThanGoodwill",
                               "ifrs-full_IntangibleAssetsAndGoodwill"], ["무형자산", "영업권및무형자산"], None),
        ("liab", ["BS"], ["ifrs-full_Liabilities"], ["부채총계"], None),
        ("cur_liab", ["BS"], ["ifrs-full_CurrentLiabilities"], ["유동부채"], None),
        ("noncur_liab", ["BS"], ["ifrs-full_NoncurrentLiabilities"], ["비유동부채"], None),
        ("equity", ["BS"], ["ifrs-full_Equity"], ["자본총계"], None),
        ("equity_parent", ["BS"], ["ifrs-full_EquityAttributableToOwnersOfParent"],
         ["지배기업소유주지분", "지배기업의소유주에게귀속되는자본", "지배기업소유주에게귀속되는자본"], None),
    ],
    "IS": [
        ("revenue", ["IS", "CIS"], ["ifrs-full_Revenue"],
         ["매출액", "수익(매출액)", "매출", "영업수익", "매출액(영업수익)"], None),
        ("cogs", ["IS", "CIS"], ["ifrs-full_CostOfSales"], ["매출원가"], None),
        ("gross", ["IS", "CIS"], ["ifrs-full_GrossProfit"], ["매출총이익"], None),
        ("sga", ["IS", "CIS"], ["dart_TotalSellingGeneralAdministrativeExpenses"],
         ["판매비와관리비", "판매비와일반관리비"], None),
        ("op", ["IS", "CIS"], ["dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"],
         ["영업이익"], None),
        ("interest", ["IS", "CIS"], [], ["이자비용"], lambda n: n.startswith("이자비용")),
        ("fin_cost", ["IS", "CIS"], ["ifrs-full_FinanceCosts"], ["금융원가", "금융비용"], None),
        ("net", ["IS", "CIS"], ["ifrs-full_ProfitLoss"],
         ["당기순이익", "분기순이익", "반기순이익", "연결당기순이익"], None),
        ("net_parent", ["IS", "CIS"], ["ifrs-full_ProfitLossAttributableToOwnersOfParent"],
         ["지배기업소유주지분", "지배기업의소유주에게귀속되는당기순이익", "지배기업의소유주"], None),
    ],
    "CF": [
        ("cash_begin", ["CF"], [], [], lambda n: "기초" in n and "현금" in n),
        ("ocf", ["CF"], ["ifrs-full_CashFlowsFromUsedInOperatingActivities"],
         ["영업활동현금흐름", "영업활동으로인한현금흐름"], lambda n: n.startswith("영업활동") and "현금흐름" in n),
        ("icf", ["CF"], ["ifrs-full_CashFlowsFromUsedInInvestingActivities"],
         ["투자활동현금흐름", "투자활동으로인한현금흐름"], lambda n: n.startswith("투자활동") and "현금흐름" in n),
        ("fcf_fin", ["CF"], ["ifrs-full_CashFlowsFromUsedInFinancingActivities"],
         ["재무활동현금흐름", "재무활동으로인한현금흐름"], lambda n: n.startswith("재무활동") and "현금흐름" in n),
        ("cash_end", ["CF"], [], [], lambda n: "기말" in n and "현금" in n),
    ],
}


def _norm(name):
    s = re.sub(r"\s+", "", name or "")
    s = re.sub(r"^[\(\[]?[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩIVX0-9]+[\.\)\]]", "", s)
    for t in ("(손실)", "（손실）", "(이익)"):
        s = s.replace(t, "")
    return s


def _num(v):
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _pick(rows, sj_divs, ids, names, contains):
    cands = [r for r in rows if r.get("sj_div") in sj_divs]
    # IS가 따로 있으면 IS 우선
    cands.sort(key=lambda r: sj_divs.index(r.get("sj_div")))
    for r in cands:
        if r.get("account_id") in ids:
            return r
    for r in cands:
        if _norm(r.get("account_nm")) in names:
            return r
    if contains:
        for r in cands:
            if contains(_norm(r.get("account_nm"))):
                return r
    return None


def parse(rows):
    """원본 행 → {"BS":{}, "IS_q":{}, "IS_cum":{}, "CF":{}} (단위: 원)."""
    out = {"BS": {}, "IS_q": {}, "IS_cum": {}, "CF": {}}
    for key, sj, ids, names, contains in ITEMS["BS"]:
        r = _pick(rows, sj, ids, names, contains)
        out["BS"][key] = _num(r.get("thstrm_amount")) if r else None
    for key, sj, ids, names, contains in ITEMS["IS"]:
        r = _pick(rows, sj, ids, names, contains)
        q = _num(r.get("thstrm_amount")) if r else None
        cum = _num(r.get("thstrm_add_amount")) if r else None
        out["IS_q"][key] = q
        out["IS_cum"][key] = cum if cum is not None else q
    for key, sj, ids, names, contains in ITEMS["CF"]:
        r = _pick(rows, sj, ids, names, contains)
        out["CF"][key] = _num(r.get("thstrm_amount")) if r else None
    for d in (out["IS_q"], out["IS_cum"]):
        if d.get("interest") is None:  # 이자비용 항목이 없는 회사는 금융원가로 대체
            d["interest"] = d.get("fin_cost")
        if d.get("gross") is None and d.get("revenue") is not None and d.get("cogs") is not None:
            d["gross"] = d["revenue"] - d["cogs"]
    return out


# ---------------------------------------------------------------- 기간 조립
def _sub(a, b):
    if a is None or b is None:
        return None
    return a - b


def annual_periods(fetch, corp_code, n, fs_div, today=None):
    """연간 n개. fetch(corp_code, year, reprt_code, fs_div) -> rows|None. 최신이 앞."""
    today = today or dt.date.today()
    latest = None
    for y in (today.year - 1, today.year - 2):
        if fetch(corp_code, y, "11011", fs_div):
            latest = y
            break
    if latest is None:
        return []
    periods = []
    for y in range(latest, latest - n, -1):
        rows = fetch(corp_code, y, "11011", fs_div)
        if not rows:
            continue
        p = parse(rows)
        periods.append({"label": str(y), "year": y, "q": 4,
                        "BS": p["BS"], "IS": p["IS_cum"], "CF": p["CF"]})
    return periods


def quarterly_periods(fetch, corp_code, n, fs_div, today=None):
    """분기 n개 (손익·현금흐름은 3개월 값). 최신이 앞."""
    today = today or dt.date.today()
    cache = {}

    def rep(y, q):
        if (y, q) not in cache:
            rows = fetch(corp_code, y, REPRT[q], fs_div)
            cache[(y, q)] = parse(rows) if rows else None
        return cache[(y, q)]

    y, q = today.year, (today.month - 1) // 3 + 1
    periods, misses = [], 0
    while len(periods) < n and misses < 6:
        q -= 1
        if q == 0:
            y, q = y - 1, 4
        cur = rep(y, q)
        if cur is None:
            misses += 1
            continue
        prev = rep(y, q - 1) if q > 1 else None
        is_q, cf_q = {}, {}
        for key in cur["IS_cum"]:
            if q == 1:
                is_q[key] = cur["IS_cum"][key]
            elif q in (2, 3) and cur["IS_q"].get(key) is not None:
                is_q[key] = cur["IS_q"][key]
            else:
                is_q[key] = _sub(cur["IS_cum"].get(key), prev["IS_cum"].get(key) if prev else None)
        for key in cur["CF"]:
            if key == "cash_begin":
                cf_q[key] = cur["CF"][key] if q == 1 else (prev["CF"].get("cash_end") if prev else None)
            elif key == "cash_end" or q == 1:
                cf_q[key] = cur["CF"][key]
            else:
                cf_q[key] = _sub(cur["CF"].get(key), prev["CF"].get(key) if prev else None)
        periods.append({"label": f"{y}-{q}Q", "year": y, "q": q,
                        "BS": cur["BS"], "IS": is_q, "CF": cf_q})
    return periods
