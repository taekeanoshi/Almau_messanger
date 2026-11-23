"use strict";
// Клубная система: подписки, «Мои клубы» на главной, события клубов с записью,
// колокольчик уведомлений и кабинет организатора.

const EVENT_KIND_ICON = {hike: "🏔", party: "🎉", game: "🎲", lecture: "🎤", tickets: "🎫"};
const fmtAt = iso => fmtStart(iso);   // «24 октября, сб, 07:00»
const proTag = `<span class="pro-tag" title="Во время пилота открыто всем клубам">в тарифе «Клуб»</span>`;

// ---------- подписки ----------
let myFollowing = new Set();
function followBtn(clubId) {
  const on = myFollowing.has(clubId);
  return `<button type="button" class="btn sm ${on ? "ghost" : ""}" data-follow="${esc(clubId)}" data-on="${on ? 1 : 0}">${on ? "✓ Вы подписаны" : "＋ Подписаться"}</button>`;
}
function paintFollowButtons() {
  document.querySelectorAll("[data-follow]").forEach(b => {
    const on = myFollowing.has(b.dataset.follow);
    b.dataset.on = on ? 1 : 0;
    b.classList.toggle("ghost", on);
    b.textContent = on ? "✓ Вы подписаны" : "＋ Подписаться";
  });
}
async function loadFollowing() {
  if (!chatToken) { myFollowing = new Set(); paintFollowButtons(); return null; }
  try {
    const data = await api("/api/clubs/mine");
    myFollowing = new Set(data.following.map(c => c.id));
    paintFollowButtons();
    return data;
  } catch { return null; }
}
async function toggleFollow(btn) {
  if (!chatToken) { toast("Чтобы подписаться на клуб, сначала войдите."); location.hash = "#login"; return; }
  const follow = btn.dataset.on !== "1";
  try {
    const {following} = await api(`/api/clubs/${encodeURIComponent(btn.dataset.follow)}/follow`, {method: "POST", body: {follow}});
    myFollowing = new Set(following);
    paintFollowButtons();
    toast(follow ? "Подписка оформлена: события клуба появятся на главной." : "Вы отписались от клуба.", follow ? "ok" : "info");
  } catch (err) { fail(err, "Не получилось изменить подписку."); }
}

// ---------- «Мои клубы» на главной ----------
function myClubsCardHtml(data) {
  if (!data || !data.following.length) {
    return `<h3>Мои клубы<a href="#clubs">все клубы →</a></h3>${dashEmpty("Подпишись на клуб — его события будут появляться здесь.")}
      <a class="btn sm" href="#clubs" style="margin-top:12px">Выбрать клубы</a>`;
  }
  const lines = data.feed.slice(0, 5).map(e => {
    const state = e.my_status === "going" ? `<span class="pill ok">записан(а)</span>`
      : e.my_status === "waitlist" ? `<span class="pill warn">лист ожидания · №${e.waitlist_position || "?"}</span>`
      : e.spots_left > 0 ? `<a class="btn sm" href="#events" data-scroll-later="club-events">Записаться</a>`
      : `<span class="pill">мест нет</span>`;
    return dashLine(`${EVENT_KIND_ICON[e.kind] || "📌"} ${esc(e.title)}`, `${esc(e.club_name)} · ${esc(fmtAt(e.start))}`, state);
  }).join("");
  return `<h3>Мои клубы<a href="#clubs">все клубы →</a></h3>
    <p class="muted small" style="margin:0 0 8px">${data.following.map(c => esc(c.name)).join(" · ")}</p>
    ${lines || dashEmpty("У твоих клубов пока нет ближайших событий.")}`;
}
async function loadMyClubsCard() {
  const box = $("dash-clubs");
  if (!box) return;
  box.innerHTML = myClubsCardHtml(await loadFollowing());
}

// ---------- события клубов с записью (страница «Ивенты») ----------
async function loadClubEvents() {
  const box = $("club-events");
  if (!box) return;
  try {
    const events = await api("/api/hikes", {auth: false});
    let mine = {};
    if (chatToken) { try { mine = Object.fromEntries((await api("/api/hikes/mine")).hikes.map(h => [h.id, h])); } catch {} }
    const names = Object.fromEntries(siteClubs.map(c => [c.id, c.name]));
    box.innerHTML = events.length
      ? events.map(e => hikeCard(e, mine[e.id], names[e.club] || "")).join("")
      : `<div class="empty">Событий с записью пока нет. Подпишись на клуб, чтобы узнать о новом первым.</div>`;
    $("club-events-sect").hidden = false;
  } catch { $("club-events-sect").hidden = true; }
}

// ---------- колокольчик ----------
let notifTimer = null, notifUnread = 0;
function paintBell() {
  const bell = $("nav-bell");
  bell.hidden = !chatToken;
  bell.innerHTML = `🔔${notifUnread ? `<span class="count">${notifUnread}</span>` : ""}`;
  bell.setAttribute("aria-label", notifUnread ? `Уведомления: ${notifUnread} новых` : "Уведомления");
}
async function refreshNotifications(render = false) {
  if (!chatToken || document.hidden) return;
  try {
    const data = await api("/api/notifications");
    notifUnread = data.unread;
    paintBell();
    if (render) {
      $("notif-panel").innerHTML = `<div class="notif-head"><b>Уведомления</b></div>` + (data.items.length
        ? data.items.map(n => `<a class="notif ${n.unread ? "unread" : ""}" href="${esc(n.link || "#home")}">
            <span>${esc(n.text)}</span><small>${esc(fmtWhen(n.at))}</small></a>`).join("")
        : `<div class="empty" style="margin:10px">Пока тихо. Сюда придут подтверждения записи, напоминания и новости клубов.</div>`);
    }
  } catch {}
}
function closeNotifPanel() { $("notif-panel").classList.remove("open"); }
async function toggleNotifPanel() {
  const panel = $("notif-panel");
  if (panel.classList.contains("open")) { closeNotifPanel(); return; }
  closeNavDrawer();
  panel.classList.add("open");
  await refreshNotifications(true);
  if (notifUnread) {
    try { await api("/api/notifications/read", {method: "POST"}); notifUnread = 0; paintBell(); } catch {}
  }
}
function syncNotifPolling() {
  clearInterval(notifTimer);
  notifUnread = 0;
  paintBell();
  closeNotifPanel();
  if (chatToken) { refreshNotifications(); notifTimer = setInterval(refreshNotifications, 60000); }
}

// ---------- кабинет организатора ----------
let cabinet = null, cabinetOpenEvent = null, cabinetEditing = null;
const cabinetQuery = () => (hasRole("admin") && cabinet?.club ? `?club=${encodeURIComponent(cabinet.club.id)}` : "");

function cabinetEventRow(e, past) {
  const fill = `${e.going} из ${e.capacity}${e.waitlist ? ` · в ожидании ${e.waitlist}` : ""}`;
  const turnout = past ? (e.turnout != null ? `<span class="pill ok">явка ${e.turnout}%</span>` : `<span class="pill">явка не отмечена</span>`) : "";
  return `<div class="cab-event" data-id="${esc(e.id)}">
    <div class="grow"><b>${EVENT_KIND_ICON[e.kind] || "📌"} ${esc(e.title)}${e.demo ? `<span class="demo-tag">Пример</span>` : ""}</b>
      <small>${esc(fmtAt(e.start))} · ${esc(e.meeting_point || "место уточняется")}</small>
      <div class="bar" aria-hidden="true"><i style="width:${Math.min(100, Math.round(e.going * 100 / e.capacity))}%"></i></div>
      <small>Записались: ${fill} ${turnout}</small></div>
    <div class="row cab-actions">
      <button type="button" class="btn sm" data-cab-signups="${esc(e.id)}">👥 Список</button>
      ${past ? "" : `<button type="button" class="btn sm ghost" data-cab-edit="${esc(e.id)}">Изменить</button>`}
      <button type="button" class="btn sm ghost" data-cab-dup="${esc(e.id)}">Повторить через неделю</button>
      ${past ? "" : `<button type="button" class="btn sm ghost" data-cab-cancel="${esc(e.id)}">Отменить</button>`}
    </div></div>`;
}

function cabinetFormHtml(e) {
  const kinds = Object.entries(cabinet.kinds).map(([v, l]) => `<option value="${v}" ${e?.kind === v ? "selected" : ""}>${EVENT_KIND_ICON[v] || ""} ${esc(l)}</option>`).join("");
  return `<form class="cab-form" id="cab-form">
    <h3>${e ? "Изменить событие" : "Новое событие"}</h3>
    <div class="cab-grid">
      <label>Тип<select name="kind">${kinds}</select></label>
      <label>Название<input name="title" required maxlength="120" value="${esc(e?.title || "")}" placeholder="Например: Поход выходного дня"></label>
      <label>Дата и время<input name="start_at" type="datetime-local" required value="${esc(e?.start || "")}"></label>
      <label>Сколько мест<input name="capacity" type="number" min="1" max="1000" required value="${e?.capacity || 20}"></label>
      <label>Место сбора<input name="meeting_point" maxlength="300" value="${esc(e?.meeting_point || "")}" placeholder="У главного входа AlmaU"></label>
      <label>Цена<input name="price" maxlength="60" value="${esc(e?.price || "Бесплатно")}"></label>
    </div>
    <label>Описание<textarea name="route" maxlength="600" placeholder="Что будет и что взять с собой">${esc(e?.route || "")}</textarea></label>
    <div class="row"><button class="btn" type="submit">${e ? "Сохранить" : "Создать событие"}</button>
      <button type="button" class="btn ghost" id="cab-form-cancel">Отмена</button></div>
    ${e ? `<p class="muted small" style="margin:8px 0 0">Если изменить время или место, все записанные получат уведомление.</p>`
        : `<p class="muted small" style="margin:8px 0 0">Подписчики клуба получат уведомление о новом событии.</p>`}
  </form>`;
}

function renderCabinet() {
  const c = cabinet;
  $("cab-title").textContent = `Кабинет клуба · ${c.club.name}`;
  $("cab-stats").innerHTML = [
    ["Подписчики", c.followers], ["Ближайших событий", c.upcoming.length],
    ["Записей на ближайшие", c.signups_upcoming], ["Средняя явка", c.avg_turnout != null ? c.avg_turnout + "%" : "—"],
  ].map(([label, value]) => `<div class="card stat"><b>${value}</b><span>${label}</span></div>`).join("");
  $("cab-form-box").innerHTML = cabinetEditing !== null ? cabinetFormHtml(cabinetEditing || null) : "";
  $("cab-upcoming").innerHTML = c.upcoming.length ? c.upcoming.map(e => cabinetEventRow(e, false)).join("")
    : `<div class="empty">Ближайших событий нет. Нажмите «＋ Новое событие» — запись откроется сразу.</div>`;
  $("cab-past").innerHTML = c.past.length ? c.past.map(e => cabinetEventRow(e, true)).join("")
    : `<div class="empty">Прошедших событий пока нет.</div>`;
}

async function loadCabinet() {
  try {
    cabinet = await api("/api/organizer/overview" + cabinetQuery());
    renderCabinet();
    if (cabinetOpenEvent) loadCabinetSignups(cabinetOpenEvent);
  } catch (err) {
    $("cab-upcoming").innerHTML = `<div class="empty">${esc(explain(err, "Не получилось открыть кабинет."))}</div>`;
  }
}

async function loadCabinetSignups(eventId) {
  cabinetOpenEvent = eventId;
  const box = $("cab-signups");
  try {
    const {event, signups} = await api(`/api/organizer/events/${encodeURIComponent(eventId)}/signups${cabinetQuery()}`);
    const going = signups.filter(s => s.status === "going"), waiting = signups.filter(s => s.status === "waitlist");
    const row = s => `<div class="roster-row"><span class="grow"><b>${esc(s.name)}</b><small>${esc(s.phone)}${s.checked_in ? " · спуск отмечен" : ""}</small></span>
      ${s.status === "going"
        ? `<label class="att"><input type="checkbox" data-cab-att="${s.user_id}" ${s.attended ? "checked" : ""}> пришёл</label>`
        : `<span class="pill warn">ожидание · №${s.waitlist_position}</span>`}</div>`;
    box.innerHTML = `<div class="card">
      <h3>${esc(event.title)} · ${esc(fmtAt(event.start))}<a href="#cabinet" id="cab-signups-close">закрыть ✕</a></h3>
      <p class="muted small" style="margin:0 0 10px">Идут: ${event.going} из ${event.capacity} · лист ожидания: ${event.waitlist} · пришли: <b id="cab-attended">${event.attended}</b></p>
      <div class="row" style="margin-bottom:12px">
        <button type="button" class="btn sm ghost" data-cab-csv="${esc(event.id)}">⬇ Скачать список (CSV)</button>${proTag}
      </div>
      ${going.length ? `<p class="small muted" style="margin:0 0 6px">Отмечайте пришедших на входе — явка посчитается сама. ${proTag}</p>` + going.map(row).join("") : `<div class="empty">Пока никто не записался.</div>`}
      ${waiting.length ? `<h4 style="margin:16px 0 6px">Лист ожидания</h4>` + waiting.map(row).join("") : ""}
      <form class="mini-form" id="cab-msg-form" style="margin-top:16px">
        <input name="text" maxlength="300" required placeholder="Сообщение всем записанным, например: выходим на час раньше">
        <button class="btn sm" type="submit">Отправить</button>${proTag}
      </form></div>`;
    box.scrollIntoView({behavior: "smooth", block: "nearest"});
  } catch (err) { box.innerHTML = `<div class="empty">${esc(explain(err, "Не получилось загрузить список."))}</div>`; }
}

async function cabinetSubmit(form) {
  const body = Object.fromEntries(new FormData(form).entries());
  body.capacity = Number(body.capacity);
  const editing = cabinetEditing && cabinetEditing.id;
  const btn = form.querySelector("button[type=submit]");
  btn.disabled = true;
  try {
    await api(`/api/organizer/events${editing ? "/" + encodeURIComponent(editing) : ""}${cabinetQuery()}`, {method: "POST", body});
    toast(editing ? "Событие обновлено." : "Событие создано — запись открыта.", "ok");
    cabinetEditing = null;
    loadCabinet();
  } catch (err) {
    fail(err, "Не получилось сохранить событие. Проверьте дату и число мест.");
    btn.disabled = false;
  }
}

async function cabinetClick(e) {
  const t = e.target;
  if (t.closest("#cab-new")) { cabinetEditing = false; renderCabinet(); $("cab-form").scrollIntoView({behavior: "smooth", block: "center"}); return; }
  if (t.closest("#cab-form-cancel")) { cabinetEditing = null; renderCabinet(); return; }
  if (t.closest("#cab-signups-close")) { e.preventDefault(); cabinetOpenEvent = null; $("cab-signups").innerHTML = ""; return; }
  const q = cabinetQuery();
  let b;
  if ((b = t.closest("[data-cab-signups]"))) return loadCabinetSignups(b.dataset.cabSignups);
  if ((b = t.closest("[data-cab-edit]"))) {
    cabinetEditing = cabinet.upcoming.find(x => x.id === b.dataset.cabEdit) || false;
    renderCabinet(); $("cab-form").scrollIntoView({behavior: "smooth", block: "center"}); return;
  }
  if ((b = t.closest("[data-cab-dup]"))) {
    try {
      const {event} = await api(`/api/organizer/events/${encodeURIComponent(b.dataset.cabDup)}/duplicate${q}`, {method: "POST", body: {days: 7}});
      toast(`Создана копия на ${fmtAt(event.start)}.`, "ok"); loadCabinet();
    } catch (err) { fail(err, "Не получилось повторить событие."); }
    return;
  }
  if ((b = t.closest("[data-cab-cancel]"))) {
    if (!confirm("Отменить событие? Все записанные получат уведомление.")) return;
    try {
      const {notified} = await api(`/api/organizer/events/${encodeURIComponent(b.dataset.cabCancel)}/cancel${q}`, {method: "POST"});
      toast(`Событие отменено. Уведомлений отправлено: ${notified}.`);
      if (cabinetOpenEvent === b.dataset.cabCancel) { cabinetOpenEvent = null; $("cab-signups").innerHTML = ""; }
      loadCabinet();
    } catch (err) { fail(err, "Не получилось отменить событие."); }
    return;
  }
  if ((b = t.closest("[data-cab-csv]"))) {
    try {
      const blob = await apiBlob(`/api/organizer/events/${encodeURIComponent(b.dataset.cabCsv)}/signups?format=csv${q ? "&" + q.slice(1) : ""}`);
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = `spisok-${b.dataset.cabCsv}.csv`;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    } catch (err) { fail(err, "Не получилось скачать список."); }
  }
}

function initClubs() {
  definePage("cabinet", {auth: true, roles: ["organizer", "admin"], enter() { cabinetEditing = null; loadCabinet(); }});
  $("nav-bell").addEventListener("click", e => { e.stopPropagation(); toggleNotifPanel(); });
  document.addEventListener("click", e => {
    const follow = e.target.closest("[data-follow]");
    if (follow) { toggleFollow(follow); return; }
    if (e.target.closest(".notif")) closeNotifPanel();
    else if (!e.target.closest("#notif-panel") && !e.target.closest("#nav-bell")) closeNotifPanel();
    if (e.target.closest("#p-cabinet")) cabinetClick(e);
  });
  document.addEventListener("submit", async e => {
    if (e.target.id === "cab-form") { e.preventDefault(); cabinetSubmit(e.target); }
    if (e.target.id === "cab-msg-form") {
      e.preventDefault();
      const text = e.target.text.value.trim();
      try {
        const {sent} = await api(`/api/organizer/events/${encodeURIComponent(cabinetOpenEvent)}/message${cabinetQuery()}`, {method: "POST", body: {text}});
        toast(`Отправлено: ${sent}. Сообщение придёт участникам в колокольчик.`, "ok");
        e.target.reset();
      } catch (err) { fail(err, "Не получилось отправить сообщение."); }
    }
  });
  document.addEventListener("change", async e => {
    const box = e.target.closest("[data-cab-att]");
    if (!box) return;
    try {
      const {event} = await api(`/api/organizer/events/${encodeURIComponent(cabinetOpenEvent)}/attendance${cabinetQuery()}`,
        {method: "POST", body: {user_id: Number(box.dataset.cabAtt), present: box.checked}});
      $("cab-attended").textContent = event.attended;
    } catch (err) { box.checked = !box.checked; fail(err, "Не получилось отметить."); }
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshNotifications(); });
  sessionListeners.push(syncNotifPolling);
  sessionListeners.push(() => { loadFollowing(); });
}
