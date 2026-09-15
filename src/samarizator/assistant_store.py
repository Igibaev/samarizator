"""Private storage for the local desktop companion.

The companion deliberately uses a separate database from meeting transcripts.  A
bad migration or an experimental assistant feature must never put recordings at
risk.
"""

import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import data_dir


def utc_now():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Task:
    id: int
    title: str
    source_path: str | None
    source_line: int | None
    due_at: str | None
    status: str
    pinned: bool


class CompanionStore:
    def __init__(self, path=None):
        self.path = Path(path or data_dir() / "assistant.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    source_path TEXT,
                    source_line INTEGER,
                    origin_key TEXT UNIQUE,
                    due_at TEXT,
                    status TEXT NOT NULL DEFAULT 'open',
                    pinned INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS tasks_status_due ON tasks(status, due_at);
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS note_files (
                    path TEXT PRIMARY KEY,
                    mtime_ns INTEGER NOT NULL,
                    size INTEGER NOT NULL,
                    digest TEXT NOT NULL,
                    title TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS companion_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            try:
                db.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS note_fts "
                    "USING fts5(path UNINDEXED, heading, body, tokenize='unicode61')"
                )
            except sqlite3.OperationalError:
                # Some corporate Python builds omit FTS5.  LIKE search is slower but
                # keeps the assistant useful without a new binary dependency.
                db.execute(
                    "CREATE TABLE IF NOT EXISTS note_fts "
                    "(path TEXT NOT NULL, heading TEXT NOT NULL, body TEXT NOT NULL)"
                )
                db.execute("CREATE INDEX IF NOT EXISTS note_fts_path ON note_fts(path)")
        self.path.chmod(0o600)

    def prepare_vault(self, root):
        """Drop the old search index when the user selects another vault."""
        root = str(Path(root).resolve())
        with self.connect() as db:
            row = db.execute(
                "SELECT value FROM companion_meta WHERE key='vault_root'"
            ).fetchone()
            if row and row[0] != root:
                db.execute("DELETE FROM note_files")
                db.execute("DELETE FROM note_fts")
                db.execute("DELETE FROM tasks WHERE source_path IS NOT NULL AND pinned=0")
            db.execute(
                "INSERT INTO companion_meta(key,value) VALUES('vault_root',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (root,),
            )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _task(row):
        return Task(
            id=row["id"],
            title=row["title"],
            source_path=row["source_path"],
            source_line=row["source_line"],
            due_at=row["due_at"],
            status=row["status"],
            pinned=bool(row["pinned"]),
        )

    def add_task(self, title, due_at=None, pinned=False):
        title = " ".join(str(title).split())
        if not title or len(title) > 500:
            raise ValueError("Задача должна содержать от 1 до 500 символов.")
        now = utc_now()
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO tasks(title,due_at,pinned,created_at,updated_at) VALUES(?,?,?,?,?)",
                (title, due_at, int(pinned), now, now),
            )
            return cursor.lastrowid

    def tasks(self, include_done=False, limit=100):
        where = "1=1" if include_done else "status='open'"
        with self.connect() as db:
            rows = db.execute(
                f"SELECT * FROM tasks WHERE {where} "
                "ORDER BY pinned DESC, due_at IS NULL, due_at, updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._task(row) for row in rows]

    def set_task_status(self, task_id, status):
        if status not in {"open", "done", "dismissed"}:
            raise ValueError("Некорректный статус задачи.")
        with self.connect() as db:
            db.execute(
                "UPDATE tasks SET status=?,updated_at=? WHERE id=?",
                (status, utc_now(), task_id),
            )

    def toggle_pin(self, task_id):
        with self.connect() as db:
            db.execute(
                "UPDATE tasks SET pinned=CASE pinned WHEN 0 THEN 1 ELSE 0 END,updated_at=? WHERE id=?",
                (utc_now(), task_id),
            )

    def due_tasks(self, now=None):
        now = now or utc_now()
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM tasks WHERE status='open' AND due_at IS NOT NULL AND due_at<=? "
                "ORDER BY due_at",
                (now,),
            ).fetchall()
        return [self._task(row) for row in rows]

    def sync_note(self, path, mtime_ns, size, digest, title, chunks, tasks):
        """Replace one note's search rows and merge its checkbox tasks atomically."""
        now = utc_now()
        current_keys = {item["origin_key"] for item in tasks}
        with self.connect() as db:
            db.execute("DELETE FROM note_fts WHERE path=?", (path,))
            db.executemany(
                "INSERT INTO note_fts(path,heading,body) VALUES(?,?,?)",
                [(path, heading, body) for heading, body in chunks],
            )
            db.execute(
                "INSERT INTO note_files(path,mtime_ns,size,digest,title,updated_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET mtime_ns=excluded.mtime_ns,size=excluded.size,"
                "digest=excluded.digest,title=excluded.title,updated_at=excluded.updated_at",
                (path, mtime_ns, size, digest, title, now),
            )
            for item in tasks:
                db.execute(
                    "INSERT INTO tasks(title,source_path,source_line,origin_key,due_at,status,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(origin_key) DO UPDATE SET "
                    "title=excluded.title,source_line=excluded.source_line,due_at=excluded.due_at,"
                    "status=CASE WHEN excluded.status='done' THEN 'done' ELSE tasks.status END,"
                    "updated_at=excluded.updated_at",
                    (
                        item["title"],
                        path,
                        item["line"],
                        item["origin_key"],
                        item.get("due_at"),
                        item["status"],
                        now,
                        now,
                    ),
                )
            old = db.execute(
                "SELECT id,origin_key,pinned FROM tasks WHERE source_path=?", (path,)
            ).fetchall()
            for row in old:
                if row["origin_key"] not in current_keys and not row["pinned"]:
                    db.execute("DELETE FROM tasks WHERE id=?", (row["id"],))

    def note_signature(self, path):
        with self.connect() as db:
            row = db.execute(
                "SELECT mtime_ns,size,digest FROM note_files WHERE path=?", (path,)
            ).fetchone()
        return tuple(row) if row else None

    def remove_missing_notes(self, existing):
        existing = set(existing)
        with self.connect() as db:
            known = [row[0] for row in db.execute("SELECT path FROM note_files")]
            for path in known:
                if path not in existing:
                    db.execute("DELETE FROM note_files WHERE path=?", (path,))
                    db.execute("DELETE FROM note_fts WHERE path=?", (path,))
                    db.execute("DELETE FROM tasks WHERE source_path=? AND pinned=0", (path,))

    def remove_note(self, path):
        with self.connect() as db:
            db.execute("DELETE FROM note_files WHERE path=?", (path,))
            db.execute("DELETE FROM note_fts WHERE path=?", (path,))
            db.execute("DELETE FROM tasks WHERE source_path=? AND pinned=0", (path,))

    @staticmethod
    def _fts_enabled(db):
        row = db.execute(
            "SELECT sql FROM sqlite_master WHERE name='note_fts'"
        ).fetchone()
        return bool(row and "VIRTUAL TABLE" in (row[0] or "").upper())

    def search_notes(self, query, limit=6):
        tokens = [token for token in re.findall(r"[\w-]{2,}", query.casefold()) if len(token) < 80]
        if not tokens:
            return []
        with self.connect() as db:
            if self._fts_enabled(db):
                terms = []
                for token in tokens[:12]:
                    escaped = token.replace('"', '""')
                    terms.append(f'"{escaped}"')
                    # A tiny prefix fallback catches common Russian inflections such
                    # as карта/карту without a heavyweight morphology dependency.
                    if len(token) >= 5:
                        terms.append(f'"{escaped[:-1]}"*')
                expression = " OR ".join(terms)
                rows = db.execute(
                    "SELECT path,heading,body,bm25(note_fts) AS score FROM note_fts "
                    "WHERE note_fts MATCH ? ORDER BY score LIMIT ?",
                    (expression, limit),
                ).fetchall()
                return [dict(row) for row in rows]
            rows = db.execute("SELECT path,heading,body FROM note_fts").fetchall()
        scored = []
        for row in rows:
            haystack = (row["heading"] + "\n" + row["body"]).casefold()
            score = sum(
                haystack.count(token) + (haystack.count(token[:-1]) if len(token) >= 5 else 0)
                for token in tokens
            )
            if score:
                scored.append((score, dict(row)))
        return [row for _, row in sorted(scored, key=lambda item: item[0], reverse=True)[:limit]]

    def add_message(self, role, content):
        if role not in {"user", "assistant"}:
            raise ValueError("Некорректная роль сообщения.")
        content = str(content).strip()
        if not content:
            return
        with self.connect() as db:
            db.execute(
                "INSERT INTO messages(role,content,created_at) VALUES(?,?,?)",
                (role, content[:20_000], utc_now()),
            )
            # Bound local history; durable knowledge belongs in Obsidian, not chat logs.
            db.execute(
                "DELETE FROM messages WHERE id NOT IN (SELECT id FROM messages ORDER BY id DESC LIMIT 500)"
            )

    def recent_messages(self, limit=12):
        with self.connect() as db:
            rows = db.execute(
                "SELECT role,content FROM messages ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in reversed(rows)]
