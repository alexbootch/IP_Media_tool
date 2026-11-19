import { api, showError } from "/assets/api.js";

const form = document.querySelector("#login-form");
const password = document.querySelector("#password");
const error = document.querySelector("#login-error");
const submit = document.querySelector("#login-submit");
const toggle = document.querySelector("#toggle-password");

toggle.addEventListener("click", () => {
  const visible = password.type === "password";
  password.type = visible ? "text" : "password";
  toggle.textContent = visible ? "Сховати" : "Показати";
  toggle.setAttribute("aria-pressed", String(visible));
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (submit.disabled) return;
  submit.disabled = true;
  showError(error);
  try {
    await api("/api/auth/login", { method: "POST", redirect: false, body: { username: form.username.value.trim(), password: password.value } });
    password.value = "";
    window.location.replace("/app");
  } catch (failure) {
    showError(error, failure.message);
    submit.disabled = false;
  }
});

async function initialize() {
  try {
    const setup = await api("/api/setup", { redirect: false });
    document.querySelector("#setup-notice").hidden = setup.admin_ready;
    submit.disabled = !setup.admin_ready;
    if (setup.admin_ready) {
      try { await api("/api/session", { redirect: false }); window.location.replace("/app"); }
      catch { /* A logged-out visitor is expected here. */ }
    }
  } catch (failure) { showError(error, failure.message); }
}
initialize();
