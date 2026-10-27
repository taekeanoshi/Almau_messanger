"""Тесты веб-чата: регистрация с подтверждением почты, вход по паролю, подбор по полу, сообщения."""
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


async def _register(client, first_name, last_name, phone, email, password, gender):
    resp = await client.post("/api/chat/register", json={
        "first_name": first_name, "last_name": last_name, "phone": phone,
        "email": email, "password": password, "gender": gender,
    })
    body = await resp.json() if resp.status in (200, 201) else None
    return resp.status, body


async def _register_and_verify(client, sent, first_name, last_name, phone, email, password, gender):
    """Регистрирует и сразу подтверждает почту кодом — возвращает тело ответа otp/verify (с токеном)."""
    status, body = await _register(client, first_name, last_name, phone, email, password, gender)
    assert status == 201, body
    assert body["email"] == email
    code = sent[email]
    resp = await client.post("/api/chat/otp/verify", json={"email": email, "code": code})
    assert resp.status == 200
    return await resp.json()


def test_register_requires_verification_before_login(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            status, body = await _register(client, "Аян", "Касым", "+7 700 123 45 67",
                                            "ayan@example.com", "secret1", "male")
            assert status == 201
            assert body["ok"] is True and "email" in body
            assert "token" not in body  # токен выдаётся только после подтверждения почты

            # Телефон нормализуется (пробелы убираются), повторная регистрация тем же телефоном — конфликт
            status, _ = await _register(client, "Другой", "Чел", "+77001234567",
                                        "other@example.com", "secret2", "female")
            assert status == 409

            # Тот же email с другим телефоном — тоже конфликт
            status, _ = await _register(client, "Третий", "Чел", "+77009998877",
                                        "ayan@example.com", "secret3", "female")
            assert status == 409

            # Пока почта не подтверждена — логин с верным паролем всё равно отклоняется
            resp = await client.post("/api/chat/login", json={"email": "ayan@example.com", "password": "secret1"})
            assert resp.status == 403

            code = sent["ayan@example.com"]
            resp = await client.post("/api/chat/otp/verify", json={"email": "ayan@example.com", "code": code})
            assert resp.status == 200
            verify_body = await resp.json()
            assert verify_body["user"]["id"] == 1

            # Теперь логин с правильным паролем проходит
            resp = await client.post("/api/chat/login", json={"email": "ayan@example.com", "password": "secret1"})
            assert resp.status == 200
            login_body = await resp.json()
            assert login_body["user"]["id"] == 1

            # Неверный пароль по-прежнему отклоняется
            resp = await client.post("/api/chat/login", json={"email": "ayan@example.com", "password": "wrong"})
            assert resp.status == 401

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
            await _register(client, "Аян", "Касым", "+77001234567", "ayan@example.com", "secret1", "male")
            assert "ayan@example.com" in sent  # код уже отправлен при регистрации

            resp = await client.post("/api/chat/otp/verify", json={"email": "ayan@example.com", "code": "000000"})
            assert resp.status == 401

            resp = await client.post("/api/chat/otp/verify", json={"email": "never@example.com", "code": "123456"})
            assert resp.status == 401

    run(go())


def test_otp_locks_after_too_many_wrong_attempts(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            await _register(client, "Аян", "Касым", "+77001234567", "ayan@example.com", "secret1", "male")

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
            status, _ = await _register(client, "", "Фамилия", "+77001234567",
                                        "a@example.com", "secret1", "male")
            assert status == 400  # нет имени

            status, _ = await _register(client, "Имя", "Фамилия", "+77001234567",
                                        "not-an-email", "secret1", "male")
            assert status == 400  # невалидная почта

            status, _ = await _register(client, "Имя", "Фамилия", "+77001234567",
                                        "a@example.com", "123", "male")
            assert status == 400  # пароль короче 6 символов

            status, _ = await _register(client, "Имя", "Фамилия", "+77001234567",
                                        "a@example.com", "secret1", "alien")
            assert status == 400  # пол не male/female

    run(go())


def test_gender_matching_opposite_and_same(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            male_a = await _register_and_verify(client, sent, "А", "А", "+77001110001",
                                                "a@example.com", "secret1", "male")
            female_b = await _register_and_verify(client, sent, "Б", "Б", "+77001110002",
                                                   "b@example.com", "secret1", "female")
            male_c = await _register_and_verify(client, sent, "В", "В", "+77001110003",
                                                "c@example.com", "secret1", "male")

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


def test_send_and_poll_messages(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            u1 = await _register_and_verify(client, sent, "А", "А", "+77001110001",
                                            "a@example.com", "secret1", "male")
            u2 = await _register_and_verify(client, sent, "Б", "Б", "+77001110002",
                                            "b@example.com", "secret1", "female")
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


def test_stop_ends_dialog_for_both_sides(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            u1 = await _register_and_verify(client, sent, "А", "А", "+77001110001",
                                            "a@example.com", "secret1", "male")
            u2 = await _register_and_verify(client, sent, "Б", "Б", "+77001110002",
                                            "b@example.com", "secret1", "female")
            t1, t2 = u1["token"], u2["token"]

            await client.post("/api/chat/search", headers=auth(t1), json={"preference": "any"})
            await client.post("/api/chat/search", headers=auth(t2), json={"preference": "any"})

            resp = await client.post("/api/chat/stop", headers=auth(t1))
            assert (await resp.json())["status"] == "idle"

            resp = await client.get("/api/chat/poll", headers=auth(t2))
            assert (await resp.json())["status"] == "idle"

    run(go())


def test_next_requeues_immediately(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            u1 = await _register_and_verify(client, sent, "А", "А", "+77001110001",
                                            "a@example.com", "secret1", "male")
            u2 = await _register_and_verify(client, sent, "Б", "Б", "+77001110002",
                                            "b@example.com", "secret1", "female")
            u3 = await _register_and_verify(client, sent, "В", "В", "+77001110003",
                                            "c@example.com", "secret1", "female")
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


def test_stats_include_web_chat_counts(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            await _register(client, "А", "А", "+77001110001", "a@example.com", "secret1", "male")
            resp = await client.get("/api/stats")
            data = await resp.json()
            assert data["web_users"] == 1
            assert data["web_dialogs"] == 0

    run(go())
