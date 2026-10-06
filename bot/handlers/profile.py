"""Регистрация (/start) и профиль: вуз, курс, интересы, соцсети."""
import html

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import keyboards as kb
from bot.content import Content
from bot.db import Database
from bot.matchmaking import Matchmaker

router = Router(name="profile")

UNIVERSITIES = ["AlmaU", "KBTU", "КазНУ", "Narxoz", "SDU", "МУИТ", "КазНМУ", "Другой вуз"]
COURSES = ["1 курс", "2 курс", "3 курс", "4 курс", "Магистратура"]
INTERESTS = ["Музыка", "Спорт", "IT", "Бизнес", "Кино", "Игры", "Путешествия", "Искусство", "Книги", "Мемы"]
MAX_SOCIALS_LEN = 200


class Reg(StatesGroup):
    university = State()
    course = State()
    interests = State()
    socials = State()


def _choice_kb(prefix: str, options: list[str], per_row: int = 2):
    b = InlineKeyboardBuilder()
    for i, opt in enumerate(options):
        b.button(text=opt, callback_data=f"{prefix}:{i}")
    b.adjust(per_row)
    return b.as_markup()


def _interests_kb(selected: set[str]):
    b = InlineKeyboardBuilder()
    for i, name in enumerate(INTERESTS):
        b.button(text=("✅ " if name in selected else "") + name, callback_data=f"int:{i}")
    b.button(text="Готово ➡️", callback_data="int:done")
    b.adjust(2)
    return b.as_markup()


def _profile_kb():
    b = InlineKeyboardBuilder()
    b.button(text="✏️ Интересы", callback_data="prof:interests")
    b.button(text="📱 Соцсети для /give", callback_data="prof:socials")
    b.button(text="🔄 Заполнить заново", callback_data="prof:restart")
    b.adjust(2, 1)
    return b.as_markup()


async def begin_registration(message: Message, state: FSMContext) -> None:
    """Первый шаг анкеты. Вызывается и из deep-link ивентов (часть Хамида)."""
    await state.set_state(Reg.university)
    await message.answer(
        "Привет! Это анонимный мессенджер студентов Almau 👋\n"
        "Твоё имя собеседник не увидит. Вуз и курс покажутся, только если ты сам нажмёшь «Раскрыть».\n\n"
        "Где ты учишься?",
        reply_markup=_choice_kb("uni", UNIVERSITIES),
    )


@router.message(CommandStart())
async def on_start(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    if db.get_user(message.from_user.id):
        await message.answer("С возвращением! Выбери действие в меню.", reply_markup=kb.main_menu)
    else:
        await begin_registration(message, state)


@router.callback_query(StateFilter(Reg.university), F.data.startswith("uni:"))
async def on_university(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(university=UNIVERSITIES[int(cb.data.split(":")[1])])
    await state.set_state(Reg.course)
    await cb.message.edit_text("На каком ты курсе?", reply_markup=_choice_kb("course", COURSES))
    await cb.answer()


@router.callback_query(StateFilter(Reg.course), F.data.startswith("course:"))
async def on_course(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(course=COURSES[int(cb.data.split(":")[1])], interests=[])
    await state.set_state(Reg.interests)
    await cb.message.edit_text(
        "Выбери интересы — по ним подберём собеседника с общими темами. Можно несколько.",
        reply_markup=_interests_kb(set()),
    )
    await cb.answer()


@router.callback_query(StateFilter(Reg.interests), F.data.startswith("int:"))
async def on_interest(cb: CallbackQuery, state: FSMContext, db: Database, content: Content) -> None:
    data = await state.get_data()
    selected = set(data.get("interests", []))
    choice = cb.data.split(":")[1]

    if choice != "done":
        selected ^= {INTERESTS[int(choice)]}  # повторное нажатие снимает галочку
        await state.update_data(interests=sorted(selected))
        await cb.message.edit_reply_markup(reply_markup=_interests_kb(selected))
        await cb.answer()
        return

    uid = cb.from_user.id
    if data.get("editing_interests"):
        db.set_interests(uid, selected)
    else:
        db.save_profile(uid, data["university"], data["course"], selected)
    pending_event = data.get("pending_event")
    await state.clear()
    await cb.message.edit_text("Профиль сохранён ✅")
    await cb.message.answer("Готово! Жми «🔍 Найти собеседника».", reply_markup=kb.main_menu)
    await cb.answer()

    if pending_event:  # пользователь пришёл по ссылке «Найти компанию» с сайта
        from bot.handlers.events import send_event_card  # локальный импорт: events импортирует этот модуль
        await send_event_card(cb.message, db, content, pending_event, uid)


@router.message(Command("profile"))
@router.message(F.text == kb.BTN_PROFILE)
async def on_profile(message: Message, db: Database) -> None:
    user = db.get_user(message.from_user.id)
    if user is None:
        await message.answer("Профиля пока нет: /start")
        return
    interests = ", ".join(sorted(user.interests)) or "не выбраны"
    socials = html.escape(user.socials) if user.socials else "не указаны"
    await message.answer(
        f"👤 <b>Твой профиль</b>\n"
        f"Вуз: {html.escape(user.university)}\nКурс: {html.escape(user.course)}\n"
        f"Интересы: {interests}\nСоцсети для /give: {socials}",
        reply_markup=_profile_kb(),
    )


@router.callback_query(F.data == "prof:interests")
async def on_edit_interests(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    user = db.get_user(cb.from_user.id)
    if user is None:
        await cb.answer("Сначала /start", show_alert=True)
        return
    await state.set_state(Reg.interests)
    await state.update_data(interests=sorted(user.interests), editing_interests=True)
    await cb.message.answer("Отметь интересы:", reply_markup=_interests_kb(set(user.interests)))
    await cb.answer()


@router.callback_query(F.data == "prof:socials")
async def on_edit_socials(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Reg.socials)
    await cb.message.answer(
        "Отправь одним сообщением соцсети, которые увидит собеседник после /give.\n"
        "Например: inst @almau_student, tg @nickname\nОтправь «-», чтобы удалить."
    )
    await cb.answer()


@router.message(StateFilter(Reg.socials), F.text)
async def on_socials(message: Message, state: FSMContext, db: Database) -> None:
    text = message.text.strip()
    if len(text) > MAX_SOCIALS_LEN:
        await message.answer(f"Слишком длинно, максимум {MAX_SOCIALS_LEN} символов. Попробуй ещё раз.")
        return
    db.set_socials(message.from_user.id, "" if text == "-" else text)
    await state.clear()
    await message.answer("Соцсети сохранены ✅", reply_markup=kb.main_menu)


@router.callback_query(F.data == "prof:restart")
async def on_restart(cb: CallbackQuery, state: FSMContext, mm: Matchmaker) -> None:
    if mm.partner_of(cb.from_user.id) is not None:
        await cb.answer("Сначала заверши диалог", show_alert=True)
        return
    await state.clear()
    await begin_registration(cb.message, state)
    await cb.answer()
