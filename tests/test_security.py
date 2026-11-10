"""Тесты защиты данных: сессии, лимиты частоты, анонимность, аккаунт, жалобы и блокировки."""
import base64
import sqlite3
from datetime import datetime, timedelta, timezone

import bot.web_chat as web_chat_module
from bot.db import Database, hash_token
from tests.test_web_chat import _register, _register_and_verify, auth, capture_otp_emails, make_client, run

PNG = b"\x89PNG\r\n\x1a\n fake-bytes"


async def _student(client, sent, n, gender="male", first="Студент"):
    return await _register_and_verify(client, sent, first, f"Номер{n}", f"+7700111{n:04d}",
                                      f"s{n}@example.com", "secret-pw-1", gender)


# ---------- сессии ----------

def test_session_tokens_are_stored_hashed_and_expire(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            user = await _student(client, sent, 1)
            token = user["token"]

            stored = [r["token"] for r in db.conn.execute("SELECT token FROM web_sessions")]
            assert token not in stored and hash_token(token) in stored

            resp = await client.get("/api/chat/me", headers=auth(token))
            assert resp.status == 200

            # Сессия старше срока жизни перестаёт работать
            old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat(timespec="seconds")
            with db.conn:
                db.conn.execute("UPDATE web_sessions SET created_at = ?", (old,))
            resp = await client.get("/api/chat/me", headers=auth(token))
            assert resp.status == 401
            assert db.purge_expired()["sessions"] == 1

    run(go())


def test_plaintext_sessions_from_old_database_are_hashed_in_place(tmp_path):
    """База, пережившая деплой со старым хранением токенов открытым текстом: после запуска
    токены захешированы, а уже вошедшие пользователи остаются вошедшими."""
    db_path = tmp_path / "legacy.db"
    db = Database(db_path)
    user_id = db.create_web_user("Аян", "Касым", "+77001234567", "a@example.com", "h", "s", "male", "2", "IT", "ник")
    with db.conn:
        db.conn.execute("INSERT INTO web_sessions (token, user_id, created_at) VALUES (?, ?, ?)",
                        ("raw-legacy-token", user_id, datetime.now(timezone.utc).isoformat(timespec="seconds")))
    db.conn.close()

    db = Database(db_path)
    assert db.web_session_user("raw-legacy-token") == user_id
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT token FROM web_sessions").fetchone()[0] == hash_token("raw-legacy-token")


def test_banned_account_loses_access_immediately(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            user = await _student(client, sent, 1)
            assert (await client.get("/api/chat/me", headers=auth(user["token"]))).status == 200

            db.set_web_user_banned(user["user"]["id"], True)
            assert (await client.get("/api/chat/me", headers=auth(user["token"]))).status == 401
            resp = await client.post("/api/chat/login", json={"email": "s1@example.com", "password": "secret-pw-1"})
            assert resp.status == 403

    run(go())


# ---------- лимиты частоты ----------

def test_login_is_locked_after_repeated_wrong_passwords(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            await _student(client, sent, 1)
            for _ in range(8):
                resp = await client.post("/api/chat/login", json={"email": "s1@example.com", "password": "wrong-pass"})
                assert resp.status == 401
            # Дальше не пускает даже с правильным паролем — пока окно не пройдёт
            resp = await client.post("/api/chat/login", json={"email": "s1@example.com", "password": "secret-pw-1"})
            assert resp.status == 429
            # Другой аккаунт при этом не страдает
            await _student(client, sent, 2)
            resp = await client.post("/api/chat/login", json={"email": "s2@example.com", "password": "secret-pw-1"})
            assert resp.status == 200

    run(go())


def test_code_emails_have_a_cooldown(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            status, _ = await _register(client, "Аян", "Касым", "+77001110001", "a@example.com", "secret-pw-1", "male")
            assert status == 201
            # Сразу же запросить ещё одно письмо нельзя — защита от спама на чужую почту
            resp = await client.post("/api/chat/otp/request", json={"email": "a@example.com"})
            assert resp.status == 429

    run(go())


def test_short_password_is_rejected(monkeypatch):
    capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            status, _ = await _register(client, "Аян", "Касым", "+77001110001", "a@example.com", "short1", "male")
            assert status == 400

    run(go())


def test_leads_are_rate_limited_per_ip():
    async def go():
        client, db = make_client()
        async with client:
            for i in range(10):
                resp = await client.post("/api/leads", json={"name": f"Имя{i}", "contact": "@x"})
                assert resp.status == 201
            resp = await client.post("/api/leads", json={"name": "Спам", "contact": "@x"})
            assert resp.status == 429

    run(go())


def test_responses_carry_security_headers():
    async def go():
        client, db = make_client()
        async with client:
            resp = await client.get("/api/stats")
            assert resp.headers["X-Content-Type-Options"] == "nosniff"
            assert resp.headers["Cache-Control"] == "no-store"
            assert resp.headers["X-Frame-Options"] == "DENY"

    run(go())


def test_bad_since_parameter_is_a_client_error_not_a_crash(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            a = await _student(client, sent, 1)
            b = await _student(client, sent, 2, "female")
            await client.post("/api/chat/search", headers=auth(a["token"]), json={"preference": "any"})
            await client.post("/api/chat/search", headers=auth(b["token"]), json={"preference": "any"})
            resp = await client.get("/api/chat/poll", headers=auth(a["token"]), params={"since": "abc"})
            assert resp.status == 400

    run(go())


# ---------- анонимность ----------

def test_anonymous_chat_never_exposes_partner_identity(monkeypatch):
    """Ни id собеседника, ни связка «ник ↔ настоящее имя» не должны утекать: иначе собеседника
    можно найти в разделе «Люди» и анонимный чат перестаёт быть анонимным."""
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            a = await _student(client, sent, 1)
            b = await _student(client, sent, 2, "female")
            resp = await client.post("/api/chat/search", headers=auth(a["token"]), json={"preference": "any"})
            assert "partner_id" not in await resp.json()
            resp = await client.post("/api/chat/search", headers=auth(b["token"]), json={"preference": "any"})
            matched = await resp.json()
            assert matched["status"] == "matched" and "partner_id" not in matched
            assert matched["partner_nickname"] == a["user"]["nickname"]

            resp = await client.get("/api/chat/poll", headers=auth(a["token"]))
            assert "partner_id" not in await resp.json()

            # В справочнике людей ника нет — только настоящее имя
            resp = await client.get("/api/chat/direct/search-users", headers=auth(a["token"]))
            users = (await resp.json())["users"]
            assert users and all("nickname" not in u for u in users)

    run(go())


def test_photo_type_is_taken_from_file_signature(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            a = await _student(client, sent, 1)
            b = await _student(client, sent, 2, "female")
            await client.post("/api/chat/search", headers=auth(a["token"]), json={"preference": "any"})
            resp = await client.post("/api/chat/search", headers=auth(b["token"]), json={"preference": "any"})
            dialog_id = (await resp.json())["dialog_id"]

            # Клиент заявляет jpeg, по сигнатуре это png — сохраняется настоящий тип
            resp = await client.post("/api/chat/send-photo", headers=auth(a["token"]), json={
                "dialog_id": dialog_id, "content_type": "image/jpeg", "data": base64.b64encode(PNG).decode()})
            photo_id = (await resp.json())["photo_id"]
            resp = await client.get(f"/api/chat/photo/{photo_id}", headers=auth(b["token"]))
            assert resp.headers["Content-Type"] == "image/png"

            # HTML под видом картинки не проходит
            resp = await client.post("/api/chat/send-photo", headers=auth(a["token"]), json={
                "dialog_id": dialog_id, "content_type": "image/png",
                "data": base64.b64encode(b"<html><script>alert(1)</script>").decode()})
            assert resp.status == 400

    run(go())


# ---------- аккаунт ----------

def test_unfinished_registration_can_be_completed_later(monkeypatch):
    """Закрыл вкладку на шаге с кодом — почта не «сгорает»: вход говорит, что почта
    не подтверждена, код можно запросить заново и закончить регистрацию."""
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            status, _ = await _register(client, "Аян", "Касым", "+77001110001", "a@example.com", "secret-pw-1", "male")
            assert status == 201
            resp = await client.post("/api/chat/login", json={"email": "a@example.com", "password": "secret-pw-1"})
            assert resp.status == 403

            client.app["limiter"].reset("code-cooldown:a@example.com")
            resp = await client.post("/api/chat/otp/request", json={"email": "a@example.com"})
            assert resp.status == 200
            resp = await client.post("/api/chat/otp/verify", json={"email": "a@example.com", "code": sent["a@example.com"]})
            assert resp.status == 200
            # Профиль в ответе — полный: фронту не нужен отдельный запрос
            user = (await resp.json())["user"]
            assert user["role"] == "student" and user["last_name"] == "Касым" and user["email"] == "a@example.com"

    run(go())


def test_abandoned_unverified_accounts_are_purged(monkeypatch):
    capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            await _register(client, "Аян", "Касым", "+77001110001", "a@example.com", "secret-pw-1", "male")
            assert db.purge_expired()["unverified_accounts"] == 0  # свежую регистрацию не трогаем

            old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(timespec="seconds")
            with db.conn:
                db.conn.execute("UPDATE web_users SET created_at = ?", (old,))
            assert db.purge_expired()["unverified_accounts"] == 1
            assert db.get_web_user_by_email("a@example.com") is None

    run(go())


def test_password_reset_by_email_code(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            user = await _student(client, sent, 1)
            old_token = user["token"]

            # Для неизвестной почты ответ такой же, письмо не уходит
            resp = await client.post("/api/chat/password/forgot", json={"email": "nobody@example.com"})
            assert resp.status == 200 and "nobody@example.com" not in sent

            del sent["s1@example.com"]
            client.app["limiter"].reset("code-cooldown:s1@example.com")  # в тесте не ждём 45 секунд
            resp = await client.post("/api/chat/password/forgot", json={"email": "s1@example.com"})
            assert resp.status == 200
            code = sent["s1@example.com"]

            resp = await client.post("/api/chat/password/reset", json={
                "email": "s1@example.com", "code": "000000" if code != "000000" else "111111",
                "password": "brand-new-pw"})
            assert resp.status == 401
            resp = await client.post("/api/chat/password/reset", json={
                "email": "s1@example.com", "code": code, "password": "short"})
            assert resp.status == 400
            resp = await client.post("/api/chat/password/reset", json={
                "email": "s1@example.com", "code": code, "password": "brand-new-pw"})
            assert resp.status == 200
            new_token = (await resp.json())["token"]

            # Старые сессии сброшены, старый пароль не работает, новый — работает
            assert (await client.get("/api/chat/me", headers=auth(old_token))).status == 401
            assert (await client.get("/api/chat/me", headers=auth(new_token))).status == 200
            resp = await client.post("/api/chat/login", json={"email": "s1@example.com", "password": "secret-pw-1"})
            assert resp.status == 401
            resp = await client.post("/api/chat/login", json={"email": "s1@example.com", "password": "brand-new-pw"})
            assert resp.status == 200
            # Код одноразовый
            resp = await client.post("/api/chat/password/reset", json={
                "email": "s1@example.com", "code": code, "password": "another-new-pw"})
            assert resp.status == 401

    run(go())


def test_profile_update_and_password_change(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            user = await _student(client, sent, 1)
            token = user["token"]
            resp = await client.post("/api/chat/login", json={"email": "s1@example.com", "password": "secret-pw-1"})
            other_device = (await resp.json())["token"]

            resp = await client.post("/api/chat/profile", headers=auth(token), json={
                "nickname": "Тень", "course": "3", "role": "admin", "email": "hacker@example.com"})
            body = (await resp.json())["user"]
            assert body["nickname"] == "Тень" and body["course"] == "3"
            assert body["role"] == "student" and body["email"] == "s1@example.com"  # роль и почту так не сменить

            resp = await client.post("/api/chat/profile", headers=auth(token), json={"first_name": "  "})
            assert resp.status == 400

            resp = await client.post("/api/chat/password/change", headers=auth(token), json={
                "current_password": "not-my-password", "new_password": "brand-new-pw"})
            assert resp.status == 403
            resp = await client.post("/api/chat/password/change", headers=auth(token), json={
                "current_password": "secret-pw-1", "new_password": "brand-new-pw"})
            assert resp.status == 200
            # Текущее устройство остаётся в аккаунте, остальные — выходят
            assert (await client.get("/api/chat/me", headers=auth(token))).status == 200
            assert (await client.get("/api/chat/me", headers=auth(other_device))).status == 401

    run(go())


def test_account_deletion_erases_personal_data(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            a = await _student(client, sent, 1, first="Аян")
            b = await _student(client, sent, 2, "female", first="Алия")
            resp = await client.post("/api/chat/direct/start", headers=auth(a["token"]),
                                     json={"user_id": b["user"]["id"]})
            dialog_id = (await resp.json())["dialog_id"]
            await client.post("/api/chat/direct/send", headers=auth(a["token"]),
                              json={"dialog_id": dialog_id, "text": "привет"})

            resp = await client.post("/api/chat/account/delete", headers=auth(a["token"]), json={"password": "wrong"})
            assert resp.status == 403
            resp = await client.post("/api/chat/account/delete", headers=auth(a["token"]),
                                     json={"password": "secret-pw-1"})
            assert resp.status == 200

            assert db.get_web_user(a["user"]["id"]) is None
            assert db.conn.execute("SELECT COUNT(*) FROM web_messages").fetchone()[0] == 0
            assert db.conn.execute("SELECT COUNT(*) FROM web_sessions WHERE user_id = ?",
                                   (a["user"]["id"],)).fetchone()[0] == 0
            assert (await client.get("/api/chat/me", headers=auth(a["token"]))).status == 401
            # Освободившуюся почту можно зарегистрировать заново
            client.app["limiter"].reset("code-cooldown:s1@example.com")
            status, _ = await _register(client, "Аян", "Касым", "+77001110001", "s1@example.com", "secret-pw-1", "male")
            assert status == 201

    run(go())


# ---------- жалобы и блокировки ----------

def test_report_blocks_pair_and_auto_bans_after_threshold(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            troll = await _student(client, sent, 1, first="Тролль")
            victims = [await _student(client, sent, n, "female") for n in (2, 3, 4)]

            for i, victim in enumerate(victims):
                await client.post("/api/chat/search", headers=auth(troll["token"]), json={"preference": "any"})
                resp = await client.post("/api/chat/search", headers=auth(victim["token"]), json={"preference": "any"})
                dialog_id = (await resp.json())["dialog_id"]

                resp = await client.post("/api/chat/report", headers=auth(victim["token"]),
                                         json={"dialog_id": dialog_id, "reason": "оскорбления"})
                body = await resp.json()
                assert resp.status == 200
                assert body["banned"] is (i == 2)  # третья жалоба от третьего человека — автобан

                # Диалог для пожаловавшегося закончился
                resp = await client.get("/api/chat/poll", headers=auth(victim["token"]))
                assert (await resp.json())["status"] == "idle"

            assert db.get_web_user(troll["user"]["id"])["banned"] == 1
            assert (await client.get("/api/chat/me", headers=auth(troll["token"]))).status == 401

    run(go())


def test_reported_pair_is_never_matched_again(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            a = await _student(client, sent, 1)
            b = await _student(client, sent, 2, "female")
            await client.post("/api/chat/search", headers=auth(a["token"]), json={"preference": "any"})
            resp = await client.post("/api/chat/search", headers=auth(b["token"]), json={"preference": "any"})
            dialog_id = (await resp.json())["dialog_id"]
            await client.post("/api/chat/report", headers=auth(b["token"]), json={"dialog_id": dialog_id})

            # Оба снова ищут — друг с другом их больше не сводит
            await client.post("/api/chat/search", headers=auth(a["token"]), json={"preference": "any"})
            resp = await client.post("/api/chat/search", headers=auth(b["token"]), json={"preference": "any"})
            assert (await resp.json())["status"] == "waiting"

            # Посторонний не может пожаловаться на чужой диалог
            c = await _student(client, sent, 3)
            resp = await client.post("/api/chat/report", headers=auth(c["token"]), json={"dialog_id": dialog_id})
            assert resp.status == 403

    run(go())


def test_block_stops_direct_messages_both_ways(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            a = await _student(client, sent, 1)
            b = await _student(client, sent, 2, "female")
            resp = await client.post("/api/chat/direct/start", headers=auth(a["token"]),
                                     json={"user_id": b["user"]["id"]})
            dialog_id = (await resp.json())["dialog_id"]

            resp = await client.post("/api/chat/block", headers=auth(b["token"]), json={"user_id": a["user"]["id"]})
            assert resp.status == 200
            for sender in (a, b):
                resp = await client.post("/api/chat/direct/send", headers=auth(sender["token"]),
                                         json={"dialog_id": dialog_id, "text": "эй"})
                assert resp.status == 403
            # Заблокированный не видит заблокировавшего в поиске и не может начать новый диалог
            resp = await client.get("/api/chat/direct/search-users", headers=auth(a["token"]))
            assert all(u["id"] != b["user"]["id"] for u in (await resp.json())["users"])

            resp = await client.get("/api/chat/blocks", headers=auth(b["token"]))
            assert [u["id"] for u in (await resp.json())["blocked"]] == [a["user"]["id"]]

            await client.post("/api/chat/block", headers=auth(b["token"]),
                              json={"user_id": a["user"]["id"], "blocked": False})
            resp = await client.post("/api/chat/direct/send", headers=auth(a["token"]),
                                     json={"dialog_id": dialog_id, "text": "эй"})
            assert resp.status == 200

    run(go())


def test_messages_are_rate_limited(monkeypatch):
    sent = capture_otp_emails(monkeypatch)
    monkeypatch.setattr(web_chat_module, "MESSAGES_PER_USER", (3, 15))

    async def go():
        client, db = make_client()
        async with client:
            a = await _student(client, sent, 1)
            b = await _student(client, sent, 2, "female")
            resp = await client.post("/api/chat/direct/start", headers=auth(a["token"]),
                                     json={"user_id": b["user"]["id"]})
            dialog_id = (await resp.json())["dialog_id"]
            statuses = []
            for i in range(4):
                resp = await client.post("/api/chat/direct/send", headers=auth(a["token"]),
                                         json={"dialog_id": dialog_id, "text": f"спам {i}"})
                statuses.append(resp.status)
            assert statuses == [200, 200, 200, 429]

    run(go())


def test_old_anonymous_messages_are_purged_but_reported_ones_kept(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            a = await _student(client, sent, 1)
            b = await _student(client, sent, 2, "female")
            c = await _student(client, sent, 3, "female")

            async def chat(x, y, text, report=False):
                await client.post("/api/chat/search", headers=auth(x["token"]), json={"preference": "any"})
                resp = await client.post("/api/chat/search", headers=auth(y["token"]), json={"preference": "any"})
                dialog_id = (await resp.json())["dialog_id"]
                await client.post("/api/chat/send", headers=auth(x["token"]), json={"dialog_id": dialog_id, "text": text})
                if report:
                    await client.post("/api/chat/report", headers=auth(y["token"]), json={"dialog_id": dialog_id})
                else:
                    await client.post("/api/chat/stop", headers=auth(x["token"]))
                return dialog_id

            await chat(a, b, "обычный разговор")
            reported = await chat(a, c, "гадость", report=True)

            old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat(timespec="seconds")
            with db.conn:
                db.conn.execute("UPDATE web_dialogs SET started_at = ?", (old,))
            assert db.purge_expired()["anonymous_messages"] == 1
            left = db.conn.execute("SELECT dialog_id FROM web_messages").fetchall()
            assert [r["dialog_id"] for r in left] == [reported]  # доказательство по открытой жалобе сохранено

    run(go())
