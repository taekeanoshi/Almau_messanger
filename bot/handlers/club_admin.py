"""Панель организатора партнёрского клуба (/club): создать поход, список записавшихся,
CSV, рассылка, «кто не отметился», отмена похода, отчёт и ссылки для Instagram.

Доступна только организаторам из CLUB_ADMIN_IDS и админам из ADMIN_IDS.
"""
import asyncio

from aiogram import F, Router
from aiogram.filters import Command, Filter, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import hikes as hk
from bot import keyboards as kb
from bot.config import Config
from bot.db import Database
from bot.handlers.chat import _safe_send
from bot.handlers.flow import PLAIN_TEXT, add_flow_exits
from bot.handlers.hikes import organizer_ids
from bot.hike_format import (SOURCE_LINKS, card_text, chunk, csv_bytes, deep_link, e, fmt_short,
                             report_text, roster_text, safety_alert_text)

router = Router(name="club_admin")

SEND_DELAY = 0.05   # пауза между рассылкой, чтобы не упереться в лимиты Telegram


class IsOrganizer(Filter):
    async def __call__(self, event: Message | CallbackQuery, config: Config) -> bool:
        return event.from_user.id in organizer_ids(config)


router.message.filter(IsOrganizer())
router.callback_query.filter(IsOrganizer())


class NewHike(StatesGroup):
    title = State()
    start = State()
    meeting = State()
    route = State()
    difficulty = State()
    duration = State()
    capacity = State()
    price = State()
    confirm = State()


class Broadcast(StatesGroup):
    text = State()
    confirm = State()


add_flow_exits(router, NewHike, Broadcast)


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _kb(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


def panel_view():
    text = (f"🏔 <b>Панель организатора · {hk.PARTNER_NAME}</b>\n"
            "Походы, записи, рассылки и отчёт. Участники видят походы в боте и на сайте.")
    return text, _kb([_btn("➕ Создать поход", "club:new")], [_btn("📋 Походы и записи", "club:list")],
                     [_btn("📊 Отчёт", "club:report"), _btn("🔗 Ссылки для Instagram", "club:links")])


def _hid(cb: CallbackQuery) -> str:
    return cb.data.split(":", 2)[2]


@router.message(Command("club"))
async def on_club(message: Message, state: FSMContext) -> None:
    await state.clear()
    text, markup = panel_view()
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "club:menu")
async def on_menu(cb: CallbackQuery) -> None:
    text, markup = panel_view()
    await cb.message.edit_text(text, reply_markup=markup)
    await cb.answer()


# ---------- создание похода ----------

@router.callback_query(F.data == "club:new")
async def on_new(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(NewHike.title)
    await cb.message.answer("➕ <b>Новый поход</b>\nКак называется поход? Отмена: /cancel")
    await cb.answer()


@router.message(StateFilter(NewHike.title), PLAIN_TEXT)
async def on_title(message: Message, state: FSMContext) -> None:
    title = " ".join(message.text.split())
    if not 3 <= len(title) <= 80:
        await message.answer("Название от 3 до 80 символов.")
        return
    await state.update_data(title=title)
    await state.set_state(NewHike.start)
    await message.answer("Дата и время начала по Алматы, формат ДД.ММ.ГГГГ ЧЧ:ММ. Например: 24.10.2026 07:00")


@router.message(StateFilter(NewHike.start), PLAIN_TEXT)
async def on_start_time(message: Message, state: FSMContext) -> None:
    start = hk.parse_start(message.text)
    if start is None:
        await message.answer("Не получилось. Формат: ДД.ММ.ГГГГ ЧЧ:ММ, и время должно быть в будущем.")
        return
    await state.update_data(start=start)
    await state.set_state(NewHike.meeting)
    await message.answer("Где и во сколько сбор? Например: «Остановка Медеу, 06:30». Можно «-», чтобы пропустить.")


@router.message(StateFilter(NewHike.meeting), PLAIN_TEXT)
async def on_meeting(message: Message, state: FSMContext) -> None:
    await state.update_data(meeting="" if message.text.strip() == "-" else message.text.strip()[:200])
    await state.set_state(NewHike.route)
    await message.answer("Коротко о маршруте (куда идём, сколько километров). Можно «-».")


@router.message(StateFilter(NewHike.route), PLAIN_TEXT)
async def on_route(message: Message, state: FSMContext) -> None:
    await state.update_data(route="" if message.text.strip() == "-" else message.text.strip()[:300])
    await state.set_state(NewHike.difficulty)
    await message.answer("Сложность?", reply_markup=_kb([_btn(d, f"club:diff:{i}") for i, d in enumerate(hk.DIFFICULTIES)]))


@router.callback_query(StateFilter(NewHike.difficulty), F.data.startswith("club:diff:"))
async def on_difficulty(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(difficulty=hk.DIFFICULTIES[int(cb.data.split(":")[2])])
    await state.set_state(NewHike.duration)
    await cb.message.answer("Сколько часов займёт поход? Например: 6 или 4,5")
    await cb.answer()


@router.message(StateFilter(NewHike.duration), PLAIN_TEXT)
async def on_duration(message: Message, state: FSMContext) -> None:
    hours = hk.parse_duration(message.text)
    if hours is None:
        await message.answer("Число часов от 0,5 до 24. Например: 6")
        return
    await state.update_data(duration=hours)
    await state.set_state(NewHike.capacity)
    await message.answer("Сколько мест?")


@router.message(StateFilter(NewHike.capacity), PLAIN_TEXT)
async def on_capacity(message: Message, state: FSMContext) -> None:
    cap = hk.parse_capacity(message.text)
    if cap is None:
        await message.answer("Целое число от 1 до 500.")
        return
    await state.update_data(capacity=cap)
    await state.set_state(NewHike.price)
    await message.answer("Стоимость? Например: «Бесплатно» или «3000 тг». Можно «-» (будет «Бесплатно»).")


def _draft_hike(data: dict) -> hk.Hike:
    return hk.Hike(id="(новый)", club_id=hk.PARTNER_CLUB_ID, title=data["title"], start_at=data["start"],
                   duration_hours=data["duration"], meeting_point=data["meeting"], route=data["route"],
                   difficulty=data["difficulty"], capacity=data["capacity"], price=data["price"],
                   lat=None, lon=None, gear=(), status="open", demo=False)


@router.message(StateFilter(NewHike.price), PLAIN_TEXT)
async def on_price(message: Message, state: FSMContext) -> None:
    price = "Бесплатно" if message.text.strip() == "-" else message.text.strip()[:60]
    await state.update_data(price=price)
    await state.set_state(NewHike.confirm)
    draft = _draft_hike(await state.get_data())
    await message.answer("Проверь поход:\n\n" + card_text(draft, 0, 0),
                         reply_markup=_kb([_btn("✅ Опубликовать", "club:publish"), _btn("❌ Отмена", "club:discard")]))


@router.callback_query(StateFilter(NewHike.confirm), F.data == "club:publish")
async def on_publish(cb: CallbackQuery, state: FSMContext, db: Database, config: Config) -> None:
    d = await state.get_data()
    await state.clear()
    hike = db.hikes.create(club_id=hk.PARTNER_CLUB_ID, title=d["title"], start_at=d["start"], duration_hours=d["duration"],
                           meeting_point=d["meeting"], route=d["route"], difficulty=d["difficulty"],
                           capacity=d["capacity"], price=d["price"])
    await cb.message.edit_text(f"✅ Поход опубликован: «{e(hike.title)}»\n\nСсылка для записи:\n"
                               f"{deep_link(config.bot_username, hike.id)}\n\nДругие ссылки (Instagram, сайт): /club → «Ссылки».")
    await cb.answer()


@router.callback_query(StateFilter(NewHike.confirm), F.data == "club:discard")
async def on_discard(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.edit_text("Создание похода отменено.")
    await cb.answer()


# ---------- походы и записи ----------

def _hike_buttons(db: Database):
    hikes = db.hikes.for_club(hk.PARTNER_CLUB_ID)
    now = hk.now()
    upcoming = [h for h in hikes if h.end >= now]
    past = [h for h in hikes if h.end < now][-5:]
    rows = []
    for h in upcoming + past:
        going, _ = db.hikes.counts(h.id)
        mark = "❌ " if not h.is_open else ("✔️ " if h.end < now else "")
        rows.append([_btn(f"{mark}{fmt_short(h)} · {h.title} ({going}/{h.capacity})"[:60], f"club:h:{h.id}")])
    rows.append([_btn("⬅️ Панель", "club:menu")])
    return hikes, _kb(*rows)


@router.callback_query(F.data == "club:list")
async def on_list(cb: CallbackQuery, db: Database) -> None:
    hikes, markup = _hike_buttons(db)
    await cb.message.edit_text("📋 <b>Походы</b>" if hikes else "Походов пока нет. Создайте первый: /club → «Создать поход».",
                               reply_markup=markup)
    await cb.answer()


@router.callback_query(F.data.startswith("club:h:"))
async def on_hike(cb: CallbackQuery, db: Database) -> None:
    hike = db.hikes.get(_hid(cb))
    if hike is None:
        await cb.answer("Поход не найден", show_alert=True)
        return
    going, waitlist = db.hikes.counts(hike.id)
    rows = [[_btn("👥 Список", f"club:roster:{hike.id}"), _btn("⬇️ CSV", f"club:csv:{hike.id}")],
            [_btn("📣 Рассылка", f"club:bc:{hike.id}"), _btn("🚦 Не отметились", f"club:late:{hike.id}")]]
    if hike.is_open:
        rows.append([_btn("🗑 Отменить поход", f"club:cancel:{hike.id}")])
    rows.append([_btn("⬅️ Все походы", "club:list")])
    markup = _kb(*rows)
    await cb.message.edit_text(card_text(hike, going, waitlist), reply_markup=markup)
    await cb.answer()


@router.callback_query(F.data.startswith("club:roster:"))
async def on_roster(cb: CallbackQuery, db: Database) -> None:
    hike = db.hikes.get(_hid(cb))
    if hike is None:
        await cb.answer("Поход не найден", show_alert=True)
        return
    for part in chunk(roster_text(hike, db.hikes.signups(hike.id))):
        await cb.message.answer(part)
    await cb.answer()


@router.callback_query(F.data.startswith("club:csv:"))
async def on_csv(cb: CallbackQuery, db: Database) -> None:
    hike = db.hikes.get(_hid(cb))
    if hike is None:
        await cb.answer("Поход не найден", show_alert=True)
        return
    file = BufferedInputFile(csv_bytes(db.hikes.signups(hike.id)), filename=f"{hike.id}.csv")
    await cb.message.answer_document(file, caption=f"Список участников: {e(hike.title)}")
    await cb.answer()


@router.callback_query(F.data.startswith("club:late:"))
async def on_late(cb: CallbackQuery, db: Database) -> None:
    hike = db.hikes.get(_hid(cb))
    if hike is None:
        await cb.answer("Поход не найден", show_alert=True)
        return
    missing = db.hikes.not_checked_in(hike.id)
    if missing:
        await cb.message.answer(safety_alert_text(hike, missing))
    else:
        await cb.message.answer("✅ Все записанные отметились, что спустились (или записавшихся нет).")
    await cb.answer()


# ---------- рассылка ----------

async def send_to_signups(bot, signups, text: str) -> int:
    """Отправляет текст каждому; возвращает, сколько человек получили."""
    ok = 0
    for s in signups:
        ok += await _safe_send(bot, s.user_id, text)
        await asyncio.sleep(SEND_DELAY)
    return ok


@router.callback_query(F.data.startswith("club:bc:"))
async def on_broadcast(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    hike = db.hikes.get(_hid(cb))
    if hike is None:
        await cb.answer("Поход не найден", show_alert=True)
        return
    total = len(db.hikes.signups(hike.id))
    if total == 0:
        await cb.answer("Пока никто не записался", show_alert=True)
        return
    await state.set_state(Broadcast.text)
    await state.update_data(hike_id=hike.id)
    await cb.message.answer(f"📣 Напишите объявление. Его получат все записавшиеся и лист ожидания: {total} чел.\n"
                            "Например: «Выходим на час раньше из-за погоды». Отмена: /cancel")
    await cb.answer()


@router.message(StateFilter(Broadcast.text), PLAIN_TEXT)
async def on_broadcast_text(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    if len(text) > 1000:
        await message.answer("Слишком длинно: до 1000 символов.")
        return
    await state.update_data(text=text)
    await state.set_state(Broadcast.confirm)
    await message.answer(f"Отправить так?\n\n«{e(text)}»",
                         reply_markup=_kb([_btn("✅ Отправить", "club:bcsend"), _btn("❌ Отмена", "club:bcno")]))


@router.callback_query(StateFilter(Broadcast.confirm), F.data == "club:bcsend")
async def on_broadcast_send(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    await state.clear()
    hike = db.hikes.get(data["hike_id"])
    signups = db.hikes.signups(hike.id)
    await cb.message.edit_text("Отправляю…")
    body = f"📣 <b>{hk.PARTNER_NAME}: «{e(hike.title)}»</b>\n\n{e(data['text'])}"
    ok = await send_to_signups(cb.bot, signups, body)
    await cb.message.edit_text(f"✅ Доставлено: {ok} из {len(signups)}."
                               + ("\nНе дошло тем, кто заблокировал бота." if ok < len(signups) else ""))
    await cb.answer()


@router.callback_query(StateFilter(Broadcast.confirm), F.data == "club:bcno")
async def on_broadcast_no(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.edit_text("Рассылка отменена.")
    await cb.answer()


# ---------- отмена похода ----------

@router.callback_query(F.data.startswith("club:cancel:"))
async def on_cancel_ask(cb: CallbackQuery) -> None:
    hike_id = _hid(cb)
    await cb.message.edit_text("Отменить поход? Все записавшиеся получат уведомление.",
                               reply_markup=_kb([_btn("Да, отменить", f"club:cancelyes:{hike_id}"),
                                                 _btn("Нет", f"club:h:{hike_id}")]))
    await cb.answer()


@router.callback_query(F.data.startswith("club:cancelyes:"))
async def on_cancel_yes(cb: CallbackQuery, db: Database) -> None:
    hike = db.hikes.get(_hid(cb))
    if hike is None or not hike.is_open:
        await cb.answer("Поход не найден или уже отменён", show_alert=True)
        return
    signups = db.hikes.signups(hike.id)
    db.hikes.cancel_hike(hike.id)
    ok = await send_to_signups(cb.bot, signups, f"❌ Поход «{e(hike.title)}» ({fmt_short(hike)}) отменён организатором. "
                                                 "Следите за новыми походами: /hikes")
    await cb.message.edit_text(f"Поход отменён. Уведомлено: {ok} из {len(signups)}.")
    await cb.answer()


# ---------- отчёт и ссылки ----------

@router.callback_query(F.data == "club:report")
async def on_report(cb: CallbackQuery, db: Database) -> None:
    await cb.message.answer(report_text(db.hikes.report(hk.PARTNER_CLUB_ID)))
    await cb.answer()


@router.message(Command("club_report"))
async def on_report_command(message: Message, db: Database) -> None:
    await message.answer(report_text(db.hikes.report(hk.PARTNER_CLUB_ID)))


@router.callback_query(F.data == "club:links")
async def on_links(cb: CallbackQuery, db: Database, config: Config) -> None:
    upcoming = db.hikes.upcoming(hk.PARTNER_CLUB_ID)
    if not upcoming:
        await cb.message.answer("Нет предстоящих походов. Сначала создайте поход.")
        await cb.answer()
        return
    lines = ["🔗 <b>Ссылки для записи</b>",
             "Размещайте разные ссылки в разных местах: отчёт покажет, откуда приходят люди.\n"]
    for h in upcoming:
        lines.append(f"<b>{e(h.title)}</b>, {fmt_short(h)}")
        for src, label in SOURCE_LINKS:
            lines.append(f"{label}: <code>{deep_link(config.bot_username, h.id, src)}</code>")
        lines.append("")
    for part in chunk("\n".join(lines)):
        await cb.message.answer(part, disable_web_page_preview=True)
    await cb.answer()
