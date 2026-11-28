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

from .ais import AIS, COIN_NAMES, COINS, NOTIONAL, notional
from .engine import TPE, select_params
from .sim import ema

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
REPO = "NTCloudy/btc-ai-trader"
UI_VERSION = 2  # Bump together with UI in docs/index.html whenever the state format changes.
CRED = Path.home() / ".config" / "tw-futures-ai"


def coin_info(m) -> dict:
  d1 = m.bars(1440)
  closes = [b.c for b in d1]
  ma200 = ema(closes, 200)[-1] if len(closes) > 200 else None
  return {"symbol": m.symbol, "coin": COIN_NAMES[m.symbol], "price": m.price, "change_24h": m.change_24h(),
          "funding_rate": m.last_rate, "ma200d": ma200,
          "regime": None if ma200 is None or not m.price else ("bull" if m.price > ma200 else "bear"),
          "spark": [[b.t.isoformat(), b.c] for b in m.bars(60)[-24 * 7:]]}


def build_state(ledger, markets: dict) -> dict:
  from .engine import Engine
  now = datetime.now(UTC)
  eng = Engine(ledger, markets)
  ais = []
  for ai in AIS:
    trades = ledger.rows("SELECT * FROM trades WHERE ai=? ORDER BY id DESC", (ai["id"],))
    coins = []
    for sym in ai["coins"]:
      era = ledger.get(f"era:{ai['id']}:{sym}") or {}
      pos = ledger.get(f"pos:{ai['id']}:{sym}")
      ct = [t for t in trades if t["symbol"] == sym]
      coins.append({"symbol": sym, "coin": COIN_NAMES[sym], "params": era.get("params"), "since": era.get("since"),
                    "stats": era.get("stats"), "reason": era.get("reason"), "position": pos,
                    "realized": round(sum(t["pnl"] for t in ct), 2), "trades": len(ct)})
    realized = sum(t["pnl"] for t in trades)
    unreal = sum((c["position"] or {}).get("unrealized", 0.0) for c in coins)
    eq = ledger.rows("SELECT ts, value FROM equity WHERE ai=? ORDER BY ts", (ai["id"],))
    ais.append({
        "id": ai["id"], "name": ai["name"], "emoji": ai["emoji"], "color": ai["color"], "title": ai["title"],
        "market": "現貨（只做多）" if ai["market"] == "spot" else "永續合約（多空）", "desc": ai["desc"],
        "per_coin": round(notional(ai)), "coins": coins, "realized": round(realized, 2),
        "total": round(realized + unreal, 2), "trades": len(trades), "wins": sum(1 for t in trades if t["pnl"] > 0),
        "recent": [dict(t, coin=COIN_NAMES.get(t["symbol"], t["symbol"])) for t in trades[:15]],
        "equity": [[e["ts"], round(e["value"], 2)] for e in eq[::max(1, len(eq) // 400)]],
    })
  bh = ledger.get("bh")
  bh_eq = ledger.rows("SELECT ts, value FROM equity WHERE ai='bh' ORDER BY ts")
  coins_list = [coin_info(markets[s]) for s in COINS if s in markets]
  btc_compat = coins_list[0] if coins_list else {}
  return {
      **btc_compat,  # Backwards-compat top-level fields in case a browser cached an older index.html.
      "ui": UI_VERSION,  # The page reloads itself when its own version differs (stale cached page).
      "generated_at": now.isoformat(timespec="seconds"),
      "coins": coins_list,
      "notional": NOTIONAL, "ais": ais,
      "bh": {"since": bh["t"] if bh else None,
             "equity": [[e["ts"], round(e["value"], 2)] for e in bh_eq[::max(1, len(bh_eq) // 400)]],
             "value": round(eng.bh_value(), 2)},
      "learning": ledger.get("learning_log", [])[-12:],
      "events": ledger.rows("SELECT ts, ai, level, message FROM events ORDER BY id DESC LIMIT 40"),
  }


class Publisher:
  """Same approach as the futures project: amend one commit on the `live-state` branch and write a fresh
  slots/<epoch//60>.json each minute (raw.githubusercontent caches a path for ~5 minutes)."""

  def __init__(self, ledger, markets):
    self.ledger, self.markets = ledger, markets
    self.dir = ROOT / "data" / "publish"
    self._last_gc = 0.0

  def _git(self, *args):
    token = (CRED / "gh_token").read_text().strip()
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    env = dict(
        os.environ,
        GIT_TERMINAL_PROMPT="0",
        GIT_CONFIG_COUNT="2",
        GIT_CONFIG_KEY_0="credential.helper",
        GIT_CONFIG_VALUE_0="",
        GIT_CONFIG_KEY_1="http.https://github.com/.extraheader",
        GIT_CONFIG_VALUE_1=f"Authorization: Basic {basic}",
    )
    try:
      return subprocess.run(["git", *args], cwd=self.dir, check=True, capture_output=True, text=True,
                            env=env, timeout=60).stdout
    except subprocess.CalledProcessError as exc:
      raise RuntimeError(f"git {args[0] if args else ''} failed (exit {exc.returncode})") from None

  def publish(self):
    if not (self.dir / ".git").exists():
      self.dir.mkdir(parents=True, exist_ok=True)
      self._git("init", "-q", "-b", "live-state")
      self._git("config", "user.name", "btc-ai-bot")
      self._git("config", "user.email", "bot@users.noreply.github.com")
      self._git("config", "core.logAllRefUpdates", "false")
      self._git("remote", "add", "origin", f"https://github.com/{REPO}.git")
    body = json.dumps(build_state(self.ledger, self.markets), ensure_ascii=False, default=str)
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
    if time.monotonic() - self._last_gc >= 3600:
      self._last_gc = time.monotonic()
      self._git("config", "core.logAllRefUpdates", "false")
      self._git("gc", "--prune=now", "-q")
    self._git("push", "-q", "-f", "origin", "HEAD:live-state")


# ---- Email notices (user 2026-11-28): only Trendy's BTC entries and exits, by email (not LINE, to keep the free
# LINE quota for the futures alerts). Recipients and the sending account live outside the repo in
# ~/.config/tw-futures-ai/mail.json: {"to": [...], "smtp_user": "...", "app_password": "...",
# "host": "smtp.gmail.com", "port": 465}. Sending runs in a background thread with retries, so a mail failure
# never blocks or stops the engine loop; each notice is sent at most once (key remembered in the ledger).
MAIL_SCOPE = {("trendy", "BTCUSDT")}
WHY_ZH = {"target": "停利（達到目標價）", "stop": "停損／移動停損", "ema_exit": "價格穿越快線（趨勢轉弱）出場",
          "每週調整參數": "每週調整參數時平倉"}
DISCLAIMER = ("※ 本信是 BTC AI「模擬交易」（紙上交易，沒有真實下單）的訊號，僅供參考，不是投資建議。"
              "實際成交價、滑價、手續費與資金費率都可能不同，請自行判斷。")


class Mailer:

  def __init__(self, ledger, cred_path: Path | None = None, send_fn=None, retries: int = 3, wait_s: float = 30.0):
    f = cred_path or CRED / "mail.json"
    try:
      self.cfg = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except Exception as exc:
      print(f"mail.json 讀取失敗：{exc}")
      self.cfg = {}
    self.ledger, self.retries, self.wait_s = ledger, retries, wait_s
    self._send_fn = send_fn or self._smtp

  def ready(self) -> bool:
    return bool(self.cfg.get("to") and self.cfg.get("smtp_user") and self.cfg.get("app_password"))

  @staticmethod
  def wants(ai_id: str, sym: str) -> bool:
    return (ai_id, sym) in MAIL_SCOPE

  def _smtp(self, subject: str, body: str) -> None:
    import smtplib
    import ssl
    from email.message import EmailMessage
    to = self.cfg["to"] if isinstance(self.cfg["to"], list) else [self.cfg["to"]]
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, self.cfg["smtp_user"], ", ".join(to)
    msg.set_content(body)
    with smtplib.SMTP_SSL(self.cfg.get("host", "smtp.gmail.com"), int(self.cfg.get("port", 465)),
                          context=ssl.create_default_context(), timeout=20) as smtp:
      smtp.login(self.cfg["smtp_user"], str(self.cfg["app_password"]).replace(" ", ""))
      smtp.send_message(msg)

  def _deliver(self, subject: str, body: str) -> bool:
    for i in range(self.retries):
      try:
        self._send_fn(subject, body)
        print(f"📧 已寄出：{subject}")
        return True
      except Exception as exc:  # Never raise into the engine.
        print(f"📧 寄信失敗（第 {i + 1}/{self.retries} 次）：{exc}")
        if i + 1 < self.retries:
          time.sleep(self.wait_s)
    return False

  def notify(self, key: str, subject: str, body: str, background: bool = True) -> bool:
    """Queues one notice. Returns False when it was already sent or no sending account is configured."""
    sent = self.ledger.get("mail_sent") or []
    if key in sent:
      return False
    self.ledger.set("mail_sent", (sent + [key])[-200:])  # At most once, even if the engine restarts mid-send.
    if not self.ready():
      self.ledger.log(f"📧 尚未設定寄信帳號，這封沒有寄出：{subject}", "trendy", "INFO")
      return False
    body = f"{body}\n\n{DISCLAIMER}"
    if background:
      import threading
      threading.Thread(target=self._deliver, args=(subject, body), daemon=True).start()
      return True
    return self._deliver(subject, body)

  def entry(self, ai: dict, sym: str, pos: dict) -> bool:
    if not self.wants(ai["id"], sym):
      return False
    coin, side = COIN_NAMES[sym], ("做多" if pos["side"] == 1 else "放空")
    t = datetime.fromisoformat(pos["t"]) if isinstance(pos["t"], str) else pos["t"]
    stop_txt = f"{pos['stop']:,.1f} USDT（移動停損：價格往有利方向走時會跟著移動，出場時會再寄信通知）"
    tp_txt = f"{pos['tp']:,.1f} USDT" if pos.get("tp") else "沒有固定停利（觸及移動停損或價格穿越快線時出場）"
    body = "\n".join([f"{ai['name']}（{ai['title']}）{coin} 永續合約・模擬單", f"動作：進場 {side}",
                      f"時間：{t.astimezone(TPE):%Y-%m-%d %H:%M}（台北）", f"進場價：{pos['entry']:,.1f} USDT",
                      f"停損：{stop_txt}", f"停利：{tp_txt}",
                      f"模擬資金：每個幣 {notional(ai):,.0f} USDT"])
    return self.notify(f"entry:{ai['id']}:{sym}:{t.isoformat()}",
                       f"【{ai['name']} {coin} 模擬訊號】進場 {side} @ {pos['entry']:,.1f}", body)

  def exit(self, ai: dict, sym: str, side: int, et, ep: float, xt, xp: float, net: float, why: str) -> bool:
    if not self.wants(ai["id"], sym):
      return False
    coin, s = COIN_NAMES[sym], ("多單" if side == 1 else "空單")
    pct = (xp - ep) / ep * side * 100 if ep else 0.0
    body = "\n".join([f"{ai['name']}（{ai['title']}）{coin} 永續合約・模擬單", f"動作：出場（平{s}）",
                      f"進場：{et.astimezone(TPE):%Y-%m-%d %H:%M} @ {ep:,.1f} USDT",
                      f"出場：{xt.astimezone(TPE):%Y-%m-%d %H:%M} @ {xp:,.1f} USDT（{pct:+.2f}%）",
                      f"出場原因：{WHY_ZH.get(why, why)}",
                      f"損益：{net:+,.1f} USDT（含手續費與資金費率；模擬資金每個幣 {notional(ai):,.0f} USDT）"])
    return self.notify(f"exit:{ai['id']}:{sym}:{et.isoformat()}:{xt.isoformat()}",
                       f"【{ai['name']} {coin} 模擬訊號】出場 {s} {ep:,.1f} → {xp:,.1f}（{net:+,.0f} USDT）", body)


class Notifier:
  """LINE (same bot as the futures project): short daily report at 08:00 Taipei + weekly summary. Per-trade
  entry / exit LINE notices are off (user 2026-11-17: LINE entry alerts only for the futures Omega); Trendy BTC
  trades are emailed instead (Mailer)."""

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
    btc = engine.markets["BTCUSDT"]
    lines = [f"每日報告 {now:%m/%d}｜BTC {btc.price:,.0f}（24h {btc.change_24h() or 0:+.1%}）", "AI｜過去 24h｜累計｜持倉"]
    for ai in AIS:
      d = sum(t["pnl"] for t in self.ledger.rows("SELECT pnl FROM trades WHERE ai=? AND exit_t>=?", (ai["id"], since)))
      held = [COIN_NAMES[s] for s in ai["coins"] if self.ledger.get(f"pos:{ai['id']}:{s}")]
      lines.append(f"{ai['emoji']} {ai['name']}｜{d:+,.0f}｜{engine.ai_total(ai):+,.0f}｜{'、'.join(held) or '空手'}")
    lines.append(f"📦 買進持有｜–｜{engine.bh_value():+,.0f}｜6 幣平均")
    lines.append("（USDT；每位 AI 總資金 10,000，平均分給負責的幣）")
    self.send("\n".join(lines))


def weekly(ledger, markets: dict, notifier=None, mailer=None) -> list[str]:
  """Weekly self-learning: re-select each AI's parameters per coin from the trailing year; switch only if better."""
  from .engine import mark_to_market
  out = [f"每週自主學習 {datetime.now(TPE):%m/%d}"]
  log = ledger.get("learning_log", [])
  for ai in AIS:
    changed, kept = [], []
    for sym in ai["coins"]:
      m = markets[sym]
      key = f"era:{ai['id']}:{sym}"
      era = ledger.get(key)
      best, stats = select_params(ai, m, cur_params=era.get("params") if era else None)
      if era and best == era["params"]:
        kept.append(COIN_NAMES[sym])
        continue
      now = datetime.now(UTC)
      pos = ledger.get(f"pos:{ai['id']}:{sym}")
      if pos and era:  # Close the previous era's position at the current price before switching.
        p = dict(pos, t=datetime.fromisoformat(pos["t"]))
        net = mark_to_market(ai, p, m.price)
        if ledger.add_trade(ai["id"], sym, pos["side"], p["t"], pos["entry"], now, m.price, net, "每週調整參數",
                            era["params"]) and mailer:
          mailer.exit(ai, sym, pos["side"], p["t"], pos["entry"], now, m.price, net, "每週調整參數")
        ledger.set(f"pos:{ai['id']}:{sym}", None)
      ledger.set(key, {"since": now.isoformat(timespec="seconds"), "params": best, "stats": stats, "reason": "每週調整"})
      changed.append(f"{COIN_NAMES[sym]}（近一年 {stats['pnl']:+,}）")
      log.append({"ts": now.isoformat(timespec="seconds"), "ai": f"{ai['name']}／{COIN_NAMES[sym]}",
                  "from": era and era["params"], "to": best, "stats": stats})
      ledger.log(f"{ai['name']}／{COIN_NAMES[sym]} 每週調整參數 → {best}", ai["id"], "LEARN")
    out.append(f"{ai['emoji']} {ai['name']}：" + (f"調整 {'、'.join(changed)}" if changed else "全部維持")
               + (f"；維持 {'、'.join(kept)}" if changed and kept else ""))
  ledger.set("learning_log", log[-60:])
  if notifier:
    notifier.send("\n".join(out))
  return out
