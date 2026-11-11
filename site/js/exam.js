"use strict";
// Прохождение проктор-экзамена студентом. Экзамен сдаётся один раз; время считает сервер;
// выходы из окна считаются всегда; запись экрана уходит на сервер кусками по ходу экзамена,
// снимки с веб-камеры — по одному кадру. Разрешение на запись и камеру спрашивает сам браузер.

const RECORDING_SLICE_MS = 15000;       // длина одного куска записи
const RECORDING_BITRATE = 150000;       // ~1 МБ в минуту: хватает, чтобы читать экран, и не раздувает базу
const SNAPSHOT_INTERVAL_MS = 20000;
const examCache = {};                   // id экзамена → его описание (для карточек и запуска)
let examRun = null;                     // идущий сейчас экзамен

function examPills(ex) {
  return `<span class="row" style="margin:6px 0">
    ${ex.time_limit_minutes ? `<span class="pill">⏱ ${ex.time_limit_minutes} мин</span>` : `<span class="pill">без лимита времени</span>`}
    ${ex.require_recording ? `<span class="pill acc">🎥 запись экрана</span>` : ""}
    ${ex.require_camera ? `<span class="pill acc">📷 снимки с веб-камеры</span>` : ""}
    <span class="pill">считаются выходы из окна</span></span>`;
}

function examCardHtml(ex, attempt, inProgress) {
  examCache[ex.id] = ex;
  let state;
  if (attempt) {
    state = `<div class="box"><b>Сдано: ${attempt.correct_count} из ${attempt.total_count} · ${Math.round(attempt.score)}%</b>
      <div class="row" style="margin-top:6px"><span class="pill ${attempt.tab_exit_count ? "warn" : "ok"}">выходов из окна: ${attempt.tab_exit_count}</span>
        ${attempt.duration_seconds != null ? `<span class="pill">длительность: ${fmtDuration(attempt.duration_seconds)}</span>` : ""}
        ${attempt.has_recording ? `<span class="pill">запись сохранена</span>` : ""}${attempt.snapshot_count ? `<span class="pill">снимков: ${attempt.snapshot_count}</span>` : ""}</div>
      <p class="muted small" style="margin:8px 0 0">Экзамен сдаётся один раз. Разрешить пересдачу может только преподаватель.</p></div>`;
  } else if (inProgress) {
    const left = inProgress.seconds_left;
    state = `<p class="small" style="margin:6px 0"><span class="pill warn">экзамен начат</span>
      ${left == null ? "" : left > 0 ? ` осталось ${fmtDuration(left)}` : " время вышло — открой и сдай то, что успел(а)"}</p>
      <button type="button" class="btn sm" data-act="start-exam" data-id="${ex.id}">Продолжить экзамен</button>`;
  } else {
    state = `<button type="button" class="btn sm" data-act="start-exam" data-id="${ex.id}">Начать экзамен</button>`;
  }
  return `<div class="item"><h6>${esc(ex.title)}</h6>${ex.description ? `<p>${esc(ex.description)}</p>` : ""}${examPills(ex)}${state}</div>`;
}

async function myExamsHtml(courseId) {
  const {exams} = await api(`/api/academics/courses/${courseId}/exams`);
  if (!exams.length) return `<p class="muted small" style="margin:0">Экзаменов пока нет.</p>`;
  const states = await Promise.all(exams.map(ex => api(`/api/academics/exams/my-attempt?exam_id=${ex.id}`)));
  return exams.map((ex, i) => examCardHtml(ex, states[i].attempt, states[i].in_progress)).join("");
}

const stopTracks = stream => { if (stream) stream.getTracks().forEach(t => t.stop()); };
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const examStore = {
  get(key) { try { return JSON.parse(sessionStorage.getItem(key) || "null"); } catch { return null; } },
  set(key, value) { try { sessionStorage.setItem(key, JSON.stringify(value)); } catch {} },
  del(key) { try { sessionStorage.removeItem(key); } catch {} },
};

function examRules(ex) {
  const rules = ["Экзамен сдаётся один раз — пересдать можно, только если разрешит преподаватель."];
  if (ex.time_limit_minutes) rules.push(`Время — ${ex.time_limit_minutes} мин. Таймер запустится сразу и не остановится, даже если закрыть страницу.`);
  rules.push("Каждый уход из окна экзамена (другая вкладка, другое приложение) считается, и преподаватель это увидит.");
  if (ex.require_recording) rules.push("Нужна запись экрана: браузер спросит разрешение — выбери «Весь экран».");
  if (ex.require_camera) rules.push("Нужна веб-камера: раз в 20 секунд делается снимок, преподаватель их увидит.");
  return rules.map(r => "• " + r).join("\n");
}

async function startExam(examId) {
  const ex = examCache[examId];
  if (!ex || examRun) return;
  if (ex.require_recording && !(navigator.mediaDevices?.getDisplayMedia && window.MediaRecorder)) {
    toast("Этот браузер не умеет записывать экран. Открой экзамен на компьютере в Chrome, Edge или Firefox.", "error", 8000);
    return;
  }
  if (ex.require_camera && !navigator.mediaDevices?.getUserMedia) {
    toast("Этот браузер не даёт доступ к камере — открой экзамен в другом браузере.", "error", 8000);
    return;
  }
  if (!await askConfirm(examRules(ex), {title: `Начать «${ex.title}»?`, ok: "Начать экзамен"})) return;

  // Сначала разрешения, потом старт на сервере: если студент отказал в записи, попытка не «сгорает».
  let screen = null, cam = null;
  if (ex.require_recording) {
    try { screen = await navigator.mediaDevices.getDisplayMedia({video: {frameRate: 5, width: {max: 1280}}, audio: false}); }
    catch { toast("Без разрешения на запись экрана этот экзамен начать нельзя.", "error", 7000); return; }
  }
  if (ex.require_camera) {
    try { cam = await navigator.mediaDevices.getUserMedia({video: {width: {ideal: 320}, height: {ideal: 240}}, audio: false}); }
    catch { stopTracks(screen); toast("Без доступа к веб-камере этот экзамен начать нельзя.", "error", 7000); return; }
  }

  let data;
  try {
    data = await api("/api/academics/exams/start", {method: "POST", body: {exam_id: examId}});
  } catch (err) {
    stopTracks(screen); stopTracks(cam);
    fail(err, "Не получилось начать экзамен.", {409: "Этот экзамен уже сдан.", 403: "Ты не записан(а) на эту дисциплину."});
    return;
  }

  const elapsed = Math.max(0, (Date.now() - Date.parse(data.started_at)) / 1000);
  const resumed = elapsed > 5;  // сеанс уже шёл — значит, страницу перезагружали или закрывали
  const saved = examStore.get(`exam-state:${examId}`) || {exits: 0, awayMs: 0};
  examRun = {
    exam: data.exam, questions: data.questions, screen, cam,
    deadline: data.seconds_left == null ? null : Date.now() + data.seconds_left * 1000,
    exits: saved.exits + (resumed ? 1 : 0), awayMs: saved.awayMs, awaySince: null,
    seq: resumed ? Math.floor(elapsed / (RECORDING_SLICE_MS / 1000)) + 1 : 0,
    uploads: Promise.resolve(), snapshotsSent: 0, finishing: false, timers: [],
  };
  renderExamOverlay(examRun);
  trackExamFocus(examRun);
  if (screen) startExamRecorder(examRun);
  if (cam) startExamSnapshots(examRun);
  startExamTimer(examRun);
  window.onbeforeunload = e => { e.preventDefault(); e.returnValue = ""; };
}

function renderExamOverlay(run) {
  const answers = examStore.get(`exam-answers:${run.exam.id}`) || {};
  const overlay = document.createElement("div");
  overlay.className = "exam-overlay";
  overlay.id = "exam-overlay";
  overlay.innerHTML = `<div class="exam-bar"><div class="wrap"><b>${esc(run.exam.title)}</b>
      <span class="row"><span class="pill" id="exam-exits"></span><span class="exam-timer" id="exam-timer"></span></span></div></div>
    <div class="exam-body">
      <div class="banner warn"><span>Не переключайся на другие вкладки и окна — каждый уход считается.
        ${run.screen ? " Идёт запись экрана." : ""}${run.cam ? " Камера делает снимки." : ""}</span></div>
      <form id="exam-form">
        ${run.questions.length ? run.questions.map((q, i) => `<div class="q"><p>${i + 1}. ${esc(q.text)}</p>
          ${q.options.map((o, oi) => `<label><input type="radio" name="q${i}" value="${oi}" ${answers[i] === oi ? "checked" : ""}> <span>${esc(o)}</span></label>`).join("")}</div>`).join("")
          : `<p class="muted">В этом экзамене пока нет вопросов.</p>`}
        <p class="lead-status" id="exam-status" style="margin-top:14px"></p>
        <button class="btn" type="submit" id="exam-submit-btn" style="margin-top:6px">Сдать экзамен</button>
      </form></div>
    ${run.cam ? `<video class="cam-preview" id="exam-cam" autoplay muted playsinline></video>` : ""}`;
  document.body.appendChild(overlay);
  overlay.querySelector("#exam-form").addEventListener("change", () => {
    const current = {};
    run.questions.forEach((q, i) => {
      const checked = overlay.querySelector(`input[name="q${i}"]:checked`);
      if (checked) current[i] = Number(checked.value);
    });
    examStore.set(`exam-answers:${run.exam.id}`, current);  // перезагрузка страницы не сотрёт выбранное
  });
  overlay.querySelector("#exam-form").addEventListener("submit", e => { e.preventDefault(); submitExam(false); });
  updateExamExits(run);
}

function updateExamExits(run) {
  const el = $("exam-exits");
  if (!el) return;
  el.textContent = `выходов из окна: ${run.exits}`;
  el.className = "pill " + (run.exits ? "warn" : "ok");
  examStore.set(`exam-state:${run.exam.id}`, {exits: run.exits, awayMs: run.awayMs});
}

// Уход из окна = потеря фокуса или скрытая вкладка. Оба события приходят парой — считаем один раз.
function trackExamFocus(run) {
  const leave = () => {
    if (run.finishing || run.awaySince) return;
    run.awaySince = Date.now();
    run.exits++;
    updateExamExits(run);
  };
  const back = () => {
    if (!run.awaySince) return;
    run.awayMs += Date.now() - run.awaySince;
    run.awaySince = null;
    updateExamExits(run);
  };
  const onVisibility = () => (document.hidden ? leave() : back());
  window.addEventListener("blur", leave);
  window.addEventListener("focus", back);
  document.addEventListener("visibilitychange", onVisibility);
  run.back = back;
  run.untrack = () => {
    window.removeEventListener("blur", leave);
    window.removeEventListener("focus", back);
    document.removeEventListener("visibilitychange", onVisibility);
  };
}

function startExamTimer(run) {
  const el = $("exam-timer");
  if (run.deadline == null) { el.textContent = ""; return; }
  const tick = () => {
    const left = Math.max(0, Math.round((run.deadline - Date.now()) / 1000));
    el.textContent = fmtDuration(left);
    el.classList.toggle("low", left <= 60);
    if (left <= 0 && !run.finishing) {
      $("exam-status").textContent = "Время вышло — отправляем ответы…";
      submitExam(true);
    }
  };
  tick();
  run.timers.push(setInterval(tick, 500));
}

function uploadExamChunk(run, blob) {
  const seq = run.seq++;
  run.uploads = run.uploads.then(async () => {
    const data = await readFileAsBase64(blob);
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        await api("/api/academics/exams/recording-chunk", {method: "POST", body: {exam_id: run.exam.id, seq, data}});
        return;
      } catch (err) {
        // Отказ сервера (лимит размера, экзамен уже сдан) повтором не лечится; обрыв связи — пробуем ещё
        if (err instanceof ApiError && err.status !== 0 && err.status < 500) return;
        await sleep(1500);
      }
    }
  });
}

function startExamRecorder(run) {
  const mimeType = ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"].find(t => MediaRecorder.isTypeSupported(t));
  run.recorder = new MediaRecorder(run.screen, {mimeType, videoBitsPerSecond: RECORDING_BITRATE});
  run.recorder.ondataavailable = e => { if (e.data && e.data.size) uploadExamChunk(run, e.data); };
  run.recorder.start(RECORDING_SLICE_MS);
  run.screen.getVideoTracks()[0].addEventListener("ended", () => {
    if (examRun !== run || run.finishing) return;
    run.exits++;
    updateExamExits(run);
    toast("Запись экрана остановлена — это отмечено в результатах экзамена.", "error", 8000);
  });
}

function startExamSnapshots(run) {
  const video = $("exam-cam");
  video.srcObject = run.cam;
  const canvas = document.createElement("canvas");
  canvas.width = 320; canvas.height = 240;
  run.shoot = () => new Promise(resolve => {
    if (video.readyState < 2) { resolve(); return; }
    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
    canvas.toBlob(async blob => {
      try {
        if (blob) {
          await api("/api/academics/exams/snapshot", {method: "POST", body: {exam_id: run.exam.id, data: await readFileAsBase64(blob)}});
          run.snapshotsSent++;
        }
      } catch {}
      resolve();
    }, "image/jpeg", 0.6);
  });
  video.addEventListener("loadeddata", () => setTimeout(run.shoot, 400), {once: true});
  run.timers.push(setInterval(run.shoot, SNAPSHOT_INTERVAL_MS));
}

function closeExamOverlay(run) {
  run.timers.forEach(clearInterval);
  if (run.untrack) run.untrack();
  stopTracks(run.screen); stopTracks(run.cam);
  window.onbeforeunload = null;
  $("exam-overlay")?.remove();
  examStore.del(`exam-answers:${run.exam.id}`);
  examStore.del(`exam-state:${run.exam.id}`);
  examRun = null;
}

async function submitExam(auto) {
  const run = examRun;
  if (!run || run.finishing) return;
  const overlay = $("exam-overlay");
  const answers = run.questions.map((q, i) => {
    const checked = overlay.querySelector(`input[name="q${i}"]:checked`);
    return checked ? Number(checked.value) : -1;   // -1 — вопрос без ответа
  });
  if (!auto) {
    const skipped = answers.filter(a => a < 0).length;
    const text = skipped ? `Без ответа осталось вопросов: ${skipped}. После сдачи изменить ответы будет нельзя.`
      : "После сдачи изменить ответы будет нельзя.";
    if (!await askConfirm(text, {title: "Сдать экзамен?", ok: "Сдать"})) return;
    if (examRun !== run || run.finishing) return;
  }

  run.finishing = true;
  if (run.back) run.back();
  const btn = $("exam-submit-btn"), status = $("exam-status");
  btn.disabled = true;
  status.textContent = run.recorder ? "Дозагружаем запись экрана и отправляем ответы…" : "Отправляем ответы…";

  // Повторная отправка после сбоя: прежняя запись уже остановлена — дописываем короткий кусок,
  // чтобы на сервере точно была хоть какая-то запись, если прошлые куски не дошли.
  if (run.retry && run.recorder && run.recorder.state === "inactive" && run.screen.getVideoTracks()[0]?.readyState === "live") {
    startExamRecorder(run);
    await sleep(1200);
  }
  if (run.recorder && run.recorder.state !== "inactive") {
    await new Promise(resolve => { run.recorder.addEventListener("stop", resolve, {once: true}); run.recorder.stop(); });
  }
  await Promise.race([run.uploads, sleep(60000)]);
  if (run.shoot && run.snapshotsSent === 0) await run.shoot();

  try {
    const result = await api("/api/academics/exams/submit", {method: "POST", body: {
      exam_id: run.exam.id, answers, tab_exit_count: run.exits, away_seconds: Math.round(run.awayMs / 1000)}});
    closeExamOverlay(run);
    await openModal(`<h3>Экзамен сдан</h3>
      <p>Результат: ${result.correct_count} из ${result.total_count} (${Math.round(result.score)}%).
Выходов из окна: ${result.tab_exit_count}.${result.late ? "\nСдано после окончания времени — преподаватель это увидит." : ""}</p>
      <div class="actions"><button type="button" class="btn" data-close="ok">Понятно</button></div>`).closed;
  } catch (err) {
    if (err instanceof ApiError && err.status === 409) {
      closeExamOverlay(run);
      toast("Этот экзамен уже сдан.", "error");
    } else {
      // Ответы остаются на экране — можно повторить отправку, когда связь вернётся
      run.finishing = false;
      run.retry = true;
      btn.disabled = false;
      btn.textContent = "Повторить отправку";
      status.textContent = explain(err, "Не получилось сдать экзамен. Ответы сохранены на экране — попробуй ещё раз.", {
        400: run.exam.require_recording ? "Запись экрана не дошла до сервера. Проверь связь и нажми «Повторить отправку»."
          : "Снимки с камеры не дошли до сервера. Проверь связь и нажми «Повторить отправку»."});
      return;
    }
  }
  if (currentPage === "academics") showAcadTab("courses");
}

const EXAM_ACTIONS = {
  "start-exam": el => startExam(Number(el.dataset.id)),
};
