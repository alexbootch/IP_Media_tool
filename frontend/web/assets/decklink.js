function text(tag, content, className = "") {
  const element = document.createElement(tag);
  element.textContent = content;
  element.className = className;
  return element;
}
function parameter(list, label, value) {
  list.append(text("dt", label), text("dd", value ?? "Не надано"));
}
function modeLabel(mode) {
  const fps = mode.fps_num / mode.fps_den;
  const interlaced = mode.interlace_mode === "interleaved";
  const rate = (interlaced ? fps * 2 : fps).toLocaleString("uk-UA", { maximumFractionDigits: 3 });
  const field = { "top-field-first": "TFF", "bottom-field-first": "BFF" }[mode.field_order];
  const suffix = interlaced ? "i" : mode.interlace_mode === "progressive" ? "p" : ` ${mode.interlace_mode} `;
  return `${mode.width} × ${mode.height}${suffix}${rate} · ${mode.fps_num}/${mode.fps_den} кадр/с${mode.pixel_aspect_ratio ? ` · PAR ${mode.pixel_aspect_ratio}` : ""}${field ? ` · ${field}` : ""}${mode.format ? ` · ${mode.format}` : ""}`;
}
function modesPanel(port, direction, label) {
  const modes = port[`${direction}_modes`] ?? [];
  const panel = text("details", "", "decklink-modes");
  panel.append(text("summary", `${label} · ${modes.length} режимів`));
  if (modes.length) {
    const list = text("ul", "", "decklink-mode-list");
    list.append(...modes.map((mode) => text("li", modeLabel(mode))));
    panel.append(list);
  } else panel.append(text("p", "Список режимів не отримано; див. GStreamer caps.", "muted small-text"));
  const raw = text("details", "", "gpu-raw");
  raw.append(text("summary", "GStreamer caps"), text("pre", port[`${direction}_caps`] ?? "Не надано"));
  panel.append(raw);
  return panel;
}
function portCard(port) {
  const panel = text("details", "", "decklink-port");
  const directions = [port.has_input && "Вхід", port.has_output && "Вихід"].filter(Boolean).join(" / ");
  panel.append(text("summary", `${port.display_name} · device ${port.device_number} · ${directions || "Напрямок не визначено"}`));
  const info = text("dl", "", "gpu-parameters");
  parameter(info, "Persistent ID", port.persistent_id);
  parameter(info, "Gst device-number", port.device_number);
  parameter(info, "Ідентифікація", { persistent: "ID від provider", session_index: "Індекс поточного сканування", ambiguous_persistent_id: "Повторний ID · використовується індекс" }[port.identity_status]);
  if (port.has_input) {
    parameter(info, "Автовизначення входу", port.supports_format_detection == null ? null : port.supports_format_detection ? "Так" : "Ні");
    parameter(info, "Аудіоканали входу, до", port.input_max_audio_channels);
  }
  if (port.has_output) parameter(info, "Аудіоканали виходу, до", port.output_max_audio_channels);
  panel.append(info);
  for (const warning of port.warnings ?? []) panel.append(text("p", warning, "gpu-message"));
  if (port.has_input) panel.append(modesPanel(port, "input", "Вхід"));
  if (port.has_output) panel.append(modesPanel(port, "output", "Вихід"));
  return panel;
}
function boardCard(board, ports) {
  const card = text("article", "", "gpu-adapter-card");
  card.append(text("h3", board.model_name));
  card.append(text("p", board.serial_number ? `Серійний номер: ${board.serial_number} · портів: ${ports.length}` : "Серійний номер плати не надано. Цей порт показано окремо; групування з іншими портами не припускається.", "muted small-text"));
  card.append(...ports.map(portCard));
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
    status.textContent = `${snapshot.from_cache ? "Кеш" : "Останнє опитування"}: ${date}${snapshot.stale ? " · дані потребують перевірки" : ""}${snapshot.gstreamer_version ? ` · ${snapshot.gstreamer_version}` : ""}`;
    const failures = [snapshot.last_error, ...(snapshot.scan_errors ?? [])].filter(Boolean);
    error.textContent = failures.join("\n");
    error.hidden = !failures.length;
    const info = text("dl", "", "gpu-parameters");
    const driver = snapshot.driver ?? {};
    parameter(info, "Драйвер завантажений", driver.loaded_driver_version);
    parameter(info, "Драйвер встановлений", driver.installed_driver_version);
    parameter(info, "Desktop Video", driver.desktop_video_package);
    parameter(info, "Gst DeckLink plugin", snapshot.plugin_version);
    parameter(info, "Плат із serial", boards.length);
    parameter(info, "Портів без serial", ungrouped.length);
    parameter(info, "PCI Blackmagic", driver.pci_devices?.map((device) => `${device.pci_bus_id} · ${device.kernel_driver ?? "без драйвера"}`).join(", ") || null);
    devices.replaceChildren(info);
    for (const board of boards) devices.append(boardCard(board, ports.filter((port) => port.board_id === board.board_id)));
    for (const port of ungrouped) devices.append(boardCard({ model_name: port.model_name }, [port]));
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
