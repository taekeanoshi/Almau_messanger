"""Тексты про походы: карточка, список записавшихся, CSV, отчёт, напоминания.

Чистые функции без aiogram. Всё, что ввели люди (имена, названия, объявления), экранируется,
потому что бот отправляет сообщения в HTML-режиме.
"""
import csv
import html
import io
from datetime import datetime

from bot.hikes import PARTNER_NAME, Hike, Signup

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря"]
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
SOURCE_LABELS = {"": "Напрямую в боте", "ig": "Instagram", "site": "Сайт", "tg": "Telegram-канал"}
SOURCE_LINKS = (("ig", "Instagram"), ("site", "Сайт"), ("tg", "Telegram-канал"))
PARTNER_LINE = f"🤝 {PARTNER_NAME} — партнёр Almau Messenger"
SOS_NOTE = "Если угрожает жизни, звоните 112."


def e(text: str) -> str:
    return html.escape(str(text))


def fmt_when(hike: Hike) -> str:
    d = hike.start
    return f"{d.day} {MONTHS[d.month - 1]}, {WEEKDAYS[d.weekday()]}, {d:%H:%M}"


def fmt_short(hike: Hike) -> str:
    d = hike.start
    return f"{d.day} {MONTHS[d.month - 1][:3]}"


def fmt_hours(hours: float) -> str:
    return f"~{hours:g} ч".replace(".", ",")


def deep_link(bot_username: str, hike_id: str, src: str = "") -> str:
    return f"https://t.me/{bot_username}?start=hike_{hike_id}" + (f"__{src}" if src else "")


def source_label(src: str) -> str:
    return SOURCE_LABELS.get(src, f"Метка «{src}»")


def card_text(hike: Hike, going: int, waitlist: int, mine: Signup | None = None, waitlist_pos: int | None = None) -> str:
    lines = [f"🏔 <b>{e(hike.title)}</b>" + ("  <i>(пример)</i>" if hike.demo else "")]
    if not hike.is_open:
        lines.append("❌ <b>Поход отменён</b>")
    lines += [
        f"🗓 {fmt_when(hike)} · ⏱ {fmt_hours(hike.duration_hours)}",
        f"📍 Сбор: {e(hike.meeting_point) or 'уточняется'}",
        f"🥾 Сложность: {e(hike.difficulty)} · 💰 {e(hike.price)}",
    ]
    if hike.route:
        lines.append(f"🧭 {e(hike.route)}")
    if going >= hike.capacity:
        lines.append(f"👥 Мест нет ({hike.capacity} из {hike.capacity}). В листе ожидания: {waitlist}")
    else:
        lines.append(f"👥 Свободно мест: {hike.capacity - going} из {hike.capacity}")
    if mine is not None:
        if mine.status == "going":
            lines.append("✅ <b>Ты записан(а)</b>" + (" · 🟢 спустился(ась)" if mine.checked_in else ""))
        else:
            lines.append(f"⏳ <b>Ты в листе ожидания, №{waitlist_pos or '?'}</b>. Если место освободится, напишем.")
    lines.append(f"\n{PARTNER_LINE}")
    return "\n".join(lines)


def roster_text(hike: Hike, signups: list[Signup]) -> str:
    going = [s for s in signups if s.status == "going"]
    waiting = [s for s in signups if s.status == "waitlist"]
    lines = [f"👥 <b>{e(hike.title)}</b>, {fmt_short(hike)}", f"Едут: {len(going)} из {hike.capacity}, лист ожидания: {len(waiting)}"]
    for i, s in enumerate(going, 1):
        lines.append(f"{i}. {e(s.full_name)} · {e(s.phone)}" + (" · 🟢" if s.checked_in else ""))
    if waiting:
        lines.append("\n<b>Лист ожидания</b>")
        for i, s in enumerate(waiting, 1):
            lines.append(f"{i}. {e(s.full_name)} · {e(s.phone)}")
    if not signups:
        lines.append("Пока никто не записался.")
    return "\n".join(lines)


def csv_bytes(signups: list[Signup]) -> bytes:
    """CSV с BOM, чтобы Excel правильно показал кириллицу."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["№", "Имя", "Телефон", "Статус", "Источник", "Записан(а)", "Спустился(ась)"])
    for i, s in enumerate(signups, 1):
        w.writerow([i, s.full_name, s.phone, "едет" if s.status == "going" else "лист ожидания",
                    source_label(s.src), s.created_at, "да" if s.checked_in else "нет"])
    return buf.getvalue().encode("utf-8-sig")


def chunk(text: str, limit: int = 3500) -> list[str]:
    """Режет длинное сообщение по строкам: у Telegram лимит 4096 символов."""
    parts, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + len(line) + 1 > limit:
            parts.append(cur)
            cur = ""
        cur += ("\n" if cur else "") + line
    return parts + [cur] if cur else parts


def percent(part: int, whole: int) -> str:
    return f"{round(part * 100 / whole)}%" if whole else "—"


def report_text(report: dict) -> str:
    t = report["totals"]
    lines = [f"📊 <b>{PARTNER_NAME}: отчёт</b>",
             f"Просмотров карточек: {t['views']} (уникальных людей на каждый поход)",
             f"Записалось: {t['signups']} · лист ожидания: {t['waitlist']} · отмен: {t['cancels']}",
             f"Просмотр → запись: {percent(t['signups'] + t['waitlist'], t['views'])}",
             f"Отметились «Я спустился»: {t['checkins']}"]
    if report["sources"]:
        lines.append("\n<b>Откуда приходят</b> (просмотры → записи)")
        for src, v in sorted(report["sources"].items(), key=lambda kv: -kv[1]["views"]):
            lines.append(f"• {e(source_label(src))}: {v['views']} → {v['signups']}")
    if report["hikes"]:
        lines.append("\n<b>По походам</b>")
        for r in report["hikes"]:
            h = r["hike"]
            filled = percent(r["going_now"], h.capacity)
            note = " (отменён)" if not h.is_open else ""
            lines.append(f"• {fmt_short(h)} · {e(h.title)}{note}: просмотров {r['views']}, записей {r['signups']}, "
                         f"заполнено {filled}, спустились {r['checkins']}")
    else:
        lines.append("\nПоходов пока нет.")
    return "\n".join(lines)


def reminder_text(hike: Hike, kind: str, forecast: str = "") -> str:
    head = (f"⏰ <b>Завтра поход «{e(hike.title)}»</b>" if kind == "24h"
            else f"🚶 <b>Через ~3 часа выходим: «{e(hike.title)}»</b>")
    lines = [head, f"🗓 {fmt_when(hike)}", f"📍 Сбор: {e(hike.meeting_point) or 'уточняется'}"]
    if forecast:
        lines.append(forecast)
    if kind == "24h":
        lines.append("🎒 Что взять: кнопка ниже. Не можешь идти? Отмени запись, чтобы место получил следующий.")
    else:
        lines.append("💧 Проверь воду, тёплый слой и заряд телефона. После спуска нажми «Я спустился».")
    return "\n".join(lines)


def safety_alert_text(hike: Hike, missing: list[Signup]) -> str:
    lines = [f"⚠️ <b>После похода «{e(hike.title)}» не отметились:</b>"]
    lines += [f"• {e(s.full_name)} · {e(s.phone)}" for s in missing]
    lines.append("Свяжитесь с ними и убедитесь, что всё в порядке. " + SOS_NOTE)
    return "\n".join(lines)


def when_label(at: datetime) -> str:
    return f"{at.day} {MONTHS[at.month - 1]}"
