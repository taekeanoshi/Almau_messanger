"""Тесты веб-чата: регистрация, логин, подбор по полу, сообщения — через TestClient, без сети."""
import asyncio

from aiohttp.test_utils import TestClient, TestServer

from bot.api import build_app
from bot.config import BASE_DIR
from bot.content import Content
from bot.db import Database


def make_client():
    db = Database(":memory:")
    content = Content(BASE_DIR / "site" / "data")
    app = build_app(db, content, bot=None, admin_ids=frozenset())
    return TestClient(TestServer(app)), db


def run(coro):
    return asyncio.run(coro)


async def _register(client, first_name, last_name, phone, password, gender):
    resp = await client.post("/api/chat/register", json={
        "first_name": first_name, "last_name": last_name,
        "phone": phone, "password": password, "gender": gender,
    })
    body = await resp.json() if resp.status in (200, 201) else None
    return resp.status, body


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_register_login_and_duplicate_phone():
    async def go():
        client, db = make_client()
        async with client:
            status, body = await _register(client, "Аян", "Касым", "+7 700 123 45 67", "secret1", "male")
            assert status == 201
            assert body["ok"] is True and "token" in body
            assert body["user"]["gender"] == "male"

            # Телефон нормализуется (пробелы убираются), повторная регистрация с тем же номером — конфликт
            status, _ = await _register(client, "Другой", "Чел", "+77001234567", "secret2", "female")
            assert status == 409

            resp = await client.post("/api/chat/login", json={"phone": "+7 700 123 45 67", "password": "secret1"})
            assert resp.status == 200
            login_body = await resp.json()
            assert login_body["user"]["id"] == body["user"]["id"]

            resp = await client.post("/api/chat/login", json={"phone": "+77001234567", "password": "wrong"})
            assert resp.status == 401

    run(go())


def test_registration_validation():
    async def go():
        client, db = make_client()
        async with client:
            status, _ = await _register(client, "", "Фамилия", "+77001234567", "secret1", "male")
            assert status == 400

            status, _ = await _register(client, "Имя", "Фамилия", "+77001234567", "123", "male")
            assert status == 400  # пароль короче 6 символов

            status, _ = await _register(client, "Имя", "Фамилия", "+77001234567", "secret1", "alien")
            assert status == 400  # пол не male/female

    run(go())


def test_gender_matching_opposite_and_same():
    async def go():
        client, db = make_client()
        async with client:
            _, male_a = await _register(client, "А", "А", "+77001110001", "secret1", "male")
            _, female_b = await _register(client, "Б", "Б", "+77001110002", "secret1", "female")
            _, male_c = await _register(client, "В", "В", "+77001110003", "secret1", "male")

            token_a, token_b, token_c = male_a["token"], female_b["token"], male_c["token"]

            # А ищет "противоположный пол" — встаёт в очередь, пары пока нет
            resp = await client.post("/api/chat/search", headers=auth(token_a), json={"preference": "opposite"})
            assert (await resp.json())["status"] == "waiting"

            # В (тоже male) ищет "такой же пол" — с А (male) несовместимо по предпочтению А (opposite),
            # поэтому тоже встаёт в очередь
            resp = await client.post("/api/chat/search", headers=auth(token_c), json={"preference": "same"})
            assert (await resp.json())["status"] == "waiting"

            # Б (female) ищет "противоположный" — подходит и А (opposite, male) и совместимо взаимно
            resp = await client.post("/api/chat/search", headers=auth(token_b), json={"preference": "opposite"})
            body = await resp.json()
            assert body["status"] == "matched"

            # У А теперь тоже статус matched
            resp = await client.get("/api/chat/poll", headers=auth(token_a))
            assert (await resp.json())["status"] == "matched"

            # В всё ещё ждёт
            resp = await client.get("/api/chat/poll", headers=auth(token_c))
            assert (await resp.json())["status"] == "waiting"

    run(go())


def test_send_and_poll_messages():
    async def go():
        client, db = make_client()
        async with client:
            _, u1 = await _register(client, "А", "А", "+77001110001", "secret1", "male")
            _, u2 = await _register(client, "Б", "Б", "+77001110002", "secret1", "female")
            t1, t2 = u1["token"], u2["token"]

            await client.post("/api/chat/search", headers=auth(t1), json={"preference": "any"})
            resp = await client.post("/api/chat/search", headers=auth(t2), json={"preference": "any"})
            dialog_id = (await resp.json())["dialog_id"]

            resp = await client.post("/api/chat/send", headers=auth(t1),
                                     json={"dialog_id": dialog_id, "text": "привет"})
            assert resp.status == 200

            resp = await client.get("/api/chat/poll", headers=auth(t2))
            body = await resp.json()
            assert body["status"] == "matched"
            assert len(body["messages"]) == 1
            assert body["messages"][0]["text"] == "привет"
            assert body["messages"][0]["mine"] is False
            last_id = body["messages"][0]["id"]

            # Повторный поллинг с since=last_id не возвращает уже прочитанное сообщение
            resp = await client.get("/api/chat/poll", headers=auth(t2), params={"since": str(last_id)})
            assert (await resp.json())["messages"] == []

            # Пустой текст отклоняется
            resp = await client.post("/api/chat/send", headers=auth(t1),
                                     json={"dialog_id": dialog_id, "text": "   "})
            assert resp.status == 400

    run(go())


def test_stop_ends_dialog_for_both_sides():
    async def go():
        client, db = make_client()
        async with client:
            _, u1 = await _register(client, "А", "А", "+77001110001", "secret1", "male")
            _, u2 = await _register(client, "Б", "Б", "+77001110002", "secret1", "female")
            t1, t2 = u1["token"], u2["token"]

            await client.post("/api/chat/search", headers=auth(t1), json={"preference": "any"})
            await client.post("/api/chat/search", headers=auth(t2), json={"preference": "any"})

            resp = await client.post("/api/chat/stop", headers=auth(t1))
            assert (await resp.json())["status"] == "idle"

            resp = await client.get("/api/chat/poll", headers=auth(t2))
            assert (await resp.json())["status"] == "idle"

    run(go())


def test_next_requeues_immediately():
    async def go():
        client, db = make_client()
        async with client:
            _, u1 = await _register(client, "А", "А", "+77001110001", "secret1", "male")
            _, u2 = await _register(client, "Б", "Б", "+77001110002", "secret1", "female")
            _, u3 = await _register(client, "В", "В", "+77001110003", "secret1", "female")
            t1, t2, t3 = u1["token"], u2["token"], u3["token"]

            await client.post("/api/chat/search", headers=auth(t1), json={"preference": "any"})
            await client.post("/api/chat/search", headers=auth(t2), json={"preference": "any"})

            resp = await client.post("/api/chat/next", headers=auth(t1), json={"preference": "any"})
            assert (await resp.json())["status"] == "waiting"

            resp = await client.post("/api/chat/search", headers=auth(t3), json={"preference": "any"})
            assert (await resp.json())["status"] == "matched"

    run(go())


def test_unauthorized_without_token():
    async def go():
        client, db = make_client()
        async with client:
            resp = await client.get("/api/chat/poll")
            assert resp.status == 401
            resp = await client.get("/api/chat/poll", headers=auth("garbage"))
            assert resp.status == 401

    run(go())


def test_stats_include_web_chat_counts():
    async def go():
        client, db = make_client()
        async with client:
            await _register(client, "А", "А", "+77001110001", "secret1", "male")
            resp = await client.get("/api/stats")
            data = await resp.json()
            assert data["web_users"] == 1
            assert data["web_dialogs"] == 0

    run(go())
