"""Учёба: дисциплины, запись на них (с выбором варианта расписания), посещаемость
с автоматическим ретейком и оценки — Platonus-стиль, первый модуль учебной части платформы
(дальше по плану: каналы с заданиями, видеоуроки, прокторинг).

Роли у аккаунта (web_users.role): 'student' (по умолчанию при регистрации), 'teacher',
'registrar' (отдел расписания), 'admin'. Роль teacher/registrar нельзя получить обычной
регистрацией — только через invite_key, который выдаёт и может в любой момент перевыпустить
админ (POST /api/admin/rotate-invite-key в bot/api.py); либо вручную через /api/admin/set-role.

Кто что может:
- студент — сам записывается на дисциплины (enroll/unenroll), если у дисциплины несколько
  вариантов расписания (напр. математика по Пн ИЛИ по Ср) — выбирает один при записи или
  позже через choose_schedule; видит только выбранное, не все варианты сразу.
- преподаватель — ведёт дисциплины, где он указан teacher_id (на уровне курса или
  переопределён на конкретном варианте расписания schedule_items.teacher_id): ставит оценки,
  отмечает посещаемость. Отметить присутствие/отсутствие можно только тому, кто выбрал
  именно этот вариант расписания. Прошлые отметки можно свободно поправить (снять/добавить
  пропуск задним числом) — но НОВУЮ отметку поставить нельзя, если у студента уже больше
  30% пропусков по этому варианту (db.set_attendance бросает AttendanceLocked → 409).
- отдел расписания (registrar) — управляет самим расписанием: создаёт/удаляет варианты
  расписания любой дисциплины, переназначает преподавателя конкретному варианту, переводит
  студента на другой день (choose_schedule от чужого имени). Оценки и посещаемость не трогает.
- admin — может всё то же, что teacher и registrar вместе, по любой дисциплине.
"""
import re

from aiohttp import web

from bot.db import AttendanceLocked, Database
from bot.web_chat import read_json_body, require_session

MAX_CODE_LEN = 20
MAX_TITLE_LEN = 200
MAX_DESCRIPTION_LEN = 2000
MAX_LABEL_LEN = 40
MAX_ROOM_LEN = 40
MAX_KIND_LEN = 40
MAX_COMMENT_LEN = 500
TEACHING_ROLES = {"teacher", "admin"}
SCHEDULING_ROLES = {"registrar", "admin"}
VALID_KINDS = {"лекция", "практика", "лаб", "семинар"}
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _require_user(db: Database, user_id: int):
    row = db.get_web_user(user_id)
    if row is None:
        raise web.HTTPUnauthorized(reason="user not found")
    return row


def _require_role(db: Database, user_id: int, roles: set[str]):
    row = _require_user(db, user_id)
    if row["role"] not in roles:
        raise web.HTTPForbidden(reason=f"one of roles {sorted(roles)} required")
    return row


def _require_course(db: Database, course_id: int):
    course = db.get_course(course_id)
    if course is None:
        raise web.HTTPNotFound(reason="course not found")
    return course


def _require_grade_manager(db: Database, user_id: int, course_id: int):
    """Только преподаватель ЭТОЙ дисциплины или админ — ставить оценки может лишь он."""
    user = _require_role(db, user_id, TEACHING_ROLES)
    course = _require_course(db, course_id)
    if user["role"] != "admin" and course["teacher_id"] != user_id:
        raise web.HTTPForbidden(reason="not the teacher of this course")
    return user, course


def _require_schedule_manager(db: Database, user_id: int, course_id: int):
    """Преподаватель этой дисциплины, отдел расписания (любая дисциплина) или админ."""
    user = _require_user(db, user_id)
    course = _require_course(db, course_id)
    is_course_teacher = user["role"] == "teacher" and course["teacher_id"] == user_id
    if user["role"] not in ({"admin"} | SCHEDULING_ROLES) and not is_course_teacher:
        raise web.HTTPForbidden(reason="teacher of this course, registrar or admin required")
    return user, course


def _effective_slot_teacher_id(course, schedule_item) -> int | None:
    return schedule_item["teacher_id"] if schedule_item["teacher_id"] is not None else course["teacher_id"]


def _require_slot_teacher(db: Database, user_id: int, schedule_item):
    """Кто ведёт именно этот вариант расписания (или админ) — только он отмечает посещаемость."""
    user = _require_user(db, user_id)
    course = _require_course(db, schedule_item["course_id"])
    if user["role"] != "admin" and _effective_slot_teacher_id(course, schedule_item) != user_id:
        raise web.HTTPForbidden(reason="not the teacher of this schedule slot")
    return user, course


def _schedule_item_or_404(db: Database, item_id: int):
    item = db.get_schedule_item(item_id)
    if item is None:
        raise web.HTTPNotFound(reason="schedule item not found")
    return item


def _public_course(row) -> dict:
    return {
        "id": row["id"], "code": row["code"], "title": row["title"],
        "description": row["description"], "teacher_id": row["teacher_id"],
    }


def _public_schedule_item(row) -> dict:
    out = {
        "id": row["id"], "course_id": row["course_id"], "day_of_week": row["day_of_week"],
        "start_time": row["start_time"], "end_time": row["end_time"],
        "room": row["room"], "kind": row["kind"], "teacher_id": row["teacher_id"],
    }
    if "course_code" in row.keys():
        out["course_code"] = row["course_code"]
        out["course_title"] = row["course_title"]
    return out


def _public_grade(row) -> dict:
    out = {
        "id": row["id"], "course_id": row["course_id"], "label": row["label"],
        "score": row["score"], "max_score": row["max_score"], "comment": row["comment"],
    }
    if "course_code" in row.keys():
        out["course_code"] = row["course_code"]
        out["course_title"] = row["course_title"]
    return out


def _course_id_from(data: dict) -> int:
    try:
        return int(data.get("course_id"))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(reason="course_id is required")


def _int_field(data: dict, key: str) -> int:
    try:
        return int(data.get(key))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(reason=f"{key} is required")


async def list_courses(request: web.Request) -> web.Response:
    require_session(request)
    db: Database = request.app["db"]
    return web.json_response({"courses": [_public_course(c) for c in db.list_courses()]})


async def my_courses(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    return web.json_response({"courses": [_public_course(c) for c in db.courses_for_student(user_id)]})


async def enroll(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    course_id = _course_id_from(data)
    _require_course(db, course_id)
    items = db.schedule_for_course(course_id)

    schedule_item_id = data.get("schedule_item_id")
    if schedule_item_id is not None:
        schedule_item_id = _int_field(data, "schedule_item_id")
        if schedule_item_id not in {i["id"] for i in items}:
            raise web.HTTPBadRequest(reason="schedule_item_id does not belong to this course")
    elif len(items) > 1:
        raise web.HTTPBadRequest(reason="this course has several schedule options — pass schedule_item_id")

    db.enroll(user_id, course_id, schedule_item_id)
    return web.json_response({"ok": True})


async def unenroll(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    db.unenroll(user_id, _course_id_from(data))
    return web.json_response({"ok": True})


async def course_schedule_options(request: web.Request) -> web.Response:
    """Варианты расписания дисциплины — видно любому вошедшему (не только преподавателю/
    registrar/админу), чтобы студент мог выбрать один при записи."""
    require_session(request)
    db: Database = request.app["db"]
    try:
        course_id = int(request.match_info["course_id"])
    except ValueError:
        raise web.HTTPBadRequest(reason="invalid course id")
    _require_course(db, course_id)
    items = [_public_schedule_item(i) for i in db.schedule_for_course(course_id)]
    return web.json_response({"schedule": items})


async def choose_schedule(request: web.Request) -> web.Response:
    """Студент выбирает/меняет свой вариант расписания по уже записанной дисциплине —
    или, если указан user_id, это делает за него отдел расписания/админ."""
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    course_id = _course_id_from(data)
    schedule_item_id = _int_field(data, "schedule_item_id")

    target_id = data.get("user_id")
    if target_id is not None:
        target_id = int(target_id)
        _require_schedule_manager(db, user_id, course_id)
    else:
        target_id = user_id

    _require_course(db, course_id)
    items = db.schedule_for_course(course_id)
    if schedule_item_id not in {i["id"] for i in items}:
        raise web.HTTPBadRequest(reason="schedule_item_id does not belong to this course")
    if not db.set_enrollment_schedule(target_id, course_id, schedule_item_id):
        raise web.HTTPBadRequest(reason="not enrolled in this course")
    return web.json_response({"ok": True})


async def my_schedule(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    items = [_public_schedule_item(i) for i in db.schedule_for_student(user_id)]
    return web.json_response({"schedule": items})


async def my_grades(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    grades = [_public_grade(g) for g in db.grades_for_student(user_id)]
    return web.json_response({"grades": grades})


async def my_attendance(request: web.Request) -> web.Response:
    """Своя посещаемость и статистика (включая флаг ретейка) по одной дисциплине."""
    user_id = require_session(request)
    db: Database = request.app["db"]
    course_id = _int_field(dict(request.query), "course_id")
    enrollment = db.get_enrollment(user_id, course_id)
    empty_stats = {"total": 0, "absent": 0, "ratio": 0.0, "on_retake": False}
    if enrollment is None or enrollment["schedule_item_id"] is None:
        return web.json_response({"stats": empty_stats, "records": []})
    schedule_item_id = enrollment["schedule_item_id"]
    records = [{"class_date": a["class_date"], "present": bool(a["present"])}
               for a in db.attendance_for_student(schedule_item_id, user_id)]
    return web.json_response({"stats": db.attendance_stats(schedule_item_id, user_id), "records": records})


async def teaching(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    user = _require_role(db, user_id, TEACHING_ROLES)
    courses = db.list_courses() if user["role"] == "admin" else db.courses_taught_by(user_id)
    return web.json_response({"courses": [_public_course(c) for c in courses]})


async def create_course(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    user = _require_role(db, user_id, TEACHING_ROLES)

    code = str(data.get("code", "")).strip()[:MAX_CODE_LEN]
    title = str(data.get("title", "")).strip()[:MAX_TITLE_LEN]
    description = str(data.get("description", "")).strip()[:MAX_DESCRIPTION_LEN]
    if not code or not title:
        raise web.HTTPBadRequest(reason="code and title are required")

    teacher_id = user_id if user["role"] == "teacher" else None
    course_id = db.create_course(code, title, description, teacher_id)
    return web.json_response({"ok": True, "course": _public_course(db.get_course(course_id))}, status=201)


async def course_roster(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    try:
        course_id = int(request.match_info["course_id"])
    except ValueError:
        raise web.HTTPBadRequest(reason="invalid course id")
    _require_schedule_manager(db, user_id, course_id)

    students = [
        {"id": s["id"], "first_name": s["first_name"], "last_name": s["last_name"],
         "course": s["course"], "specialty": s["specialty"],
         "schedule_item_id": s["enrollment_schedule_item_id"]}
        for s in db.roster(course_id)
    ]
    return web.json_response({"students": students})


async def add_schedule_item(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    course_id = _course_id_from(data)
    _require_schedule_manager(db, user_id, course_id)

    day_of_week = _int_field(data, "day_of_week")
    if not 0 <= day_of_week <= 6:
        raise web.HTTPBadRequest(reason="day_of_week must be 0..6 (Пн..Вс)")

    start_time = str(data.get("start_time", "")).strip()[:5]
    end_time = str(data.get("end_time", "")).strip()[:5]
    room = str(data.get("room", "")).strip()[:MAX_ROOM_LEN]
    kind = str(data.get("kind", "лекция")).strip()[:MAX_KIND_LEN] or "лекция"
    if not start_time or not end_time:
        raise web.HTTPBadRequest(reason="start_time and end_time are required")
    if kind not in VALID_KINDS:
        raise web.HTTPBadRequest(reason=f"kind must be one of {sorted(VALID_KINDS)}")

    teacher_id = data.get("teacher_id")
    if teacher_id is not None:
        teacher_id = int(teacher_id)
        teacher = db.get_web_user(teacher_id)
        if teacher is None or teacher["role"] not in TEACHING_ROLES:
            raise web.HTTPBadRequest(reason="teacher_id must reference a teacher account")

    item_id = db.add_schedule_item(course_id, day_of_week, start_time, end_time, room, kind, teacher_id)
    return web.json_response({"ok": True, "item": _public_schedule_item(db.get_schedule_item(item_id))}, status=201)


async def set_schedule_teacher(request: web.Request) -> web.Response:
    """Переназначить, кто ведёт конкретный вариант расписания — дело отдела расписания/админа,
    не самого преподавателя (поэтому отдельная, более узкая проверка роли)."""
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    _require_role(db, user_id, SCHEDULING_ROLES)

    item_id = _int_field(data, "item_id")
    item = _schedule_item_or_404(db, item_id)
    teacher_id = data.get("teacher_id")
    if teacher_id is not None:
        teacher_id = int(teacher_id)
        teacher = db.get_web_user(teacher_id)
        if teacher is None or teacher["role"] not in TEACHING_ROLES:
            raise web.HTTPBadRequest(reason="teacher_id must reference a teacher account")
    db.set_schedule_item_teacher(item_id, teacher_id)
    return web.json_response({"ok": True, "item": _public_schedule_item(db.get_schedule_item(item["id"]))})


async def delete_schedule_item(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    item_id = _int_field(data, "item_id")
    item = _schedule_item_or_404(db, item_id)
    _require_schedule_manager(db, user_id, item["course_id"])
    db.delete_schedule_item(item_id)
    return web.json_response({"ok": True})


async def course_schedule(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    try:
        course_id = int(request.match_info["course_id"])
    except ValueError:
        raise web.HTTPBadRequest(reason="invalid course id")
    _require_schedule_manager(db, user_id, course_id)
    items = [_public_schedule_item(i) for i in db.schedule_for_course(course_id)]
    return web.json_response({"schedule": items})


async def set_grade(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    course_id = _course_id_from(data)
    _require_grade_manager(db, user_id, course_id)

    target_id = _int_field(data, "user_id")
    try:
        score = float(data.get("score"))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(reason="score is required")
    max_score = data.get("max_score", 100)
    try:
        max_score = float(max_score)
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(reason="max_score must be a number")
    label = str(data.get("label", "")).strip()[:MAX_LABEL_LEN]
    comment = str(data.get("comment", "")).strip()[:MAX_COMMENT_LEN]
    if not label:
        raise web.HTTPBadRequest(reason="label is required")
    if not db.is_enrolled(target_id, course_id):
        raise web.HTTPBadRequest(reason="student is not enrolled in this course")

    grade_id = db.set_grade(target_id, course_id, label, score, max_score, comment)
    return web.json_response({"ok": True, "id": grade_id})


async def course_grades(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    try:
        course_id = int(request.match_info["course_id"])
    except ValueError:
        raise web.HTTPBadRequest(reason="invalid course id")
    _require_grade_manager(db, user_id, course_id)
    grades = [_public_grade(g) for g in db.grades_for_course(course_id)]
    return web.json_response({"grades": grades})


async def mark_attendance(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    schedule_item_id = _int_field(data, "schedule_item_id")
    target_id = _int_field(data, "user_id")
    item = _schedule_item_or_404(db, schedule_item_id)
    _require_slot_teacher(db, user_id, item)

    class_date = str(data.get("class_date", "")).strip()
    if not DATE_RE.fullmatch(class_date):
        raise web.HTTPBadRequest(reason="class_date must be YYYY-MM-DD")
    present = bool(data.get("present", True))

    enrollment = db.get_enrollment(target_id, item["course_id"])
    if enrollment is None or enrollment["schedule_item_id"] != schedule_item_id:
        raise web.HTTPBadRequest(reason="student did not choose this schedule slot")

    try:
        db.set_attendance(schedule_item_id, target_id, class_date, present, user_id)
    except AttendanceLocked:
        raise web.HTTPConflict(reason="student is on retake (over 30% absences) — no new marks allowed")
    return web.json_response({"ok": True, "stats": db.attendance_stats(schedule_item_id, target_id)})


async def delete_attendance_mark(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    schedule_item_id = _int_field(data, "schedule_item_id")
    target_id = _int_field(data, "user_id")
    item = _schedule_item_or_404(db, schedule_item_id)
    _require_slot_teacher(db, user_id, item)

    class_date = str(data.get("class_date", "")).strip()
    db.delete_attendance(schedule_item_id, target_id, class_date)
    return web.json_response({"ok": True, "stats": db.attendance_stats(schedule_item_id, target_id)})


async def slot_attendance(request: web.Request) -> web.Response:
    """Список студентов этого варианта расписания с их посещаемостью и статусом ретейка —
    для преподавателя, который его ведёт."""
    user_id = require_session(request)
    db: Database = request.app["db"]
    try:
        schedule_item_id = int(request.match_info["item_id"])
    except ValueError:
        raise web.HTTPBadRequest(reason="invalid schedule item id")
    item = _schedule_item_or_404(db, schedule_item_id)
    _require_slot_teacher(db, user_id, item)

    out = []
    for s in db.roster_for_schedule_item(schedule_item_id):
        records = [{"class_date": a["class_date"], "present": bool(a["present"])}
                   for a in db.attendance_for_student(schedule_item_id, s["id"])]
        out.append({"id": s["id"], "first_name": s["first_name"], "last_name": s["last_name"],
                     "stats": db.attendance_stats(schedule_item_id, s["id"]), "records": records})
    return web.json_response({"students": out})


def register_routes(app: web.Application) -> None:
    app.router.add_get("/api/academics/courses", list_courses)
    app.router.add_get("/api/academics/my-courses", my_courses)
    app.router.add_post("/api/academics/enroll", enroll)
    app.router.add_post("/api/academics/unenroll", unenroll)
    app.router.add_post("/api/academics/schedule/choose", choose_schedule)
    app.router.add_get("/api/academics/schedule", my_schedule)
    app.router.add_get("/api/academics/grades", my_grades)
    app.router.add_get("/api/academics/attendance", my_attendance)
    app.router.add_get("/api/academics/teaching", teaching)
    app.router.add_post("/api/academics/courses", create_course)
    app.router.add_get("/api/academics/courses/{course_id}/roster", course_roster)
    app.router.add_get("/api/academics/courses/{course_id}/schedule", course_schedule)
    app.router.add_get("/api/academics/courses/{course_id}/options", course_schedule_options)
    app.router.add_post("/api/academics/schedule", add_schedule_item)
    app.router.add_post("/api/academics/schedule/set-teacher", set_schedule_teacher)
    app.router.add_post("/api/academics/schedule/delete", delete_schedule_item)
    app.router.add_post("/api/academics/grades/set", set_grade)
    app.router.add_get("/api/academics/courses/{course_id}/grades", course_grades)
    app.router.add_post("/api/academics/attendance/mark", mark_attendance)
    app.router.add_post("/api/academics/attendance/delete", delete_attendance_mark)
    app.router.add_get("/api/academics/schedule-items/{item_id}/attendance", slot_attendance)
