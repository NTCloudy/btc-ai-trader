"""Dashboard state (JSON), GitHub publisher, LINE notices and the weekly self-learning job."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .ais import AIS, NOTIONAL
from .engine import TPE, select_params
from .sim import ema

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
REPO = "NTCloudy/btc-ai-trader"
CRED = Path.home() / ".config" / "tw-futures-ai"


def build_state(ledger, market) -> dict:
  now = datetime.now(UTC)
  d1 = market.bars(1440)
  closes = [b.c for b in d1]
  ma200 = ema(closes, 200)[-1] if len(closes) > 200 else None
  h1 = market.bars(60)[-24 * 7:]
  ais = []
  for ai in AIS:
    era = ledger.get(f"era:{ai['id']}") or {}
    pos = ledger.get(f"pos:{ai['id']}")
    trades = ledger.rows("SELECT * FROM trades WHERE ai=? ORDER BY id DESC", (ai["id"],))
    realized = sum(t["pnl"] for t in trades)
    eq = ledger.rows("SELECT ts, value FROM equity WHERE ai=? ORDER BY ts", (ai["id"],))
    wins = sum(1 for t in trades if t["pnl"] > 0)
    ais.append({
        "id": ai["id"], "name": ai["name"], "emoji": ai["emoji"], "color": ai["color"], "title": ai["title"],
        "market": "現貨（只做多）" if ai["market"] == "spot" else "永續合約（多空）", "desc": ai["desc"],
        "params": era.get("params"), "since": era.get("since"), "stats": era.get("stats"), "reason": era.get("reason"),
        "position": pos, "realized": round(realized, 2),
        "total": round(realized + (pos["unrealized"] if pos else 0.0), 2),
        "trades": len(trades), "wins": wins, "recent": trades[:15],
        "equity": [[e["ts"], round(e["value"], 2)] for e in eq[::max(1, len(eq) // 400)]],
    })
  bh = ledger.get("bh")
  bh_eq = ledger.rows("SELECT ts, value FROM equity WHERE ai='bh' ORDER BY ts")
  return {
      "generated_at": now.isoformat(timespec="seconds"),
      "price": market.price, "mark": market.mark, "change_24h": market.change_24h(),
      "funding_rate": market.last_rate, "next_funding": market.next_funding.isoformat() if market.next_funding else None,
      "ma200d": ma200, "regime": None if ma200 is None else ("bull" if market.price > ma200 else "bear"),
      "spark": [[b.t.isoformat(), b.c] for b in h1],
      "notional": NOTIONAL, "ais": ais,
      "bh": {"since": bh["t"] if bh else None, "entry": bh["price"] if bh else None,
             "equity": [[e["ts"], round(e["value"], 2)] for e in bh_eq[::max(1, len(bh_eq) // 400)]],
             "value": round(bh_eq[-1]["value"], 2) if bh_eq else 0.0},
      "learning": ledger.get("learning_log", [])[-12:],
      "events": ledger.rows("SELECT ts, ai, level, message FROM events ORDER BY id DESC LIMIT 40"),
  }


class Publisher:
  """Same approach as the futures project: amend one commit on the `live-state` branch and write a fresh
  slots/<epoch//60>.json each minute (raw.githubusercontent caches a path for ~5 minutes)."""

  def __init__(self, ledger, market):
    self.ledger, self.market = ledger, market
    self.dir = ROOT / "data" / "publish"

  def _git(self, *args):
    token = (CRED / "gh_token").read_text().strip()
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    extra = ["-c", "credential.helper=", "-c", f"http.https://github.com/.extraheader=Authorization: Basic {basic}"]
    return subprocess.run(["git", *extra, *args], cwd=self.dir, check=True, capture_output=True, text=True,
                          env=dict(os.environ, GIT_TERMINAL_PROMPT="0"), timeout=60).stdout

  def publish(self):
    if not (self.dir / ".git").exists():
      self.dir.mkdir(parents=True, exist_ok=True)
      self._git("init", "-q", "-b", "live-state")
      self._git("config", "user.name", "btc-ai-bot")
      self._git("config", "user.email", "bot@users.noreply.github.com")
      self._git("remote", "add", "origin", f"https://github.com/{REPO}.git")
    body = json.dumps(build_state(self.ledger, self.market), ensure_ascii=False, default=str)
    slot = int(time.time() // 60)
    (self.dir / "slots").mkdir(exist_ok=True)
    (self.dir / "slots" / f"{slot}.json").write_text(body, encoding="utf-8")
    for f in (self.dir / "slots").glob("*.json"):
      if f.stem.isdigit() and int(f.stem) < slot - 8:
        f.unlink()
    (self.dir / "state.json").write_text(body, encoding="utf-8")
    self._git("add", "-A")
    has_head = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=self.dir, capture_output=True).returncode == 0
    self._git("commit", "-q", "--allow-empty", "-m", "state", *(["--amend"] if has_head else []))
    self._git("push", "-q", "-f", "origin", "HEAD:live-state")


class Notifier:
  """LINE (same bot as the futures project). Entry / exit notices + a short daily report at 08:00 Taipei."""

  def __init__(self, ledger):
    self.ledger = ledger
    f = CRED / "line.json"
    self.cred = json.loads(f.read_text()) if f.exists() else None

  def send(self, text: str) -> None:
    if not self.cred:
      return
    body = json.dumps({"to": self.cred["to"], "messages": [{"type": "text", "text": f"【BTC AI】{text}"[:4900]}]}).encode()
    req = urllib.request.Request("https://api.line.me/v2/bot/message/push", data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {self.cred['token']}"})
    try:
      urllib.request.urlopen(req, timeout=10).read()
    except Exception as exc:
      print(f"LINE failed: {exc}")

  def maybe_daily(self, engine) -> None:
    now = datetime.now(TPE)
    day = now.strftime("%Y-%m-%d")
    if now.hour < 8 or self.ledger.get("daily_sent") == day:
      return
    self.ledger.set("daily_sent", day)
    since = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    lines = [f"每日報告 {now:%m/%d}｜BTC {engine.market.price:,.0f}（24h {engine.market.change_24h() or 0:+.1%}）",
             "AI｜過去 24h｜累計｜持倉"]
    for ai in AIS:
      d = sum(t["pnl"] for t in self.ledger.rows("SELECT pnl FROM trades WHERE ai=? AND exit_t>=?", (ai["id"], since)))
      pos = self.ledger.get(f"pos:{ai['id']}")
      tot = self.ledger.realized(ai["id"]) + (pos["unrealized"] if pos else 0)
      held = f"{'多' if pos['side'] == 1 else '空'}單 {pos['entry']:,.0f}（{pos['unrealized']:+,.0f}）" if pos else "空手"
      lines.append(f"{ai['emoji']} {ai['name']}｜{d:+,.0f}｜{tot:+,.0f}｜{held}")
    lines.append(f"📦 買進持有｜–｜{engine.bh_value():+,.0f}｜持有")
    lines.append("（單位 USDT，每位 AI 每筆 10,000 USDT 模擬）")
    self.send("\n".join(lines))


def weekly(ledger, market, notifier=None) -> list[str]:
  """Weekly self-learning: re-select each AI's parameters from the trailing year; switch only if different."""
  from .engine import mark_to_market
  out = [f"每週自主學習 {datetime.now(TPE):%m/%d}"]
  log = ledger.get("learning_log", [])
  for ai in AIS:
    era = ledger.get(f"era:{ai['id']}")
    best, stats = select_params(ai, market)
    if era and best == era["params"]:
      out.append(f"{ai['emoji']} {ai['name']}：維持 {best}（近一年 {stats['pnl']:+,} USDT，勝 {stats['wins']}/{stats['trades']}）")
      continue
    pos = ledger.get(f"pos:{ai['id']}")
    now = datetime.now(UTC)
    if pos:  # Close the previous era's position at the current price before switching.
      p = dict(pos, t=datetime.fromisoformat(pos["t"]))
      pnl = mark_to_market(ai, p, market.price)
      ledger.add_trade(ai["id"], pos["side"], p["t"], pos["entry"], now, market.price, pnl, "每週調整參數", era["params"])
      ledger.set(f"pos:{ai['id']}", None)
    ledger.set(f"era:{ai['id']}", {"since": now.isoformat(timespec="seconds"), "params": best, "stats": stats,
                                   "reason": "每週調整"})
    out.append(f"{ai['emoji']} {ai['name']}：✅ 改為 {best}（近一年 {stats['pnl']:+,} USDT，勝 {stats['wins']}/{stats['trades']}）")
    log.append({"ts": now.isoformat(timespec="seconds"), "ai": ai["name"], "from": era and era["params"], "to": best,
                "stats": stats})
    ledger.log(f"{ai['name']} 每週調整參數 → {best}", ai["id"], "LEARN")
  ledger.set("learning_log", log[-50:])
  if notifier:
    notifier.send("\n".join(out))
  return out
