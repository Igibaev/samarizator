import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import data_dir


class Store:
    def __init__(self, path=None):
        self.path = Path(path or data_dir() / "meetings.sqlite3")
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS meetings (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT NOT NULL,
                    created TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new',
                    duration REAL DEFAULT 0, channels INTEGER DEFAULT 1,
                    settings TEXT NOT NULL, summary TEXT, note TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS segments (
                    id INTEGER PRIMARY KEY, meeting TEXT REFERENCES meetings(id),
                    start REAL NOT NULL, end REAL NOT NULL, speaker TEXT NOT NULL,
                    text TEXT NOT NULL, uncertain INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS segment_meeting ON segments(meeting, start);
                CREATE TABLE IF NOT EXISTS checkpoints (
                    meeting TEXT, phase TEXT, part INTEGER, data TEXT,
                    PRIMARY KEY(meeting, phase, part));
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(segments)")}
            if "review" not in columns:
                db.execute("ALTER TABLE segments ADD COLUMN review TEXT NOT NULL DEFAULT ''")
            if "retry_text" not in columns:
                db.execute("ALTER TABLE segments ADD COLUMN retry_text TEXT NOT NULL DEFAULT ''")
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def create(self, source, settings):
        from dataclasses import asdict

        mid = uuid.uuid4().hex
        source = Path(source).resolve(strict=True)
        with self.connect() as db:
            db.execute(
                "INSERT INTO meetings(id,title,source,created,settings) VALUES(?,?,?,?,?)",
                (
                    mid,
                    source.stem,
                    str(source),
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps(asdict(settings)),
                ),
            )
        return mid

    def meeting(self, mid):
        with self.connect() as db:
            row = db.execute("SELECT * FROM meetings WHERE id=?", (mid,)).fetchone()
        if not row:
            raise ValueError("Запись не найдена.")
        return dict(row)

    def meetings(self, query=""):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM meetings WHERE title LIKE ? OR summary LIKE ? OR EXISTS "
                    "(SELECT 1 FROM segments WHERE meeting=meetings.id AND text LIKE ?) "
                    "ORDER BY created DESC LIMIT 500",
                    (f"%{query}%", f"%{query}%", f"%{query}%"),
                )
            ]

    def update(self, mid, **values):
        allowed = {"status", "duration", "channels", "summary", "note", "error", "settings", "title"}
        if not values or not set(values) <= allowed:
            raise ValueError("Invalid meeting fields")
        with self.connect() as db:
            db.execute(
                f"UPDATE meetings SET {','.join(k + '=?' for k in values)} WHERE id=?",
                (*values.values(), mid),
            )

    def segments(self, mid, offset=0, limit=500, uncertain_only=False):
        clause = "meeting=? AND uncertain=1" if uncertain_only else "meeting=?"
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    f"SELECT * FROM segments WHERE {clause} ORDER BY start,id LIMIT ? OFFSET ?",
                    (mid, limit, offset),
                )
            ]

    def iter_segments(self, mid):
        offset = 0
        while rows := self.segments(mid, offset):
            yield from rows
            offset += len(rows)

    def last_segment(self, mid):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM segments WHERE meeting=? ORDER BY start DESC, id DESC LIMIT 1", (mid,)
            ).fetchone()
            return dict(row) if row else None

    def checkpoint(self, mid, phase, part):
        with self.connect() as db:
            row = db.execute(
                "SELECT data FROM checkpoints WHERE meeting=? AND phase=? AND part=?", (mid, phase, part)
            ).fetchone()
            return json.loads(row[0]) if row else None

    def save_chunk(self, mid, part, segments):
        # Transcript and checkpoint commit together: retry never duplicates a chunk.
        with self.connect() as db:
            if db.execute(
                "SELECT 1 FROM checkpoints WHERE meeting=? AND phase='asr' AND part=?", (mid, part)
            ).fetchone():
                return
            db.executemany(
                "INSERT INTO segments(meeting,start,end,speaker,text,uncertain,review) VALUES(?,?,?,?,?,?,?)",
                [
                    (
                        mid,
                        s["start"],
                        s["end"],
                        s["speaker"],
                        s["text"],
                        s.get("uncertain", 0),
                        s.get("review", ""),
                    )
                    for s in segments
                ],
            )
            db.execute("INSERT INTO checkpoints VALUES(?,?,?,?)", (mid, "asr", part, "true"))

    def save_checkpoint(self, mid, phase, part, data):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO checkpoints VALUES(?,?,?,?)",
                (mid, phase, part, json.dumps(data, ensure_ascii=False)),
            )

    def edit_segment(self, mid, sid, speaker, text):
        with self.connect() as db:
            db.execute(
                "UPDATE segments SET speaker=?,text=?,uncertain=0,review='',retry_text='' "
                "WHERE meeting=? AND id=?",
                (speaker.strip() or "Не определён", text.strip(), mid, sid),
            )
            db.execute("DELETE FROM checkpoints WHERE meeting=? AND phase LIKE 'summary%'", (mid,))
            db.execute("UPDATE meetings SET summary=NULL,status='review' WHERE id=?", (mid,))

    def save_retry(self, mid, sid, text):
        with self.connect() as db:
            db.execute(
                "UPDATE segments SET retry_text=? WHERE meeting=? AND id=?", (text, mid, sid)
            )

    def accept_retry(self, mid, sid):
        """Replace a segment's text with its retry only on explicit user action."""
        with self.connect() as db:
            row = db.execute(
                "SELECT retry_text FROM segments WHERE meeting=? AND id=?", (mid, sid)
            ).fetchone()
            if not row or not row["retry_text"]:
                raise ValueError("Нет повторного варианта для этой реплики.")
            db.execute(
                "UPDATE segments SET text=?,retry_text='',uncertain=0,review='' "
                "WHERE meeting=? AND id=?",
                (row["retry_text"], mid, sid),
            )
            db.execute("DELETE FROM checkpoints WHERE meeting=? AND phase LIKE 'summary%'", (mid,))
            db.execute("UPDATE meetings SET summary=NULL,status='review' WHERE id=?", (mid,))

    def recover(self):
        with self.connect() as db:
            db.execute(
                "UPDATE meetings SET status='interrupted',error='Предыдущий запуск прерван; можно продолжить.' "
                "WHERE status IN ('transcribing','summarizing')"
            )

    def delete(self, mid):
        with self.connect() as db:
            db.execute("DELETE FROM segments WHERE meeting=?", (mid,))
            db.execute("DELETE FROM checkpoints WHERE meeting=?", (mid,))
            db.execute("DELETE FROM meetings WHERE id=?", (mid,))

    def rename_speaker(self, mid, old, new):
        with self.connect() as db:
            db.execute(
                "UPDATE segments SET speaker=? WHERE meeting=? AND speaker=?",
                (new.strip() or "Не определён", mid, old),
            )
            db.execute("DELETE FROM checkpoints WHERE meeting=? AND phase LIKE 'summary%'", (mid,))
            db.execute("UPDATE meetings SET summary=NULL,status='review' WHERE id=?", (mid,))
