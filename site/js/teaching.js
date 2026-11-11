"use strict";
// Учёба, сторона сотрудника. Преподаватель: расписание своих дисциплин, посещаемость, журнал оценок,
// задания и проверка сдач, видеоуроки с тестами, экзамены и их результаты. Отдел расписания: расписание
// всех дисциплин, назначение преподавателей, перевод студентов между днями. Админ — всё сразу.

let teachCaps = {role: "", can_grade: false, can_assign_teachers: false};
let teachTeachers = null;  // список преподавателей для выпадающих списков (только у отдела расписания/админа)

const sub = (title, extra = "") => `<h5>${title}${extra}</h5>`;
const todayIso = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };

function teacherSelect(name, selectedId, emptyLabel, attrs = "") {
  return `<select name="${name}" ${attrs} aria-label="Преподаватель"><option value="">${emptyLabel}</option>${(teachTeachers || []).map(t =>
    `<option value="${t.id}" ${t.id === selectedId ? "selected" : ""}>${esc(fullName(t))}</option>`).join("")}</select>`;
}

async function renderTeachingTab(box) {
  try {
    const data = await api("/api/academics/teaching");
    teachCaps = data;
    if (data.can_assign_teachers) teachTeachers = (await api("/api/academics/teachers")).teachers;
    const createForm = data.can_grade ? `<details class="course"><summary><span><b>➕ Новая дисциплина</b></span></summary><div class="body">
      <form class="form" data-form="create-course">
        <div class="cols"><label>Код<input name="code" placeholder="CS101" required maxlength="20"></label>
          <label>Название<input name="title" required maxlength="200"></label></div>
        <label>Описание<textarea name="description" maxlength="2000" placeholder="Необязательно"></textarea></label>
        <div><button class="btn" type="submit">Создать дисциплину</button></div></form></div></details>` : "";
    const intro = data.role === "registrar"
      ? `<p class="muted" style="margin:0 0 16px">Здесь расписание всех дисциплин: дни и время пар, кто их ведёт и в какой день учится каждый студент.</p>` : "";
    const list = data.courses.length ? data.courses.map(c => `<details class="course" data-course="${c.id}" data-lazy="teaching-course">
      <summary><span><b>${esc(c.code)} · ${esc(c.title)}</b><small>${c.teacher_name ? `Ведёт: ${esc(c.teacher_name)}` : "Преподаватель не назначен"}</small></span></summary>
      <div class="body">${acadEmpty("Загрузка…")}</div></details>`).join("")
      : acadEmpty(data.can_grade ? "Дисциплин пока нет — создай первую." : "Дисциплин пока нет — их создают преподаватели.");
    box.innerHTML = intro + createForm + list;
  } catch (err) {
    box.innerHTML = acadError(err, "Не получилось загрузить дисциплины.");
  }
}

// ---------- расписание и посещаемость ----------
function scheduleSection(course, schedule) {
  const rows = schedule.length ? schedule.map(i => `<div class="item" data-slot="${i.id}">
      <div class="row between"><span><b>${slotLabel(i)}</b> · ${esc(i.kind)}<br><small class="muted">Ведёт: ${esc(i.teacher_name || "не назначен")}</small></span>
        <span class="row">
          ${teachCaps.can_assign_teachers ? teacherSelect("teacher_id", i.teacher_id, "как у дисциплины", 'data-change="slot-teacher"') : ""}
          ${teachCaps.can_grade ? `<button type="button" class="btn ghost sm" data-act="toggle-attendance">Посещаемость</button>` : ""}
          <button type="button" class="btn danger sm" data-act="delete-slot">Удалить</button></span></div>
      <div class="box" data-box="attendance" hidden></div></div>`).join("")
    : `<p class="muted small" style="margin:0 0 8px">Пар пока нет. Добавь один вариант — или несколько в разные дни, тогда студент сам выберет свой.</p>`;
  return `<div class="sect">${sub("Расписание")}${rows}
    <form class="mini-form" data-form="add-slot">
      <select name="day_of_week" aria-label="День">${WEEKDAY_NAMES.map((d, i) => `<option value="${i}">${d}</option>`).join("")}</select>
      <input name="start_time" type="time" required aria-label="Начало"><input name="end_time" type="time" required aria-label="Конец">
      <input name="room" placeholder="Ауд." maxlength="40" style="width:90px">
      <select name="kind" aria-label="Тип">${SCHEDULE_KINDS.map(k => `<option>${k}</option>`).join("")}</select>
      ${teachCaps.can_assign_teachers ? teacherSelect("teacher_id", null, "ведёт преподаватель дисциплины") : ""}
      <button class="btn sm" type="submit">+ Пара</button></form></div>`;
}

function attendanceHtml(students, date) {
  if (!students.length) return `<p class="muted small" style="margin:0">Этот день пока никто не выбрал.</p>`;
  return `<div class="row" style="margin-bottom:10px"><label class="small muted">Дата пары
      <input type="date" class="input" data-change="attendance-date" value="${esc(date)}" max="${todayIso()}" style="width:170px;padding:7px 10px"></label>
    <span class="muted small">Можно отметить и прошлые пары — или снять ошибочную отметку.</span></div>
    ${students.map(s => {
      const record = s.records.find(r => r.class_date === date);
      const st = s.stats;
      const absences = s.records.filter(r => !r.present).map(r => fmtDate(r.class_date));
      return `<div class="att-row" data-student="${s.id}">
        <span><b>${esc(fullName(s))}</b>${st.on_retake ? ` <span class="pill bad">ретейк</span>` : ""}
          <br><small class="muted">${st.total ? `пропущено ${st.absent} из ${st.total} (${Math.round(st.ratio * 100)}%)` : "отметок нет"}${absences.length ? ` · ${esc(absences.join(", "))}` : ""}</small></span>
        <span class="row"><span class="seg">
          <button type="button" class="yes ${record && record.present ? "on" : ""}" data-act="mark-attendance" data-present="1">Был(а)</button>
          <button type="button" class="no ${record && !record.present ? "on" : ""}" data-act="mark-attendance" data-present="0">Не был(а)</button></span>
          ${record ? `<button type="button" class="linkbtn small" data-act="clear-attendance">снять</button>` : ""}</span></div>`;
    }).join("")}`;
}

async function loadAttendance(box, date) {
  const slotId = Number(box.closest("[data-slot]").dataset.slot);
  box._date = date || box._date || todayIso();
  try {
    const {students} = await api(`/api/academics/schedule-items/${slotId}/attendance`);
    box.innerHTML = attendanceHtml(students, box._date);
  } catch (err) {
    box.innerHTML = `<p class="muted small" style="margin:0">${esc(explain(err, "Не получилось загрузить посещаемость.",
      {403: "Посещаемость отмечает тот, кто ведёт эту пару."}))}</p>`;
  }
}

// ---------- студенты и журнал ----------
function rosterSection(course, roster, schedule, grades) {
  const rows = roster.length ? roster.map(s => {
    const mine = grades.filter(g => g.user_id === s.id);
    const slotSelect = schedule.length > 1 || (schedule.length === 1 && s.schedule_item_id == null)
      ? `<select data-change="move-student" aria-label="День занятий студента">${s.schedule_item_id == null ? `<option value="">день не выбран</option>` : ""}${schedule.map(i =>
          `<option value="${i.id}" ${i.id === s.schedule_item_id ? "selected" : ""}>${slotLabel(i)}</option>`).join("")}</select>` : "";
    const journal = teachCaps.can_grade ? `
      <div style="margin-top:8px">${mine.length ? mine.map(g => `<span class="grade-pill" title="${esc(g.comment || "")}">${esc(g.label)}: ${g.score}/${g.max_score}</span>`).join("")
        : `<span class="muted small">Оценок нет</span>`}</div>
      <form class="mini-form" data-form="set-grade">
        <input name="label" placeholder="РК1" maxlength="40" required style="width:100px" list="grade-labels">
        <input name="score" type="number" step="0.1" min="0" placeholder="Балл" required>
        <input name="max_score" type="number" step="0.1" min="1" value="100" aria-label="Максимум">
        <input name="comment" placeholder="Комментарий" maxlength="500">
        <button class="btn sm" type="submit">Поставить</button></form>` : "";
    return `<div class="item" data-student="${s.id}"><div class="row between">
        <span><b>${esc(fullName(s))}</b><br><small class="muted">${esc([courseLabel(s.course), s.specialty].filter(Boolean).join(" · "))}</small></span>
        <span class="row">${slotSelect}<button type="button" class="btn ghost sm" data-act="write-teacher" data-id="${s.id}">✉️</button></span></div>${journal}</div>`;
  }).join("") : `<p class="muted small" style="margin:0">Пока никто не записался.</p>`;
  return `<div class="sect">${sub(`Студенты · ${roster.length}`)}${rows}
    <datalist id="grade-labels"><option value="РК1"><option value="РК2"><option value="Экзамен"><option value="Итог"></datalist>
    ${teachCaps.can_grade && roster.length ? `<p class="muted small" style="margin:8px 0 0">Оценка с тем же названием (например, «РК1») заменяет прежнюю.</p>` : ""}</div>`;
}

// ---------- задания ----------
function assignmentsSection(assignments) {
  const rows = assignments.length ? assignments.map(a => `<div class="item" data-assignment="${a.id}">
      <div class="row between"><span><b>${esc(a.title)}</b>${a.due_at ? ` <span class="pill">до ${esc(fmtDate(a.due_at))}</span>` : ""}
          ${a.file_name ? `<br><small class="muted">📎 ${esc(a.file_name)}</small>` : ""}</span>
        <span class="row"><button type="button" class="btn ghost sm" data-act="toggle-submissions">Сдачи</button>
          <button type="button" class="btn danger sm" data-act="delete-assignment">Удалить</button></span></div>
      <div class="box" data-box="submissions" hidden></div></div>`).join("")
    : `<p class="muted small" style="margin:0 0 8px">Заданий пока нет.</p>`;
  return `<div class="sect">${sub("Задания")}${rows}
    <form class="mini-form" data-form="create-assignment">
      <input name="title" placeholder="Название задания" maxlength="200" required>
      <textarea name="description" placeholder="Что нужно сделать (необязательно)" maxlength="2000"></textarea>
      <label class="small muted">Срок <input name="due_at" type="date"></label>
      <input type="file" name="file">
      <button class="btn sm" type="submit">+ Задание</button></form></div>`;
}

function submissionsHtml(submissions) {
  if (!submissions.length) return `<p class="muted small" style="margin:0">Пока никто не сдал.</p>`;
  return submissions.map(s => `<div class="q" data-submission="${s.id}">
    <div class="row between"><b>${esc(s.first_name)} ${esc(s.last_name)}</b>
      <span class="row">${s.status === "graded" ? `<span class="pill ok">оценено: ${s.grade_score}</span>` : `<span class="pill warn">ждёт проверки</span>`}
        <small class="muted">${esc(fmtWhen(s.submitted_at))}</small></span></div>
    ${s.text ? `<p style="font-weight:400;white-space:pre-wrap;overflow-wrap:anywhere;margin-top:6px">${esc(s.text)}</p>` : ""}
    ${fileButton("submission", s.id, s.file_name)}
    <form class="mini-form" data-form="grade-submission" style="border:none;padding:0">
      <input name="score" type="number" step="0.1" min="0" placeholder="Балл" required value="${s.grade_score ?? ""}">
      <input name="comment" placeholder="Комментарий студенту" maxlength="500" value="${esc(s.grade_comment || "")}">
      <button class="btn sm" type="submit">${s.status === "graded" ? "Изменить оценку" : "Оценить"}</button></form></div>`).join("");
}

async function loadSubmissions(box) {
  const id = Number(box.closest("[data-assignment]").dataset.assignment);
  try { box.innerHTML = submissionsHtml((await api(`/api/academics/assignments/${id}/submissions`)).submissions); }
  catch (err) { box.innerHTML = `<p class="muted small" style="margin:0">${esc(explain(err, "Не получилось загрузить сдачи."))}</p>`; }
}

// ---------- видеоуроки и экзамены ----------
function lessonsSection(lessons) {
  const rows = lessons.length ? lessons.map(l => `<div class="item" data-lesson="${l.id}">
      <div class="row between"><span><b>${esc(l.title)}</b><br><small class="muted">${[l.video_url ? "🎬 видео" : "", l.file_name ? `📎 ${esc(l.file_name)}` : ""].filter(Boolean).join(" · ") || "без материалов"}</small></span>
        <span class="row"><button type="button" class="btn ghost sm" data-act="toggle-questions" data-kind="quiz">Мини-тест</button>
          <button type="button" class="btn ghost sm" data-act="toggle-quiz-results">Результаты</button>
          <button type="button" class="btn danger sm" data-act="delete-lesson">Удалить</button></span></div>
      <div class="box" data-box="questions" hidden></div><div class="box" data-box="results" hidden></div></div>`).join("")
    : `<p class="muted small" style="margin:0 0 8px">Видеоуроков пока нет.</p>`;
  return `<div class="sect">${sub("Видеоуроки")}${rows}
    <form class="mini-form" data-form="create-lesson">
      <input name="title" placeholder="Название урока" maxlength="200" required>
      <textarea name="description" placeholder="Конспект или пояснение (необязательно)" maxlength="2000"></textarea>
      <input name="video_url" type="url" placeholder="Ссылка на видео (YouTube и т.п.)" style="flex:1;min-width:220px">
      <input type="file" name="file">
      <button class="btn sm" type="submit">+ Урок</button></form>
    <p class="muted small" style="margin:8px 0 0">Файл — конспект в Word/PDF или презентация. После урока можно добавить мини-тест.</p></div>`;
}

function examsSection(exams) {
  const rows = exams.length ? exams.map(x => `<div class="item" data-exam="${x.id}">
      <div class="row between"><span><b>${esc(x.title)}</b><br><span class="row" style="margin-top:4px">
          ${x.time_limit_minutes ? `<span class="pill">⏱ ${x.time_limit_minutes} мин</span>` : `<span class="pill">без лимита</span>`}
          ${x.require_recording ? `<span class="pill acc">🎥 запись экрана</span>` : ""}${x.require_camera ? `<span class="pill acc">📷 снимки камеры</span>` : ""}</span></span>
        <span class="row"><button type="button" class="btn ghost sm" data-act="toggle-questions" data-kind="exam">Вопросы</button>
          <button type="button" class="btn ghost sm" data-act="toggle-exam-results">Результаты</button>
          <button type="button" class="btn danger sm" data-act="delete-exam">Удалить</button></span></div>
      <div class="box" data-box="questions" hidden></div><div class="box" data-box="results" hidden></div></div>`).join("")
    : `<p class="muted small" style="margin:0 0 8px">Экзаменов пока нет.</p>`;
  return `<div class="sect">${sub("Экзамены и рубежные контроли")}${rows}
    <form class="mini-form" data-form="create-exam">
      <input name="title" placeholder="Название (например, РК1)" maxlength="200" required>
      <textarea name="description" placeholder="Правила или пояснение (необязательно)" maxlength="2000"></textarea>
      <label class="small muted">Лимит, мин <input name="time_limit_minutes" type="number" min="1" max="600" style="width:90px"></label>
      <label class="row small" style="gap:6px"><input type="checkbox" name="require_recording"> запись экрана</label>
      <label class="row small" style="gap:6px"><input type="checkbox" name="require_camera"> снимки с веб-камеры</label>
      <button class="btn sm" type="submit">+ Экзамен</button></form>
    <p class="muted small" style="margin:8px 0 0">Экзамен сдаётся один раз, выходы из окна считаются всегда. Запись и снимки хранятся 90 дней.</p></div>`;
}

// Редактор вопросов — общий для мини-теста урока (kind="quiz") и экзамена (kind="exam").
function optionRow(index, checked = false) {
  return `<div class="opt-row"><input type="radio" name="correct" value="${index}" ${checked ? "checked" : ""} title="Правильный ответ" aria-label="Правильный ответ">
    <input type="text" name="option" placeholder="Вариант ${index + 1}" maxlength="200"></div>`;
}
function questionsHtml(questions, kind) {
  const list = questions.length ? questions.map((q, i) => `<div class="q" data-question="${q.id}">
      <div class="row between"><p>${i + 1}. ${esc(q.text)}</p><button type="button" class="linkbtn small" data-act="delete-question" data-kind="${kind}">удалить</button></div>
      ${q.options.map((o, oi) => `<span class="pill ${oi === q.correct_index ? "ok" : ""}" style="margin:0 4px 4px 0">${oi === q.correct_index ? "✓ " : ""}${esc(o)}</span>`).join("")}</div>`).join("")
    : `<p class="muted small" style="margin:0 0 8px">Вопросов пока нет.</p>`;
  return `${list}<form class="form" data-form="add-question" data-kind="${kind}" style="margin-top:12px">
    <label>Новый вопрос<input name="text" maxlength="500" required></label>
    <div><span class="small muted">Варианты ответа — отметь кружком правильный:</span>
      <div data-options style="margin-top:6px">${optionRow(0, true)}${optionRow(1)}${optionRow(2)}</div>
      <button type="button" class="linkbtn small" data-act="add-option">+ ещё вариант</button></div>
    <div><button class="btn sm" type="submit">Добавить вопрос</button></div></form>`;
}

function questionsOwner(el) {
  const lesson = el.closest("[data-lesson]"), exam = el.closest("[data-exam]");
  return exam ? {kind: "exam", id: Number(exam.dataset.exam), item: exam} : {kind: "quiz", id: Number(lesson.dataset.lesson), item: lesson};
}
async function loadQuestions(box) {
  const {kind, id} = questionsOwner(box);
  const path = kind === "exam" ? `/api/academics/exams/${id}/questions` : `/api/academics/lessons/${id}/quiz`;
  try { box.innerHTML = questionsHtml((await api(path)).questions, kind); }
  catch (err) { box.innerHTML = `<p class="muted small" style="margin:0">${esc(explain(err, "Не получилось загрузить вопросы."))}</p>`; }
}

async function loadQuizResults(box) {
  const id = Number(box.closest("[data-lesson]").dataset.lesson);
  try {
    const {attempts} = await api(`/api/academics/lessons/${id}/attempts`);
    box.innerHTML = attempts.length ? attempts.map(a => `<div class="line"><span>${esc(a.first_name)} ${esc(a.last_name)}<small>${esc(fmtWhen(a.submitted_at))}</small></span>
      <b>${a.correct_count} из ${a.total_count} · ${Math.round(a.score)}%</b></div>`).join("")
      : `<p class="muted small" style="margin:0">Пока никто не проходил тест.</p>`;
  } catch (err) { box.innerHTML = `<p class="muted small" style="margin:0">${esc(explain(err, "Не получилось загрузить результаты."))}</p>`; }
}

async function loadExamResults(box) {
  const id = Number(box.closest("[data-exam]").dataset.exam);
  try {
    const {attempts, unfinished} = await api(`/api/academics/exams/${id}/attempts`);
    const done = attempts.map(a => `<div class="q" data-user="${a.user_id}">
      <div class="row between"><b>${esc(a.first_name)} ${esc(a.last_name)}</b><b>${a.correct_count} из ${a.total_count} · ${Math.round(a.score)}%</b></div>
      <div class="row" style="margin-top:6px">
        <span class="pill ${a.tab_exit_count ? "warn" : "ok"}">выходов из окна: ${a.tab_exit_count}</span>
        ${a.away_seconds ? `<span class="pill warn">вне окна: ${fmtDuration(a.away_seconds)}</span>` : ""}
        ${a.duration_seconds != null ? `<span class="pill ${a.late ? "bad" : ""}">${a.late ? "сдано после лимита · " : "длительность: "}${fmtDuration(a.duration_seconds)}</span>` : ""}
        <small class="muted">${esc(fmtWhen(a.submitted_at))}</small></div>
      <div class="actions" style="margin-top:8px">
        ${a.has_recording ? `<button type="button" class="btn ghost sm" data-act="download" data-kind="recording" data-id="${a.id}" data-filename="exam-${a.id}.webm">🎥 Запись экрана</button>` : ""}
        ${a.snapshot_count ? `<button type="button" class="btn ghost sm" data-act="show-snapshots" data-id="${a.id}" data-name="${esc(a.first_name + " " + a.last_name)}">📷 Снимки (${a.snapshot_count})</button>` : ""}
        <button type="button" class="btn danger sm" data-act="reset-attempt">Разрешить пересдачу</button></div></div>`).join("");
    const pending = unfinished.map(u => `<div class="q" data-user="${u.user_id}"><div class="row between">
      <span><b>${esc(u.first_name)} ${esc(u.last_name)}</b> <span class="pill warn">начал(а) ${esc(fmtWhen(u.started_at))}, не сдал(а)</span></span>
      <button type="button" class="btn danger sm" data-act="reset-attempt">Сбросить</button></div></div>`).join("");
    box.innerHTML = (done + pending) || `<p class="muted small" style="margin:0">Пока никто не сдавал.</p>`;
  } catch (err) { box.innerHTML = `<p class="muted small" style="margin:0">${esc(explain(err, "Не получилось загрузить результаты."))}</p>`; }
}

async function showSnapshots(attemptId, name) {
  try {
    const {snapshots} = await api(`/api/academics/exam-attempts/${attemptId}/snapshots`);
    const {dialog} = openModal(`<h3>Снимки камеры · ${esc(name)}</h3>
      <div class="shots">${snapshots.map(s => `<figure><img data-snap="${s.id}" alt="Кадр с веб-камеры"><figcaption>${esc(fmtWhen(s.taken_at))}</figcaption></figure>`).join("")}</div>
      <div class="actions" style="margin-top:16px"><button type="button" class="btn ghost" data-close="">Закрыть</button></div>`, {wide: true});
    dialog.querySelectorAll("img[data-snap]").forEach(img => loadAuthedImage(img, `/api/academics/exam-snapshots/${img.dataset.snap}`));
  } catch (err) { fail(err, "Не получилось загрузить снимки."); }
}

// ---------- настройки дисциплины ----------
function settingsSection(course) {
  const edit = teachCaps.can_grade ? `<form class="form" data-form="update-course">
      <div class="cols"><label>Код<input name="code" required maxlength="20" value="${esc(course.code)}"></label>
        <label>Название<input name="title" required maxlength="200" value="${esc(course.title)}"></label></div>
      <label>Описание<textarea name="description" maxlength="2000">${esc(course.description || "")}</textarea></label>
      <div class="row"><button class="btn sm" type="submit">Сохранить</button>
        <button type="button" class="btn danger sm" data-act="delete-course">Удалить дисциплину</button></div></form>` : "";
  const assign = teachCaps.can_assign_teachers ? `<div class="row" style="margin-top:12px"><span class="small muted">Преподаватель дисциплины:</span>
      ${teacherSelect("teacher_id", course.teacher_id, "не назначен", 'data-change="course-teacher"')}</div>` : "";
  return `<div class="sect">${sub("Настройки")}${edit}${assign}</div>`;
}

async function loadTeachingCourse(details) {
  const courseId = Number(details.dataset.course);
  const body = details.querySelector(".body");
  try {
    const empty = key => Promise.resolve({[key]: []});
    const [{courses}, {students}, {schedule}, {assignments}, {lessons}, {exams}, {grades}] = await Promise.all([
      api("/api/academics/courses"),
      api(`/api/academics/courses/${courseId}/roster`),
      api(`/api/academics/courses/${courseId}/schedule`),
      teachCaps.can_grade ? api(`/api/academics/courses/${courseId}/assignments`) : empty("assignments"),
      teachCaps.can_grade ? api(`/api/academics/courses/${courseId}/lessons`) : empty("lessons"),
      teachCaps.can_grade ? api(`/api/academics/courses/${courseId}/exams`) : empty("exams"),
      teachCaps.can_grade ? api(`/api/academics/courses/${courseId}/grades`) : empty("grades"),
    ]);
    const course = courses.find(c => c.id === courseId);
    if (!course) { details.remove(); return; }
    details.querySelector("summary span").innerHTML = `<b>${esc(course.code)} · ${esc(course.title)}</b><small>${course.teacher_name ? `Ведёт: ${esc(course.teacher_name)}` : "Преподаватель не назначен"}</small>`;
    body.innerHTML = scheduleSection(course, schedule) + rosterSection(course, students, schedule, grades)
      + (teachCaps.can_grade ? assignmentsSection(assignments) + lessonsSection(lessons) + examsSection(exams) : "")
      + settingsSection(course);
  } catch (err) { body.innerHTML = acadError(err, "Не получилось загрузить дисциплину."); }
}
const reloadCourseOf = el => loadTeachingCourse(el.closest("details.course"));

// Раскрывает/сворачивает вложенный блок и загружает его содержимое при открытии.
function toggleBox(el, name, loader) {
  const box = el.closest(".item").querySelector(`[data-box="${name}"]`);
  box.hidden = !box.hidden;
  if (!box.hidden) { box.innerHTML = `<p class="muted small" style="margin:0">Загрузка…</p>`; loader(box); }
}

async function confirmDelete(what) {
  return askConfirm(`${what} Это нельзя отменить.`, {title: "Удалить?", ok: "Удалить", danger: true});
}
async function postThenReload(el, path, body, fallback) {
  try { await api(path, {method: "POST", body}); reloadCourseOf(el); } catch (err) { fail(err, fallback); }
}

const TEACH_ACTIONS = {
  "toggle-attendance": el => toggleBox(el, "attendance", box => loadAttendance(box)),
  "toggle-submissions": el => toggleBox(el, "submissions", loadSubmissions),
  "toggle-questions": el => toggleBox(el, "questions", loadQuestions),
  "toggle-quiz-results": el => toggleBox(el, "results", loadQuizResults),
  "toggle-exam-results": el => toggleBox(el, "results", loadExamResults),
  "show-snapshots": el => showSnapshots(Number(el.dataset.id), el.dataset.name),
  "add-option"(el) {
    const box = el.parentElement.querySelector("[data-options]");
    const count = box.querySelectorAll(".opt-row").length;
    if (count >= 8) { toast("Максимум 8 вариантов."); return; }
    box.insertAdjacentHTML("beforeend", optionRow(count));
  },
  async "mark-attendance"(el) {
    const box = el.closest('[data-box="attendance"]');
    const body = {schedule_item_id: Number(el.closest("[data-slot]").dataset.slot),
      user_id: Number(el.closest("[data-student]").dataset.student), class_date: box._date, present: el.dataset.present === "1"};
    try { await api("/api/academics/attendance/mark", {method: "POST", body}); }
    catch (err) {
      fail(err, "Не получилось отметить.", {409: "Студент уже на ретейке (больше 30% пропусков) — новую отметку поставить нельзя. Прошлые отметки можно исправить.",
        403: "Посещаемость отмечает тот, кто ведёт эту пару."});
    }
    loadAttendance(box);
  },
  async "clear-attendance"(el) {
    const box = el.closest('[data-box="attendance"]');
    const body = {schedule_item_id: Number(el.closest("[data-slot]").dataset.slot),
      user_id: Number(el.closest("[data-student]").dataset.student), class_date: box._date};
    try { await api("/api/academics/attendance/delete", {method: "POST", body}); } catch (err) { fail(err, "Не получилось снять отметку."); }
    loadAttendance(box);
  },
  async "delete-slot"(el) {
    if (!await confirmDelete("Пара исчезнет из расписания, студентам этого дня придётся выбрать другой.")) return;
    postThenReload(el, "/api/academics/schedule/delete", {item_id: Number(el.closest("[data-slot]").dataset.slot)}, "Не получилось удалить пару.");
  },
  async "delete-assignment"(el) {
    if (!await confirmDelete("Задание удалится вместе со всеми сданными работами.")) return;
    postThenReload(el, "/api/academics/assignments/delete", {assignment_id: Number(el.closest("[data-assignment]").dataset.assignment)}, "Не получилось удалить задание.");
  },
  async "delete-lesson"(el) {
    if (!await confirmDelete("Урок удалится вместе с мини-тестом и результатами.")) return;
    postThenReload(el, "/api/academics/lessons/delete", {lesson_id: Number(el.closest("[data-lesson]").dataset.lesson)}, "Не получилось удалить урок.");
  },
  async "delete-exam"(el) {
    if (!await confirmDelete("Экзамен удалится вместе с результатами, записями экрана и снимками.")) return;
    postThenReload(el, "/api/academics/exams/delete", {exam_id: Number(el.closest("[data-exam]").dataset.exam)}, "Не получилось удалить экзамен.");
  },
  async "delete-question"(el) {
    const box = el.closest('[data-box="questions"]');
    const path = el.dataset.kind === "exam" ? "/api/academics/exam-questions/delete" : "/api/academics/quiz-questions/delete";
    try { await api(path, {method: "POST", body: {question_id: Number(el.closest("[data-question]").dataset.question)}}); }
    catch (err) { fail(err, "Не получилось удалить вопрос."); }
    loadQuestions(box);
  },
  async "reset-attempt"(el) {
    if (!await askConfirm("Результат студента, запись экрана и снимки будут удалены — он сможет сдать экзамен заново.",
      {title: "Разрешить пересдачу?", ok: "Сбросить попытку", danger: true})) return;
    const box = el.closest('[data-box="results"]');
    try {
      await api("/api/academics/exam-attempts/reset", {method: "POST", body: {
        exam_id: Number(el.closest("[data-exam]").dataset.exam), user_id: Number(el.closest("[data-user]").dataset.user)}});
      toast("Попытка сброшена — студент может сдать заново.", "ok");
    } catch (err) { fail(err, "Не получилось сбросить попытку."); }
    loadExamResults(box);
  },
  async "delete-course"(el) {
    if (!await confirmDelete("Дисциплина удалится целиком: расписание, записи студентов, оценки, задания, уроки и экзамены.")) return;
    try {
      await api("/api/academics/courses/delete", {method: "POST", body: {course_id: courseIdOf(el)}});
      toast("Дисциплина удалена.");
      showAcadTab("teaching");
    } catch (err) { fail(err, "Не получилось удалить дисциплину."); }
  },
};

const TEACH_FORMS = {
  async "create-course"(form) {
    await api("/api/academics/courses", {method: "POST", body: Object.fromEntries(new FormData(form).entries())});
    toast("Дисциплина создана.", "ok");
    showAcadTab("teaching");
  },
  async "update-course"(form) {
    await api("/api/academics/courses/update", {method: "POST", body: {course_id: courseIdOf(form), ...Object.fromEntries(new FormData(form).entries())}});
    toast("Сохранено.", "ok");
    reloadCourseOf(form);
  },
  async "add-slot"(form) {
    const data = Object.fromEntries(new FormData(form).entries());
    if (data.end_time <= data.start_time) { toast("Конец пары должен быть позже начала."); return; }
    const body = {course_id: courseIdOf(form), day_of_week: Number(data.day_of_week), start_time: data.start_time,
      end_time: data.end_time, room: data.room, kind: data.kind};
    if (data.teacher_id) body.teacher_id = Number(data.teacher_id);
    await api("/api/academics/schedule", {method: "POST", body});
    reloadCourseOf(form);
  },
  async "set-grade"(form) {
    const data = Object.fromEntries(new FormData(form).entries());
    await api("/api/academics/grades/set", {method: "POST", body: {
      course_id: courseIdOf(form), user_id: Number(form.closest("[data-student]").dataset.student),
      label: data.label, score: Number(data.score), max_score: Number(data.max_score || 100), comment: data.comment}});
    toast("Оценка выставлена.", "ok");
    reloadCourseOf(form);
  },
  async "create-assignment"(form) {
    const fd = new FormData(form);
    const body = {course_id: courseIdOf(form), title: fd.get("title"), description: fd.get("description"), due_at: fd.get("due_at") || null};
    const file = await fileFieldToPayload(form.elements.file);
    if (file) body.file = file;
    await api("/api/academics/assignments", {method: "POST", body});
    reloadCourseOf(form);
  },
  async "grade-submission"(form) {
    const box = form.closest('[data-box="submissions"]');
    await api("/api/academics/submissions/grade", {method: "POST", body: {
      submission_id: Number(form.closest("[data-submission]").dataset.submission),
      score: Number(form.elements.score.value), comment: form.elements.comment.value}});
    toast("Оценка за работу сохранена.", "ok");
    loadSubmissions(box);
  },
  async "create-lesson"(form) {
    const fd = new FormData(form);
    const body = {course_id: courseIdOf(form), title: fd.get("title"), description: fd.get("description"), video_url: fd.get("video_url")};
    const file = await fileFieldToPayload(form.elements.file);
    if (file) body.file = file;
    await api("/api/academics/lessons", {method: "POST", body});
    reloadCourseOf(form);
  },
  async "create-exam"(form) {
    const fd = new FormData(form);
    await api("/api/academics/exams", {method: "POST", body: {
      course_id: courseIdOf(form), title: fd.get("title"), description: fd.get("description"),
      time_limit_minutes: fd.get("time_limit_minutes") || null,
      require_recording: fd.get("require_recording") === "on", require_camera: fd.get("require_camera") === "on"}});
    reloadCourseOf(form);
  },
  async "add-question"(form) {
    const box = form.closest('[data-box="questions"]');
    const {kind, id} = questionsOwner(form);
    const rows = [...form.querySelectorAll(".opt-row")].map(row => ({
      text: row.querySelector('[name="option"]').value.trim(), correct: row.querySelector('[name="correct"]').checked}));
    const filled = rows.filter(r => r.text);
    const correctIndex = filled.findIndex(r => r.correct);
    if (filled.length < 2) { toast("Нужно минимум два варианта ответа."); return; }
    if (correctIndex < 0) { toast("Отметь кружком правильный вариант (он не должен быть пустым)."); return; }
    const body = {text: form.elements.text.value, options: filled.map(r => r.text), correct_index: correctIndex};
    if (kind === "exam") body.exam_id = id; else body.lesson_id = id;
    await api(kind === "exam" ? "/api/academics/exam-questions" : "/api/academics/quiz-questions", {method: "POST", body});
    loadQuestions(box);
  },
};

// Выпадающие списки, которые сохраняются сразу при выборе.
const TEACH_CHANGES = {
  "attendance-date": el => loadAttendance(el.closest('[data-box="attendance"]'), el.value || todayIso()),
  async "slot-teacher"(el) {
    try {
      await api("/api/academics/schedule/set-teacher", {method: "POST", body: {
        item_id: Number(el.closest("[data-slot]").dataset.slot), teacher_id: el.value ? Number(el.value) : null}});
      toast("Преподаватель пары изменён.", "ok");
    } catch (err) { fail(err, "Не получилось назначить преподавателя."); }
    reloadCourseOf(el);
  },
  async "course-teacher"(el) {
    try {
      await api("/api/academics/courses/set-teacher", {method: "POST", body: {course_id: courseIdOf(el), teacher_id: el.value ? Number(el.value) : null}});
      toast("Преподаватель дисциплины изменён.", "ok");
    } catch (err) { fail(err, "Не получилось назначить преподавателя."); }
    reloadCourseOf(el);
  },
  async "move-student"(el) {
    if (!el.value) return;
    try {
      await api("/api/academics/schedule/choose", {method: "POST", body: {
        course_id: courseIdOf(el), user_id: Number(el.closest("[data-student]").dataset.student), schedule_item_id: Number(el.value)}});
      toast("Студент переведён на другой день.", "ok");
    } catch (err) { fail(err, "Не получилось перевести студента."); }
    reloadCourseOf(el);
  },
};

function initTeaching() {
  document.addEventListener("change", e => {
    const el = e.target.closest("[data-change]");
    if (el && TEACH_CHANGES[el.dataset.change]) TEACH_CHANGES[el.dataset.change](el);
  });
}
