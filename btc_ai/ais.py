"""The three AIs: strategy, market, timeframe, parameter grid for weekly self-learning, and copy for the site.

Each AI trades several coins (capital split equally) and re-selects its parameters for each coin once a
week from its grid, using the trailing 365 days
(score = profit - 0.5 x max drawdown), and the new parameters apply from that moment on.
"""

from __future__ import annotations

import itertools

from .sim import meanrev, momentum, trend

AIS = [
    {
        "id": "momo", "name": "Momo", "emoji": "🚀", "color": "#f7931a",
        "title": "日線動能", "market": "spot", "tf": 1440, "warmup_days": 160,
        "desc": "看日線：過去一段時間是漲的、而且價格在均線之上才持有比特幣；趨勢轉弱就出場。只做多（現貨）。",
        "fn": "momentum", "coins": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"],
        "grid": [dict(lookback=lb, ma=m, atr_k=k) for lb, m, k in itertools.product([20, 60, 120], [50, 100], [3.0, 5.0])],
        "default": dict(lookback=60, ma=50, atr_k=5.0),
    },
    {
        "id": "trendy", "name": "Trendy", "emoji": "📈", "color": "#22d3ee",
        "title": "4 小時趨勢突破", "market": "spot", "tf": 240, "warmup_days": 45,
        "desc": "看 4 小時 K：突破前 N 根高點、且 EMA50 在 EMA200 之上時買進，用 ATR 移動停損抱住波段。只做多（現貨）。",
        "fn": "trend", "coins": ["BTCUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"],
        "grid": [dict(lookback=lb, atr_k=k, fast=50, slow=200) for lb, k in itertools.product([10, 20, 40], [2.5, 3.5, 5.0])],
        "default": dict(lookback=20, atr_k=3.5, fast=50, slow=200),
    },
    {
        "id": "bounce", "name": "Bounce", "emoji": "🎯", "color": "#a78bfa",
        "title": "超跌反彈", "market": "perp", "tf": 240, "warmup_days": 80,
        "desc": "看 4 小時 K：長期向上時，短線急跌（RSI-2 很低）就接；長期向下時，短線急漲就空。彈回均線就出場。永續合約，多空都做。",
        "fn": "meanrev", "coins": ["BTCUSDT", "SOLUSDT"],
        "grid": [dict(trend=t, rsi_lo=r, sl_atr=s, max_bars=mb)
                 for t, r, s, mb in itertools.product([200, 400], [5, 10, 20], [2.0, 3.0], [6, 12])],
        "default": dict(trend=200, rsi_lo=5, sl_atr=2.0, max_bars=12),
    },
]

BY_ID = {a["id"]: a for a in AIS}
NOTIONAL = 10_000.0  # USDT of capital per AI (paper), split equally across the AI's coins.
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"]
COIN_NAMES = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "SOLUSDT": "SOL", "BNBUSDT": "BNB", "XRPUSDT": "XRP", "DOGEUSDT": "DOGE"}


def notional(ai: dict) -> float:
  return NOTIONAL / len(ai["coins"])


def run(ai: dict, bars: list, params: dict, funding: dict | None = None, size: float | None = None):
  """Runs the AI's strategy over bars of its timeframe; returns the Result (trade log + open position)."""
  fn = {"momentum": momentum, "trend": trend, "meanrev": meanrev}[ai["fn"]]
  size = size or notional(ai)
  if ai["fn"] == "momentum":
    return fn(bars, ai["market"], params, notional=size, funding=funding)
  return fn(bars, ai["market"], params, notional=size, funding=funding, minutes=ai["tf"])
