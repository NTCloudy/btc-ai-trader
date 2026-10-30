"""SQLite ledger: closed trades, equity snapshots, events, key/value state."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

UTC = timezone.utc


class Ledger:

  def __init__(self, path: str):
    self.conn = sqlite3.connect(path, check_same_thread=False)
    self.conn.row_factory = sqlite3.Row
    self.lock = threading.Lock()
    with self.lock:
      self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY, ai TEXT, symbol TEXT, side INTEGER, entry_t TEXT,
          entry REAL, exit_t TEXT, exit REAL, pnl REAL, why TEXT, params TEXT, UNIQUE(ai, symbol, entry_t, side));
        CREATE TABLE IF NOT EXISTS equity(ts TEXT, ai TEXT, value REAL);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, ts TEXT, ai TEXT, level TEXT, message TEXT);
        CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT);
      """)

  def rows(self, sql: str, args=()):
    with self.lock:
      return [dict(r) for r in self.conn.execute(sql, args)]

  def exec(self, sql: str, args=()):
    with self.lock:
      cur = self.conn.execute(sql, args)
      self.conn.commit()
      return cur

  def get(self, key: str, default=None):
    r = self.rows("SELECT value FROM state WHERE key=?", (key,))
    return json.loads(r[0]["value"]) if r else default

  def set(self, key: str, value) -> None:
    self.exec("INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (key, json.dumps(value, ensure_ascii=False, default=str)))

  def log(self, message: str, ai: str | None = None, level: str = "INFO") -> None:
    self.exec("INSERT INTO events(ts,ai,level,message) VALUES(?,?,?,?)",
              (datetime.now(UTC).isoformat(timespec="seconds"), ai, level, message))

  def add_trade(self, ai: str, symbol: str, side: int, entry_t, entry: float, exit_t, exit_: float, pnl: float,
                why: str, params: dict) -> bool:
    cur = self.exec("INSERT OR IGNORE INTO trades(ai,symbol,side,entry_t,entry,exit_t,exit,pnl,why,params) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (ai, symbol, side, entry_t.isoformat(), entry, exit_t.isoformat(), exit_, pnl, why, json.dumps(params)))
    return cur.rowcount == 1

  def realized(self, ai: str) -> float:
    return self.rows("SELECT COALESCE(SUM(pnl),0) s FROM trades WHERE ai=?", (ai,))[0]["s"]
