"""Учёба: дисциплины, запись на них, расписание и оценки — Platonus-стиль, первый модуль
учебной части платформы (дальше по плану: каналы с заданиями, видеоуроки, прокторинг).

Роли у аккаунта (web_users.role): 'student' (по умолчанию при регистрации), 'teacher',
'admin'. Самостоятельно выдать себе роль преподавателя нельзя — это делает администратор
через POST /api/admin/set-role (bot/api.py), защищённый тем же ADMIN_TOKEN, что и прочие
админские эндпоинты. Так на старте не нужен отдельный UI для управления ролями.

Студент сам записывается на дисциплины (enroll/unenroll) — это и есть его расписание
и оценки: показываем только то, на что он записан. Преподаватель видит и ведёт только
свои дисциплины (course.teacher_id), админ — любые.
"""
from aiohttp import web

from bot.db import Database
from bot.web_chat import read_json_body, require_session

MAX_CODE_LEN = 20
MAX_TITLE_LEN = 200
MAX_DESCRIPTION_LEN = 2000
MAX_LABEL_LEN = 40
MAX_ROOM_LEN = 40
MAX_KIND_LEN = 40
MAX_COMMENT_LEN = 500
STAFF_ROLES = {"teacher", "admin"}
VALID_KINDS = {"лекция", "практика", "лаб", "семинар"}


def _require_user(db: Database, user_id: int):
    row = db.get_web_user(user_id)
    if row is None:
        raise web.HTTPUnauthorized(reason="user not found")
    return row


def _require_staff(db: Database, user_id: int):
    row = _require_user(db, user_id)
    if row["role"] not in STAFF_ROLES:
        raise web.HTTPForbidden(reason="teacher or admin role required")
    return row


def _require_course(db: Database, course_id: int):
    course = db.get_course(course_id)
    if course is None:
        raise web.HTTPNotFound(reason="course not found")
    return course


def _require_course_owner(db: Database, user_id: int, course_id: int):
    """Преподаватель этой дисциплины или админ — иначе 403. Возвращает (user, course)."""
    user = _require_staff(db, user_id)
    course = _require_course(db, course_id)
    if user["role"] != "admin" and course["teacher_id"] != user_id:
        raise web.HTTPForbidden(reason="not the teacher of this course")
    return user, course


def _public_course(row) -> dict:
    return {
        "id": row["id"], "code": row["code"], "title": row["title"],
        "description": row["description"], "teacher_id": row["teacher_id"],
    }


def _public_schedule_item(row) -> dict:
    out = {
        "id": row["id"], "course_id": row["course_id"], "day_of_week": row["day_of_week"],
        "start_time": row["start_time"], "end_time": row["end_time"],
        "room": row["room"], "kind": row["kind"],
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
    course = _require_course(db, _course_id_from(data))
    db.enroll(user_id, course["id"])
    return web.json_response({"ok": True})


async def unenroll(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    course_id = _course_id_from(data)
    db.unenroll(user_id, course_id)
    return web.json_response({"ok": True})


def _course_id_from(data: dict) -> int:
    try:
        return int(data.get("course_id"))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(reason="course_id is required")


WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


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


async def teaching(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    user = _require_staff(db, user_id)
    courses = db.list_courses() if user["role"] == "admin" else db.courses_taught_by(user_id)
    return web.json_response({"courses": [_public_course(c) for c in courses]})


async def create_course(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    user = _require_staff(db, user_id)

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
    _require_course_owner(db, user_id, course_id)

    students = [
        {"id": s["id"], "first_name": s["first_name"], "last_name": s["last_name"],
         "course": s["course"], "specialty": s["specialty"]}
        for s in db.roster(course_id)
    ]
    return web.json_response({"students": students})


async def add_schedule_item(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    course_id = _course_id_from(data)
    _require_course_owner(db, user_id, course_id)

    try:
        day_of_week = int(data.get("day_of_week"))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(reason="day_of_week must be 0..6 (Пн..Вс)")
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

    item_id = db.add_schedule_item(course_id, day_of_week, start_time, end_time, room, kind)
    return web.json_response({"ok": True, "item": _public_schedule_item(db.get_schedule_item(item_id))}, status=201)


async def delete_schedule_item(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    try:
        item_id = int(data.get("item_id"))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(reason="item_id is required")

    item = db.get_schedule_item(item_id)
    if item is None:
        raise web.HTTPNotFound(reason="schedule item not found")
    _require_course_owner(db, user_id, item["course_id"])
    db.delete_schedule_item(item_id)
    return web.json_response({"ok": True})


async def course_schedule(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    try:
        course_id = int(request.match_info["course_id"])
    except ValueError:
        raise web.HTTPBadRequest(reason="invalid course id")
    _require_course_owner(db, user_id, course_id)
    items = [_public_schedule_item(i) for i in db.schedule_for_course(course_id)]
    return web.json_response({"schedule": items})


async def set_grade(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    course_id = _course_id_from(data)
    _require_course_owner(db, user_id, course_id)

    try:
        target_id = int(data.get("user_id"))
        score = float(data.get("score"))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(reason="user_id and score are required")
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
    _require_course_owner(db, user_id, course_id)
    grades = [_public_grade(g) for g in db.grades_for_course(course_id)]
    return web.json_response({"grades": grades})


def register_routes(app: web.Application) -> None:
    app.router.add_get("/api/academics/courses", list_courses)
    app.router.add_get("/api/academics/my-courses", my_courses)
    app.router.add_post("/api/academics/enroll", enroll)
    app.router.add_post("/api/academics/unenroll", unenroll)
    app.router.add_get("/api/academics/schedule", my_schedule)
    app.router.add_get("/api/academics/grades", my_grades)
    app.router.add_get("/api/academics/teaching", teaching)
    app.router.add_post("/api/academics/courses", create_course)
    app.router.add_get("/api/academics/courses/{course_id}/roster", course_roster)
    app.router.add_get("/api/academics/courses/{course_id}/schedule", course_schedule)
    app.router.add_post("/api/academics/schedule", add_schedule_item)
    app.router.add_post("/api/academics/schedule/delete", delete_schedule_item)
    app.router.add_post("/api/academics/grades/set", set_grade)
    app.router.add_get("/api/academics/courses/{course_id}/grades", course_grades)
