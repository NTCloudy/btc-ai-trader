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
