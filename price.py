"""주가 가져오기: Yahoo Finance(15~20분 지연) → 실패하면 공공데이터포털(전일 종가).

반환: {"series": pd.Series(날짜→종가) | None, "source": str, "mcap": float|None, "errors": [str]}
"""
import datetime as dt
import os
import shutil
from urllib.parse import unquote

import pandas as pd
import requests

DATA_GO_URL = "https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo"


def _ascii_ca_bundle():
    """curl(Yahoo용)은 Windows에서 한글 경로의 인증서 파일을 못 읽는다 → 영문 경로로 복사해서 사용."""
    try:
        import certifi
        src = certifi.where()
    except ImportError:
        return None
    if src.isascii():
        return src
    drive = os.environ.get("SystemDrive", "C:")
    for d in (os.environ.get("PUBLIC"), os.path.join(drive + os.sep, "Users", "Public"),
              os.environ.get("ProgramData"), drive + os.sep):
        if not d or not d.isascii():
            continue
        dst = os.path.join(d, "fs_analyzer_cacert.pem")
        try:
            if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(src):
                shutil.copyfile(src, dst)
            return dst
        except OSError:
            continue
    return None


def _yahoo_session():
    ca = _ascii_ca_bundle()
    if ca:
        os.environ["CURL_CA_BUNDLE"] = ca
    try:
        from curl_cffi import requests as creq
        return creq.Session(impersonate="chrome", verify=ca or True)
    except Exception:
        return None


def from_yahoo(stock_code, corp_cls):
    try:
        import yfinance as yf
    except ImportError:
        return None, None, "yfinance가 설치되어 있지 않습니다"
    order = [".KQ", ".KS"] if corp_cls == "K" else [".KS", ".KQ"]
    last_err = "데이터 없음"
    session = _yahoo_session()
    for suf in order:
        try:
            t = yf.Ticker(stock_code + suf, session=session) if session else yf.Ticker(stock_code + suf)
            h = t.history(period="3y", interval="1d")  # 일봉: 마지막 값이 최신 종가(장중엔 현재가)
        except Exception as e:  # 차단, 네트워크, 요청 한도 등
            last_err = f"{type(e).__name__}: {str(e)[:150]}"
            continue
        if h is not None and len(h):
            s = h["Close"].dropna()
            s.index = pd.to_datetime(s.index).tz_localize(None)
            return stock_code + suf, s, None
    return None, None, last_err


def from_data_go(stock_code, service_key):
    if not service_key:
        return None, None, "공공데이터포털 키 없음"
    key = unquote(service_key.strip())  # 인코딩 키를 넣어도 동작하도록
    begin = (dt.date.today() - dt.timedelta(days=365 * 3)).strftime("%Y%m%d")
    try:
        r = requests.get(DATA_GO_URL, timeout=20, params={
            "serviceKey": key, "resultType": "json", "numOfRows": 1000, "pageNo": 1,
            "likeSrtnCd": stock_code, "beginBasDt": begin,
        })
    except requests.RequestException as e:
        return None, None, f"접속 실패: {type(e).__name__}"
    try:
        data = r.json()
    except ValueError:
        txt = r.text
        for code in ("SERVICE_KEY_IS_NOT_REGISTERED_ERROR", "SERVICE_ACCESS_DENIED_ERROR",
                     "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR"):
            if code in txt:
                msg = {"SERVICE_KEY_IS_NOT_REGISTERED_ERROR": "키가 아직 등록되지 않았습니다 (발급 후 1~2시간 걸릴 수 있음)",
                       "SERVICE_ACCESS_DENIED_ERROR": "이 API에 대한 활용신청이 안 되어 있습니다",
                       "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR": "오늘 요청 한도를 넘었습니다"}[code]
                return None, None, msg
        return None, None, f"알 수 없는 응답 (HTTP {r.status_code})"
    items = (((data.get("response") or {}).get("body") or {}).get("items") or {}).get("item") or []
    if isinstance(items, dict):
        items = [items]
    items = [i for i in items if str(i.get("srtnCd", "")).lstrip("A") == stock_code]
    if not items:
        return None, None, "해당 종목 데이터 없음"
    df = pd.DataFrame(items)
    df["basDt"] = pd.to_datetime(df["basDt"], format="%Y%m%d")
    df = df.sort_values("basDt")
    s = pd.Series(df["clpr"].astype(float).values, index=df["basDt"])
    mcap = float(df["mrktTotAmt"].iloc[-1]) if "mrktTotAmt" in df and df["mrktTotAmt"].iloc[-1] else None
    return mcap, s, None


def from_yahoo_symbol(symbol):
    """미국 등 해외 티커 (예: MRVL, BRK-B)."""
    try:
        import yfinance as yf
    except ImportError:
        return None, "yfinance가 설치되어 있지 않습니다"
    session = _yahoo_session()
    try:
        t = yf.Ticker(symbol, session=session) if session else yf.Ticker(symbol)
        h = t.history(period="3y", interval="1d")
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:150]}"
    if h is None or not len(h):
        return None, "데이터 없음"
    s = h["Close"].dropna()
    s.index = pd.to_datetime(s.index).tz_localize(None)
    return s, None


def get_price_us(ticker):
    sym = ticker.replace(".", "-")
    s, err = from_yahoo_symbol(sym)
    if s is not None:
        return {"series": s, "source": f"Yahoo Finance · {sym} · 15분 지연", "mcap": None, "errors": []}
    return {"series": None, "source": "", "mcap": None, "errors": [f"Yahoo: {err}"]}


def usd_krw():
    """원/달러 환율 (최근 종가). 실패하면 None."""
    s, _ = from_yahoo_symbol("KRW=X")
    return float(s.iloc[-1]) if s is not None and len(s) else None


def get_price(stock_code, corp_cls, data_key=None):
    errors = []
    ticker, s, err = from_yahoo(stock_code, corp_cls)
    if s is not None:
        return {"series": s, "source": f"Yahoo Finance · {ticker} · 15~20분 지연", "mcap": None, "errors": errors}
    errors.append(f"Yahoo: {err}")
    mcap, s, err = from_data_go(stock_code, data_key)
    if s is not None:
        return {"series": s, "source": f"공공데이터포털 · {s.index[-1]:%Y-%m-%d} 종가", "mcap": mcap, "errors": errors}
    errors.append(f"공공데이터포털: {err}")
    return {"series": None, "source": "", "mcap": None, "errors": errors}
