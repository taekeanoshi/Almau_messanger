# Almau Messenger

Анонимный Telegram-бот для студентов и сайт о нём. Бот случайно соединяет двух студентов
для разговора. Имя, @username и телефон собеседник не видит.

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
site/
  index.html         сайт (О проекте, Клубы, Ивенты)                         — Хамид, + RK1 (форма заявки, статистика, кнопка «Иду»)
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
   - `CORS_ORIGIN` — домен сайта на Vercel (например `https://almau.vercel.app`); для защиты
     можно временно оставить `*`.
   - `ADMIN_TOKEN` — свой произвольный секрет, чтобы читать заявки через `GET /api/leads`
     без бота (см. раздел [API](#api)).
   - `BREVO_API_KEY`, `BREVO_SENDER_EMAIL` — вход в «Анонимный чат» на сайте идёт по коду
     на почту через [Brevo](https://brevo.com) (300 писем/день бесплатно). Регистрация,
     подтверждённый отправитель (Senders → Add a sender) и API-ключ (SMTP & API → API Keys) —
     за пару минут. Без них код виден только в логах сервиса, письма не уходят.
   - `BOT_TOKEN` и `ADMIN_IDS` этому сервису не нужны вообще.
4. **Settings → Networking → Generate Domain** — обязательно, без публичного домена сайт
   не достучится до API. Railway сам передаёт порт через `$PORT`.
5. Проверьте `<домен>/health` → должно вернуть `{"status":"ok"}`.
6. Скопируйте домен в константу `API` в `site/index.html` и в таблицу
   [Живой продукт](#живой-продукт) выше.

База SQLite в обоих сервисах лежит в контейнере и сбрасывается при каждом новом деплое.
Для демо это не страшно, для постоянной работы подключите Volume и задайте
`DB_PATH=/data/almau.db`.

## API

Эндпоинты `bot/api.py` (сервис `bot/api_server.py`), доступны по адресу из константы `API`
в `site/index.html`:

| Метод | Путь | Что делает |
|-------|------|------------|
| GET | `/health` | проверка живости сервиса (используется Railway healthcheck) |
| GET | `/api/clubs` | список клубов из `content.py` |
| GET | `/api/events` | список ивентов + живое число «Иду» из БД |
| GET | `/api/events/{id}` | один ивент, 404 если не найден |
| POST | `/api/events/{id}/going` | отметка «Иду» прямо с сайта — требует вход в аккаунт (`Authorization: Bearer <token>`) |
| GET | `/api/stats` | публичная статистика: пользователи, диалоги, заявки, аккаунты и диалоги чата |
| POST | `/api/leads` | сохраняет заявку с формы сайта (`name`, `contact`, `message`, опц. `event_id`) в БД |
| GET | `/api/leads?token=<ADMIN_TOKEN>` | просмотр последних заявок без бота (401 без правильного токена) |
| POST | `/api/admin/reset-web-chat?token=<ADMIN_TOKEN>&confirm=yes` | **необратимо** стирает все аккаунты/диалоги/сообщения/фото «Анонимного чата» — для очистки тестовых данных перед демо |
| POST | `/api/chat/register` | аккаунт: `first_name`, `last_name`, `phone`, `email`, `password`, опц. `account_type` (`student` по умолчанию, `teacher`, `registrar`). Для `student` ещё нужны `gender`, `course`, `specialty`, опц. `nickname`. Для `teacher`/`registrar` вместо них — `invite_key` (см. `/api/admin/rotate-invite-key`), иначе 403. Всегда шлёт код подтверждения на почту |
| POST | `/api/chat/login` | `{"email", "password"}` — вход по паролю (нужна подтверждённая почта), выдаёт токен сессии |
| POST | `/api/chat/otp/request` | `{"email"}` — повторно шлёт 6-значный код на почту, действует 10 минут |
| POST | `/api/chat/otp/verify` | `{"email", "code"}` — подтверждает почту кодом (один раз, после регистрации), выдаёт токен сессии |
| POST | `/api/chat/logout` | завершает сессию по текущему токену |
| GET | `/api/chat/me` | данные текущего пользователя по токену (имя, пол, ник, курс, специальность) |
| POST | `/api/chat/search` | `{"preference": "any"\|"same"\|"opposite"}` — встать в очередь/найти собеседника по полу |
| GET | `/api/chat/poll?since=<id>` | опрос раз в 2.5 сек: статус (`idle`/`waiting`/`matched`), ник собеседника и новые сообщения |
| POST | `/api/chat/send` | `{"dialog_id", "text"}` — отправить текстовое сообщение в активном диалоге |
| POST | `/api/chat/send-photo` | `{"dialog_id", "content_type", "data"}` — отправить фото (base64, до 5 МБ, jpeg/png/webp/gif) |
| GET | `/api/chat/photo/{photo_id}` | отдаёт байты фото — только участникам диалога, иначе 403 |
| POST | `/api/chat/reveal` | `{"dialog_id"}` — раскрыть собеседнику своё настоящее имя системным сообщением в чате |
| POST | `/api/chat/next` / `/api/chat/stop` | закончить диалог и (не)встать в поиск заново |
| GET | `/api/chat/direct/search-users?q=<имя>` | прямой (не анонимный) чат — поиск зарегистрированных по имени/фамилии; пустой `q` отдаёт весь список как справочник |
| POST | `/api/chat/direct/start` | `{"user_id"}` — создать (или вернуть уже существующий) прямой диалог с конкретным человеком |
| GET | `/api/chat/direct/list` | список прямых диалогов текущего пользователя с данными собеседников |
| GET | `/api/chat/direct/poll?dialog_id=<id>&since=<id>` | новые сообщения в прямом диалоге — только для его участников |
| POST | `/api/chat/direct/send` | `{"dialog_id", "text"}` — отправить сообщение в прямом диалоге |
| POST | `/api/admin/set-role?token=<ADMIN_TOKEN>` | `{"email", "role"}` (`student`\|`teacher`\|`registrar`\|`admin`) — выдаёт роль аккаунту по почте вручную |
| POST | `/api/admin/rotate-invite-key?token=<ADMIN_TOKEN>` | `{"role": "teacher"\|"registrar"}` — перевыпускает ключ-приглашение для самостоятельной регистрации сотрудника; старый ключ сразу перестаёт работать, новый отдаётся только в этом ответе |
| GET | `/api/academics/courses` | список всех дисциплин |
| GET | `/api/academics/my-courses` | дисциплины, на которые записан текущий пользователь |
| POST | `/api/academics/enroll` | `{"course_id", опц. "schedule_item_id"}` — записаться; если у дисциплины несколько вариантов расписания, `schedule_item_id` обязателен |
| POST | `/api/academics/unenroll` | `{"course_id"}` — отписаться |
| GET | `/api/academics/courses/{id}/options` | варианты расписания дисциплины — виден любому вошедшему, чтобы выбрать при записи |
| POST | `/api/academics/schedule/choose` | `{"course_id", "schedule_item_id", опц. "user_id"}` — выбрать/сменить вариант расписания; без `user_id` — для себя, с `user_id` — только `registrar`/`admin` (перевести студента на другой день) |
| GET | `/api/academics/schedule` | личное расписание — только выбранный вариант по каждой записанной дисциплине |
| GET | `/api/academics/grades` | личные оценки по всем дисциплинам |
| GET | `/api/academics/attendance?course_id=<id>` | своя посещаемость и статистика (включая флаг ретейка) по одной дисциплине |
| GET | `/api/academics/teaching` | дисциплины, которые ведёт текущий (`teacher`/`admin`) аккаунт — 403 студентам и `registrar` |
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
| POST | `/api/academics/exams` | `{"course_id", "title", опц. "description", "time_limit_minutes", "require_recording"}` — создать экзамен (только `teacher`/`admin`) |
| POST | `/api/academics/exams/delete` | `{"exam_id"}` — удалить экзамен вместе с вопросами и попытками |
| POST | `/api/academics/exam-questions` | `{"exam_id", "text", "options": [...] (2-8 шт.), "correct_index"}` |
| POST | `/api/academics/exam-questions/delete` | `{"question_id"}` |
| GET | `/api/academics/exams/{id}/questions` | вопросы — студенту без `correct_index`, преподавателю/админу — с ним |
| POST | `/api/academics/exams/submit` | `{"exam_id", "answers", "tab_exit_count", опц. "recording": {"filename","content_type" (video/webm\|video/mp4),"data"}}` — авто-проверка + подсчёт выходов за пределы окна (считает браузер) + необязательная запись экрана (до 50 МБ); если у экзамена `require_recording=true`, без записи не принимается (`400`) |
| GET | `/api/academics/exams/my-attempt?exam_id=<id>` | своя попытка: счёт, число выходов, есть ли запись |
| GET | `/api/academics/exams/{id}/attempts` | результаты всех студентов — преподаватель дисциплины/админ |
| GET | `/api/academics/exam-attempts/{id}/recording` | скачать запись экрана конкретной попытки — сам сдававший студент или преподаватель дисциплины/админ |

Проверить локально после `python -m bot.api_server`:

```bash
curl http://localhost:8080/api/stats
curl -X POST http://localhost:8080/api/leads \
  -H "Content-Type: application/json" \
  -d '{"name":"Аян","contact":"@ayan","message":"Хочу демо"}'
curl "http://localhost:8080/api/leads?token=$ADMIN_TOKEN"
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

Сайт читает `data/*.json` через `fetch`, поэтому при двойном клике по файлу данные не загрузятся.
Его нужно открывать через веб-сервер:

```bash
cd site && python -m http.server 8000   # открыть http://localhost:8000
```

На Vercel в настройках проекта укажите **Root Directory = `site`**.
В `site/index.html` константа `BOT` должна совпадать с username бота, а константа `API` —
с доменом задеплоенного бэкенда (см. раздел «Хостинг» выше, подраздел «API»).
Если `API` недоступен, сайт не падает: статистика, кнопка «Иду» и живой счётчик просто
не работают, а клубы/ивенты как и раньше читаются из `data/*.json`.
