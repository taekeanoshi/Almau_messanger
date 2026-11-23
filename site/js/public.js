"use strict";
// Публичные разделы: клубы, ивенты, партнёры (Summit Seekers, Remus, Hunters, Mafia, MOF), заявки, цифры на главной.
// Клубы и ивенты лежат в data/*.json — эти же файлы читает бэкенд.

const G = ([a, b]) => `radial-gradient(circle at 70% 20%,${a},transparent 60%),linear-gradient(160deg,${b},#050507)`;

// Подпись кнопки «Иду» между перезагрузками — просто подсказка в интерфейсе,
// реальная проверка и подсчёт всегда идут через авторизованный запрос на сервер.
const isGoingLocally = id => store.get("going:" + id) === "1";
const setGoingLocally = (id, going) => store.set("going:" + id, going ? "1" : "0");

function renderFilters(el, list, render) {
  const tags = ["Все", ...new Set(list.map(x => x.category))];
  el.innerHTML = tags.map((t, i) => `<button type="button" class="${i ? "" : "on"}" data-t="${esc(t)}">${esc(t)}</button>`).join("");
  el.onclick = e => {
    const b = e.target.closest("button"); if (!b) return;
    el.querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
    render(b.dataset.t);
  };
}

// Логотип, если он есть, иначе эмодзи
const badge = (logo, emoji, name) => logo
  ? `<img class="badge" src="${esc(logo)}" alt="Логотип ${esc(name)}" loading="lazy">`
  : `<div class="glyph">${esc(emoji)}</div>`;
let clubLogos = {};  // id клуба -> путь к логотипу, для карточек ивентов

function clubTile(c) {
  return `<article class="tile ${c.wide ? "wide" : ""}"><div class="art" style="background:${G(c.colors)}"></div>${badge(c.logo, c.emoji, c.name)}
    ${c.instagram ? `<a class="ig" href="https://instagram.com/${esc(c.instagram)}" target="_blank" rel="noopener">@${esc(c.instagram)}</a>` : ""}
    <h3>${esc(c.name)}</h3><p>${esc(c.description)}</p>
    <div class="chips">${c.partner ? `<span>🤝 Партнёр Pople</span>` : ""}${c.activities.map(x => `<span>${esc(x)}</span>`).join("")}</div>
    <div class="row">${c.partner ? `<a class="btn sm" href="${esc(c.page || "#summit")}">${esc(c.cta || "🏔 Походы и запись")}</a>` : ""}${followBtn(c.id)}</div></article>`;
}

function eventTile(e) {
  const going = isGoingLocally(e.id);
  return `<article class="tile ev ${e.wide ? "wide" : ""}" data-id="${esc(e.id)}"><div class="art" style="background:${G(e.colors)}"></div>${badge(clubLogos[e.club], e.emoji, e.host)}
    <div class="date">${esc(e.date)}</div><h3>${esc(e.title)}</h3><p>${esc(e.description)}</p>
    <div class="who">${esc(e.host)}<span class="going-count"></span></div>
    <div class="actions">
      <button type="button" class="btn sm ghost going-btn" data-going="${going ? 1 : 0}">${going ? "✅ Иду" : "Отметиться, что иду"}</button>
      <button type="button" class="btn sm ghost" data-find-company="${esc(e.id)}">🎟 Найти компанию</button>
    </div></article>`;
}

// «Иду» и «Найти компанию» доступны только вошедшим студентам (и админу).
function requireStudentAccount(what) {
  if (!chatToken) {
    toast(`Чтобы ${what}, сначала войдите или зарегистрируйтесь.`);
    location.hash = "#login";
    return false;
  }
  if (!isStudentLike()) {
    toast("Это доступно только студенческим аккаунтам.");
    return false;
  }
  return true;
}

async function markGoing(eventId, going) {
  const data = await api(`/api/events/${encodeURIComponent(eventId)}/going`, {method: "POST", body: {going}});
  setGoingLocally(eventId, going);
  return data;
}

function updateGoingButton(tile, going, count) {
  const btn = tile.querySelector(".going-btn");
  btn.dataset.going = going ? "1" : "0";
  btn.textContent = going ? "✅ Иду" : "Отметиться, что иду";
  const countEl = tile.querySelector(".going-count");
  if (countEl && count != null) countEl.textContent = ` · идут: ${count}`;
}

// Дополняет уже отрисованные карточки ивентов количеством «Иду» (/api/events).
async function enhanceEventsWithGoing() {
  try {
    const live = await api("/api/events", {auth: false});
    const goingById = Object.fromEntries(live.map(e => [e.id, e.going]));
    document.querySelectorAll("#events .tile.ev").forEach(tile => {
      const n = goingById[tile.dataset.id];
      const countEl = tile.querySelector(".going-count");
      if (n != null && countEl) countEl.textContent = ` · идут: ${n}`;
    });
  } catch { /* бэкенд недоступен — карточки остаются как есть */ }
}

// Живые цифры с бэкенда. Если API недоступен, блок просто скрывается.
async function loadStats() {
  const el = $("stats");
  if (!el) return;
  try {
    const s = await api("/api/stats", {auth: false});
    if (s.users < 10) { el.hidden = true; return; }  // пока людей мало, нули на главной только отпугивают
    el.innerHTML = `
      <div><b>${s.users}</b><span>студентов на платформе</span></div>
      <div><b>${s.dialogs_total}</b><span>диалогов</span></div>
      <div><b>${s.event_dialogs}</b><span>нашли компанию на ивент</span></div>`;
  } catch {
    el.hidden = true;
  }
}

function mount(listId, filterId, items, tile, onRendered) {
  const render = (t = "Все") => {
    $(listId).innerHTML = items.filter(x => t === "Все" || x.category === t).map(tile).join("");
    if (onRendered) onRendered();
  };
  renderFilters($(filterId), items, render);
  render();
}

async function fetchJson(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url);
  return r.json();
}

let siteEvents = null, siteClubs = [];
async function loadClubsAndEvents() {
  try {
    const [clubs, allEvents] = await Promise.all([fetchJson("data/clubs.json"), fetchJson("data/events.json")]);
    const events = upcomingEvents(allEvents);
    siteEvents = events;
    siteClubs = clubs;
    clubLogos = Object.fromEntries(clubs.filter(c => c.logo).map(c => [c.id, c.logo]));
    mount("clubs", "cf", clubs, clubTile);
    mount("events", "ef", events, eventTile, enhanceEventsWithGoing);
  } catch {
    const msg = `<div class="empty">Не удалось загрузить данные. Обновите страницу.</div>`;
    $("clubs").innerHTML = msg;
    $("events").innerHTML = msg;
  }
}

// Заявка с главной и формы партнёров уходят в один POST /api/leads.
async function sendLead(payload) {
  await api("/api/leads", {method: "POST", body: payload, auth: false});
}

function initLeadForm() {
  const form = $("lead-form"), status = $("lead-status");
  form.addEventListener("submit", async e => {
    e.preventDefault();
    const btn = form.querySelector("button[type=submit]");
    status.textContent = "Отправляем…";
    btn.disabled = true;
    try {
      await sendLead(Object.fromEntries(new FormData(form).entries()));
      status.textContent = "Спасибо! Мы скоро свяжемся с вами.";
      form.reset();
    } catch (err) {
      status.textContent = explain(err, "Не получилось отправить. Попробуйте ещё раз чуть позже.");
    } finally {
      btn.disabled = false;
    }
  });
}

// ---------- Summit Seekers: походы с записью прямо на сайте ----------
// «2026-10-24T07:00» → «24 октября, сб, 07:00». Разбираем строку вручную, чтобы часовой пояс браузера ничего не сдвигал.
function fmtStart(iso) {
  const [date, time = ""] = iso.split("T");
  const [y, m, d] = date.split("-").map(Number);
  return `${d} ${MONTHS[m - 1]}, ${WEEKDAYS[new Date(Date.UTC(y, m - 1, d)).getUTCDay()]}, ${time.slice(0, 5)}`;
}

function hikeActions(h, mine) {
  if (h.going == null) return "";  // данные из запасного файла, а не с сервера — записаться нельзя
  if (!chatToken) return `<a class="btn sm" href="#login">Войти, чтобы записаться</a>`;
  if (mine) {
    const state = mine.my_status === "going"
      ? `<span class="pill ok">Вы записаны</span>`
      : `<span class="pill warn">В листе ожидания${mine.waitlist_position ? ` · №${mine.waitlist_position}` : ""}</span>`;
    const checkin = mine.active
      ? (mine.checked_in ? `<span class="pill ok">Спуск отмечен</span>`
        : `<button type="button" class="btn sm" data-hike-checkin="${esc(h.id)}">✅ Я спустился</button>`) : "";
    return `<div class="row">${state}${checkin}
      <button type="button" class="btn sm ghost" data-hike-cancel="${esc(h.id)}">Отменить запись</button></div>`;
  }
  if (!isStudentLike()) return "";
  return `<button type="button" class="btn sm" data-hike-signup="${esc(h.id)}">${h.spots_left > 0 ? "Записаться" : "В лист ожидания"}</button>`;
}

// Карточка события клуба. Поход — частный случай: у него есть сложность и «Я спустился».
function hikeCard(h, mine, clubName = "") {
  const isHike = (h.kind || "hike") === "hike";
  const live = h.going != null;
  const full = live && h.spots_left <= 0;
  const spots = !live ? `Мест: ${h.capacity}`
    : full ? `Мест нет, в листе ожидания: ${h.waitlist}` : `Осталось ${h.spots_left} из ${h.capacity} мест`;
  const pct = live ? Math.min(100, Math.round(h.going * 100 / h.capacity)) : 0;
  return `<article class="hike">
    <div class="when">${esc(fmtStart(h.start))}${clubName ? ` · ${esc(clubName)}` : ""}</div>
    <h3>${isHike ? "" : (EVENT_KIND_ICON[h.kind] || "") + " "}${esc(h.title)}${h.demo ? `<span class="demo-tag">Пример</span>` : ""}</h3>
    ${h.route ? `<p>${esc(h.route)}</p>` : ""}
    <div class="meta">${isHike ? `<span>🥾 ${esc(h.difficulty)}</span>` : ""}<span>⏱ ~${esc(String(h.duration_hours).replace(".", ","))} ч</span><span>💰 ${esc(h.price)}</span></div>
    <p>📍 ${isHike ? "Сбор" : "Место"}: ${esc(h.meeting_point || "уточняется")}</p>
    <div class="spots">${esc(spots)}</div>
    ${live ? `<div class="bar" aria-hidden="true"><i style="width:${pct}%"></i></div>` : ""}
    ${hikeActions(h, mine)}
  </article>`;
}

// Живые данные берём из API (там считаются свободные места). Если API недоступен, показываем походы из data/hikes.json.
async function loadHikes() {
  const box = $("hikes");
  let hikes, mine = {};
  try {
    hikes = await api("/api/hikes?club=summit", {auth: false});
    if (chatToken) {
      try { mine = Object.fromEntries((await api("/api/hikes/mine")).hikes.map(h => [h.id, h])); } catch {}
    }
  } catch {
    try {
      const now = new Date();
      hikes = (await fetchJson("data/hikes.json")).filter(h => h.club === "summit" && new Date(h.start) > now);
    } catch {
      box.innerHTML = `<div class="empty">Не удалось загрузить походы. Попробуйте позже.</div>`;
      return;
    }
  }
  // Поход, на который я записан, мог уже начаться и пропасть из «ближайших» — показываем его всё равно
  const shown = new Set(hikes.map(h => h.id));
  const extra = Object.values(mine).filter(h => !shown.has(h.id));
  const all = [...extra, ...hikes];
  box.innerHTML = all.length ? all.map(h => hikeCard(h, mine[h.id])).join("")
    : `<div class="empty">Ближайшие походы скоро появятся. Подпишитесь на Instagram клуба, чтобы не пропустить.</div>`;
}

async function hikeAction(hikeId, action) {
  const messages = {
    signup: ["Не получилось записаться.", {409: "Запись на это событие уже закрыта."}],
    cancel: ["Не получилось отменить запись.", {}],
    checkin: ["Не получилось отметиться.", {409: "Отметка доступна только во время похода."}],
  };
  try {
    const data = await api(`/api/hikes/${encodeURIComponent(hikeId)}/${action}`, {method: "POST"});
    if (action === "signup") {
      toast(data.hike.my_status === "going" ? "Вы записаны! Событие появится на главной."
        : "Мест нет — вы в листе ожидания. Если место освободится, запись подтвердится сама.", "ok");
    } else if (action === "cancel") toast("Запись отменена.");
    else toast("Отмечено: вы спустились. Хорошего отдыха!", "ok");
    loadHikes();
    loadClubEvents();
    refreshNotifications();
  } catch (err) { fail(err, ...messages[action]); }
}

const gearKey = i => "gear:" + i;
function renderSchool(school) {
  $("safety").innerHTML = school.safety.map(t => `<li>${esc(t)}</li>`).join("");
  $("gear").innerHTML = school.gear.map((g, i) =>
    `<label><input type="checkbox" data-gear="${i}" ${store.get(gearKey(i)) === "1" ? "checked" : ""}> ${esc(g)}</label>`).join("");
  $("lessons").innerHTML = school.lessons.map(l =>
    `<details class="lesson"><summary>${esc(l.emoji)} ${esc(l.title)}</summary><ul>${l.points.map(p => `<li>${esc(p)}</li>`).join("")}</ul></details>`).join("");
}

let schoolLoaded = false;
async function loadSummit() {
  loadHikes();
  if (schoolLoaded) return;
  try {
    renderSchool(await fetchJson("data/school.json"));
    schoolLoaded = true;
  } catch {
    $("lessons").innerHTML = `<div class="empty">Не удалось загрузить уроки. Обновите страницу.</div>`;
  }
}

// ---------- Remus и Hunters ----------
// Билеты на DTMM и запись волонтёров — заявки со своим event_id ("dtmm" / "open-day"),
// поэтому в админке их легко отличить. Если API недоступен — честно говорим и даём Instagram клуба.
function initPartnerForm(formId, statusId, eventId, buildMessage, fallbackIg) {
  const form = $(formId), status = $(statusId);
  form.addEventListener("submit", async e => {
    e.preventDefault();
    const btn = form.querySelector("button[type=submit]");
    const data = Object.fromEntries(new FormData(form).entries());
    status.textContent = "Отправляем…";
    btn.disabled = true;
    try {
      await sendLead({
        name: String(data.name || "").trim(), contact: String(data.contact || "").trim(),
        message: buildMessage(data).slice(0, 300), event_id: eventId,
      });
      status.textContent = "Готово! Организаторы скоро с тобой свяжутся.";
      form.reset();
      prefillPartnerForms();
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) { status.textContent = explain(err); return; }
      status.innerHTML = `Не получилось отправить заявку. Напиши клубу в Instagram: <a href="https://instagram.com/${esc(fallbackIg)}" target="_blank" rel="noopener">@${esc(fallbackIg)}</a>`;
    } finally {
      btn.disabled = false;
    }
  });
}

function prefillPartnerForms() {
  if (!chatUser) return;
  for (const id of ["remus-ticket-form", "hunters-volunteer-form", "mafia-signup-form", "mof-ticket-form"]) {
    const name = document.querySelector(`#${id} input[name=name]`);
    if (name && !name.value) name.value = fullName(chatUser);
    const contact = document.querySelector(`#${id} input[name=contact]`);
    if (contact && !contact.value && chatUser.phone) contact.value = chatUser.phone;
  }
  const course = document.querySelector("#hunters-volunteer-form input[name=course]");
  if (course && !course.value && (chatUser.course || chatUser.specialty)) {
    course.value = [courseLabel(chatUser.course), chatUser.specialty || ""].filter(Boolean).join(", ");
  }
}

function clubEventCard(e) {
  return `<article class="hike">
    <div class="when">${esc(e.date)}</div>
    <h3>${esc(e.emoji)} ${esc(e.title)}</h3>
    <p>${esc(e.description)}</p>
  </article>`;
}

// Прошедшие ивенты (поле until в events.json раньше сегодняшнего дня) не показываем.
function upcomingEvents(list) {
  const today = new Date().toISOString().slice(0, 10);
  return list.filter(e => !e.until || e.until >= today);
}

async function eventsList() {
  if (!siteEvents) siteEvents = upcomingEvents(await fetchJson("data/events.json"));
  return siteEvents;
}

async function loadRemus() {
  prefillPartnerForms();
  const box = $("remus-events");
  const dtmm = `<article class="hike">
    <div class="when">Дата — у организаторов</div>
    <h3>🎫 DTMM</h3>
    <p>Главный ивент стаи Remus. Билеты — по заявке прямо здесь.</p>
    <a class="btn sm" href="#remus" data-scroll="remus-tickets">Купить билет на DTMM</a>
  </article>`;
  try {
    box.innerHTML = dtmm + (await eventsList()).filter(e => e.club === "remus").map(clubEventCard).join("");
  } catch {
    box.innerHTML = dtmm;
  }
}

async function loadHunters() {
  prefillPartnerForms();
  try {
    const openDay = (await eventsList()).find(e => e.id === "open-day");
    const el = $("hunters-openday-date");
    if (openDay && el && !el.dataset.filled) {
      el.textContent += ` Ближайший Open Day: ${openDay.date}.`;
      el.dataset.filled = "1";
    }
  } catch {}
}

// ---------- Mafia и MOF ----------
// Ближайшие 4 пятницы для записи в Mafia. Если сегодня пятница и уже позже 18:30 —
// начинаем со следующей недели, чтобы не записаться на игру, которая уже идёт.
function upcomingFridays(count = 4) {
  const now = new Date();
  const d = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  let add = (5 - d.getDay() + 7) % 7;
  if (add === 0 && (now.getHours() > 18 || (now.getHours() === 18 && now.getMinutes() >= 30))) add = 7;
  d.setDate(d.getDate() + add);
  const out = [];
  for (let i = 0; i < count; i++) {
    out.push(`${d.getDate()} ${MONTHS[d.getMonth()]}`);
    d.setDate(d.getDate() + 7);
  }
  return out;
}

function loadMafia() {
  prefillPartnerForms();
  const sel = $("mafia-friday");
  if (sel && !sel.options.length) {
    sel.innerHTML = upcomingFridays().map((f, i) =>
      `<option value="${esc(f)}">${i === 0 ? "Ближайшая" : "Пятница"}, ${esc(f)}, 18:30</option>`).join("");
  }
}

async function loadMof() {
  prefillPartnerForms();
  const box = $("mof-events");
  const date = `<article class="hike">
    <div class="when">Дата — у организаторов</div>
    <h3>🎫 MOF DATE</h3>
    <p>Мероприятие семьи MOF. Билеты — по заявке прямо здесь.</p>
    <a class="btn sm" href="#mof" data-scroll="mof-tickets">Купить билет на MOF DATE</a>
  </article>`;
  try {
    box.innerHTML = date + (await eventsList()).filter(e => e.club === "mof").map(clubEventCard).join("");
  } catch {
    box.innerHTML = date;
  }
}

// Вкладка «Партнёры»: все клубы с partner:true из data/clubs.json — добавить клуб = добавить запись в json.
function partnerCard(c) {
  return `<article class="partner-tile">
    <img src="${esc(c.logo || "img/logo.png")}" alt="Логотип ${esc(c.name)}" loading="lazy">
    <div class="grow"><span class="partner-badge">🤝 Партнёр Pople</span><h3>${esc(c.name)}</h3>
      <p>${esc(c.description)}</p>
      <div class="chips" style="margin-top:10px">${(c.activities || []).map(a => `<span>${esc(a)}</span>`).join("")}</div></div>
    <div class="actions">
      <a class="btn sm" href="${esc(c.page || "#clubs")}">${esc(c.cta || "Подробнее")}</a>
      ${c.instagram ? `<a class="btn ghost sm" href="https://instagram.com/${esc(c.instagram)}" target="_blank" rel="noopener noreferrer">Instagram</a>` : ""}
    </div></article>`;
}

async function renderPartners() {
  const box = $("partners-list");
  if (!siteClubs.length) {
    try { siteClubs = await fetchJson("data/clubs.json"); }
    catch { box.innerHTML = `<div class="empty">Не удалось загрузить список. Обновите страницу.</div>`; return; }
  }
  const partners = siteClubs.filter(c => c.partner);
  $("partners-count").textContent = partners.length ? `${partners.length}` : "";
  box.innerHTML = partners.length ? partners.map(partnerCard).join("") : `<div class="empty">Партнёров пока нет.</div>`;
}

function initPublic() {
  definePage("clubs", {enter() { loadFollowing(); }});
  definePage("partners", {enter: renderPartners});
  definePage("events", {enter() { enhanceEventsWithGoing(); loadClubEvents(); }});
  definePage("summit", {enter: loadSummit});
  definePage("remus", {enter: loadRemus});
  definePage("hunters", {enter: loadHunters});
  definePage("mafia", {enter: loadMafia});
  definePage("mof", {enter: loadMof});
  definePage("privacy");

  initLeadForm();
  initPartnerForm("remus-ticket-form", "remus-ticket-status", "dtmm",
    d => `Билет на DTMM × ${Math.min(10, Math.max(1, parseInt(d.qty, 10) || 1))}${d.note ? `. ${String(d.note).trim()}` : ""}`,
    "remus.almaty");
  initPartnerForm("hunters-volunteer-form", "hunters-volunteer-status", "open-day",
    d => `Волонтёр на день открытых дверей (Hunters)${d.course ? ` · ${String(d.course).trim()}` : ""}${d.note ? `. ${String(d.note).trim()}` : ""}`,
    "almau_hunters");
  initPartnerForm("mafia-signup-form", "mafia-signup-status", "mafia-friday",
    d => `Игра в пятницу ${String(d.friday || "").trim()}, 18:30 · ${String(d.game || "Любая").trim()} · ${Math.min(10, Math.max(1, parseInt(d.qty, 10) || 1))} чел.`,
    "mafia.almau");
  initPartnerForm("mof-ticket-form", "mof-ticket-status", "mof-date",
    d => `Билет на MOF DATE × ${Math.min(10, Math.max(1, parseInt(d.qty, 10) || 1))}${d.note ? `. ${String(d.note).trim()}` : ""}`,
    "mof.almaty");

  document.addEventListener("change", e => {
    const box = e.target.closest("[data-gear]");
    if (box) store.set(gearKey(box.dataset.gear), box.checked ? "1" : "0");
  });

  document.addEventListener("click", async e => {
    const goingBtn = e.target.closest(".going-btn");
    if (goingBtn) {
      if (!requireStudentAccount("отметиться на ивент")) return;
      const tile = goingBtn.closest(".tile");
      const going = goingBtn.dataset.going !== "1";
      goingBtn.disabled = true;
      try {
        const data = await markGoing(tile.dataset.id, going);
        updateGoingButton(tile, going, data.count);
      } catch (err) { fail(err, "Не получилось отметиться. Попробуйте позже."); }
      finally { goingBtn.disabled = false; }
      return;
    }
    const companyBtn = e.target.closest("[data-find-company]");
    if (companyBtn) {
      if (!requireStudentAccount("найти компанию на ивент")) return;
      const tile = companyBtn.closest(".tile");
      const eventId = companyBtn.dataset.findCompany;
      // Ищешь компанию — значит, идёшь: отмечаем «Иду» заодно
      if (tile && tile.querySelector(".going-btn")?.dataset.going !== "1") {
        try { updateGoingButton(tile, true, (await markGoing(eventId, true)).count); } catch {}
      }
      startCompanySearch(eventId);
      return;
    }
    const hikeBtn = e.target.closest("[data-hike-signup],[data-hike-cancel],[data-hike-checkin]");
    if (hikeBtn) {
      const d = hikeBtn.dataset;
      if (d.hikeSignup) hikeAction(d.hikeSignup, "signup");
      else if (d.hikeCheckin) hikeAction(d.hikeCheckin, "checkin");
      else if (await askConfirm("Отменить запись на поход? Место освободится для следующего в листе ожидания.", {ok: "Отменить запись", danger: true})) {
        hikeAction(d.hikeCancel, "cancel");
      }
    }
  });

  loadClubsAndEvents();
  loadStats();
}
