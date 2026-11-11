"use strict";
// Анонимный чат: подбор собеседника по полу (или среди идущих на тот же ивент), сообщения, фото,
// раскрытие личности, жалоба. Состояние подбора живёт на сервере, сюда приходит поллингом.

const CHAT_POLL_MS = 2500;
let chatDialogId = null, chatSince = 0, chatPollTimer = null, chatPolling = false;
let pendingEventId = null;   // ивент, на который ищем компанию (выбран на афише или на главной)

function chatSection(id) {
  ["chat-denied", "chat-search", "chat-waiting", "chat-room"].forEach(s => { $(s).hidden = s !== id; });
}
function eventTitle(eventId) {
  return (siteEvents || []).find(e => e.id === eventId)?.title || "ивент";
}
const currentPreference = () => document.querySelector('input[name="preference"]:checked')?.value || "any";

function stopChatPolling() { clearInterval(chatPollTimer); chatPollTimer = null; }
function startChatPolling() {
  if (!chatPollTimer) chatPollTimer = setInterval(pollChat, CHAT_POLL_MS);
}

function renderSearchScreen() {
  const banner = $("chat-event-banner");
  banner.hidden = !pendingEventId;
  if (pendingEventId) {
    $("chat-event-banner-text").textContent = `Ищем компанию на «${eventTitle(pendingEventId)}» — соединим только с тем, кто тоже ищет компанию на этот ивент.`;
  }
  $("search-btn").textContent = pendingEventId ? "🎟 Найти компанию на ивент" : "🔍 Найти собеседника";
  chatSection("chat-search");
}

// Применяет состояние с сервера: idle / waiting / matched (+ новые сообщения).
function applyChatState(body) {
  if (body.status === "matched") {
    if (chatDialogId !== body.dialog_id) {
      chatDialogId = body.dialog_id;
      chatSince = 0;
      $("chat-box").innerHTML = "";
      pendingEventId = null;
      if (currentPage !== "chat") toast("Собеседник найден! Открой «Анонимный чат».", "ok", 8000);
    }
    $("chat-partner-name").textContent = `Собеседник: ${body.partner_nickname || "Аноним"}`;
    $("chat-event-note").textContent = body.event_title ? `Вы оба ищете компанию на «${body.event_title}». ` : "";
    chatSection("chat-room");
    if (body.messages?.length) chatSince = Math.max(chatSince, appendMessages($("chat-box"), body.messages));
    startChatPolling();
  } else if (body.status === "waiting") {
    $("chat-waiting-text").textContent = body.event_title
      ? `Ищем, кто ещё хочет пойти на «${body.event_title}»…` : "Ищем собеседника…";
    chatSection("chat-waiting");
    startChatPolling();
  } else {
    stopChatPolling();
    if (chatDialogId) {
      chatDialogId = null;
      toast("Собеседник завершил диалог.");
    }
    renderSearchScreen();
  }
}

async function pollChat() {
  if (chatPolling || !chatToken) return;
  chatPolling = true;
  try {
    applyChatState(await api(`/api/chat/poll?since=${chatSince}`));
  } catch (err) {
    if (err instanceof ApiError && (err.status === 401 || err.status === 403)) stopChatPolling();
  } finally {
    chatPolling = false;
  }
}

async function chatAction(path, body, fallback) {
  try {
    applyChatState(await api(path, {method: "POST", body}));
  } catch (err) {
    fail(err, fallback, {403: "Анонимный чат доступен только студенческим аккаунтам.", 404: "Этот ивент больше не в афише."});
    renderSearchScreen();
  }
}

function startChatSearch() {
  $("chat-waiting-text").textContent = "Ищем собеседника…";
  chatSection("chat-waiting");
  const body = {preference: currentPreference()};
  if (pendingEventId) body.event_id = pendingEventId;
  chatAction("/api/chat/search", body, "Не получилось начать поиск.");
}

// Вызывается с афиши и с главной: «Найти компанию» на конкретный ивент.
function startCompanySearch(eventId) {
  pendingEventId = eventId;
  if (location.hash === "#chat") enterChat(); else location.hash = "#chat";
}

async function sendChatPhoto(file) {
  const payload = await photoPayload(file);
  if (!payload || !chatDialogId) return;
  try {
    await api("/api/chat/send-photo", {method: "POST", body: {dialog_id: chatDialogId, ...payload}});
    pollChat();
  } catch (err) {
    fail(err, "Не получилось отправить фото.", {400: "Это не похоже на фото. Подойдут JPG, PNG, WebP или GIF до 5 МБ.",
      409: "Диалог уже завершён.", 413: "Фото слишком большое — до 5 МБ."});
  }
}

async function reportPartner(dialogId, onDone) {
  const reason = await askText("Пожаловаться на собеседника", {
    message: "Опиши, что случилось. Диалог сразу завершится, и вас больше не соединят. Жалобу увидит только модератор.",
    placeholder: "Например: оскорбления, спам, непристойные фото", type: "textarea", ok: "Отправить жалобу", danger: true,
  });
  if (reason === null) return;
  try {
    await api("/api/chat/report", {method: "POST", body: {dialog_id: dialogId, reason}});
    toast("Жалоба отправлена. Спасибо, что помогаешь держать чат безопасным.", "ok");
    onDone();
  } catch (err) { fail(err, "Не получилось отправить жалобу."); }
}

function enterChat() {
  if (!isStudentLike()) { chatSection("chat-denied"); return; }
  if (!chatDialogId && !chatPollTimer) renderSearchScreen();
  pollChat();
}

function initChat() {
  definePage("chat", {auth: true, enter: enterChat});
  // Вышел из аккаунта — подбор и диалог этого браузера больше не актуальны
  sessionListeners.push(() => { if (!chatToken) { stopChatPolling(); chatDialogId = null; pendingEventId = null; } });

  $("search-btn").addEventListener("click", startChatSearch);
  $("chat-event-cancel").addEventListener("click", () => { pendingEventId = null; renderSearchScreen(); });
  $("cancel-search-btn").addEventListener("click", () => {
    stopChatPolling();
    chatAction("/api/chat/stop", undefined, "Не получилось отменить поиск.");
  });
  $("next-btn").addEventListener("click", () => {
    chatDialogId = null;
    stopChatPolling();
    $("chat-waiting-text").textContent = "Ищем следующего собеседника…";
    chatSection("chat-waiting");
    chatAction("/api/chat/next", {preference: currentPreference()}, "Не получилось найти следующего собеседника.");
  });
  $("stop-btn").addEventListener("click", () => {
    chatDialogId = null;
    stopChatPolling();
    chatAction("/api/chat/stop", undefined, "Не получилось завершить диалог.");
  });
  $("reveal-btn").addEventListener("click", async () => {
    if (!chatDialogId) return;
    const ok = await askConfirm("Собеседник увидит твои настоящие имя и фамилию. Отменить это будет нельзя.",
      {title: "Раскрыть личность?", ok: "Раскрыть"});
    if (!ok) return;
    try {
      await api("/api/chat/reveal", {method: "POST", body: {dialog_id: chatDialogId}});
      pollChat();
    } catch (err) { fail(err, "Не получилось раскрыть личность.", {409: "Диалог уже завершён."}); }
  });
  $("report-btn").addEventListener("click", () => {
    if (!chatDialogId) return;
    reportPartner(chatDialogId, () => { chatDialogId = null; stopChatPolling(); renderSearchScreen(); });
  });
  $("photo-btn").addEventListener("click", () => $("photo-input").click());
  $("photo-input").addEventListener("change", e => {
    const file = e.target.files[0];
    e.target.value = "";
    if (file) sendChatPhoto(file);
  });
  $("send-form").addEventListener("submit", async e => {
    e.preventDefault();
    const input = $("chat-text");
    const text = input.value.trim();
    if (!text || !chatDialogId) return;
    input.value = "";
    try {
      await api("/api/chat/send", {method: "POST", body: {dialog_id: chatDialogId, text}});
      pollChat();
    } catch (err) {
      input.value = text;  // не теряем набранное, если отправка не прошла
      fail(err, "Сообщение не отправлено.", {409: "Собеседник уже завершил диалог.", 429: "Слишком быстро — подождите пару секунд."});
    }
  });

  // Если подбор или диалог уже идёт (страницу перезагрузили) — подхватываем его
  if (chatToken && isStudentLike()) pollChat();
}
