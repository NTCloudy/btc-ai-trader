"""Live paper-trading engine.

Each loop: refresh market data, run every AI's strategy over its timeframe bars (the last bar may be the
in-progress one, so stops react within the bar while entries only use closed bars), record new closed
trades of the current "era" (parameters in force since the last weekly re-tune), mark the open position,
snapshot equity, publish the dashboard state and send LINE notices.

Because the live engine runs the exact backtest code on the live bars, live and backtest results match by
construction; differences can only come from data (live klines vs archive) or timing.
"""

from __future__ import annotations

import json
import time
import traceback
from datetime import datetime, timedelta, timezone

from .ais import AIS, NOTIONAL, run
from .sim import FEES

UTC = timezone.utc
TPE = timezone(timedelta(hours=8))


def select_params(ai: dict, market, now: datetime | None = None, days: int = 365) -> tuple[dict, dict]:
  """Weekly self-learning: best grid parameters over the trailing `days` (score = profit - 0.5 x drawdown)."""
  now = now or datetime.now(UTC)
  start = now - timedelta(days=days)
  bars = [b for b in market.bars(ai["tf"], include_live=False) if b.t >= start - timedelta(days=ai["warmup_days"])]
  best, best_score, best_stats = None, None, None
  for p in ai["grid"]:
    r = run(ai, bars, p, market.funding)
    trades = [x for x in r.log if x[0] >= start]
    eq = peak = dd = 0.0
    for x in trades:
      eq += x[5]
      peak = max(peak, eq)
      dd = max(dd, peak - eq)
    score = eq - 0.5 * dd
    if best_score is None or score > best_score:
      best, best_score = p, score
      best_stats = {"pnl": round(eq), "maxdd": round(dd), "trades": len(trades),
                    "wins": sum(1 for x in trades if x[5] > 0)}
  return best, best_stats


def mark_to_market(ai: dict, pos: dict, price: float) -> float:
  fee = FEES[ai["market"]]["taker"]
  return (price - pos["entry"]) * pos["qty"] * pos["side"] - pos["fee_in"] - price * pos["qty"] * fee - pos.get("funding", 0.0)


class Engine:

  def __init__(self, ledger, market, publisher=None, notifier=None, loop_seconds: int = 30):
    self.ledger, self.market, self.publisher, self.notifier = ledger, market, publisher, notifier
    self.loop_seconds = loop_seconds
    self.last_equity = 0.0
    self.last_publish = 0.0

  def era(self, ai: dict) -> dict:
    e = self.ledger.get(f"era:{ai['id']}")
    if e is None:
      params, stats = select_params(ai, self.market)
      e = {"since": datetime.now(UTC).isoformat(timespec="seconds"), "params": params, "stats": stats, "reason": "上線"}
      self.ledger.set(f"era:{ai['id']}", e)
      self.ledger.log(f"{ai['name']} 上線，參數 {params}（近一年回測 {stats['pnl']:+,} USDT）", ai["id"])
    return e

  def step(self) -> None:
    m = self.market
    m.update()
    now = datetime.now(UTC)
    if self.ledger.get("bh") is None:  # Buy-and-hold benchmark starts with the engine.
      self.ledger.set("bh", {"t": now.isoformat(timespec="seconds"), "price": m.price})
    for ai in AIS:
      e = self.era(ai)
      since = datetime.fromisoformat(e["since"])
      r = run(ai, m.bars(ai["tf"]), e["params"], m.funding)
      for (et, xt, side, ep, xp, net, why) in r.log:
        if et >= since and self.ledger.add_trade(ai["id"], side, et, ep, xt, xp, net, why, e["params"]):
          msg = f"🏁 {ai['name']} 平倉｜{'多' if side == 1 else '空'}單 {ep:,.0f} → {xp:,.0f}｜{net:+,.0f} USDT（{why}）"
          self.ledger.log(msg, ai["id"], "TRADE")
          if self.notifier:
            self.notifier.send(msg)
      pos = r.open_pos if r.open_pos and r.open_pos["t"] >= since else None
      prev = self.ledger.get(f"pos:{ai['id']}")
      if pos:
        info = {"side": pos["side"], "entry": pos["entry"], "t": pos["t"].isoformat(), "stop": pos["stop"],
                "tp": pos["tp"], "qty": pos["qty"], "fee_in": pos["fee_in"], "funding": pos.get("funding", 0.0),
                "unrealized": round(mark_to_market(ai, pos, m.price), 2)}
        if not prev or prev.get("t") != info["t"]:
          msg = (f"✅ {ai['name']} 進場｜{'做多' if pos['side'] == 1 else '放空'} {pos['entry']:,.0f}｜"
                 f"停損 {pos['stop']:,.0f}" + (f"｜停利 {pos['tp']:,.0f}" if pos["tp"] else "｜移動停損"))
          self.ledger.log(msg, ai["id"], "TRADE")
          if self.notifier:
            self.notifier.send(msg)
        self.ledger.set(f"pos:{ai['id']}", info)
      elif prev:
        self.ledger.set(f"pos:{ai['id']}", None)
    if time.monotonic() - self.last_equity >= 300:
      self.last_equity = time.monotonic()
      ts = now.isoformat(timespec="seconds")
      for ai in AIS:
        p = self.ledger.get(f"pos:{ai['id']}")
        self.ledger.exec("INSERT INTO equity(ts,ai,value) VALUES(?,?,?)",
                         (ts, ai["id"], self.ledger.realized(ai["id"]) + (p["unrealized"] if p else 0.0)))
      self.ledger.exec("INSERT INTO equity(ts,ai,value) VALUES(?,?,?)", (ts, "bh", self.bh_value()))

  def bh_value(self) -> float:
    bh = self.ledger.get("bh")
    if not bh or not self.market.price:
      return 0.0
    qty = NOTIONAL / bh["price"]
    fee = FEES["spot"]["taker"]
    return qty * self.market.price - NOTIONAL - NOTIONAL * fee - qty * self.market.price * fee

  def run_forever(self) -> None:
    self.ledger.log("引擎啟動")
    while True:
      try:
        self.step()
        if self.publisher and time.monotonic() - self.last_publish >= 60:
          self.last_publish = time.monotonic()
          self.publisher.publish()
        if self.notifier:
          self.notifier.maybe_daily(self)
      except Exception as exc:
        self.ledger.log(f"引擎錯誤：{exc}", level="ERROR")
        traceback.print_exc()
      time.sleep(self.loop_seconds)
