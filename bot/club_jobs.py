"""Фоновые задачи партнёрского клуба: напоминания, «Я спустился», тревога организатору,
еженедельный отчёт и удаление старых персональных данных.

run_once() делает один проход и принимает текущее время параметром, поэтому логику
можно проверять в тестах без ожидания. club_jobs_loop() вызывает его раз в минуту.
"""
import asyncio
import logging
from datetime import datetime

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot import hikes as hk
from bot import weather
from bot.config import Config
from bot.db import Database
from bot.handlers.chat import _safe_send
from bot.handlers.hikes import notify_organizers
from bot.hike_format import e, reminder_text, report_text, safety_alert_text

logger = logging.getLogger(__name__)

REPORT_HOUR = 9   # по понедельникам в 9:00 по Алматы


def _button(text: str, data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=data)]])


async def _forecast_line(hike: hk.Hike, at: datetime) -> str:
    try:
        return weather.forecast_text(await weather.get_forecast(hike.lat, hike.lon, hike.start.date(), at.date()))
    except weather.ForecastError:
        return ""


async def _remind(bot: Bot, db: Database, at: datetime) -> int:
    sent = 0
    for hike, signup, kind in db.hikes.due_reminders(at):
        forecast = await _forecast_line(hike, at) if kind == "24h" else ""
        markup = (_button("🎒 Что взять · отменить запись", f"hike:open:{hike.id}") if kind == "24h"
                  else _button("📍 Карточка похода", f"hike:open:{hike.id}"))
        if await _safe_send(bot, signup.user_id, reminder_text(hike, kind, forecast), reply_markup=markup):
            sent += 1
        db.hikes.mark_reminded(hike.id, signup.user_id, kind)   # помечаем и при ошибке отправки, чтобы не долбить
    return sent


async def _checkins(bot: Bot, db: Database, config: Config, at: datetime) -> None:
    for hike, pending in db.hikes.due_checkin_prompts(at):
        for s in pending:
            await _safe_send(bot, s.user_id, f"🏁 Поход «{e(hike.title)}» должен был закончиться. Как ты? "
                                             "Нажми, когда спустишься: так организатор будет знать, что ты в порядке.",
                             reply_markup=_button("🟢 Я спустился", f"hike:down:{hike.id}"))
        db.hikes.mark_hike_flag(hike.id, "checkin_prompt_sent")
    for hike, missing in db.hikes.due_safety_alerts(at):
        if missing:
            await notify_organizers(bot, config, safety_alert_text(hike, missing))
        db.hikes.mark_hike_flag(hike.id, "safety_alert_sent")


async def _weekly_report(bot: Bot, db: Database, config: Config, at: datetime) -> bool:
    today = at.date().isoformat()
    if at.weekday() != 0 or at.hour < REPORT_HOUR or db.hikes.kv_get("weekly_report_date") == today:
        return False
    db.hikes.kv_set("weekly_report_date", today)
    if not db.hikes.for_club(hk.PARTNER_CLUB_ID):
        return False
    text = "🗓 Еженедельный отчёт\n\n" + report_text(db.hikes.report(hk.PARTNER_CLUB_ID))
    for oid in (config.club_admin_ids or config.admin_ids):
        await _safe_send(bot, oid, text)
    return True


async def run_once(bot: Bot, db: Database, config: Config, at: datetime | None = None) -> None:
    at = at or hk.now()
    await _remind(bot, db, at)
    await _checkins(bot, db, config, at)
    await _weekly_report(bot, db, config, at)
    if db.hikes.kv_get("purge_date") != at.date().isoformat():
        db.hikes.kv_set("purge_date", at.date().isoformat())
        removed = db.hikes.purge_personal_data(at)
        if removed:
            logger.info("Удалено записей с персональными данными: %s", removed)


async def club_jobs_loop(bot: Bot, db: Database, config: Config, interval: float = 60.0) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            await run_once(bot, db, config)
        except Exception:  # сбой одного прохода не должен останавливать напоминания навсегда
            logger.exception("Ошибка в фоновых задачах клуба")
