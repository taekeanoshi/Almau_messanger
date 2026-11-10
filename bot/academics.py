"""Учёба: дисциплины, запись на них (с выбором варианта расписания), посещаемость
с автоматическим ретейком, оценки, задания со сдачами (канал препод↔студент, как в MS
Teams), видеоуроки с мини-тестом после просмотра (как в Moodle) и проктор-экзамены
с подсчётом выходов за пределы окна и необязательной записью экрана (как в Далет-тесте/
Oqulabs) — Platonus-стиль.

«Канал» — это просто дисциплина (courses), а не отдельная сущность: у неё уже есть
преподаватель и ростер записанных студентов, этого достаточно. Вопрос преподавателю
«по поводу вопросов» — это уже работающий прямой чат по ФИО (bot/web_chat.py, вкладка
«Люди»): туда уже попадают и преподаватели, искать и писать можно как любому студенту.

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

Проктор-экзамены — отдельная сущность от видеоуроков с мини-тестом: это для серьёзных
рубежек/экзаменов, а не для лёгкой самопроверки после видео. Как устроена честность:
- экзамен сдаётся один раз; вопросы студент получает только при старте (POST /exams/start),
  а правильные ответы после сдачи не показываются — иначе их можно было бы подсмотреть
  и пересдать на 100%. Разрешить пересдачу может только преподаватель (сброс попытки);
- время старта фиксирует сервер, лимит считается от него: подкрутить часы в браузере или
  перезагрузить страницу бесполезно. Сдача после лимита принимается (чтобы студент не
  потерял ответы из-за связи), но помечается преподавателю как просроченная;
- подсчёт выходов (tab_exit_count) и время вне окна (away_seconds) считает браузер по
  потере фокуса и шлёт при сдаче — сервер ему доверяет;
- запись экрана (getDisplayMedia/MediaRecorder) уходит на сервер кусками по ходу экзамена
  (exam_recording_chunks), снимки с веб-камеры — по одному кадру (exam_snapshots). Раз это
  API браузера, он сам спрашивает у студента разрешение системным диалогом — без согласия
  записи не будет. Записи и снимки старше EXAM_MEDIA_TTL_DAYS удаляются автоматически
  (db.purge_expired), чтобы не раздувать базу и не хранить видео студентов вечно.
"""
import base64
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from aiohttp import web

from bot.db import AttendanceLocked, Database
from bot.web_chat import read_json_body, require_session, sniff_image_type

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

MAX_ASSIGNMENT_TITLE_LEN = 200
MAX_ASSIGNMENT_TEXT_LEN = 5000
MAX_FILE_NAME_LEN = 150
MAX_FILE_BYTES = 10 * 1024 * 1024
ALLOWED_FILE_TYPES = {
    "application/pdf", "application/zip",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/plain", "text/csv",
    "image/jpeg", "image/png", "image/webp", "image/gif",
}

MAX_VIDEO_URL_LEN = 500
MAX_QUESTION_TEXT_LEN = 500
MAX_OPTION_LEN = 200
MIN_OPTIONS = 2
MAX_OPTIONS = 8

MAX_EXAM_TITLE_LEN = 200
MAX_RECORDING_NAME_LEN = 150
MAX_RECORDING_BYTES = 150 * 1024 * 1024       # вся запись экрана одной попытки (сумма кусков)
MAX_RECORDING_CHUNK_BYTES = 8 * 1024 * 1024   # один кусок записи — уходит отдельным запросом
MAX_RECORDING_CHUNKS = 5000
RECORDING_CONTENT_TYPES = {"video/webm", "video/mp4"}
MAX_SNAPSHOT_BYTES = 300 * 1024               # один кадр с веб-камеры (JPEG)
MAX_SNAPSHOTS = 600                           # кадров на попытку: раз в 20 секунд хватает на 3+ часа
MAX_TAB_EXIT_COUNT = 100_000  # защита от мусорного числа с клиента, не настоящий лимит попыток
MAX_AWAY_SECONDS = 24 * 60 * 60
LATE_GRACE_SECONDS = 60       # запас на отправку ответов после истечения лимита времени


def _attachment(body: bytes, content_type: str, filename: str) -> web.Response:
    """Отдаёт файл на скачивание. Имя — и ASCII-вариант, и UTF-8 (RFC 5987): заголовки HTTP
    не переносят кириллицу напрямую, а кавычки/переводы строк в имени ломали бы заголовок."""
    filename = filename or "file"
    ascii_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"
    return web.Response(body=body, content_type=content_type, headers={"Content-Disposition": disposition})


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


def _person_name(db: Database, user_id: int | None) -> str:
    row = db.get_web_user(user_id) if user_id is not None else None
    return f"{row['first_name']} {row['last_name']}".strip() if row is not None else ""


def _public_course(row, db: Database | None = None) -> dict:
    out = {
        "id": row["id"], "code": row["code"], "title": row["title"],
        "description": row["description"], "teacher_id": row["teacher_id"],
    }
    if db is not None:
        out["teacher_name"] = _person_name(db, row["teacher_id"])
    return out


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


def _schedule_item_with_teacher(db: Database, row) -> dict:
    """Пара + кто её ведёт (переопределённый преподаватель варианта или преподаватель дисциплины)."""
    out = _public_schedule_item(row)
    course = db.get_course(row["course_id"])
    out["teacher_name"] = _person_name(db, _effective_slot_teacher_id(course, row)) if course else ""
    return out


def _public_grade(row) -> dict:
    out = {
        "id": row["id"], "course_id": row["course_id"], "user_id": row["user_id"], "label": row["label"],
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
    return web.json_response({"courses": [_public_course(c, db) for c in db.list_courses()]})


async def my_courses(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    return web.json_response({"courses": [_public_course(c, db) for c in db.courses_for_student(user_id)]})


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
    items = [_schedule_item_with_teacher(db, i) for i in db.schedule_for_course(course_id)]
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
        target_id = _int_field(data, "user_id")
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
    items = [_schedule_item_with_teacher(db, i) for i in db.schedule_for_student(user_id)]
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
    """Дисциплины, которыми управляет сотрудник: преподаватель — свои, отдел расписания
    и админ — все. can_grade говорит интерфейсу, показывать ли оценки/задания/экзамены
    (отделу расписания доступно только расписание и состав групп)."""
    user_id = require_session(request)
    db: Database = request.app["db"]
    user = _require_role(db, user_id, TEACHING_ROLES | SCHEDULING_ROLES)
    courses = db.courses_taught_by(user_id) if user["role"] == "teacher" else db.list_courses()
    return web.json_response({
        "courses": [_public_course(c, db) for c in courses],
        "role": user["role"],
        "can_grade": user["role"] in TEACHING_ROLES,
        "can_assign_teachers": user["role"] in SCHEDULING_ROLES,
    })


async def list_teachers(request: web.Request) -> web.Response:
    """Кого можно назначить преподавателем — для отдела расписания и админа."""
    user_id = require_session(request)
    db: Database = request.app["db"]
    _require_role(db, user_id, SCHEDULING_ROLES)
    return web.json_response({"teachers": [
        {"id": t["id"], "first_name": t["first_name"], "last_name": t["last_name"]} for t in db.teaching_staff()]})


def _teacher_id_from(db: Database, data: dict) -> int | None:
    teacher_id = data.get("teacher_id")
    if teacher_id in (None, ""):
        return None
    try:
        teacher_id = int(teacher_id)
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(reason="teacher_id must be an integer")
    teacher = db.get_web_user(teacher_id)
    if teacher is None or teacher["role"] not in TEACHING_ROLES:
        raise web.HTTPBadRequest(reason="teacher_id must reference a teacher account")
    return teacher_id


async def set_course_teacher(request: web.Request) -> web.Response:
    """Назначить (или снять) преподавателя дисциплины — отдел расписания или админ."""
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    _require_role(db, user_id, SCHEDULING_ROLES)
    course_id = _course_id_from(data)
    _require_course(db, course_id)
    db.set_course_teacher(course_id, _teacher_id_from(db, data))
    return web.json_response({"ok": True, "course": _public_course(db.get_course(course_id), db)})


async def update_course(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    course_id = _course_id_from(data)
    _require_grade_manager(db, user_id, course_id)
    code = str(data.get("code", "")).strip()[:MAX_CODE_LEN]
    title = str(data.get("title", "")).strip()[:MAX_TITLE_LEN]
    description = str(data.get("description", "")).strip()[:MAX_DESCRIPTION_LEN]
    if not code or not title:
        raise web.HTTPBadRequest(reason="code and title are required")
    db.update_course(course_id, code, title, description)
    return web.json_response({"ok": True, "course": _public_course(db.get_course(course_id), db)})


async def delete_course(request: web.Request) -> web.Response:
    """Удаляет дисциплину вместе со всем содержимым — преподаватель этой дисциплины или админ."""
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    course_id = _course_id_from(data)
    _require_grade_manager(db, user_id, course_id)
    db.delete_course(course_id)
    return web.json_response({"ok": True})


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
    return web.json_response({"ok": True, "course": _public_course(db.get_course(course_id), db)}, status=201)


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

    teacher_id = _teacher_id_from(db, data)

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
    teacher_id = _teacher_id_from(db, data)
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
    items = [_schedule_item_with_teacher(db, i) for i in db.schedule_for_course(course_id)]
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


def _decode_optional_file(data: dict) -> tuple[str, str, bytes] | tuple[None, None, None]:
    """Файл в теле запроса — {"filename", "content_type", "data" (base64)}, необязателен."""
    file = data.get("file")
    if not file:
        return None, None, None
    if not isinstance(file, dict):
        raise web.HTTPBadRequest(reason="file must be an object with filename, content_type, data")
    filename = re.sub(r'[\r\n"]', "_", str(file.get("filename", "")).strip())[:MAX_FILE_NAME_LEN]
    content_type = str(file.get("content_type", "")).strip().lower()
    if not filename or content_type not in ALLOWED_FILE_TYPES:
        raise web.HTTPBadRequest(reason=f"content_type must be one of {sorted(ALLOWED_FILE_TYPES)}")
    try:
        file_bytes = base64.b64decode(str(file.get("data", "")), validate=True)
    except Exception:
        raise web.HTTPBadRequest(reason="invalid file data")
    if not file_bytes or len(file_bytes) > MAX_FILE_BYTES:
        raise web.HTTPBadRequest(reason="file too large or empty")
    return filename, content_type, file_bytes


def _require_enrolled_or_manager(db: Database, user_id: int, course_id: int) -> bool:
    """Задания видит записанный студент (только себя) или преподаватель этой дисциплины/админ
    (видит всех). Возвращает True, если это «менеджер» (может видеть все сдачи), иначе False."""
    user = _require_user(db, user_id)
    course = _require_course(db, course_id)
    if user["role"] == "admin" or (user["role"] == "teacher" and course["teacher_id"] == user_id):
        return True
    if db.is_enrolled(user_id, course_id):
        return False
    raise web.HTTPForbidden(reason="not enrolled in this course")


def _public_assignment(row) -> dict:
    return {
        "id": row["id"], "course_id": row["course_id"], "title": row["title"],
        "description": row["description"], "due_at": row["due_at"], "created_by": row["created_by"],
        "created_at": row["created_at"], "file_name": row["file_name"],
    }


def _public_submission(row, include_names: bool = False) -> dict:
    out = {
        "id": row["id"], "assignment_id": row["assignment_id"], "user_id": row["user_id"],
        "text": row["text"], "status": row["status"], "grade_score": row["grade_score"],
        "grade_comment": row["grade_comment"], "submitted_at": row["submitted_at"],
        "graded_at": row["graded_at"], "file_name": row["file_name"],
    }
    if include_names and "first_name" in row.keys():
        out["first_name"] = row["first_name"]
        out["last_name"] = row["last_name"]
    return out


async def list_assignments(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    try:
        course_id = int(request.match_info["course_id"])
    except ValueError:
        raise web.HTTPBadRequest(reason="invalid course id")
    _require_enrolled_or_manager(db, user_id, course_id)
    items = [_public_assignment(a) for a in db.assignments_for_course(course_id)]
    return web.json_response({"assignments": items})


async def create_assignment(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    course_id = _course_id_from(data)
    _require_grade_manager(db, user_id, course_id)

    title = str(data.get("title", "")).strip()[:MAX_ASSIGNMENT_TITLE_LEN]
    description = str(data.get("description", "")).strip()[:MAX_DESCRIPTION_LEN]
    due_at = str(data.get("due_at", "")).strip() or None
    if not title:
        raise web.HTTPBadRequest(reason="title is required")
    filename, content_type, file_bytes = _decode_optional_file(data)

    assignment_id = db.create_assignment(course_id, title, description, due_at, user_id,
                                          filename, content_type, file_bytes)
    return web.json_response(
        {"ok": True, "assignment": _public_assignment(db.get_assignment(assignment_id))}, status=201)


async def delete_assignment(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    assignment_id = _int_field(data, "assignment_id")
    assignment = db.get_assignment(assignment_id)
    if assignment is None:
        raise web.HTTPNotFound(reason="assignment not found")
    _require_grade_manager(db, user_id, assignment["course_id"])
    db.delete_assignment(assignment_id)
    return web.json_response({"ok": True})


async def assignment_file(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    try:
        assignment_id = int(request.match_info["assignment_id"])
    except ValueError:
        raise web.HTTPBadRequest(reason="invalid assignment id")
    assignment = db.get_assignment(assignment_id)
    if assignment is None or assignment["file_data"] is None:
        raise web.HTTPNotFound(reason="file not found")
    _require_enrolled_or_manager(db, user_id, assignment["course_id"])
    return _attachment(assignment["file_data"], assignment["file_type"], assignment["file_name"])


async def submit_assignment(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    assignment_id = _int_field(data, "assignment_id")
    assignment = db.get_assignment(assignment_id)
    if assignment is None:
        raise web.HTTPNotFound(reason="assignment not found")
    if not db.is_enrolled(user_id, assignment["course_id"]):
        raise web.HTTPForbidden(reason="not enrolled in this course")

    text = str(data.get("text", "")).strip()[:MAX_ASSIGNMENT_TEXT_LEN]
    filename, content_type, file_bytes = _decode_optional_file(data)
    if not text and filename is None:
        raise web.HTTPBadRequest(reason="text or file is required")

    submission_id = db.upsert_submission(assignment_id, user_id, text, filename, content_type, file_bytes)
    return web.json_response({"ok": True, "id": submission_id})


async def my_submission(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    assignment_id = _int_field(dict(request.query), "assignment_id")
    submission = db.get_submission_for(assignment_id, user_id)
    return web.json_response({"submission": _public_submission(submission) if submission else None})


async def assignment_submissions(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    try:
        assignment_id = int(request.match_info["assignment_id"])
    except ValueError:
        raise web.HTTPBadRequest(reason="invalid assignment id")
    assignment = db.get_assignment(assignment_id)
    if assignment is None:
        raise web.HTTPNotFound(reason="assignment not found")
    _require_grade_manager(db, user_id, assignment["course_id"])
    items = [_public_submission(s, include_names=True) for s in db.submissions_for_assignment(assignment_id)]
    return web.json_response({"submissions": items})


async def submission_file(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    try:
        submission_id = int(request.match_info["submission_id"])
    except ValueError:
        raise web.HTTPBadRequest(reason="invalid submission id")
    submission = db.get_submission(submission_id)
    if submission is None or submission["file_data"] is None:
        raise web.HTTPNotFound(reason="file not found")
    assignment = db.get_assignment(submission["assignment_id"])
    user = _require_user(db, user_id)
    course = _require_course(db, assignment["course_id"])
    is_manager = user["role"] == "admin" or (user["role"] == "teacher" and course["teacher_id"] == user_id)
    if submission["user_id"] != user_id and not is_manager:
        raise web.HTTPForbidden(reason="not allowed")
    return _attachment(submission["file_data"], submission["file_type"], submission["file_name"])


async def grade_submission(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    submission_id = _int_field(data, "submission_id")
    submission = db.get_submission(submission_id)
    if submission is None:
        raise web.HTTPNotFound(reason="submission not found")
    assignment = db.get_assignment(submission["assignment_id"])
    _require_grade_manager(db, user_id, assignment["course_id"])

    try:
        score = float(data.get("score"))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(reason="score is required")
    comment = str(data.get("comment", "")).strip()[:MAX_COMMENT_LEN]
    db.grade_submission(submission_id, score, comment)
    return web.json_response({"ok": True})


def _public_lesson(row) -> dict:
    return {
        "id": row["id"], "course_id": row["course_id"], "title": row["title"],
        "description": row["description"], "video_url": row["video_url"], "file_name": row["file_name"],
    }


async def list_lessons(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    try:
        course_id = int(request.match_info["course_id"])
    except ValueError:
        raise web.HTTPBadRequest(reason="invalid course id")
    _require_enrolled_or_manager(db, user_id, course_id)
    items = [_public_lesson(lesson) for lesson in db.lessons_for_course(course_id)]
    return web.json_response({"lessons": items})


async def create_lesson(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    course_id = _course_id_from(data)
    _require_grade_manager(db, user_id, course_id)

    title = str(data.get("title", "")).strip()[:MAX_ASSIGNMENT_TITLE_LEN]
    description = str(data.get("description", "")).strip()[:MAX_DESCRIPTION_LEN]
    video_url = str(data.get("video_url", "")).strip()[:MAX_VIDEO_URL_LEN]
    if not title:
        raise web.HTTPBadRequest(reason="title is required")
    filename, content_type, file_bytes = _decode_optional_file(data)

    lesson_id = db.create_lesson(course_id, title, description, video_url, user_id,
                                  filename, content_type, file_bytes)
    return web.json_response({"ok": True, "lesson": _public_lesson(db.get_lesson(lesson_id))}, status=201)


async def delete_lesson(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    lesson_id = _int_field(data, "lesson_id")
    lesson = db.get_lesson(lesson_id)
    if lesson is None:
        raise web.HTTPNotFound(reason="lesson not found")
    _require_grade_manager(db, user_id, lesson["course_id"])
    db.delete_lesson(lesson_id)
    return web.json_response({"ok": True})


async def lesson_file(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    try:
        lesson_id = int(request.match_info["lesson_id"])
    except ValueError:
        raise web.HTTPBadRequest(reason="invalid lesson id")
    lesson = db.get_lesson(lesson_id)
    if lesson is None or lesson["file_data"] is None:
        raise web.HTTPNotFound(reason="file not found")
    _require_enrolled_or_manager(db, user_id, lesson["course_id"])
    return _attachment(lesson["file_data"], lesson["file_type"], lesson["file_name"])


def _validate_options(data: dict) -> tuple[str, list[str], int]:
    text = str(data.get("text", "")).strip()[:MAX_QUESTION_TEXT_LEN]
    options = data.get("options")
    if not text:
        raise web.HTTPBadRequest(reason="text is required")
    if not isinstance(options, list) or not (MIN_OPTIONS <= len(options) <= MAX_OPTIONS):
        raise web.HTTPBadRequest(reason=f"options must be a list of {MIN_OPTIONS}-{MAX_OPTIONS} strings")
    options = [str(o).strip()[:MAX_OPTION_LEN] for o in options]
    if any(not o for o in options):
        raise web.HTTPBadRequest(reason="options can't be empty")
    try:
        correct_index = int(data.get("correct_index"))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(reason="correct_index is required")
    if not 0 <= correct_index < len(options):
        raise web.HTTPBadRequest(reason="correct_index out of range")
    return text, options, correct_index


async def add_quiz_question(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    lesson_id = _int_field(data, "lesson_id")
    lesson = db.get_lesson(lesson_id)
    if lesson is None:
        raise web.HTTPNotFound(reason="lesson not found")
    _require_grade_manager(db, user_id, lesson["course_id"])

    text, options, correct_index = _validate_options(data)
    question_id = db.add_quiz_question(lesson_id, text, options, correct_index)
    return web.json_response({"ok": True, "id": question_id}, status=201)


async def delete_quiz_question(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    question_id = _int_field(data, "question_id")
    question = db.get_quiz_question(question_id)
    if question is None:
        raise web.HTTPNotFound(reason="question not found")
    lesson = db.get_lesson(question["lesson_id"])
    _require_grade_manager(db, user_id, lesson["course_id"])
    db.delete_quiz_question(question_id)
    return web.json_response({"ok": True})


async def get_quiz(request: web.Request) -> web.Response:
    """Студент видит вопросы без правильного индекса (чтобы не подсматривать), преподаватель
    этой дисциплины/админ — с ним (для редактирования/проверки)."""
    user_id = require_session(request)
    db: Database = request.app["db"]
    try:
        lesson_id = int(request.match_info["lesson_id"])
    except ValueError:
        raise web.HTTPBadRequest(reason="invalid lesson id")
    lesson = db.get_lesson(lesson_id)
    if lesson is None:
        raise web.HTTPNotFound(reason="lesson not found")
    is_manager = _require_enrolled_or_manager(db, user_id, lesson["course_id"])

    questions = []
    for q in db.quiz_questions_for_lesson(lesson_id):
        item = {"id": q["id"], "text": q["text"], "options": json.loads(q["options"])}
        if is_manager:
            item["correct_index"] = q["correct_index"]
        questions.append(item)
    return web.json_response({"questions": questions})


async def submit_quiz(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    lesson_id = _int_field(data, "lesson_id")
    lesson = db.get_lesson(lesson_id)
    if lesson is None:
        raise web.HTTPNotFound(reason="lesson not found")
    if not db.is_enrolled(user_id, lesson["course_id"]):
        raise web.HTTPForbidden(reason="not enrolled in this course")

    answers = data.get("answers")
    if not isinstance(answers, list):
        raise web.HTTPBadRequest(reason="answers must be a list of option indices")
    try:
        answers = [int(a) for a in answers]
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(reason="answers must be integers")

    result = db.submit_quiz_attempt(lesson_id, user_id, answers)
    return web.json_response({"ok": True, **result})


async def my_quiz_attempt(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    lesson_id = _int_field(dict(request.query), "lesson_id")
    attempt = db.get_quiz_attempt(lesson_id, user_id)
    if attempt is None:
        return web.json_response({"attempt": None})
    return web.json_response({"attempt": {
        "correct_count": attempt["correct_count"], "total_count": attempt["total_count"],
        "score": attempt["score"], "submitted_at": attempt["submitted_at"],
    }})


async def lesson_attempts(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    try:
        lesson_id = int(request.match_info["lesson_id"])
    except ValueError:
        raise web.HTTPBadRequest(reason="invalid lesson id")
    lesson = db.get_lesson(lesson_id)
    if lesson is None:
        raise web.HTTPNotFound(reason="lesson not found")
    _require_grade_manager(db, user_id, lesson["course_id"])

    out = [{"user_id": a["user_id"], "first_name": a["first_name"], "last_name": a["last_name"],
            "correct_count": a["correct_count"], "total_count": a["total_count"], "score": a["score"],
            "submitted_at": a["submitted_at"]} for a in db.quiz_attempts_for_lesson(lesson_id)]
    return web.json_response({"attempts": out})


def _public_exam(row) -> dict:
    return {
        "id": row["id"], "course_id": row["course_id"], "title": row["title"],
        "description": row["description"], "time_limit_minutes": row["time_limit_minutes"],
        "require_recording": bool(row["require_recording"]),
        "require_camera": bool(row["require_camera"]),
    }


def _is_course_manager(db: Database, user_id: int, course_id: int) -> bool:
    user = _require_user(db, user_id)
    course = _require_course(db, course_id)
    return user["role"] == "admin" or (user["role"] == "teacher" and course["teacher_id"] == user_id)


def _exam_or_404(db: Database, exam_id: int):
    exam = db.get_exam(exam_id)
    if exam is None:
        raise web.HTTPNotFound(reason="exam not found")
    return exam


def _seconds_left(exam, started_at: str) -> int | None:
    """Сколько секунд осталось до конца экзамена по часам сервера; None — лимита нет."""
    if not exam["time_limit_minutes"]:
        return None
    deadline = datetime.fromisoformat(started_at) + timedelta(minutes=exam["time_limit_minutes"])
    return max(0, int((deadline - datetime.now(timezone.utc)).total_seconds()))


def _is_late(exam, duration_seconds: int | None) -> bool:
    if not exam["time_limit_minutes"] or duration_seconds is None:
        return False
    return duration_seconds > exam["time_limit_minutes"] * 60 + LATE_GRACE_SECONDS


async def list_exams(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    try:
        course_id = int(request.match_info["course_id"])
    except ValueError:
        raise web.HTTPBadRequest(reason="invalid course id")
    _require_enrolled_or_manager(db, user_id, course_id)
    items = [_public_exam(e) for e in db.exams_for_course(course_id)]
    return web.json_response({"exams": items})


async def create_exam(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    course_id = _course_id_from(data)
    _require_grade_manager(db, user_id, course_id)

    title = str(data.get("title", "")).strip()[:MAX_EXAM_TITLE_LEN]
    description = str(data.get("description", "")).strip()[:MAX_DESCRIPTION_LEN]
    if not title:
        raise web.HTTPBadRequest(reason="title is required")
    time_limit = data.get("time_limit_minutes")
    if time_limit not in (None, ""):
        try:
            time_limit = int(time_limit)
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(reason="time_limit_minutes must be an integer")
        if not 1 <= time_limit <= 600:
            raise web.HTTPBadRequest(reason="time_limit_minutes must be between 1 and 600")
    else:
        time_limit = None

    exam_id = db.create_exam(course_id, title, description, time_limit, bool(data.get("require_recording")),
                             user_id, require_camera=bool(data.get("require_camera")))
    return web.json_response({"ok": True, "exam": _public_exam(db.get_exam(exam_id))}, status=201)


async def delete_exam(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    exam_id = _int_field(data, "exam_id")
    exam = _exam_or_404(db, exam_id)
    _require_grade_manager(db, user_id, exam["course_id"])
    db.delete_exam(exam_id)
    return web.json_response({"ok": True})


async def add_exam_question(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    exam_id = _int_field(data, "exam_id")
    exam = _exam_or_404(db, exam_id)
    _require_grade_manager(db, user_id, exam["course_id"])

    text, options, correct_index = _validate_options(data)
    question_id = db.add_exam_question(exam_id, text, options, correct_index)
    return web.json_response({"ok": True, "id": question_id}, status=201)


async def delete_exam_question(request: web.Request) -> web.Response:
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    question_id = _int_field(data, "question_id")
    question = db.get_exam_question(question_id)
    if question is None:
        raise web.HTTPNotFound(reason="question not found")
    exam = db.get_exam(question["exam_id"])
    _require_grade_manager(db, user_id, exam["course_id"])
    db.delete_exam_question(question_id)
    return web.json_response({"ok": True})


def _student_questions(db: Database, exam_id: int) -> list[dict]:
    return [{"id": q["id"], "text": q["text"], "options": json.loads(q["options"])}
            for q in db.exam_questions_for_exam(exam_id)]


async def get_exam_questions(request: web.Request) -> web.Response:
    """Вопросы с правильными ответами — только преподавателю этой дисциплины/админу (редактор).
    Студент получает вопросы через POST /exams/start: до старта экзамена они ему не видны."""
    user_id = require_session(request)
    db: Database = request.app["db"]
    try:
        exam_id = int(request.match_info["exam_id"])
    except ValueError:
        raise web.HTTPBadRequest(reason="invalid exam id")
    exam = _exam_or_404(db, exam_id)
    if not _is_course_manager(db, user_id, exam["course_id"]):
        raise web.HTTPForbidden(reason="start the exam to see its questions")

    questions = [{"id": q["id"], "text": q["text"], "options": json.loads(q["options"]),
                  "correct_index": q["correct_index"]} for q in db.exam_questions_for_exam(exam_id)]
    return web.json_response({"questions": questions, "exam": _public_exam(exam)})


def _require_exam_taker(db: Database, user_id: int, exam):
    if not db.is_enrolled(user_id, exam["course_id"]):
        raise web.HTTPForbidden(reason="not enrolled in this course")
    if db.get_exam_attempt(exam["id"], user_id) is not None:
        raise web.HTTPConflict(reason="exam already submitted")


def _require_running_session(db: Database, user_id: int, exam):
    _require_exam_taker(db, user_id, exam)
    session = db.get_exam_session(exam["id"], user_id)
    if session is None:
        raise web.HTTPConflict(reason="exam is not started")
    return session


async def start_exam(request: web.Request) -> web.Response:
    """Студент начинает экзамен: сервер запоминает время старта (от него считается лимит —
    часы браузера подкрутить бесполезно) и только теперь отдаёт вопросы. Повторный вызов
    после перезагрузки страницы возвращает тот же сеанс с оставшимся временем."""
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    exam = _exam_or_404(db, _int_field(data, "exam_id"))
    _require_exam_taker(db, user_id, exam)

    started_at = db.start_exam_session(exam["id"], user_id)
    return web.json_response({
        "ok": True, "exam": _public_exam(exam), "questions": _student_questions(db, exam["id"]),
        "started_at": started_at, "seconds_left": _seconds_left(exam, started_at),
    })


async def exam_snapshot(request: web.Request) -> web.Response:
    """Кадр с веб-камеры во время экзамена. Кадры уходят по одному, пока экзамен идёт."""
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    exam = _exam_or_404(db, _int_field(data, "exam_id"))
    _require_running_session(db, user_id, exam)

    try:
        image = base64.b64decode(str(data.get("data", "")), validate=True)
    except Exception:
        raise web.HTTPBadRequest(reason="invalid snapshot data")
    if not image or len(image) > MAX_SNAPSHOT_BYTES or sniff_image_type(image) != "image/jpeg":
        raise web.HTTPBadRequest(reason="snapshot must be a JPEG up to 300 KB")
    if db.count_exam_snapshots(exam["id"], user_id) >= MAX_SNAPSHOTS:
        return web.json_response({"ok": True, "stored": False})
    db.add_exam_snapshot(exam["id"], user_id, image)
    return web.json_response({"ok": True, "stored": True})


async def exam_recording_chunk(request: web.Request) -> web.Response:
    """Кусок записи экрана. Запись уходит на сервер частями по ходу экзамена, а не одним
    огромным файлом в конце: длинный экзамен не упирается в лимит размера запроса, а при
    обрыве связи пропадает только последний кусок, а не вся запись."""
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    exam = _exam_or_404(db, _int_field(data, "exam_id"))
    _require_running_session(db, user_id, exam)

    seq = _int_field(data, "seq")
    if not 0 <= seq < MAX_RECORDING_CHUNKS:
        raise web.HTTPBadRequest(reason="seq out of range")
    try:
        chunk = base64.b64decode(str(data.get("data", "")), validate=True)
    except Exception:
        raise web.HTTPBadRequest(reason="invalid chunk data")
    if not chunk or len(chunk) > MAX_RECORDING_CHUNK_BYTES:
        raise web.HTTPBadRequest(reason="chunk too large or empty")
    if db.exam_recording_size(exam["id"], user_id) + len(chunk) > MAX_RECORDING_BYTES:
        raise web.HTTPRequestEntityTooLarge(max_size=MAX_RECORDING_BYTES, actual_size=MAX_RECORDING_BYTES + 1)
    db.add_exam_recording_chunk(exam["id"], user_id, seq, chunk)
    return web.json_response({"ok": True})


def _decode_optional_recording(data: dict) -> bytes | None:
    """Запись целиком в теле сдачи — старый способ, оставлен для совместимости (короткие записи)."""
    file = data.get("recording")
    if not file:
        return None
    if not isinstance(file, dict):
        raise web.HTTPBadRequest(reason="recording must be an object with filename, content_type, data")
    content_type = str(file.get("content_type", "")).strip().lower()
    if content_type not in RECORDING_CONTENT_TYPES:
        raise web.HTTPBadRequest(reason=f"content_type must be one of {sorted(RECORDING_CONTENT_TYPES)}")
    try:
        file_bytes = base64.b64decode(str(file.get("data", "")), validate=True)
    except Exception:
        raise web.HTTPBadRequest(reason="invalid recording data")
    if not file_bytes or len(file_bytes) > MAX_RECORDING_BYTES:
        raise web.HTTPBadRequest(reason="recording too large or empty")
    return file_bytes


def _clamped_int(data: dict, key: str, maximum: int) -> int:
    try:
        value = int(data.get(key, 0))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(reason=f"{key} must be an integer")
    return max(0, min(value, maximum))


async def submit_exam(request: web.Request) -> web.Response:
    """Сдача экзамена — один раз. Пересдать можно, только если преподаватель сбросил попытку.
    Правильные ответы в ответе не возвращаются: студент видит только свой балл."""
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    exam_id = _int_field(data, "exam_id")
    exam = _exam_or_404(db, exam_id)
    _require_exam_taker(db, user_id, exam)

    answers = data.get("answers")
    if not isinstance(answers, list):
        raise web.HTTPBadRequest(reason="answers must be a list of option indices")
    try:
        answers = [int(a) for a in answers]
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(reason="answers must be integers")
    tab_exit_count = _clamped_int(data, "tab_exit_count", MAX_TAB_EXIT_COUNT)
    away_seconds = _clamped_int(data, "away_seconds", MAX_AWAY_SECONDS)

    db.start_exam_session(exam_id, user_id)  # на случай сдачи без явного старта — сеанс всё равно нужен
    legacy_recording = _decode_optional_recording(data)
    if legacy_recording is not None:
        db.add_exam_recording_chunk(exam_id, user_id, 0, legacy_recording)
    if exam["require_recording"] and db.exam_recording_size(exam_id, user_id) == 0:
        raise web.HTTPBadRequest(reason="this exam requires a screen recording to submit")
    if exam["require_camera"] and db.count_exam_snapshots(exam_id, user_id) == 0:
        raise web.HTTPBadRequest(reason="this exam requires camera snapshots to submit")

    result = db.submit_exam_attempt(exam_id, user_id, answers, tab_exit_count, away_seconds=away_seconds)
    return web.json_response({"ok": True, **result, "late": _is_late(exam, result["duration_seconds"])})


async def my_exam_attempt(request: web.Request) -> web.Response:
    """Моя попытка (если сдал) или незавершённый сеанс (если начал и не сдал — можно продолжить)."""
    user_id = require_session(request)
    db: Database = request.app["db"]
    exam_id = _int_field(dict(request.query), "exam_id")
    attempt = db.get_exam_attempt(exam_id, user_id)
    if attempt is None:
        session, exam = db.get_exam_session(exam_id, user_id), db.get_exam(exam_id)
        in_progress = None
        if session is not None and exam is not None:
            in_progress = {"started_at": session["started_at"],
                           "seconds_left": _seconds_left(exam, session["started_at"])}
        return web.json_response({"attempt": None, "in_progress": in_progress})
    return web.json_response({"attempt": {
        "correct_count": attempt["correct_count"], "total_count": attempt["total_count"],
        "score": attempt["score"], "tab_exit_count": attempt["tab_exit_count"],
        "away_seconds": attempt["away_seconds"], "duration_seconds": attempt["duration_seconds"],
        "has_recording": attempt["recording_data"] is not None or db.exam_recording_size(exam_id, user_id) > 0,
        "snapshot_count": db.count_exam_snapshots(exam_id, user_id),
        "submitted_at": attempt["submitted_at"],
    }, "in_progress": None})


async def exam_attempts(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    try:
        exam_id = int(request.match_info["exam_id"])
    except ValueError:
        raise web.HTTPBadRequest(reason="invalid exam id")
    exam = _exam_or_404(db, exam_id)
    _require_grade_manager(db, user_id, exam["course_id"])

    out = [{"id": a["id"], "user_id": a["user_id"], "first_name": a["first_name"], "last_name": a["last_name"],
            "correct_count": a["correct_count"], "total_count": a["total_count"], "score": a["score"],
            "tab_exit_count": a["tab_exit_count"], "away_seconds": a["away_seconds"],
            "duration_seconds": a["duration_seconds"], "late": _is_late(exam, a["duration_seconds"]),
            "has_recording": bool(a["has_recording"]), "snapshot_count": a["snapshot_count"],
            "submitted_at": a["submitted_at"]} for a in db.exam_attempts_for_exam(exam_id)]
    unfinished = [{"user_id": s["user_id"], "first_name": s["first_name"], "last_name": s["last_name"],
                   "started_at": s["started_at"]} for s in db.unfinished_exam_sessions(exam_id)]
    return web.json_response({"attempts": out, "unfinished": unfinished})


def _attempt_for_manager(request: web.Request, user_id: int, allow_owner: bool = False):
    db: Database = request.app["db"]
    try:
        attempt_id = int(request.match_info["attempt_id"])
    except ValueError:
        raise web.HTTPBadRequest(reason="invalid attempt id")
    attempt = db.get_exam_attempt_by_id(attempt_id)
    if attempt is None:
        raise web.HTTPNotFound(reason="attempt not found")
    exam = db.get_exam(attempt["exam_id"])
    is_owner = allow_owner and attempt["user_id"] == user_id
    if not is_owner and not _is_course_manager(db, user_id, exam["course_id"]):
        raise web.HTTPForbidden(reason="not allowed")
    return attempt


async def exam_recording(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    attempt = _attempt_for_manager(request, user_id, allow_owner=True)
    chunks = db.exam_recording_chunks(attempt["exam_id"], attempt["user_id"])
    if chunks:
        return _attachment(b"".join(chunks), "video/webm", f"exam-{attempt['id']}.webm")
    if attempt["recording_data"] is None:
        raise web.HTTPNotFound(reason="recording not found")
    return _attachment(attempt["recording_data"], attempt["recording_type"], attempt["recording_name"])


async def exam_attempt_snapshots(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    attempt = _attempt_for_manager(request, user_id)
    rows = db.exam_snapshots_meta(attempt["exam_id"], attempt["user_id"])
    return web.json_response({"snapshots": [{"id": r["id"], "taken_at": r["taken_at"]} for r in rows]})


async def exam_snapshot_image(request: web.Request) -> web.Response:
    user_id = require_session(request)
    db: Database = request.app["db"]
    try:
        snapshot_id = int(request.match_info["snapshot_id"])
    except ValueError:
        raise web.HTTPBadRequest(reason="invalid snapshot id")
    snapshot = db.get_exam_snapshot(snapshot_id)
    if snapshot is None:
        raise web.HTTPNotFound(reason="snapshot not found")
    exam = _exam_or_404(db, snapshot["exam_id"])
    if not _is_course_manager(db, user_id, exam["course_id"]):
        raise web.HTTPForbidden(reason="not allowed")
    return web.Response(body=snapshot["data"], content_type="image/jpeg")


async def reset_exam_attempt(request: web.Request) -> web.Response:
    """Преподаватель разрешает пересдачу: удаляет попытку студента (или незавершённый сеанс)
    вместе с записью экрана и снимками камеры."""
    user_id = require_session(request)
    data = await read_json_body(request)
    db: Database = request.app["db"]
    exam = _exam_or_404(db, _int_field(data, "exam_id"))
    _require_grade_manager(db, user_id, exam["course_id"])
    db.reset_exam_attempt(exam["id"], _int_field(data, "user_id"))
    return web.json_response({"ok": True})


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
    app.router.add_post("/api/academics/courses/update", update_course)
    app.router.add_post("/api/academics/courses/delete", delete_course)
    app.router.add_post("/api/academics/courses/set-teacher", set_course_teacher)
    app.router.add_get("/api/academics/teachers", list_teachers)
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
    app.router.add_get("/api/academics/courses/{course_id}/assignments", list_assignments)
    app.router.add_post("/api/academics/assignments", create_assignment)
    app.router.add_post("/api/academics/assignments/delete", delete_assignment)
    app.router.add_get("/api/academics/assignments/{assignment_id}/file", assignment_file)
    app.router.add_post("/api/academics/submissions", submit_assignment)
    app.router.add_get("/api/academics/my-submission", my_submission)
    app.router.add_get("/api/academics/assignments/{assignment_id}/submissions", assignment_submissions)
    app.router.add_get("/api/academics/submissions/{submission_id}/file", submission_file)
    app.router.add_post("/api/academics/submissions/grade", grade_submission)
    app.router.add_get("/api/academics/courses/{course_id}/lessons", list_lessons)
    app.router.add_post("/api/academics/lessons", create_lesson)
    app.router.add_post("/api/academics/lessons/delete", delete_lesson)
    app.router.add_get("/api/academics/lessons/{lesson_id}/file", lesson_file)
    app.router.add_post("/api/academics/quiz-questions", add_quiz_question)
    app.router.add_post("/api/academics/quiz-questions/delete", delete_quiz_question)
    app.router.add_get("/api/academics/lessons/{lesson_id}/quiz", get_quiz)
    app.router.add_post("/api/academics/quiz/submit", submit_quiz)
    app.router.add_get("/api/academics/quiz/my-attempt", my_quiz_attempt)
    app.router.add_get("/api/academics/lessons/{lesson_id}/attempts", lesson_attempts)
    app.router.add_get("/api/academics/courses/{course_id}/exams", list_exams)
    app.router.add_post("/api/academics/exams", create_exam)
    app.router.add_post("/api/academics/exams/delete", delete_exam)
    app.router.add_post("/api/academics/exam-questions", add_exam_question)
    app.router.add_post("/api/academics/exam-questions/delete", delete_exam_question)
    app.router.add_get("/api/academics/exams/{exam_id}/questions", get_exam_questions)
    app.router.add_post("/api/academics/exams/start", start_exam)
    app.router.add_post("/api/academics/exams/snapshot", exam_snapshot)
    app.router.add_post("/api/academics/exams/recording-chunk", exam_recording_chunk)
    app.router.add_post("/api/academics/exams/submit", submit_exam)
    app.router.add_get("/api/academics/exams/my-attempt", my_exam_attempt)
    app.router.add_get("/api/academics/exams/{exam_id}/attempts", exam_attempts)
    app.router.add_get("/api/academics/exam-attempts/{attempt_id}/recording", exam_recording)
    app.router.add_get("/api/academics/exam-attempts/{attempt_id}/snapshots", exam_attempt_snapshots)
    app.router.add_get("/api/academics/exam-snapshots/{snapshot_id}", exam_snapshot_image)
    app.router.add_post("/api/academics/exam-attempts/reset", reset_exam_attempt)
