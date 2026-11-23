"""Клубная система: демо-аккаунты, кабинет организатора, подписки и уведомления."""
import asyncio

from aiohttp.test_utils import TestClient, TestServer

from bot.api import build_app
from bot.config import BASE_DIR
from bot.content import Content
from bot.db import Database
from bot.demo_club import (DEFAULT_ORGANIZER_PASSWORD, DEFAULT_STUDENT_PASSWORD, ORGANIZER_EMAIL, STUDENT_EMAIL,
                           seed_demo_club)
from bot.demo_people import seed_demo_people


def make():
    db = Database(":memory:")
    content = Content(BASE_DIR / "site" / "data")
    db.hikes.seed(content.hikes_seed)
    seed_demo_people(db, 30)
    seed_demo_club(db)
    return TestClient(TestServer(build_app(db, content))), db


async def login(client, email, password):
    resp = await client.post("/api/chat/login", json={"email": email, "password": password})
    assert resp.status == 200, await resp.text()
    data = await resp.json()
    return {"Authorization": f"Bearer {data['token']}"}, data["user"]


def run(coro):
    return asyncio.run(coro)


def test_demo_accounts_can_log_in_and_are_not_counted():
    async def go():
        client, db = make()
        async with client:
            _, student = await login(client, STUDENT_EMAIL, DEFAULT_STUDENT_PASSWORD)
            _, organizer = await login(client, ORGANIZER_EMAIL, DEFAULT_ORGANIZER_PASSWORD)
            assert student["role"] == "student"
            assert organizer["role"] == "organizer" and organizer["club_id"] == "summit"
            assert (await (await client.get("/api/stats")).json())["users"] == 0   # демо не считаем
        assert seed_demo_club(db)   # повторный запуск ничего не ломает и не дублирует
        assert db.conn.execute("SELECT COUNT(*) FROM web_users WHERE email = ?", (STUDENT_EMAIL,)).fetchone()[0] == 1

    run(go())


def test_student_sees_followed_clubs_and_waitlist():
    async def go():
        client, _ = make()
        async with client:
            auth, _ = await login(client, STUDENT_EMAIL, DEFAULT_STUDENT_PASSWORD)
            data = await (await client.get("/api/clubs/mine", headers=auth)).json()
            assert {c["id"] for c in data["following"]} == {"summit", "mafia"}
            mafia = next(e for e in data["feed"] if e["club"] == "mafia")
            assert mafia["my_status"] == "waitlist" and mafia["waitlist_position"] == 1 and mafia["kind"] == "game"
            resp = await client.post("/api/clubs/mafia/follow", json={"follow": False}, headers=auth)
            assert [c for c in (await resp.json())["following"]] == ["summit"]
            assert (await client.post("/api/clubs/nope/follow", json={}, headers=auth)).status == 404

    run(go())


def test_organizer_manages_only_own_club():
    async def go():
        client, db = make()
        async with client:
            org, _ = await login(client, ORGANIZER_EMAIL, DEFAULT_ORGANIZER_PASSWORD)
            student, _ = await login(client, STUDENT_EMAIL, DEFAULT_STUDENT_PASSWORD)
            assert (await client.get("/api/organizer/overview", headers=student)).status == 403

            over = await (await client.get("/api/organizer/overview", headers=org)).json()
            assert over["club"]["id"] == "summit" and over["upcoming"] and over["past"]
            assert over["past"][0]["turnout"] == 75          # 9 из 12 пришли

            resp = await client.post("/api/organizer/events", headers=org, json={
                "title": "Лекция о снаряжении", "start_at": "2027-01-15T18:00", "capacity": 2,
                "meeting_point": "Ауд. 301", "kind": "lecture"})
            event = (await resp.json())["event"]
            assert resp.status == 200 and event["club"] == "summit" and event["kind"] == "lecture"
            assert (await client.post("/api/organizer/events", headers=org, json={"title": ""})).status == 400

            # чужое событие (клуб mafia) организатору Summit недоступно
            assert (await client.get("/api/organizer/events/mafia-demo-1/signups", headers=org)).status == 404

            # студент записывается → уведомление; организатор отмечает явку и выгружает CSV
            await client.post(f"/api/hikes/{event['id']}/signup", headers=student)
            notes = await (await client.get("/api/notifications", headers=student)).json()
            assert any("Лекция о снаряжении" in n["text"] for n in notes["items"]) and notes["unread"] >= 1
            data = await (await client.get(f"/api/organizer/events/{event['id']}/signups", headers=org)).json()
            key = data["signups"][0]["user_id"]
            resp = await client.post(f"/api/organizer/events/{event['id']}/attendance", headers=org,
                                     json={"user_id": key, "present": True})
            assert (await resp.json())["event"]["attended"] == 1
            csv_resp = await client.get(f"/api/organizer/events/{event['id']}/signups?format=csv", headers=org)
            assert "Айдана Демо" in await csv_resp.text()

            # перенос времени и отмена приходят записанным в колокольчик
            await client.post(f"/api/organizer/events/{event['id']}", headers=org, json={"start_at": "2027-01-16T18:00"})
            copy = (await (await client.post(f"/api/organizer/events/{event['id']}/duplicate", headers=org,
                                             json={"days": 7})).json())["event"]
            assert copy["start"] == "2027-01-23T18:00" and copy["going"] == 0
            await client.post(f"/api/organizer/events/{event['id']}/cancel", headers=org)
            texts = [n["text"] for n in (await (await client.get("/api/notifications", headers=student)).json())["items"]]
            assert any("Изменения" in t for t in texts) and any("отменено" in t for t in texts)
            assert db.hikes.get(event["id"]).status == "cancelled"

    run(go())


def test_waitlist_promotion_notifies_next_person():
    async def go():
        client, db = make()
        async with client:
            student, user = await login(client, STUDENT_EMAIL, DEFAULT_STUDENT_PASSWORD)
            first = db.hikes.signups("mafia-demo-1")[0]
            db.hikes.cancel_signup("mafia-demo-1", first.user_id)          # кто-то из идущих отменил напрямую
            assert db.hikes.signup_of("mafia-demo-1", -user["id"]).status == "going"
            # а через API отмена поднимает следующего и пишет ему уведомление
            resp = await client.post("/api/organizer/events/mafia-demo-1/cancel", headers=student)
            assert resp.status == 403

    run(go())
