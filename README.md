# Almau Messenger

Анонимный Telegram-бот для студентов и сайт о нём. Бот случайно соединяет двух студентов
для разговора. Имя, @username и телефон собеседник не видит.

**Новое в этапе 2:**
- **Подбор по интересам.** Бот сначала ищет собеседника с общими темами и показывает их обоим.
- **Компания на ивент.** Можно отметить «Иду» на ивенте и анонимно поговорить с тем, кто тоже идёт.
  Кнопка на сайте открывает бота сразу на карточке ивента.
- **Модерация.** Жалобы приходят админам. После N жалоб от разных людей нарушитель блокируется автоматически.
  Для админов есть команды `/stats`, `/ban`, `/unban`.

Кто за что отвечает и как устроен код: [docs/TEAM.md](docs/TEAM.md).

## Структура

```
bot/
  main.py            точка входа, сборка диспетчера, фоновый подбор пар      — Дияр
  config.py          настройки из .env                                       — Дияр
  matchmaking.py     очередь поиска, подбор по интересам и ивентам          — Дияр
  keyboards.py       кнопки меню и диалога                                   — Дияр
  handlers/chat.py   поиск, пересылка сообщений, Следующий/Стоп, /give       — Дияр
  db.py              SQLite: профили, жалобы, баны, статистика, «Иду»        — Абулхаир
  handlers/profile.py   регистрация и профиль (вуз, курс, интересы, соцсети) — Абулхаир
  handlers/moderation.py  жалобы, автобан, /stats /ban /unban                — Абулхаир
  content.py         загрузка клубов и ивентов из site/data                  — Хамид
  handlers/events.py афиша, «Иду», «Найти компанию», /clubs                  — Хамид
site/
  index.html         сайт (О проекте, Клубы, Ивенты)                         — Хамид
  data/clubs.json    клубы — общие для сайта и бота                          — Хамид
  data/events.json   ивенты — общие для сайта и бота                         — Хамид
tests/               тесты всех частей (pytest)
```

## Запуск бота

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env        # вставить BOT_TOKEN от @BotFather и свой ADMIN_IDS
python -m bot.main
```

## Хостинг бота на Railway

1. [railway.com](https://railway.com) → **New Project** → **Deploy from GitHub repo** → `Almau_messanger`.
2. **Variables**: `BOT_TOKEN`, `ADMIN_IDS`, `BOT_USERNAME`.
3. Команда запуска уже задана в `railway.json`: `python -m bot.main`.

База SQLite лежит в контейнере и сбрасывается при каждом новом деплое. Для демо это не страшно,
для постоянной работы подключите к сервису Volume и задайте `DB_PATH=/data/almau.db`.

## Тесты

```bash
pytest -q
```

`tests/test_bot_flow.py` прогоняет полные сценарии бота без интернета: регистрацию, диалог,
жалобы, автобан и поиск компании на ивент.

## Сайт

Сайт читает `data/*.json` через `fetch`, поэтому при двойном клике по файлу данные не загрузятся.
Его нужно открывать через веб-сервер:

```bash
cd site && python -m http.server 8000   # открыть http://localhost:8000
```

На Vercel в настройках проекта укажите **Root Directory = `site`**.
В `site/index.html` константа `BOT` должна совпадать с username бота.
