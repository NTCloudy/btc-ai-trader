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
        "title": "日線動能", "market": "perp", "tf": 1440, "warmup_days": 150,
        "lookback_days": 270, "hyst_pct": 0.20,
        "desc": "看日線：過去 N 天動能、價格與均線斜率同向時順勢進場（多空雙向），搭配 ATR 停利與移動停損保護波段。",
        "fn": "momentum", "coins": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"],
        "grid": [dict(lookback=lb, ma=m, atr_k=k, tp_atr=tp)
                 for lb, m, k, tp in itertools.product([20, 40, 60], [30, 50], [2.5, 3.0, 4.0], [3.5, 5.0])],
        "default": dict(lookback=40, ma=50, atr_k=3.0, tp_atr=5.0),
    },
    {
        "id": "trendy", "name": "Trendy", "emoji": "📈", "color": "#22d3ee",
        "title": "4 小時趨勢突破", "market": "perp", "tf": 240, "warmup_days": 150,
        "lookback_days": 270, "hyst_pct": 0.20,
        "desc": "看 4 小時 K：收盤突破前 N 根極值、快慢均線斜率同向且突破動能 >= 1.5 ATR 時進場，跌破快線或達標即出場（永續合約多空）。",
        "fn": "trend", "coins": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        "grid": [dict(lookback=lb, atr_k=k, fast=f, slow=s, tp_atr=tp, req_slow_slope=True, min_roc_atr=1.5)
                 for lb, k, f, s, tp in itertools.product([35, 50, 65], [3.0, 4.0], [40, 60], [160, 200], [0, 5.0])],
        "default": dict(lookback=50, atr_k=3.0, fast=40, slow=200, tp_atr=5.0, req_slow_slope=True, min_roc_atr=1.5),
    },
    {
        "id": "bounce", "name": "Bounce", "emoji": "🎯", "color": "#a78bfa",
        "title": "超跌反彈", "market": "perp", "tf": 240, "warmup_days": 150,
        "lookback_days": 180, "hyst_pct": 0.20,
        "desc": "看 4 小時 K：長中期均線同向上揚時，短線急跌（RSI-2 極低）就接；同向下彎時，短線急漲就空。彈回 EMA5 即出場（永續合約多空）。",
        "fn": "meanrev", "coins": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        "grid": [dict(trend=t, rsi_lo=r, sl_atr=s, max_bars=mb)
                 for t, r, s, mb in itertools.product([180, 200, 240], [8, 10, 12], [2.5, 3.5], [8, 12])],
        "default": dict(trend=200, rsi_lo=10, sl_atr=2.5, max_bars=12),
    },
]

BY_ID = {a["id"]: a for a in AIS}
NOTIONAL = 10_000.0  # USDT of capital per AI (paper), split equally across the AI's coins.
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"]
COIN_NAMES = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "SOLUSDT": "SOL", "BNBUSDT": "BNB", "XRPUSDT": "XRP", "DOGEUSDT": "DOGE"}


def notional(ai: dict) -> float:
  return NOTIONAL / len(ai["coins"])


def run(ai: dict, bars: list, params: dict, funding: dict | None = None, size: float | None = None,
        trade_from=None):
  """Runs the AI's strategy over bars of its timeframe; returns the Result (trade log + open position)."""
  fn = {"momentum": momentum, "trend": trend, "meanrev": meanrev}[ai["fn"]]
  size = size or notional(ai)
  if ai["fn"] == "momentum":
    return fn(bars, ai["market"], params, notional=size, funding=funding, trade_from=trade_from)
  return fn(bars, ai["market"], params, notional=size, funding=funding, minutes=ai["tf"], trade_from=trade_from)
