function text(tag, content, className = "") {
  const element = document.createElement(tag);
  element.textContent = content;
  element.className = className;
  return element;
}
function modeLabel(mode) {
  const fps = mode.fps_num / mode.fps_den;
  const interlaced = mode.interlace_mode === "interleaved";
  const rate = (interlaced ? fps * 2 : fps).toLocaleString("uk-UA", { maximumFractionDigits: 3 });
  const field = { "top-field-first": "TFF", "bottom-field-first": "BFF" }[mode.field_order];
  const suffix = interlaced ? "i" : mode.interlace_mode === "progressive" ? "p" : ` ${mode.interlace_mode} `;
  return `${mode.width} × ${mode.height}${suffix}${rate} · ${mode.fps_num}/${mode.fps_den} кадр/с${mode.pixel_aspect_ratio ? ` · PAR ${mode.pixel_aspect_ratio}` : ""}${field ? ` · ${field}` : ""}${mode.format ? ` · ${mode.format}` : ""}`;
}
function modesPanel(ports) {
  const modes = new Map();
  const totals = { input: 0, output: 0 };
  for (const port of ports) {
    for (const direction of ["input", "output"]) {
      if (!port[`has_${direction}`]) continue;
      totals[direction] += 1;
      const seen = new Set();
      for (const mode of port[`${direction}_modes`] ?? []) {
        // Use full mode parameters: never collapse fractional rates, PAR or formats.
        const key = JSON.stringify([mode.width, mode.height, mode.fps_num, mode.fps_den, mode.interlace_mode, mode.pixel_aspect_ratio ?? null, mode.field_order ?? null, mode.format ?? null]);
        if (seen.has(key)) continue;
        seen.add(key);
        if (!modes.has(key)) modes.set(key, { mode, input: 0, output: 0 });
        modes.get(key)[direction] += 1;
      }
    }
  }
  const panel = text("details", "", "decklink-modes");
  panel.append(text("summary", `Режими роботи · ${modes.size}`));
  if (modes.size) {
    const list = text("ul", "", "decklink-mode-list");
    const ordered = [...modes.values()].sort((a, b) => a.mode.width - b.mode.width || a.mode.height - b.mode.height || a.mode.fps_num / a.mode.fps_den - b.mode.fps_num / b.mode.fps_den || modeLabel(a.mode).localeCompare(modeLabel(b.mode), "uk"));
    for (const entry of ordered) {
      const common = entry.input === totals.input && entry.output === totals.output;
      const scope = common ? "" : ` · вхід ${entry.input}/${totals.input}, вихід ${entry.output}/${totals.output}`;
      list.append(text("li", modeLabel(entry.mode) + scope));
    }
    panel.append(list);
  } else panel.append(text("p", "Список режимів не отримано.", "muted small-text"));
  return panel;
}
function boardCard(board, ports) {
  const card = text("article", "", "gpu-adapter-card");
  card.append(text("h3", board.model_name));
  card.append(text("p", `Входів: ${ports.filter((port) => port.has_input).length} · Виходів: ${ports.filter((port) => port.has_output).length}`, "muted small-text"));
  if (board.ungrouped) card.append(text("p", "Сумарно для цієї моделі: прив’язка портів до окремих фізичних плат не визначена.", "muted small-text"));
  if (ports.some((port) => port.query_status !== "complete")) card.append(text("p", "Частину даних про порти не підтверджено.", "gpu-message"));
  card.append(modesPanel(ports));
  return card;
}

export function initializeDeckLinkPanel({ request, role }) {
  const panel = document.querySelector("#decklink-panel");
  const name = document.querySelector("#decklink-name");
  const summary = document.querySelector("#decklink-summary");
  const indicator = document.querySelector("#decklink-status");
  const status = document.querySelector("#decklink-query-state");
  const error = document.querySelector("#decklink-error");
  const devices = document.querySelector("#decklink-devices");
  const refresh = document.querySelector("#decklink-refresh");
  let timer;
  let disposed = false;
  let refreshing = false;
  let queryRunning = false;
  refresh.hidden = !["admin", "operator"].includes(role);

  function render(snapshot) {
    queryRunning = snapshot.query_running;
    const ports = snapshot.ports ?? [];
    const boards = snapshot.boards ?? [];
    const ungrouped = ports.filter((port) => !port.board_id);
    name.textContent = ports.length ? `DeckLink · ${ports.length} портів` : snapshot.initialized ? "DeckLink · порти не виявлено" : "DeckLink · перевірка";
    summary.textContent = snapshot.query_running ? "Опитування портів…" : snapshot.stale ? "Попередні дані · потрібна перевірка" : `Вхід: ${ports.filter((port) => port.has_input).length} · Вихід: ${ports.filter((port) => port.has_output).length}`;
    indicator.className = `gpu-status ${snapshot.query_running ? "querying" : snapshot.last_error || snapshot.stale ? "failed" : snapshot.query_status === "complete" ? "complete" : "partial"}`;
    const date = snapshot.queried_at ? new Date(snapshot.queried_at).toLocaleString("uk-UA") : "ще не завершено";
    status.textContent = `${snapshot.from_cache ? "Кеш" : "Останнє опитування"}: ${date}${snapshot.stale ? " · дані потребують перевірки" : ""}`;
    const failures = [snapshot.last_error, ...(snapshot.scan_errors ?? [])].filter(Boolean);
    error.textContent = failures.join("\n");
    error.hidden = !failures.length;
    devices.replaceChildren();
    for (const board of boards) devices.append(boardCard(board, ports.filter((port) => port.board_id === board.board_id)));
    const models = new Map();
    for (const port of ungrouped) {
      if (!models.has(port.model_name)) models.set(port.model_name, []);
      models.get(port.model_name).push(port);
    }
    for (const [model, modelPorts] of models) devices.append(boardCard({ model_name: model, ungrouped: true }, modelPorts));
    if (!ports.length && snapshot.initialized && !snapshot.query_running && !failures.length) devices.append(text("p", "Provider не надав відеопортів DeckLink.", "muted"));
    refresh.disabled = queryRunning || refreshing;
  }
  function schedule(snapshot) {
    clearTimeout(timer);
    if (!disposed && snapshot.query_running) timer = setTimeout(load, 1500);
  }
  function failed(failure) {
    error.textContent = failure.message;
    error.hidden = false;
    indicator.className = "gpu-status failed";
    queryRunning = false;
    refresh.disabled = false;
  }
  async function load() {
    try {
      const snapshot = await request("/api/hardware/decklink-capabilities");
      if (disposed) return;
      render(snapshot);
      schedule(snapshot);
    } catch (failure) { if (!disposed) failed(failure); }
  }
  refresh.addEventListener("click", async () => {
    if (refreshing) return;
    refreshing = true;
    refresh.disabled = true;
    clearTimeout(timer);
    try {
      const snapshot = await request("/api/hardware/decklink-capabilities/refresh", { method: "POST", body: {} });
      if (disposed) return;
      render(snapshot);
      schedule(snapshot);
    } catch (failure) { if (!disposed) failed(failure); }
    finally { refreshing = false; refresh.disabled = queryRunning; }
  });
  document.addEventListener("pointerdown", (event) => { if (panel.open && !panel.contains(event.target)) panel.open = false; });
  panel.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { panel.open = false; panel.querySelector("summary").focus(); }
  });
  // One hardware popup at a time, including keyboard activation of its summary.
  panel.addEventListener("toggle", () => { if (panel.open) document.querySelector("#gpu-panel").open = false; });
  document.querySelector("#gpu-panel").addEventListener("toggle", (event) => { if (event.target.open) panel.open = false; });
  window.addEventListener("pagehide", () => { disposed = true; clearTimeout(timer); });
  window.addEventListener("pageshow", (event) => { if (event.persisted) { disposed = false; load(); } });
  load();
}
