"""Анонимный чат: поиск собеседника, пересылка сообщений, «Следующий», «Стоп», раскрытие и /give."""
import asyncio
import html
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import Message

from bot import keyboards as kb
from bot.db import Database
from bot.matchmaking import Matchmaker

router = Router(name="chat")


async def _safe_send(bot: Bot, chat_id: int, text: str, **kwargs) -> bool:
    """Отправка, которая не падает, если собеседник заблокировал бота."""
    try:
        await bot.send_message(chat_id, text, **kwargs)
        return True
    except (TelegramForbiddenError, TelegramBadRequest):
        return False


async def end_dialog(bot: Bot, mm: Matchmaker, user_id: int, partner_text: str) -> int | None:
    """Завершает диалог пользователя и предупреждает собеседника. Используется и модерацией."""
    partner = mm.end_dialog(user_id)
    if partner is not None:
        await _safe_send(bot, partner, partner_text, reply_markup=kb.main_menu)
    return partner


async def announce_pair(bot: Bot, db: Database, mm: Matchmaker, a: int, b: int,
                        event_id: str | None = None, event_title: str | None = None) -> bool:
    """Сообщает обоим, что собеседник найден. Если кто-то недоступен — пара разрывается."""
    db.log_dialog(a, b, event_id)
    ua, ub = db.get_user(a), db.get_user(b)
    common = sorted(ua.interests & ub.interests) if ua and ub else []
    intro = "✅ Собеседник найден! Пиши — твоё имя он не видит."
    if event_title:
        intro += f"\nВы оба идёте на «{html.escape(event_title)}» 🎉"
    if common:
        intro += "\nОбщие интересы: " + ", ".join(common)
    for me in (a, b):
        if not await _safe_send(bot, me, intro, reply_markup=kb.in_dialog):
            await end_dialog(bot, mm, me, "Собеседник недоступен. Нажми «🔍 Найти собеседника» ещё раз.")
            return False
    return True


async def start_search(message: Message, db: Database, mm: Matchmaker,
                       user_id: int, event_id: str | None = None, event_title: str | None = None) -> None:
    """Ставит пользователя в очередь. Для ивентов (часть Хамида) передаётся event_id."""
    user = db.get_user(user_id)
    if user is None:
        await message.answer("Сначала заполни короткий профиль: /start")
        return
    if mm.partner_of(user_id) is not None:
        await message.answer("Ты уже в диалоге. Нажми «⛔ Стоп» или «⏭ Следующий».", reply_markup=kb.in_dialog)
        return

    partner_id = mm.enqueue(user_id, user.interests, event_id)
    if partner_id is None:
        where = f" среди тех, кто идёт на «{html.escape(event_title)}»" if event_title else ""
        hint = "\nСначала ищем человека с общими интересами." if user.interests and not event_id else ""
        await message.answer(f"🔎 Ищем собеседника{where}…{hint}", reply_markup=kb.searching)
        return
    await announce_pair(message.bot, db, mm, user_id, partner_id, event_id, event_title)


async def matchmaking_loop(bot: Bot, db: Database, mm: Matchmaker, content, interval: float = 5.0) -> None:
    """Фоновая задача: соединяет тех, кто не дождался собеседника с общими интересами."""
    while True:
        await asyncio.sleep(interval)
        try:
            for a, b, event_id in mm.pair_stale():
                ev = content.event(event_id) if event_id else None
                await announce_pair(bot, db, mm, a, b, event_id, ev.title if ev else None)
        except Exception:  # ошибка одной пары не должна останавливать подбор для всех
            logging.exception("Ошибка в фоновом подборе собеседников")


@router.message(Command("search"))
@router.message(F.text == kb.BTN_SEARCH)
async def on_search(message: Message, db: Database, mm: Matchmaker) -> None:
    await start_search(message, db, mm, message.from_user.id)


@router.message(F.text == kb.BTN_CANCEL)
async def on_cancel(message: Message, mm: Matchmaker) -> None:
    mm.leave_queue(message.from_user.id)
    await message.answer("Поиск отменён.", reply_markup=kb.main_menu)


@router.message(Command("next"))
@router.message(F.text == kb.BTN_NEXT)
async def on_next(message: Message, db: Database, mm: Matchmaker) -> None:
    await end_dialog(message.bot, mm, message.from_user.id, "Собеседник ушёл к следующему. Найдём нового?")
    await start_search(message, db, mm, message.from_user.id)


@router.message(Command("stop"))
@router.message(F.text == kb.BTN_STOP)
async def on_stop(message: Message, mm: Matchmaker) -> None:
    uid = message.from_user.id
    if await end_dialog(message.bot, mm, uid, "Собеседник завершил диалог.") is None:
        mm.leave_queue(uid)
    await message.answer("Диалог завершён.", reply_markup=kb.main_menu)


@router.message(F.text == kb.BTN_REVEAL)
async def on_reveal(message: Message, db: Database, mm: Matchmaker) -> None:
    partner = mm.partner_of(message.from_user.id)
    user = db.get_user(message.from_user.id)
    if partner is None or user is None:
        await message.answer("Ты сейчас не в диалоге.", reply_markup=kb.main_menu)
        return
    await _safe_send(message.bot, partner,
                     f"🎓 Собеседник раскрылся: {html.escape(user.university)}, {html.escape(user.course)}")
    await message.answer("Собеседник увидел твой вуз и курс.")


@router.message(Command("give"))
async def on_give(message: Message, db: Database, mm: Matchmaker) -> None:
    partner = mm.partner_of(message.from_user.id)
    user = db.get_user(message.from_user.id)
    if partner is None or user is None:
        await message.answer("Команда работает только в диалоге.")
        return
    if not user.socials:
        await message.answer("Соцсети не указаны. Добавь их в «👤 Профиль», потом снова отправь /give.")
        return
    await _safe_send(message.bot, partner, "📱 Собеседник поделился контактами:\n" + user.socials,
                     parse_mode=None)
    await message.answer("Контакты отправлены собеседнику.")


@router.message()
async def relay(message: Message, mm: Matchmaker) -> None:
    """Любое другое сообщение в диалоге копируется собеседнику без имени отправителя."""
    partner = mm.partner_of(message.from_user.id)
    if partner is None:
        await message.answer("Нажми «🔍 Найти собеседника», чтобы начать.", reply_markup=kb.main_menu)
        return
    try:
        await message.copy_to(partner)
    except TelegramForbiddenError:
        mm.end_dialog(message.from_user.id)
        await message.answer("Собеседник покинул бота. Диалог завершён.", reply_markup=kb.main_menu)
    except TelegramBadRequest:
        await message.answer("Этот тип сообщения нельзя переслать анонимно.")
