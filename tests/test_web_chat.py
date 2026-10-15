"""Тесты веб-чата: регистрация, вход по коду на почту, подбор по полу, сообщения."""
import asyncio

from aiohttp.test_utils import TestClient, TestServer

import bot.web_chat as web_chat_module
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


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def capture_otp_emails(monkeypatch):
    """Brevo в тестах не настроен (ключа нет) — подменяем отправку письма, чтобы достать код."""
    sent: dict[str, str] = {}

    async def fake_send(app, to_email, code):
        sent[to_email] = code

    monkeypatch.setattr(web_chat_module, "_send_otp_email", fake_send)
    return sent


async def _register(client, first_name, last_name, phone, email, gender):
    resp = await client.post("/api/chat/register", json={
        "first_name": first_name, "last_name": last_name,
        "phone": phone, "email": email, "gender": gender,
    })
    body = await resp.json() if resp.status in (200, 201) else None
    return resp.status, body


async def _login_via_otp(client, email, sent):
    resp = await client.post("/api/chat/otp/request", json={"email": email})
    assert resp.status == 200
    code = sent[email]
    return await client.post("/api/chat/otp/verify", json={"email": email, "code": code})


def test_register_and_login_with_otp(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            status, body = await _register(client, "Аян", "Касым", "+7 700 123 45 67",
                                            "ayan@example.com", "male")
            assert status == 201
            assert body["ok"] is True and "token" in body
            assert body["user"]["gender"] == "male"

            # Телефон нормализуется (пробелы убираются), повторная регистрация тем же телефоном — конфликт
            status, _ = await _register(client, "Другой", "Чел", "+77001234567",
                                        "other@example.com", "female")
            assert status == 409

            # Тот же email с другим телефоном — тоже конфликт
            status, _ = await _register(client, "Третий", "Чел", "+77009998877",
                                        "ayan@example.com", "female")
            assert status == 409

            resp = await _login_via_otp(client, "ayan@example.com", sent)
            assert resp.status == 200
            login_body = await resp.json()
            assert login_body["user"]["id"] == body["user"]["id"]

    run(go())


def test_otp_request_rejects_unknown_email():
    async def go():
        client, db = make_client()
        async with client:
            resp = await client.post("/api/chat/otp/request", json={"email": "nobody@example.com"})
            assert resp.status == 404

    run(go())


def test_otp_verify_rejects_wrong_code(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            await _register(client, "Аян", "Касым", "+77001234567", "ayan@example.com", "male")
            await client.post("/api/chat/otp/request", json={"email": "ayan@example.com"})
            assert "ayan@example.com" in sent

            resp = await client.post("/api/chat/otp/verify", json={"email": "ayan@example.com", "code": "000000"})
            assert resp.status == 401

            # Без запрошенного кода вообще (для другого email) — тоже 401
            resp = await client.post("/api/chat/otp/verify", json={"email": "never@example.com", "code": "123456"})
            assert resp.status == 401

    run(go())


def test_otp_locks_after_too_many_wrong_attempts(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            await _register(client, "Аян", "Касым", "+77001234567", "ayan@example.com", "male")
            await client.post("/api/chat/otp/request", json={"email": "ayan@example.com"})

            for _ in range(5):
                resp = await client.post("/api/chat/otp/verify",
                                         json={"email": "ayan@example.com", "code": "000000"})
                assert resp.status == 401

            # После 5 неверных попыток даже правильный код больше не принимается
            resp = await client.post("/api/chat/otp/verify",
                                     json={"email": "ayan@example.com", "code": sent["ayan@example.com"]})
            assert resp.status == 403

    run(go())


def test_registration_validation():
    async def go():
        client, db = make_client()
        async with client:
            status, _ = await _register(client, "", "Фамилия", "+77001234567", "a@example.com", "male")
            assert status == 400  # нет имени

            status, _ = await _register(client, "Имя", "Фамилия", "+77001234567", "not-an-email", "male")
            assert status == 400  # невалидная почта

            status, _ = await _register(client, "Имя", "Фамилия", "+77001234567", "a@example.com", "alien")
            assert status == 400  # пол не male/female

    run(go())


def test_gender_matching_opposite_and_same():
    async def go():
        client, db = make_client()
        async with client:
            _, male_a = await _register(client, "А", "А", "+77001110001", "a@example.com", "male")
            _, female_b = await _register(client, "Б", "Б", "+77001110002", "b@example.com", "female")
            _, male_c = await _register(client, "В", "В", "+77001110003", "c@example.com", "male")

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
            _, u1 = await _register(client, "А", "А", "+77001110001", "a@example.com", "male")
            _, u2 = await _register(client, "Б", "Б", "+77001110002", "b@example.com", "female")
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
            _, u1 = await _register(client, "А", "А", "+77001110001", "a@example.com", "male")
            _, u2 = await _register(client, "Б", "Б", "+77001110002", "b@example.com", "female")
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
            _, u1 = await _register(client, "А", "А", "+77001110001", "a@example.com", "male")
            _, u2 = await _register(client, "Б", "Б", "+77001110002", "b@example.com", "female")
            _, u3 = await _register(client, "В", "В", "+77001110003", "c@example.com", "female")
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
            await _register(client, "А", "А", "+77001110001", "a@example.com", "male")
            resp = await client.get("/api/stats")
            data = await resp.json()
            assert data["web_users"] == 1
            assert data["web_dialogs"] == 0

    run(go())
