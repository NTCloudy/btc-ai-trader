"""CLI: python3 -m btc_ai run | weekly | status"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None) -> int:
  ap = argparse.ArgumentParser(prog="btc_ai")
  ap.add_argument("cmd", choices=["run", "weekly", "status", "publish"])
  ap.add_argument("--no-publish", action="store_true")
  ap.add_argument("--no-line", action="store_true")
  a = ap.parse_args(argv)
  from .data import Market
  from .ledger import Ledger
  from .service import Notifier, Publisher, build_state, weekly
  from .ais import COINS
  ledger = Ledger(str(ROOT / "data" / "btc.db"))
  markets = {s: Market(s) for s in COINS}
  notifier = None if a.no_line else Notifier(ledger)
  if a.cmd == "run":
    from .engine import Engine
    Engine(ledger, markets, None if a.no_publish else Publisher(ledger, markets), notifier).run_forever()
    return 0
  for m in markets.values():
    m.update()
  if a.cmd == "weekly":
    print("\n".join(weekly(ledger, markets, notifier)))
  elif a.cmd == "publish":
    Publisher(ledger, markets).publish()
  else:
    st = build_state(ledger, markets)
    print("  ".join(f"{c['coin']} {c['price']:,.6g}（{(c['change_24h'] or 0):+.1%}）" for c in st["coins"]))
    for x in st["ais"]:
      held = [f"{c['coin']} {'多' if c['position']['side'] == 1 else '空'} 未實現 {c['position']['unrealized']:+,.0f}"
              for c in x["coins"] if c["position"]]
      print(f"  {x['emoji']} {x['name']}：累計 {x['total']:+,.0f}｜{x['trades']} 筆｜{'、'.join(held) or '空手'}")
    print(f"  📦 買進持有（6 幣）：{st['bh']['value']:+,.0f}")
  return 0


if __name__ == "__main__":
  sys.exit(main())
