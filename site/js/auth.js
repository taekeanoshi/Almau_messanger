"use strict";
// Вход, регистрация (с подтверждением почты кодом) и сброс забытого пароля.

const CODE_COOLDOWN_SECONDS = 45;  // сервер не шлёт письма чаще — кнопка «Отправить ещё раз» ждёт столько же

function afterLogin() {
  let target = "home";
  try { target = sessionStorage.getItem("return_to") || "home"; sessionStorage.removeItem("return_to"); } catch {}
  if (location.hash === "#" + target) route(); else location.hash = "#" + target;
}

function showAuthPanel(name) {
  for (const panel of ["login", "register", "forgot"]) $(`auth-${panel}`).hidden = panel !== name;
  document.querySelectorAll("[data-authtab]").forEach(b => b.classList.toggle("on", b.dataset.authtab === name));
  $("auth-tabs").hidden = name === "forgot";
}

// Кнопка повторной отправки кода с обратным отсчётом.
function startCooldown(btn) {
  const label = btn.dataset.label || (btn.dataset.label = btn.textContent);
  let left = CODE_COOLDOWN_SECONDS;
  btn.disabled = true;
  clearInterval(btn._timer);
  const tick = () => {
    if (left <= 0) { clearInterval(btn._timer); btn.disabled = false; btn.textContent = label; return; }
    btn.textContent = `${label} (${left--})`;
  };
  tick();
  btn._timer = setInterval(tick, 1000);
}

async function resendCode(email, btn, status) {
  try {
    await api("/api/chat/otp/request", {method: "POST", body: {email}, auth: false});
    status.textContent = "Новый код отправлен на почту.";
    startCooldown(btn);
  } catch (err) {
    status.textContent = explain(err, "Не получилось отправить код.", {429: "Код уже отправлен — подождите минуту и попробуйте снова."});
  }
}

async function verifyCode(email, code, status) {
  status.textContent = "Проверяем код…";
  try {
    const body = await api("/api/chat/otp/verify", {method: "POST", body: {email, code}, auth: false});
    status.textContent = "";
    saveSession(body.token, body.user);
    toast("Почта подтверждена. Добро пожаловать!", "ok");
    return true;
  } catch (err) {
    status.textContent = explain(err, "Неверный или устаревший код.", {
      403: err.reason === "banned" ? "Аккаунт заблокирован." : "Слишком много неверных попыток — запросите новый код.",
    });
    return false;
  }
}

function initLogin() {
  const form = $("login-form"), status = $("login-status"), verifyBox = $("login-verify");
  let pendingEmail = null;

  form.addEventListener("submit", async e => {
    e.preventDefault();
    const btn = form.querySelector("button[type=submit]");
    const data = Object.fromEntries(new FormData(form).entries());
    status.textContent = "Входим…";
    btn.disabled = true;
    try {
      const body = await api("/api/chat/login", {method: "POST", body: data, auth: false});
      status.textContent = "";
      form.reset();
      verifyBox.hidden = true;
      saveSession(body.token, body.user);
      afterLogin();
    } catch (err) {
      if (err.status === 403 && err.reason === "email not verified") {
        // Регистрацию бросили на шаге с кодом — даём закончить её прямо отсюда
        pendingEmail = String(data.email).trim().toLowerCase();
        verifyBox.hidden = false;
        await resendCode(pendingEmail, $("login-resend-btn"), status);
        status.textContent = `Почта ещё не подтверждена. ${status.textContent} Введите код ниже.`;
        $("login-code").focus();
      } else {
        status.textContent = explain(err, "Не получилось войти.", {
          401: "Неверная почта или пароль.",
          403: "Аккаунт заблокирован за нарушение правил.",
          429: "Слишком много неудачных попыток. Подождите 15 минут или сбросьте пароль.",
        });
      }
    } finally {
      btn.disabled = false;
    }
  });

  $("login-verify-btn").addEventListener("click", async () => {
    const code = $("login-code").value.trim();
    if (!code || !pendingEmail) return;
    if (await verifyCode(pendingEmail, code, status)) {
      form.reset(); $("login-code").value = ""; verifyBox.hidden = true;
      afterLogin();
    }
  });
  $("login-resend-btn").addEventListener("click", () => { if (pendingEmail) resendCode(pendingEmail, $("login-resend-btn"), status); });
}

// Студенческие поля (курс/специальность/пол/ник) нужны только студенту — преподавателю и
// отделу расписания вместо них нужен ключ-приглашение, который даёт роль.
function updateRegisterFields(form) {
  const isStudent = (form.querySelector('input[name="account_type"]:checked')?.value || "student") === "student";
  $("register-student-fields").hidden = !isStudent;
  $("register-staff-fields").hidden = isStudent;
  form.querySelectorAll('[name="course"], [name="specialty"], [name="gender"]').forEach(el => { el.required = isStudent; });
  form.querySelector('[name="invite_key"]').required = !isStudent;
}

function initRegister() {
  const form = $("register-form"), status = $("register-status");
  const step1 = $("register-step1"), step2 = $("register-step2");
  let pendingEmail = null;

  updateRegisterFields(form);
  form.querySelectorAll('input[name="account_type"]').forEach(r => r.addEventListener("change", () => updateRegisterFields(form)));

  const reset = () => {
    form.reset(); updateRegisterFields(form);
    step1.hidden = false; step2.hidden = true; pendingEmail = null; status.textContent = "";
  };

  form.addEventListener("submit", async e => {
    e.preventDefault();
    if (pendingEmail) return;  // уже на шаге с кодом — Enter в поле кода обрабатывает кнопка ниже
    const btn = $("register-submit-btn");
    const data = Object.fromEntries(new FormData(form).entries());
    delete data.code; delete data.consent;
    status.textContent = "Создаём аккаунт…";
    btn.disabled = true;
    try {
      const body = await api("/api/chat/register", {method: "POST", body: data, auth: false});
      pendingEmail = body.email;
      status.textContent = `Код отправлен на ${body.email}. Введите его, чтобы подтвердить почту.`;
      step1.hidden = true; step2.hidden = false;
      startCooldown($("register-resend-btn"));
      $("register-code").focus();
    } catch (err) {
      status.textContent = explain(err, "Проверьте данные: имя, телефон, почта, пароль от 8 символов.", {
        409: "Этот телефон или почта уже зарегистрированы. Попробуйте войти или сбросить пароль.",
        403: "Неверный или устаревший ключ-приглашение — уточните у администрации.",
      });
    } finally {
      btn.disabled = false;
    }
  });

  const verify = async () => {
    const code = $("register-code").value.trim();
    if (!code || !pendingEmail) return;
    if (await verifyCode(pendingEmail, code, status)) { reset(); afterLogin(); }
  };
  $("register-verify-btn").addEventListener("click", verify);
  $("register-code").addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); verify(); } });
  $("register-resend-btn").addEventListener("click", () => { if (pendingEmail) resendCode(pendingEmail, $("register-resend-btn"), status); });
  $("register-back-btn").addEventListener("click", () => {
    step1.hidden = false; step2.hidden = true; pendingEmail = null;
    status.textContent = "Аккаунт уже создан. Чтобы подтвердить почту позже — просто войдите с этой почтой и паролем.";
  });
}

function initForgot() {
  const form = $("forgot-form"), resetForm = $("reset-form"), status = $("forgot-status");
  let email = null;

  $("forgot-link").addEventListener("click", () => {
    showAuthPanel("forgot");
    form.hidden = false; resetForm.hidden = true; status.textContent = "";
    form.elements.email.value = $("login-form").elements.email.value;
  });
  $("forgot-back").addEventListener("click", () => showAuthPanel("login"));

  form.addEventListener("submit", async e => {
    e.preventDefault();
    const btn = form.querySelector("button[type=submit]");
    email = form.elements.email.value.trim().toLowerCase();
    status.textContent = "Отправляем код…";
    btn.disabled = true;
    try {
      await api("/api/chat/password/forgot", {method: "POST", body: {email}, auth: false});
      form.hidden = true; resetForm.hidden = false;
      status.textContent = `Если аккаунт с почтой ${email} существует, на неё отправлен код. Введите его и новый пароль.`;
      resetForm.elements.code.focus();
    } catch (err) {
      status.textContent = explain(err, "Проверьте адрес почты.", {429: "Код уже отправлен — подождите минуту и попробуйте снова."});
    } finally {
      btn.disabled = false;
    }
  });

  resetForm.addEventListener("submit", async e => {
    e.preventDefault();
    const btn = resetForm.querySelector("button[type=submit]");
    status.textContent = "Меняем пароль…";
    btn.disabled = true;
    try {
      const body = await api("/api/chat/password/reset", {method: "POST", auth: false, body: {
        email, code: resetForm.elements.code.value.trim(), password: resetForm.elements.password.value,
      }});
      resetForm.reset(); status.textContent = "";
      saveSession(body.token, body.user);
      toast("Пароль изменён. На других устройствах выполнен выход.", "ok");
      showAuthPanel("login");
      afterLogin();
    } catch (err) {
      status.textContent = explain(err, "Не получилось сменить пароль.", {
        400: "Пароль должен быть не короче 8 символов.",
        401: "Неверный или устаревший код.",
        403: "Слишком много неверных попыток — запросите новый код.",
        404: "Неверный или устаревший код.",
      });
    } finally {
      btn.disabled = false;
    }
  });
}

function initAuth() {
  definePage("login");
  document.querySelectorAll("[data-authtab]").forEach(btn => btn.addEventListener("click", () => showAuthPanel(btn.dataset.authtab)));
  initLogin();
  initRegister();
  initForgot();
}
