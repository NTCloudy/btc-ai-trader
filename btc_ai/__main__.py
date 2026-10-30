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
  ledger = Ledger(str(ROOT / "data" / "btc.db"))
  market = Market()
  notifier = None if a.no_line else Notifier(ledger)
  if a.cmd == "run":
    from .engine import Engine
    Engine(ledger, market, None if a.no_publish else Publisher(ledger, market), notifier).run_forever()
  elif a.cmd == "weekly":
    market.update()
    print("\n".join(weekly(ledger, market, notifier)))
  elif a.cmd == "publish":
    market.update()
    Publisher(ledger, market).publish()
  else:
    market.update()
    st = build_state(ledger, market)
    print(f"BTC {st['price']:,.0f}（24h {st['change_24h']:+.2%}）｜{'多頭' if st['regime'] == 'bull' else '空頭'}（200 日均線 {st['ma200d']:,.0f}）")
    for x in st["ais"]:
      p = x["position"]
      held = f"{'多' if p['side'] == 1 else '空'}單 {p['entry']:,.0f} 停損 {p['stop']:,.0f} 未實現 {p['unrealized']:+,.0f}" if p else "空手"
      print(f"  {x['emoji']} {x['name']}：累計 {x['total']:+,.0f}｜{x['trades']} 筆｜{held}｜參數 {x['params']}")
    print(f"  📦 買進持有：{st['bh']['value']:+,.0f}")
  return 0


if __name__ == "__main__":
  sys.exit(main())
