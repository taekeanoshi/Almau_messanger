"""Походы партнёра (Summit Seekers) для студентов: афиша, запись, лист ожидания, погода,
что взять, «Я спустился», SOS и школа туризма.

Запись на поход отделена от анонимного чата. Имя и телефон студент вводит сам, отдельным
шагом, и их видит только организатор клуба. Это нужно для связи и безопасности в горах.
"""
import html
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import hikes as hk
from bot import keyboards as kb
from bot import weather
from bot.config import Config
from bot.content import Content
from bot.db import Database
from bot.handlers.chat import _safe_send
from bot.handlers.flow import PLAIN_TEXT, add_flow_exits
from bot.hike_format import PARTNER_LINE, SOS_NOTE, card_text, e, fmt_short, fmt_when
from bot.matchmaking import Matchmaker

router = Router(name="hikes")


class HikeReg(StatesGroup):
    name = State()
    phone = State()


class Sos(StatesGroup):
    location = State()


add_flow_exits(router, HikeReg, Sos)


def organizer_ids(config: Config) -> frozenset[int]:
    """Кому приходят записи, SOS и тревоги: организаторы клуба и админы Almau Messenger."""
    return config.club_admin_ids | config.admin_ids


async def notify_organizers(bot: Bot, config: Config, text: str, **kwargs) -> int:
    sent = 0
    for oid in organizer_ids(config):
        sent += await _safe_send(bot, oid, text, **kwargs)
    return sent


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _hike_id(cb: CallbackQuery) -> str:
    return cb.data.split(":", 2)[2]


def card_for(db: Database, hike: hk.Hike, user_id: int, at: datetime | None = None):
    """Текст и кнопки карточки похода для конкретного пользователя."""
    at = at or hk.now()
    going, waitlist = db.hikes.counts(hike.id)
    mine = db.hikes.signup_of(hike.id, user_id)
    pos = db.hikes.waitlist_position(hike.id, user_id) if mine else None
    text = card_text(hike, going, waitlist, mine, pos)

    rows = []
    if hike.is_open and hike.end >= at:
        rows.append([_btn("❌ Отменить запись", f"hike:leave:{hike.id}") if mine
                     else _btn("✅ Записаться", f"hike:join:{hike.id}")])
    rows.append([_btn("🌤 Погода", f"hike:wx:{hike.id}"), _btn("🎒 Что взять", f"hike:gear:{hike.id}")])
    if mine and mine.status == "going" and hike.is_active(at):
        safety = []
        if not mine.checked_in:
            safety.append(_btn("🟢 Я спустился", f"hike:down:{hike.id}"))
        safety.append(_btn("🆘 SOS", f"hike:sos:{hike.id}"))
        rows.append(safety)
    rows.append([_btn("⬅️ Все походы", "hike:list")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def list_view(db: Database):
    upcoming = db.hikes.upcoming(hk.PARTNER_CLUB_ID)
    lines = [f"🏔 <b>Походы {hk.PARTNER_NAME}</b>", PARTNER_LINE, ""]
    rows = []
    if upcoming:
        lines.append("Выбери поход, чтобы увидеть детали и записаться:")
        for h in upcoming:
            going, _ = db.hikes.counts(h.id)
            full = " · мест нет" if going >= h.capacity else ""
            rows.append([_btn(f"{fmt_short(h)} · {h.title}{full}"[:60], f"hike:open:{h.id}")])
    else:
        lines.append("Ближайшие походы скоро появятся. Загляни позже.")
    rows.append([_btn("🎓 Школа туризма", "school:list")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def send_card(message: Message, db: Database, hike: hk.Hike, user_id: int) -> None:
    text, markup = card_for(db, hike, user_id)
    await message.answer(text, reply_markup=markup)


@router.message(CommandStart(deep_link=True, magic=F.args.startswith("hike_")))
async def on_deep_link(message: Message, command: CommandObject, state: FSMContext, db: Database) -> None:
    """Ссылка вида t.me/бот?start=hike_<id>__<источник>: с сайта, Instagram и т.д."""
    await state.clear()
    hike_id, src = hk.parse_deep_link(command.args)
    hike = db.hikes.get(hike_id)
    if hike is None:
        await message.answer("Такой поход не найден. Посмотри афишу: /hikes", reply_markup=kb.main_menu)
        return
    db.hikes.log_view(hike.id, message.from_user.id, src)
    await message.answer("Открываю поход 👇", reply_markup=kb.main_menu)
    await send_card(message, db, hike, message.from_user.id)


@router.message(Command("hikes"))
@router.message(F.text == kb.BTN_HIKES)
async def on_hikes(message: Message, db: Database) -> None:
    text, markup = list_view(db)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "hike:list")
async def on_list(cb: CallbackQuery, db: Database) -> None:
    text, markup = list_view(db)
    await cb.message.edit_text(text, reply_markup=markup)
    await cb.answer()


@router.callback_query(F.data.startswith("hike:open:"))
async def on_open(cb: CallbackQuery, db: Database) -> None:
    hike = db.hikes.get(_hike_id(cb))
    if hike is None:
        await cb.answer("Поход не найден", show_alert=True)
        return
    db.hikes.log_view(hike.id, cb.from_user.id, db.hikes.last_view_src(hike.id, cb.from_user.id))
    text, markup = card_for(db, hike, cb.from_user.id)
    await cb.message.edit_text(text, reply_markup=markup)
    await cb.answer()


# ---------- запись ----------

@router.callback_query(F.data.startswith("hike:join:"))
async def on_join(cb: CallbackQuery, state: FSMContext, db: Database, mm: Matchmaker) -> None:
    uid = cb.from_user.id
    hike = db.hikes.get(_hike_id(cb))
    if hike is None or not hike.is_open or hike.end < hk.now():
        await cb.answer("Запись на этот поход закрыта", show_alert=True)
        return
    if mm.partner_of(uid) is not None or mm.is_waiting(uid):
        await cb.answer("Сначала заверши анонимный диалог или поиск (⛔ Стоп), потом запишись.", show_alert=True)
        return
    if db.hikes.signup_of(hike.id, uid):
        await cb.answer("Ты уже записан(а) на этот поход")
        return
    await state.set_state(HikeReg.name)
    await state.update_data(hike_id=hike.id)
    await cb.message.answer(
        f"Запись на «{e(hike.title)}».\n\n"
        f"Для записи нужны имя и телефон. Их увидит только организатор {hk.PARTNER_NAME} (и админы Almau Messenger): "
        "для связи в день похода и безопасности в горах. Анонимный чат это не затрагивает. "
        "Через 30 дней после похода данные удаляются.\n\n"
        "Как тебя зовут? Чтобы отменить, отправь /cancel."
    )
    await cb.answer()


@router.message(StateFilter(HikeReg.name), PLAIN_TEXT)
async def on_name(message: Message, state: FSMContext) -> None:
    name = " ".join(message.text.split())
    if not 2 <= len(name) <= 60:
        await message.answer("Напиши имя от 2 до 60 символов.")
        return
    await state.update_data(full_name=name)
    await state.set_state(HikeReg.phone)
    await message.answer("Теперь телефон для связи в день похода. Нажми кнопку или напиши номер, например +77071234567.",
                         reply_markup=kb.phone_request)


@router.message(StateFilter(HikeReg.phone), F.contact | PLAIN_TEXT)
async def on_phone(message: Message, state: FSMContext, db: Database, config: Config) -> None:
    raw = message.contact.phone_number if message.contact else message.text
    phone = hk.normalize_phone(raw)
    if phone is None:
        await message.answer("Не похоже на номер. Напиши, например, +77071234567 или нажми кнопку.")
        return
    data = await state.get_data()
    await state.clear()
    uid, hike_id = message.from_user.id, data["hike_id"]
    src = db.hikes.last_view_src(hike_id, uid)
    result = db.hikes.signup(hike_id, uid, data["full_name"], phone, src)
    hike = db.hikes.get(hike_id)

    if result == "going":
        await message.answer(f"✅ Ты записан(а) на «{e(hike.title)}»! Напомним за сутки и за 3 часа до выхода. "
                             "Не сможешь пойти: отмени запись в карточке, место получит следующий.",
                             reply_markup=kb.main_menu)
        going, _ = db.hikes.counts(hike_id)
        await notify_organizers(message.bot, config, f"🆕 Новая запись на «{e(hike.title)}»: {e(data['full_name'])} · "
                                f"{e(phone)} ({going}/{hike.capacity})", disable_notification=True)
    elif result == "waitlist":
        pos = db.hikes.waitlist_position(hike_id, uid)
        await message.answer(f"⏳ Мест нет, ты в листе ожидания под номером {pos}. Если кто-то отменит, мы сразу напишем.",
                             reply_markup=kb.main_menu)
    elif result == "exists":
        await message.answer("Ты уже записан(а) на этот поход.", reply_markup=kb.main_menu)
    else:
        await message.answer("Запись на этот поход закрыта.", reply_markup=kb.main_menu)
    if result in ("going", "waitlist", "exists"):
        await send_card(message, db, hike, uid)


@router.callback_query(F.data.startswith("hike:leave:"))
async def on_leave(cb: CallbackQuery) -> None:
    hike_id = _hike_id(cb)
    markup = InlineKeyboardMarkup(inline_keyboard=[[_btn("Да, отменить", f"hike:leaveyes:{hike_id}"),
                                                    _btn("Нет, остаюсь", f"hike:open:{hike_id}")]])
    await cb.message.edit_text("Отменить запись? Твои имя и телефон будут удалены.", reply_markup=markup)
    await cb.answer()


@router.callback_query(F.data.startswith("hike:leaveyes:"))
async def on_leave_yes(cb: CallbackQuery, db: Database) -> None:
    hike = db.hikes.get(_hike_id(cb))
    if hike is None:
        await cb.answer("Поход не найден", show_alert=True)
        return
    existed, promoted = db.hikes.cancel_signup(hike.id, cb.from_user.id)
    text, markup = card_for(db, hike, cb.from_user.id)
    await cb.message.edit_text(text, reply_markup=markup)
    await cb.answer("Запись отменена" if existed else "Записи уже не было")
    if promoted is not None:
        await _safe_send(cb.bot, promoted.user_id,
                         f"🎉 Освободилось место! Ты едешь на «{e(hike.title)}», {fmt_when(hike)}.",
                         reply_markup=card_for(db, hike, promoted.user_id)[1])


# ---------- погода, снаряжение ----------

@router.callback_query(F.data.startswith("hike:wx:"))
async def on_weather(cb: CallbackQuery, db: Database) -> None:
    hike = db.hikes.get(_hike_id(cb))
    if hike is None:
        await cb.answer("Поход не найден", show_alert=True)
        return
    await cb.answer("Смотрю прогноз…")
    try:
        text = weather.forecast_text(await weather.get_forecast(hike.lat, hike.lon, hike.start.date(), hk.now().date()))
    except weather.ForecastError as exc:
        text = f"🌤 {exc}"
    await cb.message.answer(text)


@router.callback_query(F.data.startswith("hike:gear:"))
async def on_gear(cb: CallbackQuery, db: Database, content: Content) -> None:
    hike = db.hikes.get(_hike_id(cb))
    if hike is None:
        await cb.answer("Поход не найден", show_alert=True)
        return
    gear = list(hike.gear) or content.school.get("gear", [])
    lines = ["🎒 <b>Что взять с собой</b>"] + [f"☐ {e(g)}" for g in gear]
    safety = content.school.get("safety", [])
    if safety:
        lines += ["", "🛟 <b>Безопасность</b>"] + [f"• {e(s)}" for s in safety]
    await cb.message.answer("\n".join(lines))
    await cb.answer()


# ---------- безопасность ----------

@router.callback_query(F.data.startswith("hike:down:"))
async def on_down(cb: CallbackQuery, db: Database, config: Config) -> None:
    hike = db.hikes.get(_hike_id(cb))
    uid = cb.from_user.id
    mine = db.hikes.signup_of(hike.id, uid) if hike else None
    if hike is None or mine is None or mine.status != "going":
        await cb.answer("Эта отметка доступна участникам похода", show_alert=True)
        return
    if db.hikes.check_in(hike.id, uid):
        await cb.answer("Отметили, ты спустился(ась) 🏁")
        if not db.hikes.not_checked_in(hike.id):
            await notify_organizers(cb.bot, config, f"✅ Все участники «{e(hike.title)}» отметились, что спустились.")
    else:
        await cb.answer("Ты уже отмечен(а)")
    text, markup = card_for(db, hike, uid)
    await cb.message.edit_text(text, reply_markup=markup)


async def begin_sos(bot: Bot, user_id: int, state: FSMContext, db: Database, config: Config,
                    hike_id: str | None = None) -> None:
    pairs = db.hikes.active_for_user(user_id, hk.now())
    if hike_id:
        pairs = [p for p in pairs if p[0].id == hike_id]
    if not pairs:
        await bot.send_message(user_id, "SOS доступен участникам похода, пока он идёт. " + SOS_NOTE)
        return
    hike, mine = pairs[0]
    delivered = await notify_organizers(
        bot, config, f"🆘 <b>SOS</b> по походу «{e(hike.title)}»\n{e(mine.full_name)} · {e(mine.phone)}\n"
                     "Участник просит помощи. Свяжитесь с ним сейчас. Геолокацию он пришлёт следующим сообщением.")
    await state.set_state(Sos.location)
    await state.update_data(hike_id=hike.id)
    note = ("Я передал сигнал организаторам." if delivered
            else "Организаторы не подключены к боту, свяжись с ними напрямую.")
    await bot.send_message(user_id, f"🆘 {note}\nОтправь геолокацию кнопкой ниже, чтобы тебя нашли. {SOS_NOTE}",
                           reply_markup=kb.location_request)


@router.callback_query(F.data.startswith("hike:sos:"))
async def on_sos_button(cb: CallbackQuery, state: FSMContext, db: Database, config: Config) -> None:
    await cb.answer()
    await begin_sos(cb.bot, cb.from_user.id, state, db, config, _hike_id(cb))


@router.message(Command("sos"))
async def on_sos_command(message: Message, state: FSMContext, db: Database, config: Config) -> None:
    await begin_sos(message.bot, message.from_user.id, state, db, config)


@router.message(StateFilter(Sos.location), F.location)
async def on_sos_location(message: Message, state: FSMContext, db: Database, config: Config) -> None:
    data = await state.get_data()
    await state.clear()
    hike = db.hikes.get(data.get("hike_id", ""))
    mine = db.hikes.signup_of(hike.id, message.from_user.id) if hike else None
    who = f"{e(mine.full_name)} · {e(mine.phone)}" if mine else str(message.from_user.id)
    for oid in organizer_ids(config):
        try:
            await message.bot.send_location(oid, message.location.latitude, message.location.longitude)
        except Exception:
            continue
        await _safe_send(message.bot, oid, f"📍 Геолокация участника {who}")
    await message.answer(f"Геолокацию отправил организаторам. {SOS_NOTE}", reply_markup=kb.main_menu)


@router.message(StateFilter(Sos.location), PLAIN_TEXT)
async def on_sos_other(message: Message) -> None:
    await message.answer("Нажми кнопку «Отправить геолокацию» или /cancel. " + SOS_NOTE)


# ---------- школа туризма ----------

def _school_list(content: Content):
    lessons = content.school.get("lessons", [])
    rows = [[_btn(f"{les['emoji']} {les['title']}", f"school:{les['id']}")] for les in lessons]
    text = f"🎓 <b>Школа туризма</b>\n{PARTNER_LINE}\n\n" + ("Короткие уроки для тех, кто идёт в горы:" if lessons
                                                             else "Уроки скоро появятся.")
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("school"))
async def on_school(message: Message, content: Content) -> None:
    text, markup = _school_list(content)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "school:list")
async def on_school_list(cb: CallbackQuery, content: Content) -> None:
    text, markup = _school_list(content)
    await cb.message.answer(text, reply_markup=markup)
    await cb.answer()


@router.callback_query(F.data.startswith("school:"))
async def on_lesson(cb: CallbackQuery, content: Content) -> None:
    lesson_id = cb.data.split(":", 1)[1]
    lesson = next((x for x in content.school.get("lessons", []) if x["id"] == lesson_id), None)
    if lesson is None:
        await cb.answer("Урок не найден", show_alert=True)
        return
    body = "\n".join(f"• {html.escape(p)}" for p in lesson["points"])
    markup = InlineKeyboardMarkup(inline_keyboard=[[_btn("⬅️ Все уроки", "school:list")]])
    await cb.message.answer(f"{lesson['emoji']} <b>{html.escape(lesson['title'])}</b>\n\n{body}", reply_markup=markup)
    await cb.answer()
