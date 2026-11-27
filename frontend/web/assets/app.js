import { api, showError, submitForm } from "/assets/api.js";
import { initializeGpuPanel } from "/assets/gpu.js";
import { initializeDeckLinkPanel } from "/assets/decklink.js";
import { initializeGraph } from "/assets/graph.js";

const $ = (selector) => document.querySelector(selector);
const roles = { admin: "Адміністратор", operator: "Оператор", viewer: "Перегляд" };
let session;
let users = [];
let editing = null;
let passwordTarget = null;
let deleting = null;
let toastTimer;
function request(path, options = {}) {
  if (!session) throw new Error("Зачекайте, поки завантажиться обліковий запис.");
  return api(path, { ...options, csrf: session.csrf });
}
function toast(message, error = false) {
  clearTimeout(toastTimer);
  $("#toast").textContent = message;
  $("#toast").classList.toggle("error", error);
  $("#toast").hidden = false;
  toastTimer = setTimeout(() => { $("#toast").hidden = true; }, 6000);
}
function account() {
  $("#account-name").textContent = session.user.display_name;
  $("#account-role").textContent = roles[session.user.role];
  $("#account-avatar").textContent = session.user.display_name.slice(0, 1).toUpperCase();
  $("#users-nav").hidden = session.user.role !== "admin";
}
function node(tag, text, className = "") {
  const element = document.createElement(tag);
  element.textContent = text;
  element.className = className;
  return element;
}
function action(text, operation, disabled = false, danger = false) {
  const button = node("button", text, danger ? "row-action destructive" : "row-action");
  button.type = "button";
  button.disabled = disabled;
  button.addEventListener("click", operation);
  return button;
}
function renderUsers() {
  const query = $("#user-search").value.trim().toLocaleLowerCase();
  const filtered = users.filter((user) => `${user.username} ${user.display_name}`.toLocaleLowerCase().includes(query));
  const body = $("#user-rows");
  body.replaceChildren();
  $("#user-count").textContent = String(users.length);
  $("#users-empty").hidden = filtered.length > 0;
  for (const user of filtered) {
    const row = document.createElement("tr");
    const identity = document.createElement("td");
    const person = node("div", "", "person-cell");
    person.append(node("span", user.display_name.slice(0, 1).toUpperCase(), "avatar"));
    const details = node("div", "");
    details.append(node("strong", user.display_name), node("span", `${user.username}${user.id === session.user.id ? " · Ви" : ""}`, "muted small-text"));
    person.append(details);
    identity.append(person);
    const role = document.createElement("td");
    role.append(node("span", roles[user.role], `role-badge ${user.role}`));
    const status = document.createElement("td");
    status.append(node("span", user.enabled ? "Активний" : "Вимкнений", user.enabled ? "user-status enabled" : "user-status"));
    const last = node("td", user.last_login ? new Date(user.last_login).toLocaleString("uk-UA", { dateStyle: "short", timeStyle: "short" }) : "Ще не входив", "muted small-text");
    const controls = node("td", "", "align-right");
    controls.append(action("Редагувати", () => openUser(user)), action("Пароль", () => openPassword(user), user.id === session.user.id), action("Видалити", () => openDelete(user), user.id === session.user.id, true));
    row.append(identity, role, status, last, controls);
    body.append(row);
  }
}
async function refreshUsers() {
  const result = await request("/api/users");
  users = result.users;
  renderUsers();
}
async function navigate(page) {
  if (!session) return;
  if (page === "users" && session.user.role !== "admin") return;
  $("#workspace-view").hidden = page !== "workspace";
  $("#graph-sidebar").hidden = page !== "workspace";
  $("#users-view").hidden = page !== "users";
  for (const name of ["workspace", "users"]) {
    const active = page === name;
    $(`#${name}-nav`).classList.toggle("active", active);
    if (active) $(`#${name}-nav`).setAttribute("aria-current", "page");
    else $(`#${name}-nav`).removeAttribute("aria-current");
  }
  $("#breadcrumb-page").textContent = page === "users" ? "Користувачі" : "Робочий простір";
  document.title = `${$("#breadcrumb-page").textContent} · IP Media Tool`;
  if (page === "users") {
    try { await refreshUsers(); } catch (failure) { toast(failure.message, true); }
  }
}
function openUser(user = null) {
  editing = user;
  $("#user-form").reset();
  showError($("#user-form [data-error]"));
  $("#user-dialog-title").textContent = user ? "Редагування користувача" : "Новий користувач";
  $("#user-name").value = user?.display_name ?? "";
  $("#user-login").value = user?.username ?? "";
  $("#user-login").disabled = Boolean(user);
  $("#user-role").value = user?.role ?? "viewer";
  $("#user-role").disabled = user?.id === session.user.id;
  $("#user-enabled").checked = user?.enabled ?? true;
  $("#user-enabled").disabled = user?.id === session.user.id;
  $("#user-enabled-group").hidden = !user;
  $("#new-password-group").hidden = Boolean(user);
  $("#user-password").required = !user;
  $("#user-dialog").showModal();
}
function openPassword(user = null) {
  passwordTarget = user;
  $("#password-form").reset();
  showError($("#password-form [data-error]"));
  $("#password-title").textContent = user ? "Скинути пароль" : "Змінити мій пароль";
  $("#password-description").textContent = user ? `Новий пароль для ${user.display_name} (${user.username}).` : "Після зміни пароля потрібно буде увійти знову.";
  $("#current-password-group").hidden = Boolean(user);
  $("#current-password").required = !user;
  $("#password-dialog").showModal();
}
function openDelete(user) {
  deleting = user;
  showError($("#delete-form [data-error]"));
  $("#delete-description").textContent = `${user.display_name} (${user.username}) втратить доступ до системи. Цю дію неможливо скасувати.`;
  $("#delete-dialog").showModal();
}
document.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => button.closest("dialog").close()));
document.querySelectorAll("dialog").forEach((dialog) => dialog.addEventListener("close", () => dialog.querySelectorAll('input[type="password"]').forEach((input) => { input.value = ""; })));
$("#workspace-nav").addEventListener("click", () => navigate("workspace"));
$("#users-nav").addEventListener("click", () => navigate("users"));
$("#user-search").addEventListener("input", renderUsers);
$("#refresh-users").addEventListener("click", () => refreshUsers().catch((failure) => toast(failure.message, true)));
$("#add-user-button").addEventListener("click", () => openUser());
$("#own-password-button").addEventListener("click", () => openPassword());
$("#logout-button").addEventListener("click", async () => {
  try { await request("/api/auth/logout", { method: "POST", body: {} }); window.location.replace("/login"); }
  catch (failure) { toast(failure.message, true); }
});
$("#user-form").addEventListener("submit", (event) => {
  event.preventDefault();
  submitForm(event.currentTarget, async () => {
    const fields = { display_name: $("#user-name").value.trim(), role: $("#user-role").value };
    if (editing) {
      await request(`/api/users/${editing.id}`, { method: "PATCH", body: { ...fields, enabled: $("#user-enabled").checked, revision: editing.revision } });
    } else {
      await request("/api/users", { method: "POST", body: { ...fields, username: $("#user-login").value.trim(), password: $("#user-password").value } });
    }
    $("#user-dialog").close();
    session = await request("/api/session");
    account();
    await refreshUsers();
    toast(editing ? "Зміни збережено." : "Користувача створено.");
  });
});
$("#password-form").addEventListener("submit", (event) => {
  event.preventDefault();
  submitForm(event.currentTarget, async () => {
    const password = $("#new-password").value;
    if (password !== $("#confirm-password").value) throw new Error("Паролі не збігаються.");
    if (passwordTarget) {
      await request(`/api/users/${passwordTarget.id}/password`, { method: "POST", body: { password } });
      $("#password-dialog").close();
      await refreshUsers();
      toast("Пароль змінено. Сесії користувача завершено.");
    } else {
      await request("/api/auth/password", { method: "POST", body: { password, current_password: $("#current-password").value } });
      window.location.replace("/login");
    }
  });
});
$("#delete-form").addEventListener("submit", (event) => {
  event.preventDefault();
  submitForm(event.currentTarget, async () => {
    await request(`/api/users/${deleting.id}?revision=${deleting.revision}`, { method: "DELETE", body: {} });
    $("#delete-dialog").close();
    await refreshUsers();
    toast("Користувача видалено.");
  });
});
async function initialize() {
  try {
    session = await api("/api/session");
    account();
    initializeGpuPanel({ request, role: session.user.role });
    initializeDeckLinkPanel({ request, role: session.user.role });
    initializeGraph({ request, role: session.user.role, notify: toast });
    $("#main-content").setAttribute("aria-busy", "false");
  } catch (failure) { toast(failure.message, true); }
}
initialize();
