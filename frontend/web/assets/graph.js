import { showError } from "/assets/api.js";

const STATUS = {
  stopped: "Зупинено", starting: "Запуск…", stopping: "Зупинка…", running: "Працює",
  waiting_source: "Очікує джерело", waiting_signal: "Очікує сигнал", error: "Помилка воркера",
};
const SVG_NS = "http://www.w3.org/2000/svg";
const LIMIT = 100000;

/** Make a plain element, keeping server content out of HTML and CSS strings. */
function element(tag, label = "", className = "") {
  const item = document.createElement(tag);
  item.textContent = label;
  item.className = className;
  return item;
}

/** Make a button that never submits its surrounding form. */
function button(label, action, className = "button subtle") {
  const item = element("button", label, className);
  item.type = "button";
  item.addEventListener("click", action);
  return item;
}

/** Generate a v4 ID on both HTTPS and the documented local HTTP deployment. */
function nodeId() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/** Keep a large imported coordinate from overflowing browser layout limits. */
function drawingCoordinate(value) {
  return Math.max(-LIMIT, Math.min(LIMIT, value));
}

/** Present server validation details without rendering arbitrary markup. */
function failureText(failure) {
  const detail = Array.isArray(failure.issues) ? failure.issues.slice(0, 3).map(({ path, message }) => `${path || "/"} — ${message}`).join(" · ") : "";
  const conflict = failure.code === "revision_conflict" ? " Онови граф перед повторною спробою." : "";
  return `${failure.message}${detail ? ` ${detail}` : ""}${conflict}`;
}

/** Attach the editor to the authenticated workspace and its existing APIs. */
export function initializeGraph({ request, role }) {
  const $ = (id) => document.getElementById(id);
  const ui = {
    viewport: $("graph-viewport"), plane: $("graph-plane"), cards: $("graph-nodes"), edges: $("graph-edges"),
    add: $("graph-add"), reload: $("graph-reload"), cancelEdge: $("graph-cancel-edge"), runtime: $("graph-runtime"),
    notice: $("graph-notice"), error: $("graph-error"), empty: $("graph-empty"), dialog: $("graph-dialog"),
    form: $("graph-form"), type: $("graph-type"), name: $("graph-name"), enabled: $("graph-enabled"),
    x: $("graph-x"), y: $("graph-y"), settings: $("graph-settings"), connections: $("graph-connections"),
    moduleSettings: $("graph-module-settings"), jsonSettings: $("graph-json-settings"),
    formError: $("graph-form-error"), save: $("graph-save"), close: $("graph-dialog-close"),
    logDialog: $("graph-log-dialog"), logTitle: $("graph-log-title"), logLines: $("graph-log-lines"),
    logError: $("graph-log-error"), logRefresh: $("graph-log-refresh"), logClose: $("graph-log-close"),
  };
  const canEdit = role === "admin" || role === "operator";
  let nodes = new Map();
  let modules = new Map();
  let workers = new Map();
  let moduleIssues = [];
  let runtimeReady = false;
  let graphBusy = false;
  let pollGeneration = 0;
  let frame = 0;
  let offset = { x: 20, y: 20 };
  let selection = null;
  let editBase = null;
  let drag = null;
  let formBusy = false;
  let logView = null;
  let moduleController = null;
  let formNodeId = null;
  let settingsRevision = 0;
  const moduleStyles = new Set();
  const commands = new Map();
  const cards = new Map();
  const resize = new ResizeObserver(queueEdges);
  resize.observe(ui.plane);

  /** Check whether lifecycle rules allow modifying this node. */
  function editable(config) {
    const worker = workers.get(config.id);
    return canEdit && runtimeReady && !graphBusy && !commands.has(config.id) && modules.has(config.type) &&
      !worker?.process_alive && !worker?.restart_pending && !["starting", "stopping"].includes(worker?.state);
  }

  /** Return the configured port or a placeholder for a missing module. */
  function findPort(config, portId, direction) {
    return modules.get(config?.type)?.ports.find((port) => port.id === portId && port.direction === direction);
  }

  /** Refresh worker state and permissions without replacing cards or the open form. */
  function paintControls() {
    ui.add.hidden = !canEdit;
    ui.add.disabled = !runtimeReady || graphBusy || !modules.size || Boolean(drag);
    ui.reload.disabled = graphBusy || Boolean(drag);
    ui.cancelEdge.hidden = selection === null;
    for (const [id, card] of cards) {
      const config = nodes.get(id);
      const worker = workers.get(id);
      const pending = commands.get(id);
      const state = pending === "start" ? "starting" : pending === "stop" ? "stopping" : worker?.state ?? "stopped";
      const active = worker?.process_alive || worker?.restart_pending || state === "starting" || state === "stopping";
      card.status.dataset.state = state;
      card.status.textContent = runtimeReady ? `${STATUS[state] ?? state}${worker?.restart_pending ? " · Очікує відновлення" : ""}${worker?.error?.message ? ` · ${worker.error.message}` : ""}` : "Статус недоступний";
      card.edit.textContent = editable(config) ? "Налаштувати" : "Переглянути";
      card.header.classList.toggle("can-move", editable(config));
      card.start.disabled = !editable(config) || !config.enabled || active || Boolean(drag);
      card.stop.disabled = !runtimeReady || pending === "stop" || (!active && state !== "error");
      for (const port of card.ports) {
        const source = selection && findPort(nodes.get(selection.id), selection.portId, "output");
        port.button.disabled = !canEdit || !runtimeReady || graphBusy || Boolean(drag) || (port.direction === "input" && (!editable(config) || !source || selection.id === id || source.contract_type !== port.contractType));
        if (port.direction === "output") port.button.setAttribute("aria-pressed", String(selection?.id === id && selection.portId === port.id));
      }
    }
  }

  /** Display config and module diagnostics from the latest server snapshot. */
  function paintDiagnostics(issues) {
    const entries = [...moduleIssues, ...issues];
    $("graph-diagnostics").hidden = !entries.length;
    $("graph-diagnostics-count").textContent = `(${entries.length})`;
    const items = document.createDocumentFragment();
    for (const { path, message } of entries) items.append(element("li", `${path} — ${message}`));
    $("graph-diagnostics-items").replaceChildren(items);
  }

  /** Position a card from model coordinates, optionally using a drag preview. */
  function positionCard(card, position) {
    card.style.left = `${drawingCoordinate(position.x) + offset.x}px`;
    card.style.top = `${drawingCoordinate(position.y) + offset.y}px`;
  }

  /** Coalesce layout updates from dragging and resizing into one frame. */
  function queueEdges() {
    if (!frame) frame = requestAnimationFrame(drawEdges);
  }

  /** Size the scroll surface and draw links between measured port positions. */
  function drawEdges() {
    frame = 0;
    if (!ui.plane.getClientRects().length) return;
    const bounds = ui.plane.getBoundingClientRect();
    let width = 0;
    let height = 0;
    for (const card of cards.values()) {
      const rect = card.root.getBoundingClientRect();
      width = Math.max(width, rect.right - bounds.left + 32);
      height = Math.max(height, rect.bottom - bounds.top + 32);
    }
    ui.plane.style.width = `${Math.ceil(width)}px`;
    ui.plane.style.height = `${Math.ceil(height)}px`;
    const paths = document.createDocumentFragment();
    for (const config of nodes.values()) {
      for (const [targetId, link] of Object.entries(config.connections)) {
        const output = cards.get(link.source_node_id)?.anchors.get(`output:${link.source_port_id}`);
        const input = cards.get(config.id)?.anchors.get(`input:${targetId}`);
        if (!output || !input || input.classList.contains("is-unresolved")) continue;
        const from = output.getBoundingClientRect();
        const to = input.getBoundingClientRect();
        const x1 = from.right - bounds.left;
        const x2 = to.left - bounds.left;
        const y1 = from.top + from.height / 2 - bounds.top;
        const y2 = to.top + to.height / 2 - bounds.top;
        const bend = Math.max(34, Math.min(150, Math.abs(x2 - x1) / 2));
        const path = document.createElementNS(SVG_NS, "path");
        path.setAttribute("d", `M${x1},${y1} C${x1 + bend},${y1} ${x2 - bend},${y2} ${x2},${y2}`);
        paths.append(path);
      }
    }
    ui.edges.replaceChildren(paths);
  }

  /** Create a labeled port with an unresolved indicator and a stable edge anchor. */
  function appendPort(card, config, port) {
    const link = port.direction === "input" ? config.connections[port.id] : null;
    const upstream = nodes.get(link?.source_node_id);
    const source = findPort(upstream, link?.source_port_id, "output");
    const unresolved = Boolean(link && (!source || source.contract_type !== port.contract_type));
    const arrow = port.direction === "input" ? "◀" : "▶";
    const label = `${arrow} ${port.name}${link ? (unresolved ? " · втрачено" : " · з’єднано") : ""}`;
    const item = button(label, () => choosePort(config, port), "graph-port");
    item.dataset.direction = port.direction;
    item.classList.toggle("is-unresolved", unresolved);
    item.title = link ? `${upstream?.name ?? link.source_node_id} / ${source?.name ?? link.source_port_id}` : port.name;
    card.portList.append(item);
    card.ports.push({ id: port.id, direction: port.direction, contractType: port.contract_type, button: item });
    card.anchors.set(`${port.direction}:${port.id}`, item);
  }

  /** Build cards only when configuration changes, keeping polling non-disruptive. */
  function render() {
    for (const card of cards.values()) resize.unobserve(card.root);
    cards.clear();
    ui.cards.replaceChildren();
    offset = { x: 20, y: 20 };
    for (const config of nodes.values()) {
      offset.x = Math.max(offset.x, 20 - drawingCoordinate(config.position.x));
      offset.y = Math.max(offset.y, 20 - drawingCoordinate(config.position.y));
    }
    for (const config of nodes.values()) {
      const module = modules.get(config.type);
      const root = element("article", "", "graph-card");
      root.dataset.nodeType = config.type;
      root.dataset.nodeId = config.id;
      const header = element("div", "", "graph-card-header");
      header.append(element("strong", config.name), element("span", module?.name ?? `${config.type} · Модуль недоступний`, "muted small-text"));
      const status = element("p", "", "graph-card-status small-text");
      const portList = element("div", "", "graph-card-ports");
      const actions = element("div", "", "graph-card-actions");
      const edit = button("Налаштувати", () => openForm(config));
      const start = button("Запустити", () => lifecycle(config, "start"));
      const stop = button("Зупинити", () => lifecycle(config, "stop"));
      start.hidden = stop.hidden = !canEdit;
      actions.append(edit, start, stop);
      if (canEdit) actions.append(button("Журнал", () => openNodeLog(config)));
      root.append(header, status, portList, actions);
      const card = { root, header, status, portList, edit, start, stop, ports: [], anchors: new Map() };
      cards.set(config.id, card);
      for (const port of module?.ports ?? []) appendPort(card, config, port);
      for (const id of Object.keys(config.connections)) {
        if (!card.anchors.has(`input:${id}`)) appendPort(card, config, { id, name: id, direction: "input" });
      }
      header.addEventListener("pointerdown", (event) => beginDrag(event, config, card));
      header.addEventListener("pointermove", moveDrag);
      header.addEventListener("pointerup", finishDrag);
      header.addEventListener("pointercancel", cancelDrag);
      header.addEventListener("lostpointercapture", cancelDrag);
      positionCard(root, config.position);
      ui.cards.append(root);
      resize.observe(root);
    }
    ui.empty.hidden = nodes.size > 0;
    ui.empty.textContent = modules.size ? "На графі ще немає нод." : "Немає зареєстрованих модулів нод.";
    paintControls();
    queueEdges();
  }

  /** Read saved configs and module metadata without discarding a failed refresh. */
  async function loadGraph() {
    if (graphBusy || drag) return;
    graphBusy = true;
    paintControls();
    showError(ui.error);
    try {
      const [catalog, graph] = await Promise.all([request("/api/node-modules"), request("/api/nodes")]);
      modules = new Map(catalog.modules.map((item) => [item.type, item]));
      nodes = new Map(graph.nodes.map((item) => [item.id, item]));
      moduleIssues = catalog.diagnostics;
      selection = null;
      paintDiagnostics(graph.diagnostics);
      showError(ui.notice);
    } catch (failure) {
      showError(ui.error, failureText(failure));
    } finally {
      graphBusy = false;
      render();
    }
  }

  /** Poll runtime summaries without treating a failed request as stopped workers. */
  async function pollWorkers() {
    if (!document.hidden && !$("workspace-view").hidden) {
      const generation = pollGeneration;
      try {
        const current = new Map();
        let cursor = 0;
        do {
          const page = await request(`/api/workers?offset=${cursor}`, { signal: AbortSignal.timeout(7000) });
          if (generation !== pollGeneration) return;
          for (const item of page.workers) current.set(item.node_id, item);
          if (page.next_offset !== null && page.next_offset <= cursor) throw new Error("Некоректна сторінка статусів.");
          cursor = page.next_offset;
        } while (cursor !== null);
        workers = current;
        void showModuleStatus(moduleController, editBase?.id);
        runtimeReady = true;
        ui.runtime.textContent = "Runtime підключено";
      } catch (failure) {
        if (generation !== pollGeneration) return;
        runtimeReady = false;
        ui.runtime.textContent = `Runtime недоступний · ${failure.message}`;
      } finally {
        paintControls();
        if (drag && !editable(drag.config)) cancelDrag();
      }
    }
  }

  /** Deliver the selected node's detailed status only to its still-open form. */
  async function showModuleStatus(controller, id) {
    if (!controller || !id) return;
    const summary = workers.get(id);
    if (!summary?.process_alive) {
      controller.updateStatus(null);
      return;
    }
    const detail = await request(`/api/nodes/${id}/status`, { signal: AbortSignal.timeout(7000) }).catch(() => null);
    if (controller === moduleController && ui.dialog.open && detail?.status?.instance_id === workers.get(id)?.instance_id) {
      controller.updateStatus(detail);
    }
  }

  /** Open one node's journal and discard any responses from an earlier dialog. */
  function openNodeLog(config) {
    logView = { id: config.id, loading: false };
    ui.logTitle.textContent = `Журнал · ${config.name}`;
    ui.logLines.textContent = "Завантаження…";
    showError(ui.logError);
    ui.logDialog.showModal();
    void loadNodeLog();
  }

  /** Refresh only the open journal and keep the last output if a request fails. */
  async function loadNodeLog() {
    const view = logView;
    if (!view || !ui.logDialog.open || view.loading) return;
    view.loading = true;
    ui.logRefresh.disabled = true;
    try {
      const { lines } = await request(`/api/nodes/${view.id}/logs`, { signal: AbortSignal.timeout(7000) });
      if (logView !== view || !ui.logDialog.open) return;
      const text = [...lines].reverse().join("\n");
      const next = text || "У журналі ще немає подій.";
      if (ui.logLines.textContent !== next) ui.logLines.textContent = next;
      showError(ui.logError);
    } catch (failure) {
      if (logView === view && ui.logDialog.open) showError(ui.logError, failureText(failure));
    } finally {
      view.loading = false;
      if (logView === view) ui.logRefresh.disabled = false;
    }
  }

  /** Schedule the next status poll only after the current one finishes. */
  async function pollLoop() {
    try { await Promise.all([pollWorkers(), loadNodeLog()]); }
    finally { setTimeout(pollLoop, 2500); }
  }

  /** Persist a new config or an exact revision, then redraw from the server response. */
  async function saveConfig(config, previous = null) {
    if (graphBusy) throw new Error("Зачекай завершення поточного запису.");
    graphBusy = true;
    paintControls();
    try {
      const url = previous ? `/api/nodes/${previous.id}?expected_revision=${previous.revision}` : "/api/nodes";
      const data = await request(url, { method: previous ? "PUT" : "POST", body: { ...config, revision: previous ? previous.revision + 1 : 1 } });
      nodes.set(data.node.id, data.node);
      paintDiagnostics(data.diagnostics);
      showError(ui.error);
      showError(ui.notice, "Зміни збережено.");
      selection = null;
    } finally {
      graphBusy = false;
      render();
    }
  }

  /** Submit one explicit lifecycle command and let the next poll reconcile status. */
  async function lifecycle(config, operation) {
    if (!canEdit || !runtimeReady || (commands.has(config.id) && (operation !== "stop" || commands.get(config.id) === "stop"))) return;
    const pending = operation;
    commands.set(config.id, pending);
    pollGeneration += 1;
    showError(ui.error);
    paintControls();
    try {
      const suffix = operation === "start" ? `?expected_revision=${config.revision}` : "";
      const result = await request(`/api/nodes/${config.id}/${operation}${suffix}`, { method: "POST", body: {} });
      if (commands.get(config.id) === pending) {
        workers.set(config.id, { ...result, error: result.status?.error });
        showError(ui.notice, operation === "start" ? "Команду запуску прийнято." : "Команду зупинки виконано.");
      }
    } catch (failure) {
      if (commands.get(config.id) === pending) showError(ui.error, failureText(failure));
    } finally {
      if (commands.get(config.id) === pending) {
        commands.delete(config.id);
        pollGeneration += 1;
        paintControls();
      }
    }
  }

  /** Select an output, then write the selected link only in its consumer config. */
  async function choosePort(config, port) {
    if (port.direction === "output") {
      selection = selection?.id === config.id && selection.portId === port.id ? null : { id: config.id, portId: port.id };
      showError(ui.notice, selection ? `${config.name} / ${port.name} — вибери сумісний вхід.` : "");
      paintControls();
      return;
    }
    if (!selection || !editable(config)) return;
    const connections = { ...config.connections, [port.id]: { source_node_id: selection.id, source_port_id: selection.portId } };
    try { await saveConfig({ ...config, connections }, config); }
    catch (failure) { showError(ui.error, failureText(failure)); }
  }

  /** Capture the initiating pointer and preserve the original revision for CAS. */
  function beginDrag(event, config, card) {
    if (drag || event.button !== 0 || !editable(config)) return;
    drag = { config, card, id: event.pointerId, x: event.clientX, y: event.clientY, left: ui.viewport.scrollLeft, top: ui.viewport.scrollTop, position: null };
    card.header.setPointerCapture(event.pointerId);
    card.root.classList.add("is-dragging");
    paintControls();
  }

  /** Show a local preview; skip writes until the pointer is released. */
  function moveDrag(event) {
    if (!drag || drag.id !== event.pointerId) return;
    const dx = event.clientX - drag.x + ui.viewport.scrollLeft - drag.left;
    const dy = event.clientY - drag.y + ui.viewport.scrollTop - drag.top;
    if (!drag.position && Math.abs(dx) + Math.abs(dy) < 4) return;
    const origin = drag.config.position;
    drag.position = {
      x: Math.round(Math.max(0, drawingCoordinate(origin.x) + offset.x + dx) - offset.x),
      y: Math.round(Math.max(0, drawingCoordinate(origin.y) + offset.y + dy) - offset.y),
    };
    positionCard(drag.card.root, drag.position);
    queueEdges();
  }

  /** Clear the pointer preview and commit one position update or restore it on failure. */
  async function finishDrag(event) {
    if (!drag || drag.id !== event.pointerId) return;
    moveDrag(event);
    const { config, position } = drag;
    cancelDrag(event);
    if (!position || !editable(config) || (position.x === config.position.x && position.y === config.position.y)) return;
    try { await saveConfig({ ...config, position }, config); }
    catch (failure) { showError(ui.error, failureText(failure)); }
  }

  /** Cancel a pointer interaction without persisting its preview coordinates. */
  function cancelDrag(event) {
    if (!drag || (event && drag.id !== event.pointerId)) return;
    const { card, config, id } = drag;
    drag = null;
    if (card.header.hasPointerCapture(id)) card.header.releasePointerCapture(id);
    card.root.classList.remove("is-dragging");
    positionCard(card.root, config.position);
    paintControls();
    queueEdges();
  }

  /** Render one source selector per input, preserving unresolved saved references. */
  function connectionFields(config, inputs, readOnly) {
    ui.connections.replaceChildren();
    for (const port of inputs) {
      const label = element("label", `Вхід: ${port.name}`);
      const select = element("select");
      select.dataset.portId = port.id;
      select.disabled = readOnly;
      select.add(new Option("Без з’єднання", ""));
      const link = config.connections[port.id];
      const saved = link ? JSON.stringify([link.source_node_id, link.source_port_id]) : "";
      for (const upstream of nodes.values()) {
        if (upstream.id === config.id) continue;
        for (const output of modules.get(upstream.type)?.ports ?? []) {
          if (output.direction === "output" && output.contract_type === port.contract_type) {
            select.add(new Option(`${upstream.name} / ${output.name}`, JSON.stringify([upstream.id, output.id])));
          }
        }
      }
      if (saved && !Array.from(select.options).some((item) => item.value === saved)) {
        select.add(new Option(`Джерело недоступне · ${link.source_node_id} / ${link.source_port_id}`, saved));
      }
      select.value = saved;
      label.append(select);
      ui.connections.append(label);
    }
  }

  /** Open creation, revision-bound editing or a read-only view of the config. */
  function openForm(config = null) {
    if (graphBusy || drag || (!config && (!canEdit || !runtimeReady || !modules.size))) return;
    editBase = config;
    formNodeId = config?.id ?? nodeId();
    const readOnly = Boolean(config && !editable(config));
    $("graph-dialog-title").textContent = config ? (readOnly ? "Конфігурація ноди" : "Налаштування ноди") : "Додати ноду";
    ui.type.replaceChildren(...Array.from(modules.values(), (item) => new Option(item.name, item.type)));
    if (config && !modules.has(config.type)) ui.type.add(new Option(config.type, config.type));
    if (config) ui.type.value = config.type;
    ui.type.disabled = Boolean(config);
    ui.name.value = config?.name ?? modules.get(ui.type.value)?.name ?? "";
    ui.enabled.checked = config?.enabled ?? true;
    ui.x.value = config?.position.x ?? Math.max(0, ui.viewport.scrollLeft - offset.x + 40) + (nodes.size % 5) * 28;
    ui.y.value = config?.position.y ?? Math.max(0, ui.viewport.scrollTop - offset.y + 40) + (nodes.size % 7) * 28;
    ui.settings.value = JSON.stringify(config?.settings ?? {}, null, 2);
    const inputs = (modules.get(ui.type.value)?.ports ?? []).filter((port) => port.direction === "input");
    if (config) {
      for (const id of Object.keys(config.connections)) {
        if (!inputs.some((port) => port.id === id)) inputs.push({ id, name: id });
      }
    }
    connectionFields(config ?? { connections: {} }, inputs, readOnly);
    for (const field of [ui.name, ui.enabled, ui.x, ui.y, ui.settings]) field.disabled = readOnly;
    ui.save.hidden = readOnly;
    showError(ui.formError);
    ui.dialog.showModal();
    void showModuleSettings(config, readOnly);
  }

  /** Load a release module's form for this open dialog, keeping JSON as a fallback. */
  async function showModuleSettings(config, readOnly) {
    const revision = ++settingsRevision;
    const type = ui.type.value;
    const module = modules.get(type);
    const initialJSON = ui.settings.value;
    moduleController?.unmount();
    moduleController = null;
    ui.moduleSettings.replaceChildren();
    ui.jsonSettings.hidden = false;
    if (!module) return;
    const base = `/assets/node-modules/${encodeURIComponent(type)}`;
    try {
      const { mount } = await import(`${base}/${module.ui.path}`);
      if (typeof mount !== "function" || revision !== settingsRevision || !ui.dialog.open || ui.settings.value !== initialJSON) return;
      const container = document.createElement("div");
      container.dataset.nodeType = type;
      const controller = mount(container, { nodeId: formNodeId, readOnly, request });
      controller.updateConfig(config);
      if (!moduleStyles.has(type)) {
        const stylesheet = document.createElement("link");
        stylesheet.rel = "stylesheet";
        stylesheet.href = `${base}/${module.ui.stylesheet}`;
        document.head.append(stylesheet);
        moduleStyles.add(type);
      }
      ui.moduleSettings.replaceChildren(container);
      ui.jsonSettings.hidden = true;
      moduleController = controller;
      void showModuleStatus(controller, config?.id);
      let capabilities = null;
      if (module.requirements.capabilities.some(({ provider }) => provider === "decklink")) {
        capabilities = await request("/api/hardware/decklink-capabilities", { signal: AbortSignal.timeout(7000) }).catch(() => null);
      }
      if (revision === settingsRevision && ui.dialog.open) controller.updateCapabilities(capabilities);
    } catch {
      // Independent modules may fail to load or have only the generic JSON editor.
    }
  }

  /** Collect the form, preserve secret references, and let server schemas validate settings. */
  async function submitConfig(event) {
    event.preventDefault();
    if (formBusy || ui.save.hidden) return;
    formBusy = true;
    ui.save.disabled = ui.close.disabled = true;
    showError(ui.formError);
    try {
      const manifest = modules.get(ui.type.value);
      const draft = editBase ?? { schema_version: 1, id: formNodeId, type: manifest.type, settings_version: manifest.settings_version, secret_refs: {} };
      let settings;
      if (moduleController) settings = moduleController.readSettings();
      else {
        try { settings = JSON.parse(ui.settings.value); }
        catch { throw new Error("Параметри мають бути коректним JSON."); }
      }
      if (!settings || Array.isArray(settings) || typeof settings !== "object") throw new Error("Параметри мають бути JSON-об’єктом.");
      const connections = Object.create(null);
      for (const source of ui.connections.querySelectorAll("select")) {
        if (!source.value) continue;
        const [source_node_id, source_port_id] = JSON.parse(source.value);
        connections[source.dataset.portId] = { source_node_id, source_port_id };
      }
      await saveConfig({ ...draft, name: ui.name.value.trim(), enabled: ui.enabled.checked, position: { x: ui.x.valueAsNumber, y: ui.y.valueAsNumber }, settings, connections }, editBase);
      ui.dialog.close();
    } catch (failure) {
      showError(ui.formError, failureText(failure));
    } finally {
      formBusy = false;
      ui.save.disabled = ui.close.disabled = false;
    }
  }

  ui.add.addEventListener("click", () => openForm());
  ui.reload.addEventListener("click", loadGraph);
  ui.cancelEdge.addEventListener("click", () => { selection = null; showError(ui.notice); paintControls(); });
  ui.close.addEventListener("click", () => ui.dialog.close());
  ui.logClose.addEventListener("click", () => ui.logDialog.close());
  ui.logRefresh.addEventListener("click", loadNodeLog);
  ui.logDialog.addEventListener("close", () => { logView = null; });
  ui.dialog.addEventListener("cancel", (event) => { if (formBusy) event.preventDefault(); });
  ui.dialog.addEventListener("close", () => {
    settingsRevision += 1;
    moduleController?.unmount();
    moduleController = null;
  });
  ui.form.addEventListener("submit", submitConfig);
  ui.type.addEventListener("change", () => {
    const manifest = modules.get(ui.type.value);
    ui.name.value = manifest.name;
    ui.settings.value = "{}";
    connectionFields({ connections: {} }, manifest.ports.filter((port) => port.direction === "input"), false);
    void showModuleSettings(null, false);
  });
  loadGraph();
  pollLoop();
}
