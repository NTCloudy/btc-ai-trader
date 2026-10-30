"""Research backtester for BTCUSDT (Binance). Pure Python, bar-based, conservative.

Data: Binance public 1-minute klines (data.binance.vision), USDT-M perpetual. Spot prices are virtually
identical, so the same bars are used for the spot (long-only) tests with spot fees.

Fills / costs (per side, on notional):
  spot : 0.10% market, 0.10% limit (no BNB discount assumed)
  perp : 0.05% taker (market / stops), 0.02% maker (limit entries); funding every 8 h on open notional
Stops are checked on each bar with the stop assumed to fill first when stop and target are both touched.
Each trade uses a fixed notional (USDT) so results are comparable across strategies.
"""

from __future__ import annotations

import csv
import io
import math
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
RAW = Path(__file__).resolve().parents[1] / "data" / "raw"

FEES = {"spot": {"taker": 0.0010, "maker": 0.0010}, "perp": {"taker": 0.0005, "maker": 0.0002}}


@dataclass
class Bar:
  t: datetime  # bar open time (UTC)
  o: float
  h: float
  l: float
  c: float
  v: float


def load_1m(raw: Path = RAW) -> list[Bar]:
  rows: dict[int, Bar] = {}
  for f in sorted(raw.glob("fut_BTCUSDT-1m-*.zip")):
    with zipfile.ZipFile(f) as z:
      for name in z.namelist():
        for r in csv.reader(io.TextIOWrapper(z.open(name), encoding="utf-8")):
          if not r or not r[0].isdigit():
            continue  # header
          ms = int(r[0])
          if ms > 1e14:  # microseconds in newer files
            ms //= 1000
          rows[ms] = Bar(datetime.fromtimestamp(ms / 1000, UTC), float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                         float(r[5]))
  return [rows[k] for k in sorted(rows)]


def load_funding(raw: Path = RAW) -> dict[int, float]:
  """funding time (ms, rounded to the hour) -> rate"""
  out = {}
  for f in sorted(raw.glob("BTCUSDT-fundingRate-*.zip")):
    with zipfile.ZipFile(f) as z:
      for name in z.namelist():
        for r in csv.reader(io.TextIOWrapper(z.open(name), encoding="utf-8")):
          if not r or not r[0].isdigit():
            continue
          ms = int(r[0]) // 3_600_000 * 3_600_000
          out[ms] = float(r[2])
  return out


def resample(bars: list[Bar], minutes: int) -> list[Bar]:
  out: list[Bar] = []
  cur = None
  for b in bars:
    epoch = int(b.t.timestamp()) // 60
    key = epoch - epoch % minutes
    if cur is None or cur[0] != key:
      if cur:
        out.append(cur[1])
      cur = (key, Bar(datetime.fromtimestamp(key * 60, UTC), b.o, b.h, b.l, b.c, b.v))
    else:
      x = cur[1]
      x.h, x.l, x.c, x.v = max(x.h, b.h), min(x.l, b.l), b.c, x.v + b.v
  if cur:
    out.append(cur[1])
  return out


def ema(values: list[float], n: int) -> list[float]:
  k, out, e = 2 / (n + 1), [], None
  for v in values:
    e = v if e is None else e + k * (v - e)
    out.append(e)
  return out


def atr(bars: list[Bar], n: int = 14) -> list[float]:
  out, a, prev = [], None, None
  for b in bars:
    tr = b.h - b.l if prev is None else max(b.h - b.l, abs(b.h - prev), abs(b.l - prev))
    a = tr if a is None else a + (tr - a) / n
    out.append(a)
    prev = b.c
  return out


def us_open_utc(d) -> datetime:
  """US cash open (09:30 New York) in UTC for date d: 13:30 in US DST, 14:30 otherwise."""
  y = d.year
  mar = datetime(y, 3, 8, tzinfo=UTC)
  dst_start = mar + timedelta(days=(6 - mar.weekday()) % 7)  # 2nd Sunday of March
  nov = datetime(y, 11, 1, tzinfo=UTC)
  dst_end = nov + timedelta(days=(6 - nov.weekday()) % 7)  # 1st Sunday of November
  dt = datetime(d.year, d.month, d.day, tzinfo=UTC)
  return dt.replace(hour=13, minute=30) if dst_start <= dt < dst_end else dt.replace(hour=14, minute=30)


@dataclass
class Result:
  pnl: float = 0.0
  trades: int = 0
  wins: int = 0
  maxdd: float = 0.0
  log: list = field(default_factory=list)
  open_pos: dict | None = None  # Position still open at the last bar (used by the live engine).


class Book:
  """Single-position book with fixed notional; tracks P&L, drawdown, fees and funding."""

  def __init__(self, market: str, notional: float, funding: dict[int, float] | None):
    self.fee = FEES[market]
    self.market, self.notional, self.funding = market, notional, funding or {}
    self.r = Result()
    self.peak = 0.0
    self.pos = None  # dict(side, entry, qty, stop, tp, trail, best, t, fee_in)

  def open(self, t, side, price, stop, tp=None, trail=None, maker=False):
    qty = self.notional / price
    fee_in = self.notional * (self.fee["maker"] if maker else self.fee["taker"])
    self.pos = dict(side=side, entry=price, qty=qty, stop=stop, tp=tp, trail=trail, best=price, t=t, fee_in=fee_in,
                    funding=0.0, last_fund=None)

  def close(self, t, price, why):
    p = self.pos
    gross = (price - p["entry"]) * p["qty"] * p["side"]
    net = gross - p["fee_in"] - price * p["qty"] * self.fee["taker"] - p["funding"]
    self.r.pnl += net
    self.r.trades += 1
    self.r.wins += net > 0
    self.peak = max(self.peak, self.r.pnl)
    self.r.maxdd = max(self.r.maxdd, self.peak - self.r.pnl)
    self.r.log.append((p["t"], t, p["side"], p["entry"], price, net, why))
    self.pos = None

  def mark(self, b: Bar, minutes: int):
    """Funding (perp) and bracket / trailing-stop checks on bar b. Returns True if the position closed."""
    p = self.pos
    if p is None:
      return False
    if self.market == "perp" and self.funding:
      start = int(b.t.timestamp() * 1000)
      for hms in range(start - start % 3_600_000, start + minutes * 60_000, 3_600_000):
        if hms >= start and hms in self.funding and p["last_fund"] != hms:
          p["funding"] += self.funding[hms] * p["qty"] * b.o * p["side"]  # longs pay positive rates
          p["last_fund"] = hms
    s = p["side"]
    if (s == 1 and b.l <= p["stop"]) or (s == -1 and b.h >= p["stop"]):
      px = min(p["stop"], b.o) if s == 1 else max(p["stop"], b.o)
      self.close(b.t, px, "stop")
      return True
    if p["tp"] is not None and ((s == 1 and b.h >= p["tp"]) or (s == -1 and b.l <= p["tp"])):
      self.close(b.t, p["tp"], "target")
      return True
    if p["trail"]:
      p["best"] = max(p["best"], b.h) if s == 1 else min(p["best"], b.l)
      ns = p["best"] - p["trail"] if s == 1 else p["best"] + p["trail"]
      if (s == 1 and ns > p["stop"]) or (s == -1 and ns < p["stop"]):
        p["stop"] = ns
    return False


# ----------------------------------------------------------------------------------------------
# Strategy 1: TREND (Omega-like) — Donchian breakout with EMA filter and ATR trailing stop.
def trend(bars: list[Bar], market: str, p: dict, notional=10_000, funding=None, minutes=240) -> Result:
  book = Book(market, notional, funding)
  closes = [b.c for b in bars]
  ef, es, a = ema(closes, p.get("fast", 50)), ema(closes, p.get("slow", 200)), atr(bars, 14)
  n = p["lookback"]
  for i in range(max(n, 210), len(bars)):
    b = bars[i]
    if book.pos and book.mark(b, minutes):
      continue
    if book.pos:
      continue
    hi = max(x.h for x in bars[i - n - 1:i - 1])  # Prior n bars, excluding the signal bar itself.
    lo = min(x.l for x in bars[i - n - 1:i - 1])
    prev = bars[i - 1]
    k = p["atr_k"] * a[i - 1]
    if prev.c > hi and ef[i - 1] > es[i - 1]:  # Signal on the closed bar, enter at this bar's open.
      book.open(b.t, 1, b.o, b.o - k, trail=k)
      book.mark(b, minutes)
    elif market == "perp" and p.get("short", True) and prev.c < lo and ef[i - 1] < es[i - 1]:
      book.open(b.t, -1, b.o, b.o + k, trail=k)
      book.mark(b, minutes)
  book.r.open_pos = book.pos
  return book.r


# Strategy 2: SESSION breakout (Nova-like) — opening range after an anchor (UTC 00:00 or US open).
def session(bars15: list[Bar], market: str, p: dict, notional=10_000, funding=None) -> Result:
  book = Book(market, notional, funding)
  rng_min, win_min = p["range_min"], p["window_min"]
  day_key, hi, lo, a_end, w_end, traded = None, None, None, None, None, 0
  for i in range(1, len(bars15)):
    b, prev = bars15[i], bars15[i - 1]
    if book.pos and book.mark(b, 15):
      continue
    anchor = us_open_utc(b.t) if p["anchor"] == "us" else b.t.replace(hour=0, minute=0, second=0, microsecond=0)
    if b.t < anchor:
      anchor -= timedelta(days=1)
      anchor = us_open_utc(anchor) if p["anchor"] == "us" else anchor
    if day_key != anchor:
      if book.pos and p.get("flat_daily", True):
        book.close(b.t, b.o, "day end")
      day_key, hi, lo, traded = anchor, None, None, 0
      a_end, w_end = anchor + timedelta(minutes=rng_min), anchor + timedelta(minutes=rng_min + win_min)
    if b.t < a_end:
      hi = b.h if hi is None else max(hi, b.h)
      lo = b.l if lo is None else min(lo, b.l)
      continue
    if book.pos or hi is None or b.t >= w_end or traded >= p.get("max_trades", 3):
      continue
    width = hi - lo
    stop_d = min(max(width * p.get("sl_frac", 1.0), prev.c * 0.003), prev.c * 0.02)
    buf = prev.c * 0.0005
    if prev.c > hi + buf and bars15[i - 2].c <= hi + buf:
      book.open(b.t, 1, b.o, b.o - stop_d, tp=b.o + stop_d * p["tp_mult"])
      traded += 1
      book.mark(b, 15)
    elif market == "perp" and prev.c < lo - buf and bars15[i - 2].c >= lo - buf:
      book.open(b.t, -1, b.o, b.o + stop_d, tp=b.o - stop_d * p["tp_mult"])
      traded += 1
      book.mark(b, 15)
  book.r.open_pos = book.pos
  return book.r


# Strategy 3: PULLBACK in trend (Sigma-like) — 1h trend filter, 15m pullback to the daily VWAP.
def pullback(bars15: list[Bar], market: str, p: dict, notional=10_000, funding=None) -> Result:
  book = Book(market, notional, funding)
  closes = [b.c for b in bars15]
  ef, es = ema(closes, p.get("fast", 80)), ema(closes, p.get("slow", 200))  # ~20h / 50h EMAs on 15m bars
  a = atr(bars15, 14)
  vw_num = vw_den = 0.0
  day = None
  cooldown = 0
  for i in range(210, len(bars15)):
    b, prev = bars15[i], bars15[i - 1]
    d = prev.t.date()
    if d != day:
      day, vw_num, vw_den = d, 0.0, 0.0
    tp_ = (prev.h + prev.l + prev.c) / 3
    vw_num += tp_ * prev.v
    vw_den += prev.v
    vw = vw_num / vw_den if vw_den else prev.c
    if book.pos and book.mark(b, 15):
      cooldown = p.get("cooldown", 4)
      continue
    if book.pos:
      continue
    if cooldown:
      cooldown -= 1
      continue
    k = p["sl_atr"] * a[i - 1]
    up = ef[i - 1] > es[i - 1] and (ef[i - 1] - ef[i - 9]) > 0
    dn = ef[i - 1] < es[i - 1] and (ef[i - 1] - ef[i - 9]) < 0
    if up and prev.l <= vw + p["pull_atr"] * a[i - 1] and prev.c > vw and prev.c > prev.o:
      book.open(b.t, 1, b.o, b.o - k, tp=b.o + k * p["tp_mult"], maker=False)
      book.mark(b, 15)
    elif market == "perp" and dn and prev.h >= vw - p["pull_atr"] * a[i - 1] and prev.c < vw and prev.c < prev.o:
      book.open(b.t, -1, b.o, b.o + k, tp=b.o - k * p["tp_mult"], maker=False)
      book.mark(b, 15)
  book.r.open_pos = book.pos
  return book.r


def split_by_time(bars: list[Bar], start: datetime, end: datetime) -> list[Bar]:
  return [b for b in bars if start <= b.t < end]


# Strategy 4: MOMENTUM (time-series momentum) on daily bars: hold long while the N-day return and price
# vs the M-day average are positive (perp: short when both negative). Rebalanced at each daily close.
def momentum(bars1d: list[Bar], market: str, p: dict, notional=10_000, funding=None) -> Result:
  book = Book(market, notional, funding)
  closes = [b.c for b in bars1d]
  ma = ema(closes, p["ma"])
  a = atr(bars1d, 14)
  n = p["lookback"]
  for i in range(max(n, p["ma"]) + 1, len(bars1d)):
    b = bars1d[i]
    if book.pos and book.mark(b, 1440):
      continue
    ret = closes[i - 1] / closes[i - 1 - n] - 1
    want = 1 if (ret > 0 and closes[i - 1] > ma[i - 1]) else (-1 if (market == "perp" and ret < 0 and closes[i - 1] < ma[i - 1]) else 0)
    if book.pos and book.pos["side"] != want:
      book.close(b.t, b.o, "signal")
    if not book.pos and want:
      k = p["atr_k"] * a[i - 1]
      book.open(b.t, want, b.o, b.o - want * k, trail=k)
      book.mark(b, 1440)
  book.r.open_pos = book.pos
  return book.r


# Strategy 5: MEAN REVERSION (RSI-2 style) on 4h bars: in a long-term uptrend, buy after a sharp short-term
# drop, exit on a bounce above the short average or after max_bars; perp also shorts spikes in downtrends.
def meanrev(bars: list[Bar], market: str, p: dict, notional=10_000, funding=None, minutes=240) -> Result:
  book = Book(market, notional, funding)
  closes = [b.c for b in bars]
  slow, fast = ema(closes, p["trend"]), ema(closes, 5)
  a = atr(bars, 14)
  # RSI(2)
  rsi, up, dn = [], 0.0, 0.0
  for i, c in enumerate(closes):
    if i == 0:
      rsi.append(50)
      continue
    ch = c - closes[i - 1]
    up = (up * 1 + max(ch, 0)) / 2
    dn = (dn * 1 + max(-ch, 0)) / 2
    rsi.append(100 if dn == 0 else 100 - 100 / (1 + up / dn))
  held = 0
  for i in range(p["trend"] + 2, len(bars)):
    b = bars[i]
    if book.pos:
      held += 1
      if book.mark(b, minutes):
        continue
      s = book.pos["side"]
      if (s == 1 and closes[i - 1] > fast[i - 1]) or (s == -1 and closes[i - 1] < fast[i - 1]) or held >= p["max_bars"]:
        book.close(b.t, b.o, "exit")
      continue
    k = p["sl_atr"] * a[i - 1]
    if closes[i - 1] > slow[i - 1] and rsi[i - 1] < p["rsi_lo"]:
      book.open(b.t, 1, b.o, b.o - k)
      held = 0
      book.mark(b, minutes)
    elif market == "perp" and closes[i - 1] < slow[i - 1] and rsi[i - 1] > 100 - p["rsi_lo"]:
      book.open(b.t, -1, b.o, b.o + k)
      held = 0
      book.mark(b, minutes)
  book.r.open_pos = book.pos
  return book.r
