"use strict";
// Главная: гостю — лендинг, вошедшему — сводка «Сегодня» (пары, дедлайны, оценки, сообщения).

function dashLine(main, sub, right = "") {
  return `<div class="line"><span>${main}${sub ? `<small>${sub}</small>` : ""}</span>${right}</div>`;
}
const dashEmpty = text => `<p class="muted small" style="margin:0">${text}</p>`;

function classesCard(d) {
  const now = new Date().toTimeString().slice(0, 5);
  let body;
  if (d.classes_today.length) {
    body = d.classes_today.map(c => {
      const state = c.end_time < now ? `<span class="pill">прошла</span>`
        : c.start_time <= now ? `<span class="pill ok">идёт сейчас</span>` : "";
      return dashLine(`${esc(c.course_code)} · ${esc(c.course_title)}`,
        `${esc(c.kind)}${c.room ? ` · ауд. ${esc(c.room)}` : ""}`,
        `<span class="row" style="gap:6px;flex-wrap:nowrap">${state}<span class="when">${esc(c.start_time)}–${esc(c.end_time)}</span></span>`);
    }).join("");
  } else if (d.classes_total) {
    body = dashEmpty("Сегодня пар нет — можно выдохнуть.");
  } else {
    body = dashEmpty("Расписание появится, когда запишешься на дисциплины.") +
      `<a class="btn sm" href="#academics" data-acad-tab="catalog" style="margin-top:12px">Выбрать дисциплины</a>`;
  }
  return `<div class="card"><h3>Пары сегодня · ${WEEKDAY_FULL[d.weekday].toLowerCase()}<a href="#academics" data-acad-tab="schedule">вся неделя →</a></h3>${body}</div>`;
}

function deadlinesCard(d) {
  const body = d.deadlines.length ? d.deadlines.map(a => dashLine(esc(a.title), esc(a.course_code),
    a.due_at ? `<span class="pill ${a.overdue ? "bad" : "warn"}">${a.overdue ? "просрочено · " : "до "}${esc(fmtDate(a.due_at))}</span>`
      : `<span class="pill">без срока</span>`)).join("")
    : dashEmpty("Несданных заданий нет.");
  return `<div class="card"><h3>Задания к сдаче<a href="#academics" data-acad-tab="courses">открыть →</a></h3>${body}</div>`;
}

function examsCard(d) {
  if (!d.exams.length) return "";
  return `<div class="card"><h3>Экзамены<a href="#academics" data-acad-tab="courses">открыть →</a></h3>${
    d.exams.map(x => dashLine(esc(x.title), esc(x.course_code),
      x.time_limit_minutes ? `<span class="pill">${x.time_limit_minutes} мин</span>` : "")).join("")}</div>`;
}

function gradesCard(d) {
  const body = d.grades.length ? d.grades.map(g => dashLine(esc(g.label), esc(g.course_code),
    `<b>${g.score}<span class="muted"> / ${g.max_score}</span></b>`)).join("") : dashEmpty("Оценок пока нет.");
  return `<div class="card"><h3>Последние оценки<a href="#academics" data-acad-tab="grades">все →</a></h3>${body}</div>`;
}

function attendanceCard(d) {
  if (!d.attendance_alerts.length) return "";
  return `<div class="card"><h3>Посещаемость</h3>${d.attendance_alerts.map(a => dashLine(
    `${esc(a.course_code)} · ${esc(a.course_title)}`, `пропущено ${a.absent} из ${a.total}`,
    a.on_retake ? `<span class="pill bad">ретейк</span>` : `<span class="pill warn">${Math.round(a.ratio * 100)}% — близко к 30%</span>`)).join("")}</div>`;
}

function eventsCard(d) {
  const lines = [
    ...d.hikes.map(h => dashLine(`🏔 ${esc(h.title)}`, esc(fmtStart(h.start)),
      h.my_status === "going" ? `<span class="pill ok">записан(а)</span>` : `<span class="pill warn">лист ожидания</span>`)),
    ...d.events.map(e => dashLine(esc(e.title), esc(e.date),
      `<button type="button" class="btn ghost sm" data-find-company="${esc(e.id)}">🎟 Компания</button>`)),
  ];
  const body = lines.length ? lines.join("") : dashEmpty("Отметься «Иду» на ивенте — он появится здесь, и можно будет найти компанию.");
  return `<div class="card"><h3>Мои ивенты и походы<a href="#events">афиша →</a></h3>${body}</div>`;
}

function teachingCard(d) {
  if (!d.teaching) return "";
  return `<div class="card"><h3>Преподавание<a href="#academics" data-acad-tab="teaching">открыть →</a></h3>
    ${dashLine("Дисциплин", "", `<b>${d.teaching.courses}</b>`)}
    ${dashLine("Работ ждут проверки", "", d.teaching.ungraded ? `<span class="count">${d.teaching.ungraded}</span>` : `<b>0</b>`)}</div>`;
}

function quickActions(d) {
  const unread = d.unread ? `<span class="count">${d.unread}</span>` : "";
  const role = d.user.role;
  const buttons = [];
  if (role === "student" || role === "admin") buttons.push(`<a class="btn" href="#chat">💬 Анонимный чат</a>`);
  buttons.push(`<a class="btn ghost" href="#people">✉️ Сообщения${unread}</a>`);
  buttons.push(`<a class="btn ghost" href="#academics">${role === "registrar" ? "🗓 Расписание" : "🎓 Учёба"}</a>`);
  if (role === "student" || role === "admin") buttons.push(`<a class="btn ghost" href="#events">🎉 Ивенты</a>`);
  if (role === "admin") buttons.push(`<a class="btn ghost" href="#admin">🛠 Админка</a>`);
  return buttons.join("");
}

function renderDashboard(d) {
  const today = new Date();
  $("dash-hello").textContent = `Привет, ${d.user.first_name}!`;
  $("dash-date").textContent = `${WEEKDAY_FULL[d.weekday]}, ${today.getDate()} ${MONTHS[today.getMonth()]} · ${ROLE_NAMES[d.user.role] || ""}`;
  $("dash-quick").innerHTML = quickActions(d);
  const studentCards = d.user.role === "student" || d.user.role === "admin";
  $("dash-cards").innerHTML = [
    studentCards ? classesCard(d) : "",
    teachingCard(d),
    studentCards ? deadlinesCard(d) : "",
    studentCards ? examsCard(d) : "",
    studentCards ? gradesCard(d) : "",
    studentCards ? attendanceCard(d) : "",
    studentCards ? eventsCard(d) : "",
    d.user.role === "registrar" ? `<div class="card"><h3>Отдел расписания</h3>${dashEmpty(
      "Расписание дисциплин, назначение преподавателей и перевод студентов между днями — в разделе «Учёба».")}
      <a class="btn sm" href="#academics" style="margin-top:12px">Открыть расписание</a></div>` : "",
  ].join("");
  if (d.unread !== unreadCount) { unreadCount = d.unread; renderNav(); }
}

async function loadDashboard() {
  try {
    renderDashboard(await api("/api/dashboard"));
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return;
    $("dash-cards").innerHTML = `<div class="empty">${esc(explain(err, "Не получилось загрузить сводку."))}
      <br><button type="button" class="btn ghost sm" id="dash-retry">Повторить</button></div>`;
  }
}

function enterHome() {
  const authed = !!chatToken;
  $("home-landing").hidden = authed;
  $("home-dashboard").hidden = !authed;
  if (!authed) return;
  if (chatUser) {
    $("dash-hello").textContent = `Привет, ${chatUser.first_name}!`;
  }
  loadDashboard();
}

function initHome() {
  definePage("home", {enter: enterHome});
  document.addEventListener("click", e => { if (e.target.closest("#dash-retry")) loadDashboard(); });
}
