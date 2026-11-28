import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from btc_ai.ais import AIS, run  # noqa: E402
from btc_ai.sim import Bar, Book, resample, us_open_utc  # noqa: E402

UTC = timezone.utc


def series(n, start=30000.0, step=50.0, minutes=60):
  t0 = datetime(2025, 1, 1, tzinfo=UTC)
  out, p = [], start
  for i in range(n):
    p2 = p + step
    out.append(Bar(t0 + timedelta(minutes=minutes * i), p, max(p, p2) + 10, min(p, p2) - 10, p2, 1.0))
    p = p2
  return out


class BtcTests(unittest.TestCase):

  def test_book_fees_and_stop(self):
    b = Book("spot", 10_000, None)
    b.open(datetime(2025, 1, 1, tzinfo=UTC), 1, 100.0, 95.0)
    bar = Bar(datetime(2025, 1, 1, 1, tzinfo=UTC), 100, 101, 94, 96, 1)
    self.assertTrue(b.mark(bar, 60))
    self.assertAlmostEqual(b.r.pnl, -500 - 10 - 9.5, places=6)  # 5% loss on 10k, 0.1% fee in, 0.1% fee out at 95

  def test_resample_daily(self):
    d = resample(series(48), 1440)
    self.assertEqual(len(d), 2)

  def test_uptrend_momentum_goes_long(self):
    momo = [a for a in AIS if a["id"] == "momo"][0]
    bars = resample(series(24 * 200, step=20.0), 1440)
    r = run(momo, bars, momo["default"])
    self.assertTrue(r.open_pos and r.open_pos["side"] == 1)

  def test_us_open_dst(self):
    self.assertEqual(us_open_utc(datetime(2026, 7, 1, tzinfo=UTC)).hour, 13)
    self.assertEqual(us_open_utc(datetime(2026, 12, 1, tzinfo=UTC)).hour, 14)


if __name__ == "__main__":
  unittest.main()


class MailerTests(unittest.TestCase):
  """User 2026-11-28: email only Trendy's BTC entries/exits; never block or break the engine."""

  def setUp(self):
    import json
    import tempfile
    from btc_ai.ledger import Ledger
    from btc_ai.service import Mailer
    self.tmp = tempfile.TemporaryDirectory()
    d = Path(self.tmp.name)
    self.ledger = Ledger(str(d / "t.db"))
    cred = d / "mail.json"
    cred.write_text(json.dumps({"to": ["x@example.com"], "smtp_user": "u@example.com", "app_password": "p"}))
    self.sent = []
    self.m = Mailer(self.ledger, cred_path=cred, send_fn=lambda s, b: self.sent.append((s, b)), wait_s=0)
    self.Mailer, self.d = Mailer, d
    self.trendy = [a for a in AIS if a["id"] == "trendy"][0]
    self.t = datetime(2026, 11, 28, 4, 0, tzinfo=UTC)

  def tearDown(self):
    self.tmp.cleanup()

  def pos(self, tp=None):
    return {"side": 1, "entry": 95000.0, "t": self.t.isoformat(), "stop": 93000.0, "tp": tp}

  def test_entry_scope_dedup_and_disclaimer(self):
    self.assertTrue(self.m.entry(self.trendy, "BTCUSDT", self.pos()))
    import time as _t
    _t.sleep(0.2)
    self.assertEqual(len(self.sent), 1)
    subj, body = self.sent[0]
    self.assertIn("進場 做多 @ 95,000.0", subj)
    self.assertIn("沒有固定停利", body)
    self.assertIn("模擬交易", body)  # disclaimer
    self.assertIn("2026-11-28 12:00（台北）", body)
    self.assertFalse(self.m.entry(self.trendy, "BTCUSDT", self.pos()))  # same entry: not sent twice
    self.assertFalse(self.m.entry(self.trendy, "ETHUSDT", self.pos()))  # other coin: out of scope
    momo = [a for a in AIS if a["id"] == "momo"][0]
    self.assertFalse(self.m.entry(momo, "BTCUSDT", self.pos()))  # other AI: out of scope
    _t.sleep(0.1)
    self.assertEqual(len(self.sent), 1)

  def test_exit_body(self):
    ok = self.m.exit(self.trendy, "BTCUSDT", -1, self.t, 64130.5, self.t + timedelta(days=1), 57487.1, 343.6, "target")
    import time as _t
    _t.sleep(0.2)
    self.assertTrue(ok)
    subj, body = self.sent[0]
    self.assertIn("出場 空單 64,130.5 → 57,487.1（+344 USDT）", subj)
    self.assertIn("停利（達到目標價）", body)
    self.assertIn("+10.36%", body)

  def test_failure_retries_and_never_raises(self):
    calls = []

    def boom(s, b):
      calls.append(1)
      raise OSError("smtp down")
    m = self.Mailer(self.ledger, cred_path=self.d / "mail.json", send_fn=boom, retries=3, wait_s=0)
    self.assertFalse(m.notify("k1", "s", "b", background=False))
    self.assertEqual(len(calls), 3)

  def test_no_credentials_skips_and_logs(self):
    m = self.Mailer(self.ledger, cred_path=self.d / "missing.json", send_fn=lambda s, b: self.sent.append(s))
    self.assertFalse(m.entry(self.trendy, "BTCUSDT", self.pos()))
    self.assertEqual(self.sent, [])
    ev = self.ledger.rows("SELECT message FROM events")
    self.assertTrue(any("尚未設定寄信帳號" in r["message"] for r in ev))

  def test_engine_mail_errors_are_contained(self):
    from btc_ai.engine import Engine
    e = Engine(self.ledger, {}, mailer=object())
    e._mail(lambda: 1 / 0)  # must not raise
    Engine(self.ledger, {})._mail(lambda: 1 / 0)  # no mailer: not even called
