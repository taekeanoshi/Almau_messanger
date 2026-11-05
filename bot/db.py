"""SQLite-хранилище: профили, жалобы, баны, статистика диалогов и записи на ивенты."""
import json
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
    course        TEXT NOT NULL DEFAULT '',
    specialty     TEXT NOT NULL DEFAULT '',
    nickname      TEXT NOT NULL DEFAULT '',  -- анонимный ник, виден собеседнику вместо имени
    role          TEXT NOT NULL DEFAULT 'student',  -- 'student' | 'teacher' | 'admin'
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
    mode        TEXT NOT NULL DEFAULT 'anonymous',  -- 'anonymous' (подбор по полу) | 'direct' (чат по ФИО)
    started_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS web_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dialog_id   TEXT NOT NULL,
    sender_id   INTEGER NOT NULL,
    text        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'text',  -- 'text' | 'photo' | 'reveal'
    photo_id    INTEGER,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS web_photos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    dialog_id     TEXT NOT NULL,
    sender_id     INTEGER NOT NULL,
    content_type  TEXT NOT NULL,
    data          BLOB NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS courses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL,            -- короткий код дисциплины, напр. "CS101"
    title       TEXT NOT NULL,
    teacher_id  INTEGER,                  -- web_users.id преподавателя, может быть не назначен
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS enrollments (
    user_id           INTEGER NOT NULL,
    course_id         INTEGER NOT NULL,
    schedule_item_id  INTEGER,  -- выбранный студентом вариант расписания (если у дисциплины их несколько)
    created_at        TEXT NOT NULL,
    PRIMARY KEY (user_id, course_id)
);
CREATE TABLE IF NOT EXISTS schedule_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id   INTEGER NOT NULL,
    day_of_week INTEGER NOT NULL,         -- 0=понедельник … 6=воскресенье
    start_time  TEXT NOT NULL,            -- "HH:MM"
    end_time    TEXT NOT NULL,            -- "HH:MM"
    room        TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'лекция',  -- 'лекция' | 'практика' | 'лаб' | ...
    teacher_id  INTEGER,                  -- переопределяет courses.teacher_id для этого конкретного варианта
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS grades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    course_id   INTEGER NOT NULL,
    label       TEXT NOT NULL,            -- "РК1" | "РК2" | "Экзамен" | "Итог" | ...
    score       REAL NOT NULL,
    max_score   REAL NOT NULL DEFAULT 100,
    comment     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attendance (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_item_id  INTEGER NOT NULL,
    user_id           INTEGER NOT NULL,
    class_date        TEXT NOT NULL,      -- "YYYY-MM-DD" — конкретная дата пары
    present           INTEGER NOT NULL,   -- 1 = был(а), 0 = отсутствовал(а)
    marked_by         INTEGER NOT NULL,
    created_at        TEXT NOT NULL,
    UNIQUE(schedule_item_id, user_id, class_date)
);
CREATE TABLE IF NOT EXISTS staff_invite_keys (
    role        TEXT PRIMARY KEY,   -- 'teacher' | 'registrar'
    key_hash    TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assignments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id   INTEGER NOT NULL,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    due_at      TEXT,                 -- ISO-дата дедлайна, необязательно
    file_name   TEXT,
    file_type   TEXT,
    file_data   BLOB,
    created_by  INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS submissions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    text          TEXT NOT NULL DEFAULT '',
    file_name     TEXT,
    file_type     TEXT,
    file_data     BLOB,
    status        TEXT NOT NULL DEFAULT 'submitted',  -- 'submitted' | 'graded'
    grade_score   REAL,
    grade_comment TEXT NOT NULL DEFAULT '',
    submitted_at  TEXT NOT NULL,
    graded_at     TEXT,
    UNIQUE(assignment_id, user_id)
);
CREATE TABLE IF NOT EXISTS lessons (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id   INTEGER NOT NULL,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    video_url   TEXT NOT NULL DEFAULT '',  -- ссылка на видео (YouTube и т.п.) — само видео не храним
    file_name   TEXT,
    file_type   TEXT,
    file_data   BLOB,
    created_by  INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quiz_questions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    lesson_id     INTEGER NOT NULL,
    text          TEXT NOT NULL,
    options       TEXT NOT NULL,      -- JSON-список вариантов ответа
    correct_index INTEGER NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quiz_attempts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    lesson_id     INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    answers       TEXT NOT NULL,      -- JSON-список выбранных индексов, по порядку вопросов
    correct_count INTEGER NOT NULL,
    total_count   INTEGER NOT NULL,
    score         REAL NOT NULL,      -- 0..100
    submitted_at  TEXT NOT NULL,
    UNIQUE(lesson_id, user_id)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AttendanceLocked(Exception):
    """Студент уже на автоматическом ретейке (>30% пропусков) — новую отметку посещаемости
    поставить нельзя, можно только править уже существующие записи."""


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
                "course": "ALTER TABLE web_users ADD COLUMN course TEXT NOT NULL DEFAULT ''",
                "specialty": "ALTER TABLE web_users ADD COLUMN specialty TEXT NOT NULL DEFAULT ''",
                "nickname": "ALTER TABLE web_users ADD COLUMN nickname TEXT NOT NULL DEFAULT ''",
                "role": "ALTER TABLE web_users ADD COLUMN role TEXT NOT NULL DEFAULT 'student'",
            },
            "web_messages": {
                "kind": "ALTER TABLE web_messages ADD COLUMN kind TEXT NOT NULL DEFAULT 'text'",
                "photo_id": "ALTER TABLE web_messages ADD COLUMN photo_id INTEGER",
            },
            "web_dialogs": {
                "mode": "ALTER TABLE web_dialogs ADD COLUMN mode TEXT NOT NULL DEFAULT 'anonymous'",
            },
            "enrollments": {
                "schedule_item_id": "ALTER TABLE enrollments ADD COLUMN schedule_item_id INTEGER",
            },
            "schedule_items": {
                "teacher_id": "ALTER TABLE schedule_items ADD COLUMN teacher_id INTEGER",
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
                         password_hash: str, password_salt: str, gender: str,
                         course: str, specialty: str, nickname: str, role: str = "student") -> int:
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO web_users (first_name, last_name, phone, email, password_hash, password_salt,
                                          gender, course, specialty, nickname, role, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (first_name, last_name, phone, email, password_hash, password_salt, gender,
                 course, specialty, nickname, role, _now()),
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

    def create_web_dialog(self, dialog_id: str, user_a: int, user_b: int, mode: str = "anonymous") -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO web_dialogs (dialog_id, user_a, user_b, mode, started_at) VALUES (?, ?, ?, ?, ?)",
                (dialog_id, user_a, user_b, mode, _now()),
            )

    def get_web_dialog(self, dialog_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM web_dialogs WHERE dialog_id = ?", (dialog_id,)).fetchone()

    def find_direct_dialog(self, user_a: int, user_b: int) -> sqlite3.Row | None:
        """Прямой (по ФИО) диалог между двумя конкретными пользователями — один на пару,
        в отличие от анонимных диалогов, которых может быть много подряд."""
        return self.conn.execute(
            """SELECT * FROM web_dialogs WHERE mode = 'direct'
               AND ((user_a = ? AND user_b = ?) OR (user_a = ? AND user_b = ?))""",
            (user_a, user_b, user_b, user_a),
        ).fetchone()

    def list_direct_dialogs(self, user_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT * FROM web_dialogs WHERE mode = 'direct' AND (user_a = ? OR user_b = ?)
               ORDER BY started_at DESC""",
            (user_id, user_id),
        ).fetchall()

    def search_web_users(self, query: str, exclude_id: int, limit: int = 100) -> list[sqlite3.Row]:
        """Поиск по ФИО для прямого чата — не анонимного: возвращает только незабаненных,
        без самого себя. Пустой запрос — весь список зарегистрированных (как справочник).

        Фильтруем в Python, а не через SQL LIKE: SQLite по умолчанию регистронезависимо
        сравнивает только ASCII-латиницу, а казахская/русская кириллица (включая Ә, Қ, Ң,
        Ғ, Ү, Ұ, Һ, І) с LIKE просто не матчилась бы при разном регистре."""
        rows = self.conn.execute(
            "SELECT * FROM web_users WHERE banned = 0 AND id != ? ORDER BY first_name, last_name",
            (exclude_id,),
        ).fetchall()
        q = query.strip().casefold()
        if not q:
            return rows[:limit]
        out = []
        for row in rows:
            full_name = f"{row['first_name']} {row['last_name']}".casefold()
            if q in full_name:
                out.append(row)
                if len(out) >= limit:
                    break
        return out

    def add_web_message(self, dialog_id: str, sender_id: int, text: str,
                         kind: str = "text", photo_id: int | None = None) -> int:
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO web_messages (dialog_id, sender_id, text, kind, photo_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (dialog_id, sender_id, text, kind, photo_id, _now()),
            )
        return cur.lastrowid

    def web_messages_since(self, dialog_id: str, since_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM web_messages WHERE dialog_id = ? AND id > ? ORDER BY id", (dialog_id, since_id)
        ).fetchall()

    # ---------- фото в веб-чате ----------

    def add_web_photo(self, dialog_id: str, sender_id: int, content_type: str, data: bytes) -> int:
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO web_photos (dialog_id, sender_id, content_type, data, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (dialog_id, sender_id, content_type, data, _now()),
            )
        return cur.lastrowid

    def get_web_photo(self, photo_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM web_photos WHERE id = ?", (photo_id,)).fetchone()

    # ---------- учёба: дисциплины, расписание, оценки ----------

    def set_web_user_role(self, email: str, role: str) -> bool:
        with self.conn:
            cur = self.conn.execute("UPDATE web_users SET role = ? WHERE email = ?", (role, email))
        return cur.rowcount > 0

    def create_course(self, code: str, title: str, description: str = "", teacher_id: int | None = None) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO courses (code, title, teacher_id, description, created_at) VALUES (?, ?, ?, ?, ?)",
                (code, title, teacher_id, description, _now()),
            )
        return cur.lastrowid

    def list_courses(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM courses ORDER BY code").fetchall()

    def get_course(self, course_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM courses WHERE id = ?", (course_id,)).fetchone()

    def courses_taught_by(self, teacher_id: int) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM courses WHERE teacher_id = ? ORDER BY code", (teacher_id,)).fetchall()

    def enroll(self, user_id: int, course_id: int, schedule_item_id: int | None = None) -> None:
        """Если у дисциплины ровно один вариант расписания, выбираем его автоматически.
        Если вариантов несколько, schedule_item_id обязателен — вызывающий код должен
        это проверить (см. academics.enroll) и явно передать выбор студента."""
        if schedule_item_id is None:
            items = self.schedule_for_course(course_id)
            if len(items) == 1:
                schedule_item_id = items[0]["id"]
        with self.conn:
            self.conn.execute(
                """INSERT INTO enrollments (user_id, course_id, schedule_item_id, created_at) VALUES (?, ?, ?, ?)
                   ON CONFLICT(user_id, course_id) DO NOTHING""",
                (user_id, course_id, schedule_item_id, _now()),
            )

    def unenroll(self, user_id: int, course_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM enrollments WHERE user_id = ? AND course_id = ?", (user_id, course_id))

    def is_enrolled(self, user_id: int, course_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM enrollments WHERE user_id = ? AND course_id = ?", (user_id, course_id)
        ).fetchone()
        return row is not None

    def get_enrollment(self, user_id: int, course_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM enrollments WHERE user_id = ? AND course_id = ?", (user_id, course_id)
        ).fetchone()

    def set_enrollment_schedule(self, user_id: int, course_id: int, schedule_item_id: int) -> bool:
        """Меняет выбранный студентом вариант расписания — сам студент (из открытых
        вариантов своей дисциплины) или отдел расписания (registrar/admin) за него."""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE enrollments SET schedule_item_id = ? WHERE user_id = ? AND course_id = ?",
                (schedule_item_id, user_id, course_id),
            )
        return cur.rowcount > 0

    def courses_for_student(self, user_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT c.* FROM courses c JOIN enrollments e ON e.course_id = c.id
               WHERE e.user_id = ? ORDER BY c.code""",
            (user_id,),
        ).fetchall()

    def roster(self, course_id: int) -> list[sqlite3.Row]:
        """Студенты, записанные на дисциплину, вместе с их выбранным вариантом расписания —
        для преподавателя/админа/отдела расписания."""
        return self.conn.execute(
            """SELECT u.*, e.schedule_item_id AS enrollment_schedule_item_id FROM web_users u
               JOIN enrollments e ON e.user_id = u.id
               WHERE e.course_id = ? ORDER BY u.first_name, u.last_name""",
            (course_id,),
        ).fetchall()

    def roster_for_schedule_item(self, schedule_item_id: int) -> list[sqlite3.Row]:
        """Студенты, выбравшие именно этот вариант расписания — те, кого реально
        отмечает на посещаемости преподаватель этого варианта."""
        return self.conn.execute(
            """SELECT u.* FROM web_users u JOIN enrollments e ON e.user_id = u.id
               WHERE e.schedule_item_id = ? ORDER BY u.first_name, u.last_name""",
            (schedule_item_id,),
        ).fetchall()

    def add_schedule_item(self, course_id: int, day_of_week: int, start_time: str, end_time: str,
                           room: str = "", kind: str = "лекция", teacher_id: int | None = None) -> int:
        """Если это первый (единственный) вариант расписания у дисциплины, сразу
        подставляем его всем, кто уже записался раньше — им выбирать не из чего, незачем
        заставлять их заходить и подтверждать то единственное, что есть."""
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO schedule_items (course_id, day_of_week, start_time, end_time, room, kind,
                                               teacher_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (course_id, day_of_week, start_time, end_time, room, kind, teacher_id, _now()),
            )
            item_id = cur.lastrowid
            still_only_one = self.conn.execute(
                "SELECT COUNT(*) FROM schedule_items WHERE course_id = ?", (course_id,)
            ).fetchone()[0] == 1
            if still_only_one:
                self.conn.execute(
                    "UPDATE enrollments SET schedule_item_id = ? WHERE course_id = ? AND schedule_item_id IS NULL",
                    (item_id, course_id),
                )
        return item_id

    def set_schedule_item_teacher(self, item_id: int, teacher_id: int | None) -> None:
        with self.conn:
            self.conn.execute("UPDATE schedule_items SET teacher_id = ? WHERE id = ?", (teacher_id, item_id))

    def delete_schedule_item(self, item_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM schedule_items WHERE id = ?", (item_id,))
            # у кого этот вариант был выбран — выбор сбрасывается, не оставляем битую ссылку
            self.conn.execute("UPDATE enrollments SET schedule_item_id = NULL WHERE schedule_item_id = ?", (item_id,))

    def get_schedule_item(self, item_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM schedule_items WHERE id = ?", (item_id,)).fetchone()

    def schedule_for_course(self, course_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM schedule_items WHERE course_id = ? ORDER BY day_of_week, start_time", (course_id,)
        ).fetchall()

    def schedule_for_student(self, user_id: int) -> list[sqlite3.Row]:
        """Только выбранный студентом вариант по каждой дисциплине — не все варианты сразу."""
        return self.conn.execute(
            """SELECT s.*, c.code AS course_code, c.title AS course_title FROM enrollments e
               JOIN schedule_items s ON s.id = e.schedule_item_id
               JOIN courses c ON c.id = e.course_id
               WHERE e.user_id = ? ORDER BY s.day_of_week, s.start_time""",
            (user_id,),
        ).fetchall()

    def set_grade(self, user_id: int, course_id: int, label: str, score: float,
                  max_score: float = 100, comment: str = "") -> int:
        """Выставляет оценку. Повторная проставление той же метки (напр. «РК1») тому же
        студенту по той же дисциплине обновляет существующую запись, а не плодит дубликаты."""
        with self.conn:
            existing = self.conn.execute(
                "SELECT id FROM grades WHERE user_id = ? AND course_id = ? AND label = ?",
                (user_id, course_id, label),
            ).fetchone()
            if existing:
                self.conn.execute(
                    "UPDATE grades SET score = ?, max_score = ?, comment = ? WHERE id = ?",
                    (score, max_score, comment, existing["id"]),
                )
                return existing["id"]
            cur = self.conn.execute(
                """INSERT INTO grades (user_id, course_id, label, score, max_score, comment, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (user_id, course_id, label, score, max_score, comment, _now()),
            )
            return cur.lastrowid

    def grades_for_student(self, user_id: int, course_id: int | None = None) -> list[sqlite3.Row]:
        if course_id is None:
            return self.conn.execute(
                """SELECT g.*, c.code AS course_code, c.title AS course_title FROM grades g
                   JOIN courses c ON c.id = g.course_id
                   WHERE g.user_id = ? ORDER BY c.code, g.created_at""",
                (user_id,),
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM grades WHERE user_id = ? AND course_id = ? ORDER BY created_at",
            (user_id, course_id),
        ).fetchall()

    def grades_for_course(self, course_id: int) -> list[sqlite3.Row]:
        """Все оценки по дисциплине (все студенты) — для ведомости преподавателя."""
        return self.conn.execute(
            "SELECT * FROM grades WHERE course_id = ? ORDER BY user_id, created_at", (course_id,)
        ).fetchall()

    # ---------- посещаемость и автоматический ретейк ----------

    RETAKE_ABSENCE_RATIO = 0.30  # больше 30% пропусков — автоматический ретейк

    def attendance_stats(self, schedule_item_id: int, user_id: int) -> dict:
        row = self.conn.execute(
            """SELECT COUNT(*) AS total, SUM(CASE WHEN present = 0 THEN 1 ELSE 0 END) AS absent
               FROM attendance WHERE schedule_item_id = ? AND user_id = ?""",
            (schedule_item_id, user_id),
        ).fetchone()
        total = row["total"] or 0
        absent = row["absent"] or 0
        ratio = (absent / total) if total else 0.0
        return {"total": total, "absent": absent, "ratio": ratio,
                "on_retake": total > 0 and ratio > self.RETAKE_ABSENCE_RATIO}

    def set_attendance(self, schedule_item_id: int, user_id: int, class_date: str,
                        present: bool, marked_by: int) -> None:
        """Отмечает присутствие/отсутствие на конкретную дату. Правка уже существующей
        отметки (в том числе за прошлые недели) разрешена всегда — так преподаватель
        может снять или добавить пропуск задним числом. А вот НОВУЮ отметку поставить
        нельзя, если студент уже на ретейке (>30% пропусков) по этому варианту расписания —
        это и значит «больше его отметить нельзя»."""
        existing = self.conn.execute(
            "SELECT id FROM attendance WHERE schedule_item_id = ? AND user_id = ? AND class_date = ?",
            (schedule_item_id, user_id, class_date),
        ).fetchone()
        if existing is None and self.attendance_stats(schedule_item_id, user_id)["on_retake"]:
            raise AttendanceLocked("student is on retake — no new attendance marks allowed")
        with self.conn:
            self.conn.execute(
                """INSERT INTO attendance (schedule_item_id, user_id, class_date, present, marked_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(schedule_item_id, user_id, class_date)
                   DO UPDATE SET present = excluded.present, marked_by = excluded.marked_by""",
                (schedule_item_id, user_id, class_date, int(present), marked_by, _now()),
            )

    def delete_attendance(self, schedule_item_id: int, user_id: int, class_date: str) -> None:
        with self.conn:
            self.conn.execute(
                "DELETE FROM attendance WHERE schedule_item_id = ? AND user_id = ? AND class_date = ?",
                (schedule_item_id, user_id, class_date),
            )

    def attendance_for_student(self, schedule_item_id: int, user_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM attendance WHERE schedule_item_id = ? AND user_id = ? ORDER BY class_date",
            (schedule_item_id, user_id),
        ).fetchall()

    # ---------- ключи-приглашения для регистрации преподавателей и отдела расписания ----------

    def set_invite_key(self, role: str, key_hash: str) -> None:
        with self.conn:
            self.conn.execute(
                """INSERT INTO staff_invite_keys (role, key_hash, created_at) VALUES (?, ?, ?)
                   ON CONFLICT(role) DO UPDATE SET key_hash = excluded.key_hash, created_at = excluded.created_at""",
                (role, key_hash, _now()),
            )

    def get_invite_key_hash(self, role: str) -> str | None:
        row = self.conn.execute("SELECT key_hash FROM staff_invite_keys WHERE role = ?", (role,)).fetchone()
        return row["key_hash"] if row else None

    # ---------- задания и сдачи (канал препод↔студент) ----------

    def create_assignment(self, course_id: int, title: str, description: str, due_at: str | None,
                           created_by: int, file_name: str | None = None, file_type: str | None = None,
                           file_data: bytes | None = None) -> int:
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO assignments (course_id, title, description, due_at, file_name, file_type,
                                            file_data, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (course_id, title, description, due_at, file_name, file_type, file_data, created_by, _now()),
            )
        return cur.lastrowid

    def get_assignment(self, assignment_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()

    def assignments_for_course(self, course_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM assignments WHERE course_id = ? ORDER BY created_at DESC", (course_id,)
        ).fetchall()

    def delete_assignment(self, assignment_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM submissions WHERE assignment_id = ?", (assignment_id,))
            self.conn.execute("DELETE FROM assignments WHERE id = ?", (assignment_id,))

    def upsert_submission(self, assignment_id: int, user_id: int, text: str,
                           file_name: str | None = None, file_type: str | None = None,
                           file_data: bytes | None = None) -> int:
        """Пересдача до проверки — обычное дело: повторная отправка просто заменяет
        прошлую (включая файл) и сбрасывает оценку, если она уже была выставлена."""
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO submissions (assignment_id, user_id, text, file_name, file_type, file_data,
                                            status, submitted_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'submitted', ?)
                   ON CONFLICT(assignment_id, user_id) DO UPDATE SET
                       text = excluded.text, file_name = excluded.file_name, file_type = excluded.file_type,
                       file_data = excluded.file_data, status = 'submitted', submitted_at = excluded.submitted_at,
                       grade_score = NULL, grade_comment = '', graded_at = NULL""",
                (assignment_id, user_id, text, file_name, file_type, file_data, _now()),
            )
        return cur.lastrowid

    def get_submission(self, submission_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM submissions WHERE id = ?", (submission_id,)).fetchone()

    def get_submission_for(self, assignment_id: int, user_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM submissions WHERE assignment_id = ? AND user_id = ?", (assignment_id, user_id)
        ).fetchone()

    def submissions_for_assignment(self, assignment_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT s.*, u.first_name, u.last_name FROM submissions s
               JOIN web_users u ON u.id = s.user_id
               WHERE s.assignment_id = ? ORDER BY s.submitted_at""",
            (assignment_id,),
        ).fetchall()

    def grade_submission(self, submission_id: int, score: float, comment: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE submissions SET status = 'graded', grade_score = ?, grade_comment = ?, graded_at = ? "
                "WHERE id = ?",
                (score, comment, _now(), submission_id),
            )

    # ---------- видеоуроки и мини-тесты ----------

    def create_lesson(self, course_id: int, title: str, description: str, video_url: str, created_by: int,
                       file_name: str | None = None, file_type: str | None = None,
                       file_data: bytes | None = None) -> int:
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO lessons (course_id, title, description, video_url, file_name, file_type,
                                        file_data, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (course_id, title, description, video_url, file_name, file_type, file_data, created_by, _now()),
            )
        return cur.lastrowid

    def get_lesson(self, lesson_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM lessons WHERE id = ?", (lesson_id,)).fetchone()

    def lessons_for_course(self, course_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM lessons WHERE course_id = ? ORDER BY created_at", (course_id,)
        ).fetchall()

    def delete_lesson(self, lesson_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM quiz_attempts WHERE lesson_id = ?", (lesson_id,))
            self.conn.execute("DELETE FROM quiz_questions WHERE lesson_id = ?", (lesson_id,))
            self.conn.execute("DELETE FROM lessons WHERE id = ?", (lesson_id,))

    def add_quiz_question(self, lesson_id: int, text: str, options: list[str], correct_index: int) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO quiz_questions (lesson_id, text, options, correct_index, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (lesson_id, text, json.dumps(options, ensure_ascii=False), correct_index, _now()),
            )
        return cur.lastrowid

    def get_quiz_question(self, question_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM quiz_questions WHERE id = ?", (question_id,)).fetchone()

    def delete_quiz_question(self, question_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM quiz_questions WHERE id = ?", (question_id,))

    def quiz_questions_for_lesson(self, lesson_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM quiz_questions WHERE lesson_id = ? ORDER BY id", (lesson_id,)
        ).fetchall()

    def submit_quiz_attempt(self, lesson_id: int, user_id: int, answers: list[int]) -> dict:
        """Авто-проверка: сверяет ответы с правильными индексами по порядку вопросов.
        Повторное прохождение (пересдача теста) просто заменяет прошлую попытку."""
        questions = self.quiz_questions_for_lesson(lesson_id)
        results = []
        correct_count = 0
        for i, q in enumerate(questions):
            selected = answers[i] if i < len(answers) else None
            is_correct = selected == q["correct_index"]
            if is_correct:
                correct_count += 1
            results.append({"question_id": q["id"], "text": q["text"], "options": json.loads(q["options"]),
                             "selected": selected, "correct_index": q["correct_index"], "is_correct": is_correct})
        total = len(questions)
        score = (correct_count / total * 100) if total else 0.0
        with self.conn:
            self.conn.execute(
                """INSERT INTO quiz_attempts (lesson_id, user_id, answers, correct_count, total_count, score,
                                              submitted_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(lesson_id, user_id) DO UPDATE SET
                       answers = excluded.answers, correct_count = excluded.correct_count,
                       total_count = excluded.total_count, score = excluded.score,
                       submitted_at = excluded.submitted_at""",
                (lesson_id, user_id, json.dumps(answers), correct_count, total, score, _now()),
            )
        return {"correct_count": correct_count, "total_count": total, "score": score, "results": results}

    def get_quiz_attempt(self, lesson_id: int, user_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM quiz_attempts WHERE lesson_id = ? AND user_id = ?", (lesson_id, user_id)
        ).fetchone()

    def quiz_attempts_for_lesson(self, lesson_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT a.*, u.first_name, u.last_name FROM quiz_attempts a
               JOIN web_users u ON u.id = a.user_id
               WHERE a.lesson_id = ? ORDER BY a.submitted_at""",
            (lesson_id,),
        ).fetchall()

    # ---------- обслуживание ----------

    WEB_CHAT_TABLES = ("web_sessions", "web_otp_codes", "web_photos", "web_messages", "web_dialogs", "web_users")

    def reset_web_chat_data(self) -> dict[str, int]:
        """Полностью стирает аккаунты, диалоги, сообщения, фото и коды из «Анонимного чата» —
        необратимо. Для очистки тестовых данных перед показом, через админ-эндпоинт с ADMIN_TOKEN."""
        counts: dict[str, int] = {}
        with self.conn:
            for table in self.WEB_CHAT_TABLES:
                counts[table] = self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                self.conn.execute(f"DELETE FROM {table}")
        return counts
