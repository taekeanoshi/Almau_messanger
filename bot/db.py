"""SQLite-хранилище: профили, жалобы, баны, статистика диалогов и записи на ивенты."""
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,           -- Telegram user id
    university  TEXT NOT NULL,
    course      TEXT NOT NULL,
    interests   TEXT NOT NULL DEFAULT '',      -- через запятую
    socials     TEXT NOT NULL DEFAULT '',
    banned      INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_id INTEGER NOT NULL,
    target_id   INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dialogs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_a      INTEGER NOT NULL,
    user_b      INTEGER NOT NULL,
    event_id    TEXT,
    started_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS event_going (
    user_id     INTEGER NOT NULL,
    event_id    TEXT NOT NULL,
    PRIMARY KEY (user_id, event_id)
);
CREATE TABLE IF NOT EXISTS site_leads (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    contact     TEXT NOT NULL,
    message     TEXT NOT NULL DEFAULT '',
    event_id    TEXT,
    created_at  TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class User:
    id: int
    university: str
    course: str
    interests: frozenset[str]
    socials: str
    banned: bool


class Database:
    def __init__(self, path: Path | str) -> None:
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # ---------- профиль ----------

    def get_user(self, user_id: int) -> User | None:
        row = self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            return None
        return User(
            id=row["id"],
            university=row["university"],
            course=row["course"],
            interests=frozenset(i for i in row["interests"].split(",") if i),
            socials=row["socials"],
            banned=bool(row["banned"]),
        )

    def save_profile(self, user_id: int, university: str, course: str, interests: set[str]) -> None:
        """Создаёт профиль или перезаписывает вуз, курс и интересы. Соцсети и бан не трогает."""
        with self.conn:
            self.conn.execute(
                """INSERT INTO users (id, university, course, interests, created_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       university = excluded.university,
                       course = excluded.course,
                       interests = excluded.interests""",
                (user_id, university, course, ",".join(sorted(interests)), _now()),
            )

    def set_interests(self, user_id: int, interests: set[str]) -> None:
        with self.conn:
            self.conn.execute("UPDATE users SET interests = ? WHERE id = ?",
                              (",".join(sorted(interests)), user_id))

    def set_socials(self, user_id: int, socials: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE users SET socials = ? WHERE id = ?", (socials, user_id))

    # ---------- модерация ----------

    def is_banned(self, user_id: int) -> bool:
        row = self.conn.execute("SELECT banned FROM users WHERE id = ?", (user_id,)).fetchone()
        return bool(row and row["banned"])

    def set_banned(self, user_id: int, banned: bool) -> bool:
        """Возвращает False, если такого пользователя нет."""
        with self.conn:
            cur = self.conn.execute("UPDATE users SET banned = ? WHERE id = ?", (int(banned), user_id))
        return cur.rowcount > 0

    def add_report(self, reporter_id: int, target_id: int) -> int:
        """Сохраняет жалобу и возвращает, сколько разных людей пожаловались на target_id."""
        with self.conn:
            self.conn.execute(
                "INSERT INTO reports (reporter_id, target_id, created_at) VALUES (?, ?, ?)",
                (reporter_id, target_id, _now()),
            )
        row = self.conn.execute(
            "SELECT COUNT(DISTINCT reporter_id) AS n FROM reports WHERE target_id = ?", (target_id,)
        ).fetchone()
        return row["n"]

    # ---------- статистика ----------

    def log_dialog(self, user_a: int, user_b: int, event_id: str | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO dialogs (user_a, user_b, event_id, started_at) VALUES (?, ?, ?, ?)",
                (user_a, user_b, event_id, _now()),
            )

    def stats(self) -> dict[str, int]:
        today = datetime.now(timezone.utc).date().isoformat()
        q = lambda sql, *args: self.conn.execute(sql, args).fetchone()[0]  # noqa: E731
        return {
            "users": q("SELECT COUNT(*) FROM users"),
            "banned": q("SELECT COUNT(*) FROM users WHERE banned = 1"),
            "dialogs_total": q("SELECT COUNT(*) FROM dialogs"),
            "dialogs_today": q("SELECT COUNT(*) FROM dialogs WHERE started_at >= ?", today),
            "event_dialogs": q("SELECT COUNT(*) FROM dialogs WHERE event_id IS NOT NULL"),
            "reports": q("SELECT COUNT(*) FROM reports"),
            "leads": q("SELECT COUNT(*) FROM site_leads"),
        }

    # ---------- ивенты ----------

    def set_going(self, user_id: int, event_id: str, going: bool) -> None:
        with self.conn:
            if going:
                self.conn.execute("INSERT OR IGNORE INTO event_going VALUES (?, ?)", (user_id, event_id))
            else:
                self.conn.execute("DELETE FROM event_going WHERE user_id = ? AND event_id = ?",
                                  (user_id, event_id))

    def is_going(self, user_id: int, event_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM event_going WHERE user_id = ? AND event_id = ?", (user_id, event_id)
        ).fetchone() is not None

    def going_count(self, event_id: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM event_going WHERE event_id = ?", (event_id,)
        ).fetchone()[0]

    # ---------- заявки с сайта (веб-форма «Связаться») ----------

    def add_lead(self, name: str, contact: str, message: str, event_id: str | None = None) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO site_leads (name, contact, message, event_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (name, contact, message, event_id, _now()),
            )
        return cur.lastrowid

    def leads_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM site_leads").fetchone()[0]

    def recent_leads(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM site_leads ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
