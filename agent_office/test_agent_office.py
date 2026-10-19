# -*- coding: utf-8 -*-
"""agent office 組裝邏輯的自測(不需要 pytest, 不打網路)。

跑法(在 C:\\claude-assistant 底下):  python -X utf8 agent_office\\test_agent_office.py
欄位形狀照 tama_v5.py / shape_office.py 的真實結構寫; 數字是測試用的小例子。
"""
import json
import math
import os
import sys
import tempfile
import time

os.environ["AGENT_OFFICE_NO_NET"] = "1"
os.environ.setdefault("AGENT_OFFICE_NO_BROWSER", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_office_server as A  # noqa: E402

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)
        print("FAIL", msg)


MEM = {
    "risk_circuit_breaker": {"killswitch": False, "daily_loss_limit_hit": False, "daily_pnl_usdt": -2.0,
                             "daily_pnl_date": "2026-10-19",
                             "circuit_events": [{"ts": "2026-08-13T13:13:12+00:00", "event": "總回撤達25%(峰值100.0->75.0),自動全停"},
                                                {"ts": "2026-08-19T16:43:37+00:00", "event": "Jesse批准重啟(第四次)"}]},
    "real_trading_strategy_b": {
        "mode": "live", "last_cycle_ts": "2026-10-19T03:00:00+00:00",
        "open_positions": [{"id": "b1", "symbol": "TRX-USDT", "dir": "SHORT", "fill_price": 0.33, "sl_price": 0.333,
                            "opened_ts": "2026-10-19T02:00:00+00:00"}],
        "closed_trades": [{"id": "c1", "symbol": "TRX-USDT", "dir": "SHORT", "fill_price": 0.3300, "exit_price": 0.3313,
                           "exit_reason": "sl_hit", "realized_r": -2.0, "closed_ts": "2026-10-01T11:13:00+00:00",
                           "opened_ts": "2026-10-01T11:03:57+00:00"}]},
    "data": {
        "loop_ts": "2026-10-19T03:01:00+00:00",
        "equity_history": [{"ts": "2026-10-18T00:00:00+00:00", "eq": 1000.0}, {"ts": "2026-10-19T00:00:00+00:00", "eq": 1012.5},
                           {"ts": "bad", "eq": 9}, {"ts": "2026-10-19T01:00:00+00:00", "eq": float("nan")}],
        "strat_tl_ledger": {"open_positions": [], "history": [
            {"id": "t1", "symbol": "LINK-USDT", "direction": "LONG", "reason": "SL_exchange", "pnl_r": -1.07,
             "closed_ts": "2026-08-31T07:23:00+00:00"},
            {"id": "t2", "symbol": "LINK-USDT", "direction": "LONG", "reason": "SL_exchange_canceled", "pnl_r": -1.0,
             "closed_ts": "2026-08-31T08:00:00+00:00"},
            {"id": "t3", "symbol": "ETH-USDT", "direction": "LONG", "reason": "SL_exchange", "pnl_r": 0.4,
             "closed_ts": "2026-08-31T15:59:00+00:00"},   # 移動停利掃到: 不算停損事件
            {"id": "t4", "symbol": "BTC-USDT", "direction": "LONG", "reason": "manual", "pnl_r": 1.0,
             "external_close": True, "closed_ts": "2026-08-31T09:00:00+00:00"}]},
        "strat_rs_ledger": {"history": [{"id": "p1", "symbol": "SOL-USDT", "reason": "paper_sl", "paper": True,
                                         "closed_ts": "2026-08-31T10:00:00+00:00"}]},
    },
}

OFFICE = {
    "generated": "2026-10-19 07:40", "n_runs": 25, "status": "影子帳(不下單)",
    "desks": {
        "shaper": {"status": "ok", "did": "庫校驗 3 形狀", "last_run": "2026-10-19 07:40",
                   "events": [["07:40:07", 100.0, "庫 sha x"], ["07:40:07", 100.01, "每月漂移重算: 下次 2026-11-01"]]},
        "sentinel": {"status": "ok", "events": [["07:40:12", 104.0, "tw 刷新"], ["07:40:23", 115.0, "命中 2"]]},
        "cost": {"status": "ok", "events": [["07:40:23", 115.4, "成本表"]]},
        "lever": {"status": "ok", "events": [["07:40:23", 115.41, "止損放在爆倉線 80%% 內"]]},
        "ledger": {"status": "ok", "events": [["07:40:23", 115.6, "補進場 0"]]},
        "judge": {"status": "bad", "events": [["07:40:23", 115.6, "形狀: 2 筆"]]},
        "calib": {"status": "warn", "events": [["07:40:23", 115.42, "查表"], ["07:40:28", 120.3, "體檢"]]},
    },
    "today_hits": [{"market": "us", "tradable": True, "lev_risky": True, "cost_bps": 10, "p_liq20": 0.6, "p_liq20_cal": 0.5,
                    "p_liq30": 0.7, "p_liq50": 0.8},
                   {"market": "tw", "tradable": False, "lev_risky": False, "cost_bps": 5, "p_liq20": 0.2, "p_liq30": 0.3,
                    "p_liq50": 0.4}],
    "ledger": {"open": 3, "closed": 1}, "data_fresh": {"加密": "2026-10-17", "美股": "2026-10-16", "台股": "2026-10-01"},
    "library": {"created": "2026-10-17T20:19:29", "patterns": [{"name": "a", "price": [1, 2, 3]}, {"name": "b", "price": [3, 2]}]},
    "judge": {"n_verdicts": 3, "n_act": 2, "ledger": {"total": 10, "scored": 2},
              "verdicts": [{"sym": "AAA-USDT", "market": "crypto", "dir": 1, "conf": 0.5},
                           {"sym": "NCX-USDT", "market": "nc", "dir": -1, "conf": 0.9},
                           {"sym": "AEHR", "market": "us", "dir": 1, "conf": 0.99}]},
    "calib": {"n_green": 1, "n_red": 3, "hist": [{"status": "green"}, {"status": "red"}]},
}


def t_parsers():
    b = A.parse_balance({"code": 0, "data": {"balance": {"asset": "USDT", "equity": "1234.5", "balance": "1230.2",
                                                         "availableMargin": "9", "unrealizedProfit": "0.3"}}})
    check(b and b["equity"] == 1234.5 and b["available"] == 9.0 and b["upnl"] == 0.3, "巢狀 balance 解析")
    b = A.parse_balance({"data": [{"asset": "VST", "equity": "1"}, {"asset": "USDT", "equity": "7"}]})
    check(b and b["equity"] == 7.0, "list 版 balance 取 USDT")
    check(A.parse_balance({"data": {}}) is None, "空 balance -> None(頁面 N/A)")
    ps = A.parse_positions({"data": [{"symbol": "ETH-USDT", "positionSide": "long", "positionAmt": "-0.2", "avgPrice": "2450"},
                                     {"symbol": "X-USDT", "positionAmt": "0"}]})
    check(len(ps) == 1 and ps[0]["amt"] == 0.2 and ps[0]["side"] == "LONG", "持倉解析(0 量排除)")
    check(A.parse_ts("2026-08-12 21:00:00") == A.parse_ts("2026-08-12T21:00:00+00:00"), "無時區 ISO 視為 UTC")
    check(A.parse_ts(1790852603000) == 1790852603.0, "毫秒")
    check(abs(A.parse_local_ts("2026-10-19 07:40") - A.parse_ts("2026-10-18T23:40:00+00:00")) < 1, "本機蟻時間 = 台北")
    check(A.sanitize({"a": float("nan"), "b": [float("inf"), 1.0]}) == {"a": None, "b": [None, 1.0]}, "NaN/Inf -> null")
    check(A.fmt_dur(30) == "30 秒" and A.fmt_dur(4 * 86400) == "4.0 天", "人話時長")


def t_tama():
    tr = A.tama_trades(MEM)
    ids = [t["id"] for t in tr]
    check("t2" not in ids and "t4" not in ids and "p1" not in ids, "撤 SL / 人手平倉 / 紙上單不算: %s" % ids)
    check(ids[0] == "c1", "新到舊排序")
    op = A.tama_open(MEM)
    check(len(op) == 1 and op[0]["sym"] == "TRX-USDT" and op[0]["tag"] == "B1", "持倉")
    check(A.tama_heartbeat(MEM) == A.parse_ts("2026-10-19T03:01:00+00:00"), "心跳取最新")
    eq = A.equity_series(MEM)
    check([e[1] for e in eq] == [1000.0, 1012.5], "權益序列丟掉壞列: %s" % eq)
    ev = A.risk_events(MEM, {"n_fire": 1, "run_at": "2026-10-18T08:30:02", "nearest_rule": "R1"}, {"retired": [], "run_at": "x"})
    kinds = [e["kind"] for e in ev]
    ids = [e["id"] for e in ev]
    check(any(i.startswith("sl:c1") for i in ids) and any(i.startswith("sl:t1") for i in ids), "停損事件")
    check(not any(i.startswith("sl:t3") for i in ids), "R>0 的 SL 出場不閃停損")
    check(kinds.count("halt") == 1, "熔斷全停 1 則, 重啟那行不算: %s" % kinds)
    check("kill" in kinds, "斬首規則觸發")
    mem2 = json.loads(json.dumps(MEM))
    mem2["risk_circuit_breaker"]["killswitch"] = True
    check(any(e["en"] == "KILLSWITCH ENGAGED" for e in A.risk_events(mem2)), "總開關")
    days = A.trades_per_day(A.tama_trades(MEM))
    d0 = days[0][0] / 1000.0
    check(abs(((d0 + 8 * 3600) % 86400)) < 1, "成交量柱用台北日切")


def t_office():
    tl, hand = A.office_timeline(OFFICE)
    check(len(tl) == 10, "事件數 %d" % len(tl))
    check([h["from"] + ">" + h["to"] for h in hand][:4] == ["shaper>sentinel", "sentinel>cost", "cost>lever", "lever>calib"],
          "交接順序照真實時間: %s" % hand)
    check(all("%%" not in e["text"] for e in tl), "%% 轉 %")
    now = A.parse_local_ts("2026-10-19 09:00")
    ags = A.build_agents(OFFICE, False, now, MEM, {"b1": {"live_enabled": True, "max_positions": 5}}, None, None)
    by = {a["key"]: a for a in ags}
    check(len(ags) == 8 and by["tama"]["name"] == "TAMA 交易員", "8 張卡")
    check(by["judge"]["status"] == "alert" and by["judge"]["status_text"] == "出錯", "bad -> 紅燈出錯")
    check(by["calib"]["status"] == "alert", "warn -> 紅燈警報")
    check(by["shaper"]["status"] == "ok", "ok -> 綠燈")
    check(by["sentinel"]["bar"]["label"] == "資料新鮮 2/3 市場", "資料新鮮度: %s" % by["sentinel"]["bar"])
    check(by["cost"]["metric"]["value"] == 1 and by["cost"]["metric"]["unit"] == "/2", "可交易 1/2")
    check(by["lever"]["monitor"]["values"][0] == 35, "20x 爆倉率中位(校正值優先): %s" % by["lever"]["monitor"])
    check(by["tama"]["metric"]["value"] == 1 and by["tama"]["metric"]["unit"] == "/5", "TAMA 持倉/上限")
    check(by["tama"]["status"] == "busy", "持倉中 = 黃燈處理中")
    ags = A.build_agents(OFFICE, True, now, MEM, None, None, None)
    check(all(a["status"] == "busy" for a in ags[:7]), "有鎖 = 全桌處理中")
    ags = A.build_agents(None, False, now, None, None, None, "沒有 GITHUB_TOKEN")
    check(all(a["status"] == "na" for a in ags) and ags[-1]["action"] == "沒有 GITHUB_TOKEN", "讀不到 -> N/A 不補值")
    stale = A.build_agents(OFFICE, False, now + 30 * 3600, MEM, None, None, None)
    check(stale[0]["status_text"] == "超過 26 小時沒跑班", "漏班 -> 紅燈")
    old = A.build_agents(OFFICE, False, A.parse_ts("2026-10-19T04:00:00+00:00"), MEM, None, None, None)
    check(old[-1]["status"] == "alert" and "沒心跳" in old[-1]["status_text"], "D 主迴圈 59 分鐘沒心跳 -> 紅燈")


def t_symbol_and_kline():
    now = A.parse_ts("2026-10-19T04:00:00+00:00")
    tr = A.tama_trades(MEM)
    vs = OFFICE["judge"]["verdicts"]
    s, why, c = A.pick_symbol(None, [{"symbol": "ETH-USDT"}], A.tama_open(MEM), tr, vs, now)
    check(s == "ETH-USDT" and why == "BingX 持倉", "BingX 持倉優先")
    s, why, c = A.pick_symbol(None, None, [], [], vs, now)
    check(s == "NCX-USDT", "判官最高信心(只挑 BingX 可交易的 crypto/nc): %s" % s)
    s, why, c = A.pick_symbol("SOL-USDT", None, [], [], [], now)
    check(s == "SOL-USDT" and why.startswith("手動釘選"), "釘選")
    s, why, c = A.pick_symbol(None, None, [], [], [], now)
    check(s == "BTC-USDT", "沒東西 -> BTC")
    rows = [{"time": 1000 * 3600 * (i + 1), "open": 100 + i, "high": 102 + i, "low": 99 + i, "close": 101 + i, "volume": 5}
            for i in range(60)]
    bars = A._bars_from_rows(list(reversed(rows)) + [rows[0]])
    check(len(bars) == 60 and bars[0][0] < bars[-1][0], "K 線排序去重")
    a = A.kline_analysis(bars)
    check(a["last"] == 160 and a["above_ema20"] is True and a["ema20_slope5"] > 0, "上升段: 價在 EMA20 上")
    check(math.isclose(a["atr"], 3.0), "ATR: %s" % a["atr"])
    check(A.kline_analysis([]) is None, "空 K 線 -> None")


def t_params():
    cfg = {"_meta": {"updated": "2026-10-10T15:56:21+08:00", "by": "x"},
           "b1": {"live_enabled": True, "risk_pct": 0.0025, "max_positions": 5},
           "global": {"killswitch": False}, "auto_strategies": [{"id": "AUTO_A", "live_enabled": True, "state": "probe"}]}
    p = A.build_params(cfg, time.time() - 60, {"gh_sha": "abc", "applied_sha": "abc"}, time.time())
    check(p["applied"] is True and p["auto"] == {"n": 1, "live": 1, "states": p["auto"]["states"]}, "已套用 / AUTO 計數")
    b1 = [g for g in p["groups"] if g["key"] == "b1"][0]
    check({r["k"]: r["v"] for r in b1["rows"]}["b1.atr_r_thr"] is None, "缺的參數 = None(頁面 N/A)")
    check(A.build_params(None, None, None, time.time()) is None, "沒檔 -> None")


def t_hub_integration():
    """用暫存資料夾當主基地: 只放辦公室檔與設定檔, 其他全缺 -> 應該起得來且缺的標 N/A。"""
    d = tempfile.mkdtemp(prefix="agent_office_test_")
    os.makedirs(os.path.join(d, "research", "i1017", "office"))
    with open(os.path.join(d, "research", "i1017", "office", "state.json"), "w", encoding="utf-8") as f:
        json.dump(OFFICE, f, ensure_ascii=False)
    with open(os.path.join(d, "tama_config_live_snapshot.json"), "w", encoding="utf-8") as f:
        json.dump({"b1": {"live_enabled": True, "max_positions": 5}}, f)
    memp = os.path.join(d, "mem.json")
    with open(memp, "w", encoding="utf-8") as f:
        json.dump(MEM, f, ensure_ascii=False)
    old_root, A.ROOT = A.ROOT, d
    os.environ["TAMA_MEMORY_FILE"] = memp
    try:
        h = A.Hub()
        for f_ in h.files.values():
            f_.poll()
        for n in h.net.values():
            n.run_once()
        st = h.state()
        json.dumps(st, allow_nan=False)
        check(st["sources"]["office"]["ok"] and not st["sources"]["killwatch"]["ok"], "有的讀到、缺的標錯")
        check(st["sources"]["balance"]["err"] and st["equity"]["balance"]["equity"] == 1012.5, "BingX 缺 -> 退 D 記憶最後權益")
        check(st["kline"] is None and st["kline_sym"] == "TRX-USDT", "K 線缺 -> None; 標的 = TAMA 持倉")
        check(len(st["office"]["handoffs"]) >= 4 and st["params"] is not None, "辦公室 + 參數")
        check(not os.path.exists(os.path.join(d, "kline_store")), "不在主基地建資料夾(只讀)")
    finally:
        A.ROOT = old_root
        os.environ.pop("TAMA_MEMORY_FILE", None)


if __name__ == "__main__":
    for fn in (t_parsers, t_tama, t_office, t_symbol_and_kline, t_params, t_hub_integration):
        fn()
    print("agent office 自測: %s" % ("全部通過" if not FAILS else "%d 項失敗" % len(FAILS)))
    sys.exit(1 if FAILS else 0)
