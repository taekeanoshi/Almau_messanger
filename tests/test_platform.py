"""Тесты админ-панели, честных экзаменов (снимки камеры, запись кусками), прямых чатов
с непрочитанными, подбора компании на ивент, веб-записи на походы и сводки «Сегодня»."""
import base64
from datetime import timedelta

from bot.hikes import now as hikes_now
from tests.test_academics import (_register_and_verify, _setup_course_with_exam, auth, capture_otp_emails,
                                  make_client, run)

JPEG = b"\xff\xd8\xff\xe0 fake jpeg frame"
PNG = b"\x89PNG\r\n\x1a\n fake-bytes"


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


async def _student(client, sent, n, gender="male", first="Студент"):
    return await _register_and_verify(client, sent, first, f"Номер{n}", f"+7700222{n:04d}",
                                      f"p{n}@example.com", "secret-pw-1", gender)


async def _make_admin(client, user, admin_token="admintok"):
    resp = await client.post("/api/admin/set-role", headers={"X-Admin-Token": admin_token},
                             json={"email": user["user"]["email"], "role": "admin"})
    assert resp.status == 200, await resp.text()


# ---------- админ-панель ----------

def test_admin_token_is_accepted_in_header_and_checked(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client(admin_token="admintok")
        async with client:
            user = await _student(client, sent, 1)
            body = {"email": user["user"]["email"], "role": "teacher"}
            resp = await client.post("/api/admin/set-role", headers={"X-Admin-Token": "wrong"}, json=body)
            assert resp.status == 401
            resp = await client.post("/api/admin/set-role", json=body)
            assert resp.status == 401
            resp = await client.post("/api/admin/set-role", headers={"X-Admin-Token": "admintok"}, json=body)
            assert resp.status == 200

    run(go())


def test_admin_panel_manages_users_and_is_closed_to_others(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client(admin_token="admintok")
        async with client:
            boss = await _student(client, sent, 1, first="Босс")
            student = await _student(client, sent, 2, "female", first="Алия")
            await _make_admin(client, boss)
            t_admin, t_student = boss["token"], student["token"]
            student_id = student["user"]["id"]

            # Обычному студенту админка закрыта целиком
            for path in ("/api/admin/overview", "/api/admin/users", "/api/admin/leads",
                         "/api/admin/reports", "/api/admin/hike-signups"):
                assert (await client.get(path, headers=auth(t_student))).status == 403
            resp = await client.post("/api/admin/users/role", headers=auth(t_student),
                                     json={"user_id": student_id, "role": "admin"})
            assert resp.status == 403

            resp = await client.get("/api/admin/overview", headers=auth(t_admin))
            stats = await resp.json()
            assert stats["users"] == 2 and stats["admins"] == 1

            resp = await client.get("/api/admin/users", headers=auth(t_admin), params={"q": "алия"})
            users = (await resp.json())["users"]
            assert [u["id"] for u in users] == [student_id] and users[0]["email"] == "p2@example.com"

            # Роль: студент → преподаватель
            resp = await client.post("/api/admin/users/role", headers=auth(t_admin),
                                     json={"user_id": student_id, "role": "teacher"})
            assert (await resp.json())["user"]["role"] == "teacher"
            resp = await client.post("/api/admin/users/role", headers=auth(t_admin),
                                     json={"user_id": student_id, "role": "superuser"})
            assert resp.status == 400

            # Последнего админа разжаловать нельзя, себя забанить — тоже
            resp = await client.post("/api/admin/users/role", headers=auth(t_admin),
                                     json={"user_id": boss["user"]["id"], "role": "student"})
            assert resp.status == 409
            resp = await client.post("/api/admin/users/ban", headers=auth(t_admin),
                                     json={"user_id": boss["user"]["id"], "banned": True})
            assert resp.status == 400

            # Бан выкидывает пользователя сразу, разбан возвращает возможность войти
            resp = await client.post("/api/admin/users/ban", headers=auth(t_admin),
                                     json={"user_id": student_id, "banned": True})
            assert (await resp.json())["user"]["banned"] is True
            assert (await client.get("/api/chat/me", headers=auth(t_student))).status == 401
            await client.post("/api/admin/users/ban", headers=auth(t_admin),
                              json={"user_id": student_id, "banned": False})
            resp = await client.post("/api/chat/login", json={"email": "p2@example.com", "password": "secret-pw-1"})
            assert resp.status == 200

            # Ключ-приглашение из панели работает для регистрации преподавателя
            resp = await client.post("/api/admin/invite-key", headers=auth(t_admin), json={"role": "teacher"})
            key = (await resp.json())["invite_key"]
            resp = await client.post("/api/chat/register", json={
                "first_name": "Берик", "last_name": "Нурлан", "phone": "+77003330001",
                "email": "teacher@example.com", "password": "secret-pw-1",
                "account_type": "teacher", "invite_key": key})
            assert resp.status == 201

            db.add_lead("Аян", "@ayan", "демо", None)
            resp = await client.get("/api/admin/leads", headers=auth(t_admin))
            assert (await resp.json())["leads"][0]["name"] == "Аян"

    run(go())


def test_admin_sees_reports_with_dialog_excerpt_and_resolves_them(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client(admin_token="admintok")
        async with client:
            boss = await _student(client, sent, 1, first="Босс")
            troll = await _student(client, sent, 2, first="Тролль")
            victim = await _student(client, sent, 3, "female", first="Алия")
            await _make_admin(client, boss)

            await client.post("/api/chat/search", headers=auth(troll["token"]), json={"preference": "any"})
            resp = await client.post("/api/chat/search", headers=auth(victim["token"]), json={"preference": "any"})
            dialog_id = (await resp.json())["dialog_id"]
            await client.post("/api/chat/send", headers=auth(troll["token"]),
                              json={"dialog_id": dialog_id, "text": "грубость"})
            await client.post("/api/chat/report", headers=auth(victim["token"]),
                              json={"dialog_id": dialog_id, "reason": "оскорбляет"})

            resp = await client.get("/api/admin/reports", headers=auth(boss["token"]))
            reports = (await resp.json())["reports"]
            assert len(reports) == 1
            report = reports[0]
            assert report["reason"] == "оскорбляет" and report["target"]["name"].startswith("Тролль")
            assert report["messages"] == [
                {"from_target": True, "kind": "text", "text": "грубость", "at": report["messages"][0]["at"]}]

            resp = await client.post("/api/admin/reports/resolve", headers=auth(boss["token"]),
                                     json={"report_id": report["id"]})
            assert resp.status == 200
            resp = await client.get("/api/admin/reports", headers=auth(boss["token"]))
            assert (await resp.json())["reports"] == []
            resp = await client.get("/api/admin/reports", headers=auth(boss["token"]), params={"all": "1"})
            assert len((await resp.json())["reports"]) == 1

    run(go())


# ---------- экзамены: снимки камеры и запись экрана кусками ----------

def test_exam_recording_is_uploaded_in_chunks(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client(admin_token="admintok")
        async with client:
            teacher, student, course_id, exam_id = await _setup_course_with_exam(client, sent, require_recording=True)
            t_teacher, t_student = teacher["token"], student["token"]

            # Кусок записи без начатого экзамена не принимается
            chunk = {"exam_id": exam_id, "seq": 0, "data": b64(b"webm-part-0;")}
            resp = await client.post("/api/academics/exams/recording-chunk", headers=auth(t_student), json=chunk)
            assert resp.status == 409

            await client.post("/api/academics/exams/start", headers=auth(t_student), json={"exam_id": exam_id})
            # Куски приходят не по порядку — склеиваются по seq
            for seq, part in ((1, b"webm-part-1;"), (0, b"webm-part-0;"), (2, b"webm-part-2")):
                resp = await client.post("/api/academics/exams/recording-chunk", headers=auth(t_student),
                                         json={"exam_id": exam_id, "seq": seq, "data": b64(part)})
                assert resp.status == 200

            resp = await client.post("/api/academics/exams/submit", headers=auth(t_student),
                                     json={"exam_id": exam_id, "answers": [1, 1], "tab_exit_count": 0})
            assert resp.status == 200

            # После сдачи дописать запись уже нельзя
            resp = await client.post("/api/academics/exams/recording-chunk", headers=auth(t_student),
                                     json={"exam_id": exam_id, "seq": 3, "data": b64(b"late")})
            assert resp.status == 409

            resp = await client.get(f"/api/academics/exams/{exam_id}/attempts", headers=auth(t_teacher))
            attempt = (await resp.json())["attempts"][0]
            assert attempt["has_recording"] is True and attempt["late"] is False

            resp = await client.get(f"/api/academics/exam-attempts/{attempt['id']}/recording", headers=auth(t_teacher))
            assert resp.status == 200
            assert await resp.read() == b"webm-part-0;webm-part-1;webm-part-2"

    run(go())


def test_exam_camera_snapshots_required_and_visible_only_to_teacher(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client(admin_token="admintok")
        async with client:
            teacher, student, course_id, _ = await _setup_course_with_exam(client, sent)
            t_teacher, t_student = teacher["token"], student["token"]
            resp = await client.post("/api/academics/exams", headers=auth(t_teacher), json={
                "course_id": course_id, "title": "С камерой", "require_camera": True})
            exam = (await resp.json())["exam"]
            assert exam["require_camera"] is True
            exam_id = exam["id"]
            await client.post("/api/academics/exam-questions", headers=auth(t_teacher), json={
                "exam_id": exam_id, "text": "2 + 2 = ?", "options": ["3", "4"], "correct_index": 1})

            await client.post("/api/academics/exams/start", headers=auth(t_student), json={"exam_id": exam_id})
            # Без единого снимка камеры сдать нельзя
            resp = await client.post("/api/academics/exams/submit", headers=auth(t_student),
                                     json={"exam_id": exam_id, "answers": [1]})
            assert resp.status == 400

            # Принимается только JPEG
            resp = await client.post("/api/academics/exams/snapshot", headers=auth(t_student),
                                     json={"exam_id": exam_id, "data": b64(PNG)})
            assert resp.status == 400
            for _ in range(2):
                resp = await client.post("/api/academics/exams/snapshot", headers=auth(t_student),
                                         json={"exam_id": exam_id, "data": b64(JPEG)})
                assert resp.status == 200 and (await resp.json())["stored"] is True

            resp = await client.post("/api/academics/exams/submit", headers=auth(t_student),
                                     json={"exam_id": exam_id, "answers": [1]})
            assert resp.status == 200

            resp = await client.get(f"/api/academics/exams/{exam_id}/attempts", headers=auth(t_teacher))
            attempt = (await resp.json())["attempts"][0]
            assert attempt["snapshot_count"] == 2

            resp = await client.get(f"/api/academics/exam-attempts/{attempt['id']}/snapshots", headers=auth(t_teacher))
            snapshots = (await resp.json())["snapshots"]
            assert len(snapshots) == 2
            resp = await client.get(f"/api/academics/exam-snapshots/{snapshots[0]['id']}", headers=auth(t_teacher))
            assert resp.status == 200 and await resp.read() == JPEG and resp.headers["Content-Type"] == "image/jpeg"

            # Студент чужие (и свои) снимки через преподавательские ручки не получает
            resp = await client.get(f"/api/academics/exam-attempts/{attempt['id']}/snapshots", headers=auth(t_student))
            assert resp.status == 403
            resp = await client.get(f"/api/academics/exam-snapshots/{snapshots[0]['id']}", headers=auth(t_student))
            assert resp.status == 403

            # Сброс попытки стирает и снимки
            await client.post("/api/academics/exam-attempts/reset", headers=auth(t_teacher),
                              json={"exam_id": exam_id, "user_id": student["user"]["id"]})
            assert db.count_exam_snapshots(exam_id, student["user"]["id"]) == 0

    run(go())


def test_exam_timer_is_server_side_and_late_submission_is_flagged(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client(admin_token="admintok")
        async with client:
            teacher, student, course_id, exam_id = await _setup_course_with_exam(client, sent)
            t_teacher, t_student = teacher["token"], student["token"]

            resp = await client.post("/api/academics/exams/start", headers=auth(t_student), json={"exam_id": exam_id})
            first = await resp.json()
            # Повторный старт (перезагрузка страницы) не обнуляет время
            resp = await client.post("/api/academics/exams/start", headers=auth(t_student), json={"exam_id": exam_id})
            assert (await resp.json())["started_at"] == first["started_at"]

            # Преподаватель видит, кто начал и не сдал
            resp = await client.get(f"/api/academics/exams/{exam_id}/attempts", headers=auth(t_teacher))
            body = await resp.json()
            assert body["attempts"] == [] and body["unfinished"][0]["first_name"] == "Аян"

            # Студент «вернулся» через 40 минут при лимите 30: ответы принимаются, но попытка помечена
            with db.conn:
                db.conn.execute("UPDATE exam_sessions SET started_at = datetime(started_at, '-40 minutes') || '+00:00'")
            resp = await client.get("/api/academics/exams/my-attempt", headers=auth(t_student),
                                    params={"exam_id": exam_id})
            assert (await resp.json())["in_progress"]["seconds_left"] == 0
            resp = await client.post("/api/academics/exams/submit", headers=auth(t_student),
                                     json={"exam_id": exam_id, "answers": [1, 1]})
            body = await resp.json()
            assert resp.status == 200 and body["late"] is True and body["duration_seconds"] >= 40 * 60

            resp = await client.get(f"/api/academics/exams/{exam_id}/attempts", headers=auth(t_teacher))
            body = await resp.json()
            assert body["attempts"][0]["late"] is True and body["unfinished"] == []

    run(go())


def test_file_download_survives_cyrillic_and_quotes_in_name(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client(admin_token="admintok")
        async with client:
            teacher, student, course_id, _ = await _setup_course_with_exam(client, sent)
            resp = await client.post("/api/academics/assignments", headers=auth(teacher["token"]), json={
                "course_id": course_id, "title": "Эссе",
                "file": {"filename": 'Задание "первое".pdf', "content_type": "application/pdf", "data": b64(b"%PDF-1.4")}})
            assignment_id = (await resp.json())["assignment"]["id"]
            resp = await client.get(f"/api/academics/assignments/{assignment_id}/file", headers=auth(student["token"]))
            assert resp.status == 200 and await resp.read() == b"%PDF-1.4"
            disposition = resp.headers["Content-Disposition"]
            assert disposition.startswith("attachment;") and "filename*=UTF-8''" in disposition
            assert resp.headers["X-Content-Type-Options"] == "nosniff"

    run(go())


# ---------- прямые чаты: непрочитанные, фото ----------

def test_direct_chat_unread_counts_preview_and_photos(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            a = await _student(client, sent, 1, first="Аян")
            b = await _student(client, sent, 2, "female", first="Алия")
            ta, tb = a["token"], b["token"]
            resp = await client.post("/api/chat/direct/start", headers=auth(ta), json={"user_id": b["user"]["id"]})
            dialog_id = (await resp.json())["dialog_id"]

            await client.post("/api/chat/direct/send", headers=auth(ta), json={"dialog_id": dialog_id, "text": "привет"})
            resp = await client.post("/api/chat/direct/send-photo", headers=auth(ta),
                                     json={"dialog_id": dialog_id, "data": b64(PNG)})
            assert resp.status == 200
            photo_id = (await resp.json())["photo_id"]

            # У получателя два непрочитанных, у отправителя — ноль
            assert (await (await client.get("/api/chat/unread", headers=auth(tb))).json())["unread"] == 2
            assert (await (await client.get("/api/chat/unread", headers=auth(ta))).json())["unread"] == 0
            resp = await client.get("/api/chat/direct/list", headers=auth(tb))
            dialog = (await resp.json())["dialogs"][0]
            assert dialog["unread"] == 2 and dialog["last_text"] == "📷 Фото" and dialog["last_mine"] is False
            assert dialog["partner"]["first_name"] == "Аян"

            # Открыл диалог — прочитано; фото доступно участнику
            resp = await client.get("/api/chat/direct/poll", headers=auth(tb), params={"dialog_id": dialog_id})
            messages = (await resp.json())["messages"]
            assert [m["kind"] for m in messages] == ["text", "photo"] and messages[0]["at"]
            assert (await (await client.get("/api/chat/unread", headers=auth(tb))).json())["unread"] == 0
            resp = await client.get(f"/api/chat/photo/{photo_id}", headers=auth(tb))
            assert resp.status == 200 and await resp.read() == PNG

    run(go())


# ---------- компания на ивент ----------

def test_event_company_matches_only_people_looking_for_the_same_event(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client()
        async with client:
            a = await _student(client, sent, 1)
            b = await _student(client, sent, 2, "female")
            c = await _student(client, sent, 3, "female")

            resp = await client.post("/api/chat/search", headers=auth(a["token"]),
                                     json={"preference": "any", "event_id": "remus-night"})
            body = await resp.json()
            assert body["status"] == "waiting" and body["event_title"] == "Remus Night"

            # Ищущий «просто собеседника» и ищущий на другой ивент с ним не сводятся
            resp = await client.post("/api/chat/search", headers=auth(b["token"]), json={"preference": "any"})
            assert (await resp.json())["status"] == "waiting"
            resp = await client.post("/api/chat/search", headers=auth(c["token"]),
                                     json={"preference": "any", "event_id": "acoustic-evening"})
            assert (await resp.json())["status"] == "waiting"

            # А тот, кто ищет компанию на тот же ивент, — сводится
            resp = await client.post("/api/chat/search", headers=auth(c["token"]),
                                     json={"preference": "any", "event_id": "remus-night"})
            body = await resp.json()
            assert body["status"] == "matched" and body["event_title"] == "Remus Night"
            resp = await client.get("/api/chat/poll", headers=auth(a["token"]))
            assert (await resp.json())["event_title"] == "Remus Night"

            resp = await client.post("/api/chat/search", headers=auth(a["token"]),
                                     json={"preference": "any", "event_id": "no-such-event"})
            assert resp.status == 404
            assert (await (await client.get("/api/stats")).json())["event_dialogs"] == 1

    run(go())


# ---------- походы и сводка ----------

def test_hike_signup_from_the_site_with_waitlist_and_checkin(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client(admin_token="admintok")
        async with client:
            a = await _student(client, sent, 1, first="Аян")
            b = await _student(client, sent, 2, "female", first="Алия")
            start = (hikes_now() + timedelta(minutes=30)).replace(tzinfo=None).isoformat(timespec="minutes")
            hike = db.hikes.create(club_id="summit", title="Кок-Жайляу", start_at=start, capacity=1)

            assert (await client.post(f"/api/hikes/{hike.id}/signup")).status == 401
            resp = await client.post(f"/api/hikes/{hike.id}/signup", headers=auth(a["token"]))
            assert (await resp.json())["hike"]["my_status"] == "going"
            resp = await client.post(f"/api/hikes/{hike.id}/signup", headers=auth(b["token"]))
            body = (await resp.json())["hike"]
            assert body["my_status"] == "waitlist" and body["waitlist_position"] == 1

            # Публичный список по-прежнему без имён и телефонов
            resp = await client.get("/api/hikes")
            public = next(h for h in await resp.json() if h["id"] == hike.id)
            assert public["going"] == 1 and public["waitlist"] == 1 and "signups" not in public

            # Имена и телефоны записавшихся видит только админ
            await _make_admin(client, a)
            resp = await client.get("/api/admin/hike-signups", headers=auth(a["token"]))
            signups = next(h for h in (await resp.json())["hikes"] if h["id"] == hike.id)["signups"]
            assert {s["full_name"] for s in signups} == {"Аян Номер1", "Алия Номер2"}
            assert all(s["src"] == "web" for s in signups)

            # Поход скоро начнётся — доступна отметка «Я спустился»
            resp = await client.get("/api/hikes/mine", headers=auth(a["token"]))
            mine = (await resp.json())["hikes"]
            assert mine[0]["active"] is True and mine[0]["checked_in"] is False
            assert (await client.post(f"/api/hikes/{hike.id}/checkin", headers=auth(a["token"]))).status == 200
            resp = await client.get("/api/hikes/mine", headers=auth(a["token"]))
            assert (await resp.json())["hikes"][0]["checked_in"] is True

            # Отмена записи продвигает первого из листа ожидания
            await client.post(f"/api/hikes/{hike.id}/cancel", headers=auth(a["token"]))
            resp = await client.get("/api/hikes/mine", headers=auth(b["token"]))
            assert (await resp.json())["hikes"][0]["my_status"] == "going"

    run(go())


def test_dashboard_summarises_the_day(monkeypatch):
    sent = capture_otp_emails(monkeypatch)

    async def go():
        client, db = make_client(admin_token="admintok")
        async with client:
            teacher, student, course_id, exam_id = await _setup_course_with_exam(client, sent)
            t_teacher, t_student = teacher["token"], student["token"]

            resp = await client.get("/api/dashboard", headers=auth(t_student))
            weekday = (await resp.json())["weekday"]
            await client.post("/api/academics/schedule", headers=auth(t_teacher), json={
                "course_id": course_id, "day_of_week": weekday, "start_time": "09:00", "end_time": "10:30",
                "room": "204", "kind": "лекция"})
            resp = await client.post("/api/academics/assignments", headers=auth(t_teacher), json={
                "course_id": course_id, "title": "Эссе", "due_at": "2020-01-01"})
            assignment_id = (await resp.json())["assignment"]["id"]
            await client.post("/api/academics/grades/set", headers=auth(t_teacher), json={
                "course_id": course_id, "user_id": student["user"]["id"], "label": "РК1", "score": 87})
            await client.post("/api/events/remus-night/going", headers=auth(t_student), json={"going": True})
            resp = await client.post("/api/chat/direct/start", headers=auth(t_teacher),
                                     json={"user_id": student["user"]["id"]})
            await client.post("/api/chat/direct/send", headers=auth(t_teacher),
                              json={"dialog_id": (await resp.json())["dialog_id"], "text": "Зайдите после пары"})

            resp = await client.get("/api/dashboard", headers=auth(t_student))
            assert resp.status == 200
            d = await resp.json()
            assert d["user"]["first_name"] == "Аян"
            assert [c["course_code"] for c in d["classes_today"]] == ["CS101"] and d["classes_today"][0]["room"] == "204"
            assert d["deadlines"] == [{"id": assignment_id, "course_id": course_id, "course_code": "CS101",
                                       "title": "Эссе", "due_at": "2020-01-01", "overdue": True}]
            assert [x["id"] for x in d["exams"]] == [exam_id]
            assert d["grades"][0]["label"] == "РК1" and d["grades"][0]["score"] == 87
            assert d["unread"] == 1
            assert d["events"] == [{"id": "remus-night", "title": "Remus Night", "date": d["events"][0]["date"]}]
            assert "teaching" not in d

            # Сдал задание — из списка дел оно уходит, а у преподавателя появляется «на проверке»
            await client.post("/api/academics/submissions", headers=auth(t_student),
                              json={"assignment_id": assignment_id, "text": "готово"})
            resp = await client.get("/api/dashboard", headers=auth(t_student))
            assert (await resp.json())["deadlines"] == []
            resp = await client.get("/api/dashboard", headers=auth(t_teacher))
            assert (await resp.json())["teaching"] == {"courses": 1, "ungraded": 1}

            assert (await client.get("/api/dashboard")).status == 401

    run(go())
