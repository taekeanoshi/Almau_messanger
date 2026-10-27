"""SQLite-хранилище: профили, жалобы, баны, статистика диалогов и записи на ивенты."""
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from bot.hikes import HikeStore

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
CREATE TABLE IF NOT EXISTS web_users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name    TEXT NOT NULL,
    last_name     TEXT NOT NULL,
    phone         TEXT NOT NULL UNIQUE,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    email_verified INTEGER NOT NULL DEFAULT 0,  -- код из письма подтверждён хотя бы раз
    gender        TEXT NOT NULL,          -- 'male' | 'female'
    banned        INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS web_otp_codes (
    email       TEXT PRIMARY KEY,
    code_hash   TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS web_sessions (
    token       TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS web_dialogs (
    dialog_id   TEXT PRIMARY KEY,
    user_a      INTEGER NOT NULL,
    user_b      INTEGER NOT NULL,
    started_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS web_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dialog_id   TEXT NOT NULL,
    sender_id   INTEGER NOT NULL,
    text        TEXT NOT NULL,
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
        self._migrate()
        self.hikes = HikeStore(self.conn)   # походы партнёрского клуба, см. bot/hikes.py

    def _migrate(self) -> None:
        """CREATE TABLE IF NOT EXISTS не трогает уже существующую таблицу, даже если её
        схема устарела (например, БД пережила деплой со старой структурой web_users).
        Добираем недостающие колонки через ALTER TABLE, чтобы запись не падала с
        'no such column' на расхождении схемы."""
        column_migrations = {
            "web_users": {
                "email": "ALTER TABLE web_users ADD COLUMN email TEXT",
                "password_hash": "ALTER TABLE web_users ADD COLUMN password_hash TEXT",
                "password_salt": "ALTER TABLE web_users ADD COLUMN password_salt TEXT",
                "email_verified": "ALTER TABLE web_users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0",
            },
        }
        with self.conn:
            for table, migrations in column_migrations.items():
                existing = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}
                for column, sql in migrations.items():
                    if column not in existing:
                        self.conn.execute(sql)

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
            "web_users": q("SELECT COUNT(*) FROM web_users"),
            "web_dialogs": q("SELECT COUNT(*) FROM web_dialogs"),
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

    # ---------- веб-чат: аккаунты, сессии, диалоги (отдельно от Telegram-бота) ----------

    def create_web_user(self, first_name: str, last_name: str, phone: str, email: str,
                         password_hash: str, password_salt: str, gender: str) -> int:
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO web_users (first_name, last_name, phone, email, password_hash, password_salt, gender, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (first_name, last_name, phone, email, password_hash, password_salt, gender, _now()),
            )
        return cur.lastrowid

    def get_web_user_by_phone(self, phone: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM web_users WHERE phone = ?", (phone,)).fetchone()

    def get_web_user_by_email(self, email: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM web_users WHERE email = ?", (email,)).fetchone()

    def get_web_user(self, user_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM web_users WHERE id = ?", (user_id,)).fetchone()

    def mark_web_email_verified(self, email: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE web_users SET email_verified = 1 WHERE email = ?", (email,))

    # ---------- одноразовые коды для входа по почте ----------

    def save_otp_code(self, email: str, code_hash: str, expires_at: str) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO web_otp_codes (email, code_hash, expires_at, attempts, created_at)
                   VALUES (?, ?, ?, 0, ?)
                   ON CONFLICT(email) DO UPDATE SET
                       code_hash = excluded.code_hash,
                       expires_at = excluded.expires_at,
                       attempts = 0,
                       created_at = excluded.created_at""",
                (email, code_hash, expires_at, _now()),
            )

    def get_otp_code(self, email: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM web_otp_codes WHERE email = ?", (email,)).fetchone()

    def bump_otp_attempts(self, email: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE web_otp_codes SET attempts = attempts + 1 WHERE email = ?", (email,))

    def clear_otp_code(self, email: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM web_otp_codes WHERE email = ?", (email,))

    def create_web_session(self, token: str, user_id: int) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO web_sessions (token, user_id, created_at) VALUES (?, ?, ?)",
                              (token, user_id, _now()))

    def web_session_user(self, token: str) -> int | None:
        row = self.conn.execute("SELECT user_id FROM web_sessions WHERE token = ?", (token,)).fetchone()
        return row["user_id"] if row else None

    def delete_web_session(self, token: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM web_sessions WHERE token = ?", (token,))

    def create_web_dialog(self, dialog_id: str, user_a: int, user_b: int) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO web_dialogs (dialog_id, user_a, user_b, started_at) VALUES (?, ?, ?, ?)",
                (dialog_id, user_a, user_b, _now()),
            )

    def add_web_message(self, dialog_id: str, sender_id: int, text: str) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO web_messages (dialog_id, sender_id, text, created_at) VALUES (?, ?, ?, ?)",
                (dialog_id, sender_id, text, _now()),
            )
        return cur.lastrowid

    def web_messages_since(self, dialog_id: str, since_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM web_messages WHERE dialog_id = ? AND id > ? ORDER BY id", (dialog_id, since_id)
        ).fetchall()
