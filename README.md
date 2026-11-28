# ₿ Crypto AI Trader

三位會**自主學習**的加密貨幣 AI（BTC、ETH、SOL、BNB、XRP、DOGE），用幣安即時行情做**模擬交易**；最終目標是連接幣安帳戶。

儀表板：https://ntcloudy.github.io/btc-ai-trader/

## 三位 AI

| AI | 策略 | 市場 | 時間框架 | 負責的幣 |
| :--- | :--- | :--- | :--- | :--- |
| 🚀 Momo | 日線動能：過去一段時間上漲、且價格在均線之上才持有 | 現貨，只做多 | 日線 | BTC ETH SOL BNB XRP DOGE |
| 📈 Trendy | 4 小時趨勢突破：突破前 N 根高點且 EMA50 > EMA200，ATR 移動停損 | 現貨，只做多 | 4 小時 | BTC SOL BNB XRP DOGE |
| 🎯 Bounce | 超跌反彈：長期向上時接短線急跌、長期向下時空短線急漲 | 永續合約，多空 | 4 小時 | BTC SOL |

每位 AI 總資金 10,000 USDT（模擬），平均分給它負責的幣；「買進持有」基準是 10,000 USDT 平均買進 6 個幣。
各幣的回測見 `python3 -m btc_ai.coins_research ETHUSDT SOLUSDT XRPUSDT BNBUSDT DOGEUSDT`。

**自主學習**：每週六 10:05（台灣）每位 AI 在每個幣上用最近一年的資料，從參數組合裡重新挑選（分數＝獲利 − 0.5 × 最大回撤），比現在好才換，一週最多換一次。

## 研究結果（2023/10～2026/10，每筆 10,000 USDT，已扣手續費與資金費率）

- 日線動能：12 組參數全部賺錢、四個年度都賺（最佳 +8,931，最大回撤 2,638）。
- 4 小時趨勢：9 組有 6 組賺錢（最佳 +2,880）。
- 超跌反彈（合約）：24 組有 6 組賺錢（最佳 +1,579）。
- 開盤區間突破、回測均線等短線策略：幾乎全部被手續費吃掉，已淘汰。
- 比較基準「買進持有」：+199%，但期間最大跌幅 −53%。

## 執行

```bash
python3 -m btc_ai run        # 模擬交易引擎（每 30 秒更新，每分鐘發布儀表板）
python3 -m btc_ai status     # 目前狀態
python3 -m btc_ai weekly     # 每週自主學習（cron：週六 10:00 台灣）
python3 -m btc_ai.research   # 重新跑策略研究
```

歷史資料：`data.binance.vision` 公開檔；即時：幣安公開 API（不需金鑰）。

## Email 通知（只限 Trendy 的 BTC）

Trendy 在 BTC 上的進場（方向、價格、停損、停利或移動停損）與出場（進場價→出場價、損益、原因）會寄 Email，其他 AI 和幣不寄，也不佔 LINE 額度。
收件人與寄信帳號放在 repo 外：`~/.config/tw-futures-ai/mail.json`（權限 600，不要 commit）：

```json
{"to": ["收件人@example.com"], "smtp_user": "寄件 Gmail", "app_password": "Gmail 應用程式密碼（16 碼）",
 "host": "smtp.gmail.com", "port": 465}
```

沒填寄信帳號時只會在儀表板事件記一筆「尚未設定寄信帳號」，不影響交易；那段期間的通知視為已處理，之後補上帳號也不會補寄舊信。LINE 只推每日報告與每週摘要，不推個別進出場（使用者 11/17：LINE 進場提醒只留期貨 Omega）。寄信在背景執行、失敗重試 3 次，每則通知只寄一次。
