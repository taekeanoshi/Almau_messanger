"""Учебный план AlmaU: специальности, дисциплины по курсам и преподаватели.

Данные — site/data/curriculum.json, собраны из файлов расписания учебного отдела (осенний
семестр 2026-2027, русское отделение). Из файлов взяты именно дисциплины каждой специальности
и курса и фамилии преподавателей; в какой день и в какое время идёт пара и кто из
преподавателей ведёт конкретный вариант, распределено случайно, но детерминированно (одно и то
же при каждом запуске).

Что делает модуль:
- при старте создаёт дисциплины каталога (по одной на название) с 3–4 вариантами расписания
  у разных преподавателей и в разные дни;
- записывает студента на дисциплины его специальности и курса, выбирая вариант без накладок
  по времени (при регистрации, при смене курса/специальности в профиле, при старте);
- демо-студентам (и аккаунтам из SHOWCASE_GRADES_FOR) ставит текущие оценки: ТК1, РК1, ТК2.
  РК2 и сессии ещё не было, поэтому их и итога нет.
"""
import hashlib
import json
import logging
import os
import random
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot.config import BASE_DIR
from bot.db import Database

logger = logging.getLogger(__name__)

CURRICULUM_PATH = BASE_DIR / "site" / "data" / "curriculum.json"
CATALOG_MARK = "Дисциплина из расписания AlmaU, осенний семестр 2026-2027."

# Метки оценок в ведомости. Итог = (РК1 + РК2) / 2 × 0,6 + Сессия × 0,4.
GRADE_LABELS = ("ТК1", "РК1", "ТК2", "РК2", "Сессия")
# Начало пары; пара — два академических часа по 50 минут с перерывом 10 минут.
SLOT_STARTS = ("08:00", "09:00", "10:00", "11:10", "12:10", "13:10", "14:20", "15:20", "16:20", "17:20")
PAIR_MINUTES = 110
ROOMS = ("204", "222", "247", "302", "305", "311", "318", "320", "326", "333", "409", "412", "426", "430",
         "а201", "а203", "а400", "а402", "а403", "а501", "а503", "а600")
DEFAULT_SHOWCASE = "Хасанов Хамид"


def _seed(*parts: object) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16)


def _end_time(start: str) -> str:
    h, m = map(int, start.split(":"))
    total = h * 60 + m + PAIR_MINUTES
    return f"{total // 60:02d}:{total % 60:02d}"


def _minutes(t: str) -> int:
    h, m = map(int, t.split(":"))
    return h * 60 + m


def _course_code(title: str, taken: set[str]) -> str:
    words = re.findall(r"[A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі0-9]+", title)
    letters = "".join(w[0] for w in words if len(w) > 2 or w.isdigit()).upper()[:4] or "ДИС"
    n = 100 + _seed(title) % 900
    while f"{letters}-{n}" in taken:
        n = 100 + (n - 99) % 900
    return f"{letters}-{n}"


class Curriculum:
    def __init__(self, path: Path | str = CURRICULUM_PATH) -> None:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("Учебный план %s не прочитан — расписание из файлов недоступно", path)
            data = {"specialties": [], "teachers": {}}
        self.specialties: dict[str, dict[str, list[str]]] = {s["name"]: s["courses"] for s in data["specialties"]}
        self.teachers: dict[str, list[str]] = data.get("teachers", {})
        self.source: str = data.get("source", "")

    # ---------- справочник для регистрации ----------

    def options(self) -> dict:
        """Курс → специальности, у которых в файлах есть расписание этого курса. На 4 курсе в
        файлах только МВД, поэтому там показываем все специальности (расписание появится позже)."""
        by_course = {c: sorted(n for n, cs in self.specialties.items() if c in cs) for c in ("1", "2", "3", "4")}
        all_names = sorted(self.specialties)
        return {"courses": {c: (names if c != "4" else all_names) for c, names in by_course.items()},
                "with_schedule": {c: names for c, names in by_course.items()}}

    def match_specialty(self, raw: str) -> str | None:
        """Сопоставляет свободно введённую специальность (старые аккаунты) с названием из плана."""
        q = (raw or "").strip().casefold()
        if not q:
            return None
        for name in self.specialties:
            if name.casefold() == q:
                return name
        for name in self.specialties:
            n = name.casefold()
            if q in n or n in q:
                return name
        return None

    def subjects(self, specialty: str, course: str) -> list[str]:
        name = self.match_specialty(specialty)
        return list(self.specialties.get(name, {}).get(str(course).strip(), [])) if name else []

    def all_subjects(self) -> list[str]:
        return sorted({s for cs in self.specialties.values() for subs in cs.values() for s in subs})

    def all_teachers(self) -> list[str]:
        return sorted({t for names in self.teachers.values() for t in names})


_curriculum: Curriculum | None = None


def get_curriculum() -> Curriculum:
    global _curriculum
    if _curriculum is None:
        _curriculum = Curriculum()
    return _curriculum


# ---------- преподаватели и каталог ----------

def _teacher_email(name: str) -> str:
    return f"teacher-{hashlib.sha256(name.encode()).hexdigest()[:10]}@example.com"


def ensure_teachers(db: Database, cur: Curriculum) -> dict[str, int]:
    """Аккаунты преподавателей из файлов. Войти под ними нельзя (пароль-заглушка), почта на
    example.com. Фамилия хранится в first_name, инициалы — в last_name, чтобы имя
    показывалось как в расписании: «Нуртазина А.О.»."""
    ids: dict[str, int] = {}
    for i, name in enumerate(cur.all_teachers()):
        email = _teacher_email(name)
        row = db.get_web_user_by_email(email)
        if row is None:
            surname, _, initials = name.partition(" ")
            db.create_web_user(surname, initials, f"+7701{_seed(name) % 10_000_000:07d}", email,
                               password_hash="!demo-" + secrets.token_hex(16), password_salt=secrets.token_hex(16),
                               gender="", course="", specialty="", nickname="", role="teacher")
            db.mark_web_email_verified(email)
            db.mark_web_user_demo(email)
            row = db.get_web_user_by_email(email)
        ids[name] = row["id"]
    return ids


def catalog_courses(db: Database) -> dict[str, int]:
    """Название → id дисциплины каталога."""
    return {r["title"]: r["id"] for r in db.conn.execute(
        "SELECT id, title FROM courses WHERE description = ?", (CATALOG_MARK,))}


def ensure_catalog(db: Database, cur: Curriculum | None = None) -> dict[str, int]:
    """Создаёт недостающие дисциплины и их варианты расписания. Идемпотентно."""
    cur = cur or get_curriculum()
    subjects = cur.all_subjects()
    if not subjects:
        return {}
    teacher_ids = ensure_teachers(db, cur)
    pool = sorted(teacher_ids)
    existing = catalog_courses(db)
    taken = {r["code"] for r in db.list_courses()}
    created = 0
    for title in subjects:
        rng = random.Random(_seed("course", title))
        known = [t for t in cur.teachers.get(title, []) if t in teacher_ids]
        if title not in existing:
            code = _course_code(title, taken)
            taken.add(code)
            lead = known[0] if known else rng.choice(pool)
            existing[title] = db.create_course(code, title, CATALOG_MARK, teacher_ids[lead])
            created += 1
        course_id = existing[title]
        if db.schedule_for_course(course_id):
            continue
        # «Один предмет — несколько преподавателей и разные дни»: сначала те, кто ведёт его по
        # файлам, остальных добираем случайно из общего списка.
        variants = min(4, max(3, len(known)))
        names = known[:variants]
        while len(names) < variants:
            extra = rng.choice(pool)
            if extra not in names:
                names.append(extra)
        days = rng.sample(range(6), variants)
        kind = rng.choice(("лекция", "практика", "семинар"))
        for name, day in zip(names, days):
            start = rng.choice(SLOT_STARTS)
            db.add_schedule_item(course_id, day, start, _end_time(start), rng.choice(ROOMS), kind, teacher_ids[name])
    if created:
        logger.info("Каталог AlmaU: добавлено дисциплин %s", created)
    return existing


# ---------- запись студента и оценки ----------

def _overlaps(a, b) -> bool:
    return (a["day_of_week"] == b["day_of_week"]
            and _minutes(a["start_time"]) < _minutes(b["end_time"])
            and _minutes(b["start_time"]) < _minutes(a["end_time"]))


def sync_student(db: Database, user_id: int, cur: Curriculum | None = None) -> int:
    """Записывает студента на дисциплины его специальности и курса (вариант без накладок по
    времени) и снимает с дисциплин каталога, которых в его плане больше нет. Свои записи на
    дисциплины преподавателей не трогает. Возвращает, на сколько дисциплин записан заново."""
    cur = cur or get_curriculum()
    user = db.get_web_user(user_id)
    if user is None or user["role"] != "student":
        return 0
    catalog = catalog_courses(db)
    wanted = [catalog[t] for t in cur.subjects(user["specialty"], user["course"]) if t in catalog]
    catalog_ids = set(catalog.values())
    enrolled = {r["id"] for r in db.courses_for_student(user_id)}
    for course_id in enrolled & catalog_ids - set(wanted):
        db.unenroll(user_id, course_id)
    busy = list(db.schedule_for_student(user_id))
    todo = [c for c in wanted if c not in enrolled]
    options = {c: db.schedule_for_course(c) for c in todo}
    rng = random.Random(_seed("student", user_id))
    best, best_clashes = {}, None
    for _ in range(40):  # жадный выбор в разном порядке; берём раскладку с наименьшим числом накладок
        order = todo[:]
        rng.shuffle(order)
        order.sort(key=lambda c: len(options[c]))  # сначала дисциплины с меньшим выбором
        taken, picks, clashes = busy[:], {}, 0
        for c in order:
            opts = options[c][:]
            rng.shuffle(opts)
            free = [o for o in opts if not any(_overlaps(o, b) for b in taken)]
            choice = (free or opts or [None])[0]
            clashes += not free and bool(opts)
            picks[c] = choice
            if choice:
                taken.append(choice)
        if best_clashes is None or clashes < best_clashes:
            best, best_clashes = picks, clashes
        if clashes == 0:
            break
    added = 0
    for course_id in todo:
        choice = best.get(course_id)
        db.enroll(user_id, course_id, choice["id"] if choice else None)
        added += 1
    return added


def showcase_names() -> set[str]:
    raw = os.getenv("SHOWCASE_GRADES_FOR", DEFAULT_SHOWCASE)
    return {" ".join(sorted(n.casefold().split())) for n in raw.split(",") if n.strip()}


def gets_showcase_grades(user) -> bool:
    if user is None or user["role"] != "student":
        return False
    if user["is_demo"]:
        return True
    key = " ".join(sorted(f"{user['first_name']} {user['last_name']}".casefold().split()))
    return key in showcase_names()


def seed_grades(db: Database, user_id: int) -> int:
    """ТК1, РК1, ТК2 по каждой дисциплине каталога, где оценок ещё нет. У каждого студента свой
    «уровень», у дисциплин — небольшой разброс, поэтому ведомость выглядит правдоподобно."""
    catalog_ids = set(catalog_courses(db).values())
    rng = random.Random(_seed("grades", user_id))
    level = rng.choice((58, 66, 72, 78, 84, 90, 94))
    added = 0
    for course in db.courses_for_student(user_id):
        if course["id"] not in catalog_ids or db.grades_for_student(user_id, course["id"]):
            continue
        base = level + rng.randint(-9, 8)
        for label, spread in (("ТК1", 7), ("РК1", 11), ("ТК2", 7)):
            score = max(35, min(100, base + rng.randint(-spread, spread)))
            db.set_grade(user_id, course["id"], label, score, 100)
        added += 1
    return added


def on_profile_changed(db: Database, user_id: int) -> None:
    """Регистрация или смена курса/специальности: пересобрать расписание и, если положено,
    выставить текущие оценки."""
    sync_student(db, user_id)
    user = db.get_web_user(user_id)
    if gets_showcase_grades(user):
        seed_grades(db, user_id)


def sync_all(db: Database) -> None:
    """При старте: каталог, записи всех студентов, оценки демо-студентов и витринных аккаунтов."""
    # Первый запуск пишет тысячи строк; без fsync на каждой транзакции это секунды, а не минуты.
    db.conn.execute("PRAGMA synchronous = OFF")
    try:
        ensure_catalog(db)
        rows = db.conn.execute("SELECT id FROM web_users WHERE role = 'student' AND banned = 0").fetchall()
        for row in rows:
            on_profile_changed(db, row["id"])
    finally:
        db.conn.execute("PRAGMA synchronous = FULL")


# ---------- «живые» демо-аккаунты ----------

def spread_demo_activity(db: Database, share: float = 1.0, within_minutes: int = 3 * 24 * 60) -> int:
    """Демо-аккаунтам ставит «был(а) в сети» в пределах последних within_minutes минут —
    только доле share из них. При старте разбрасываем всех на трое суток, потом фоновая задача
    раз в несколько минут «будит» небольшую часть, чтобы кто-то всегда был в сети."""
    ids = [r["id"] for r in db.conn.execute("SELECT id FROM web_users WHERE is_demo = 1 AND role = 'student'")]
    if not ids:
        return 0
    rng = random.Random()
    chosen = ids if share >= 1 else rng.sample(ids, max(1, int(len(ids) * share)))
    now = datetime.now(timezone.utc)
    with db.conn:
        for user_id in chosen:
            seen = now - timedelta(minutes=rng.randint(0, within_minutes), seconds=rng.randint(0, 59))
            db.conn.execute("UPDATE web_users SET last_seen_at = ? WHERE id = ? AND "
                            "(last_seen_at IS NULL OR last_seen_at < ?)",
                            (seen.isoformat(), user_id, seen.isoformat()))
    return len(chosen)
