"""Сквозные сценарии клуба: студент записывается, организатор управляет походом, безопасность и фоновые задачи."""
import asyncio
from datetime import timedelta

import pytest
from aiogram.methods import SendDocument, SendLocation

from bot import club_jobs, weather
from bot import hikes as hk
from bot.handlers import club_admin
from tests.test_bot_flow import ADMIN, ORGANIZER, Harness

FAR = "2099-12-31T07:00"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def no_delays_no_network(monkeypatch):
    monkeypatch.setattr(club_admin, "SEND_DELAY", 0)
    weather._cache.clear()

    async def offline(url, params):
        raise OSError("нет сети в тестах")
    monkeypatch.setattr(weather, "fetch_json", offline)


def make_hike(h, start=FAR, cap=2, **kw):
    return h.db.hikes.create(club_id="summit", title="Тропа", start_at=start, capacity=cap, duration_hours=6,
                             meeting_point="Остановка", **kw)


def around_now(hours_from_now: float) -> str:
    return (hk.now() + timedelta(hours=hours_from_now)).strftime("%Y-%m-%dT%H:%M")


async def join(h, uid, hike_id, name="Айдар", phone="+7 707 123 45 67"):
    await h.click(uid, f"hike:join:{hike_id}")
    await h.send(uid, name)
    await h.send(uid, phone)


def test_signup_via_instagram_link_without_profile():
    async def scenario():
        h = Harness()
        hike = make_hike(h)
        await h.send(5, f"/start hike_{hike.id}__ig")           # у студента нет профиля анонимного чата
        assert any("Тропа" in t and "партнёр Almau Messenger" in t for t in h.texts_to(5))
        await join(h, 5, hike.id)
        s = h.db.hikes.signup_of(hike.id, 5)
        assert (s.full_name, s.phone, s.status, s.src) == ("Айдар", "+77071234567", "going", "ig")
        assert any("Новая запись" in t and "Айдар" in t for t in h.texts_to(ORGANIZER))
        assert any("Новая запись" in t for t in h.texts_to(ADMIN))
        assert h.db.hikes.report("summit")["sources"]["ig"] == {"views": 1, "signups": 1}
    run(scenario())


def test_bad_phone_is_asked_again_and_commands_interrupt_the_form():
    async def scenario():
        h = Harness()
        hike = make_hike(h)
        await h.click(5, f"hike:join:{hike.id}")
        await h.send(5, "Айдар")
        await h.send(5, "не телефон")
        assert any("Не похоже на номер" in t for t in h.texts_to(5))
        assert h.db.hikes.signup_of(hike.id, 5) is None
        await h.send(5, "/hikes")                                # команда прерывает форму и выполняется
        assert any("Походы Summit Seekers" in t for t in h.texts_to(5))
        await h.send(5, "+77071234567")                          # форма закрыта: это уже не телефон
        assert h.db.hikes.signup_of(hike.id, 5) is None
    run(scenario())


def test_waitlist_and_promotion_on_cancel():
    async def scenario():
        h = Harness()
        hike = make_hike(h, cap=1)
        await join(h, 1, hike.id, "Первый", "+77070000001")
        await join(h, 2, hike.id, "Второй", "+77070000002")
        assert h.db.hikes.signup_of(hike.id, 2).status == "waitlist"
        assert any("в листе ожидания" in t for t in h.texts_to(2))
        await h.click(1, f"hike:leave:{hike.id}")
        await h.click(1, f"hike:leaveyes:{hike.id}")
        assert h.db.hikes.signup_of(hike.id, 1) is None
        assert h.db.hikes.signup_of(hike.id, 2).status == "going"
        assert any("Освободилось место" in t for t in h.texts_to(2))
    run(scenario())


def test_cannot_sign_up_while_in_anonymous_dialog():
    async def scenario():
        h = Harness()
        hike = make_hike(h)
        await h.register(1)
        await h.register(2)
        await h.send(1, "/search")
        await h.send(2, "/search")
        assert h.mm.partner_of(1) == 2
        await h.click(1, f"hike:join:{hike.id}")
        await h.send(1, "Айдар")                                 # не должно стать именем: сообщение уходит собеседнику
        assert h.db.hikes.signup_of(hike.id, 1) is None
        assert h.copies_to(2)
    run(scenario())


def test_gear_school_and_weather_buttons():
    async def scenario():
        h = Harness()
        hike = make_hike(h)
        await h.click(7, f"hike:gear:{hike.id}")
        assert any("Что взять" in t and "Трекинговая обувь" in t and "112" in t for t in h.texts_to(7))
        await h.send(7, "/school")
        assert any("Школа туризма" in t for t in h.texts_to(7))
        await h.click(7, "school:weather")
        assert any("Погода в горах" in t for t in h.texts_to(7))
        await h.click(7, f"hike:wx:{hike.id}")                   # далеко в будущем → вежливый отказ, не ошибка
        assert any("🌤" in t and "появится" in t for t in h.texts_to(7))
    run(scenario())


def test_organizer_panel_is_closed_to_students():
    async def scenario():
        h = Harness()
        await h.send(5, "/club")
        assert not any("Панель организатора" in t for t in h.texts_to(5))
        await h.send(ORGANIZER, "/club")
        assert any("Панель организатора" in t for t in h.texts_to(ORGANIZER))
        await h.send(ADMIN, "/club")                             # админы Almau Messenger тоже имеют доступ
        assert any("Панель организатора" in t for t in h.texts_to(ADMIN))
    run(scenario())


def test_organizer_creates_hike_step_by_step():
    async def scenario():
        h = Harness()
        o = ORGANIZER
        await h.click(o, "club:new")
        await h.send(o, "Первый снег")
        await h.send(o, "31.12.2099 07:00")
        await h.send(o, "Остановка Медеу, 06:30")
        await h.send(o, "-")
        await h.click(o, "club:diff:1")
        await h.send(o, "5,5")
        await h.send(o, "12")
        await h.send(o, "3000 тг")
        assert any("Проверь поход" in t and "Первый снег" in t for t in h.texts_to(o))
        await h.click(o, "club:publish")
        (hike,) = h.db.hikes.for_club("summit")
        assert (hike.title, hike.start_at, hike.difficulty, hike.duration_hours, hike.capacity, hike.price) == (
            "Первый снег", "2099-12-31T07:00", "Средний", 5.5, 12, "3000 тг")
        assert hike.meeting_point == "Остановка Медеу, 06:30" and hike.route == ""
    run(scenario())


def test_organizer_form_validates_and_cancel_works():
    async def scenario():
        h = Harness()
        o = ORGANIZER
        await h.click(o, "club:new")
        await h.send(o, "Ок")                                    # слишком короткое название
        assert any("от 3 до 80" in t for t in h.texts_to(o))
        await h.send(o, "Нормальное название")
        await h.send(o, "01.01.2020 07:00")                      # прошлое
        assert any("в будущем" in t for t in h.texts_to(o))
        await h.send(o, "/cancel")
        assert h.db.hikes.for_club("summit") == []
        await h.send(o, "31.12.2099 07:00")                      # форма закрыта, это просто сообщение
        assert h.db.hikes.for_club("summit") == []
    run(scenario())


def test_roster_csv_broadcast_and_cancel_hike():
    async def scenario():
        h = Harness()
        hike = make_hike(h, cap=2)
        await join(h, 1, hike.id, "Айдар", "+77070000001")
        await join(h, 2, hike.id, "Дана", "+77070000002")
        o = ORGANIZER

        await h.click(o, f"club:roster:{hike.id}")
        assert any("Айдар" in t and "+77070000001" in t and "Дана" in t for t in h.texts_to(o))

        await h.click(o, f"club:csv:{hike.id}")
        docs = [c for c in h.session.calls if isinstance(c, SendDocument)]
        assert len(docs) == 1 and docs[0].document.filename == f"{hike.id}.csv"

        await h.click(o, f"club:bc:{hike.id}")
        await h.send(o, "Выходим на час раньше из-за погоды")
        await h.click(o, "club:bcsend")
        for uid in (1, 2):
            assert any("Выходим на час раньше" in t and "Summit Seekers" in t for t in h.texts_to(uid))
        assert any("Доставлено: 2 из 2" in str(c) for c in h.session.calls)

        await h.click(o, f"club:cancel:{hike.id}")
        await h.click(o, f"club:cancelyes:{hike.id}")
        assert not h.db.hikes.get(hike.id).is_open
        assert any("отменён организатором" in t for t in h.texts_to(1))
    run(scenario())


def test_report_and_links_for_organizer():
    async def scenario():
        h = Harness()
        hike = make_hike(h)
        await h.send(1, f"/start hike_{hike.id}__site")
        await join(h, 1, hike.id)
        await h.click(ORGANIZER, "club:report")
        report = " ".join(h.texts_to(ORGANIZER))
        assert "Сайт: 1 → 1" in report
        await h.click(ORGANIZER, "club:links")
        links = " ".join(h.texts_to(ORGANIZER))
        assert f"start=hike_{hike.id}__ig" in links and f"start=hike_{hike.id}__site" in links
    run(scenario())


def test_descended_button_and_all_checked_in_notice():
    async def scenario():
        h = Harness()
        hike = make_hike(h, start=around_now(-1))                # поход идёт прямо сейчас
        await join(h, 1, hike.id, "Айдар", "+77070000001")
        await join(h, 2, hike.id, "Дана", "+77070000002")
        await h.click(1, f"hike:down:{hike.id}")
        assert h.db.hikes.signup_of(hike.id, 1).checked_in
        assert not any("Все участники" in t for t in h.texts_to(ORGANIZER))
        await h.click(2, f"hike:down:{hike.id}")
        assert any("Все участники" in t and "спустились" in t for t in h.texts_to(ORGANIZER))
    run(scenario())


def test_sos_alerts_organizers_and_forwards_location():
    async def scenario():
        h = Harness()
        hike = make_hike(h, start=around_now(-1))
        await join(h, 1, hike.id, "Айдар", "+77070000001")
        await h.click(1, f"hike:sos:{hike.id}")
        alert = [t for t in h.texts_to(ORGANIZER) if "SOS" in t]
        assert alert and "Айдар" in alert[0] and "+77070000001" in alert[0]
        assert any("112" in t for t in h.texts_to(1))
        await h.send_location(1, 43.1, 77.05)
        locs = [c for c in h.session.calls if isinstance(c, SendLocation)]
        assert {c.chat_id for c in locs} == {ORGANIZER, ADMIN}
        assert (locs[0].latitude, locs[0].longitude) == (43.1, 77.05)
    run(scenario())


def test_sos_is_refused_outside_of_a_hike():
    async def scenario():
        h = Harness()
        hike = make_hike(h)                                      # в далёком будущем
        await join(h, 1, hike.id)
        await h.send(1, "/sos")
        assert any("пока он идёт" in t and "112" in t for t in h.texts_to(1))
        assert not any("SOS" in t for t in h.texts_to(ORGANIZER))
    run(scenario())


def test_background_jobs_remind_check_in_and_alert_organizer():
    h = Harness()
    hike = make_hike(h, start="2026-10-24T07:00", cap=2)
    # signup() проверяет реальное время, а этот поход уже может быть в прошлом: записи кладём напрямую в базу
    for uid, name in ((1, "Айдар"), (2, "Дана")):
        h.db.hikes.conn.execute(
            "INSERT INTO hike_signups (hike_id, user_id, full_name, phone, status, created_at) VALUES (?,?,?,?,?,?)",
            (hike.id, uid, name, f"+7707000000{uid}", "going", "2026-10-01T10:00:00"))
    start, end = hike.start, hike.end

    async def scenario():
        run_at = lambda at: club_jobs.run_once(h.bot, h.db, h.config, at)
        await run_at(start - timedelta(hours=23))
        assert any("Завтра поход" in t for t in h.texts_to(1)) and any("Завтра поход" in t for t in h.texts_to(2))
        n = len(h.texts_to(1))
        await run_at(start - timedelta(hours=22))                  # повторно не шлём
        assert len(h.texts_to(1)) == n
        await run_at(start - timedelta(hours=2))
        assert any("Через ~3 часа" in t for t in h.texts_to(1))

        await run_at(end + timedelta(minutes=5))
        assert any("Нажми, когда спустишься" in t for t in h.texts_to(1))
        h.db.hikes.check_in(hike.id, 1)
        await run_at(end + timedelta(minutes=95))
        alerts = [t for t in h.texts_to(ORGANIZER) if "не отметились" in t]
        assert len(alerts) == 1 and "Дана" in alerts[0] and "Айдар" not in alerts[0]
        await run_at(end + timedelta(minutes=100))                 # тревога одна
        assert len([t for t in h.texts_to(ORGANIZER) if "не отметились" in t]) == 1
    run(scenario())


def test_weekly_report_once_on_monday_morning():
    async def scenario():
        h = Harness()
        hike = make_hike(h)
        await join(h, 1, hike.id)
        monday = hk.now().replace(year=2026, month=10, day=12, hour=9, minute=5)           # понедельник
        assert monday.weekday() == 0
        await club_jobs.run_once(h.bot, h.db, h.config, monday.replace(hour=8))
        assert not any("Еженедельный отчёт" in t for t in h.texts_to(ORGANIZER))
        await club_jobs.run_once(h.bot, h.db, h.config, monday)
        await club_jobs.run_once(h.bot, h.db, h.config, monday + timedelta(minutes=30))
        assert len([t for t in h.texts_to(ORGANIZER) if "Еженедельный отчёт" in t]) == 1
        tuesday = monday + timedelta(days=1)
        await club_jobs.run_once(h.bot, h.db, h.config, tuesday)
        assert len([t for t in h.texts_to(ORGANIZER) if "Еженедельный отчёт" in t]) == 1
    run(scenario())


def test_clubs_list_marks_partner():
    async def scenario():
        h = Harness()
        await h.send(3, "🏛 Клубы")
        text = " ".join(h.texts_to(3))
        assert "Партнёр Almau Messenger" in text and "/hikes" in text
    run(scenario())
