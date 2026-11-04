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
