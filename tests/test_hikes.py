"""Походы Summit Seekers: хранилище, лист ожидания, напоминания, безопасность, отчёт, погода."""
import asyncio
import json
from datetime import date, datetime, timedelta

import pytest

from bot import hikes as hk
from bot.config import BASE_DIR
from bot.content import Content
from bot.db import Database

START = "2026-10-24T07:00"                       # поход на 6 часов: 07:00–13:00
START_DT = datetime(2026, 10, 24, 7, 0, tzinfo=hk.TZ)
END_DT = START_DT + timedelta(hours=6)
BEFORE = START_DT - timedelta(days=3)            # «сейчас» для записи


def make(db, start=START, cap=2, **kw):
    return db.hikes.create(club_id="summit", title="Тест", start_at=start, capacity=cap, duration_hours=6, **kw)


def test_ids_are_unique_per_day():
    db = Database(":memory:")
    assert make(db).id == "h20261024-1"
    assert make(db).id == "h20261024-2"


def test_seed_is_idempotent_and_uses_real_file():
    db = Database(":memory:")
    items = Content(BASE_DIR / "site" / "data").hikes_seed
    assert db.hikes.seed(items) == len(items) > 0
    assert db.hikes.seed(items) == 0
    assert all(h.demo for h in db.hikes.for_club("summit"))


def test_signup_capacity_waitlist_and_duplicates():
    db = Database(":memory:")
    h = make(db, cap=2)
    assert db.hikes.signup(h.id, 1, "А", "+77071111111", at=BEFORE) == "going"
    assert db.hikes.signup(h.id, 2, "Б", "+77072222222", at=BEFORE) == "going"
    assert db.hikes.signup(h.id, 3, "В", "+77073333333", at=BEFORE) == "waitlist"
    assert db.hikes.signup(h.id, 1, "А", "+77071111111", at=BEFORE) == "exists"
    assert db.hikes.counts(h.id) == (2, 1)
    assert db.hikes.waitlist_position(h.id, 3) == 1
    assert db.hikes.signup("нет-такого", 9, "Х", "+77070000000") == "unknown"


def test_signup_closed_after_end_or_when_cancelled():
    db = Database(":memory:")
    h = make(db)
    assert db.hikes.signup(h.id, 1, "А", "+77071111111", at=END_DT + timedelta(minutes=1)) == "closed"
    db.hikes.cancel_hike(h.id)
    assert db.hikes.signup(h.id, 1, "А", "+77071111111", at=BEFORE) == "closed"


def test_cancel_promotes_first_in_waitlist_and_deletes_personal_data():
    db = Database(":memory:")
    h = make(db, cap=1)
    db.hikes.signup(h.id, 1, "А", "+77071111111", at=BEFORE)
    db.hikes.signup(h.id, 2, "Б", "+77072222222", at=BEFORE)
    db.hikes.signup(h.id, 3, "В", "+77073333333", at=BEFORE)
    existed, promoted = db.hikes.cancel_signup(h.id, 1)
    assert existed and promoted.user_id == 2 and promoted.status == "going"
    assert db.hikes.signup_of(h.id, 1) is None
    assert db.hikes.counts(h.id) == (1, 1)
    assert db.hikes.cancel_signup(h.id, 1) == (False, None)
    # отмена записи из листа ожидания никого не продвигает
    assert db.hikes.cancel_signup(h.id, 3) == (True, None)


def test_input_parsers():
    assert hk.normalize_phone("+7 (707) 123-45-67") == "+77071234567"
    assert hk.normalize_phone("87071234567") == "87071234567"
    assert hk.normalize_phone("привет") is None and hk.normalize_phone("123") is None
    at = datetime(2026, 10, 1, 12, 0, tzinfo=hk.TZ)
    assert hk.parse_start("24.10.2026 07:00", at) == "2026-10-24T07:00"
    assert hk.parse_start("24.10.2026", at) is None
    assert hk.parse_start("01.09.2026 07:00", at) is None
    assert hk.parse_duration("4,5") == 4.5 and hk.parse_duration("30") is None and hk.parse_duration("x") is None
    assert hk.parse_capacity("20") == 20 and hk.parse_capacity("0") is None and hk.parse_capacity("-3") is None
    assert hk.parse_deep_link("hike_h20261024-1__ig") == ("h20261024-1", "ig")
    assert hk.parse_deep_link("hike_summit-demo-1") == ("summit-demo-1", "")


def test_reminders_24h_and_3h_each_sent_once_and_only_to_going():
    db = Database(":memory:")
    h = make(db, cap=1)
    db.hikes.signup(h.id, 1, "А", "+77071111111", at=BEFORE)
    db.hikes.signup(h.id, 2, "Б", "+77072222222", at=BEFORE)   # лист ожидания
    assert db.hikes.due_reminders(START_DT - timedelta(hours=25)) == []
    due = db.hikes.due_reminders(START_DT - timedelta(hours=23))
    assert [(s.user_id, kind) for _, s, kind in due] == [(1, "24h")]
    db.hikes.mark_reminded(h.id, 1, "24h")
    assert db.hikes.due_reminders(START_DT - timedelta(hours=22)) == []
    due = db.hikes.due_reminders(START_DT - timedelta(hours=2))
    assert [(s.user_id, kind) for _, s, kind in due] == [(1, "3h")]
    db.hikes.mark_reminded(h.id, 1, "3h")
    assert db.hikes.due_reminders(START_DT - timedelta(hours=1)) == []
    assert db.hikes.due_reminders(START_DT + timedelta(minutes=1)) == []   # после старта не напоминаем


def test_late_signup_gets_only_3h_reminder():
    db = Database(":memory:")
    h = make(db)
    db.hikes.signup(h.id, 1, "А", "+77071111111", at=START_DT - timedelta(hours=2))
    assert [k for _, _, k in db.hikes.due_reminders(START_DT - timedelta(hours=2))] == ["3h"]
    db.hikes.mark_reminded(h.id, 1, "3h")
    assert db.hikes.due_reminders(START_DT - timedelta(hours=1)) == []


def test_checkin_prompt_and_safety_alert_flow():
    db = Database(":memory:")
    h = make(db, cap=3)
    for uid in (1, 2):
        db.hikes.signup(h.id, uid, f"У{uid}", f"+7707000000{uid}", at=BEFORE)
    assert db.hikes.due_checkin_prompts(END_DT - timedelta(minutes=1)) == []
    (hike, pending), = db.hikes.due_checkin_prompts(END_DT + timedelta(minutes=1))
    assert {s.user_id for s in pending} == {1, 2}
    db.hikes.mark_hike_flag(h.id, "checkin_prompt_sent")
    assert db.hikes.due_checkin_prompts(END_DT + timedelta(minutes=2)) == []

    assert db.hikes.check_in(h.id, 1) and not db.hikes.check_in(h.id, 1)
    assert db.hikes.due_safety_alerts(END_DT + timedelta(minutes=89)) == []
    (hike, missing), = db.hikes.due_safety_alerts(END_DT + timedelta(minutes=91))
    assert [s.user_id for s in missing] == [2]
    db.hikes.mark_hike_flag(h.id, "safety_alert_sent")
    assert db.hikes.due_safety_alerts(END_DT + timedelta(minutes=92)) == []


def test_waitlist_cannot_check_in_and_active_window():
    db = Database(":memory:")
    h = make(db, cap=1)
    db.hikes.signup(h.id, 1, "А", "+77071111111", at=BEFORE)
    db.hikes.signup(h.id, 2, "Б", "+77072222222", at=BEFORE)
    assert not db.hikes.check_in(h.id, 2)
    assert db.hikes.active_for_user(1, START_DT - timedelta(hours=3)) == []
    assert db.hikes.active_for_user(1, START_DT - timedelta(minutes=30))[0][0].id == h.id
    assert db.hikes.active_for_user(2, START_DT + timedelta(hours=1)) == []   # лист ожидания не «в походе»
    assert db.hikes.active_for_user(1, END_DT + timedelta(hours=13)) == []


def test_purge_removes_names_but_keeps_statistics():
    db = Database(":memory:")
    h = make(db)
    db.hikes.log_view(h.id, 1, "ig")
    db.hikes.signup(h.id, 1, "А", "+77071111111", "ig", at=BEFORE)
    assert db.hikes.purge_personal_data(END_DT + timedelta(days=29)) == 0
    assert db.hikes.purge_personal_data(END_DT + timedelta(days=31)) == 1
    assert db.hikes.signups(h.id) == []
    assert db.hikes.report("summit")["totals"]["signups"] == 1


def test_last_view_src_and_kv():
    db = Database(":memory:")
    h = make(db)
    assert db.hikes.last_view_src(h.id, 1) == ""
    db.hikes.log_view(h.id, 1, "ig")
    db.hikes.log_view(h.id, 1, "site")
    assert db.hikes.last_view_src(h.id, 1) == "site"
    assert db.hikes.kv_get("x") is None
    db.hikes.kv_set("x", "1")
    db.hikes.kv_set("x", "2")
    assert db.hikes.kv_get("x") == "2"


@pytest.fixture(autouse=True)
def payload(day="2026-10-24", code=3, tmin=2.0, tmax=11.0, precip=0.2, wind=4.0):
    return {"daily": {"time": [day], "weather_code": [code], "temperature_2m_max": [tmax],
                      "temperature_2m_min": [tmin], "precipitation_sum": [precip], "wind_speed_10m_max": [wind]}}


def test_hikes_and_school_json_are_valid():
    data = BASE_DIR / "site" / "data"
    hikes = json.loads((data / "hikes.json").read_text(encoding="utf-8"))
    ids = [h["id"] for h in hikes]
    assert len(ids) == len(set(ids))
    for h in hikes:
        assert h["difficulty"] in hk.DIFFICULTIES and h["capacity"] > 0
        datetime.fromisoformat(h["start"])
        assert len(f"hike:leaveyes:{h['id']}".encode()) <= 64 and "__" not in h["id"]
    school = json.loads((data / "school.json").read_text(encoding="utf-8"))
    lesson_ids = [x["id"] for x in school["lessons"]]
    assert len(lesson_ids) == len(set(lesson_ids)) >= 5
    assert all(x["points"] for x in school["lessons"]) and school["gear"] and school["safety"]
    assert any("112" in s for s in school["safety"])


def test_summit_is_marked_as_partner():
    content = Content(BASE_DIR / "site" / "data")
    assert {c.id for c in content.clubs if c.partner} == {"summit", "remus", "hunters", "mof", "mafia"}
