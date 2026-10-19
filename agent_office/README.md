# APEX Agent Office v3(霓虹像素版)

形態辦公室 7 桌 + D·TAMA 的即時儀表板。單頁、深黑底、洋紅霓虹框、JetBrains Mono,1920×1080 一屏看完。

## 在哪台機器、哪個資料夾跑

- 機器:家裡那台跑 APEX 的 Windows 電腦(主基地 `C:\claude-assistant`)。
- 資料夾:`C:\claude-assistant\agent_office\`(四個檔放在一起)。
- 啟動:雙擊 `start_agent_office.bat`,瀏覽器會自己開 <http://127.0.0.1:8777/>。黑色視窗開著才有資料,關掉就停。
- 自測:`python -X utf8 agent_office\test_agent_office.py`(在 `C:\claude-assistant` 底下跑,不打網路)。

## 四層畫面與資料來源

| 層 | 內容 | 真實來源 | 更新 |
|---|---|---|---|
| 1 卡片 | 7 桌 + TAMA:狀態燈、最新動作、關鍵數字、進度條 | `research/i1017/office/state.json`、`shape_office.lock`、tama_memory.json | 2 秒 |
| 2 辦公室 | 等角像素辦公室,桌上螢幕是該代理的迷你圖 | 同上;重播用 state.json 每個事件的真實時間戳 | 2 秒 |
| 3 資金曲線 | 權益折線 + 每日結算筆數柱 + 即時餘額 | tama_memory.json `data.equity_history`;餘額 BingX `/user/balance` | 60 / 15 秒 |
| 4 左 | 當前標的 1h K 線、EMA20、ATR、持倉線、判官止損 | BingX 公開 K 線,失敗退本機 `kline_store/1h` | 30 秒 |
| 4 右 | 即時參數(唯讀) | `tama_config_live_snapshot.json` + `config_readback_result.json` | 檔案變就重讀 |

燈號:綠=正常、黃=處理中(正在跑班或持倉中)、紅=警報(出錯、警報、漏班、D 主迴圈 15 分鐘沒心跳、總開關)、灰=N/A。

## 會騙你的情況

- 形態辦公室一天只跑一班(07:40,約 25 秒)。其他時間小人走動是「重播」,畫面左上會寫「▶ 重播」。
- 拿不到的欄位一律顯示 N/A,滑過頂列的燈看得到原因。不會補假值。
- 餘額先用 BingX 即時;BingX 讀不到時改用 D 記憶最後一筆權益,來源列會寫明。
- 風控閃燈只閃「開頁之後新出現」的事件。開頁前的事件在資金曲線右側清單,點一下可重播閃燈。
- 只綁 127.0.0.1,餘額只在本機看得到。BingX 只走 `apex_common.bingx_get` 的唯讀白名單,不會下單。
