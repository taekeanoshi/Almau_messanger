"use strict";
// Учёба, сторона студента: расписание по дням, оценки и посещаемость, мои дисциплины
// (задания, видеоуроки с мини-тестом, экзамены), каталог. Сторона сотрудника — js/teaching.js,
// прохождение экзамена — js/exam.js.

const SCHEDULE_KINDS = ["лекция", "практика", "лаб", "семинар"];
const CATALOG_NOTE = "Дисциплина из расписания AlmaU, осенний семестр 2026-2027.";  // = bot/curriculum.py CATALOG_MARK
let acadTab = null;

const acadEmpty = text => `<div class="empty">${text}</div>`;
const acadError = (err, fallback) => acadEmpty(esc(explain(err, fallback)));
const slotLabel = i => `${WEEKDAY_NAMES[i.day_of_week]}, ${esc(i.start_time)}–${esc(i.end_time)}${i.room ? ` · ауд. ${esc(i.room)}` : ""}`;

// Ближайший к элементу id дисциплины / произвольный data-атрибут предка.
const courseIdOf = el => Number(el.closest("[data-course]").dataset.course);

function fileButton(kind, id, name) {
  return name ? `<button type="button" class="btn ghost sm" data-act="download" data-kind="${kind}" data-id="${id}" data-filename="${esc(name)}">📎 ${esc(name)}</button>` : "";
}

// ---------- расписание ----------
async function renderScheduleTab(box) {
  try {
    const {schedule} = await api("/api/academics/schedule");
    if (!schedule.length) {
      box.innerHTML = acadEmpty(`Расписание пустое. Запишись на дисциплины — пары появятся здесь.
        <br><button type="button" class="btn sm" data-acad-tab="catalog">Выбрать дисциплины</button>`);
      return;
    }
    const today = (new Date().getDay() + 6) % 7;  // 0 = понедельник, как на сервере
    const days = WEEKDAY_FULL.map((name, day) => ({name, day, items: schedule.filter(i => i.day_of_week === day)}))
      .filter(d => d.items.length || d.day === today);
    box.innerHTML = `<div class="week">${days.map(d => `<div class="day ${d.day === today ? "today" : ""}">
      <h4>${d.name}${d.day === today ? "<span>сегодня</span>" : ""}</h4>
      ${d.items.length ? d.items.map(i => `<div class="cls"><span class="when">${esc(i.start_time)}–${esc(i.end_time)}</span>
        <b>${esc(i.course_code)} · ${esc(i.course_title)}</b>
        <small>${esc(i.kind)}${i.room ? ` · ауд. ${esc(i.room)}` : ""}${i.teacher_name ? ` · ${esc(i.teacher_name)}` : ""}</small></div>`).join("")
        : `<p class="muted small" style="margin:0">Пар нет</p>`}</div>`).join("")}</div>`;
  } catch (err) { box.innerHTML = acadError(err, "Не получилось загрузить расписание."); }
}

// ---------- оценки и посещаемость ----------
function attendanceBlock(stats) {
  if (!stats.total) return `<p class="muted small" style="margin:10px 0 0">Посещаемость пока не отмечалась.</p>`;
  const pct = Math.round(stats.ratio * 100);
  const cls = stats.on_retake ? "bad" : pct >= 20 ? "warn" : "";
  return `<div style="margin-top:12px"><div class="row between small"><span>Пропущено ${stats.absent} из ${stats.total} пар (${pct}%)</span>
      ${stats.on_retake ? `<span class="pill bad">ретейк: больше 30% пропусков</span>` : pct >= 20 ? `<span class="pill warn">близко к ретейку</span>` : ""}</div>
    <div class="progress ${cls}"><i style="width:${Math.min(100, Math.round(pct / 30 * 100))}%"></i></div>
    <p class="muted small" style="margin:0">Шкала заполняется к 30% пропусков — после этого автоматический ретейк.</p></div>`;
}

// Ведомость: ТК1 (средний текущий 1), РК1, ТК2, РК2, Сессия. Итог = (РК1 + РК2) / 2 × 0,6 + Сессия × 0,4 —
// считается, только когда есть обе рубежки и сессия.
const GRADE_COLUMNS = [["ТК1", "Средний текущий 1"], ["РК1", "Рубежный контроль 1"], ["ТК2", "Средний текущий 2"],
  ["РК2", "Рубежный контроль 2"], ["Сессия", "Экзамен в сессию"]];
const round1 = x => Math.round(x * 10) / 10;
const scoreClass = pct => pct >= 90 ? "ok" : pct >= 70 ? "" : pct >= 50 ? "warn" : "bad";
function finalGrade(byLabel) {
  const [rk1, rk2, exam] = ["РК1", "РК2", "Сессия"].map(l => byLabel[l]);
  if (!rk1 || !rk2 || !exam) return null;
  const pct = g => g.score / g.max_score * 100;
  return round1((pct(rk1) + pct(rk2)) / 2 * 0.6 + pct(exam) * 0.4);
}
function gradeCell(g) {
  if (!g) return `<td class="na">—</td>`;
  const pct = g.score / g.max_score * 100;
  return `<td class="${scoreClass(pct)}" title="${esc(g.comment || "")}">${round1(g.score)}${g.max_score !== 100 ? `<small>/${g.max_score}</small>` : ""}</td>`;
}
const avgOf = list => list.length ? round1(list.reduce((a, x) => a + x, 0) / list.length) : null;

async function renderGradesTab(box) {
  try {
    const [{grades}, {courses}, {schedule}] = await Promise.all([api("/api/academics/grades"),
      api("/api/academics/my-courses"), api("/api/academics/schedule")]);
    if (!courses.length) { box.innerHTML = acadEmpty("Оценки появятся, когда запишешься на дисциплины."); return; }
    const stats = await Promise.all(courses.map(c => api(`/api/academics/attendance?course_id=${c.id}`).then(r => r.stats)
      .catch(() => ({total: 0, absent: 0, ratio: 0, on_retake: false}))));
    const teacherOf = Object.fromEntries(schedule.filter(i => i.teacher_name).map(i => [i.course_id, i.teacher_name]));
    const known = new Set(GRADE_COLUMNS.map(c => c[0]));
    const pctList = label => grades.filter(g => g.label === label).map(g => g.score / g.max_score * 100);
    const tk = avgOf([...pctList("ТК1"), ...pctList("ТК2")]), rk1 = avgOf(pctList("РК1"));

    const rows = courses.map(c => {
      const mine = grades.filter(g => g.course_id === c.id);
      const byLabel = Object.fromEntries(mine.map(g => [g.label, g]));
      const other = mine.filter(g => !known.has(g.label));
      const total = finalGrade(byLabel);
      return `<tr><th scope="row"><b>${esc(c.title)}</b>
          <small>${esc(teacherOf[c.id] || c.teacher_name || "преподаватель не назначен")} · ${esc(c.code)}</small>
          ${other.length ? `<span class="extra">${other.map(g => `<span class="grade-pill">${esc(g.label)}: ${round1(g.score)}/${g.max_score}</span>`).join("")}</span>` : ""}</th>
        ${GRADE_COLUMNS.map(([label]) => gradeCell(byLabel[label])).join("")}
        <td class="total ${total == null ? "na" : scoreClass(total)}">${total == null ? "—" : total}</td></tr>`;
    }).join("");

    const attendance = courses.map((c, idx) => stats[idx].total
      ? `<div class="card" style="margin-bottom:10px"><h3>${esc(c.title)}</h3>${attendanceBlock(stats[idx])}</div>` : "").join("");

    box.innerHTML = `<div class="card gradebook-card">
        <div class="row between" style="flex-wrap:wrap;gap:10px">
          <h3 style="margin:0">Успеваемость · осенний семестр 2026–2027</h3>
          <span class="row" style="gap:6px;flex-wrap:wrap">
            <span class="pill">дисциплин: ${courses.length}</span>
            ${tk != null ? `<span class="pill acc">средний текущий: ${tk}</span>` : ""}
            ${rk1 != null ? `<span class="pill acc">средний РК1: ${rk1}</span>` : ""}</span></div>
        <div class="gradebook-wrap"><table class="gradebook">
          <thead><tr><th scope="col">Дисциплина и преподаватель</th>
            ${GRADE_COLUMNS.map(([l, t]) => `<th scope="col" title="${t}">${l}</th>`).join("")}<th scope="col" title="Итоговая оценка">Итог</th></tr></thead>
          <tbody>${rows}</tbody></table></div>
        <p class="muted small" style="margin:10px 0 0">ТК — средний балл текущего контроля, РК — рубежный контроль.
          Итог = (РК1 + РК2) / 2 × 60% + Сессия × 40%. Он появится после второй рубежки и сессии.</p></div>
      ${attendance ? `<h3 style="margin:18px 0 10px">Посещаемость</h3>${attendance}` : ""}`;
  } catch (err) { box.innerHTML = acadError(err, "Не получилось загрузить оценки."); }
}

// ---------- мои дисциплины ----------
async function renderCoursesTab(box) {
  try {
    const {courses} = await api("/api/academics/my-courses");
    if (!courses.length) {
      box.innerHTML = acadEmpty(`Ты пока не записан(а) ни на одну дисциплину.
        <br><button type="button" class="btn sm" data-acad-tab="catalog">Открыть каталог</button>`);
      return;
    }
    box.innerHTML = courses.map(c => `<details class="course" data-course="${c.id}" data-lazy="my-course">
      <summary><span><b>${esc(c.code)} · ${esc(c.title)}</b><small>${c.teacher_name ? `Преподаватель: ${esc(c.teacher_name)}` : "Преподаватель не назначен"}</small></span></summary>
      <div class="body" data-teacher="${c.teacher_id ?? ""}">${acadEmpty("Загрузка…")}</div></details>`).join("");
    if (courses.length === 1) { const d = box.querySelector("details"); d.open = true; }
  } catch (err) { box.innerHTML = acadError(err, "Не получилось загрузить дисциплины."); }
}

function submissionStatus(sub) {
  if (!sub) return `<span class="pill warn">не сдано</span>`;
  if (sub.status === "graded") return `<span class="pill ok">оценено: ${sub.grade_score}</span>`;
  return `<span class="pill acc">сдано, ждёт проверки</span>`;
}

async function myAssignmentsHtml(courseId) {
  const {assignments} = await api(`/api/academics/courses/${courseId}/assignments`);
  if (!assignments.length) return `<p class="muted small" style="margin:0">Заданий пока нет.</p>`;
  const subs = await Promise.all(assignments.map(a => api(`/api/academics/my-submission?assignment_id=${a.id}`).then(r => r.submission)));
  const todayIso = new Date().toISOString().slice(0, 10);
  return assignments.map((a, i) => {
    const sub = subs[i];
    const overdue = a.due_at && a.due_at < todayIso && !sub;
    return `<div class="item"><div class="row between"><h6>${esc(a.title)}</h6><span class="row">
        ${a.due_at ? `<span class="pill ${overdue ? "bad" : ""}">${overdue ? "просрочено · " : "до "}${esc(fmtDate(a.due_at))}</span>` : ""}${submissionStatus(sub)}</span></div>
      ${a.description ? `<p>${esc(a.description)}</p>` : ""}
      ${fileButton("assignment", a.id, a.file_name)}
      ${sub && sub.status === "graded" && sub.grade_comment ? `<p style="margin-top:8px">Комментарий преподавателя: ${esc(sub.grade_comment)}</p>` : ""}
      ${sub && sub.file_name ? `<p class="small" style="margin-top:8px">Твой файл: ${fileButton("submission", sub.id, sub.file_name)}</p>` : ""}
      <form class="mini-form" data-form="submit-assignment" data-id="${a.id}">
        <textarea name="text" placeholder="Текст решения" maxlength="5000">${sub ? esc(sub.text) : ""}</textarea>
        <input type="file" name="file">
        <button class="btn sm" type="submit">${sub ? "Пересдать" : "Сдать"}</button>
        ${sub && sub.status === "graded" ? `<span class="muted small">При пересдаче оценка сбросится до новой проверки.</span>` : ""}
      </form></div>`;
  }).join("");
}

function embedVideo(url) {
  if (!url) return "";
  const yt = url.match(/(?:youtu\.be\/|youtube\.com\/(?:watch\?v=|embed\/|shorts\/))([\w-]{6,})/);
  if (yt) return `<iframe src="https://www.youtube-nocookie.com/embed/${yt[1]}" title="Видеоурок" style="width:100%;aspect-ratio:16/9;border:none;border-radius:12px;margin:8px 0" allowfullscreen loading="lazy"></iframe>`;
  if (!/^https?:\/\//i.test(url)) return "";  // не ссылка — не превращаем в кликабельную (защита от javascript:-адресов)
  return `<a class="btn ghost sm" href="${esc(url)}" target="_blank" rel="noopener noreferrer">🎬 Открыть видео</a>`;
}

function quizHtml(lessonId, questions, attempt) {
  if (!questions.length) return "";
  const summary = attempt ? `<p class="small" style="margin:10px 0 6px">Мини-тест пройден: <b>${attempt.correct_count} из ${attempt.total_count}</b> (${Math.round(attempt.score)}%)
    <button type="button" class="linkbtn" data-act="toggle-quiz" data-id="${lessonId}">пройти заново</button></p>` : "";
  return `${summary}<form data-form="take-quiz" data-id="${lessonId}" id="quiz-form-${lessonId}" class="box" ${attempt ? "hidden" : ""}>
    <p class="muted small" style="margin:0 0 10px">Мини-тест после урока — для самопроверки, проходить можно сколько угодно раз.</p>
    ${questions.map((q, i) => `<div class="q"><p>${i + 1}. ${esc(q.text)}</p>
      ${q.options.map((o, oi) => `<label><input type="radio" name="q${i}" value="${oi}" required> <span>${esc(o)}</span></label>`).join("")}</div>`).join("")}
    <button class="btn sm" type="submit" style="margin-top:10px">Проверить ответы</button></form>
    <div id="quiz-result-${lessonId}"></div>`;
}

async function myLessonsHtml(courseId) {
  const {lessons} = await api(`/api/academics/courses/${courseId}/lessons`);
  if (!lessons.length) return `<p class="muted small" style="margin:0">Видеоуроков пока нет.</p>`;
  const extra = await Promise.all(lessons.map(l => Promise.all([
    api(`/api/academics/lessons/${l.id}/quiz`), api(`/api/academics/quiz/my-attempt?lesson_id=${l.id}`)])));
  return lessons.map((l, i) => `<div class="item"><h6>${esc(l.title)}</h6>
    ${l.description ? `<p>${esc(l.description)}</p>` : ""}
    ${embedVideo(l.video_url)}
    ${fileButton("lesson", l.id, l.file_name)}
    ${quizHtml(l.id, extra[i][0].questions, extra[i][1].attempt)}</div>`).join("");
}

async function mySlotHtml(courseId) {
  const [{schedule}, {schedule: options}] = await Promise.all([
    api("/api/academics/schedule"), api(`/api/academics/courses/${courseId}/options`)]);
  const mine = schedule.find(i => i.course_id === courseId);
  if (!options.length) return `<p class="muted small" style="margin:0">Расписание по этой дисциплине ещё не составлено.</p>`;
  const current = mine ? `<span class="pill acc">${slotLabel(mine)} · ${esc(mine.kind)}</span>` : `<span class="pill warn">день не выбран</span>`;
  if (options.length === 1 && mine) return `<div class="row">${current}<span class="muted small">Эта дисциплина идёт только в один день.</span></div>`;
  return `<div class="row">${current}</div>
    <form class="mini-form" data-form="choose-slot">
      <select name="schedule_item_id" aria-label="День занятий">${options.map(o => `<option value="${o.id}" ${mine && mine.id === o.id ? "selected" : ""}>${slotLabel(o)} · ${esc(o.kind)}${o.teacher_name ? ` · ${esc(o.teacher_name)}` : ""}</option>`).join("")}</select>
      <button class="btn sm" type="submit">${mine ? "Сменить день" : "Выбрать день"}</button>
    </form>`;
}

async function loadMyCourse(details) {
  const courseId = Number(details.dataset.course);
  const body = details.querySelector(".body");
  const teacherId = Number(body.dataset.teacher) || null;
  try {
    const [slot, assignments, lessons, exams] = await Promise.all([
      mySlotHtml(courseId), myAssignmentsHtml(courseId), myLessonsHtml(courseId), myExamsHtml(courseId)]);
    body.innerHTML = `
      ${teacherId && teacherId !== chatUser.id ? `<button type="button" class="btn ghost sm" data-act="write-teacher" data-id="${teacherId}">✉️ Написать преподавателю</button>` : ""}
      <div class="sect"><h5>Мой день занятий</h5>${slot}</div>
      <div class="sect"><h5>Задания</h5><div data-part="assignments">${assignments}</div></div>
      <div class="sect"><h5>Видеоуроки</h5><div data-part="lessons">${lessons}</div></div>
      <div class="sect"><h5>Экзамены</h5><div data-part="exams">${exams}</div></div>
      <div class="sect"><button type="button" class="btn danger sm" data-act="unenroll">Отписаться от дисциплины</button></div>`;
  } catch (err) { body.innerHTML = acadError(err, "Не получилось загрузить дисциплину."); }
}

async function reloadMyCoursePart(el, part) {
  const details = el.closest("details.course");
  const courseId = Number(details.dataset.course);
  const box = details.querySelector(`[data-part="${part}"]`);
  const loaders = {assignments: myAssignmentsHtml, lessons: myLessonsHtml, exams: myExamsHtml};
  try { box.innerHTML = await loaders[part](courseId); } catch (err) { box.innerHTML = acadError(err, "Не получилось обновить."); }
}

// ---------- каталог ----------
async function renderCatalogTab(box) {
  try {
    const [{courses}, {courses: mine}] = await Promise.all([api("/api/academics/courses"), api("/api/academics/my-courses")]);
    const myIds = new Set(mine.map(c => c.id));
    // Сначала дисциплины, созданные преподавателями на платформе, потом каталог из расписания вуза.
    courses.sort((a, b) => (a.description === CATALOG_NOTE) - (b.description === CATALOG_NOTE));
    box.innerHTML = courses.length ? `<input class="input" type="search" data-catalog-filter placeholder="Найти дисциплину или преподавателя"
        aria-label="Поиск по каталогу" style="margin-bottom:12px">` + courses.map(c => `<div class="course-card" data-course="${c.id}" data-search="${esc(`${c.code} ${c.title} ${c.teacher_name || ""}`.toLowerCase())}">
      <div class="row between"><h4>${esc(c.code)} · ${esc(c.title)}</h4>${myIds.has(c.id) ? `<span class="pill ok">ты записан(а)</span>` : ""}</div>
      <p class="meta">${c.teacher_name ? `Преподаватель: ${esc(c.teacher_name)}` : "Преподаватель не назначен"}</p>
      ${c.description && c.description !== CATALOG_NOTE ? `<p class="meta">${esc(c.description)}</p>` : ""}
      <div class="actions" data-enroll-box>${myIds.has(c.id) ? "" : `<button type="button" class="btn sm" data-act="enroll">Записаться</button>`}</div></div>`).join("")
      : acadEmpty("Дисциплин пока нет — их создают преподаватели.");
    box.querySelector("[data-catalog-filter]")?.addEventListener("input", e => {
      const q = e.target.value.trim().toLowerCase();
      box.querySelectorAll(".course-card").forEach(card => { card.hidden = q !== "" && !card.dataset.search.includes(q); });
    });
  } catch (err) { box.innerHTML = acadError(err, "Не получилось загрузить каталог."); }
}

// Если у дисциплины несколько вариантов расписания — сначала выбор дня, иначе сразу запись.
async function startEnroll(btn) {
  const card = btn.closest("[data-course]");
  const courseId = Number(card.dataset.course);
  btn.disabled = true;
  try {
    const {schedule: options} = await api(`/api/academics/courses/${courseId}/options`);
    if (options.length <= 1) {
      await api("/api/academics/enroll", {method: "POST", body: {course_id: courseId}});
      toast("Ты записан(а) на дисциплину.", "ok");
      showAcadTab("courses");
      return;
    }
    card.querySelector("[data-enroll-box]").innerHTML = `<form class="mini-form" data-form="enroll-choice" style="border:none;padding:0;margin:0">
      <span class="small muted">Эта дисциплина идёт в разные дни — выбери свой:</span>
      <select name="schedule_item_id">${options.map(o => `<option value="${o.id}">${slotLabel(o)} · ${esc(o.kind)}${o.teacher_name ? ` · ${esc(o.teacher_name)}` : ""}</option>`).join("")}</select>
      <button class="btn sm" type="submit">Записаться</button></form>`;
  } catch (err) { fail(err, "Не получилось записаться."); btn.disabled = false; }
}

// ---------- вкладки ----------
const ACAD_TABS = {
  schedule: ["🗓 Расписание", renderScheduleTab], grades: ["📊 Оценки", renderGradesTab],
  courses: ["📚 Мои дисциплины", renderCoursesTab], catalog: ["➕ Каталог", renderCatalogTab],
  teaching: ["👩‍🏫 Преподавание", box => renderTeachingTab(box)],
};
function availableAcadTabs() {
  const tabs = isStudentLike() ? ["schedule", "grades", "courses", "catalog"] : [];
  if (isStaff()) tabs.push("teaching");
  return tabs;
}

function showAcadTab(tab) {
  const tabs = availableAcadTabs();
  acadTab = tabs.includes(tab) ? tab : tabs[0];
  ACAD_TABS.teaching[0] = hasRole("registrar") ? "🗓 Расписание дисциплин" : "👩‍🏫 Преподавание";
  $("acad-tabs").innerHTML = tabs.map(t => `<button type="button" class="${t === acadTab ? "on" : ""}" data-acad-tab="${t}">${ACAD_TABS[t][0]}</button>`).join("");
  $("acad-tabs").hidden = tabs.length < 2;
  const box = $("acad-body");
  box.innerHTML = acadEmpty("Загрузка…");
  ACAD_TABS[acadTab][1](box);
}

// ---------- действия ----------
const DOWNLOAD_PATHS = {
  assignment: id => `/api/academics/assignments/${id}/file`, submission: id => `/api/academics/submissions/${id}/file`,
  lesson: id => `/api/academics/lessons/${id}/file`, recording: id => `/api/academics/exam-attempts/${id}/recording`,
};

const ACAD_ACTIONS = {
  download: el => downloadFile(DOWNLOAD_PATHS[el.dataset.kind](el.dataset.id), el.dataset.filename),
  enroll: el => startEnroll(el),
  "write-teacher": el => openDirectWith(Number(el.dataset.id)),
  "toggle-quiz": el => { const form = $(`quiz-form-${el.dataset.id}`); if (form) form.hidden = !form.hidden; },
  async unenroll(el) {
    if (!await askConfirm("Дисциплина пропадёт из расписания. Оценки и сданные работы сохранятся у преподавателя.",
      {title: "Отписаться от дисциплины?", ok: "Отписаться", danger: true})) return;
    try {
      await api("/api/academics/unenroll", {method: "POST", body: {course_id: courseIdOf(el)}});
      showAcadTab("courses");
    } catch (err) { fail(err, "Не получилось отписаться."); }
  },
};

const ACAD_FORMS = {
  async "enroll-choice"(form) {
    const body = {course_id: courseIdOf(form), schedule_item_id: Number(form.elements.schedule_item_id.value)};
    await api("/api/academics/enroll", {method: "POST", body});
    toast("Ты записан(а) на дисциплину.", "ok");
    showAcadTab("courses");
  },
  async "choose-slot"(form) {
    await api("/api/academics/schedule/choose", {method: "POST", body: {
      course_id: courseIdOf(form), schedule_item_id: Number(form.elements.schedule_item_id.value)}});
    toast("День занятий изменён.", "ok");
    loadMyCourse(form.closest("details.course"));
  },
  async "submit-assignment"(form) {
    const body = {assignment_id: Number(form.dataset.id), text: form.elements.text.value};
    const file = await fileFieldToPayload(form.elements.file);
    if (file) body.file = file;
    if (!body.text.trim() && !file) { toast("Добавь текст решения или файл."); return; }
    await api("/api/academics/submissions", {method: "POST", body});
    toast("Работа отправлена преподавателю.", "ok");
    reloadMyCoursePart(form, "assignments");
  },
  async "take-quiz"(form) {
    const lessonId = Number(form.dataset.id);
    const fd = new FormData(form), answers = [];
    for (let i = 0; fd.has(`q${i}`); i++) answers.push(Number(fd.get(`q${i}`)));
    const r = await api("/api/academics/quiz/submit", {method: "POST", body: {lesson_id: lessonId, answers}});
    form.hidden = true;
    $(`quiz-result-${lessonId}`).innerHTML = `<div class="box"><p style="margin:0 0 10px"><b>Результат: ${r.correct_count} из ${r.total_count} (${Math.round(r.score)}%)</b></p>
      ${r.results.map((q, i) => `<div class="q"><p>${i + 1}. ${esc(q.text)}</p>
        <span class="${q.is_correct ? "right" : "wrong"}">${q.is_correct ? "✓ Верно" : "✗ Неверно"}${q.selected != null && q.options[q.selected] != null ? `: ${esc(q.options[q.selected])}` : ""}</span>
        ${q.is_correct ? "" : `<br><span class="muted small">Правильный ответ: ${esc(q.options[q.correct_index])}</span>`}</div>`).join("")}
      <button type="button" class="btn ghost sm" data-act="toggle-quiz" data-id="${lessonId}" style="margin-top:10px">Пройти заново</button></div>`;
  },
};

const ACAD_FORM_ERRORS = {
  400: "Проверь заполнение формы. Файл — до 10 МБ: PDF, Word, Excel, PowerPoint, ZIP, текст или картинка.",
  403: "Недостаточно прав для этого действия.", 404: "Не найдено — возможно, уже удалено.",
  409: "Действие сейчас недоступно.", 413: "Файл слишком большой — до 10 МБ.",
};

function enterAcademics() {
  showAcadTab(acadTab);
}

function initAcademics() {
  definePage("academics", {auth: true, enter: enterAcademics});
  sessionListeners.push(() => { acadTab = null; });

  document.addEventListener("click", e => {
    const tabBtn = e.target.closest("[data-acad-tab]");
    if (tabBtn) {
      acadTab = tabBtn.dataset.acadTab;
      // Ссылка с другой страницы сама сменит адрес и вызовет enter; на своей странице — переключаем сразу
      if (currentPage === "academics") showAcadTab(acadTab);
      return;
    }
    const actEl = e.target.closest("[data-act]");
    if (actEl) {
      const handler = ACAD_ACTIONS[actEl.dataset.act] || (typeof TEACH_ACTIONS !== "undefined" && TEACH_ACTIONS[actEl.dataset.act])
        || (typeof EXAM_ACTIONS !== "undefined" && EXAM_ACTIONS[actEl.dataset.act]);
      if (handler) handler(actEl, e);
    }
  });

  document.addEventListener("submit", async e => {
    const form = e.target.closest("[data-form]");
    if (!form) return;
    const handler = ACAD_FORMS[form.dataset.form] || (typeof TEACH_FORMS !== "undefined" && TEACH_FORMS[form.dataset.form]);
    if (!handler) return;
    e.preventDefault();
    const btn = form.querySelector("button[type=submit]");
    if (btn) btn.disabled = true;
    try {
      await handler(form);
    } catch (err) {
      fail(err, "Не получилось. Попробуйте ещё раз.", {...ACAD_FORM_ERRORS, ...(form._errors || {})});
    } finally {
      if (btn && btn.isConnected) btn.disabled = false;
    }
  });

  // Содержимое карточки дисциплины грузится при первом раскрытии
  document.addEventListener("toggle", e => {
    const details = e.target;
    if (!(details instanceof HTMLDetailsElement) || !details.open || details.dataset.loaded) return;
    if (details.dataset.lazy === "my-course") { details.dataset.loaded = "1"; loadMyCourse(details); }
    if (details.dataset.lazy === "teaching-course") { details.dataset.loaded = "1"; loadTeachingCourse(details); }
  }, true);
}
