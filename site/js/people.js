"use strict";
// Сообщения: поиск людей по ФИО и обычный (не анонимный) чат с непрочитанными, фото и блокировкой.

const PEOPLE_POLL_MS = 2500;
let peopleDialog = null;   // {dialog_id, partner}
let peopleSince = 0, peoplePollTimer = null, peopleListTimer = null, peopleSearchTimer = null, peoplePolling = false;

function personSub(u) {
  return [u.role_label, courseLabel(u.course), u.specialty].filter(Boolean).join(" · ");
}

function renderPeopleResults(users, query) {
  const box = $("people-results");
  if (!users.length) {
    box.innerHTML = `<div class="empty">${query ? "Никого не нашлось. Проверь написание имени." : "Пока здесь никого нет."}</div>`;
    return;
  }
  box.innerHTML = users.map(u => `<div class="people-row"><span class="row" style="flex-wrap:nowrap;min-width:0">
      <span class="avatar">${esc(initials(u))}</span>
      <span class="grow"><b>${esc(fullName(u))}</b><small>${esc(personSub(u))}</small></span></span>
    <button type="button" class="btn sm" data-start-direct="${u.id}">Написать</button></div>`).join("");
}

function renderPeopleDialogs(dialogs) {
  const box = $("people-dialogs");
  if (!dialogs.length) {
    box.innerHTML = `<div class="empty">Диалогов пока нет — найди человека по имени ниже и напиши первым.</div>`;
    return;
  }
  box.innerHTML = dialogs.map(d => `<button type="button" class="dialog-row ${d.unread ? "unread" : ""}" data-open-direct="${esc(d.dialog_id)}">
      <span class="avatar">${esc(initials(d.partner))}</span>
      <span class="grow"><b>${esc(fullName(d.partner))}</b>
        <span>${d.last_text ? (d.last_mine ? "Вы: " : "") + esc(d.last_text) : esc(personSub(d.partner) || "Нет сообщений")}</span></span>
      <span style="text-align:right;flex:none"><small class="muted">${esc(d.last_text ? fmtWhen(d.last_at) : "")}</small>
        ${d.unread ? `<br><span class="count">${d.unread}</span>` : ""}${d.blocked ? `<br><span class="pill bad">блок</span>` : ""}</span>
    </button>`).join("");
  box._dialogs = dialogs;
}

async function loadPeopleDialogs() {
  try {
    const {dialogs} = await api("/api/chat/direct/list");
    renderPeopleDialogs(dialogs);
    const unread = dialogs.reduce((n, d) => n + d.unread, 0);
    if (unread !== unreadCount) { unreadCount = unread; renderNav(); }
  } catch (err) {
    if (!$("people-dialogs").children.length) $("people-dialogs").innerHTML = `<div class="empty">${esc(explain(err, "Не получилось загрузить диалоги."))}</div>`;
  }
}

// Пустой запрос — весь список зарегистрированных (справочник), иначе — фильтр по ФИО.
async function loadPeopleResults(q) {
  try {
    const {users} = await api(`/api/chat/direct/search-users?q=${encodeURIComponent(q)}`);
    renderPeopleResults(users, q);
  } catch (err) {
    $("people-results").innerHTML = `<div class="empty">${esc(explain(err, "Не получилось загрузить список."))}</div>`;
  }
}

function stopPeoplePolling() { clearInterval(peoplePollTimer); peoplePollTimer = null; }

function openDirectChat(dialogId, partner) {
  peopleDialog = {dialog_id: dialogId, partner};
  peopleSince = 0;
  $("people-chat-box").innerHTML = "";
  $("people-chat-partner-name").textContent = fullName(partner);
  $("people-chat-sub").textContent = personSub(partner) || "Обычный чат — собеседник видит твоё имя.";
  $("people-list").hidden = true;
  $("people-chat").hidden = false;
  clearInterval(peopleListTimer);
  stopPeoplePolling();
  pollPeopleChat();
  peoplePollTimer = setInterval(pollPeopleChat, PEOPLE_POLL_MS);
  $("people-chat-text").focus();
}

function showPeopleList() {
  stopPeoplePolling();
  peopleDialog = null;
  $("people-chat").hidden = true;
  $("people-list").hidden = false;
  loadPeopleDialogs();
  clearInterval(peopleListTimer);
  peopleListTimer = setInterval(() => { if (!document.hidden) loadPeopleDialogs(); }, 15000);
}

async function pollPeopleChat() {
  if (!peopleDialog || peoplePolling) return;
  peoplePolling = true;
  const dialogId = peopleDialog.dialog_id;
  try {
    const {messages} = await api(`/api/chat/direct/poll?dialog_id=${encodeURIComponent(dialogId)}&since=${peopleSince}`);
    if (peopleDialog && peopleDialog.dialog_id === dialogId && messages.length) {
      peopleSince = Math.max(peopleSince, appendMessages($("people-chat-box"), messages));
    }
  } catch (err) {
    if (err instanceof ApiError && err.status === 403) { toast("Этот диалог больше недоступен.", "error"); showPeopleList(); }
  } finally {
    peoplePolling = false;
  }
}

// Открыть (или создать) диалог с конкретным человеком — например, «Написать преподавателю» из учёбы.
async function openDirectWith(userId) {
  try {
    const body = await api("/api/chat/direct/start", {method: "POST", body: {user_id: userId}});
    if (location.hash !== "#people") {
      peopleOpenOnEnter = body;
      location.hash = "#people";
    } else {
      openDirectChat(body.dialog_id, body.partner);
    }
  } catch (err) {
    fail(err, "Не получилось открыть диалог.", {403: "Написать этому человеку нельзя: один из вас заблокировал другого.",
      404: "Этот аккаунт больше недоступен."});
  }
}
let peopleOpenOnEnter = null;

const DIRECT_SEND_ERRORS = {403: "Сообщение не доставлено: один из вас заблокировал другого.",
  429: "Слишком быстро — подождите пару секунд."};

function enterPeople() {
  if (peopleOpenOnEnter) {
    const body = peopleOpenOnEnter;
    peopleOpenOnEnter = null;
    openDirectChat(body.dialog_id, body.partner);
    return;
  }
  $("people-search-input").value = "";
  showPeopleList();
  loadPeopleResults("");
}

function leavePeople() {
  stopPeoplePolling();
  clearInterval(peopleListTimer);
  peopleDialog = null;
}

function initPeople() {
  definePage("people", {auth: true, enter: enterPeople, leave: leavePeople});

  $("people-search-input").addEventListener("input", e => {
    clearTimeout(peopleSearchTimer);
    const q = e.target.value.trim();
    peopleSearchTimer = setTimeout(() => loadPeopleResults(q), 300);
  });
  $("people-back-btn").addEventListener("click", showPeopleList);

  document.addEventListener("click", e => {
    const startBtn = e.target.closest("[data-start-direct]");
    if (startBtn) { openDirectWith(Number(startBtn.dataset.startDirect)); return; }
    const openBtn = e.target.closest("[data-open-direct]");
    if (openBtn) {
      const dialog = ($("people-dialogs")._dialogs || []).find(d => d.dialog_id === openBtn.dataset.openDirect);
      if (dialog) openDirectChat(dialog.dialog_id, dialog.partner);
    }
  });

  $("people-send-form").addEventListener("submit", async e => {
    e.preventDefault();
    const input = $("people-chat-text");
    const text = input.value.trim();
    if (!text || !peopleDialog) return;
    input.value = "";
    try {
      await api("/api/chat/direct/send", {method: "POST", body: {dialog_id: peopleDialog.dialog_id, text}});
      pollPeopleChat();
    } catch (err) {
      input.value = text;
      fail(err, "Сообщение не отправлено.", DIRECT_SEND_ERRORS);
    }
  });
  $("people-photo-btn").addEventListener("click", () => $("people-photo-input").click());
  $("people-photo-input").addEventListener("change", async e => {
    const file = e.target.files[0];
    e.target.value = "";
    const payload = await photoPayload(file);
    if (!payload || !peopleDialog) return;
    try {
      await api("/api/chat/direct/send-photo", {method: "POST", body: {dialog_id: peopleDialog.dialog_id, ...payload}});
      pollPeopleChat();
    } catch (err) {
      fail(err, "Не получилось отправить фото.", {...DIRECT_SEND_ERRORS,
        400: "Это не похоже на фото. Подойдут JPG, PNG, WebP или GIF до 5 МБ.", 413: "Фото слишком большое — до 5 МБ."});
    }
  });

  $("people-block-btn").addEventListener("click", async () => {
    if (!peopleDialog) return;
    const name = fullName(peopleDialog.partner);
    if (!await askConfirm(`${name} больше не сможет писать тебе, а ты — ему. Разблокировать можно в профиле.`,
      {title: "Заблокировать?", ok: "Заблокировать", danger: true})) return;
    try {
      await api("/api/chat/block", {method: "POST", body: {user_id: peopleDialog.partner.id}});
      toast(`${name} заблокирован(а).`);
      showPeopleList();
      loadPeopleResults($("people-search-input").value.trim());
    } catch (err) { fail(err, "Не получилось заблокировать."); }
  });
  $("people-report-btn").addEventListener("click", () => {
    if (!peopleDialog) return;
    reportPartner(peopleDialog.dialog_id, () => { showPeopleList(); loadPeopleResults(""); });
  });
}
