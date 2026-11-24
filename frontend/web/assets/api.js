export async function api(path, { method = "GET", body, csrf, redirect = true, signal } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (csrf) headers["X-CSRF-Token"] = csrf;
  let response;
  try {
    response = await fetch(path, { method, headers, credentials: "same-origin", signal, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new Error("Сервер недоступний. Перевірте з’єднання та повторіть дію.");
  }
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && redirect) window.location.assign("/login");
    const message = typeof result.detail === "string" ? result.detail : "Не вдалося виконати дію. Перевірте введені дані.";
    throw Object.assign(new Error(message), { status: response.status, code: result.code, issues: result.issues });
  }
  return result;
}

export function showError(element, message = "") {
  element.textContent = message;
  element.hidden = !message;
}

export async function submitForm(form, operation) {
  const button = form.querySelector('[type="submit"]');
  const error = form.querySelector("[data-error]");
  if (button.disabled) return;
  button.disabled = true;
  showError(error);
  try { await operation(); } catch (failure) { showError(error, failure.message); }
  finally { button.disabled = false; }
}
