"""本地去重存储模块 — SQLite 记录已推送推文。"""

import hashlib
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "data" / "twitter_monitor.db"
RETENTION_DAYS = 30

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tweet_id TEXT,
    content_hash TEXT UNIQUE,
    content_preview TEXT,
    author TEXT,
    stock_name TEXT,
    stock_code TEXT,
    action TEXT,
    sent_at TEXT
)
"""


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    conn = _get_conn()
    try:
        conn.execute(CREATE_TABLE_SQL)
        conn.commit()
        logger.info("数据库初始化完成")
    finally:
        conn.close()


def _hash_content(content: str) -> str:
    return hashlib.md5(content.encode()).hexdigest()


def is_new(content: str) -> bool:
    h = _hash_content(content)
    conn = _get_conn()
    try:
        cur = conn.execute(
            "SELECT 1 FROM notifications WHERE content_hash = ?", (h,)
        )
        return cur.fetchone() is None
    finally:
        conn.close()


def mark_sent(
    tweet_id: str,
    content: str,
    author: str = "",
    stock_name: str = "",
    stock_code: str = "",
    action: str = "",
) -> None:
    h = _hash_content(content)
    preview = content[:200]
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO notifications
               (tweet_id, content_hash, content_preview, author, stock_name, stock_code, action, sent_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (tweet_id, h, preview, author, stock_name, stock_code, action,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
    finally:
        conn.close()


def cleanup_old() -> int:
    cutoff = (datetime.now() - timedelta(days=RETENTION_DAYS)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    conn = _get_conn()
    try:
        cur = conn.execute(
            "DELETE FROM notifications WHERE sent_at < ?", (cutoff,)
        )
        conn.commit()
        deleted = cur.rowcount
        if deleted:
            logger.info(f"清理了 {deleted} 条过期记录")
        return deleted
    finally:
        conn.close()
