"""SQLite 数据层:每次调用开新连接(sqlite 开连接便宜,省掉线程问题)。"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Any

DB_PATH = os.environ.get("DENGJIE_DB", os.path.join(os.path.dirname(os.path.dirname(__file__)), "dengjie.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw_hash TEXT NOT NULL, salt TEXT NOT NULL,
  weekly_hours REAL NOT NULL DEFAULT 20, semester_start TEXT, canvas_token TEXT, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS upload_tokens(token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS goals(
  id INTEGER PRIMARY KEY, user_id INTEGER, group_id INTEGER, title TEXT NOT NULL, due TEXT, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY, user_id INTEGER, goal_id INTEGER, group_id INTEGER,
  title TEXT NOT NULL, source TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
  est_hours REAL NOT NULL DEFAULT 1, remaining_hours REAL NOT NULL DEFAULT 1,
  due TEXT, scheduled_date TEXT, external_id TEXT, note TEXT, created TEXT NOT NULL, done_at TEXT);
CREATE TABLE IF NOT EXISTS schedule_slots(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL, teacher TEXT, location TEXT,
  day INTEGER NOT NULL, slot_start INTEGER, slot_end INTEGER, weeks TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS progress_events(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, task_id INTEGER, action TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fatigue_events(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, date TEXT NOT NULL, remaining REAL);
CREATE TABLE IF NOT EXISTS groups(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, invite_code TEXT UNIQUE NOT NULL, owner_id INTEGER NOT NULL,
  invite_expires TEXT, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER NOT NULL, user_id INTEGER NOT NULL, joined TEXT NOT NULL,
  PRIMARY KEY(group_id, user_id));
"""


def _connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN mail_user TEXT",
    "ALTER TABLE users ADD COLUMN mail_pass TEXT",
    "ALTER TABLE goals ADD COLUMN status TEXT NOT NULL DEFAULT 'active'",   # active | achieved | dropped
    "ALTER TABLE goals ADD COLUMN settled_at TEXT",
    "ALTER TABLE users ADD COLUMN remind_hour INTEGER",
    "ALTER TABLE users ADD COLUMN last_remind TEXT",
    "CREATE TABLE IF NOT EXISTS grades(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, course TEXT NOT NULL, credit REAL, score REAL, term TEXT, UNIQUE(user_id, course, term))",
]


def init_db(path: str | None = None) -> None:
    with _connect(path) as conn:
        conn.executescript(SCHEMA)
        for sql in MIGRATIONS:  # 旧库补列;列已存在会报错,忽略
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                pass


@contextmanager
def conn(path: str | None = None):
    c = _connect(path)
    try:
        yield c
        c.commit()
    finally:
        c.close()


def rows(sql: str, params: tuple = (), path: str | None = None) -> list[dict[str, Any]]:
    with conn(path) as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def row(sql: str, params: tuple = (), path: str | None = None) -> dict[str, Any] | None:
    r = rows(sql, params, path)
    return r[0] if r else None


def run(sql: str, params: tuple = (), path: str | None = None) -> int:
    with conn(path) as c:
        cur = c.execute(sql, params)
        return cur.lastrowid or cur.rowcount
