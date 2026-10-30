"""Strategy research: parameter grids, yearly breakdown and rolling walk-forward (train 180 d -> test 30 d).

Run: python3 -m btc_ai.research  (needs data/bars.pkl from the cache step)
"""

from __future__ import annotations

import itertools
import pickle
import sys
from datetime import timedelta
from pathlib import Path

from .sim import meanrev, momentum, pullback, resample, session, split_by_time, trend

DATA = Path(__file__).resolve().parents[1] / "data" / "bars.pkl"
WARMUP = {"momentum": 150, "trend4h": 40, "meanrev": 80}


def grids():
  return {
      "trend4h": [dict(lookback=lb, atr_k=k, fast=50, slow=200) for lb, k in itertools.product([10, 20, 40], [2.5, 3.5, 5.0])],
      "trend1h": [dict(lookback=lb, atr_k=k, fast=50, slow=200) for lb, k in itertools.product([24, 48, 96], [3.0, 4.5, 6.0])],
      "session_us": [dict(anchor="us", range_min=r, window_min=w, tp_mult=t, sl_frac=1.0)
                     for r, w, t in itertools.product([30, 60], [120, 360], [1.5, 2.5, 4.0])],
      "session_utc0": [dict(anchor="utc0", range_min=r, window_min=w, tp_mult=t, sl_frac=1.0)
                       for r, w, t in itertools.product([60, 240], [240, 720], [1.5, 2.5, 4.0])],
      "momentum": [dict(lookback=lb, ma=m, atr_k=k) for lb, m, k in itertools.product([20, 60, 120], [50, 100], [3.0, 5.0])],
      "meanrev": [dict(trend=t, rsi_lo=r, sl_atr=s, max_bars=mb) for t, r, s, mb in itertools.product([200, 400], [5, 10, 20], [2.0, 3.0], [6, 12])],
      "pullback": [dict(pull_atr=pa, sl_atr=s, tp_mult=t) for pa, s, t in itertools.product([0.2, 0.5], [1.5, 2.5], [2.0, 3.0, 4.0])],
  }


def run(name, p, market, d, bars=None):
  f = d["funding"]
  if name == "trend4h":
    return trend(bars or d["b240"], market, p, funding=f, minutes=240)
  if name == "trend1h":
    return trend(bars or d["b60"], market, p, funding=f, minutes=60)
  if name == "momentum":
    return momentum(bars or d["b1d"], market, p, funding=f)
  if name == "meanrev":
    return meanrev(bars or d["b240"], market, p, funding=f)
  if name.startswith("session"):
    return session(bars or d["b15"], market, p, funding=f)
  return pullback(bars or d["b15"], market, p, funding=f)


def tf_bars(name, d):
  return {"trend4h": d["b240"], "meanrev": d["b240"], "trend1h": d["b60"], "momentum": d["b1d"]}.get(name, d["b15"])


def walk_forward(name, market, d, train=180, test=30):
  bars = tf_bars(name, d)
  start, end = bars[0].t + timedelta(days=WARMUP.get(name, 30)), bars[-1].t
  t0, total, wins, windows, picks = start, 0.0, 0, 0, []
  while t0 + timedelta(days=train + test) <= end:
    wu = timedelta(days=WARMUP.get(name, 30))  # Indicator warm-up (daily strategies need ~150 days).
    tr = split_by_time(bars, t0 - wu, t0 + timedelta(days=train))
    te = split_by_time(bars, t0 + timedelta(days=train) - wu, t0 + timedelta(days=train + test))
    best = max(grids()[name], key=lambda p: run(name, p, market, d, tr).pnl)
    r = run(name, best, market, d, te)
    cut = t0 + timedelta(days=train)
    pnl = sum(x[5] for x in r.log if x[0] >= cut)
    total += pnl
    wins += pnl > 0
    windows += 1
    picks.append(best)
    t0 += timedelta(days=test)
  return total, wins, windows, picks[-1] if picks else None


def main():
  d = pickle.load(open(DATA, "rb"))
  d["b1d"] = resample(d["b60"], 1440)
  only = sys.argv[1:] or list(grids())
  for name in only:
    for market in ("spot", "perp"):
      res = []
      for p in grids()[name]:
        r = run(name, p, market, d)
        years = {}
        for e, _x, _s, _ep, _xp, net, _w in r.log:
          years[e.year] = years.get(e.year, 0) + net
        res.append((r.pnl, p, r, years))
      res.sort(key=lambda x: -x[0])
      pos = sum(1 for x in res if x[0] > 0)
      best = res[0]
      print(f"\n[{name} / {market}] 參數組合 {len(res)} 個，賺錢 {pos} 個")
      for pnl, p, r, years in res[:3]:
        ys = " ".join(f"{y}:{v:+,.0f}" for y, v in sorted(years.items()))
        print(f"  {pnl:+9,.0f} 筆{r.trades:4} 勝率{r.wins / max(1, r.trades) * 100:3.0f}% 回撤{r.maxdd:8,.0f} | {ys} | {p}")
      tot, w, n, last = walk_forward(name, market, d)
      print(f"  滾動考試（練習180天→考試30天）：{w}/{n} 段賺錢，考試期合計 {tot:+,.0f}；最新挑選 {last}")


if __name__ == "__main__":
  main()
