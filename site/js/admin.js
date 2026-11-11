"use strict";
// Админка (только роль admin): пользователи, роли, блокировки, жалобы, ключи-приглашения, заявки, походы.

let adminTab = "users", adminSearchTimer = null;

const ADMIN_TILES = [
  ["users", "аккаунтов"], ["students", "студентов"], ["teachers", "преподавателей"], ["registrars", "отдел расписания"],
  ["banned", "заблокировано"], ["open_reports", "открытых жалоб"], ["dialogs", "диалогов"], ["messages", "сообщений"],
  ["courses", "дисциплин"], ["leads", "заявок"], ["searching_now", "ищут собеседника"],
];

async function loadAdminOverview() {
  try {
    const s = await api("/api/admin/overview");
    $("admin-tiles").innerHTML = ADMIN_TILES.map(([key, label]) => `<div><b>${s[key] ?? 0}</b><span>${label}</span></div>`).join("");
  } catch (err) { $("admin-tiles").innerHTML = `<div class="empty">${esc(explain(err, "Не получилось загрузить сводку."))}</div>`; }
}

function adminUserRow(u) {
  const self = u.id === chatUser.id;
  const roleSelect = `<select data-admin-role="${u.id}" aria-label="Роль">${Object.entries(ROLE_NAMES).map(([value, label]) =>
    `<option value="${value}" ${u.role === value ? "selected" : ""}>${label}</option>`).join("")}</select>`;
  const flags = [
    u.banned ? `<span class="pill bad">заблокирован</span>` : "",
    u.email_verified ? "" : `<span class="pill warn">почта не подтверждена</span>`,
    self ? `<span class="pill acc">это вы</span>` : "",
  ].join(" ");
  return `<div class="urow">
    <div><b>${esc(fullName(u))}</b> ${flags}<small>${esc([courseLabel(u.course), u.specialty].filter(Boolean).join(" · ") || "—")}</small></div>
    <div><span>${esc(u.email || "—")}</span><small>${esc(u.phone || "")} · с ${esc(fmtDate(u.created_at))}</small></div>
    ${roleSelect}
    ${self ? "<span></span>" : `<button type="button" class="btn sm ${u.banned ? "ghost" : "danger"}" data-admin-ban="${u.id}" data-banned="${u.banned ? 0 : 1}">${u.banned ? "Разблокировать" : "Заблокировать"}</button>`}
  </div>`;
}

async function loadAdminUsers() {
  const q = $("admin-user-search")?.value.trim() || "";
  const list = $("admin-users-list");
  if (!list) return;
  try {
    const {users} = await api(`/api/admin/users?q=${encodeURIComponent(q)}`);
    list.innerHTML = users.length ? users.map(adminUserRow).join("") : `<div class="empty">Никого не нашлось.</div>`;
  } catch (err) { list.innerHTML = `<div class="empty">${esc(explain(err, "Не получилось загрузить пользователей."))}</div>`; }
}

function renderAdminUsers() {
  $("admin-body").innerHTML = `<input type="text" class="people-search" id="admin-user-search" placeholder="Имя, почта или телефон…" autocomplete="off" maxlength="120">
    <div id="admin-users-list"><div class="empty">Загрузка…</div></div>`;
  loadAdminUsers();
}

function reportCard(r) {
  const excerpt = r.messages.length
    ? `<div class="excerpt">${r.messages.map(m => `<p class="${m.from_target ? "t" : ""}"><b>${m.from_target ? esc(r.target.name) : "Заявитель"}:</b> ${esc(m.text)}</p>`).join("")}</div>`
    : `<p class="muted small">Сообщений в диалоге нет (или они уже удалены по сроку хранения).</p>`;
  return `<div class="card" style="margin-bottom:10px">
    <div class="row between"><b>${esc(r.reporter.name)} → ${esc(r.target.name)}</b>
      <span class="row">${r.target.banned ? `<span class="pill bad">заблокирован</span>` : ""}
        <span class="pill">жалоб от разных людей: ${r.target.reporters}</span>
        ${r.resolved ? `<span class="pill ok">закрыта</span>` : ""}</span></div>
    <p class="muted small" style="margin:6px 0 0">${esc(fmtWhen(r.created_at))}${r.reason ? ` · Причина: ${esc(r.reason)}` : " · причина не указана"}</p>
    ${excerpt}
    <div class="actions">
      <button type="button" class="btn sm ${r.target.banned ? "ghost" : "danger"}" data-admin-ban="${r.target.id}" data-banned="${r.target.banned ? 0 : 1}">${r.target.banned ? "Разблокировать" : "Заблокировать"} ${esc(r.target.name)}</button>
      ${r.resolved ? "" : `<button type="button" class="btn ghost sm" data-admin-resolve="${r.id}">Закрыть жалобу</button>`}
    </div></div>`;
}

async function renderAdminReports() {
  const body = $("admin-body");
  body.innerHTML = `<div class="empty">Загрузка…</div>`;
  try {
    const {reports} = await api("/api/admin/reports");
    body.innerHTML = reports.length ? reports.map(reportCard).join("")
      : `<div class="empty">Открытых жалоб нет.</div>`;
  } catch (err) { body.innerHTML = `<div class="empty">${esc(explain(err, "Не получилось загрузить жалобы."))}</div>`; }
}

function renderAdminKeys() {
  $("admin-body").innerHTML = `<div class="card stack">
    <p style="margin:0">Преподаватели и отдел расписания регистрируются сами — по ключу-приглашению. Новый ключ сразу отменяет
      предыдущий, так что его можно менять хоть после каждой регистрации. Ключ показывается один раз и нигде не хранится.</p>
    <div class="actions">
      <button type="button" class="btn" data-admin-key="teacher">Новый ключ для преподавателей</button>
      <button type="button" class="btn ghost" data-admin-key="registrar">Новый ключ для отдела расписания</button>
    </div>
    <div id="admin-key-out"></div>
    <p class="muted small" style="margin:0">Уже зарегистрированному аккаунту роль можно сменить во вкладке «Пользователи».</p>
  </div>`;
}

async function renderAdminLeads() {
  const body = $("admin-body");
  body.innerHTML = `<div class="empty">Загрузка…</div>`;
  const kinds = {dtmm: "Билет на DTMM (Remus)", "open-day": "Волонтёр на Open Day (Hunters)"};
  try {
    const {leads} = await api("/api/admin/leads");
    body.innerHTML = leads.length ? leads.map(l => `<div class="card" style="margin-bottom:8px">
      <div class="row between"><b>${esc(l.name)} · ${esc(l.contact)}</b><span class="muted small">${esc(fmtWhen(l.created_at))}</span></div>
      <p class="muted small" style="margin:6px 0 0">${esc(kinds[l.event_id] || (l.event_id ? `Ивент: ${l.event_id}` : "Заявка с главной"))}</p>
      ${l.message ? `<p style="margin:6px 0 0">${esc(l.message)}</p>` : ""}</div>`).join("")
      : `<div class="empty">Заявок пока нет.</div>`;
  } catch (err) { body.innerHTML = `<div class="empty">${esc(explain(err, "Не получилось загрузить заявки."))}</div>`; }
}

async function renderAdminHikes() {
  const body = $("admin-body");
  body.innerHTML = `<div class="empty">Загрузка…</div>`;
  try {
    const {hikes} = await api("/api/admin/hike-signups");
    body.innerHTML = hikes.length ? hikes.map(h => `<div class="card" style="margin-bottom:10px">
      <h3>${esc(h.title)} · ${esc(fmtStart(h.start))}<span class="pill">${h.going} из ${h.capacity}${h.waitlist ? ` · ожидают: ${h.waitlist}` : ""}</span></h3>
      ${h.signups.length ? h.signups.map(s => `<div class="line"><span>${esc(s.full_name)}<small>${esc(s.phone)}${s.src === "web" ? " · с сайта" : " · из бота"}</small></span>
        <span class="pill ${s.status === "going" ? "ok" : "warn"}">${s.status === "going" ? "идёт" : "лист ожидания"}${s.checked_in ? " · спустился" : ""}</span></div>`).join("")
        : `<p class="muted small" style="margin:0">Пока никто не записался.</p>`}</div>`).join("")
      : `<div class="empty">Ближайших походов нет.</div>`;
  } catch (err) { body.innerHTML = `<div class="empty">${esc(explain(err, "Не получилось загрузить походы."))}</div>`; }
}

const ADMIN_TABS = {users: renderAdminUsers, reports: renderAdminReports, keys: renderAdminKeys, leads: renderAdminLeads, hikes: renderAdminHikes};

function showAdminTab(tab) {
  adminTab = tab in ADMIN_TABS ? tab : "users";
  document.querySelectorAll("[data-admin-tab]").forEach(b => b.classList.toggle("on", b.dataset.adminTab === adminTab));
  ADMIN_TABS[adminTab]();
}

function initAdmin() {
  definePage("admin", {auth: true, roles: ["admin"], enter() { loadAdminOverview(); showAdminTab(adminTab); }});

  document.addEventListener("input", e => {
    if (e.target.id !== "admin-user-search") return;
    clearTimeout(adminSearchTimer);
    adminSearchTimer = setTimeout(loadAdminUsers, 300);
  });

  document.addEventListener("change", async e => {
    const select = e.target.closest("[data-admin-role]");
    if (!select) return;
    try {
      await api("/api/admin/users/role", {method: "POST", body: {user_id: Number(select.dataset.adminRole), role: select.value}});
      toast(`Роль изменена: ${ROLE_NAMES[select.value]}.`, "ok");
      loadAdminOverview();
      if (Number(select.dataset.adminRole) === chatUser.id) refreshMe();
    } catch (err) {
      fail(err, "Не получилось изменить роль.", {409: "Нельзя снять роль с единственного администратора."});
    }
    loadAdminUsers();
  });

  document.addEventListener("click", async e => {
    const tabBtn = e.target.closest("[data-admin-tab]");
    if (tabBtn) { showAdminTab(tabBtn.dataset.adminTab); return; }

    const banBtn = e.target.closest("[data-admin-ban]");
    if (banBtn) {
      const banned = banBtn.dataset.banned === "1";
      if (banned && !await askConfirm("Аккаунт сразу потеряет доступ к сайту на всех устройствах.", {title: "Заблокировать?", ok: "Заблокировать", danger: true})) return;
      try {
        await api("/api/admin/users/ban", {method: "POST", body: {user_id: Number(banBtn.dataset.adminBan), banned}});
        toast(banned ? "Аккаунт заблокирован." : "Аккаунт разблокирован.", "ok");
        loadAdminOverview();
        showAdminTab(adminTab);
      } catch (err) { fail(err, "Не получилось."); }
      return;
    }
    const resolveBtn = e.target.closest("[data-admin-resolve]");
    if (resolveBtn) {
      try {
        await api("/api/admin/reports/resolve", {method: "POST", body: {report_id: Number(resolveBtn.dataset.adminResolve)}});
        loadAdminOverview();
        renderAdminReports();
      } catch (err) { fail(err, "Не получилось закрыть жалобу."); }
      return;
    }
    const keyBtn = e.target.closest("[data-admin-key]");
    if (keyBtn) {
      const role = keyBtn.dataset.adminKey;
      try {
        const {invite_key} = await api("/api/admin/invite-key", {method: "POST", body: {role}});
        $("admin-key-out").innerHTML = `<p class="small muted" style="margin:0 0 6px">Ключ для роли «${ROLE_NAMES[role]}» — передай его человеку лично. Прежний ключ больше не действует.</p>
          <div class="keybox" id="admin-key-value">${esc(invite_key)}</div>
          <button type="button" class="btn ghost sm" id="admin-key-copy" style="margin-top:10px">Скопировать</button>`;
      } catch (err) { fail(err, "Не получилось создать ключ."); }
      return;
    }
    if (e.target.closest("#admin-key-copy")) {
      try {
        await navigator.clipboard.writeText($("admin-key-value").textContent);
        toast("Ключ скопирован.", "ok");
      } catch { toast("Не получилось скопировать — выдели ключ и скопируй вручную."); }
    }
  });
}
