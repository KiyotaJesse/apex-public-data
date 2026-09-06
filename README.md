# APEX 蟻穴公開資料

- 頁面: https://kiyotajesse.github.io/apex-public-data/apex_anthill.html
- 資料: https://kiyotajesse.github.io/apex-public-data/apex_nest_data.json (每日 08:10 由 queen_weight.py 產出; 頁面與 JSON 同一份)
- 監督台: supervision_state.json

## apex_nest_data.json 主要欄位
| 欄位 | 內容 |
|---|---|
| idx / eq / asOf | 蟻后指數(8/28=100, 出入金不算) / 權益 / 量測日 |
| health | 巢健康 0-100 + 扣分明細 why + 昨日 prev |
| proposals | 明日一鏟: 巢自己排的痛點(lv 越小越急) |
| evolution / evolution_log | 今日與近 60 天的結構變化(誕生/立碑/新疤/金塊/入學/除名/擴建) |
| tiers | 巢室級距(工蟻房/育嬰房/墳場/學院/礦坑/疤痕), 頁面照此長縮 |
| groups / strategies / info | 蟻群名冊(來自監督台)、策略蟻、每隻蟻的職掌/班表/SLO/台詞 |
| sick | 監督台紅燈的蟻 |
| alerts | 危(分級蟻)的 P0-P3 事件 |
| pantry | 糧倉五格(K 線歸檔/盤口熟成/礦機口糧/API) |
| mine | 礦坑: 1h/4h 漏斗、金塊(候選)與前瞻進度、血緣摘要、本週額度 |
| academy | 影子學院: 學員/記過/月考/預估命運, graves=除名 |
| scars | 巢牆疤痕=事故簿 |
| journal / tl / hist / health_hist | 飼主日誌(90 天)、時間旅行(每日蟻后+巢況快照)、體重與健康歷史 |
| weather | 信心度引擎: regime/恐貪/多空信心/超大底 |
| mailbox / decisions | 今日一卡投遞狀態 / 等 Jesse 回的決定 |
| archive | 決策知識庫六張表筆數 |

規則說明見頁面「巢怎麼長」。所有數字都附來源檔與時間; 讀不到的欄位會缺席或標 stale, 不會假裝有值。
