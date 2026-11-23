"""Демо-аккаунты для показа клубной системы: студент и организатор Summit Seekers.

В отличие от демо-студентов из demo_people.py под этими двумя можно войти — чтобы на
показе пройти путь студента и организатора. Пароль по умолчанию задан здесь (репозиторий
публичный, поэтому считайте его общеизвестным); свой пароль — переменная DEMO_LOGIN_PASSWORD.
Отключить вход совсем — DEMO_LOGIN=0. Оба аккаунта помечены is_demo: в публичную статистику
не входят и удаляются вместе с остальными демо-данными. Организатор управляет только
клубом Summit Seekers.

Сид идемпотентный: при каждом старте досоздаёт только то, чего нет.
"""
import logging
import os
from datetime import datetime, timedelta

from bot.clubs import hike_key, notify, set_follow, when
from bot.db import Database
from bot.hikes import TZ
from bot.web_chat import _hash_password

logger = logging.getLogger(__name__)

STUDENT_EMAIL = "student.demo@example.com"
ORGANIZER_EMAIL = "summit.demo@example.com"
DEFAULT_STUDENT_PASSWORD = "PopleStudent2026"
DEFAULT_ORGANIZER_PASSWORD = "PopleClub2026"


def _ensure_account(db: Database, first: str, last: str, phone: str, email: str, password: str,
                    role: str, club_id: str = "", **student) -> int:
    row = db.conn.execute("SELECT id FROM web_users WHERE email = ?", (email,)).fetchone()
    digest, salt = _hash_password(password)
    if row is None:
        user_id = db.create_web_user(first, last, phone, email, digest, salt, student.get("gender", ""),
                                     student.get("course", ""), student.get("specialty", ""),
                                     student.get("nickname", ""), role=role)
    else:
        user_id = row["id"]
    with db.conn:   # пароль и роль приводим к текущим настройкам при каждом старте
        db.conn.execute("""UPDATE web_users SET password_hash = ?, password_salt = ?, email_verified = 1,
                           role = ?, club_id = ?, is_demo = 1 WHERE id = ?""",
                        (digest, salt, role, club_id, user_id))
    return user_id


def _ensure_event(db: Database, hike_id: str, **fields) -> None:
    if db.hikes.get(hike_id) is None:
        db.hikes.create(hike_id=hike_id, demo=True, **fields)


def _sign(db: Database, hike_id: str, user_id: int, name: str, phone: str, at: datetime, attended: bool = False) -> None:
    key = hike_key(user_id)
    if db.hikes.signup_of(hike_id, key) is None:
        # прошедшие события закрыты для записи, поэтому вставляем запись напрямую
        hike = db.hikes.get(hike_id)
        going, _ = db.hikes.counts(hike_id)
        status = "going" if going < hike.capacity else "waitlist"
        with db.conn:
            db.conn.execute(
                """INSERT INTO hike_signups (hike_id, user_id, full_name, phone, status, src, created_at)
                   VALUES (?, ?, ?, ?, ?, 'demo', ?)""",
                (hike_id, key, name, phone, status, at.isoformat(timespec="seconds")))
    if attended:
        db.hikes.set_attended(hike_id, key, True, at)


def seed_demo_club(db: Database) -> bool:
    if os.getenv("DEMO_LOGIN", "1") == "0":
        return False
    override = os.getenv("DEMO_LOGIN_PASSWORD", "")
    student_id = _ensure_account(
        db, "Айдана", "Демо", "+77990000101", STUDENT_EMAIL, override or DEFAULT_STUDENT_PASSWORD, "student",
        gender="female", course="2", specialty="Менеджмент", nickname="aidana_demo")
    _ensure_account(db, "Summit Seekers", "(демо-организатор)", "+77990000102", ORGANIZER_EMAIL,
                    override or DEFAULT_ORGANIZER_PASSWORD, "organizer", club_id="summit")

    today = datetime.now(TZ).replace(hour=7, minute=0, second=0, microsecond=0)
    past_day = today - timedelta(days=(today.weekday() + 1) % 7 + 7)       # воскресенье на прошлой неделе
    next_friday = (today + timedelta(days=(4 - today.weekday()) % 7 or 7)).replace(hour=18, minute=30)
    fmt = "%Y-%m-%dT%H:%M"
    _ensure_event(db, "summit-demo-past", club_id="summit", title="Осенний поход (прошедший)",
                  start_at=past_day.strftime(fmt), duration_hours=6, meeting_point="У главного входа AlmaU",
                  route="Лёгкий маршрут в горах Заилийского Алатау", capacity=15, price="Бесплатно", kind="hike")
    _ensure_event(db, "mafia-demo-1", club_id="mafia", title="Пятничная мафия",
                  start_at=next_friday.strftime(fmt), duration_hours=3, meeting_point="Антикафе, адрес у организатора",
                  route="Новичкам объясняют правила, опыт не нужен", capacity=6, price="Бесплатно", kind="game")

    extras = db.conn.execute(
        "SELECT id, first_name, last_name, phone FROM web_users WHERE is_demo = 1 AND role = 'student' AND id != ? "
        "ORDER BY id LIMIT 30", (student_id,)).fetchall()
    upcoming = [h for h in db.hikes.upcoming("summit") if h.demo]
    stamp = datetime.now(TZ) - timedelta(days=2)
    for i, u in enumerate(extras):
        name = f"{u['first_name']} {u['last_name']}"
        if i < 12:
            _sign(db, "summit-demo-past", u["id"], name, u["phone"], past_day - timedelta(days=3), attended=i < 9)
        if 12 <= i < 20 and upcoming:
            _sign(db, upcoming[0].id, u["id"], name, u["phone"], stamp)
        if 20 <= i < 26:
            _sign(db, "mafia-demo-1", u["id"], name, u["phone"], stamp)   # занимают все 6 мест

    set_follow(db, student_id, "summit", True)
    set_follow(db, student_id, "mafia", True)
    if upcoming:
        _sign(db, upcoming[0].id, student_id, "Айдана Демо", "+77990000101", stamp + timedelta(hours=3))
    _sign(db, "mafia-demo-1", student_id, "Айдана Демо", "+77990000101", stamp + timedelta(hours=4))  # лист ожидания
    if not db.conn.execute("SELECT 1 FROM notifications WHERE user_id = ?", (student_id,)).fetchone():
        if upcoming:   # чтобы колокольчик на показе не был пустым
            notify(db, student_id, f"Вы записаны на «{upcoming[0].title}» — {when(upcoming[0])}.", "#summit")
        notify(db, student_id, "Мест на «Пятничная мафия» нет — вы в листе ожидания. Сообщим, если место освободится.",
               "#events")
    logger.info("Демо-аккаунты клубной системы готовы: %s и %s", STUDENT_EMAIL, ORGANIZER_EMAIL)
    return True
