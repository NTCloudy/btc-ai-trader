"""Multi-coin research: the three AIs' strategies (and grids) on other coins, spot and perp.

Run: python3 -m btc_ai.coins_research ETHUSDT SOLUSDT ...   (data in data/raw/<SYMBOL>/)
"""

from __future__ import annotations

import pickle
import sys
from pathlib import Path

from .ais import AIS
from .sim import RAW, load_1m, load_funding, meanrev, momentum, resample, trend

ROOT = Path(__file__).resolve().parents[1]


def bars_for(symbol: str) -> dict:
  cache = ROOT / "data" / f"bars_{symbol}.pkl"
  if cache.exists():
    return pickle.load(open(cache, "rb"))
  b1 = load_1m(RAW / symbol, symbol)
  b60 = resample(b1, 60)
  d = {"b60": b60, "b240": resample(b1, 240), "b1d": resample(b60, 1440), "funding": load_funding(RAW / symbol, symbol)}
  pickle.dump(d, open(cache, "wb"))
  return d


def run(fn: str, d: dict, market: str, p: dict):
  f = d["funding"]
  if fn == "momentum":
    return momentum(d["b1d"], market, p, funding=f)
  if fn == "trend":
    return trend(d["b240"], market, p, funding=f, minutes=240)
  return meanrev(d["b240"], market, p, funding=f, minutes=240)


def main():
  for sym in sys.argv[1:]:
    d = bars_for(sym)
    b = d["b240"]
    peak, mdd = 0, 0
    for x in b:
      peak = max(peak, x.c)
      mdd = max(mdd, (peak - x.c) / peak)
    print(f"\n===== {sym}  {b[0].t:%Y-%m} {b[0].c:,.4g} → {b[-1].t:%Y-%m} {b[-1].c:,.4g}｜買進持有 {b[-1].c / b[0].c - 1:+.0%}｜最大跌幅 {mdd:.0%}")
    for ai in AIS:
      for market in ("spot", "perp"):
        res = []
        for p in ai["grid"]:
          r = run(ai["fn"], d, market, p)
          years = {}
          for e, *_rest in r.log:
            years[e.year] = years.get(e.year, 0) + _rest[4]
          res.append((r.pnl, p, r, years))
        res.sort(key=lambda x: -x[0])
        pos = sum(1 for x in res if x[0] > 0)
        pnl, p, r, years = res[0]
        ys = " ".join(f"{y}:{v:+,.0f}" for y, v in sorted(years.items()))
        med = sorted(x[0] for x in res)[len(res) // 2]
        print(f"  {ai['name']:6} {market:4} 賺錢組合 {pos:2}/{len(res):2}｜中位數 {med:+8,.0f}｜最佳 {pnl:+8,.0f}（{r.trades} 筆，回撤 {r.maxdd:,.0f}）｜{ys}")


if __name__ == "__main__":
  main()
