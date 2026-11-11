"use strict";
// Профиль: свои данные, смена пароля, выход на других устройствах, чёрный список, удаление аккаунта.

function fillProfileForm() {
  const form = $("profile-form");
  for (const name of ["first_name", "last_name", "nickname", "course", "specialty"]) {
    form.elements[name].value = chatUser[name] || "";
  }
  $("profile-email").textContent = chatUser.email || "—";
  $("profile-phone").textContent = chatUser.phone || "—";
  $("profile-role").textContent = ROLE_NAMES[chatUser.role] || chatUser.role;
  // Курс, специальность и анонимный ник нужны только тем, кому доступен анонимный чат
  $("profile-student-fields").hidden = !isStudentLike();
}

async function loadBlocks() {
  const box = $("profile-blocks");
  try {
    const {blocked} = await api("/api/chat/blocks");
    box.innerHTML = blocked.length ? blocked.map(u => `<div class="line"><span>${esc(fullName(u))}</span>
      <button type="button" class="btn ghost sm" data-unblock="${u.id}">Разблокировать</button></div>`).join("")
      : `<p class="muted small" style="margin:0">Ты никого не блокировал(а).</p>`;
  } catch (err) {
    box.innerHTML = `<p class="muted small" style="margin:0">${esc(explain(err, "Не получилось загрузить список."))}</p>`;
  }
}

function enterProfile() {
  fillProfileForm();
  $("profile-status").textContent = "";
  $("password-status").textContent = "";
  loadBlocks();
}

function initProfile() {
  definePage("profile", {auth: true, enter: enterProfile});

  $("profile-form").addEventListener("submit", async e => {
    e.preventDefault();
    const status = $("profile-status");
    const data = Object.fromEntries(new FormData(e.target).entries());
    if (!isStudentLike()) { delete data.nickname; delete data.course; delete data.specialty; }
    status.textContent = "Сохраняем…";
    try {
      const {user} = await api("/api/chat/profile", {method: "POST", body: data});
      setUser(user);
      fillProfileForm();
      status.textContent = "Сохранено.";
    } catch (err) {
      status.textContent = explain(err, "Не получилось сохранить.", {400: "Имя и фамилия не могут быть пустыми."});
    }
  });

  $("password-form").addEventListener("submit", async e => {
    e.preventDefault();
    const form = e.target, status = $("password-status");
    status.textContent = "Меняем пароль…";
    try {
      await api("/api/chat/password/change", {method: "POST", body: {
        current_password: form.elements.current_password.value, new_password: form.elements.new_password.value}});
      form.reset();
      status.textContent = "Пароль изменён. На других устройствах выполнен выход.";
    } catch (err) {
      status.textContent = explain(err, "Не получилось сменить пароль.", {
        400: "Новый пароль должен быть не короче 8 символов.", 403: "Текущий пароль введён неверно."});
    }
  });

  $("logout-btn").addEventListener("click", logout);
  $("logout-all-btn").addEventListener("click", async () => {
    try {
      await api("/api/chat/logout-all", {method: "POST"});
      toast("На всех остальных устройствах выполнен выход.", "ok");
    } catch (err) { fail(err, "Не получилось."); }
  });

  $("delete-account-btn").addEventListener("click", async () => {
    const password = await askText("Удалить аккаунт навсегда?", {
      message: "Будут стёрты профиль, все переписки, оценки, сдачи заданий, результаты и записи экзаменов. Восстановить ничего нельзя.\n\nДля подтверждения введи пароль.",
      placeholder: "Пароль", type: "password", ok: "Удалить навсегда", danger: true, required: true,
    });
    if (!password) return;
    try {
      await api("/api/chat/account/delete", {method: "POST", body: {password}});
      clearSession();
      toast("Аккаунт удалён.");
      location.hash = "#home";
      route();
    } catch (err) {
      fail(err, "Не получилось удалить аккаунт.", {403: "Пароль введён неверно — аккаунт не удалён.",
        409: "Единственный администратор не может удалить свой аккаунт: сначала назначь другого."});
    }
  });

  document.addEventListener("click", async e => {
    const btn = e.target.closest("[data-unblock]");
    if (!btn) return;
    try {
      await api("/api/chat/block", {method: "POST", body: {user_id: Number(btn.dataset.unblock), blocked: false}});
      loadBlocks();
    } catch (err) { fail(err, "Не получилось разблокировать."); }
  });
}
