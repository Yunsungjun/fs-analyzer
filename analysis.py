"""비율 계산 + 자동 진단.

판단 기준값은 TH에 모여 있다. 바꾸고 싶으면 TH만 고치면 된다.
"""
import statistics

TH = {
    "debt_low": 80,        # 부채비율 80% 미만 = 보수적
    "debt_high": 120,      # 120% 초과 = 이자 부담 커짐
    "debt_danger": 200,
    "cur_high": 1.6,       # 유동비율 1~1.6 정상, 그 이상은 현금을 쌓아두는 편
    "cur_excess": 3.0,
    "inv_share": 10.0,     # 재고/자산 10% 미만이면 '재고 쌓아두고 파는 회사'로 보지 않음
    "inv_cv": 0.15,        # 상세자산비율 변동계수 15% 이내 = 잘 유지
    "rich_op_margin": 20.0,  # 영업이익률 20%+ & 부채 적음 = 현금이 많아 투자처를 못 찾은 유형
    "one_off_gap": 10.0,   # 순이익률이 영업이익률보다 10%p 이상 높으면 일회성 의심
    "roe_good": 10.0,
    "lev_high": 2.5,       # 자산/자본 2.5배 이상 = 빚으로 ROE를 올린 편
    "per_hope": 40.0,      # PER 40 이상 = 미래 기대가 주가를 끌고 있는 '기대형'
}


def _div(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b


def _pct(a, b):
    v = _div(a, b)
    return None if v is None else v * 100


def ratios(p, annualize=1):
    bs, is_ = p["BS"], p["IS"]
    net = is_.get("net")
    rev = is_.get("revenue")
    net_a = None if net is None else net * annualize
    rev_a = None if rev is None else rev * annualize
    return {
        "debt_ratio": _pct(bs.get("liab"), bs.get("equity")),
        "current_ratio": _div(bs.get("cur_assets"), bs.get("cur_liab")),
        "simple_asset_ratio": _div(bs.get("noncur_assets"), bs.get("cur_assets")),
        "detail_asset_ratio": _div(bs.get("ppe"), bs.get("inventory")),
        "gross_margin": _pct(is_.get("gross"), rev),
        "op_margin": _pct(is_.get("op"), rev),
        "net_margin": _pct(net, rev),
        "roe": _pct(net_a, bs.get("equity")),
        "roa": _pct(net_a, bs.get("assets")),
        "asset_turnover": _div(rev_a, bs.get("assets")),
        "leverage": _div(bs.get("assets"), bs.get("equity")),
        "inv_share": _pct(bs.get("inventory"), bs.get("assets")),
    }


# ---------------------------------------------------------------- 진단
def _f(v, d=1):
    return "N/A" if v is None else f"{v:,.{d}f}"


def _series(periods, rs, key, src=None):
    """오래된 것 → 최신 순서로 (label, value) 목록. None은 제외."""
    out = []
    for p, r in zip(reversed(periods), reversed(rs)):
        v = r.get(key) if src is None else p[src].get(key)
        if v is not None:
            out.append((p["label"], v))
    return out


def _trend(vals):
    return "" if len(vals) < 2 else "추이 " + " → ".join(vals) + ". "


def _item(section, level, title, body):
    return {"section": section, "level": level, "title": title, "body": body}


def diagnose(periods, kpi=None, inv_mode="auto", is_quarterly=False):
    """periods: 최신이 앞. 반환: (진단 목록, 한눈에 요약 dict)."""
    kpi = kpi or {}
    ann = 4 if is_quarterly else 1
    rs = [ratios(p, ann) for p in periods]
    out, summary = [], {}
    if not periods:
        return out, summary
    latest, r0 = periods[0], rs[0]

    debt = _series(periods, rs, "debt_ratio")
    cur = _series(periods, rs, "current_ratio")
    opm = _series(periods, rs, "op_margin")
    netm = _series(periods, rs, "net_margin")
    rev = _series(periods, rs, "revenue", "IS")
    ocf = _series(periods, rs, "ocf", "CF")
    icf = _series(periods, rs, "icf", "CF")
    latest_opm = opm[-1][1] if opm else None

    # 1) 부채비율 — 장기 안정성
    if debt:
        d = debt[-1][1]
        vals = _trend([f"{v:.0f}%" for _, v in debt])
        if d < TH["debt_low"]:
            lvl, short = "neutral", "보수적"
            cr = cur[-1][1] if cur else None
            if latest_opm is not None and latest_opm < 0:
                why = ("그런데 영업이익이 적자입니다. 본업으로 버는 돈이 없는데 빚이 적다면 "
                       "투자금을 받았거나 자산을 팔아서 버티는 구조일 수 있어요. 주석 확인이 필요합니다.")
                lvl = "warn"
            elif latest_opm is not None and latest_opm >= TH["rich_op_margin"] and cr and cr > TH["cur_high"]:
                why = (f"영업이익률이 {latest_opm:.0f}%로 높고 현금도 많습니다. 벌어들인 현금이 많아 "
                       "아직 투자처를 찾지 못한 경우일 수 있어요.")
            elif debt[0][1] > TH["debt_high"] and d < debt[0][1] / 2:
                why = (f"기간 초 {debt[0][1]:.0f}%에서 크게 낮아졌습니다. 이익이 쌓였거나 유상증자, 전환사채 전환 등으로 "
                       "자본이 늘었을 수 있어요. 사업보고서의 자본 변동 내역을 확인해보세요.")
            else:
                why = "빚을 적게 쓰는 경영 스타일입니다. 회사 문화 자체가 보수적인 경우가 많아요."
            out.append(_item("bs", lvl, f"부채비율 {d:.0f}%: 장기적으로 매우 안전하지만 보수적", f"{vals}{why}"))
        elif d <= TH["debt_high"]:
            short, lvl = "적정", "good"
            out.append(_item("bs", lvl, f"부채비율 {d:.0f}%: 적정 수준",
                             f"{vals}80~120% 안에 있어서 빚이 너무 많지도 적지도 않습니다. "
                             "다만 금리가 오르는 시기에는 적정선이 내려간다는 점은 기억해두세요."))
        elif d <= TH["debt_danger"]:
            short, lvl = "높음", "warn"
            out.append(_item("bs", lvl, f"부채비율 {d:.0f}%: 이자 부담 주의",
                             f"{vals}120%를 넘었습니다. 매달 이자를 감당할 수 있는지가 관건이라 "
                             "영업이익 대비 이자비용을 같이 보세요."))
        else:
            short, lvl = "위험", "bad"
            out.append(_item("bs", lvl, f"부채비율 {d:.0f}%: 부채 과다",
                             f"{vals}200%를 넘었습니다. 금리가 조금만 올라도 장기적으로 버티기 어려울 수 있어요."))
        if len(debt) >= 4 and all(debt[i][1] < debt[i + 1][1] for i in range(len(debt) - 4, len(debt) - 1)):
            out.append(_item("bs", "warn", "부채비율이 3기 연속 상승 중",
                             "빚을 늘려 투자하는 중인지, 실적이 나빠서 빌리는 중인지 현금흐름표로 확인해보세요."))
        summary["장기 안정성"] = (lvl, f"부채비율 {d:.0f}% · {short}")

    # 2) 유동비율 — 단기 안정성
    if cur:
        c = cur[-1][1]
        vals = _trend([f"{v:.1f}" for _, v in cur])
        below = [v < 1 for _, v in cur]
        if c < 1 and len(cur) >= 3 and all(below):
            lvl, short = "good", "1 미만 유지 · 갑 구조"
            out.append(_item("bs", lvl, f"유동비율 {c:.1f}: 계속 1 미만이면 위험이 아니라 '갑'",
                             f"{vals}기간 내내 1 미만인데 망하지 않았다는 건 의도된 구조입니다. "
                             "받을 돈(매출채권)은 빨리 받고 줄 돈(매입채무)은 늦게 주는, 거래처 대비 갑인 회사일 가능성이 높아요 "
                             "."))
        elif c < 1:
            lvl, short = "bad", "1 미만으로 하락"
            out.append(_item("bs", lvl, f"유동비율 {c:.1f}: 갑자기 1 아래로 떨어짐",
                             f"{vals}원래 1 이상이던 회사가 떨어졌다면 1년 안에 갚을 돈이 가진 돈보다 많아진 것이라 "
                             "단기적으로 위험 신호입니다."))
        elif c <= TH["cur_high"]:
            lvl, short = "good", "정상"
            out.append(_item("bs", lvl, f"유동비율 {c:.1f}: 정상",
                             f"{vals}1년 안에 갚을 돈보다 들어올 돈이 조금 더 많은, 효율적인 상태입니다."))
        elif c <= TH["cur_excess"]:
            lvl, short = "neutral", "현금 넉넉 · 보수적"
            out.append(_item("bs", lvl, f"유동비율 {c:.1f}: 단기적으로 매우 안전 (보수적)",
                             f"{vals}빚이 1억인데 예금을 {c:.1f}억 들고 있는 셈입니다. 망할 걱정은 없지만 "
                             "돈을 효율적으로 굴리는 편은 아니에요."))
        else:
            lvl, short = "warn", "현금 과다"
            extra = ("영업이익도 적자라서 투자금을 받아 쌓아둔 돈일 가능성이 있습니다."
                     if latest_opm is not None and latest_opm < 0 else
                     "투자를 받았거나 쓸 곳을 못 찾은 현금일 수 있어요.")
            out.append(_item("bs", lvl, f"유동비율 {c:.1f}: 현금이 비정상적으로 많음", f"{vals}{extra}"))
        summary["단기 안정성"] = (lvl, f"유동비율 {c:.1f} · {short}")

    # 3) 상세자산비율 — 재고 관리
    inv_share = r0.get("inv_share")
    stock_co = {"yes": True, "no": False}.get(inv_mode)
    if stock_co is None and inv_share is not None:
        stock_co = inv_share >= TH["inv_share"]
    det = _series(periods, rs, "detail_asset_ratio")
    if stock_co is False:
        out.append(_item("bs", "neutral", "재고 비율은 볼 필요 없는 회사",
                         f"재고가 자산의 {_f(inv_share)}%뿐입니다. 주문 생산이나 서비스형 회사라면 "
                         "재고 비율은 애초에 보면 안 되는 지표예요. (판단이 틀렸다면 왼쪽 설정에서 바꿀 수 있습니다.)"))
        summary["재고 관리"] = ("neutral", "해당 없음 (재고형 아님)")
    elif stock_co and len(det) >= 2:
        vals = [v for _, v in det]
        mean = statistics.mean(vals)
        cv = statistics.pstdev(vals) / mean if mean else None
        series_txt = " → ".join(f"{v:.1f}" for v in vals)
        if cv is not None and cv <= TH["inv_cv"]:
            out.append(_item("bs", "good", "상세자산비율이 일정하게 유지됨",
                             f"{series_txt}. 공장 규모 대비 재고를 일정하게 관리하고 있어서 "
                             "주문에 제때 대응할 수 있는 회사로 볼 수 있습니다."))
            summary["재고 관리"] = ("good", "적정 재고 유지")
        else:
            out.append(_item("bs", "warn", "상세자산비율이 크게 흔들림",
                             f"{series_txt}. 배송이 1시간일 때도 한 달일 때도 있는 쇼핑몰처럼, "
                             "재고 관리가 들쭉날쭉하면 고객 주문 대응이 불안정해질 수 있어요."))
            summary["재고 관리"] = ("warn", "재고 변동 큼")

    # 4) 매출 성장
    if len(rev) >= 2:
        first, last = rev[0][1], rev[-1][1]
        span = len(rev) - 1
        cagr = ((last / first) ** (1 / (span / ann)) - 1) * 100 if first > 0 and last > 0 else None
        yoy_base = rev[-1 - ann][1] if len(rev) > ann else None
        yoy = _pct(last - yoy_base, abs(yoy_base)) if yoy_base else None
        unit = "분기" if is_quarterly else "년"
        if cagr is None:
            lvl, txt = "neutral", "성장률 계산 불가"
        elif cagr >= 10:
            lvl, txt = "good", f"연평균 {cagr:.1f}% 고성장"
        elif cagr >= 0:
            lvl, txt = "neutral", f"연평균 {cagr:.1f}% 완만"
        else:
            lvl, txt = "warn", f"연평균 {cagr:.1f}% 역성장"
        body = f"{rev[0][0]} → {rev[-1][0]} ({span}{unit}) 기준입니다."
        if yoy is not None:
            body += f" 최근 1년 전 대비 {yoy:+.1f}%."
        out.append(_item("is", lvl, f"매출: {txt}", body))
        summary["성장성"] = (lvl, txt)

    # 5) 마진율·영업이익률
    if len(opm) >= 2 and len(rev) >= 2:
        o0, o1 = opm[0][1], opm[-1][1]
        rev_up = rev[-1][1] > rev[0][1]
        vals = " → ".join(f"{v:.1f}%" for _, v in opm)
        if o1 < 0 and rev_up:
            lvl, short = "bad", "매출↑ 그런데 적자"
            out.append(_item("is", lvl, "매출은 크는데 영업이익은 적자",
                             f"영업이익률 {vals}. 실력보다 가격 경쟁으로 덩치를 키우는 중일 수 있습니다. "
                             "영원히 싸게 팔 수는 없으니, 언제부터 돈을 벌 수 있는 구조인지가 핵심이에요."))
        elif rev_up and o1 > o0:
            lvl, short = "good", "매출↑ 이익률↑"
            out.append(_item("is", lvl, "매출과 영업이익률이 같이 오름",
                             f"영업이익률 {vals}. 보통 덩치가 커지면 관리비가 더 빨리 늘어서 이익률이 떨어지는데, "
                             "둘 다 오르는 건 아주 드물고 좋은 신호입니다."))
        elif o1 < o0:
            lvl, short = "warn", "효율성 하락"
            out.append(_item("is", lvl, "영업이익률이 기간 초보다 낮아짐",
                             f"영업이익률 {vals}. 많이 팔아도 남는 비율이 줄었습니다. 최근 분기에서 회복 중인지 "
                             "'분기' 보기로 확인해보세요."))
        else:
            lvl, short = "neutral", "유지"
            out.append(_item("is", lvl, "영업이익률 유지", f"영업이익률 {vals}."))
        summary["수익성"] = (lvl, f"영업이익률 {o1:.1f}% · {short}")

    # 6) 일회성 이익 의심
    nm = dict(netm)
    flags = [lbl for lbl, o in opm if lbl in nm and ((o < 0 < nm[lbl]) or nm[lbl] - o >= TH["one_off_gap"])]
    if flags:
        out.append(_item("is", "warn", f"일회성 이익 의심: {', '.join(flags)}",
                         "영업이익률보다 순이익률이 훨씬 높습니다. 본업이 아니라 자산 매각 같은 특별한 이익이 "
                         "들어간 해일 가능성이 커요. 이 해의 ROE는 실력으로 보면 안 되고, 사업보고서 주석을 확인해야 합니다."))

    # 7) ROE 분해
    roe, lev = r0.get("roe"), r0.get("leverage")
    if roe is not None:
        nm0, at0 = r0.get("net_margin"), r0.get("asset_turnover")
        decomp = f"순이익률 {_f(nm0)}% × 자산회전율 {_f(at0, 2)}회 × 레버리지 {_f(lev, 2)}배"
        if roe >= TH["roe_good"] and lev is not None and lev >= TH["lev_high"]:
            out.append(_item("is", "warn", f"ROE {roe:.1f}%: 빚의 힘이 큼",
                             f"{decomp}. ROE는 높지만 레버리지가 {lev:.1f}배라 빚으로 끌어올린 부분이 큽니다. "
                             "금리나 실적이 꺾이면 ROE도 크게 흔들려요."))
        elif roe >= TH["roe_good"]:
            out.append(_item("is", "good", f"ROE {roe:.1f}%: 실력으로 번 수익",
                             f"{decomp}. 빚에 크게 기대지 않고 높은 ROE를 내고 있습니다."))
        elif roe < 0:
            out.append(_item("is", "bad", f"ROE {roe:.1f}%: 주주 돈이 줄어듦", f"{decomp}."))
        else:
            out.append(_item("is", "neutral", f"ROE {roe:.1f}%", f"{decomp}."))

    # 8) 현금흐름
    if ocf and icf:
        ocf_d, icf_d = dict(ocf), dict(icf)
        both = [l for l, _ in ocf if l in icf_d]
        bleed = [l for l in both if ocf_d[l] < 0 and icf_d[l] > 0]
        plus_icf = [l for l in both if icf_d[l] > 0]
        neg_ocf = [l for l, v in ocf if v < 0]
        lo, li = ocf[-1][1], icf[-1][1]
        if bleed:
            lvl, short = "bad", "자산 팔아 버팀"
            out.append(_item("cf", lvl, f"본업으로 못 벌고 자산을 팔아 버팀: {', '.join(bleed)}",
                             "영업활동 현금흐름은 마이너스인데 투자활동 현금흐름이 플러스입니다. "
                             "본업에서 현금이 빠져나가고 있고, 자산 처분 등으로 이를 메우고 있다는 뜻이에요."))
        elif plus_icf:
            lvl, short = "warn", "투자CF 플러스"
            out.append(_item("cf", lvl, f"투자활동 현금흐름이 플러스: {', '.join(plus_icf)}",
                             "투자를 하면 마이너스가 정상입니다. 플러스면 공장·특허·지분 등을 팔았다는 뜻이라 "
                             "사업보고서 주석으로 이유를 확인해야 합니다."))
        elif lo > 0 and li < 0:
            lvl, short = "good", "번 돈을 재투자"
            ratio = abs(li) / lo * 100
            out.append(_item("cf", lvl, "본업으로 번 돈을 다시 투자하는 중",
                             f"최근 영업CF로 번 돈의 {ratio:.0f}%를 투자에 쓰고 있습니다. "
                             "공장 투자뿐 아니라 R&D도 여기 포함돼요. 건강한 패턴입니다."))
        else:
            lvl, short = "neutral", "확인 필요"
        if neg_ocf and not bleed:
            out.append(_item("cf", "warn", f"영업활동 현금흐름 마이너스: {', '.join(neg_ocf)}",
                             "장부상 이익과 별개로, 이 기간에는 본업에서 실제 현금이 빠져나갔습니다."))
        if len(icf) >= 3:
            recent = abs(min(icf[-1][1], 0))
            past = [abs(min(v, 0)) for _, v in icf[:-1]]
            if past and statistics.mean(past) > 0 and recent > 1.5 * statistics.mean(past):
                out.append(_item("cf", "good", "투자 규모가 과거보다 크게 늘어남",
                                 "앞으로 수요가 늘 거라고 보고 베팅을 하는 중입니다. 투자가 곧 주가 상승은 아니지만, "
                                 "회사가 기회를 놓치고 있지는 않다는 뜻이에요."))
        summary["현금흐름"] = (lvl, short)

    # 9) 주가 성격
    per = kpi.get("per")
    if per is not None:
        if per <= 0:
            out.append(_item("overall", "neutral", "PER 계산 불가 (적자)",
                             "적자 기업은 PER로 판단할 수 없습니다. 재무보다 미래 기대나 턴어라운드 가능성이 주가를 움직여요."))
        elif per >= TH["per_hope"]:
            out.append(_item("overall", "neutral", f"PER {per:.0f}배: 기대형 주식",
                             "현재 이익에 비해 주가가 훨씬 높습니다. 재무제표보다 AI·반도체·로봇 같은 큰 파도를 "
                             "얼마나 탈 수 있는지가 주가를 움직이는 종목이에요. 재무는 '망하지 않는지' 정도로만 보고, "
                             "시장·기술 분석을 같이 해야 합니다."))
        else:
            out.append(_item("overall", "neutral", f"PER {per:.0f}배: 재무형 주식",
                             "이익 대비 주가가 과하지 않은 편입니다. 이런 종목은 주가가 재무 실적을 따라 움직이는 경향이 있어서 "
                             "재무 비율을 함께 보는 의미가 큽니다."))
    return out, summary
