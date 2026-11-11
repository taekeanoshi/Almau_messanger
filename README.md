# Pople (раньше — Almau Messenger)

Платформа для студентов AlmaU: **учёба** (расписание, оценки, посещаемость, задания, видеоуроки
с мини-тестами, проктор-экзамены) и **общение** (анонимный чат, сообщения по ФИО, подбор компании
на ивент, клубы, походы) в одном аккаунте, прямо в браузере. Плюс исходный Telegram-бот анонимного
чата — он живёт отдельно и сайту не нужен.

**Что есть на сайте:**
- **Аккаунты и роли.** Студент регистрируется сам; преподаватель и отдел расписания — по
  ключу-приглашению; админ управляет всем из вкладки «Админка».
- **Учёба.** Дисциплины с несколькими вариантами расписания (студент выбирает день), журнал
  оценок, посещаемость с автоматическим ретейком после 30% пропусков, задания со сдачей файлов,
  видеоуроки с мини-тестом, экзамены с таймером на сервере, подсчётом уходов из окна, записью
  экрана и снимками с веб-камеры.
- **Общение.** Анонимный чат с подбором по полу или «на ивент», фото, раскрытие личности,
  жалобы с автоблокировкой; обычные сообщения с непрочитанными и чёрным списком.
- **Главная-сводка.** Пары на сегодня, несданные задания, экзамены, последние оценки,
  непрочитанные сообщения, ивенты и походы.

**Новое в этапе 2:**
- **Подбор по интересам.** Бот сначала ищет собеседника с общими темами и показывает их обоим.
- **Компания на ивент.** Можно отметить «Иду» на ивенте и анонимно поговорить с тем, кто тоже идёт.
  Кнопка на сайте открывает бота сразу на карточке ивента.
- **Модерация.** Жалобы приходят админам. После N жалоб от разных людей нарушитель блокируется автоматически.
  Для админов есть команды `/stats`, `/ban`, `/unban`.

**Новое в RK1 — бэкенд и хостинг:**
- **Веб-API** (`bot/api.py` + отдельная точка входа `bot/api_server.py`): сайт больше не просто
  статика — он получает данные клубов/ивентов и живую статистику с сервера, кнопка «Иду»
  на сайте и форма заявки реально сохраняют данные в БД. См. раздел [API](#api).
- **Хостинг.** Бот и бэкенд — два независимых сервиса, деплоятся и хостятся отдельно друг
  от друга (бот — отдельно, API — отдельно, каждый на своём Railway-проекте). Живые ссылки —
  в разделе [Живой продукт](#живой-продукт).

Кто за что отвечает и как устроен код: [docs/TEAM.md](docs/TEAM.md).

## Живой продукт

| Что | Ссылка |
|-----|--------|
| Сайт | https://almau-messenger.vercel.app |
| Бот | https://t.me/Almau_mes_bot |
| API (бэкенд) | https://almaumessanger-production.up.railway.app |

После деплоя сайта на Vercel обновите ссылку на сайт в этой таблице.

## Партнёр: Summit Seekers

Горный клуб AlmaU — наш партнёр. Для него сделаны запись на походы с листом ожидания, напоминания,
рассылки, панель организатора `/club`, «Я спустился» и SOS, школа туризма, отчёт и страница на сайте.
Подробности и инструкция для организатора: [docs/SUMMIT.md](docs/SUMMIT.md).
Организаторов добавляют в переменную `CLUB_ADMIN_IDS`.

## Структура

```
bot/
  main.py            точка входа бота (polling); опционально поднимает и API в том же процессе — Дияр
  config.py          настройки из .env                                       — Дияр, + RK1 (PORT, CORS_ORIGIN, ADMIN_TOKEN)
  matchmaking.py     очередь поиска, подбор по интересам и ивентам          — Дияр
  keyboards.py       кнопки меню и диалога                                   — Дияр
  handlers/chat.py   поиск, пересылка сообщений, Следующий/Стоп, /give       — Дияр
  db.py              SQLite: профили, жалобы, баны, статистика, «Иду»        — Абулхаир, + RK1 (заявки с сайта)
  handlers/profile.py   регистрация и профиль (вуз, курс, интересы, соцсети) — Абулхаир
  handlers/moderation.py  жалобы, автобан, /stats /ban /unban                — Абулхаир, + RK1 (/leads)
  api.py             веб-API: /api/clubs, /api/events, /api/leads, /api/stats — RK1
  api_server.py      отдельная точка входа: только веб-API, без бота          — RK1
  content.py         загрузка клубов и ивентов из site/data                  — Хамид
  handlers/events.py афиша, «Иду», «Найти компанию», /clubs                  — Хамид
  web_chat.py        аккаунты сайта, вход, сброс пароля, анонимный и прямой чаты, жалобы
  academics.py       учёба: дисциплины, расписание, оценки, посещаемость, задания, уроки, экзамены
  admin.py           админ-панель: пользователи, роли, блокировки, жалобы, ключи, заявки
  ratelimit.py       лимиты частоты запросов (перебор паролей и кодов, спам)
site/
  index.html         разметка всех страниц сайта
  css/app.css        стили
  js/core.js         ядро: запросы к API, сессия, уведомления, маршрутизация, меню
  js/public.js       клубы, ивенты, партнёры, походы, заявки
  js/auth.js         вход, регистрация, сброс пароля
  js/home.js         главная-сводка
  js/chat.js         анонимный чат          js/people.js    сообщения
  js/academics.js    учёба (студент)         js/teaching.js  учёба (сотрудник)
  js/exam.js         прохождение экзамена    js/profile.js   профиль
  js/admin.js        админка                 js/main.js      запуск
  vercel.json        заголовки безопасности сайта (CSP и др.)
  data/clubs.json    клубы — общие для сайта и бота                          — Хамид
  data/events.json   ивенты — общие для сайта и бота                         — Хамид
tests/               тесты всех частей, включая API (pytest)
```

## Запуск бота

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env        # вставить BOT_TOKEN от @BotFather и свой ADMIN_IDS
python -m bot.main
```

## Хостинг

Бот и бэкенд (API) — два независимых сервиса с разными точками входа. Их можно деплоить
разными людьми, в разное время и даже на разные платформы — они не знают друг о друге
и общаются только через общий файл `site/data/*.json` с клубами/ивентами.

### Бот — `python -m bot.main`

1. [railway.com](https://railway.com) → **New Project** → **Deploy from GitHub repo** → `Almau_messanger`.
2. **Variables**: `BOT_TOKEN`, `ADMIN_IDS`, `BOT_USERNAME`.
3. Команда запуска уже задана в `railway.json`. Публичный домен боту не нужен — он сам стучится
   в Telegram через long polling, а не наоборот.

### API — `python -m bot.api_server`

Отдельный сервис, без Telegram-токена, со своей базой (заявки с сайта + отметки «Иду» через сайт).

1. [railway.com](https://railway.com) → **New Project** → **Deploy from GitHub repo** → тот же
   репозиторий `Almau_messanger` (можно в своём, отдельном Railway-проекте).
2. **Settings → Deploy → Custom Start Command**: `python -m bot.api_server`
   (переопределяет команду из `railway.json`, которая рассчитана на бота).
3. **Variables**:
   - `CORS_ORIGIN` — домен сайта на Vercel (например `https://almau-messenger.vercel.app`).
     Со значением `*` API принимает запросы с любых сайтов — так оставлять не стоит.
   - `REPORTS_TO_BAN` — после жалоб от скольких разных людей аккаунт блокируется сам (по умолчанию 3).
   - `ADMIN_TOKEN` — свой длинный случайный секрет. Нужен один раз — назначить первого
     администратора (см. [Первый администратор](#первый-администратор)); дальше всё делается из «Админки».
   - `BREVO_API_KEY`, `BREVO_SENDER_EMAIL` — вход в «Анонимный чат» на сайте идёт по коду
     на почту через [Brevo](https://brevo.com) (300 писем/день бесплатно). Регистрация,
     подтверждённый отправитель (Senders → Add a sender) и API-ключ (SMTP & API → API Keys) —
     за пару минут. Без них код виден только в логах сервиса, письма не уходят.
   - `BOT_TOKEN` и `ADMIN_IDS` этому сервису не нужны вообще.
4. **Settings → Networking → Generate Domain** — обязательно, без публичного домена сайт
   не достучится до API. Railway сам передаёт порт через `$PORT`.
5. Проверьте `<домен>/health` → должно вернуть `{"status":"ok"}`.
6. Скопируйте домен в `<meta name="pople-api">` в `site/index.html`, в `connect-src` в
   `site/vercel.json` и в таблицу [Живой продукт](#живой-продукт) выше.

**База данных.** SQLite лежит в контейнере, а контейнер пересоздаётся при каждом деплое. Без
Volume все аккаунты, оценки и переписки стираются при каждом обновлении — для рабочей платформы
это недопустимо. В Railway: сервис → **Volumes → New Volume**, точка монтирования `/data`, и
переменная `DB_PATH=/data/almau.db`.

### Первый администратор

1. Зарегистрируйтесь на сайте как обычный студент.
2. Один раз выдайте себе роль серверным секретом:
   ```bash
   curl -X POST "https://<домен API>/api/admin/set-role" \
     -H "X-Admin-Token: $ADMIN_TOKEN" -H "Content-Type: application/json" \
     -d '{"email": "вы@example.com", "role": "admin"}'
   ```
3. Перезайдите на сайт — в меню появится «Админка»: пользователи и роли, блокировки, жалобы,
   ключи-приглашения для преподавателей и отдела расписания, заявки, записи на походы.

## Безопасность и данные

- Пароли хранятся только как PBKDF2-хеши, токены сессий — только как SHA-256; сессия живёт 30 дней,
  смена/сброс пароля и блокировка обрывают остальные сессии.
- Лимиты частоты (`bot/ratelimit.py`): неверные пароли, регистрация, письма с кодом, проверка
  кода, заявки, сообщения, жалобы. Письмо с кодом — не чаще раза в 45 секунд на адрес.
- Анонимный чат не раскрывает собеседника: наружу не отдаётся ни его id, ни связка «ник — имя».
- Тип фото определяется по сигнатуре файла; ответы API идут с `nosniff` и `no-store`.
- Сайт отдаётся с Content-Security-Policy без inline-скриптов (`site/vercel.json`).
- Плановая очистка раз в час: истёкшие сессии и коды, регистрации без подтверждения почты
  (2 дня), анонимные переписки (30 дней), записи экрана и снимки с экзаменов (90 дней).
- Пользователь может удалить аккаунт со всеми данными из профиля. Что и как хранится —
  на странице сайта «Как мы храним данные» (`#privacy`).
- `ADMIN_TOKEN` передавайте в заголовке `X-Admin-Token`: параметр `?token=` тоже работает,
  но адреса запросов оседают в логах.

## API

Эндпоинты сервиса `bot/api_server.py`, доступны по адресу из `<meta name="pople-api">`
в `site/index.html`. Всё, кроме публичных данных, входа и регистрации, требует
`Authorization: Bearer <token>`:

| Метод | Путь | Что делает |
|-------|------|------------|
| GET | `/health` | проверка живости сервиса (используется Railway healthcheck) |
| GET | `/api/clubs` | список клубов из `content.py` |
| GET | `/api/events` | список ивентов + живое число «Иду» из БД |
| GET | `/api/events/{id}` | один ивент, 404 если не найден |
| POST | `/api/events/{id}/going` | отметка «Иду» прямо с сайта — требует вход в аккаунт (`Authorization: Bearer <token>`) |
| GET | `/api/stats` | публичные цифры для главной: пользователи, диалоги, диалоги «на ивент» |
| GET | `/api/dashboard` | сводка «Сегодня»: пары, несданные задания, экзамены, последние оценки, непрочитанные, ивенты, походы |
| GET | `/api/hikes`, `/api/hikes/{id}` | походы с числом свободных мест (без имён и телефонов) |
| POST | `/api/hikes/{id}/signup` \| `/cancel` \| `/checkin` | запись на поход из аккаунта (лист ожидания, если мест нет), отмена, «Я спустился» |
| GET | `/api/hikes/mine` | мои походы со статусом записи |
| POST | `/api/leads` | сохраняет заявку с формы сайта (`name`, `contact`, `message`, опц. `event_id`) в БД |
| GET | `/api/leads` + `X-Admin-Token` | просмотр последних заявок без бота (401 без правильного токена) |
| POST | `/api/admin/reset-web-chat?confirm=yes` + `X-Admin-Token` | **необратимо** стирает все аккаунты/диалоги/сообщения/фото «Анонимного чата» — для очистки тестовых данных перед демо |
| POST | `/api/chat/register` | аккаунт: `first_name`, `last_name`, `phone`, `email`, `password`, опц. `account_type` (`student` по умолчанию, `teacher`, `registrar`). Для `student` ещё нужны `gender`, `course`, `specialty`, опц. `nickname`. Для `teacher`/`registrar` вместо них — `invite_key` (см. `/api/admin/rotate-invite-key`), иначе 403. Всегда шлёт код подтверждения на почту |
| POST | `/api/chat/login` | `{"email", "password"}` — вход по паролю (нужна подтверждённая почта), выдаёт токен сессии; после 8 неверных паролей — `429` на 15 минут |
| POST | `/api/chat/otp/request` | `{"email"}` — повторно шлёт 6-значный код на почту, действует 10 минут; ответ одинаковый для любой почты |
| POST | `/api/chat/password/forgot` | `{"email"}` — код для сброса пароля на почту |
| POST | `/api/chat/password/reset` | `{"email", "code", "password"}` — новый пароль по коду; прежние сессии сбрасываются |
| POST | `/api/chat/password/change` | `{"current_password", "new_password"}` — смена пароля; остальные устройства выходят |
| POST | `/api/chat/profile` | изменить имя, фамилию, ник, курс, специальность |
| POST | `/api/chat/logout-all` | выйти на всех устройствах, кроме текущего |
| POST | `/api/chat/account/delete` | `{"password"}` — необратимо удалить аккаунт со всеми данными |
| POST | `/api/chat/otp/verify` | `{"email", "code"}` — подтверждает почту кодом (один раз, после регистрации), выдаёт токен сессии |
| POST | `/api/chat/logout` | завершает сессию по текущему токену |
| GET | `/api/chat/me` | профиль текущего пользователя (включая роль) |
| POST | `/api/chat/search` | `{"preference": "any"\|"same"\|"opposite", опц. "event_id"}` — найти собеседника по полу; с `event_id` — только среди тех, кто ищет компанию на тот же ивент |
| POST | `/api/chat/report` | `{"dialog_id", опц. "reason"}` — жалоба на собеседника (анонимный или прямой чат): пара блокируется друг для друга, после жалоб от `REPORTS_TO_BAN` разных людей — автоблокировка |
| POST | `/api/chat/block` | `{"user_id", опц. "blocked": false}` — заблокировать/разблокировать человека в сообщениях |
| GET | `/api/chat/blocks` | мой чёрный список |
| GET | `/api/chat/unread` | число непрочитанных сообщений (значок в меню) |
| GET | `/api/chat/poll?since=<id>` | опрос раз в 2.5 сек: статус (`idle`/`waiting`/`matched`), ник собеседника и новые сообщения |
| POST | `/api/chat/send` | `{"dialog_id", "text"}` — отправить текстовое сообщение в активном диалоге |
| POST | `/api/chat/send-photo` | `{"dialog_id", "data"}` — отправить фото (base64, до 5 МБ; jpeg/png/webp/gif определяется по сигнатуре файла) |
| GET | `/api/chat/photo/{photo_id}` | отдаёт байты фото — только участникам диалога, иначе 403 |
| POST | `/api/chat/reveal` | `{"dialog_id"}` — раскрыть собеседнику своё настоящее имя системным сообщением в чате |
| POST | `/api/chat/next` / `/api/chat/stop` | закончить диалог и (не)встать в поиск заново |
| GET | `/api/chat/direct/search-users?q=<имя>` | прямой (не анонимный) чат — поиск зарегистрированных по имени/фамилии; пустой `q` отдаёт весь список как справочник |
| POST | `/api/chat/direct/start` | `{"user_id"}` — создать (или вернуть уже существующий) прямой диалог с конкретным человеком |
| GET | `/api/chat/direct/list` | прямые диалоги: собеседник, последнее сообщение, число непрочитанных |
| POST | `/api/chat/direct/send-photo` | `{"dialog_id", "data"}` — фото в прямом диалоге |
| GET | `/api/chat/direct/poll?dialog_id=<id>&since=<id>` | новые сообщения в прямом диалоге — только для его участников |
| POST | `/api/chat/direct/send` | `{"dialog_id", "text"}` — отправить сообщение в прямом диалоге |
| GET | `/api/admin/overview`, `/users?q=`, `/leads`, `/reports`, `/hike-signups` | админ-панель (сессия с ролью `admin`): сводка, аккаунты, заявки, жалобы с последними сообщениями диалога, записавшиеся на походы |
| POST | `/api/admin/users/role` \| `/users/ban` \| `/invite-key` \| `/reports/resolve` | сменить роль, заблокировать/разблокировать, выпустить ключ-приглашение, закрыть жалобу |
| POST | `/api/admin/set-role` + `X-Admin-Token` | `{"email", "role"}` (`student`\|`teacher`\|`registrar`\|`admin`) — выдать роль серверным секретом (нужно для первого админа) |
| POST | `/api/admin/rotate-invite-key` + `X-Admin-Token` | `{"role": "teacher"\|"registrar"}` — перевыпускает ключ-приглашение для самостоятельной регистрации сотрудника; старый ключ сразу перестаёт работать, новый отдаётся только в этом ответе |
| GET | `/api/academics/courses` | список всех дисциплин |
| GET | `/api/academics/my-courses` | дисциплины, на которые записан текущий пользователь |
| POST | `/api/academics/enroll` | `{"course_id", опц. "schedule_item_id"}` — записаться; если у дисциплины несколько вариантов расписания, `schedule_item_id` обязателен |
| POST | `/api/academics/unenroll` | `{"course_id"}` — отписаться |
| GET | `/api/academics/courses/{id}/options` | варианты расписания дисциплины — виден любому вошедшему, чтобы выбрать при записи |
| POST | `/api/academics/schedule/choose` | `{"course_id", "schedule_item_id", опц. "user_id"}` — выбрать/сменить вариант расписания; без `user_id` — для себя, с `user_id` — только `registrar`/`admin` (перевести студента на другой день) |
| GET | `/api/academics/schedule` | личное расписание — только выбранный вариант по каждой записанной дисциплине |
| GET | `/api/academics/grades` | личные оценки по всем дисциплинам |
| GET | `/api/academics/attendance?course_id=<id>` | своя посещаемость и статистика (включая флаг ретейка) по одной дисциплине |
| GET | `/api/academics/teaching` | дисциплины сотрудника: преподавателю — свои, `registrar`/`admin` — все; плюс флаги `can_grade` и `can_assign_teachers` |
| GET | `/api/academics/teachers` | кого можно назначить преподавателем — `registrar`/`admin` |
| POST | `/api/academics/courses/set-teacher` | `{"course_id", "teacher_id"\|null}` — назначить преподавателя дисциплины (`registrar`/`admin`) |
| POST | `/api/academics/courses/update` \| `/courses/delete` | изменить код/название/описание или удалить дисциплину со всем содержимым — её преподаватель/админ |
| POST | `/api/academics/courses` | `{"code", "title", "description"}` — создать дисциплину (только `teacher`/`admin`) |
| GET | `/api/academics/courses/{id}/roster` | список записанных студентов с их выбором расписания — преподаватель дисциплины, `registrar` или админ |
| GET | `/api/academics/courses/{id}/schedule` | расписание дисциплины (для управления) — преподаватель дисциплины, `registrar` или админ |
| POST | `/api/academics/schedule` | `{"course_id", "day_of_week" (0=Пн), "start_time", "end_time", опц. "room", "kind", "teacher_id"}` — добавить вариант расписания; доступно преподавателю дисциплины, `registrar` или админу |
| POST | `/api/academics/schedule/set-teacher` | `{"item_id", "teacher_id"}` — переназначить, кто ведёт конкретный вариант расписания (только `registrar`/`admin`, не сам преподаватель) |
| POST | `/api/academics/schedule/delete` | `{"item_id"}` — удалить вариант расписания |
| POST | `/api/academics/grades/set` | `{"course_id", "user_id", "label", "score", опц. "max_score", "comment"}` — выставить/обновить оценку (только преподаватель этой дисциплины/админ, студент должен быть записан) |
| GET | `/api/academics/courses/{id}/grades` | полная ведомость по дисциплине — преподаватель дисциплины/админ |
| POST | `/api/academics/attendance/mark` | `{"schedule_item_id", "user_id", "class_date" ("YYYY-MM-DD"), "present"}` — отметить присутствие/отсутствие; только преподаватель этого варианта расписания/админ. Новую отметку нельзя поставить, если у студента уже больше 30% пропусков (`409`) — но уже существующую отметку (в т.ч. за прошлые недели) можно поправить всегда |
| POST | `/api/academics/attendance/delete` | `{"schedule_item_id", "user_id", "class_date"}` — снять отметку за дату (тоже работает за прошлые недели) |
| GET | `/api/academics/schedule-items/{id}/attendance` | список студентов этого варианта расписания с их посещаемостью и статусом ретейка — для преподавателя варианта/админа |
| GET | `/api/academics/courses/{id}/assignments` | задания дисциплины — записанному студенту (видит только своё) или преподавателю дисциплины/админу (видит всё) |
| POST | `/api/academics/assignments` | `{"course_id", "title", опц. "description", "due_at" ("YYYY-MM-DD"), "file": {"filename","content_type","data" (base64)}}` — создать задание, файл до 10 МБ (pdf/zip/doc/xls/ppt/txt/картинки) |
| POST | `/api/academics/assignments/delete` | `{"assignment_id"}` — удалить задание вместе со всеми сдачами |
| GET | `/api/academics/assignments/{id}/file` | файл задания — записанному студенту/преподавателю дисциплины/админу |
| POST | `/api/academics/submissions` | `{"assignment_id", "text", опц. "file"}` — сдать (или пересдать — затирает предыдущую сдачу и сбрасывает оценку); нужны текст или файл |
| GET | `/api/academics/my-submission?assignment_id=<id>` | своя сдача по заданию (или `null`, если ещё не сдавал) |
| GET | `/api/academics/assignments/{id}/submissions` | все сдачи по заданию — преподаватель дисциплины/админ |
| GET | `/api/academics/submissions/{id}/file` | файл конкретной сдачи — сам сдавший студент или преподаватель дисциплины/админ |
| POST | `/api/academics/submissions/grade` | `{"submission_id", "score", опц. "comment"}` — оценить сдачу |
| GET | `/api/academics/courses/{id}/lessons` | видеоуроки дисциплины — записанному студенту или преподавателю дисциплины/админу |
| POST | `/api/academics/lessons` | `{"course_id", "title", опц. "description", "video_url", "file"}` — создать видеоурок; видео храним ссылкой (YouTube и т.п.), не файлом; материал — файл до 10 МБ, как в заданиях |
| POST | `/api/academics/lessons/delete` | `{"lesson_id"}` — удалить урок вместе с вопросами теста и попытками |
| GET | `/api/academics/lessons/{id}/file` | файл-материал урока — записанному студенту/преподавателю дисциплины/админу |
| POST | `/api/academics/quiz-questions` | `{"lesson_id", "text", "options": [...] (2-8 шт.), "correct_index"}` — добавить вопрос к мини-тесту урока |
| POST | `/api/academics/quiz-questions/delete` | `{"question_id"}` |
| GET | `/api/academics/lessons/{id}/quiz` | вопросы теста — студенту без `correct_index` (чтобы не подсматривать), преподавателю/админу — с ним |
| POST | `/api/academics/quiz/submit` | `{"lesson_id", "answers": [индексы по порядку вопросов]}` — авто-проверка; повторное прохождение перезаписывает попытку, а не плодит новую |
| GET | `/api/academics/quiz/my-attempt?lesson_id=<id>` | своя попытка (счёт, сколько верно) или `null` |
| GET | `/api/academics/lessons/{id}/attempts` | результаты всех студентов по тесту — преподаватель дисциплины/админ |
| GET | `/api/academics/courses/{id}/exams` | проктор-экзамены дисциплины — записанному студенту или преподавателю дисциплины/админу |
| POST | `/api/academics/exams` | `{"course_id", "title", опц. "description", "time_limit_minutes", "require_recording", "require_camera"}` — создать экзамен (только `teacher`/`admin`) |
| POST | `/api/academics/exams/delete` | `{"exam_id"}` — удалить экзамен вместе с вопросами и попытками |
| POST | `/api/academics/exam-questions` | `{"exam_id", "text", "options": [...] (2-8 шт.), "correct_index"}` |
| POST | `/api/academics/exam-questions/delete` | `{"question_id"}` |
| GET | `/api/academics/exams/{id}/questions` | вопросы с правильными ответами — только преподавателю дисциплины/админу (редактор) |
| POST | `/api/academics/exams/start` | `{"exam_id"}` — студент начинает экзамен: сервер фиксирует время старта и только теперь отдаёт вопросы (без ответов) и `seconds_left`. Повторный вызов возвращает тот же сеанс |
| POST | `/api/academics/exams/recording-chunk` | `{"exam_id", "seq", "data"}` — очередной кусок записи экрана (до 8 МБ, всего до 150 МБ на попытку) |
| POST | `/api/academics/exams/snapshot` | `{"exam_id", "data"}` — кадр с веб-камеры (JPEG до 300 КБ) |
| POST | `/api/academics/exams/submit` | `{"exam_id", "answers", "tab_exit_count", "away_seconds"}` — сдача, **один раз** (`409` при повторе). Возвращает балл, но не правильные ответы. Если экзамен требует запись экрана или снимки камеры, а их нет — `400`. Сдача после лимита принимается, но помечается `late` |
| GET | `/api/academics/exams/my-attempt?exam_id=<id>` | своя попытка или незавершённый сеанс с оставшимся временем |
| GET | `/api/academics/exams/{id}/attempts` | результаты всех студентов (балл, выходы из окна, время вне окна, длительность, просрочка) и список начавших, но не сдавших — преподаватель дисциплины/админ |
| GET | `/api/academics/exam-attempts/{id}/recording` | запись экрана попытки — сдававший студент или преподаватель дисциплины/админ |
| GET | `/api/academics/exam-attempts/{id}/snapshots`, `/api/academics/exam-snapshots/{id}` | снимки камеры попытки — преподаватель дисциплины/админ |
| POST | `/api/academics/exam-attempts/reset` | `{"exam_id", "user_id"}` — разрешить пересдачу: стирает попытку, запись и снимки |

Проверить локально после `python -m bot.api_server`:

```bash
curl http://localhost:8080/api/stats
curl -X POST http://localhost:8080/api/leads \
  -H "Content-Type: application/json" \
  -d '{"name":"Аян","contact":"@ayan","message":"Хочу демо"}'
curl http://localhost:8080/api/leads -H "X-Admin-Token: $ADMIN_TOKEN"
```

Если API запущен вместе с ботом через `python -m bot.main` (а не отдельно), `POST /api/leads`
дополнительно уведомляет админов в Telegram, и заявки видны ещё и командой `/leads` в боте.

## Тесты

```bash
pytest -q
```

`tests/test_bot_flow.py` прогоняет полные сценарии бота без интернета: регистрацию, диалог,
жалобы, автобан и поиск компании на ивент.

## Сайт

Статический сайт без сборки: `index.html` + `css/` + `js/`. Данные клубов и ивентов читаются
через `fetch`, поэтому открывать его нужно через веб-сервер, а не двойным кликом:

```bash
cd site && python -m http.server 8000   # открыть http://localhost:8000
```

Чтобы локальный сайт ходил в локальный API, запустите `python -m bot.api_server` и поменяйте
адрес в `<meta name="pople-api">` на `http://localhost:8080`.

На Vercel в настройках проекта укажите **Root Directory = `site`** — тогда подхватится
`site/vercel.json` с заголовками безопасности. В нём `connect-src` разрешает только домен API:
при смене домена бэкенда поправьте и `vercel.json`, и `<meta name="pople-api">`, иначе браузер
заблокирует запросы.

Если API недоступен, сайт не падает: афиша и клубы читаются из `data/*.json`, а действия,
которым нужен сервер, честно сообщают об ошибке.
