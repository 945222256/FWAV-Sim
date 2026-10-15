# -*- coding: utf-8 -*-
"""db.py — SQLite 数据库层：连接、建表、事务。

数据库是唯一事实源。写操作全部走事务，日志模式为预写日志，
多线程服务下读写互不阻塞。表结构由本文件唯一声明。
仅使用 Python 标准库。
"""
import os
import sqlite3
import contextlib

import settings

_local = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    pw_hash   TEXT NOT NULL,
    salt      TEXT NOT NULL,
    role      TEXT NOT NULL DEFAULT 'owner'
);
CREATE TABLE IF NOT EXISTS tokens (
    token_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    secret_hash TEXT NOT NULL,
    lanes      TEXT NOT NULL DEFAULT '["L1","L2","L3"]',
    revoked    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'open',
    opened_at     TEXT NOT NULL,
    closed_at     TEXT,
    closed_by     TEXT,
    owner         TEXT,
    lane          TEXT,
    close_criterion TEXT,
    note          TEXT,
    claimed_by    TEXT,
    claimed_at    TEXT,
    lease_until   TEXT,
    evidence      TEXT NOT NULL DEFAULT '[]',
    criteria      TEXT,
    replies       TEXT,
    blockers      TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS params (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL,
    unit   TEXT,
    status TEXT NOT NULL DEFAULT 'current',
    source TEXT,
    since  TEXT,
    note   TEXT,
    extra  TEXT
);
CREATE TABLE IF NOT EXISTS params_archive (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    param TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inbox (
    id          TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,
    decision    TEXT,
    overrides   TEXT NOT NULL DEFAULT '[]',
    submitted_at TEXT NOT NULL,
    lint        TEXT
);
CREATE TABLE IF NOT EXISTS reports (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    payload TEXT NOT NULL,
    reported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts  TEXT NOT NULL,
    action TEXT NOT NULL,
    item_id TEXT,
    actor  TEXT,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY,
    info     TEXT NOT NULL,
    last_heartbeat TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS config (
    key  TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_item ON events(item_id);
"""


def connect():
    """取线程局部连接。ThreadingHTTPServer 每请求一线程，连接不能跨线程共享。"""
    global _local
    import threading
    if _local is None:
        _local = threading.local()
    conn = getattr(_local, "conn", None)
    if conn is None:
        os.makedirs(os.path.dirname(settings.DB_PATH), exist_ok=True)
        conn = sqlite3.connect(settings.DB_PATH, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        _local.conn = conn
    return conn


def init_schema():
    conn = connect()
    with conn:
        conn.executescript(SCHEMA)
        _migrate_columns(conn)


_EXTRA_ITEM_COLUMNS = [
    ("conclusion", "TEXT"), ("criterion_check", "TEXT"),
    ("blocked_at", "TEXT"), ("blocked_by", "TEXT"), ("block_reason", "TEXT"),
    ("blocker_type", "TEXT"), ("block_needs", "TEXT"),
    ("unblock_decision", "TEXT"), ("unblocked_by", "TEXT"), ("unblocked_at", "TEXT"),
    ("depends_on", "TEXT"), ("partial_start", "TEXT"), ("claim_scope", "TEXT"),
    ("rationale", "TEXT"), ("line", "TEXT"), ("enables", "TEXT"),
    ("check_script", "TEXT"), ("check_result", "TEXT"),
]


def _migrate_columns(conn):
    """幂等加列：本地账本纪律升级带来的新字段，老库在线补齐。"""
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(items)")}
    for col, decl in _EXTRA_ITEM_COLUMNS:
        if col not in existing:
            conn.execute("ALTER TABLE items ADD COLUMN %s %s" % (col, decl))
    # I-130：params 表补 extra 列（存 graph/sample 等图结构嵌套字段）。
    # I-127 之后本地记录带嵌套 graph 节，云端扁平七列存不下，快照下行
    # 会把图字段冲掉；extra 列以 JSON 存全部非扁平字段。
    param_cols = {r["name"] for r in conn.execute("PRAGMA table_info(params)")}
    if "extra" not in param_cols:
        conn.execute("ALTER TABLE params ADD COLUMN extra TEXT")


@contextlib.contextmanager
def transaction():
    """写事务：入口取 IMMEDIATE 锁，异常整体回滚。"""
    conn = connect()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def query(sql, args=()):
    return connect().execute(sql, args).fetchall()


def query_one(sql, args=()):
    return connect().execute(sql, args).fetchone()


def server_seq():
    """事件流最新序号，代理用它判断是否需要拉快照。"""
    row = query_one("SELECT COALESCE(MAX(seq), 0) AS s FROM events")
    return row["s"]
