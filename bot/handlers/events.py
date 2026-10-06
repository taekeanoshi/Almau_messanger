"""Клубы, афиша ивентов и новая функция «Найти компанию на ивент».

Студент отмечает «Иду» на ивенте и может анонимно пообщаться с другим студентом,
который идёт туда же, — чтобы не идти одному. Ссылка с сайта ведёт сразу на карточку ивента:
https://t.me/<бот>?start=event_<id>
"""
import html

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import keyboards as kb
from bot.content import Content
from bot.db import Database
from bot.handlers.chat import start_search
from bot.handlers.profile import begin_registration
from bot.matchmaking import Matchmaker

router = Router(name="events")

DEEP_LINK_PREFIX = "event_"


def _event_kb(event_id: str, going: bool):
    b = InlineKeyboardBuilder()
    b.button(text="↩️ Не пойду" if going else "✅ Иду", callback_data=f"go:{event_id}")
    b.button(text="👥 Найти компанию", callback_data=f"buddy:{event_id}")
    b.button(text="⬅️ Все ивенты", callback_data="events:list")
    b.adjust(2, 1)
    return b.as_markup()


def _event_text(content: Content, db: Database, event_id: str) -> str | None:
    ev = content.event(event_id)
    if ev is None:
        return None
    going = db.going_count(event_id)
    return (f"{ev.emoji} <b>{html.escape(ev.title)}</b>\n"
            f"🗓 {html.escape(ev.date)} · {html.escape(ev.host)}\n\n"
            f"{html.escape(ev.description)}\n\n"
            f"Идут через бота: {going}")


async def send_event_card(message: Message, db: Database, content: Content,
                          event_id: str, user_id: int) -> None:
    text = _event_text(content, db, event_id)
    if text is None:
        await message.answer("Такой ивент не найден. Посмотри афишу: /events")
        return
    await message.answer(text, reply_markup=_event_kb(event_id, db.is_going(user_id, event_id)))


def _events_list_kb(content: Content):
    b = InlineKeyboardBuilder()
    for ev in content.events:
        b.button(text=f"{ev.emoji} {ev.title}", callback_data=f"ev:{ev.id}")
    b.adjust(1)
    return b.as_markup()


@router.message(CommandStart(deep_link=True, magic=F.args.startswith(DEEP_LINK_PREFIX)))
async def on_event_deep_link(message: Message, command: CommandObject, state: FSMContext,
                             db: Database, content: Content) -> None:
    event_id = command.args.removeprefix(DEEP_LINK_PREFIX)
    await state.clear()
    if db.get_user(message.from_user.id) is None:
        await begin_registration(message, state)
        await state.update_data(pending_event=event_id)  # карточку покажет profile.py после анкеты
        return
    await message.answer("Открываю ивент 👇", reply_markup=kb.main_menu)
    await send_event_card(message, db, content, event_id, message.from_user.id)


@router.message(Command("events"))
@router.message(F.text == kb.BTN_EVENTS)
async def on_events(message: Message, content: Content) -> None:
    await message.answer("🎉 <b>Афиша AlmaU</b>\nВыбери ивент — можно отметиться и найти компанию:",
                         reply_markup=_events_list_kb(content))


@router.callback_query(F.data == "events:list")
async def on_events_list(cb: CallbackQuery, content: Content) -> None:
    await cb.message.edit_text("🎉 <b>Афиша AlmaU</b>\nВыбери ивент:", reply_markup=_events_list_kb(content))
    await cb.answer()


@router.callback_query(F.data.startswith("ev:"))
async def on_event(cb: CallbackQuery, db: Database, content: Content) -> None:
    event_id = cb.data.split(":", 1)[1]
    text = _event_text(content, db, event_id)
    if text is None:
        await cb.answer("Ивент не найден", show_alert=True)
        return
    await cb.message.edit_text(text, reply_markup=_event_kb(event_id, db.is_going(cb.from_user.id, event_id)))
    await cb.answer()


@router.callback_query(F.data.startswith("go:"))
async def on_toggle_going(cb: CallbackQuery, db: Database, content: Content) -> None:
    event_id = cb.data.split(":", 1)[1]
    if content.event(event_id) is None:
        await cb.answer("Ивент не найден", show_alert=True)
        return
    if db.get_user(cb.from_user.id) is None:
        await cb.answer("Сначала заполни профиль: /start", show_alert=True)
        return
    going = not db.is_going(cb.from_user.id, event_id)
    db.set_going(cb.from_user.id, event_id, going)
    await cb.message.edit_text(_event_text(content, db, event_id), reply_markup=_event_kb(event_id, going))
    await cb.answer("Отметили, что идёшь ✅" if going else "Отметка снята")


@router.callback_query(F.data.startswith("buddy:"))
async def on_find_buddy(cb: CallbackQuery, db: Database, mm: Matchmaker, content: Content) -> None:
    event_id = cb.data.split(":", 1)[1]
    ev = content.event(event_id)
    if ev is None:
        await cb.answer("Ивент не найден", show_alert=True)
        return
    if db.get_user(cb.from_user.id) is not None:
        db.set_going(cb.from_user.id, event_id, True)  # ищешь компанию — значит идёшь
    await cb.answer()
    await start_search(cb.message, db, mm, cb.from_user.id, event_id=ev.id, event_title=ev.title)


@router.message(Command("clubs"))
@router.message(F.text == kb.BTN_CLUBS)
async def on_clubs(message: Message, content: Content) -> None:
    lines = ["🏛 <b>Студенческие клубы AlmaU</b>\n"]
    for c in content.clubs:
        link = f' — <a href="https://instagram.com/{c.instagram}">@{c.instagram}</a>' if c.instagram else ""
        lines.append(f"{c.emoji} <b>{html.escape(c.name)}</b> · {html.escape(c.category)}{link}\n"
                     f"{html.escape(c.description)}\n")
    await message.answer("\n".join(lines), disable_web_page_preview=True)
