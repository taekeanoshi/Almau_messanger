"""Тесты учёбы: дисциплины, запись на них, расписание и оценки (первый модуль Platonus-стиля)."""
import asyncio

from aiohttp.test_utils import TestClient, TestServer

import bot.web_chat as web_chat_module
from bot.api import build_app
from bot.config import BASE_DIR
from bot.content import Content
from bot.db import Database


def make_client(admin_token="admintok"):
    db = Database(":memory:")
    content = Content(BASE_DIR / "site" / "data")
    app = build_app(db, content, bot=None, admin_ids=frozenset(), admin_token=admin_token)
    return TestClient(TestServer(app)), db


def run(coro):
    return asyncio.run(coro)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def capture_otp_emails(monkeypatch):
    sent: dict[str, str] = {}

    async def fake_send(app, to_email, code):
        sent[to_email] = code

    monkeypatch.setattr(web_chat_module, "_send_otp_email", fake_send)
    return sent


async def _register_and_verify(client, sent, first_name, last_name, phone, email, password, gender):
    resp = await client.post("/api/chat/register", json={
        "first_name": first_name, "last_name": last_name, "phone": phone, "email": email,
        "password": password, "gender": gender, "course": "2", "specialty": "CS",
    })
    assert resp.status == 201, await resp.text()
    resp = await client.post("/api/chat/otp/verify", json={"email": email, "code": sent[email]})
    assert resp.status == 200
    return await resp.json()


async def _rotate_key(client, role, admin_token="admintok"):
    resp = await client.post("/api/admin/rotate-invite-key", params={"token": admin_token}, json={"role": role})
    assert resp.status == 200, await resp.text()
    return (await resp.json())["invite_key"]


async def _register_staff_and_verify(client, sent, first_name, last_name, phone, email, password,
                                      account_type, invite_key, status=201):
    resp = await client.post("/api/chat/register", json={
        "first_name": first_name, "last_name": last_name, "phone": phone, "email": email,
        "password": password, "account_type": account_type, "invite_key": invite_key,
    })
    assert resp.status == status, await resp.text()
    if status != 201:
        return None
    resp = await client.post("/api/chat/otp/verify", json={"email": email, "code": sent[email]})
    assert resp.status == 200
    return await resp.json()


def test_student_enroll_and_unenroll(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            u = await _register_and_verify(client, sent, "Аян", "Касым", "+77001110001",
                                           "a@example.com", "secret1", "male")
            t = u["token"]
            course_id = db.create_course("CS101", "Введение в программирование")

            resp = await client.get("/api/academics/courses", headers=auth(t))
            courses = (await resp.json())["courses"]
            assert len(courses) == 1 and courses[0]["code"] == "CS101"

            resp = await client.get("/api/academics/my-courses", headers=auth(t))
            assert (await resp.json())["courses"] == []

            resp = await client.post("/api/academics/enroll", headers=auth(t), json={"course_id": course_id})
            assert resp.status == 200

            resp = await client.get("/api/academics/my-courses", headers=auth(t))
            courses = (await resp.json())["courses"]
            assert len(courses) == 1 and courses[0]["id"] == course_id

            # Повторная запись не плодит дубликаты
            await client.post("/api/academics/enroll", headers=auth(t), json={"course_id": course_id})
            resp = await client.get("/api/academics/my-courses", headers=auth(t))
            assert len((await resp.json())["courses"]) == 1

            resp = await client.post("/api/academics/unenroll", headers=auth(t), json={"course_id": course_id})
            assert resp.status == 200
            resp = await client.get("/api/academics/my-courses", headers=auth(t))
            assert (await resp.json())["courses"] == []

    run(go())


def test_student_cannot_manage_courses(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            u = await _register_and_verify(client, sent, "Аян", "Касым", "+77001110001",
                                           "a@example.com", "secret1", "male")
            t = u["token"]

            resp = await client.post("/api/academics/courses", headers=auth(t),
                                     json={"code": "CS101", "title": "Hack"})
            assert resp.status == 403

            course_id = db.create_course("CS101", "Введение")
            resp = await client.post("/api/academics/schedule", headers=auth(t), json={
                "course_id": course_id, "day_of_week": 0, "start_time": "09:00", "end_time": "10:30",
            })
            assert resp.status == 403

            resp = await client.post("/api/academics/grades/set", headers=auth(t), json={
                "course_id": course_id, "user_id": u["user"]["id"], "label": "РК1", "score": 100,
            })
            assert resp.status == 403

    run(go())


def test_teacher_flow_schedule_and_grades(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client(admin_token="admintok")
        async with client:
            teacher = await _register_and_verify(client, sent, "Берик", "Нурлан", "+77001110002",
                                                 "teacher@example.com", "secret1", "male")
            student = await _register_and_verify(client, sent, "Аян", "Касым", "+77001110001",
                                                 "a@example.com", "secret1", "male")
            other_student = await _register_and_verify(client, sent, "Әсел", "Жанар", "+77001110003",
                                                        "c@example.com", "secret1", "female")
            t_teacher, t_student = teacher["token"], student["token"]

            resp = await client.post("/api/admin/set-role", params={"token": "admintok"},
                                     json={"email": "teacher@example.com", "role": "teacher"})
            assert resp.status == 200

            # Теперь можно создать дисциплину — себе в teacher_id
            resp = await client.post("/api/academics/courses", headers=auth(t_teacher),
                                     json={"code": "CS101", "title": "Введение в программирование",
                                           "description": "Основы Python"})
            assert resp.status == 201
            course_id = (await resp.json())["course"]["id"]

            # Студент записывается
            await client.post("/api/academics/enroll", headers=auth(t_student), json={"course_id": course_id})

            # Преподаватель добавляет пару в расписание
            resp = await client.post("/api/academics/schedule", headers=auth(t_teacher), json={
                "course_id": course_id, "day_of_week": 0, "start_time": "09:00", "end_time": "10:30",
                "room": "301", "kind": "лекция",
            })
            assert resp.status == 201

            # У студента она появляется в личном расписании
            resp = await client.get("/api/academics/schedule", headers=auth(t_student))
            schedule = (await resp.json())["schedule"]
            assert len(schedule) == 1 and schedule[0]["room"] == "301" and schedule[0]["course_code"] == "CS101"

            # Преподаватель видит список записанных (ростер)
            resp = await client.get(f"/api/academics/courses/{course_id}/roster", headers=auth(t_teacher))
            roster = (await resp.json())["students"]
            assert len(roster) == 1 and roster[0]["first_name"] == "Аян"

            # Нельзя поставить оценку незаписанному студенту
            resp = await client.post("/api/academics/grades/set", headers=auth(t_teacher), json={
                "course_id": course_id, "user_id": other_student["user"]["id"], "label": "РК1", "score": 80,
            })
            assert resp.status == 400

            # Ставит оценку записанному
            resp = await client.post("/api/academics/grades/set", headers=auth(t_teacher), json={
                "course_id": course_id, "user_id": student["user"]["id"],
                "label": "РК1", "score": 85, "max_score": 100, "comment": "Хорошо",
            })
            assert resp.status == 200

            # Повторная простановка той же метки обновляет, а не дублирует
            resp = await client.post("/api/academics/grades/set", headers=auth(t_teacher), json={
                "course_id": course_id, "user_id": student["user"]["id"], "label": "РК1", "score": 90,
            })
            assert resp.status == 200

            # Студент видит свою (одну) оценку
            resp = await client.get("/api/academics/grades", headers=auth(t_student))
            grades = (await resp.json())["grades"]
            assert len(grades) == 1 and grades[0]["score"] == 90 and grades[0]["label"] == "РК1"

            # Другой студент (не записан, не участник) не видит эту оценку в своей выдаче
            resp = await client.get("/api/academics/grades", headers=auth(other_student["token"]))
            assert (await resp.json())["grades"] == []

            # Чужой преподаватель без роли admin не может трогать эту дисциплину
            other_teacher = await _register_and_verify(client, sent, "Данияр", "Ким", "+77001110004",
                                                        "d@example.com", "secret1", "male")
            await client.post("/api/admin/set-role", params={"token": "admintok"},
                              json={"email": "d@example.com", "role": "teacher"})
            resp = await client.post("/api/academics/grades/set", headers=auth(other_teacher["token"]), json={
                "course_id": course_id, "user_id": student["user"]["id"], "label": "РК2", "score": 70,
            })
            assert resp.status == 403

    run(go())


def test_set_role_requires_admin_token_and_known_email():
    async def go():
        client, db = make_client(admin_token="admintok")
        async with client:
            resp = await client.post("/api/admin/set-role", json={"email": "x@example.com", "role": "teacher"})
            assert resp.status == 401

            resp = await client.post("/api/admin/set-role", params={"token": "wrong"},
                                     json={"email": "x@example.com", "role": "teacher"})
            assert resp.status == 401

            resp = await client.post("/api/admin/set-role", params={"token": "admintok"},
                                     json={"email": "nobody@example.com", "role": "teacher"})
            assert resp.status == 404

            resp = await client.post("/api/admin/set-role", params={"token": "admintok"},
                                     json={"email": "x@example.com", "role": "wizard"})
            assert resp.status == 400

    run(go())


def test_teacher_registration_requires_valid_invite_key(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client(admin_token="admintok")
        async with client:
            # Без ключа вообще не зарегистрироваться
            resp = await client.post("/api/chat/register", json={
                "first_name": "Берик", "last_name": "Нурлан", "phone": "+77001110002",
                "email": "teacher@example.com", "password": "secret1", "account_type": "teacher",
            })
            assert resp.status == 400

            # С неверным ключом — тоже нет
            resp = await client.post("/api/chat/register", json={
                "first_name": "Берик", "last_name": "Нурлан", "phone": "+77001110002",
                "email": "teacher@example.com", "password": "secret1",
                "account_type": "teacher", "invite_key": "garbage",
            })
            assert resp.status == 403

            key = await _rotate_key(client, "teacher")
            teacher = await _register_staff_and_verify(client, sent, "Берик", "Нурлан", "+77001110002",
                                                        "teacher@example.com", "secret1", "teacher", key)
            assert teacher["user"]["gender"] == ""

            resp = await client.get("/api/chat/me", headers=auth(teacher["token"]))
            me = await resp.json()
            assert me["course"] == "" and me["specialty"] == ""  # курс/специальность не собираем

            # Старый ключ после перевыпуска больше не работает
            resp = await client.post("/api/chat/register", json={
                "first_name": "Второй", "last_name": "Учитель", "phone": "+77001110005",
                "email": "teacher2@example.com", "password": "secret1",
                "account_type": "teacher", "invite_key": key,
            })
            assert resp.status == 201  # ключ ещё не перевыпускали — пока валиден

            new_key = await _rotate_key(client, "teacher")
            assert new_key != key
            resp = await client.post("/api/chat/register", json={
                "first_name": "Третий", "last_name": "Учитель", "phone": "+77001110006",
                "email": "teacher3@example.com", "password": "secret1",
                "account_type": "teacher", "invite_key": key,  # старый ключ
            })
            assert resp.status == 403

            # Неизвестный account_type отклоняется
            resp = await client.post("/api/chat/register", json={
                "first_name": "Х", "last_name": "Х", "phone": "+77001110007",
                "email": "x@example.com", "password": "secret1", "account_type": "wizard",
            })
            assert resp.status == 400

    run(go())


def test_teacher_cannot_use_anonymous_chat_or_event_going(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client(admin_token="admintok")
        async with client:
            key = await _rotate_key(client, "teacher")
            teacher = await _register_staff_and_verify(client, sent, "Берик", "Нурлан", "+77001110002",
                                                        "teacher@example.com", "secret1", "teacher", key)
            t = teacher["token"]

            resp = await client.post("/api/chat/search", headers=auth(t), json={"preference": "any"})
            assert resp.status == 403

            events = await (await client.get("/api/events")).json()
            event_id = events[0]["id"]
            resp = await client.post(f"/api/events/{event_id}/going", headers=auth(t), json={"going": True})
            assert resp.status == 403

    run(go())


def test_admin_account_can_use_everything(monkeypatch):
    """Личный аккаунт с ролью admin — не только управление учёбой: анонимный чат и
    отметка «Иду» на ивентах тоже доступны, в отличие от teacher/registrar."""
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client(admin_token="admintok")
        async with client:
            u = await _register_and_verify(client, sent, "Хамит", "Хасанов", "+77001110009",
                                           "owner@example.com", "secret1", "male")
            resp = await client.post("/api/admin/set-role", params={"token": "admintok"},
                                     json={"email": "owner@example.com", "role": "admin"})
            assert resp.status == 200
            t = u["token"]

            resp = await client.post("/api/chat/search", headers=auth(t), json={"preference": "any"})
            assert resp.status == 200

            events = await (await client.get("/api/events")).json()
            event_id = events[0]["id"]
            resp = await client.post(f"/api/events/{event_id}/going", headers=auth(t), json={"going": True})
            assert resp.status == 200

            # И, как и раньше, полный доступ к учёбе: видит все дисциплины как "преподаваемые"
            other_key = await _rotate_key(client, "teacher")
            other_teacher = await _register_staff_and_verify(client, sent, "Берик", "Нурлан", "+77001110002",
                                                              "teacher@example.com", "secret1", "teacher", other_key)
            resp = await client.post("/api/academics/courses", headers=auth(other_teacher["token"]),
                                     json={"code": "CS101", "title": "Введение"})
            course_id = (await resp.json())["course"]["id"]

            resp = await client.get("/api/academics/teaching", headers=auth(t))
            courses = (await resp.json())["courses"]
            assert any(c["id"] == course_id for c in courses)

    run(go())


def test_multi_variant_schedule_choice(monkeypatch):
    """Дисциплина с двумя вариантами расписания (напр. математика по Пн или по Ср) —
    студент обязан выбрать один при записи и может сменить выбор позже."""
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client(admin_token="admintok")
        async with client:
            key = await _rotate_key(client, "teacher")
            teacher = await _register_staff_and_verify(client, sent, "Берик", "Нурлан", "+77001110002",
                                                        "teacher@example.com", "secret1", "teacher", key)
            student = await _register_and_verify(client, sent, "Аян", "Касым", "+77001110001",
                                                 "a@example.com", "secret1", "male")
            t_teacher, t_student = teacher["token"], student["token"]

            resp = await client.post("/api/academics/courses", headers=auth(t_teacher),
                                     json={"code": "MATH101", "title": "Математика"})
            course_id = (await resp.json())["course"]["id"]

            r1 = await client.post("/api/academics/schedule", headers=auth(t_teacher), json={
                "course_id": course_id, "day_of_week": 0, "start_time": "09:00", "end_time": "10:30",
            })
            item_mon = (await r1.json())["item"]["id"]
            r2 = await client.post("/api/academics/schedule", headers=auth(t_teacher), json={
                "course_id": course_id, "day_of_week": 2, "start_time": "11:00", "end_time": "12:30",
            })
            item_wed = (await r2.json())["item"]["id"]

            # Без выбора варианта записаться нельзя — вариантов несколько
            resp = await client.post("/api/academics/enroll", headers=auth(t_student), json={"course_id": course_id})
            assert resp.status == 400

            resp = await client.post("/api/academics/enroll", headers=auth(t_student),
                                     json={"course_id": course_id, "schedule_item_id": item_mon})
            assert resp.status == 200

            resp = await client.get("/api/academics/schedule", headers=auth(t_student))
            schedule = (await resp.json())["schedule"]
            assert len(schedule) == 1 and schedule[0]["id"] == item_mon  # только выбранный, не оба сразу

            # Студент передумал — переходит на среду
            resp = await client.post("/api/academics/schedule/choose", headers=auth(t_student),
                                     json={"course_id": course_id, "schedule_item_id": item_wed})
            assert resp.status == 200
            resp = await client.get("/api/academics/schedule", headers=auth(t_student))
            assert (await resp.json())["schedule"][0]["id"] == item_wed

    run(go())


def test_course_schedule_options_visible_to_any_logged_in_user(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client(admin_token="admintok")
        async with client:
            key = await _rotate_key(client, "teacher")
            teacher = await _register_staff_and_verify(client, sent, "Берик", "Нурлан", "+77001110002",
                                                        "teacher@example.com", "secret1", "teacher", key)
            student = await _register_and_verify(client, sent, "Аян", "Касым", "+77001110001",
                                                 "a@example.com", "secret1", "male")
            resp = await client.post("/api/academics/courses", headers=auth(teacher["token"]),
                                     json={"code": "MATH101", "title": "Математика"})
            course_id = (await resp.json())["course"]["id"]
            await client.post("/api/academics/schedule", headers=auth(teacher["token"]), json={
                "course_id": course_id, "day_of_week": 0, "start_time": "09:00", "end_time": "10:30",
            })

            # Обычный студент (не преподаватель этой дисциплины и не registrar/admin) тоже видит варианты
            resp = await client.get(f"/api/academics/courses/{course_id}/options", headers=auth(student["token"]))
            assert resp.status == 200
            assert len((await resp.json())["schedule"]) == 1

            resp = await client.get(f"/api/academics/courses/{course_id}/options")
            assert resp.status == 401

    run(go())


def test_registrar_manages_schedule_not_grades(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client(admin_token="admintok")
        async with client:
            teacher_key = await _rotate_key(client, "teacher")
            registrar_key = await _rotate_key(client, "registrar")
            teacher = await _register_staff_and_verify(client, sent, "Берик", "Нурлан", "+77001110002",
                                                        "teacher@example.com", "secret1", "teacher", teacher_key)
            registrar = await _register_staff_and_verify(client, sent, "Дана", "Серik", "+77001110004",
                                                          "registrar@example.com", "secret1", "registrar",
                                                          registrar_key)
            student = await _register_and_verify(client, sent, "Аян", "Касым", "+77001110001",
                                                 "a@example.com", "secret1", "male")
            t_teacher, t_registrar, t_student = teacher["token"], registrar["token"], student["token"]

            resp = await client.post("/api/academics/courses", headers=auth(t_teacher),
                                     json={"code": "CS101", "title": "Введение"})
            course_id = (await resp.json())["course"]["id"]
            await client.post("/api/academics/enroll", headers=auth(t_student), json={"course_id": course_id})

            # Отдел расписания может добавить вариант в чужую (не свою) дисциплину
            resp = await client.post("/api/academics/schedule", headers=auth(t_registrar), json={
                "course_id": course_id, "day_of_week": 1, "start_time": "08:00", "end_time": "09:30",
            })
            assert resp.status == 201
            item_id = (await resp.json())["item"]["id"]

            # Но оценки ставить не может
            resp = await client.post("/api/academics/grades/set", headers=auth(t_registrar), json={
                "course_id": course_id, "user_id": student["user"]["id"], "label": "РК1", "score": 90,
            })
            assert resp.status == 403

            # Может перевести студента на другой день/вариант расписания
            resp = await client.post("/api/academics/schedule/choose", headers=auth(t_registrar), json={
                "course_id": course_id, "schedule_item_id": item_id, "user_id": student["user"]["id"],
            })
            assert resp.status == 200

            # Может назначить другого преподавателя конкретному варианту
            other_teacher = await _register_staff_and_verify(client, sent, "Данияр", "Ким", "+77001110005",
                                                              "teacher2@example.com", "secret1", "teacher",
                                                              await _rotate_key(client, "teacher"))
            resp = await client.post("/api/academics/schedule/set-teacher", headers=auth(t_registrar),
                                     json={"item_id": item_id, "teacher_id": other_teacher["user"]["id"]})
            assert resp.status == 200

            # Сам преподаватель (не registrar/admin) переназначить преподавателя варианта не может
            resp = await client.post("/api/academics/schedule/set-teacher", headers=auth(t_teacher),
                                     json={"item_id": item_id, "teacher_id": teacher["user"]["id"]})
            assert resp.status == 403

    run(go())


def test_attendance_marking_and_automatic_retake(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client(admin_token="admintok")
        async with client:
            key = await _rotate_key(client, "teacher")
            teacher = await _register_staff_and_verify(client, sent, "Берик", "Нурлан", "+77001110002",
                                                        "teacher@example.com", "secret1", "teacher", key)
            student = await _register_and_verify(client, sent, "Аян", "Касым", "+77001110001",
                                                 "a@example.com", "secret1", "male")
            t_teacher, t_student = teacher["token"], student["token"]
            student_id = student["user"]["id"]

            resp = await client.post("/api/academics/courses", headers=auth(t_teacher),
                                     json={"code": "CS101", "title": "Введение"})
            course_id = (await resp.json())["course"]["id"]
            resp = await client.post("/api/academics/schedule", headers=auth(t_teacher), json={
                "course_id": course_id, "day_of_week": 0, "start_time": "09:00", "end_time": "10:30",
            })
            item_id = (await resp.json())["item"]["id"]
            await client.post("/api/academics/enroll", headers=auth(t_student), json={"course_id": course_id})

            def mark(date, present):
                return client.post("/api/academics/attendance/mark", headers=auth(t_teacher), json={
                    "schedule_item_id": item_id, "user_id": student_id, "class_date": date, "present": present,
                })

            # 2 присутствия, 1 пропуск — 33% > 30%, уходит на ретейк
            assert (await mark("2026-01-05", True)).status == 200
            assert (await mark("2026-01-12", True)).status == 200
            resp = await mark("2026-01-19", False)
            assert resp.status == 200
            stats = (await resp.json())["stats"]
            assert stats["total"] == 3 and stats["absent"] == 1 and stats["on_retake"] is True

            # Новую отметку поставить больше нельзя
            resp = await mark("2026-01-26", True)
            assert resp.status == 409

            # Но прошлую отметку поправить — можно (преподаватель передумал/ошибся)
            resp = await client.post("/api/academics/attendance/delete", headers=auth(t_teacher), json={
                "schedule_item_id": item_id, "user_id": student_id, "class_date": "2026-01-19",
            })
            assert resp.status == 200
            assert (await resp.json())["stats"]["on_retake"] is False

            # После снятия пропуска снова можно отмечать новые даты
            resp = await mark("2026-01-26", True)
            assert resp.status == 200

            # Студент видит свою статистику
            resp = await client.get("/api/academics/attendance", headers=auth(t_student),
                                     params={"course_id": course_id})
            body = await resp.json()
            assert body["stats"]["total"] == 3 and body["stats"]["on_retake"] is False

            # Чужой студент (не выбравший этот вариант) отметить его тоже нельзя
            other = await _register_and_verify(client, sent, "Әсел", "Жанар", "+77001110003",
                                               "c@example.com", "secret1", "female")
            resp = await client.post("/api/academics/attendance/mark", headers=auth(t_teacher), json={
                "schedule_item_id": item_id, "user_id": other["user"]["id"],
                "class_date": "2026-01-05", "present": True,
            })
            assert resp.status == 400

    run(go())
