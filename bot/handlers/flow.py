"""Общее для пошаговых форм (запись на поход, создание похода, рассылка, SOS)."""
from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, StateFilter, or_f
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot import keyboards as kb


# Обычный текст: не команда и не кнопка нижнего меню. Поля форм принимают только его, иначе после
# выхода из формы текст команды ещё раз попал бы в поле (aiogram проверяет состояние один раз на апдейт).
PLAIN_TEXT = F.text & ~F.text.startswith("/") & ~F.text.in_(kb.MENU_TEXTS)


def add_flow_exits(router: Router, *groups) -> None:
    """Подключает выходы из форм этого роутера.

    - /cancel и кнопка «Отмена» отменяют форму.
    - Любая другая команда или кнопка нижнего меню тоже прерывает форму и передаётся дальше,
      чтобы сработала как обычно. Иначе текст следующей команды попал бы в поле формы.
    Вызывать до объявления обработчиков самих форм.
    """
    @router.message(StateFilter(*groups), or_f(Command("cancel"), F.text == kb.BTN_FORM_CANCEL))
    async def _cancel(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("Отменено.", reply_markup=kb.main_menu)

    @router.message(StateFilter(*groups), F.text.startswith("/") | F.text.in_(kb.MENU_TEXTS))
    async def _interrupt(message: Message, state: FSMContext) -> None:
        await state.clear()
        raise SkipHandler
