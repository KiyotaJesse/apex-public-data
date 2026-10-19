# -*- coding: utf-8 -*-
"""agent_office_server.py -- APEX agent 辦公室 v3(霓虹像素版)本機伺服器

在哪跑: Jesse 家裡電腦, 放在 C:\\claude-assistant\\agent_office\\ 底下, 雙擊 start_agent_office.bat。
做什麼: 開 http://127.0.0.1:8777/ 給瀏覽器看; 頁面每 2 秒打 /api/state 拿資料。
只讀不寫: 除了自己的 agent_office.log, 不改任何檔、不下單(BingX 只走 apex_common.bingx_get 的唯讀白名單)。
只綁 127.0.0.1: 餘額只在本機看得到, 不推到任何公開來源(CLAUDE.md 安全規範: 帳戶金額不外洩)。

資料源(全部真實; 拿不到的欄位回 null, 頁面顯示 N/A, 不補假值):
  office     research/i1017/office/state.json      形態辦公室 7 桌(shape_office 蟻每日 07:40 寫)
  office_rs  shape_office_result.json / shape_office.lock   跑班結果 / 有鎖 = 正在跑班
  tama       GitHub KiyotaJesse/tama-trading 的 tama_memory.json(.env 的 GITHUB_TOKEN;
             分支 = apex_common.d_mem_ref())                                   每 60 秒(ETag, 沒變不算額度)
  balance    BingX /openApi/swap/v2/user/balance(唯讀)                         每 15 秒
  positions  BingX /openApi/swap/v2/user/positions(唯讀)                       每 15 秒
  kline      BingX 公開 /openApi/swap/v3/quote/klines 1h; 失敗退本機 kline_store/1h/<SYM>.pkl   每 30 秒
  config     tama_config_live_snapshot.json(config_readback 蟻寫的 GitHub HEAD 鏡像)+ config_readback_result.json
  killwatch  kill_watch_result.json / undertaker_result.json / risk_budget_result.json

測試用環境變數(平常不用設):
  APEX_ROOT            主基地路徑(預設: 本檔上一層有 apex_common.py 就用它, 否則 C:\\claude-assistant)
  AGENT_OFFICE_PORT    埠號(預設 8777; 8765 是舊的 local_dashboard)
  TAMA_MEMORY_FILE     改讀本機 json 當 tama_memory(離線測試)
  AGENT_OFFICE_NO_NET  =1 時不打任何網路(BingX/GitHub 都標 N/A)
  AGENT_OFFICE_NO_BROWSER =1 時不自動開瀏覽器
"""
import json
import logging
import math
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from urllib.parse import parse_qs, urlparse

VERSION = "2026-10-19.1"
HERE = os.path.dirname(os.path.abspath(__file__))
HOST = "127.0.0.1"
PORT = int(os.environ.get("AGENT_OFFICE_PORT", "8777"))
TPE = timezone(timedelta(hours=8))
NO_NET = os.environ.get("AGENT_OFFICE_NO_NET", "").strip() in ("1", "true", "yes")

try:   # Windows 主控台 cp950 遇到 emoji/罕字會炸(CLAUDE.md 已知坑), 一律 utf-8 + replace
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _find_root():
    env = os.environ.get("APEX_ROOT", "").strip()
    if env:
        return env
    for c in (os.path.dirname(HERE), HERE, r"C:\claude-assistant"):
        if os.path.exists(os.path.join(c, "apex_common.py")):
            return c
    return r"C:\claude-assistant"


ROOT = _find_root()
LOG_PATH = os.path.join(HERE, "agent_office.log")
log = logging.getLogger("agent_office")
log.setLevel(logging.INFO)
try:
    _fh = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(_fh)
except Exception:
    pass
_sh = logging.StreamHandler(sys.stdout)
_sh.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
log.addHandler(_sh)

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
try:
    import apex_common as AC   # 重用: load_env / bingx_get(唯讀白名單) / d_mem_ref
    AC_ERR = None
except Exception as _e:        # 不在主基地跑也能起來, 只是 BingX 與分支判斷會標 N/A
    AC, AC_ERR = None, "%s: %s" % (type(_e).__name__, _e)


def _load_env():
    p = os.path.join(ROOT, ".env")
    if AC is not None:
        AC.load_env(p, into_environ=True)
        return
    try:
        with open(p, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except Exception:
        pass


_load_env()


# ───────────────────────── 小工具 ─────────────────────────

def P(*parts):
    return os.path.join(ROOT, *parts)


def fnum(v):
    """轉 float; None/布林/非數字/NaN/Inf -> None。"""
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except Exception:
        return None
    return x if math.isfinite(x) else None


def parse_ts(v):
    """ISO 字串/毫秒/秒 -> epoch 秒(UTC); 讀不懂 -> None。無時區的 ISO 視為 UTC。"""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        x = float(v)
        return x / 1000.0 if x > 1e11 else x
    s = str(v).strip()
    if re.fullmatch(r"\d{10,13}(\.\d+)?", s):
        return parse_ts(float(s))
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T", 1) if "T" not in s else s.replace("Z", "+00:00"))
    except Exception:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.timestamp()


def parse_local_ts(v):
    """shape_office 這類本機蟻寫的 'YYYY-MM-DD HH:MM'(台北時間, 無時區) -> epoch。"""
    if not v:
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19], fmt).replace(tzinfo=TPE).timestamp()
        except Exception:
            continue
    return parse_ts(s)


def human_err(e, what=""):
    """例外 -> 一句人話(頁面滑過 N/A 會看到)。不帶任何金鑰。"""
    pre = (what + ": ") if what else ""
    if isinstance(e, urllib.error.HTTPError):
        if e.code in (401, 403):
            return pre + "被拒絕(HTTP %d, Token/金鑰失效或沒權限)" % e.code
        if e.code == 404:
            return pre + "找不到(HTTP 404, 分支或路徑不對)"
        if e.code == 429:
            return pre + "被限速(HTTP 429), 稍後自動重試"
        return pre + "HTTP %d" % e.code
    if isinstance(e, urllib.error.URLError):
        return pre + "連不到網路(%s)" % str(getattr(e, "reason", e))[:60]
    if isinstance(e, (socket.timeout, TimeoutError)):
        return pre + "逾時"
    if isinstance(e, FileNotFoundError):
        return pre + "檔案不存在: %s" % (e.filename or "")
    if isinstance(e, json.JSONDecodeError):
        return pre + "不是合法 JSON(可能正在寫一半, 下一輪會重讀)"
    if isinstance(e, KeyError):
        return pre + "缺設定 %s" % e
    msg = str(e)
    for k in ("BINGX_API_KEY", "BINGX_API_SECRET", "GITHUB_TOKEN"):   # 保險: 錯誤訊息不可能帶值, 但名字可以
        v = os.environ.get(k)
        if v and v in msg:
            msg = msg.replace(v, "***")
    return pre + "%s: %s" % (type(e).__name__, msg[:140])


def sanitize(o):
    """JSON 不吃 NaN/Inf -> None。"""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {str(k): sanitize(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [sanitize(v) for v in o]
    return o


# ───────────────────────── 資料源 ─────────────────────────

class FileSource(object):
    """本機檔: mtime 變了才重讀; 壞檔保留上一份好的並標錯。"""

    def __init__(self, name, rel, desc):
        self.name, self.rel, self.desc = name, rel, desc
        self.path = P(*rel.split("/"))
        self.data, self.mtime, self.err, self.read_at = None, None, None, None
        self.lock = threading.Lock()

    def poll(self):
        try:
            m = os.path.getmtime(self.path)
        except OSError:
            with self.lock:
                self.err = "檔案不存在: %s" % self.rel
                self.data, self.mtime = None, None
            return
        if m == self.mtime and self.data is not None:
            return
        try:
            with open(self.path, encoding="utf-8-sig") as f:
                d = json.load(f)
            with self.lock:
                self.data, self.mtime, self.err, self.read_at = d, m, None, time.time()
        except Exception as e:
            with self.lock:
                self.err = human_err(e, self.rel)

    def snap(self):
        with self.lock:
            return self.data, self.mtime, self.err

    def meta(self, now):
        d, m, err = self.snap()
        return {"ok": d is not None and err is None, "age_s": (now - m) if m else None,
                "err": err, "src": self.desc}


class NetSource(object):
    """網路源: 自己一條執行緒, ttl 秒抓一次; 失敗指數退避(最多 300 秒), 保留上一份好的。"""

    def __init__(self, name, ttl, fn, desc):
        self.name, self.ttl, self.fn, self.desc = name, ttl, fn, desc
        self.data, self.ok_at, self.err, self.fails = None, None, None, 0
        self.src_detail = desc
        self.lock = threading.Lock()
        self.kick = threading.Event()
        self.stop = False

    def run_once(self):
        try:
            d, detail = self.fn(self)
            with self.lock:
                if d is not None:
                    self.data = d
                self.ok_at, self.err, self.fails = time.time(), None, 0
                self.src_detail = detail or self.desc
        except Exception as e:
            with self.lock:
                self.err = e.args[0] if isinstance(e, NeedConfig) else human_err(e)
                self.fails += 1
            if self.fails in (1, 5) or self.fails % 30 == 0:
                log.warning("[%s] %s (連續第 %d 次)", self.name, self.err, self.fails)

    def loop(self):
        while not self.stop:
            self.run_once()
            wait = self.ttl if not self.fails else min(300, self.ttl * (2 ** min(self.fails, 5)))
            self.kick.wait(wait)
            self.kick.clear()

    def snap(self):
        with self.lock:
            return self.data, self.ok_at, self.err

    def meta(self, now):
        d, ok_at, err = self.snap()
        return {"ok": err is None and d is not None, "age_s": (now - ok_at) if ok_at else None,
                "err": err, "src": self.src_detail}


class NeedConfig(Exception):
    """設定缺了(人話訊息在 args[0])。"""


def _http_json(url, headers=None, timeout=20, etag=None):
    h = {"User-Agent": "apex-agent-office/" + VERSION}
    h.update(headers or {})
    if etag:
        h["If-None-Match"] = etag
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8-sig")), r.headers.get("ETag")
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return None, etag
        raise


# --- tama_memory.json(GitHub) ---
_TAMA_ETAG = [None]


def fetch_tama(src):
    override = os.environ.get("TAMA_MEMORY_FILE", "").strip()
    if override:
        with open(override, encoding="utf-8-sig") as f:
            return json.load(f), "本機檔 %s(測試模式)" % os.path.basename(override)
    if NO_NET:
        raise NeedConfig("AGENT_OFFICE_NO_NET=1, 不讀 GitHub")
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if not tok:
        raise NeedConfig("主基地 .env 沒有 GITHUB_TOKEN, 讀不到 tama_memory.json")
    ref = AC.d_mem_ref() if AC is not None else (os.environ.get("D_MEM_REF") or "main")
    url = ("https://api.github.com/repos/KiyotaJesse/tama-trading/contents/tama_memory.json?ref=%s" % ref)
    # raw 媒體型別: >1MB 也直接回檔案本體(contents API 預設 >1MB 不給 content, CLAUDE.md 已知坑)
    d, et = _http_json(url, {"Authorization": "token " + tok, "Accept": "application/vnd.github.raw"},
                       timeout=40, etag=_TAMA_ETAG[0])
    _TAMA_ETAG[0] = et
    return d, "GitHub tama-trading@%s/tama_memory.json" % ref


# --- BingX(唯讀) ---
def _bx(path, params=None):
    if NO_NET:
        raise NeedConfig("AGENT_OFFICE_NO_NET=1, 不打 BingX")
    if AC is None:
        raise NeedConfig("找不到 apex_common.py(要放在 C:\\claude-assistant\\agent_office 底下跑): %s" % AC_ERR)
    if not os.environ.get("BINGX_API_KEY") or not os.environ.get("BINGX_API_SECRET"):
        raise NeedConfig("主基地 .env 沒有 BINGX_API_KEY / BINGX_API_SECRET")
    return AC.bingx_get(path, params=params, ua="apex-agent-office", timeout=15)


def parse_balance(resp):
    b = (resp or {}).get("data") or {}
    if isinstance(b, dict) and "balance" in b:
        b = b["balance"]                       # BingX 巢狀回應坑(CLAUDE.md)
    if isinstance(b, list):                    # 少數版本回 list
        b = next((x for x in b if isinstance(x, dict) and str(x.get("asset", "")).upper() == "USDT"),
                 b[0] if b else {})
    if not isinstance(b, dict):
        return None
    out = {"equity": fnum(b.get("equity")), "balance": fnum(b.get("balance")),
           "available": fnum(b.get("availableMargin")), "upnl": fnum(b.get("unrealizedProfit")),
           "used_margin": fnum(b.get("usedMargin")), "asset": b.get("asset") or "USDT"}
    if out["equity"] is None and out["balance"] is None:
        return None
    return out


def fetch_balance(src):
    d = parse_balance(_bx("/openApi/swap/v2/user/balance"))
    if d is None:
        raise RuntimeError("BingX 回應沒有 equity/balance 欄位")
    return d, "BingX /user/balance(唯讀)"


def parse_positions(resp):
    out = []
    for p in ((resp or {}).get("data") or []):
        if not isinstance(p, dict):
            continue
        amt = fnum(p.get("positionAmt"))
        if not amt:
            continue
        out.append({"symbol": p.get("symbol"), "side": str(p.get("positionSide") or "").upper(),
                    "amt": abs(amt), "avg": fnum(p.get("avgPrice")), "mark": fnum(p.get("markPrice")),
                    "upnl": fnum(p.get("unrealizedProfit")), "lev": fnum(p.get("leverage")),
                    "liq": fnum(p.get("liquidationPrice"))})
    return out


def fetch_positions(src):
    return parse_positions(_bx("/openApi/swap/v2/user/positions")), "BingX /user/positions(唯讀)"


# --- K 線 ---
KLINE_TF, KLINE_N = "1h", 120
_KLINE_WANT = {"sym": "BTC-USDT"}
_BINGX_BASE = os.environ.get("AGENT_OFFICE_BINGX_BASE", "https://open-api.bingx.com")


def _bars_from_rows(rows):
    bars = []
    for r in rows or []:
        if isinstance(r, dict):
            t, o, h, l, c, v = (r.get("time"), r.get("open"), r.get("high"), r.get("low"),
                                r.get("close"), r.get("volume"))
        elif isinstance(r, (list, tuple)) and len(r) >= 6:
            t, o, h, l, c, v = r[:6]
        else:
            continue
        t, o, h, l, c, v = fnum(t), fnum(o), fnum(h), fnum(l), fnum(c), fnum(v)
        if None in (t, o, h, l, c):
            continue
        bars.append([t if t > 1e11 else t * 1000, o, h, l, c, v or 0.0])
    bars.sort(key=lambda b: b[0])
    dedup = []
    for b in bars:
        if dedup and dedup[-1][0] == b[0]:
            dedup[-1] = b
        else:
            dedup.append(b)
    return dedup[-KLINE_N:]


def _kline_store_bars(sym):
    pkl = P("kline_store", KLINE_TF, sym.replace("/", "_") + ".pkl")
    if not os.path.exists(pkl):   # 先自己查: kline_store._path() 會 mkdir, 本工具承諾只讀
        raise FileNotFoundError(2, "kline_store 沒有這檔", "kline_store/%s/%s.pkl" % (KLINE_TF, sym))
    import kline_store   # 需要 pandas; 主基地本來就有
    df = kline_store.load(sym, KLINE_TF, source="bingx")
    if df is None or not len(df):
        raise FileNotFoundError(2, "kline_store 沒有這檔", "kline_store/%s/%s.pkl" % (KLINE_TF, sym))
    df = df.tail(KLINE_N)
    rows = [[float(t), float(o), float(h), float(l), float(c), float(v)]
            for t, o, h, l, c, v in zip(df["time"], df["O"], df["H"], df["L"], df["C"], df["V"])]
    return _bars_from_rows(rows)


def fetch_kline(src):
    sym = _KLINE_WANT["sym"]
    api_err = None
    if not NO_NET:
        try:
            url = "%s/openApi/swap/v3/quote/klines?symbol=%s&interval=%s&limit=%d" % (
                _BINGX_BASE, sym, KLINE_TF, KLINE_N)
            j, _ = _http_json(url, timeout=15)
            if str(j.get("code", 0)) not in ("0", "00000"):
                raise RuntimeError("BingX code=%s %s" % (j.get("code"), str(j.get("msg") or "")[:60]))
            bars = _bars_from_rows(j.get("data"))
            if bars:
                return {"sym": sym, "bars": bars}, "BingX 公開 K 線 %s(即時)" % KLINE_TF
            api_err = "BingX 回 0 根"
        except Exception as e:
            api_err = human_err(e, "BingX K 線")
    try:
        bars = _kline_store_bars(sym)
        return {"sym": sym, "bars": bars, "fallback_why": api_err}, "本機 kline_store/%s(%s)" % (
            KLINE_TF, api_err or "離線")
    except Exception as e:
        raise RuntimeError("%s; 本機 kline_store 也讀不到(%s)" % (api_err or "沒打 API", human_err(e)))


# ───────────────────────── 組裝(純函式, 有測試) ─────────────────────────

DESKS = [   # 與 research/i1017/office_render.py DESKS 同名同色(Jesse 已認得)
    ("shaper", "形", "形態庫管理員", "守 3 個形狀", "#7fb0e0"),
    ("sentinel", "哨", "掃描員", "掃三市場找命中", "#39d353"),
    ("cost", "衡", "成本員", "算哪些划算", "#f2b134"),
    ("lever", "桿", "槓桿員", "標 20/30/50x", "#ff8a5c"),
    ("ledger", "帳", "記帳員", "記帳、到期結算", "#c792ea"),
    ("judge", "判", "判官", "訊號→方向/信心/槓桿", "#e05c8a"),
    ("calib", "準", "校準員", "預測對實際·誤差≤10%", "#5cc8e0"),
]
TAMA_AGENT = ("tama", "D", "TAMA 交易員", "B1/TL/SC/MRS/AUTO 實彈", "#ff2fd6")
MKT = [("crypto", "加密"), ("us", "美股"), ("tw", "台股")]
LEDGERS = [   # 與 tama_v5.py STRATEGY_LEDGERS 同表
    ("B1", "real_trading_strategy_b", "top"), ("I", "strat_i_ledger", "data"),
    ("J", "strat_j_ledger", "data"), ("K", "strat_k_ledger", "data"), ("L", "strat_l_ledger", "data"),
    ("M", "strat_m_ledger", "data"), ("TL", "strat_tl_ledger", "data"), ("RS", "strat_rs_ledger", "data"),
    ("SC", "strat_sc_ledger", "data"), ("MRS", "strat_mrs_ledger", "data"), ("AUTO", "strat_auto_ledger", "data"),
]
SL_REASONS = ("sl_hit", "sl_exchange", "sl", "sl_filled_before_cancel", "stop", "stop_loss")
OFFICE_STALE_S = 26 * 3600
TAMA_STALE_S = 15 * 60


def _ledgers(mem):
    mem = mem if isinstance(mem, dict) else {}
    data = mem.get("data") if isinstance(mem.get("data"), dict) else {}
    for tag, key, loc in LEDGERS:
        led = mem.get(key) if loc == "top" else data.get(key)
        if isinstance(led, dict):
            yield tag, led


def _trade_r(t):
    for k in ("realized_r", "pnl_r"):
        x = fnum(t.get(k))
        if x is not None:
            return x
    p, r = fnum(t.get("pnl_usdt")), fnum(t.get("risk_usdt"))
    return (p / r) if (p is not None and r is not None and r > 0) else None


def _is_paper(t):
    return t.get("paper") is True or str(t.get("reason") or t.get("exit_reason") or "").startswith("paper")


def _counted(t):
    """與 apex_a._pub_counted 同口徑: 人手平倉/不計 EV/交易所撤 SL 不算; 紙上單也不算。"""
    return (isinstance(t, dict) and not _is_paper(t) and t.get("external_close") is not True
            and t.get("ev_exclude") is not True
            and str(t.get("reason") or t.get("exit_reason") or "") != "SL_exchange_canceled")


def tama_trades(mem):
    """所有帳本已結算的真實單(新到舊)。"""
    out = []
    for tag, led in _ledgers(mem):
        rows = [t for k in ("closed_trades", "history") if isinstance(led.get(k), list) for t in led[k]]
        for t in rows:
            if not _counted(t):
                continue
            ts = parse_ts(t.get("closed_ts") or t.get("exit_ts") or t.get("ts"))
            out.append({"tag": tag, "id": t.get("id"), "sym": t.get("symbol"),
                        "dir": str(t.get("direction") or t.get("dir") or t.get("side") or "").upper(),
                        "r": _trade_r(t), "ts": ts, "open_ts": parse_ts(t.get("opened_ts")),
                        "entry": fnum(t.get("fill_price") or t.get("entry")),
                        "exit": fnum(t.get("exit_price")), "sl": fnum(t.get("sl_price")),
                        "reason": str(t.get("exit_reason") or t.get("reason") or "")})
    out.sort(key=lambda x: x["ts"] or 0, reverse=True)
    return out


def tama_open(mem):
    out = []
    for tag, led in _ledgers(mem):
        for p in (led.get("open_positions") if isinstance(led.get("open_positions"), list) else []):
            if not isinstance(p, dict) or _is_paper(p):
                continue
            out.append({"tag": tag, "id": p.get("id"), "sym": p.get("symbol"),
                        "dir": str(p.get("direction") or p.get("dir") or p.get("side") or "").upper(),
                        "entry": fnum(p.get("fill_price") or p.get("entry")), "sl": fnum(p.get("sl_price")),
                        "tp": fnum(p.get("tp_price")), "lev": fnum(p.get("leverage")),
                        "open_ts": parse_ts(p.get("opened_ts"))})
        for po in (led.get("pending_orders") if isinstance(led.get("pending_orders"), list) else []):
            if isinstance(po, dict):
                out.append({"tag": tag, "id": po.get("cid") or po.get("id"), "sym": po.get("symbol"),
                            "dir": str(po.get("direction") or po.get("dir") or "").upper(),
                            "entry": fnum(po.get("entry") or po.get("limit_price")), "sl": fnum(po.get("sl_price")),
                            "tp": fnum(po.get("tp_price")), "lev": fnum(po.get("leverage")),
                            "open_ts": parse_ts(po.get("placed_ts")), "pending": True})
    out.sort(key=lambda x: x["open_ts"] or 0, reverse=True)
    return out


def tama_heartbeat(mem):
    mem = mem if isinstance(mem, dict) else {}
    data = mem.get("data") if isinstance(mem.get("data"), dict) else {}
    best = None
    for v in [data.get("loop_ts")] + [led.get("last_cycle_ts") for _, led in _ledgers(mem)]:
        t = parse_ts(v)
        if t and (best is None or t > best):
            best = t
    return best


def equity_series(mem):
    data = (mem or {}).get("data") if isinstance((mem or {}).get("data"), dict) else {}
    out = []
    for p in (data.get("equity_history") or []):
        if not isinstance(p, dict):
            continue
        t, e = parse_ts(p.get("ts")), fnum(p.get("eq"))
        if t and e is not None and e > 0:
            out.append([int(t * 1000), round(e, 4)])
    out.sort(key=lambda x: x[0])
    return out


def trades_per_day(trades):
    """每個台北日的已結算筆數, 分贏/輸/平: [[day_start_ms(台北 00:00), win, loss, flat], ...]。"""
    by = {}
    for t in trades:
        if not t["ts"]:
            continue
        d = int(((t["ts"] + 8 * 3600) // 86400 * 86400 - 8 * 3600) * 1000)
        row = by.setdefault(d, [d, 0, 0, 0])
        r = t["r"]
        row[1 if (r or 0) > 0 else 2 if (r or 0) < 0 else 3] += 1
    return [by[k] for k in sorted(by)]


def risk_events(mem, killwatch=None, undertaker=None, config=None):
    """真實風控事件(新到舊)。id 穩定, 頁面靠 id 判斷「新的」才閃。"""
    ev = []
    for t in tama_trades(mem)[:200]:
        rs = t["reason"].lower()
        if "liq" in rs:
            ev.append({"id": "liq:%s:%s" % (t["id"] or t["sym"], t["ts"]), "ts": t["ts"], "kind": "liq",
                       "en": "LIQUIDATED · %s" % t["sym"], "zh": "%s 強平 %s" % (t["tag"], t["sym"]),
                       "detail": "R %s · 原因 %s" % (_fmt_r(t["r"]), t["reason"])})
        elif rs in SL_REASONS and (t["r"] is None or t["r"] < 0):
            ev.append({"id": "sl:%s:%s" % (t["id"] or t["sym"], t["ts"]), "ts": t["ts"], "kind": "sl",
                       "en": "STOP LOSS · %s" % t["sym"], "zh": "%s 停損 %s %s" % (t["tag"], t["sym"], t["dir"]),
                       "detail": "R %s · 進 %s → 出 %s" % (_fmt_r(t["r"]), _fmt_px(t["entry"]), _fmt_px(t["exit"]))})
    rcb = (mem or {}).get("risk_circuit_breaker") if isinstance((mem or {}).get("risk_circuit_breaker"), dict) else {}
    for c in (rcb.get("circuit_events") or []):
        if not isinstance(c, dict):
            continue
        txt = str(c.get("event") or "")
        if "全停" in txt or "熔斷" in txt or "halt" in txt.lower():
            ts = parse_ts(c.get("ts"))
            ev.append({"id": "cb:%s" % c.get("ts"), "ts": ts, "kind": "halt", "en": "CIRCUIT BREAKER · ALL STOP",
                       "zh": "熔斷全停", "detail": txt[:90]})
    if rcb.get("killswitch") is True or rcb.get("dd_halt") is True:
        ev.append({"id": "ks:%s:%s" % (rcb.get("killswitch"), rcb.get("dd_halt")), "ts": None, "kind": "halt",
                   "en": "KILLSWITCH ENGAGED", "zh": "總開關停機中",
                   "detail": "killswitch=%s dd_halt=%s" % (rcb.get("killswitch"), rcb.get("dd_halt"))})
    if rcb.get("daily_loss_limit_hit") is True:
        ev.append({"id": "dll:%s" % rcb.get("daily_pnl_date"), "ts": None, "kind": "halt",
                   "en": "DAILY LOSS LIMIT HIT", "zh": "單日虧損上限觸發",
                   "detail": "日期 %s" % rcb.get("daily_pnl_date")})
    kw = killwatch if isinstance(killwatch, dict) else {}
    if (fnum(kw.get("n_fire")) or 0) > 0:
        ev.append({"id": "kw:%s" % kw.get("run_at"), "ts": parse_local_ts(kw.get("run_at")), "kind": "kill",
                   "en": "STRATEGY KILL RULE FIRED", "zh": "斬首規則觸發 %d 條" % int(kw["n_fire"]),
                   "detail": "最近規則 %s" % kw.get("nearest_rule")})
    ut = undertaker if isinstance(undertaker, dict) else {}
    for r in (ut.get("retired") or []):
        rid = r.get("id") if isinstance(r, dict) else str(r)
        ev.append({"id": "ut:%s" % rid, "ts": parse_local_ts(ut.get("run_at")), "kind": "kill",
                   "en": "STRATEGY RETIRED · %s" % str(rid)[:24], "zh": "策略下架 %s" % rid,
                   "detail": (r.get("why") if isinstance(r, dict) else "") or ""})
    ev.sort(key=lambda e: e["ts"] or time.time(), reverse=True)
    return ev[:40]


def fmt_dur(sec):
    if sec is None:
        return "N/A"
    sec = max(0, sec)
    if sec < 120:
        return "%d 秒" % sec
    if sec < 7200:
        return "%d 分鐘" % (sec / 60)
    if sec < 172800:
        return "%.1f 小時" % (sec / 3600)
    return "%.1f 天" % (sec / 86400)


def clean_txt(s):
    return str(s).replace("%%", "%")


def _fmt_r(r):
    return "N/A" if r is None else "%+.2fR" % r


def _fmt_px(x):
    if x is None:
        return "N/A"
    return ("%.6g" % x) if abs(x) < 1000 else ("%.2f" % x)


def office_timeline(office):
    """7 桌所有事件依真實時間排序; 換桌的那一刻 = 交接(上一桌的產出交給下一桌)。"""
    desks = (office or {}).get("desks") or {}
    evs = []
    order = {k: i for i, (k, *_r) in enumerate(DESKS)}
    for k, dk in desks.items():
        for i, e in enumerate((dk or {}).get("events") or []):
            if isinstance(e, (list, tuple)) and len(e) >= 3 and fnum(e[1]):
                evs.append({"t": float(e[1]), "hms": str(e[0]), "desk": k, "text": clean_txt(e[2]),
                            "_o": (order.get(k, 99), i)})
    evs.sort(key=lambda x: (x["t"], x["_o"]))
    hand = []
    for a, b in zip(evs, evs[1:]):
        if a["desk"] != b["desk"]:
            hand.append({"t": b["t"], "from": a["desk"], "to": b["desk"], "text": b["text"]})
    for x in evs:
        x.pop("_o", None)
    return evs, hand


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _last_event(dk):
    ev = (dk or {}).get("events") or []
    if ev and isinstance(ev[-1], (list, tuple)) and len(ev[-1]) >= 3:
        return str(ev[-1][0]), clean_txt(ev[-1][2])
    lg = (dk or {}).get("log") or []
    return (None, clean_txt(lg[-1])) if lg else (None, None)


def build_agents(office, running, now, mem=None, cfg=None, bx_pos=None, mem_err=None):
    """8 張卡: 形態辦公室 7 桌 + D·TAMA。每張: 狀態燈 / 一行最新動作 / 關鍵數字 / 迷你進度條 / 桌上螢幕圖。"""
    office = office if isinstance(office, dict) else None
    desks = (office or {}).get("desks") or {}
    hits = (office or {}).get("today_hits") or []
    gen_ts = parse_local_ts((office or {}).get("generated"))
    stale = gen_ts is None or (now - gen_ts) > OFFICE_STALE_S
    agents = []
    for key, zh, name, role, color in DESKS:
        dk = desks.get(key) if isinstance(desks.get(key), dict) else None
        a = {"key": key, "zh": zh, "name": name, "role": role, "color": color,
             "status": "na", "status_text": "N/A", "action": None, "action_t": None,
             "metric": None, "bar": None, "monitor": None, "last_run": None}
        if office is None or dk is None:
            a["action"] = "讀不到 research/i1017/office/state.json" if office is None else "state.json 沒有這一桌"
            agents.append(a)
            continue
        st = str(dk.get("status") or "")
        a["last_run"], a["did"], a["next"] = dk.get("last_run"), dk.get("did"), dk.get("next")
        a["action_t"], a["action"] = _last_event(dk)
        if running:
            a["status"], a["status_text"] = "busy", "處理中"
            a["action"] = "跑班中… " + (a["action"] or "")
        elif st == "bad":
            a["status"], a["status_text"] = "alert", "出錯"
        elif st == "warn":
            a["status"], a["status_text"] = "alert", "警報"
        elif stale:
            a["status"], a["status_text"] = "alert", "超過 26 小時沒跑班"
        elif st == "ok":
            a["status"], a["status_text"] = "ok", "正常"
        else:
            a["status"], a["status_text"] = "na", st or "N/A"
        _desk_metric(a, key, dk, office, hits, now)
        agents.append(a)
    agents.append(_tama_agent(mem, cfg, bx_pos, now, mem_err))
    return agents


def _desk_metric(a, key, dk, office, hits, now):
    if key == "shaper":
        pats = ((office.get("library") or {}).get("patterns")) or []
        a["metric"] = {"label": "形狀", "value": len(pats), "unit": "個"}
        nxt = None
        for e in (dk.get("events") or []):
            m = re.search(r"下次 (\d{4}-\d{2}-\d{2})", str(e[2] if isinstance(e, (list, tuple)) else e))
            if m:
                nxt = parse_local_ts(m.group(1))
        created = parse_ts((office.get("library") or {}).get("created"))
        if nxt and created and nxt > created:
            a["bar"] = {"v": max(0.0, min(1.0, (now - created) / (nxt - created))),
                        "label": "距月漂移重算 %d 天" % max(0, math.ceil((nxt - now) / 86400))}
        a["monitor"] = {"type": "lines", "series": [p.get("price") or [] for p in pats],
                        "names": [p.get("name") for p in pats]}
    elif key == "sentinel":
        a["metric"] = {"label": "今日命中", "value": len(hits), "unit": "筆"}
        fresh = office.get("data_fresh") or {}
        ok = sum(1 for d in fresh.values() if parse_local_ts(d) and now - parse_local_ts(d) <= 4 * 86400)
        if fresh:
            a["bar"] = {"v": ok / len(fresh), "label": "資料新鮮 %d/%d 市場" % (ok, len(fresh))}
        a["monitor"] = {"type": "bars", "values": [sum(1 for h in hits if h.get("market") == m) for m, _ in MKT],
                        "labels": [z for _, z in MKT]}
    elif key == "cost":
        n_t = sum(1 for h in hits if h.get("tradable"))
        a["metric"] = {"label": "可交易", "value": n_t, "unit": "/%d" % len(hits)}
        if hits:
            a["bar"] = {"v": n_t / len(hits), "label": "划算 %d/%d" % (n_t, len(hits))}
        a["monitor"] = {"type": "bars", "labels": [z for _, z in MKT],
                        "values": [_median([h.get("cost_bps") for h in hits if h.get("market") == m]) for m, _ in MKT],
                        "unit": "bp"}
    elif key == "lever":
        risky = sum(1 for h in hits if h.get("lev_risky"))
        a["metric"] = {"label": "高風險", "value": risky, "unit": "/%d" % len(hits)}
        if hits:
            a["bar"] = {"v": risky / len(hits), "label": "高風險標記 %d/%d" % (risky, len(hits)), "tone": "bad"}
        vals = []
        for L in (20, 30, 50):   # 五日爆倉率中位(有校正值用校正值)
            m = _median([h.get("p_liq%d_cal" % L) if h.get("p_liq%d_cal" % L) is not None
                         else h.get("p_liq%d" % L) for h in hits])
            vals.append(None if m is None else round(m * 100))
        a["monitor"] = {"type": "bars", "labels": ["20x", "30x", "50x"], "unit": "%", "tone": "bad", "values": vals}
    elif key == "ledger":
        lg = office.get("ledger") or {}
        o, c = fnum(lg.get("open")), fnum(lg.get("closed"))
        a["metric"] = {"label": "未結/已結", "value": "%s/%s" % (_i(o), _i(c)), "unit": ""}
        if o is not None and c is not None and (o + c) > 0:
            a["bar"] = {"v": c / (o + c), "label": "結算進度 %d%%" % round(c / (o + c) * 100)}
        fw = ((office.get("forward") or {}).get("totals")) or {}
        a["monitor"] = {"type": "bars", "labels": ["未結", "已結", "前瞻"],
                        "values": [o, c, fnum(fw.get("rows"))]}
    elif key == "judge":
        jd = office.get("judge") or {}
        led = jd.get("ledger") or {}
        a["metric"] = {"label": "判/做", "value": "%s/%s" % (_i(jd.get("n_verdicts")), _i(jd.get("n_act"))), "unit": "檔"}
        tot, sc = fnum(led.get("total")), fnum(led.get("scored"))
        if tot:
            a["bar"] = {"v": (sc or 0) / tot, "label": "已打分 %s/%s" % (_i(sc), _i(tot))}
        vs = jd.get("verdicts") or []
        a["monitor"] = {"type": "bars", "labels": ["多", "空", "不做"],
                        "values": [sum(1 for v in vs if v.get("dir") == 1), sum(1 for v in vs if v.get("dir") == -1),
                                   sum(1 for v in vs if v.get("dir") == 0)]}
    elif key == "calib":
        cb = office.get("calib") or {}
        g, r = fnum(cb.get("n_green")), fnum(cb.get("n_red"))
        a["metric"] = {"label": "綠/紅", "value": "%s/%s" % (_i(g), _i(r)), "unit": "類"}
        if g is not None and r is not None and (g + r) > 0:
            a["bar"] = {"v": g / (g + r), "label": "誤差≤10%% 達標 %d%%" % round(g / (g + r) * 100),
                        "tone": "bad" if r > g else "ok"}
        hist = cb.get("hist") or []
        a["monitor"] = {"type": "bars", "labels": ["綠", "紅", "灰"],
                        "values": [sum(1 for h in hist if h.get("status") == s) for s in ("green", "red", "gray")],
                        "colors": ["#39ff88", "#ff3355", "#5a6270"]}


def _i(x):
    x = fnum(x)
    return "N/A" if x is None else ("%d" % x)


def _tama_agent(mem, cfg, bx_pos, now, mem_err):
    key, zh, name, role, color = TAMA_AGENT
    a = {"key": key, "zh": zh, "name": name, "role": role, "color": color, "status": "na",
         "status_text": "N/A", "action": None, "action_t": None, "metric": None, "bar": None,
         "monitor": None, "last_run": None}
    if not isinstance(mem, dict):
        a["action"] = mem_err or "讀不到 tama_memory.json"
        return a
    hb = tama_heartbeat(mem)
    opens = [p for p in tama_open(mem) if not p.get("pending")]
    trades = tama_trades(mem)
    cap = 0
    for k, v in ((cfg or {}).items() if isinstance(cfg, dict) else []):
        if isinstance(v, dict) and v.get("live_enabled") is True:
            cap += int(fnum(v.get("max_positions")) or 0)
    for s in ((cfg or {}).get("auto_strategies") or []) if isinstance(cfg, dict) else []:
        if isinstance(s, dict) and s.get("live_enabled") is True:
            cap += int(fnum(s.get("max_positions")) or 0)
    n_open = len(opens) if bx_pos is None else max(len(opens), len(bx_pos))
    a["metric"] = {"label": "持倉", "value": n_open, "unit": "/%d" % cap if cap else ""}
    if cap:
        a["bar"] = {"v": min(1.0, n_open / cap), "label": "倉位用量 %d/%d(實彈策略上限合計)" % (n_open, cap)}
    rcb = mem.get("risk_circuit_breaker") if isinstance(mem.get("risk_circuit_breaker"), dict) else {}
    a["last_run"] = datetime.fromtimestamp(hb, TPE).strftime("%Y-%m-%d %H:%M:%S") if hb else None
    if opens:
        p = opens[0]
        a["action"] = "%s 持有 %s %s @%s" % (p["tag"], p["sym"], p["dir"], _fmt_px(p["entry"]))
        a["action_t"] = datetime.fromtimestamp(p["open_ts"], TPE).strftime("%m-%d %H:%M") if p["open_ts"] else None
    elif trades:
        t = trades[0]
        a["action"] = "%s 平倉 %s %s %s(%s)" % (t["tag"], t["sym"], t["dir"], _fmt_r(t["r"]), t["reason"])
        a["action_t"] = datetime.fromtimestamp(t["ts"], TPE).strftime("%m-%d %H:%M") if t["ts"] else None
    else:
        a["action"] = "帳本沒有任何已結算真實單"
    if rcb.get("killswitch") is True or rcb.get("dd_halt") is True:
        a["status"], a["status_text"] = "alert", "總開關停機"
    elif rcb.get("daily_loss_limit_hit") is True:
        a["status"], a["status_text"] = "alert", "單日虧損上限"
    elif hb is None or now - hb > TAMA_STALE_S:
        a["status"] = "alert"
        a["status_text"] = ("%s 沒心跳" % fmt_dur(now - hb)) if hb else "從未有心跳"
    elif n_open:
        a["status"], a["status_text"] = "busy", "處理中(持倉)"
    else:
        a["status"], a["status_text"] = "ok", "正常"
    eq = equity_series(mem)
    a["monitor"] = {"type": "line", "values": [e[1] for e in eq[-72:]]}
    return a


# --- K 線分析(只用真 K 線算, 不寫看法) ---
def _ema(vals, n):
    if len(vals) < n:
        return [None] * len(vals)
    k = 2.0 / (n + 1)
    out = [None] * (n - 1)
    e = sum(vals[:n]) / n
    out.append(e)
    for v in vals[n:]:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def kline_analysis(bars):
    if not bars or len(bars) < 2:
        return None
    c = [b[4] for b in bars]
    ema20, ema50 = _ema(c, 20), _ema(c, 50)
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i][2], bars[i][3], bars[i - 1][4]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(trs[-14:]) / min(14, len(trs)) if trs else None
    last = c[-1]
    ago24 = c[-25] if len(c) >= 25 else c[0]
    win = bars[-24:]
    e20 = ema20[-1]
    slope = None
    if e20 is not None and len(ema20) >= 6 and ema20[-6] is not None:
        slope = (e20 - ema20[-6]) / ema20[-6]
    return {"last": last, "chg24": (last / ago24 - 1) if ago24 else None,
            "hi24": max(b[2] for b in win), "lo24": min(b[3] for b in win),
            "hi_all": max(b[2] for b in bars), "lo_all": min(b[3] for b in bars),
            "ema20": e20, "ema50": ema50[-1], "atr": atr, "atr_pct": (atr / last) if (atr and last) else None,
            "above_ema20": (last > e20) if e20 is not None else None, "ema20_slope5": slope,
            "ema20_series": [None if v is None else round(v, 8) for v in ema20],
            "n": len(bars), "first_ms": bars[0][0], "last_ms": bars[-1][0]}


def pick_symbol(pinned, bx_pos, opens, trades, verdicts, now):
    """當前標的: 手動釘選 > BingX 持倉 > TAMA 持倉 > 72h 內最近成交 > 判官最高信心(BingX 可交易) > BTC。"""
    cands = []

    def add(sym, why):
        if sym and sym not in [c["sym"] for c in cands]:
            cands.append({"sym": sym, "why": why})
    for p in bx_pos or []:
        add(p.get("symbol"), "BingX 持倉")
    for p in opens or []:
        add(p.get("sym"), "%s 持倉" % p.get("tag"))
    for t in (trades or [])[:6]:
        if t.get("ts") and now - t["ts"] <= 72 * 3600:
            add(t.get("sym"), "最近成交")
    vs = [v for v in (verdicts or []) if v.get("market") in ("crypto", "nc") and v.get("sym") and v.get("dir")]
    vs.sort(key=lambda v: -(fnum(v.get("conf")) or 0))
    for v in vs[:3]:
        add(v.get("sym"), "判官信心 %.2f" % (fnum(v.get("conf")) or 0))
    add("BTC-USDT", "預設")
    if pinned:
        why = next((c["why"] for c in cands if c["sym"] == pinned), "手動選")
        return pinned, "手動釘選(%s)" % why, cands
    return cands[0]["sym"], cands[0]["why"], cands


PARAM_GROUPS = [   # 參數面板: (區塊, 標題, [(鍵, 中文, 格式)])
    ("b1", "B1 策略B", [("live_enabled", "實彈", "bool"), ("risk_pct", "每筆風險", "pct"), ("leverage", "槓桿", "x"),
                      ("max_positions", "倉位上限", "int"), ("daily_loss_limit_pct", "單日虧損上限", "pct"),
                      ("atr_r_thr", "ATR 比門檻", "num"), ("volr_thr", "量比門檻", "num"),
                      ("regime_gate", "盤勢閘", "bool"), ("maker_first", "掛單優先", "bool")]),
    ("tl", "TL 趨勢", [("live_enabled", "實彈", "bool"), ("risk_pct", "每筆風險", "pct"), ("leverage", "槓桿", "x"),
                     ("max_positions", "倉位上限", "int"), ("rr", "盈虧比", "num"), ("max_hold_h", "最長持有", "h")]),
    ("sc", "SC 崩盤捕手", [("live_enabled", "實彈", "bool"), ("risk_pct", "每筆風險", "pct"), ("leverage", "槓桿", "x"),
                        ("max_positions", "倉位上限", "int")]),
    ("mrs", "MRS 均值回歸", [("live_enabled", "實彈", "bool"), ("risk_pct", "每筆風險", "pct"),
                          ("max_positions", "倉位上限", "int"), ("vd_thr", "量背離門檻", "num"),
                          ("rsi_thr", "RSI 門檻", "num"), ("sl_atr", "停損 ATR", "num")]),
    ("rs", "RS 相對強弱", [("live_enabled", "實彈", "bool"), ("risk_pct", "每筆風險", "pct"),
                        ("max_positions", "倉位上限", "int")]),
    ("global", "全域風控", [("killswitch", "總開關", "bool_bad"), ("same_dir_max", "同向上限", "int"),
                         ("total_drawdown_halt_pct", "總回撤停機", "pct"), ("cb_auto_release_h", "熔斷自動解除", "h"),
                         ("maker_entry_all", "全面掛單進場", "bool")]),
]


def build_params(cfg, cfg_mtime, readback, now):
    if not isinstance(cfg, dict):
        return None
    groups = []
    for blk, title, keys in PARAM_GROUPS:
        b = cfg.get(blk)
        if not isinstance(b, dict):
            continue
        rows = []
        for k, zh, fmt in keys:
            rows.append({"k": "%s.%s" % (blk, k), "zh": zh, "fmt": fmt, "v": b.get(k) if k in b else None})
        groups.append({"key": blk, "title": title, "live": b.get("live_enabled"), "rows": rows})
    autos = [s for s in (cfg.get("auto_strategies") or []) if isinstance(s, dict)]
    meta = cfg.get("_meta") or {}
    rb = readback if isinstance(readback, dict) else {}
    return {"groups": groups,
            "auto": {"n": len(autos), "live": sum(1 for s in autos if s.get("live_enabled") is True),
                     "states": [{"id": s.get("id"), "state": s.get("state"), "live": s.get("live_enabled"),
                                 "side": s.get("side"), "risk_pct": s.get("risk_pct")} for s in autos[:6]]},
            "updated": meta.get("updated"), "by": meta.get("by"),
            "file_age_s": (now - cfg_mtime) if cfg_mtime else None,
            "gh_sha": rb.get("gh_sha"), "applied_sha": rb.get("applied_sha"),
            "applied": (rb.get("gh_sha") == rb.get("applied_sha")) if rb.get("gh_sha") and rb.get("applied_sha") else None,
            "commit_age_min": fnum(rb.get("config_commit_age_min")), "readback_at": rb.get("run_at")}


# ───────────────────────── 伺服器狀態 ─────────────────────────

class Hub(object):
    def __init__(self):
        self.files = {
            "office": FileSource("office", "research/i1017/office/state.json", "形態辦公室 state.json(07:40 排程)"),
            "office_rs": FileSource("office_rs", "shape_office_result.json", "shape_office_result.json"),
            "notices": FileSource("notices", "research/i1017/office/notices.json", "辦公室公告 notices.json"),
            "config": FileSource("config", "tama_config_live_snapshot.json", "tama_config 鏡像(config_readback 蟻)"),
            "readback": FileSource("readback", "config_readback_result.json", "config_readback_result.json"),
            "killwatch": FileSource("killwatch", "kill_watch_result.json", "斬首規則 kill_watch_result.json"),
            "undertaker": FileSource("undertaker", "undertaker_result.json", "下架蟻 undertaker_result.json"),
            "riskbudget": FileSource("riskbudget", "risk_budget_result.json", "risk_budget_result.json"),
        }
        self.net = {
            "tama": NetSource("tama", 60, fetch_tama, "GitHub tama_memory.json"),
            "balance": NetSource("balance", 15, fetch_balance, "BingX /user/balance"),
            "positions": NetSource("positions", 15, fetch_positions, "BingX /user/positions"),
            "kline": NetSource("kline", 30, fetch_kline, "BingX 公開 K 線"),
        }
        self.pinned = None
        self.started = time.time()
        self._file_thread = None

    def start(self):
        for f in self.files.values():
            f.poll()
        for n in self.net.values():
            th = threading.Thread(target=n.loop, name="src-" + n.name, daemon=True)
            th.start()

        def fl():
            while True:
                time.sleep(1.0)
                for f in self.files.values():
                    try:
                        f.poll()
                    except Exception as e:   # 不讓一個壞檔拖垮整條
                        log.warning("file poll %s: %s", f.name, e)
        self._file_thread = threading.Thread(target=fl, name="files", daemon=True)
        self._file_thread.start()

    def office_running(self):
        p = P("shape_office.lock")
        try:
            age = time.time() - os.path.getmtime(p)
        except OSError:
            return False
        return -300 < age < 3600   # 與 ant.py stale_lock_sec 同口徑: 逾 1 小時視為殘留鎖

    def state(self):
        now = time.time()
        office, office_m, _ = self.files["office"].snap()
        cfg, cfg_m, _ = self.files["config"].snap()
        mem, mem_at, mem_err = self.net["tama"].snap()
        bal, bal_at, bal_err = self.net["balance"].snap()
        bxp, bxp_at, bxp_err = self.net["positions"].snap()
        kl, kl_at, kl_err = self.net["kline"].snap()
        running = self.office_running()
        bx_pos_live = bxp if (bxp is not None and bxp_err is None) else None

        trades = tama_trades(mem) if mem else []
        opens = tama_open(mem) if mem else []
        eq = equity_series(mem) if mem else []
        verdicts = (((office or {}).get("judge") or {}).get("verdicts")) or []
        sym, why, cands = pick_symbol(self.pinned, bx_pos_live, opens, trades, verdicts, now)
        if _KLINE_WANT["sym"] != sym:
            _KLINE_WANT["sym"] = sym
            self.net["kline"].kick.set()

        timeline, hand = office_timeline(office)
        agents = build_agents(office, running, now, mem, cfg, bx_pos_live, mem_err)
        kw, _, _ = self.files["killwatch"].snap()
        ut, _, _ = self.files["undertaker"].snap()
        rbud, _, _ = self.files["riskbudget"].snap()
        rb, _, _ = self.files["readback"].snap()
        notices, _, _ = self.files["notices"].snap()
        office_rs, _, _ = self.files["office_rs"].snap()

        # 餘額: BingX 即時 > D 記憶最後一筆權益 > risk_budget 蟻(都附來源與時間)
        balance = None
        if bal is not None and bal_err is None:
            balance = dict(bal, src="BingX /user/balance 即時", ts=bal_at)
        elif eq:
            balance = {"equity": eq[-1][1], "balance": None, "available": None, "upnl": None,
                       "src": "D 記憶 equity_history 最後一筆(BingX 讀不到: %s)" % (bal_err or "未設定"),
                       "ts": eq[-1][0] / 1000.0}
        elif isinstance(rbud, dict) and fnum(rbud.get("equity_usd")) is not None:
            balance = {"equity": fnum(rbud.get("equity_usd")), "balance": None, "available": None, "upnl": None,
                       "src": "risk_budget 蟻 equity_usd(BingX 讀不到: %s)" % (bal_err or "未設定"),
                       "ts": parse_local_ts(rbud.get("run_at"))}

        kline = None
        if isinstance(kl, dict) and kl.get("sym") == sym and kl.get("bars"):
            ana = kline_analysis(kl["bars"])
            kline = {"sym": sym, "tf": KLINE_TF, "bars": kl["bars"], "analysis": ana,
                     "src": self.net["kline"].src_detail, "age_s": (now - kl_at) if kl_at else None,
                     "fallback_why": kl.get("fallback_why")}
        rel_pos = [p for p in opens if p.get("sym") == sym]
        rel_bx = [p for p in (bx_pos_live or []) if p.get("symbol") == sym]
        rel_tr = [t for t in trades if t.get("sym") == sym][:20]
        rel_vd = [v for v in verdicts if v.get("sym") == sym][:3]

        rcb = (mem or {}).get("risk_circuit_breaker") if isinstance((mem or {}).get("risk_circuit_breaker"), dict) else None
        sources = {k: f.meta(now) for k, f in self.files.items()}
        sources.update({k: n.meta(now) for k, n in self.net.items()})
        if running:
            sources["office"]["note"] = "shape_office 正在跑班"
        out = {
            "now": now, "ver": VERSION, "root": ROOT, "started": self.started,
            "sources": sources,
            "agents": agents,
            "office": None if office is None else {
                "generated": office.get("generated"), "generated_ts": parse_local_ts(office.get("generated")),
                "n_runs": office.get("n_runs"), "status": office.get("status"), "running": running,
                "shift": office.get("shift"), "timeline": timeline, "handoffs": hand,
                "warning": ((office.get("forward") or {}).get("warning")),
                "notices": [n.get("text") for n in (notices or []) if isinstance(n, dict) and n.get("text")]
                if isinstance(notices, list) else [],
                "data_fresh": office.get("data_fresh"),
                "rc": (office_rs or {}).get("rc") if isinstance(office_rs, dict) else None,
                "run_at": (office_rs or {}).get("run_at") if isinstance(office_rs, dict) else None,
                "elapsed_min": (office_rs or {}).get("elapsed_min") if isinstance(office_rs, dict) else None,
            },
            "tama": None if mem is None else {
                "heartbeat": tama_heartbeat(mem), "n_trades": len(trades),
                "open": opens[:12], "recent": trades[:12],
                "breaker": None if rcb is None else {
                    "killswitch": rcb.get("killswitch"), "dd_halt": rcb.get("dd_halt"),
                    "daily_loss_limit_hit": rcb.get("daily_loss_limit_hit"),
                    "daily_pnl_usdt": fnum(rcb.get("daily_pnl_usdt")), "daily_pnl_date": rcb.get("daily_pnl_date"),
                    "peak_equity": fnum(rcb.get("peak_equity"))},
            },
            "bx_positions": bx_pos_live,
            "equity": {"series": eq, "vol": trades_per_day(trades), "balance": balance,
                       "dd_30d_pct": fnum((rbud or {}).get("dd_30d_pct")) if isinstance(rbud, dict) else None},
            "kline": kline, "kline_sym": sym, "kline_why": why, "kline_cands": cands[:8],
            "kline_ctx": {"open": rel_pos, "bx": rel_bx, "trades": rel_tr, "verdicts": rel_vd},
            "params": build_params(cfg, cfg_m, rb, now),
            "risk": {"events": risk_events(mem, kw, ut, cfg)},
        }
        return sanitize(out)


HUB = Hub()
PAGE = os.path.join(HERE, "agent_office.html")


class Handler(BaseHTTPRequestHandler):
    server_version = "apex-agent-office/" + VERSION

    def log_message(self, fmt, *args):   # 每 2 秒一次的輪詢不進 log
        pass

    def _send(self, code, body, ctype):
        b = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        try:
            if u.path in ("/", "/index.html", "/agent_office.html"):
                with open(PAGE, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif u.path == "/api/state":
                self._send(200, json.dumps(HUB.state(), ensure_ascii=False, allow_nan=False),
                           "application/json; charset=utf-8")
            elif u.path == "/api/pin":
                s = (parse_qs(u.query).get("sym") or [""])[0].strip().upper()
                HUB.pinned = s if re.fullmatch(r"[A-Z0-9]{1,20}-[A-Z]{2,6}", s) else None
                HUB.net["kline"].kick.set()
                self._send(200, json.dumps({"pinned": HUB.pinned}), "application/json")
            elif u.path == "/api/health":
                self._send(200, json.dumps({"ok": True, "ver": VERSION, "root": ROOT}), "application/json")
            else:
                self._send(404, "not found", "text/plain; charset=utf-8")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            log.exception("handler %s", u.path)
            try:
                self._send(500, json.dumps({"error": human_err(e)}, ensure_ascii=False), "application/json")
            except Exception:
                pass


def _port_busy(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        return s.connect_ex((HOST, port)) == 0
    finally:
        s.close()


def main():
    url = "http://%s:%d/" % (HOST, PORT)
    if _port_busy(PORT):   # singleton: 已經在跑就只開瀏覽器
        print("[agent office] 已經在跑了(埠 %d 有人用), 直接開 %s" % (PORT, url))
        if not os.environ.get("AGENT_OFFICE_NO_BROWSER"):
            webbrowser.open(url)
        return 0
    if not os.path.exists(PAGE):
        print("[agent office] 找不到頁面檔 %s, 請把 agent_office.html 放在同一個資料夾" % PAGE)
        return 2
    log.info("agent office %s 啟動 root=%s port=%d apex_common=%s", VERSION, ROOT, PORT, "ok" if AC else AC_ERR)
    for k in ("GITHUB_TOKEN", "BINGX_API_KEY", "BINGX_API_SECRET"):
        log.info("  .env %s: %s", k, "有" if os.environ.get(k) else "缺")
    HUB.start()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    print("[agent office] 開好了: %s  (關掉這個視窗就停止; log 在 %s)" % (url, LOG_PATH))
    if not os.environ.get("AGENT_OFFICE_NO_BROWSER"):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        log.info("agent office 停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
