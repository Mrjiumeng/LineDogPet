# -*- coding: utf-8 -*-
"""
聊天记录持久化：单个 chat_history.db（SQLite，标准库自带，零依赖）。
与 config.json 同目录（data_dir），退出桌宠时批量写入，启动时回放最近 N 条。
"""
import sqlite3
import time
import os


class ChatDB:
    """聊天记录库。延迟建连接/建表，线程不安全——只在主线程用。"""

    def __init__(self, path):
        self.path = path
        self._conn = None

    def _ensure(self):
        if self._conn is not None:
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS messages ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "role TEXT NOT NULL, text TEXT NOT NULL, ts REAL NOT NULL)"
        )
        self._conn.commit()

    def add_many(self, rows):
        """批量追加 [(role, text, ts), ...]，退出时一次写盘。"""
        if not rows:
            return
        self._ensure()
        self._conn.executemany(
            "INSERT INTO messages(role, text, ts) VALUES(?, ?, ?)", rows
        )
        self._conn.commit()

    def recent(self, n=200):
        """返回最近 n 条，正序（旧→新），元素为 (role, text)。"""
        self._ensure()
        cur = self._conn.execute(
            "SELECT role, text FROM messages ORDER BY id DESC LIMIT ?", (n,)
        )
        rows = cur.fetchall()
        rows.reverse()
        return rows

    def trim(self, n=200):
        """只保留最近 n 条，删更旧的。"""
        self._ensure()
        self._conn.execute(
            "DELETE FROM messages WHERE id NOT IN "
            "(SELECT id FROM messages ORDER BY id DESC LIMIT ?)", (n,)
        )
        self._conn.commit()

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None