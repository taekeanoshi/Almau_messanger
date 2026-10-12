"""Походы партнёрского клуба Summit Seekers: хранилище, записи, лист ожидания,
напоминания, безопасность («Я спустился») и отчёт для клуба.

Модуль не зависит от aiogram, поэтому всю логику можно проверять обычными тестами.
Время походов хранится строкой в часовом поясе Алматы (UTC+5): «2026-10-24T07:00».
"""
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Almaty")
PARTNER_CLUB_ID = "summit"
PARTNER_NAME = "Summit Seekers"
DIFFICULTIES = ("Лёгкий", "Средний", "Сложный")

REMIND_24H = timedelta(hours=24)
REMIND_3H = timedelta(hours=3)
SAFETY_DELAY = timedelta(minutes=90)   # через сколько после конца похода тревожим организатора
ACTIVE_BEFORE = timedelta(hours=1)     # «Я спустился» и SOS доступны за час до старта…
ACTIVE_AFTER = timedelta(hours=12)     # …и до 12 часов после конца
PERSONAL_DATA_DAYS = 30                # через столько дней после похода имена и телефоны удаляются

SCHEMA = """
CREATE TABLE IF NOT EXISTS hikes (
    id                  TEXT PRIMARY KEY,
    club_id             TEXT NOT NULL,
    title               TEXT NOT NULL,
    start_at            TEXT NOT NULL,
    duration_hours      REAL NOT NULL DEFAULT 6,
    meeting_point       TEXT NOT NULL DEFAULT '',
    route               TEXT NOT NULL DEFAULT '',
    difficulty          TEXT NOT NULL DEFAULT 'Лёгкий',
    capacity            INTEGER NOT NULL DEFAULT 20,
    price               TEXT NOT NULL DEFAULT 'Бесплатно',
    lat                 REAL,
    lon                 REAL,
    gear                TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'open',
    demo                INTEGER NOT NULL DEFAULT 0,
    checkin_prompt_sent INTEGER NOT NULL DEFAULT 0,
    safety_alert_sent   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS hike_signups (
    hike_id       TEXT NOT NULL,
    user_id       INTEGER NOT NULL,
    full_name     TEXT NOT NULL,
    phone         TEXT NOT NULL,
    status        TEXT NOT NULL,
    src           TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    checked_in_at TEXT,
    reminded_24h  INTEGER NOT NULL DEFAULT 0,
    reminded_3h   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (hike_id, user_id)
);
CREATE TABLE IF NOT EXISTS hike_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    hike_id    TEXT NOT NULL,
    user_id    INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    src        TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def now() -> datetime:
    return datetime.now(TZ)


@dataclass(frozen=True)
class Hike:
    id: str
    club_id: str
    title: str
    start_at: str
    duration_hours: float
    meeting_point: str
    route: str
    difficulty: str
    capacity: int
    price: str
    lat: float | None
    lon: float | None
    gear: tuple[str, ...]
    status: str
    demo: bool

    @property
    def start(self) -> datetime:
        return datetime.fromisoformat(self.start_at).replace(tzinfo=TZ)

    @property
    def end(self) -> datetime:
        return self.start + timedelta(hours=self.duration_hours)

    @property
    def is_open(self) -> bool:
        return self.status == "open"

    def is_active(self, at: datetime) -> bool:
        """Идёт ли поход (с запасом): в это время работают «Я спустился» и SOS."""
        return self.is_open and self.start - ACTIVE_BEFORE <= at <= self.end + ACTIVE_AFTER

    def public_dict(self, going: int, waitlist: int) -> dict:
        """Данные для сайта: без персональных данных участников."""
        return {
            "id": self.id, "club": self.club_id, "title": self.title, "start": self.start_at,
            "duration_hours": self.duration_hours, "meeting_point": self.meeting_point,
            "route": self.route, "difficulty": self.difficulty, "capacity": self.capacity,
            "price": self.price, "going": going, "waitlist": waitlist,
            "spots_left": max(self.capacity - going, 0), "demo": self.demo, "status": self.status,
        }


@dataclass(frozen=True)
class Signup:
    hike_id: str
    user_id: int
    full_name: str
    phone: str
    status: str            # going | waitlist
    src: str
    created_at: str
    checked_in: bool
    reminded_24h: bool
    reminded_3h: bool


# ---------- разбор того, что вводят люди ----------

_PHONE_RE = re.compile(r"^\+?\d{10,15}$")


def normalize_phone(raw: str) -> str | None:
    """«+7 (707) 123-45-67» → «+77071234567». Возвращает None, если это не похоже на номер."""
    cleaned = re.sub(r"[\s\-().]", "", raw or "")
    return cleaned if _PHONE_RE.match(cleaned) else None


def parse_start(text: str, at: datetime | None = None) -> str | None:
    """«24.10.2026 07:00» → «2026-10-24T07:00». None, если формат неверный или время в прошлом."""
    try:
        dt = datetime.strptime(text.strip(), "%d.%m.%Y %H:%M")
    except ValueError:
        return None
    if dt.replace(tzinfo=TZ) <= (at or now()):
        return None
    return dt.strftime("%Y-%m-%dT%H:%M")


def parse_duration(text: str) -> float | None:
    try:
        value = float(text.strip().replace(",", "."))
    except ValueError:
        return None
    return value if 0.5 <= value <= 24 else None


def parse_capacity(text: str) -> int | None:
    text = text.strip()
    return int(text) if text.isdigit() and 1 <= int(text) <= 500 else None


def parse_deep_link(payload: str) -> tuple[str, str]:
    """«hike_h20261024-1__ig» → («h20261024-1», «ig»)."""
    body = payload.removeprefix("hike_")
    hike_id, _, src = body.partition("__")
    return hike_id, src


# ---------- хранилище ----------

class HikeStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        conn.executescript(SCHEMA)

    # --- походы ---

    @staticmethod
    def _hike(row: sqlite3.Row) -> Hike:
        return Hike(
            id=row["id"], club_id=row["club_id"], title=row["title"], start_at=row["start_at"],
            duration_hours=row["duration_hours"], meeting_point=row["meeting_point"], route=row["route"],
            difficulty=row["difficulty"], capacity=row["capacity"], price=row["price"],
            lat=row["lat"], lon=row["lon"], gear=tuple(g for g in row["gear"].split("\n") if g),
            status=row["status"], demo=bool(row["demo"]),
        )

    def get(self, hike_id: str) -> Hike | None:
        row = self.conn.execute("SELECT * FROM hikes WHERE id = ?", (hike_id,)).fetchone()
        return self._hike(row) if row else None

    def create(self, *, club_id: str, title: str, start_at: str, duration_hours: float = 6,
               meeting_point: str = "", route: str = "", difficulty: str = "Лёгкий", capacity: int = 20,
               price: str = "Бесплатно", lat: float | None = None, lon: float | None = None,
               gear: tuple[str, ...] = (), demo: bool = False, hike_id: str | None = None) -> Hike:
        if hike_id is None:
            base, n = "h" + start_at[:10].replace("-", ""), 1
            while self.get(f"{base}-{n}"):
                n += 1
            hike_id = f"{base}-{n}"
        with self.conn:
            self.conn.execute(
                """INSERT INTO hikes (id, club_id, title, start_at, duration_hours, meeting_point, route,
                                      difficulty, capacity, price, lat, lon, gear, demo)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (hike_id, club_id, title, start_at, duration_hours, meeting_point, route, difficulty,
                 capacity, price, lat, lon, "\n".join(gear), int(demo)),
            )
        return self.get(hike_id)

    def seed(self, items: list[dict]) -> int:
        """Добавляет походы из hikes.json, которых ещё нет в базе. Уже созданные не трогает."""
        added = 0
        for it in items:
            if self.get(it["id"]):
                continue
            self.create(
                hike_id=it["id"], club_id=it.get("club", PARTNER_CLUB_ID), title=it["title"],
                start_at=it["start"], duration_hours=it.get("duration_hours", 6),
                meeting_point=it.get("meeting_point", ""), route=it.get("route", ""),
                difficulty=it.get("difficulty", "Лёгкий"), capacity=it.get("capacity", 20),
                price=it.get("price", "Бесплатно"), lat=it.get("lat"), lon=it.get("lon"),
                gear=tuple(it.get("gear", ())), demo=bool(it.get("demo", False)),
            )
            added += 1
        return added

    def upcoming(self, club_id: str | None = None, at: datetime | None = None) -> list[Hike]:
        """Открытые походы, которые ещё не закончились, по возрастанию даты."""
        at = at or now()
        return [h for h in self.for_club(club_id) if h.is_open and h.end >= at]

    def for_club(self, club_id: str | None = None) -> list[Hike]:
        if club_id:
            rows = self.conn.execute("SELECT * FROM hikes WHERE club_id = ?", (club_id,)).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM hikes").fetchall()
        return sorted((self._hike(r) for r in rows), key=lambda h: h.start)

    def cancel_hike(self, hike_id: str) -> bool:
        with self.conn:
            cur = self.conn.execute("UPDATE hikes SET status = 'cancelled' WHERE id = ?", (hike_id,))
        return cur.rowcount > 0

    # --- записи ---

    @staticmethod
    def _signup(row: sqlite3.Row) -> Signup:
        return Signup(
            hike_id=row["hike_id"], user_id=row["user_id"], full_name=row["full_name"], phone=row["phone"],
            status=row["status"], src=row["src"], created_at=row["created_at"],
            checked_in=row["checked_in_at"] is not None,
            reminded_24h=bool(row["reminded_24h"]), reminded_3h=bool(row["reminded_3h"]),
        )

    def signup_of(self, hike_id: str, user_id: int) -> Signup | None:
        row = self.conn.execute("SELECT * FROM hike_signups WHERE hike_id = ? AND user_id = ?",
                                (hike_id, user_id)).fetchone()
        return self._signup(row) if row else None

    def signups(self, hike_id: str) -> list[Signup]:
        """Сначала едущие, потом лист ожидания; внутри — в порядке записи."""
        rows = self.conn.execute(
            """SELECT * FROM hike_signups WHERE hike_id = ?
               ORDER BY CASE status WHEN 'going' THEN 0 ELSE 1 END, created_at, rowid""",
            (hike_id,),
        ).fetchall()
        return [self._signup(r) for r in rows]

    def counts(self, hike_id: str) -> tuple[int, int]:
        """(едут, в листе ожидания)"""
        rows = dict(self.conn.execute(
            "SELECT status, COUNT(*) FROM hike_signups WHERE hike_id = ? GROUP BY status", (hike_id,)
        ).fetchall())
        return rows.get("going", 0), rows.get("waitlist", 0)

    def waitlist_position(self, hike_id: str, user_id: int) -> int | None:
        waiting = [s.user_id for s in self.signups(hike_id) if s.status == "waitlist"]
        return waiting.index(user_id) + 1 if user_id in waiting else None

    def signup(self, hike_id: str, user_id: int, full_name: str, phone: str, src: str = "",
               at: datetime | None = None) -> str:
        """Записывает на поход. Результат: going | waitlist | exists | closed | unknown."""
        at = at or now()
        hike = self.get(hike_id)
        if hike is None:
            return "unknown"
        if not hike.is_open or hike.end < at:
            return "closed"
        with self.conn:
            if self.signup_of(hike_id, user_id):
                return "exists"
            going, _ = self.counts(hike_id)
            status = "going" if going < hike.capacity else "waitlist"
            stamp = at.isoformat(timespec="seconds")
            self.conn.execute(
                """INSERT INTO hike_signups (hike_id, user_id, full_name, phone, status, src, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (hike_id, user_id, full_name, phone, status, src, stamp),
            )
            self.conn.execute(
                "INSERT INTO hike_log (hike_id, user_id, kind, src, created_at) VALUES (?, ?, ?, ?, ?)",
                (hike_id, user_id, "signup" if status == "going" else "waitlist", src, stamp),
            )
        return status

    def cancel_signup(self, hike_id: str, user_id: int, at: datetime | None = None) -> tuple[bool, Signup | None]:
        """Отменяет запись и удаляет имя с телефоном. Возвращает (была ли запись, кого взяли из листа ожидания)."""
        stamp = (at or now()).isoformat(timespec="seconds")
        with self.conn:
            existing = self.signup_of(hike_id, user_id)
            if existing is None:
                return False, None
            self.conn.execute("DELETE FROM hike_signups WHERE hike_id = ? AND user_id = ?", (hike_id, user_id))
            self.conn.execute(
                "INSERT INTO hike_log (hike_id, user_id, kind, src, created_at) VALUES (?, ?, 'cancel', '', ?)",
                (hike_id, user_id, stamp),
            )
            promoted = None
            if existing.status == "going":
                waiting = [s for s in self.signups(hike_id) if s.status == "waitlist"]
                if waiting:
                    promoted = waiting[0]
                    self.conn.execute("UPDATE hike_signups SET status = 'going' WHERE hike_id = ? AND user_id = ?",
                                      (hike_id, promoted.user_id))
                    promoted = self.signup_of(hike_id, promoted.user_id)
        return True, promoted

    def active_for_user(self, user_id: int, at: datetime | None = None) -> list[tuple[Hike, Signup]]:
        """Походы, на которые пользователь едет и которые сейчас идут. Ближайший по старту — первый."""
        at = at or now()
        out = []
        for row in self.conn.execute("SELECT hike_id FROM hike_signups WHERE user_id = ? AND status = 'going'",
                                     (user_id,)).fetchall():
            hike = self.get(row["hike_id"])
            if hike and hike.is_active(at):
                out.append((hike, self.signup_of(hike.id, user_id)))
        return sorted(out, key=lambda pair: abs((pair[0].start - at).total_seconds()))

    # --- безопасность ---

    def check_in(self, hike_id: str, user_id: int, at: datetime | None = None) -> bool:
        stamp = (at or now()).isoformat(timespec="seconds")
        with self.conn:
            cur = self.conn.execute(
                """UPDATE hike_signups SET checked_in_at = ?
                   WHERE hike_id = ? AND user_id = ? AND status = 'going' AND checked_in_at IS NULL""",
                (stamp, hike_id, user_id),
            )
            if cur.rowcount:
                self.conn.execute(
                    "INSERT INTO hike_log (hike_id, user_id, kind, src, created_at) VALUES (?, ?, 'checkin', '', ?)",
                    (hike_id, user_id, stamp),
                )
        return cur.rowcount > 0

    def not_checked_in(self, hike_id: str) -> list[Signup]:
        return [s for s in self.signups(hike_id) if s.status == "going" and not s.checked_in]

    # --- фоновые задачи ---

    def due_reminders(self, at: datetime | None = None) -> list[tuple[Hike, Signup, str]]:
        """Кому пора отправить напоминание: '24h' (за сутки) или '3h' (за 3 часа)."""
        at = at or now()
        due = []
        for hike in self.upcoming(at=at):
            left = hike.start - at
            if left <= timedelta(0):
                continue
            for s in self.signups(hike.id):
                if s.status != "going":
                    continue
                if left <= REMIND_3H and not s.reminded_3h:
                    due.append((hike, s, "3h"))
                elif REMIND_3H < left <= REMIND_24H and not s.reminded_24h:
                    due.append((hike, s, "24h"))
        return due

    def mark_reminded(self, hike_id: str, user_id: int, kind: str) -> None:
        sql = ("UPDATE hike_signups SET reminded_24h = 1, reminded_3h = 1 WHERE hike_id = ? AND user_id = ?"
               if kind == "3h" else
               "UPDATE hike_signups SET reminded_24h = 1 WHERE hike_id = ? AND user_id = ?")
        with self.conn:
            self.conn.execute(sql, (hike_id, user_id))

    def due_checkin_prompts(self, at: datetime | None = None) -> list[tuple[Hike, list[Signup]]]:
        """Походы, которые закончились: участникам нужно предложить нажать «Я спустился»."""
        at = at or now()
        out = []
        for hike in self.for_club():
            if not hike.is_open or hike_flag(self.conn, hike.id, "checkin_prompt_sent"):
                continue
            if hike.end <= at < hike.end + ACTIVE_AFTER:
                out.append((hike, self.not_checked_in(hike.id)))
        return out

    def due_safety_alerts(self, at: datetime | None = None) -> list[tuple[Hike, list[Signup]]]:
        """Походы, после конца которых прошло SAFETY_DELAY: кто так и не отметился."""
        at = at or now()
        out = []
        for hike in self.for_club():
            if not hike.is_open or hike_flag(self.conn, hike.id, "safety_alert_sent"):
                continue
            if hike.end + SAFETY_DELAY <= at < hike.end + timedelta(hours=24):
                out.append((hike, self.not_checked_in(hike.id)))
        return out

    def mark_hike_flag(self, hike_id: str, flag: str) -> None:
        assert flag in ("checkin_prompt_sent", "safety_alert_sent")
        with self.conn:
            self.conn.execute(f"UPDATE hikes SET {flag} = 1 WHERE id = ?", (hike_id,))

    def purge_personal_data(self, at: datetime | None = None, days: int = PERSONAL_DATA_DAYS) -> int:
        """Удаляет имена и телефоны участников давно прошедших походов. Статистика в hike_log остаётся."""
        at = at or now()
        removed = 0
        with self.conn:
            for hike in self.for_club():
                if hike.end < at - timedelta(days=days):
                    removed += self.conn.execute("DELETE FROM hike_signups WHERE hike_id = ?", (hike.id,)).rowcount
        return removed

    def kv_get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def kv_set(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO kv (key, value) VALUES (?, ?) "
                              "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    # --- аналитика для клуба ---

    def log_view(self, hike_id: str, user_id: int, src: str = "", at: datetime | None = None) -> None:
        stamp = (at or now()).isoformat(timespec="seconds")
        with self.conn:
            self.conn.execute(
                "INSERT INTO hike_log (hike_id, user_id, kind, src, created_at) VALUES (?, ?, 'view', ?, ?)",
                (hike_id, user_id, src, stamp),
            )

    def last_view_src(self, hike_id: str, user_id: int) -> str:
        row = self.conn.execute(
            "SELECT src FROM hike_log WHERE hike_id = ? AND user_id = ? AND kind = 'view' ORDER BY id DESC LIMIT 1",
            (hike_id, user_id),
        ).fetchone()
        return row["src"] if row else ""

    def report(self, club_id: str) -> dict:
        """Сводка для руководителя клуба: по каждому походу и по источникам трафика."""
        def scalar(sql: str, *args) -> int:
            return self.conn.execute(sql, args).fetchone()[0]

        rows, totals = [], {"views": 0, "signups": 0, "waitlist": 0, "cancels": 0, "checkins": 0}
        for hike in self.for_club(club_id):
            item = {
                "hike": hike,
                "views": scalar("SELECT COUNT(DISTINCT user_id) FROM hike_log WHERE hike_id = ? AND kind = 'view'", hike.id),
                "signups": scalar("SELECT COUNT(DISTINCT user_id) FROM hike_log WHERE hike_id = ? AND kind = 'signup'", hike.id),
                "waitlist": scalar("SELECT COUNT(DISTINCT user_id) FROM hike_log WHERE hike_id = ? AND kind = 'waitlist'", hike.id),
                "cancels": scalar("SELECT COUNT(*) FROM hike_log WHERE hike_id = ? AND kind = 'cancel'", hike.id),
                "checkins": scalar("SELECT COUNT(*) FROM hike_log WHERE hike_id = ? AND kind = 'checkin'", hike.id),
            }
            item["going_now"], _ = self.counts(hike.id)
            rows.append(item)
            for key in totals:
                totals[key] += item[key]

        ids = [r["hike"].id for r in rows]
        sources: dict[str, dict[str, int]] = {}
        if ids:
            marks = ",".join("?" * len(ids))
            for src, kind, n in self.conn.execute(
                f"""SELECT src, kind, COUNT(DISTINCT hike_id || ':' || user_id) FROM hike_log
                    WHERE hike_id IN ({marks}) AND kind IN ('view', 'signup', 'waitlist') GROUP BY src, kind""",
                ids,
            ).fetchall():
                bucket = sources.setdefault(src, {"views": 0, "signups": 0})
                bucket["views" if kind == "view" else "signups"] += n
        return {"hikes": rows, "totals": totals, "sources": sources}


def hike_flag(conn: sqlite3.Connection, hike_id: str, flag: str) -> bool:
    return bool(conn.execute(f"SELECT {flag} FROM hikes WHERE id = ?", (hike_id,)).fetchone()[0])
