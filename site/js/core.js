"use strict";
// Ядро сайта: адрес API, сессия, запросы, уведомления, маршрутизация и меню.
// Остальные файлы в js/ — отдельные разделы, они регистрируют свои страницы через definePage().

// Адрес бэкенда (bot/api_server.py) задаётся в index.html: <meta name="pople-api" content="...">.
// При смене домена API нужно поправить и его, и connect-src в vercel.json.
const API = (document.querySelector('meta[name="pople-api"]')?.content || "").replace(/\/$/, "");

const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const store = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch {} },
  del(key) { try { localStorage.removeItem(key); } catch {} },
};

const MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
const WEEKDAYS = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
const WEEKDAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const WEEKDAY_FULL = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"];
const ROLE_NAMES = {student: "Студент", teacher: "Преподаватель", registrar: "Отдел расписания", admin: "Администратор"};

// «2 курс» для цифр, как есть — для «Магистратура» и т.п.
const courseLabel = c => !c ? "" : /^\d+$/.test(c) ? `${c} курс` : c;
const fullName = u => `${u.first_name || ""} ${u.last_name || ""}`.trim();
const initials = u => ((u.first_name || "?")[0] + (u.last_name || "")[0]).toUpperCase();

// Время сообщения: «14:05» для сегодняшних, «9 ноя, 14:05» для остальных.
function fmtWhen(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const time = d.toLocaleTimeString("ru-RU", {hour: "2-digit", minute: "2-digit"});
  return d.toDateString() === new Date().toDateString() ? time : `${d.getDate()} ${MONTHS[d.getMonth()].slice(0, 3)}, ${time}`;
}
// «2026-11-20» → «20 ноября»
function fmtDate(isoDate) {
  const [y, m, d] = String(isoDate || "").slice(0, 10).split("-").map(Number);
  return y && m && d ? `${d} ${MONTHS[m - 1]}` : String(isoDate || "");
}
function fmtDuration(seconds) {
  const s = Math.max(0, Math.round(seconds || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
}

// ---------- уведомления и диалоги ----------
function toast(message, kind = "info", ms = 4500) {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.setAttribute("role", kind === "error" ? "alert" : "status");
  el.textContent = message;
  $("toasts").appendChild(el);
  setTimeout(() => el.remove(), ms);
}

// Модальное окно с произвольным содержимым. closed — промис с returnValue: "ok" или "" (отмена/Esc).
function openModal(html, {wide = false} = {}) {
  const dialog = document.createElement("dialog");
  dialog.className = "modal" + (wide ? " wide" : "");
  dialog.innerHTML = html;
  document.body.appendChild(dialog);
  dialog.addEventListener("click", e => {
    const btn = e.target.closest("[data-close]");
    if (btn) dialog.close(btn.dataset.close);
  });
  dialog.showModal();
  const closed = new Promise(resolve => dialog.addEventListener("close", () => {
    resolve(dialog.returnValue);
    dialog.remove();
  }));
  return {dialog, closed};
}

async function askConfirm(message, {title = "Подтвердите", ok = "Да", danger = false} = {}) {
  const {closed} = openModal(`<h3>${esc(title)}</h3><p>${esc(message)}</p>
    <div class="actions"><button type="button" class="btn ghost" data-close="">Отмена</button>
    <button type="button" class="btn ${danger ? "danger" : ""}" data-close="ok">${esc(ok)}</button></div>`);
  return (await closed) === "ok";
}

// Окно с одним полем ввода (причина жалобы, пароль для удаления аккаунта). null — отмена.
async function askText(title, {message = "", placeholder = "", ok = "Отправить", type = "text", danger = false, required = false} = {}) {
  const field = type === "textarea"
    ? `<textarea class="input" name="value" maxlength="300" placeholder="${esc(placeholder)}" style="min-height:90px"></textarea>`
    : `<input class="input" name="value" type="${type}" placeholder="${esc(placeholder)}" autocomplete="off">`;
  const {dialog, closed} = openModal(`<form method="dialog"><h3>${esc(title)}</h3>${message ? `<p>${esc(message)}</p>` : ""}
    ${field}
    <div class="actions" style="margin-top:16px"><button type="button" class="btn ghost" data-close="">Отмена</button>
    <button type="submit" class="btn ${danger ? "danger" : ""}" value="ok">${esc(ok)}</button></div></form>`);
  const input = dialog.querySelector("[name=value]");
  input.required = required;
  input.focus();
  let text = "";
  dialog.querySelector("form").addEventListener("submit", () => { text = input.value.trim(); });
  return (await closed) === "ok" ? text : null;
}

// ---------- запросы к API ----------
class ApiError extends Error {
  constructor(status, reason) { super(reason || String(status)); this.status = status; this.reason = reason || ""; }
}

let chatToken = null, chatUser = null;

async function request(path, {method = "GET", body, auth = true} = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (auth && chatToken) headers.Authorization = `Bearer ${chatToken}`;
  let r;
  try {
    r = await fetch(API + path, {method, headers, body: body !== undefined ? JSON.stringify(body) : undefined});
  } catch {
    throw new ApiError(0, "network");
  }
  if (r.ok) return r;
  // aiohttp отдаёт причину текстом вида «403: banned» — по ней различаем похожие ошибки
  const reason = (await r.text().catch(() => "")).replace(/^\d+:\s*/, "").trim();
  if (r.status === 401 && auth && chatToken) sessionExpired();
  throw new ApiError(r.status, reason);
}

// JSON-запрос: api("/api/...", {method: "POST", body: {...}}) → разобранный ответ или ApiError.
async function api(path, opts) {
  return (await request(path, opts)).json();
}
async function apiBlob(path) {
  return (await request(path)).blob();
}

// Понятный человеку текст ошибки. specific — свои сообщения по коду ответа: {403: "…", 409: "…"}.
function explain(err, fallback = "Не получилось. Попробуйте ещё раз.", specific = {}) {
  if (!(err instanceof ApiError)) { console.error(err); return fallback; }
  if (specific[err.status]) return specific[err.status];
  if (err.status === 0) return "Нет связи с сервером. Проверьте интернет и попробуйте ещё раз.";
  if (err.status === 401) return "Сессия закончилась — войдите снова.";
  if (err.status === 429) return "Слишком много попыток. Подождите немного и повторите.";
  if (err.status === 413) return "Файл слишком большой.";
  if (err.status >= 500) return "На сервере что-то сломалось. Попробуйте чуть позже.";
  return fallback;
}
function fail(err, fallback, specific) {
  if (err instanceof ApiError && err.status === 401) return;  // про истёкшую сессию уже сказал sessionExpired()
  toast(explain(err, fallback, specific), "error");
}

// ---------- сессия ----------
function hasRole(...roles) { return !!chatUser && roles.includes(chatUser.role); }
const isStudentLike = () => hasRole("student", "admin");   // кому доступны анонимный чат, ивенты, походы
const isStaff = () => hasRole("teacher", "registrar", "admin");

function saveSession(token, user) {
  chatToken = token; chatUser = user;
  store.set("chat_token", token); store.set("chat_user", JSON.stringify(user));
  onSessionChanged();
}
function setUser(user) {
  chatUser = user;
  store.set("chat_user", JSON.stringify(user));
  renderNav();
}
function clearSession() {
  chatToken = null; chatUser = null;
  store.del("chat_token"); store.del("chat_user");
  onSessionChanged();
}
function loadSession() {
  const token = store.get("chat_token"), user = store.get("chat_user");
  if (token && user) {
    try { chatUser = JSON.parse(user); chatToken = token; } catch { chatToken = chatUser = null; }
  }
}
let expiredNoticeShown = false;
function sessionExpired() {
  if (!chatToken) return;
  clearSession();
  if (!expiredNoticeShown) {
    expiredNoticeShown = true;
    toast("Сессия закончилась — войдите снова.", "error");
    setTimeout(() => { expiredNoticeShown = false; }, 5000);
  }
  route();
}
// Роль и профиль могли измениться на сервере (админ выдал роль) — подтягиваем при загрузке.
async function refreshMe() {
  if (!chatToken) return;
  try { setUser(await api("/api/chat/me")); route(); } catch {}
}

const sessionListeners = [];
function onSessionChanged() {
  renderNav();
  sessionListeners.forEach(fn => { try { fn(); } catch (e) { console.error(e); } });
}

async function logout() {
  try { await api("/api/chat/logout", {method: "POST"}); } catch {}
  clearSession();
  location.hash = "#home";
  route();
}

// ---------- файлы ----------
function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}
const MAX_UPLOAD_MB = 10;
async function fileFieldToPayload(fileInput) {
  const file = fileInput && fileInput.files && fileInput.files[0];
  if (!file) return null;
  if (file.size > MAX_UPLOAD_MB * 1024 * 1024) throw new ApiError(413, "too large");
  return {filename: file.name, content_type: file.type || "application/octet-stream", data: await readFileAsBase64(file)};
}
async function downloadFile(path, filename) {
  try {
    const blob = await apiBlob(path);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename || "file";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  } catch (err) { fail(err, "Не получилось скачать файл."); }
}
// Картинки из API закрыты авторизацией — простой <img src> не сработает, грузим через fetch.
function loadAuthedImage(img, path) {
  apiBlob(path).then(blob => { img.src = URL.createObjectURL(blob); }).catch(() => { img.alt = "Не удалось загрузить фото"; });
}
const MAX_PHOTO_MB = 5;
async function photoPayload(file) {
  if (!file) return null;
  if (file.size > MAX_PHOTO_MB * 1024 * 1024) { toast(`Фото больше ${MAX_PHOTO_MB} МБ — выберите поменьше.`, "error"); return null; }
  return {data: await readFileAsBase64(file)};
}

// Общая отрисовка сообщений чата (анонимного и прямого) в контейнер box.
function appendMessages(box, messages, revealLabel) {
  const stick = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  let last = 0;
  for (const m of messages) {
    const div = document.createElement("div");
    if (m.kind === "photo") {
      div.className = "msg photo " + (m.mine ? "r" : "l");
      const img = document.createElement("img");
      img.alt = "Фото из чата";
      div.appendChild(img);
      loadAuthedImage(img, `/api/chat/photo/${m.photo_id}`);
    } else if (m.kind === "reveal") {
      div.className = "msg reveal";
      div.textContent = (m.mine ? "Вы раскрыли личность: " : "Собеседник раскрылся: ") + m.text;
    } else {
      div.className = "msg " + (m.mine ? "r" : "l");
      div.textContent = m.text;
    }
    if (m.kind !== "reveal" && m.at) {
      const time = document.createElement("time");
      time.textContent = fmtWhen(m.at);
      div.appendChild(time);
    }
    box.appendChild(div);
    last = Math.max(last, m.id);
  }
  if (messages.length && stick) box.scrollTop = box.scrollHeight;
  return last;
}

// ---------- маршрутизация ----------
// definePage("academics", {auth: true, roles: [...], enter() {...}, leave() {...}})
const PAGES = {};
let currentPage = null;
function definePage(id, opts = {}) { PAGES[id] = opts; }

function route() {
  let id = (location.hash || "#home").slice(1) || "home";
  if (!(id in PAGES)) id = "home";
  const page = PAGES[id];
  if (page.auth && !chatToken) {
    try { sessionStorage.setItem("return_to", id); } catch {}
    id = "login";
  } else if (id === "login" && chatToken) {
    id = "home";
  } else if (page.roles && !hasRole(...page.roles)) {
    id = "home";
  }
  if (currentPage && currentPage !== id && PAGES[currentPage].leave) PAGES[currentPage].leave();
  const changed = currentPage !== id;
  currentPage = id;
  document.querySelectorAll(".page").forEach(x => x.classList.toggle("on", x.id === "p-" + id));
  document.querySelectorAll("#nav-drawer .tab").forEach(x => x.classList.toggle("on", x.dataset.p === id));
  if (changed) window.scrollTo(0, 0);
  closeNavDrawer();
  if (PAGES[id].enter) PAGES[id].enter();
}

// ---------- меню ----------
function closeNavDrawer() {
  $("nav-drawer").classList.remove("open");
  $("nav-menu-btn").setAttribute("aria-expanded", "false");
}
function toggleNavDrawer() {
  const open = $("nav-drawer").classList.toggle("open");
  $("nav-menu-btn").setAttribute("aria-expanded", open ? "true" : "false");
}

let unreadCount = 0;
// Вкладки видны только после входа — до этого в навбаре одна заметная кнопка «Войти / Регистрация».
// Состав меню зависит от роли: сотрудникам не показываем анонимный чат, админу — добавляем админку.
function renderNav() {
  const authed = !!chatToken;
  $("nav-auth-btn").hidden = authed;
  $("nav-menu-btn").hidden = !authed;
  const drawer = $("nav-drawer");
  if (!authed) { drawer.innerHTML = ""; closeNavDrawer(); return; }

  const link = (p, label, extra = "") => `<a class="tab ${currentPage === p ? "on" : ""}" href="#${p}" data-p="${p}"><span>${label}</span>${extra}</a>`;
  const unread = unreadCount ? `<span class="count">${unreadCount}</span>` : "";
  const items = [
    `<div class="nav-user"><b>${esc(fullName(chatUser))}</b><span>${esc(ROLE_NAMES[chatUser.role] || "")}</span></div>`,
    link("home", "🏠 Главная"),
    link("academics", "🎓 Учёба"),
    isStudentLike() ? link("chat", "💬 Анонимный чат") : "",
    link("people", "✉️ Сообщения", unread),
    `<div class="sep"></div>`,
    link("events", "🎉 Ивенты"),
    link("clubs", "🎭 Клубы"),
    link("summit", "🏔 Summit Seekers"),
    link("remus", "🐺 Remus"),
    link("hunters", "🦅 Hunters"),
    `<div class="sep"></div>`,
    hasRole("admin") ? link("admin", "🛠 Админка") : "",
    link("profile", "👤 Профиль"),
    `<button type="button" class="tab" id="nav-logout-btn"><span>↩ Выйти</span></button>`,
  ];
  drawer.innerHTML = items.join("");
  const badge = $("nav-menu-btn").querySelector(".count");
  if (badge) badge.remove();
  if (unreadCount) $("nav-menu-btn").insertAdjacentHTML("beforeend", `<span class="count">${unreadCount}</span>`);
}

// Значок непрочитанных сообщений: обновляется раз в 30 секунд, пока вкладка на экране.
let unreadTimer = null;
async function refreshUnread() {
  if (!chatToken || document.hidden) return;
  try {
    const {unread} = await api("/api/chat/unread");
    if (unread !== unreadCount) { unreadCount = unread; renderNav(); }
  } catch {}
}
function syncUnreadPolling() {
  clearInterval(unreadTimer);
  unreadTimer = null;
  unreadCount = 0;
  if (chatToken) { refreshUnread(); unreadTimer = setInterval(refreshUnread, 30000); }
}

function initCore() {
  loadSession();
  $("nav-menu-btn").addEventListener("click", e => { e.stopPropagation(); toggleNavDrawer(); });
  document.addEventListener("click", e => {
    if (e.target.closest("#nav-logout-btn")) { logout(); return; }
    if (e.target.closest("#nav-drawer .tab")) { closeNavDrawer(); return; }
    if (!e.target.closest("#nav-drawer") && !e.target.closest("#nav-menu-btn")) closeNavDrawer();
    // Плавная прокрутка к блоку на той же странице: <a data-scroll="id">
    const scroller = e.target.closest("[data-scroll]");
    if (scroller) {
      e.preventDefault();
      const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
      $(scroller.dataset.scroll)?.scrollIntoView({behavior: calm ? "auto" : "smooth"});
    }
    // Показать/скрыть пароль
    const eye = e.target.closest("[data-toggle-password]");
    if (eye) {
      const input = eye.parentElement.querySelector("input");
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      eye.textContent = show ? "Скрыть" : "Показать";
    }
  });
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeNavDrawer(); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshUnread(); });
  sessionListeners.push(syncUnreadPolling);
  addEventListener("hashchange", route);
}

// Запуск — после того как все файлы разделов зарегистрировали свои страницы (см. js/main.js).
function startApp() {
  renderNav();
  syncUnreadPolling();
  route();
  refreshMe();
  // Service worker — без него Android не предложит «Установить приложение»
  if ("serviceWorker" in navigator) {
    addEventListener("load", () => { navigator.serviceWorker.register("sw.js").catch(() => {}); });
  }
}
