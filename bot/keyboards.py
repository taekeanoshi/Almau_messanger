"""Нижние (reply) клавиатуры бота и тексты их кнопок."""
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

BTN_SEARCH = "🔍 Найти собеседника"
BTN_EVENTS = "🎉 Ивенты"
BTN_CLUBS = "🏛 Клубы"
BTN_PROFILE = "👤 Профиль"

BTN_CANCEL = "❌ Отменить поиск"

BTN_NEXT = "⏭ Следующий"
BTN_STOP = "⛔ Стоп"
BTN_REVEAL = "🎓 Раскрыть вуз и курс"
BTN_REPORT = "🚩 Пожаловаться"


def _kb(rows: list[list[str]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t) for t in row] for row in rows],
        resize_keyboard=True,
    )


main_menu = _kb([[BTN_SEARCH], [BTN_EVENTS, BTN_CLUBS], [BTN_PROFILE]])
searching = _kb([[BTN_CANCEL]])
in_dialog = _kb([[BTN_NEXT, BTN_STOP], [BTN_REVEAL], [BTN_REPORT]])
