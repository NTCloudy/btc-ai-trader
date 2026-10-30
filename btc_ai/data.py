"""Market data: 1-hour BTCUSDT perpetual bars (history cache + live Binance REST), price, funding rates.

History: data/bars.pkl (built from data.binance.vision archives) + data/h1.csv (bars appended live).
Live: fapi.binance.com public endpoints (no API key). The in-progress hour is kept separately so the
AIs see a partial last bar (stops react within the hour; entries still use closed bars only).
"""

from __future__ import annotations

import csv
import json
import pickle
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .sim import Bar, resample

ROOT = Path(__file__).resolve().parents[1]
H1 = ROOT / "data" / "h1.csv"
CACHE = ROOT / "data" / "bars.pkl"
FUND = ROOT / "data" / "funding.json"
FAPI = "https://fapi.binance.com/fapi/v1"
UTC = timezone.utc


def _get(url: str, timeout: int = 10):
  req = urllib.request.Request(url, headers={"User-Agent": "btc-ai-trader"})
  with urllib.request.urlopen(req, timeout=timeout) as r:
    return json.load(r)


class Market:

  def __init__(self):
    self.h1: list[Bar] = []
    self.live: Bar | None = None  # in-progress hour
    self.funding: dict[int, float] = {}
    self.price = None
    self.mark = None
    self.next_funding = None
    self.last_rate = None
    self.updated = None
    self._load()

  def _load(self):
    rows: dict[datetime, Bar] = {}
    if CACHE.exists():
      d = pickle.load(open(CACHE, "rb"))
      for b in d["b60"]:
        rows[b.t] = Bar(b.t, b.o, b.h, b.l, b.c, b.v)
      self.funding.update(d.get("funding", {}))
    if H1.exists():
      for r in csv.reader(open(H1, encoding="utf-8")):
        if r and r[0][0].isdigit():
          t = datetime.fromisoformat(r[0])
          rows[t] = Bar(t, *map(float, r[1:6]))
    if FUND.exists():
      self.funding.update({int(k): v for k, v in json.load(open(FUND)).items()})
    self.h1 = [rows[k] for k in sorted(rows)]
    if self.h1 and self.h1[-1].t >= datetime.now(UTC).replace(minute=0, second=0, microsecond=0):
      self.h1.pop()  # Never keep an unfinished hour in the closed list.

  def _append(self, bars: list[Bar]):
    new = [b for b in bars if not self.h1 or b.t > self.h1[-1].t]
    if not new:
      return
    self.h1.extend(new)
    with open(H1, "a", encoding="utf-8", newline="") as f:
      w = csv.writer(f)
      for b in new:
        w.writerow([b.t.isoformat(), b.o, b.h, b.l, b.c, b.v])

  def update(self) -> None:
    """Fills the gap since the last closed hour, refreshes the in-progress hour, price, funding."""
    now = datetime.now(UTC)
    start = (self.h1[-1].t + timedelta(hours=1)) if self.h1 else now - timedelta(days=400)
    while True:
      rows = _get(f"{FAPI}/klines?symbol=BTCUSDT&interval=1h&limit=1000&startTime={int(start.timestamp() * 1000)}")
      bars = [Bar(datetime.fromtimestamp(r[0] / 1000, UTC), float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                  float(r[5])) for r in rows]
      closed = [b for b in bars if b.t + timedelta(hours=1) <= now]
      self._append(closed)
      self.live = bars[-1] if bars and bars[-1].t + timedelta(hours=1) > now else None
      if len(rows) < 1000:
        break
      start = bars[-1].t + timedelta(hours=1)
    pi = _get(f"{FAPI}/premiumIndex?symbol=BTCUSDT")
    self.mark, self.last_rate = float(pi["markPrice"]), float(pi["lastFundingRate"])
    self.next_funding = datetime.fromtimestamp(pi["nextFundingTime"] / 1000, UTC)
    self.price = self.live.c if self.live else self.mark
    last_f = max(self.funding) if self.funding else int((now - timedelta(days=30)).timestamp() * 1000)
    fr = _get(f"{FAPI}/fundingRate?symbol=BTCUSDT&limit=1000&startTime={last_f + 1}")
    if fr:
      for r in fr:
        self.funding[int(r["fundingTime"]) // 3_600_000 * 3_600_000] = float(r["fundingRate"])
      json.dump({str(k): v for k, v in self.funding.items() if k > (now - timedelta(days=400)).timestamp() * 1000},
                open(FUND, "w"))
    self.updated = now

  def bars(self, minutes: int, include_live: bool = True) -> list[Bar]:
    """Bars of the given timeframe built from 1-hour bars; the last one may be partial (in progress)."""
    src = self.h1 + ([self.live] if include_live and self.live else [])
    if minutes == 60:
      return [Bar(b.t, b.o, b.h, b.l, b.c, b.v) for b in src]
    return resample(src, minutes)

  def change_24h(self) -> float | None:
    if not self.price or len(self.h1) < 24:
      return None
    return self.price / self.h1[-24].o - 1
