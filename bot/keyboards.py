"""Нижние (reply) клавиатуры бота и тексты их кнопок."""
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

BTN_SEARCH = "🔍 Найти собеседника"
BTN_EVENTS = "🎉 Ивенты"
BTN_CLUBS = "🏛 Клубы"
BTN_PROFILE = "👤 Профиль"
BTN_HIKES = "🏔 Походы"

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


main_menu = _kb([[BTN_SEARCH], [BTN_EVENTS, BTN_CLUBS], [BTN_HIKES, BTN_PROFILE]])
searching = _kb([[BTN_CANCEL]])
in_dialog = _kb([[BTN_NEXT, BTN_STOP], [BTN_REVEAL], [BTN_REPORT]])

# Тексты кнопок: если человек нажал одну из них посреди пошаговой формы, форма прерывается
MENU_TEXTS = {BTN_SEARCH, BTN_EVENTS, BTN_CLUBS, BTN_PROFILE, BTN_HIKES, BTN_CANCEL,
              BTN_NEXT, BTN_STOP, BTN_REVEAL, BTN_REPORT}

BTN_SEND_CONTACT = "📱 Отправить мой номер"
BTN_SEND_LOCATION = "📍 Отправить геолокацию"
BTN_FORM_CANCEL = "Отмена"

phone_request = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=BTN_SEND_CONTACT, request_contact=True)], [KeyboardButton(text=BTN_FORM_CANCEL)]],
    resize_keyboard=True, one_time_keyboard=True,
)
location_request = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=BTN_SEND_LOCATION, request_location=True)], [KeyboardButton(text=BTN_FORM_CANCEL)]],
    resize_keyboard=True, one_time_keyboard=True,
)
no_keyboard = ReplyKeyboardRemove()
