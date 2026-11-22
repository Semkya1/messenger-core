"""Тонкая обёртка над SQLite: потокобезопасные запросы, схема ядра и миграции."""
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL COLLATE NOCASE,
    display_name  TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    is_bot        INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chats (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    type       TEXT NOT NULL CHECK (type IN ('private', 'group')),
    title      TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_members (
    chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (chat_id, user_id)
);
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    user_id    INTEGER REFERENCES users(id),      -- NULL = системное сообщение
    text       TEXT NOT NULL,
    type       TEXT NOT NULL DEFAULT 'text',      -- text | voice | image | video | file | system
    meta       TEXT NOT NULL DEFAULT '{}',        -- JSON: вложение и данные плагинов
    deleted    INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, id);
CREATE TABLE IF NOT EXISTS files (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    token      TEXT UNIQUE NOT NULL,              -- имя файла на диске и часть URL
    owner_id   INTEGER NOT NULL REFERENCES users(id),
    name       TEXT NOT NULL,
    mime       TEXT NOT NULL,
    size       INTEGER NOT NULL,
    created_at REAL NOT NULL
);
"""

# Колонки, добавленные после первой версии. Для старых баз добавляются автоматически.
COLUMNS = [
    ("users", "bio", "TEXT NOT NULL DEFAULT ''"),
    ("users", "avatar", "TEXT"),                       # token файла
    ("users", "last_seen", "REAL"),
    ("chats", "avatar", "TEXT"),
    ("chats", "pinned_message_id", "INTEGER"),
    ("chat_members", "last_read_id", "INTEGER NOT NULL DEFAULT 0"),
    ("chat_members", "muted", "INTEGER NOT NULL DEFAULT 0"),
    ("messages", "reply_to", "INTEGER"),
    ("messages", "edited_at", "REAL"),
    ("messages", "forwarded_from", "TEXT"),
]


class DB:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.execute("PRAGMA foreign_keys = ON")
            self.conn.execute("PRAGMA journal_mode = WAL")
            self.conn.executescript(SCHEMA)
            for table, column, ddl in COLUMNS:
                self.ensure_column(table, column, ddl)
            self.conn.commit()

    def ensure_column(self, table, column, ddl):
        """Добавить колонку, если её нет. Удобно и для плагинов."""
        with self.lock:
            existing = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                self.conn.commit()

    def execute(self, sql, params=()):
        """INSERT/UPDATE/DELETE/CREATE. Возвращает курсор (cur.lastrowid, cur.rowcount)."""
        with self.lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def one(self, sql, params=()):
        with self.lock:
            row = self.conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def all(self, sql, params=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]
