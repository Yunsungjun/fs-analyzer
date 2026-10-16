"""미국 SEC EDGAR 데이터 (미국판 DART).

companyfacts API 하나로 10-K(연간)·10-Q(분기) 재무 수치를 전부 받는다. API 키 불필요.
반환하는 periods 구조는 dart.py와 같아서 분석 엔진(analysis.py)을 그대로 쓴다.
금액 단위: 달러(USD).
"""
import datetime as dt
import os
import time

import pandas as pd
import requests

UA_DEFAULT = "FS-Analyzer/1.0 personal-research"


class SecError(Exception):
    pass


def _ua(contact=None):
    return {"User-Agent": f"FS-Analyzer/1.0 {contact}" if contact else UA_DEFAULT,
            "Accept-Encoding": "gzip, deflate"}


def _get_json(url, contact=None):
    r = requests.get(url, headers=_ua(contact), timeout=30)
    if r.status_code == 403:
        raise SecError("SEC가 접속을 거부했습니다. 왼쪽 설정에서 연락용 이메일을 넣으면 해결되는 경우가 많아요.")
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------- 종목 목록
def load_tickers(cache_path, contact=None):
    if os.path.exists(cache_path) and time.time() - os.path.getmtime(cache_path) < 7 * 86400:
        return pd.read_csv(cache_path, dtype=str)
    d = _get_json("https://www.sec.gov/files/company_tickers.json", contact)
    df = pd.DataFrame([{"cik": str(v["cik_str"]).zfill(10), "ticker": v["ticker"], "title": v["title"]}
                       for v in d.values()])
    df.to_csv(cache_path, index=False)
    return df


def company_facts(cik, contact=None):
    return _get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", contact)


# ---------------------------------------------------------------- 태그 매핑
# 같은 항목도 회사·연도마다 태그가 달라서 후보를 순서대로 둔다
TAGS = {
    "assets": ["Assets"],
    "cur_assets": ["AssetsCurrent"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "inventory": ["InventoryNet", "InventoryGross"],
    "ppe": ["PropertyPlantAndEquipmentNet",
            "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization"],
    "intangible_ex": ["IntangibleAssetsNetExcludingGoodwill", "FiniteLivedIntangibleAssetsNet"],
    "goodwill": ["Goodwill"],
    "liab": ["Liabilities"],
    "cur_liab": ["LiabilitiesCurrent"],
    "liab_and_eq": ["LiabilitiesAndStockholdersEquity"],
    "equity_total": ["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "equity_parent": ["StockholdersEquity"],
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "cogs": ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold"],
    "gross": ["GrossProfit"],
    "opex": ["OperatingExpenses", "CostsAndExpenses"],
    "op": ["OperatingIncomeLoss"],
    "interest": ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"],
    "net_total": ["ProfitLoss"],
    "net_parent": ["NetIncomeLoss"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities"],
    "icf": ["NetCashProvidedByUsedInInvestingActivities"],
    "fcf_fin": ["NetCashProvidedByUsedInFinancingActivities"],
    "cash_cf": ["CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "CashAndCashEquivalentsAtCarryingValue"],
    "dps": ["CommonStockDividendsPerShareDeclared", "CommonStockDividendsPerShareCashPaid"],
}
INSTANT = {"assets", "cur_assets", "cash", "inventory", "ppe", "intangible_ex", "goodwill", "liab", "cur_liab",
           "liab_and_eq", "equity_total", "equity_parent", "cash_cf"}


def _d(s):
    return dt.date.fromisoformat(s)


def _index(facts):
    """{key: {(start|None, end): val}}  같은 기간이 여러 번 공시되면 가장 최근 제출값(정정 반영)을 쓴다."""
    gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    out = {}
    for key, tags in TAGS.items():
        merged = {}
        for tag in tags:
            node = gaap.get(tag)
            if not node:
                continue
            unit = "USD" if "USD" in node["units"] else next(iter(node["units"]))
            best = {}
            for f in node["units"][unit]:
                if not str(f.get("form", "")).startswith(("10-K", "10-Q")):
                    continue
                k = (f.get("start"), f["end"])
                if k not in best or f.get("filed", "") > best[k][1]:
                    best[k] = (f["val"], f.get("filed", ""))
            for k, (v, _) in best.items():
                merged.setdefault(k, v)  # 앞쪽 태그 우선
        out[key] = merged
    return out


def _dur_days(k):
    return (_d(k[1]) - _d(k[0])).days if k[0] else 0


def _instant(idx, key, end):
    return idx[key].get((None, end))


def _duration(idx, key, end, lo, hi):
    """end에 끝나는 기간 중 길이가 lo~hi일인 값."""
    for k, v in idx[key].items():
        if k[1] == end and k[0] and lo <= _dur_days(k) <= hi:
            return v, k[0]
    return None, None


def _label(end):
    e = _d(end) - dt.timedelta(days=7)  # 52/53주 회계연도(1/31, 2/1, 2/3 결산 등)를 같은 달로 맞춤
    return f"{e.year}.{e.month:02d}"


def _bs(idx, end):
    g = lambda k: _instant(idx, k, end)
    assets, cur_a = g("assets"), g("cur_assets")
    eq_total = g("equity_total") or g("equity_parent")
    liab = g("liab")
    if liab is None and g("liab_and_eq") is not None and eq_total is not None:
        liab = g("liab_and_eq") - eq_total
    cur_l = g("cur_liab")
    intang = None
    if g("intangible_ex") is not None or g("goodwill") is not None:
        intang = (g("intangible_ex") or 0) + (g("goodwill") or 0)
    return {
        "assets": assets, "cur_assets": cur_a, "cash": g("cash"), "inventory": g("inventory"),
        "noncur_assets": assets - cur_a if assets is not None and cur_a is not None else None,
        "ppe": g("ppe"), "intangible": intang, "liab": liab, "cur_liab": cur_l,
        "noncur_liab": liab - cur_l if liab is not None and cur_l is not None else None,
        "equity": eq_total, "equity_parent": g("equity_parent"),
    }


def _finish_is(d):
    if d.get("gross") is None and d.get("revenue") is not None and d.get("cogs") is not None:
        d["gross"] = d["revenue"] - d["cogs"]
    if d.get("cogs") is None and d.get("revenue") is not None and d.get("gross") is not None:
        d["cogs"] = d["revenue"] - d["gross"]
    # 미국 기업은 R&D가 판관비와 별도 → 매출총이익 - 영업이익 = 영업비용 전체(판관비+R&D 등)로 맞춤
    if d.get("gross") is not None and d.get("op") is not None:
        d["sga"] = d["gross"] - d["op"]
    else:
        d["sga"] = d.get("opex")
    d["net"] = d.get("net_total") if d.get("net_total") is not None else d.get("net_parent")
    return d


FLOW_IS = ["revenue", "cogs", "gross", "opex", "op", "interest", "net_total", "net_parent"]
FLOW_CF = ["ocf", "icf", "fcf_fin"]


def annual_periods(facts, n):
    idx = _index(facts)
    ends = sorted({k[1] for key in ("revenue", "net_parent", "ocf") for k in idx[key]
                   if k[0] and 350 <= _dur_days(k) <= 380}, reverse=True)[:n]
    periods = []
    for end in ends:
        is_ = {}
        start = None
        for key in FLOW_IS:
            v, s = _duration(idx, key, end, 350, 380)
            is_[key] = v
            start = start or s
        cf = {key: _duration(idx, key, end, 350, 380)[0] for key in FLOW_CF}
        prev_end = (_d(start) - dt.timedelta(days=1)).isoformat() if start else None
        cf["cash_begin"] = _nearest_instant(idx, "cash_cf", prev_end)
        cf["cash_end"] = _instant(idx, "cash_cf", end)
        periods.append({"label": _label(end), "end": end, "year": _d(end).year, "q": 4,
                        "BS": _bs(idx, end), "IS": _finish_is(is_), "CF": cf})
    return periods


def _nearest_instant(idx, key, date_str, tol=7):
    if not date_str:
        return None
    target = _d(date_str)
    best = None
    for (s, e), v in idx[key].items():
        if s is None and abs((_d(e) - target).days) <= tol:
            if best is None or abs((_d(e) - target).days) < best[0]:
                best = (abs((_d(e) - target).days), v)
    return best[1] if best else None


def _quarter_value(idx, key, end, prev_end):
    """3개월 값. 없으면 누적(YTD)끼리 빼서 계산 (4분기 = 연간 - 9개월 누적)."""
    v, _ = _duration(idx, key, end, 80, 100)
    if v is not None:
        return v
    ytd, start = None, None
    for k, val in idx[key].items():   # end에 끝나는 가장 긴 누적값
        if k[1] == end and k[0] and _dur_days(k) <= 380 and (ytd is None or _dur_days(k) > _dur_days((start, end))):
            ytd, start = val, k[0]
    if ytd is None or not prev_end:
        return None
    prev = idx[key].get((start, prev_end))
    return None if prev is None else ytd - prev


def quarterly_periods(facts, n):
    idx = _index(facts)
    ends = sorted({k[1] for k in idx["assets"] if k[0] is None}, reverse=True)
    periods = []
    for i, end in enumerate(ends[:n]):
        prev_end = ends[i + 1] if i + 1 < len(ends) else None
        is_ = {key: _quarter_value(idx, key, end, prev_end) for key in FLOW_IS}
        cf = {key: _quarter_value(idx, key, end, prev_end) for key in FLOW_CF}
        cf["cash_begin"] = _instant(idx, "cash_cf", prev_end) if prev_end else None
        cf["cash_end"] = _instant(idx, "cash_cf", end)
        periods.append({"label": _label(end), "end": end, "year": _d(end).year, "q": None,
                        "BS": _bs(idx, end), "IS": _finish_is(is_), "CF": cf})
    return periods


def shares_outstanding(facts):
    """현재 보통주 수. 1) 표지의 최신 주식수 → 2) 재무상태표 발행주식수 → 3) 가중평균 주식수.
    보통주가 여러 종류(Class A/B 등)인 회사는 1)이 비어 있는 경우가 많아 2)·3)으로 보완."""
    node = ((facts.get("facts") or {}).get("dei") or {}).get("EntityCommonStockSharesOutstanding")
    vals = (node or {}).get("units", {}).get("shares") or []
    if vals:
        last = max(vals, key=lambda v: (v["end"], v.get("filed", "")))
        same = {v["val"] for v in vals if v["end"] == last["end"] and v.get("accn") == last.get("accn")}
        if sum(same) > 0:
            return float(sum(same))
    gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    for tag, instant in (("CommonStockSharesOutstanding", True), ("WeightedAverageNumberOfSharesOutstandingBasic", False)):
        node = gaap.get(tag)
        if not node:
            continue
        vs = [v for v in (node["units"].get("shares") or []) if bool(v.get("start")) != instant and v.get("val")]
        if vs:
            return float(max(vs, key=lambda v: (v["end"], v.get("filed", "")))["val"])
    return None


def dividends_ttm(facts):
    """최근 1년 주당 배당금 합계 (분기 값 4개 합, 없으면 최근 연간 값)."""
    idx = _index(facts)
    q = sorted([(k[1], v) for k, v in idx["dps"].items() if k[0] and 80 <= _dur_days(k) <= 100], reverse=True)
    if len(q) >= 4:
        return sum(v for _, v in q[:4])
    a = sorted([(k[1], v) for k, v in idx["dps"].items() if k[0] and 350 <= _dur_days(k) <= 380], reverse=True)
    return a[0][1] if a else None


# ---------------------------------------------------------------- 공시 목록
FORMS = {"10-K": "연간보고서", "10-Q": "분기보고서", "8-K": "수시공시", "20-F": "연간보고서(외국)",
         "6-K": "수시공시(외국)", "DEF 14A": "주총안내"}


def disclosures(cik, contact=None, limit=40):
    d = _get_json(f"https://data.sec.gov/submissions/CIK{cik}.json", contact) or {}
    r = (d.get("filings") or {}).get("recent") or {}
    out = []
    cutoff = (dt.date.today() - dt.timedelta(days=365)).isoformat()
    for form, date, acc, doc, desc in zip(r.get("form", []), r.get("filingDate", []), r.get("accessionNumber", []),
                                          r.get("primaryDocument", []), r.get("primaryDocDescription", [])):
        if form not in FORMS or date < cutoff:
            continue
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{doc}"
        out.append({"date": date.replace("-", ""), "type": FORMS[form], "name": f"{form} {desc or ''}".strip(), "url": url})
        if len(out) >= limit:
            break
    return out
