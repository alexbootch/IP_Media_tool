import { showError } from "/assets/api.js";

const STATUS = {
  stopped: "Зупинено", starting: "Запуск…", stopping: "Зупинка…", running: "Працює",
  waiting_source: "Очікує джерело", waiting_signal: "Очікує сигнал", error: "Помилка воркера",
};
const SVG_NS = "http://www.w3.org/2000/svg";
const LIMIT = 100000;
const MIN_ZOOM = 0.2;
const MAX_ZOOM = 2.5;

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

/** Label a compact node action for both tooltips and assistive technology. */
function iconButton(symbol, label, action) {
  const item = button(symbol, action, "graph-card-action");
  item.title = label;
  item.setAttribute("aria-label", label);
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
export function initializeGraph({ request, role, notify }) {
  const $ = (id) => document.getElementById(id);
  const ui = {
    viewport: $("graph-viewport"), stage: $("graph-stage"), plane: $("graph-plane"), cards: $("graph-nodes"), edges: $("graph-edges"),
    bank: $("node-bank"), inputBank: $("node-bank-input"), outputBank: $("node-bank-output"), reload: $("graph-reload"),
    runtime: $("graph-runtime"), dialog: $("graph-dialog"), remove: $("graph-node-delete"),
    zoomIn: $("graph-zoom-in"), zoomOut: $("graph-zoom-out"), zoomReset: $("graph-zoom-reset"), zoomFit: $("graph-zoom-fit"),
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
  let bankDrag = null;
  let wire = null;
  let pan = null;
  let zoom = 1;
  let formBusy = false;
  let logView = null;
  let moduleController = null;
  let formNodeId = null;
  let settingsRevision = 0;
  const moduleStyles = new Set();
  const commands = new Map();
  const cards = new Map();
  const bankItems = [];
  let cardCapabilities;
  const resize = new ResizeObserver(queueEdges);
  resize.observe(ui.viewport);
  if (matchMedia("(max-width: 760px)").matches) ui.bank.open = false;

  /** Allow creation only while the runtime can persist the new config. */
  function canCreate() {
    return canEdit && runtimeReady && !graphBusy && !drag && !wire && !ui.dialog.open;
  }

  /** Surface diagnostics outside the workspace through the shared app toast. */
  function report(failure) {
    notify(failureText(failure), true);
  }

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
    for (const item of bankItems) item.disabled = !canCreate() && !bankDrag;
    ui.reload.disabled = graphBusy || Boolean(drag || bankDrag || wire);
    ui.zoomIn.disabled = zoom >= MAX_ZOOM || Boolean(drag || wire || bankDrag);
    ui.zoomOut.disabled = zoom <= MIN_ZOOM || Boolean(drag || wire || bankDrag);
    ui.zoomReset.disabled = Boolean(drag || wire || bankDrag);
    ui.zoomFit.disabled = !cards.size || Boolean(drag || wire || bankDrag);
    for (const [id, card] of cards) {
      const config = nodes.get(id);
      const worker = workers.get(id);
      const pending = commands.get(id);
      const state = pending === "start" ? "starting" : pending === "stop" ? "stopping" : worker?.state ?? "stopped";
      const active = worker?.process_alive || worker?.restart_pending || state === "starting" || state === "stopping";
      card.root.dataset.state = runtimeReady ? state : "unknown";
      card.status.dataset.state = state;
      card.status.textContent = runtimeReady ? `${STATUS[state] ?? state}${worker?.restart_pending ? " · Очікує відновлення" : ""}` : "Статус недоступний";
      card.status.title = worker?.error?.message ?? card.status.textContent;
      card.dot.title = card.status.textContent;
      const media = mediaReadout(worker?.video_caps ?? worker?.status?.metrics?.video_caps,
        worker?.audio_caps ?? worker?.status?.metrics?.audio_caps);
      card.media.textContent = runtimeReady && state === "running" ? media : "";
      card.media.hidden = !card.media.textContent;
      card.edit.title = editable(config) ? "Налаштувати" : "Переглянути";
      card.edit.setAttribute("aria-label", card.edit.title);
      card.header.classList.toggle("can-move", editable(config));
      card.start.disabled = !editable(config) || !config.enabled || active || Boolean(drag);
      card.stop.disabled = !runtimeReady || pending === "stop" || (!active && state !== "error");
      card.controls?.updateEditable(editable(config));
      for (const port of card.ports) {
        const source = selection && findPort(nodes.get(selection.id), selection.portId, "output");
        const possible = Boolean(port.contractType && editable(config) && source && selection.id !== id && source.contract_type === port.contractType);
        port.button.disabled = !canEdit || !runtimeReady || graphBusy || Boolean(drag) || !port.contractType || (port.direction === "input" && !possible);
        port.button.classList.toggle("is-target", port.direction === "input" && possible);
        if (port.disconnect) port.disconnect.disabled = !editable(config) || Boolean(drag || bankDrag || wire);
        if (port.direction === "output") port.button.setAttribute("aria-pressed", String(selection?.id === id && selection.portId === port.id));
      }
    }
  }

  /** Display the negotiated video cadence and embedded audio count without dumping raw caps. */
  function mediaReadout(video, audio) {
    const width = /\bwidth=\(int\)(\d+)/.exec(video ?? "")?.[1];
    const height = /\bheight=\(int\)(\d+)/.exec(video ?? "")?.[1];
    const fps = /\bframerate=\(fraction\)(\d+)\/(\d+)/.exec(video ?? "");
    const scanMode = /\binterlace-mode=\(string\)([a-z-]+)/.exec(video ?? "")?.[1];
    const scan = { interleaved: "i", progressive: "p" }[scanMode] ?? "";
    const channels = /\bchannels=\(int\)(\d+)/.exec(audio ?? "")?.[1];
    const cadence = fps && (fps[2] === "1" ? fps[1] : `${fps[1]}/${fps[2]}`);
    return [width && height && `${width}×${height}${scan}${cadence ? ` · ${cadence} кадр/с` : ""}`,
      channels && `PCM · ${channels} кан.`].filter(Boolean).join(" / ");
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

  /** Measure cards in model coordinates, independent of zoom and scrolling. */
  function contentSize() {
    let width = 0, height = 0;
    for (const { root } of cards.values()) {
      width = Math.max(width, root.offsetLeft + root.offsetWidth + 40);
      height = Math.max(height, root.offsetTop + root.offsetHeight + 40);
    }
    return { width, height };
  }

  /** Convert screen coordinates into the unscaled drawing plane. */
  function planePoint(x, y, bounds = ui.plane.getBoundingClientRect()) {
    return { x: (x - bounds.left) / zoom, y: (y - bounds.top) / zoom };
  }

  /** Hit-test the visible workspace without mistaking floating controls for the plane. */
  function overPlane(x, y) {
    return ui.viewport.contains(document.elementFromPoint(x, y));
  }

  /** Locate the center of a circular contract handle. */
  function portPoint(port, bounds) {
    const rect = port.getBoundingClientRect();
    return planePoint(rect.left + rect.width / 2, rect.top + rect.height / 2, bounds);
  }

  /** Draw one cubic curve for an entire media contract. */
  function edgePath(from, to, preview = false) {
    const bend = Math.max(34, Math.min(150, Math.abs(to.x - from.x) / 2));
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", `M${from.x},${from.y} C${from.x + bend},${from.y} ${to.x - bend},${to.y} ${to.x},${to.y}`);
    if (preview) path.classList.add("graph-edge-preview");
    return path;
  }

  /** Size the scroll surface and draw links between measured port positions. */
  function drawEdges() {
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
    if (!ui.viewport.clientWidth) return;
    const size = contentSize();
    const width = Math.max(size.width, ui.viewport.clientWidth / zoom) + 180 / zoom;
    const height = Math.max(size.height, ui.viewport.clientHeight / zoom) + 180 / zoom;
    ui.plane.style.width = `${Math.ceil(width)}px`;
    ui.plane.style.height = `${Math.ceil(height)}px`;
    ui.plane.style.transform = `scale(${zoom})`;
    ui.stage.style.width = `${Math.ceil(width * zoom)}px`;
    ui.stage.style.height = `${Math.ceil(height * zoom)}px`;
    const bounds = ui.plane.getBoundingClientRect();
    const paths = document.createDocumentFragment();
    for (const config of nodes.values()) {
      for (const [targetId, link] of Object.entries(config.connections)) {
        const output = cards.get(link.source_node_id)?.anchors.get(`output:${link.source_port_id}`);
        const input = cards.get(config.id)?.anchors.get(`input:${targetId}`);
        if (!output || !input || input.classList.contains("is-unresolved")) continue;
        paths.append(edgePath(portPoint(output, bounds), portPoint(input, bounds)));
      }
    }
    if (wire?.moved) paths.append(edgePath(portPoint(wire.button, bounds), planePoint(wire.x, wire.y, bounds), true));
    ui.edges.replaceChildren(paths);
  }

  /** Change zoom around a screen point, keeping its model coordinates stationary. */
  function setZoom(value, x = ui.viewport.clientWidth / 2, y = ui.viewport.clientHeight / 2) {
    if (drag || bankDrag || wire || pan) return;
    const next = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, value));
    const point = { x: (ui.viewport.scrollLeft + x) / zoom, y: (ui.viewport.scrollTop + y) / zoom };
    zoom = next;
    drawEdges();
    ui.viewport.scrollLeft = point.x * zoom - x;
    ui.viewport.scrollTop = point.y * zoom - y;
    ui.zoomReset.textContent = `${Math.round(zoom * 100)}%`;
    paintControls();
  }

  /** Bring the whole configured graph into the visible workspace. */
  function fitGraph() {
    const { width, height } = contentSize();
    if (!width || !height) return;
    setZoom(Math.min(1, ui.viewport.clientWidth / width, ui.viewport.clientHeight / height), 0, 0);
    ui.viewport.scrollTo(0, 0);
  }

  /** Create a labeled port with an unresolved indicator and a stable edge anchor. */
  function appendPort(card, config, port) {
    const link = port.direction === "input" ? config.connections[port.id] : null;
    const upstream = nodes.get(link?.source_node_id);
    const source = findPort(upstream, link?.source_port_id, "output");
    const unresolved = Boolean(link && (!source || source.contract_type !== port.contract_type));
    const label = `${port.name}${link ? (unresolved ? " · джерело недоступне" : " · з’єднано") : ""}`;
    let suppressClick = false;
    const item = button("", (event) => {
      if (!suppressClick || event.detail === 0) void choosePort(config, port);
      suppressClick = false;
    }, "graph-port");
    const row = element("div", label, "graph-port-row");
    row.dataset.direction = port.direction;
    item.dataset.direction = port.direction;
    item.dataset.nodeId = config.id;
    item.dataset.portId = port.id;
    item.setAttribute("aria-label", label);
    item.classList.toggle("is-unresolved", unresolved);
    item.classList.toggle("is-connected", Boolean(link && !unresolved));
    item.title = link ? `${upstream?.name ?? link.source_node_id} / ${source?.name ?? link.source_port_id}` : port.name;
    row.append(item);
    const disconnect = link && canEdit ? button("×", () => disconnectPort(config, port.id), "graph-port-disconnect") : null;
    if (disconnect) {
      disconnect.title = "Роз’єднати";
      disconnect.setAttribute("aria-label", `Роз’єднати ${port.name}`);
      row.append(disconnect);
    }
    card.portList.append(row);
    card.ports.push({ id: port.id, direction: port.direction, contractType: port.contract_type, button: item, disconnect });
    card.anchors.set(`${port.direction}:${port.id}`, item);
    if (port.direction === "output") {
      item.addEventListener("pointerdown", (event) => {
        suppressClick = false;
        beginWire(event, config, port, item);
      });
      item.addEventListener("pointermove", moveWire);
      item.addEventListener("pointerup", (event) => { suppressClick = finishWire(event); });
      item.addEventListener("pointercancel", cancelWire);
      item.addEventListener("lostpointercapture", cancelWire);
    }
  }

  /** Build cards only when configuration changes, keeping polling non-disruptive. */
  function render() {
    const previousOffset = offset;
    const previousScroll = { x: ui.viewport.scrollLeft, y: ui.viewport.scrollTop };
    for (const card of cards.values()) {
      resize.unobserve(card.root);
      card.controls?.unmount();
    }
    cards.clear();
    cardCapabilities = null;
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
      root.dataset.direction = module?.kind ?? "";
      const header = element("div", "", "graph-card-header");
      const dot = element("span", "", "graph-card-dot");
      dot.setAttribute("aria-hidden", "true");
      const name = element("strong", config.name);
      name.title = config.name;
      const status = element("p", "", "graph-card-status small-text");
      const body = element("div", "", "graph-card-body");
      const media = element("p", "", "graph-card-media muted");
      media.hidden = true;
      body.append(element("span", module?.name ?? `${config.type} · Модуль недоступний`, "muted graph-card-type"), status, media);
      const controlsHost = element("div", "", "graph-card-module");
      const portList = element("div", "", "graph-card-ports");
      const actions = element("div", "", "graph-card-actions");
      const edit = iconButton("⚙", "Налаштувати", () => openForm(config));
      const start = iconButton("▶", "Запустити", () => lifecycle(config, "start"));
      const stop = iconButton("■", "Зупинити", () => lifecycle(config, "stop"));
      start.hidden = stop.hidden = !canEdit;
      actions.append(start, stop, edit);
      if (canEdit) actions.append(iconButton("≡", "Журнал", () => openNodeLog(config)));
      header.append(dot, name, actions);
      root.append(header, controlsHost, body, portList);
      const card = { root, header, dot, status, media, portList, edit, start, stop, ports: [], anchors: new Map() };
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
      void mountCardControls(module, config, card, controlsHost);
    }
    paintControls();
    drawEdges();
    ui.viewport.scrollTo(previousScroll.x + (offset.x - previousOffset.x) * zoom,
      previousScroll.y + (offset.y - previousOffset.y) * zoom);
  }

  /** Resolve only module assets listed in its validated manifest. */
  function moduleAsset(module, entry) {
    return `/assets/node-modules/${encodeURIComponent(module.type)}/${module.ui[entry]}`;
  }

  /** Load a module's scoped stylesheet on its first card or settings dialog. */
  function ensureModuleStyle(module) {
    if (moduleStyles.has(module.type)) return;
    const stylesheet = document.createElement("link");
    stylesheet.rel = "stylesheet";
    stylesheet.href = moduleAsset(module, "stylesheet");
    document.head.append(stylesheet);
    moduleStyles.add(module.type);
  }

  /** Attach optional inline controls without exposing the graph or worker process to plugins. */
  async function mountCardControls(module, config, card, host) {
    if (!module) return;
    try {
      const { mountCard } = await import(moduleAsset(module, "path"));
      if (!mountCard || cards.get(config.id) !== card) return;
      ensureModuleStyle(module);
      const controller = mountCard(host, { config, saveSettings: async (patch) => {
        if (nodes.get(config.id) !== config || !editable(config) || ui.dialog.open || wire || bankDrag || drag) return false;
        try {
          await saveConfig({ ...config, settings: { ...config.settings, ...patch } }, config);
          return true;
        } catch (failure) {
          report(failure);
          return false;
        }
      } });
      card.controls = controller;
      controller.updateEditable(editable(config));
      if (module.requirements.capabilities.some(({ provider }) => provider === "decklink")) {
        cardCapabilities ??= request("/api/hardware/decklink-capabilities", { signal: AbortSignal.timeout(7000) }).catch(() => null);
        const capabilities = await cardCapabilities;
        if (cards.get(config.id) === card) controller.updateCapabilities(capabilities);
      }
    } catch {
      // The settings dialog and native node actions remain available independently of inline controls.
    }
  }

  /** Read saved configs and module metadata without discarding a failed refresh. */
  async function loadGraph() {
    if (graphBusy || drag || bankDrag || wire || ui.dialog.open) return;
    graphBusy = true;
    paintControls();
    try {
      const [catalog, graph] = await Promise.all([request("/api/node-modules"), request("/api/nodes")]);
      modules = new Map(catalog.modules.map((item) => [item.type, item]));
      nodes = new Map(graph.nodes.map((item) => [item.id, item]));
      moduleIssues = catalog.diagnostics;
      selection = null;
      paintDiagnostics(graph.diagnostics);
      renderBank();
    } catch (failure) {
      report(failure);
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
        ui.runtime.hidden = true;
      } catch (failure) {
        if (generation !== pollGeneration) return;
        runtimeReady = false;
        ui.runtime.hidden = false;
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
    paintControls();
    try {
      const suffix = operation === "start" ? `?expected_revision=${config.revision}` : "";
      const result = await request(`/api/nodes/${config.id}/${operation}${suffix}`, { method: "POST", body: {} });
      if (commands.get(config.id) === pending) {
        workers.set(config.id, { ...result, error: result.status?.error });
      }
    } catch (failure) {
      if (commands.get(config.id) === pending) report(failure);
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
      paintControls();
      return;
    }
    const source = selection && findPort(nodes.get(selection.id), selection.portId, "output");
    if (!source || selection.id === config.id || source.contract_type !== port.contract_type || !editable(config)) return;
    const connections = { ...config.connections, [port.id]: { source_node_id: selection.id, source_port_id: selection.portId } };
    try { await saveConfig({ ...config, connections }, config); }
    catch (failure) { report(failure); }
  }

  /** Remove the whole media contract from the consumer's configuration. */
  async function disconnectPort(config, portId) {
    if (!editable(config) || !config.connections[portId]) return;
    const connections = { ...config.connections };
    delete connections[portId];
    try { await saveConfig({ ...config, connections }, config); }
    catch (failure) { report(failure); }
  }

  /** Create two banks strictly from the installed module catalog. */
  function renderBank() {
    bankItems.length = 0;
    ui.inputBank.replaceChildren();
    ui.outputBank.replaceChildren();
    for (const module of modules.values()) {
      const group = module.kind === "input" ? ui.inputBank : module.kind === "output" ? ui.outputBank : null;
      if (!group) continue;
      const item = button(module.name, (event) => {
        if (event.detail !== 0) return;
        const bounds = ui.viewport.getBoundingClientRect();
        addAtPoint(module.type, bounds.left + bounds.width / 3, bounds.top + bounds.height / 3);
      }, "node-bank-item");
      item.title = "Перетягни на робоче поле";
      item.addEventListener("pointerdown", (event) => beginBankDrag(event, module, item));
      item.addEventListener("pointermove", moveBankDrag);
      item.addEventListener("pointerup", finishBankDrag);
      item.addEventListener("pointercancel", cancelBankDrag);
      item.addEventListener("lostpointercapture", cancelBankDrag);
      group.append(item);
      bankItems.push(item);
    }
  }

  /** Capture a bank drag consistently for mouse, pen and touch. */
  function beginBankDrag(event, module, item) {
    if (!canCreate() || bankDrag || !event.isPrimary || event.button !== 0) return;
    bankDrag = { id: event.pointerId, x: event.clientX, y: event.clientY, module, item, ghost: null };
    item.setPointerCapture(event.pointerId);
    paintControls();
  }

  /** Preview the bank item and highlight only valid drop space. */
  function moveBankDrag(event) {
    if (event.pointerId !== bankDrag?.id) return;
    if (!bankDrag.ghost) {
      if (Math.hypot(event.clientX - bankDrag.x, event.clientY - bankDrag.y) < 4) return;
      bankDrag.ghost = element("div", bankDrag.module.name, "node-bank-ghost");
      bankDrag.ghost.setAttribute("aria-hidden", "true");
      document.body.append(bankDrag.ghost);
    }
    bankDrag.ghost.style.left = `${event.clientX}px`;
    bankDrag.ghost.style.top = `${event.clientY}px`;
    ui.viewport.classList.toggle("is-drop-target", overPlane(event.clientX, event.clientY));
  }

  /** Open the node settings for a valid drop position. */
  function finishBankDrag(event) {
    if (event.pointerId !== bankDrag?.id) return;
    moveBankDrag(event);
    const type = bankDrag.module.type;
    const dropped = bankDrag.ghost && overPlane(event.clientX, event.clientY);
    cancelBankDrag(event);
    if (dropped) addAtPoint(type, event.clientX, event.clientY);
  }

  /** Clear drag indicators without creating a node. */
  function cancelBankDrag(event) {
    if (!bankDrag || (event && event.pointerId !== bankDrag.id)) return;
    const { item, id, ghost } = bankDrag;
    bankDrag = null;
    if (item.hasPointerCapture(id)) item.releasePointerCapture(id);
    ghost?.remove();
    ui.viewport.classList.remove("is-drop-target");
    paintControls();
  }

  /** Convert a drop into persisted coordinates without saving an incomplete configuration. */
  function addAtPoint(type, x, y) {
    if (!canCreate()) return;
    const point = planePoint(x, y);
    const position = { x: Math.round(drawingCoordinate(point.x - offset.x)), y: Math.round(drawingCoordinate(point.y - offset.y)) };
    openForm(null, { type, position });
  }

  /** Capture an output handle until the pointer chooses a consumer port. */
  function beginWire(event, config, port, item) {
    if (wire || drag || bankDrag || pan || !event.isPrimary || event.button !== 0 || item.disabled) return;
    wire = { id: event.pointerId, source: { id: config.id, portId: port.id }, button: item,
      startX: event.clientX, startY: event.clientY, x: event.clientX, y: event.clientY, moved: false };
    item.setPointerCapture(event.pointerId);
  }

  /** Activate compatible input handles and track the preview curve. */
  function moveWire(event) {
    if (event.pointerId !== wire?.id) return;
    if (!wire.moved) {
      if (Math.hypot(event.clientX - wire.startX, event.clientY - wire.startY) < 4) return;
      wire.moved = true;
      selection = wire.source;
      paintControls();
    }
    wire.x = event.clientX;
    wire.y = event.clientY;
    queueEdges();
  }

  /** Commit a drag only when the pointer ends on a compatible input handle. */
  function finishWire(event) {
    if (event.pointerId !== wire?.id) return false;
    moveWire(event);
    const moved = wire.moved;
    const target = moved && document.elementFromPoint(event.clientX, event.clientY)?.closest(".graph-port.is-target");
    const compatible = target && !target.disabled;
    const source = wire.source;
    cancelWire(event);
    if (compatible) {
      const config = nodes.get(target.dataset.nodeId);
      const port = findPort(config, target.dataset.portId, "input");
      if (port) {
        selection = source;
        void choosePort(config, port);
      }
    }
    return moved;
  }

  /** Release the handle and remove a discarded preview. */
  function cancelWire(event) {
    if (!wire || (event && event.pointerId !== wire.id)) return;
    const { id, button: item, moved } = wire;
    wire = null;
    if (item.hasPointerCapture(id)) item.releasePointerCapture(id);
    if (moved) selection = null;
    paintControls();
    queueEdges();
  }

  /** Capture the initiating pointer and preserve the original revision for CAS. */
  function beginDrag(event, config, card) {
    if (drag || bankDrag || wire || pan || event.button !== 0 || event.target.closest("button") || !editable(config)) return;
    drag = { config, card, id: event.pointerId, x: event.clientX, y: event.clientY, left: ui.viewport.scrollLeft, top: ui.viewport.scrollTop, position: null };
    card.header.setPointerCapture(event.pointerId);
    card.root.classList.add("is-dragging");
    paintControls();
  }

  /** Show a local preview; skip writes until the pointer is released. */
  function moveDrag(event) {
    if (!drag || drag.id !== event.pointerId) return;
    const dx = (event.clientX - drag.x + ui.viewport.scrollLeft - drag.left) / zoom;
    const dy = (event.clientY - drag.y + ui.viewport.scrollTop - drag.top) / zoom;
    if (!drag.position && (Math.abs(dx) + Math.abs(dy)) * zoom < 4) return;
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
    catch (failure) { report(failure); }
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

  /** Start panning only when the pointer is on an empty part of the canvas. */
  function beginPan(event) {
    if (drag || bankDrag || wire || pan || !event.isPrimary || event.button !== 0 || event.target.closest(".graph-card")) return;
    selection = null;
    paintControls();
    pan = { id: event.pointerId, x: event.clientX + ui.viewport.scrollLeft, y: event.clientY + ui.viewport.scrollTop };
    ui.viewport.setPointerCapture(event.pointerId);
    ui.viewport.classList.add("is-panning");
  }

  /** Pan by translating screen movement into scroll offsets. */
  function movePan(event) {
    if (event.pointerId !== pan?.id) return;
    ui.viewport.scrollTo(pan.x - event.clientX, pan.y - event.clientY);
  }

  /** Release the canvas pointer without altering any node positions. */
  function endPan(event) {
    if (!pan || (event && event.pointerId !== pan.id)) return;
    const { id } = pan;
    pan = null;
    if (ui.viewport.hasPointerCapture(id)) ui.viewport.releasePointerCapture(id);
    ui.viewport.classList.remove("is-panning");
  }

  /** Zoom with the wheel while keeping the graph point beneath it fixed. */
  function wheelZoom(event) {
    if (event.target.closest("select, input") || !event.deltaY) return;
    event.preventDefault();
    const bounds = ui.viewport.getBoundingClientRect();
    const delta = Math.min(120, Math.abs(event.deltaY) * (event.deltaMode === 1 ? 16 : 1));
    setZoom(zoom * Math.exp(-Math.sign(event.deltaY) * delta / 300), event.clientX - bounds.left, event.clientY - bounds.top);
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
  function openForm(config = null, placement = null) {
    if (graphBusy || drag || wire || bankDrag || ui.dialog.open || (!config && (!canCreate() || !modules.has(placement?.type)))) return;
    editBase = config;
    formNodeId = config?.id ?? nodeId();
    const readOnly = Boolean(config && !editable(config));
    $("graph-dialog-title").textContent = config ? (readOnly ? "Конфігурація ноди" : "Налаштування ноди") : "Додати ноду";
    const type = config?.type ?? placement.type;
    ui.type.replaceChildren(new Option(modules.get(type)?.name ?? type, type));
    ui.type.disabled = true;
    ui.name.value = config?.name ?? modules.get(ui.type.value)?.name ?? "";
    ui.enabled.checked = config?.enabled ?? true;
    ui.x.value = (config?.position ?? placement.position).x;
    ui.y.value = (config?.position ?? placement.position).y;
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
    ui.remove.hidden = !config || readOnly;
    showError(ui.formError);
    ui.dialog.showModal();
    paintControls();
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
    try {
      const { mount } = await import(moduleAsset(module, "path"));
      if (typeof mount !== "function" || revision !== settingsRevision || !ui.dialog.open || ui.settings.value !== initialJSON) return;
      const container = document.createElement("div");
      container.dataset.nodeType = type;
      const controller = mount(container, { nodeId: formNodeId, readOnly, request });
      controller.updateConfig(config);
      ensureModuleStyle(module);
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
    ui.save.disabled = ui.close.disabled = ui.remove.disabled = true;
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
      ui.save.disabled = ui.close.disabled = ui.remove.disabled = false;
    }
  }

  /** Delete the selected revision using the runtime's existing backup and graph checks. */
  async function deleteNode() {
    const config = editBase;
    if (!config || formBusy || !editable(config) || !window.confirm(`Видалити ноду «${config.name}»?`)) return;
    formBusy = graphBusy = true;
    ui.save.disabled = ui.close.disabled = ui.remove.disabled = true;
    paintControls();
    let removed = false;
    try {
      await request(`/api/nodes/${config.id}?expected_revision=${config.revision}`, { method: "DELETE", body: {} });
      nodes.delete(config.id);
      workers.delete(config.id);
      selection = null;
      removed = true;
      ui.dialog.close();
      render();
    } catch (failure) {
      showError(ui.formError, failureText(failure));
    } finally {
      formBusy = graphBusy = false;
      ui.save.disabled = ui.close.disabled = ui.remove.disabled = false;
      paintControls();
    }
    if (removed) await loadGraph();
  }

  /** Cancel a pending route gesture without modifying a consumer config. */
  function escapeGraph(event) {
    if (event.key !== "Escape" || ui.dialog.open || ui.logDialog.open) return;
    cancelBankDrag();
    cancelWire();
    cancelDrag();
    endPan();
    selection = null;
    paintControls();
  }

  ui.reload.addEventListener("click", loadGraph);
  ui.zoomIn.addEventListener("click", () => setZoom(zoom * 1.25));
  ui.zoomOut.addEventListener("click", () => setZoom(zoom / 1.25));
  ui.zoomReset.addEventListener("click", () => setZoom(1));
  ui.zoomFit.addEventListener("click", fitGraph);
  ui.viewport.addEventListener("wheel", wheelZoom, { passive: false });
  ui.viewport.addEventListener("pointerdown", beginPan);
  ui.viewport.addEventListener("pointermove", movePan);
  ui.viewport.addEventListener("pointerup", endPan);
  ui.viewport.addEventListener("pointercancel", endPan);
  ui.viewport.addEventListener("lostpointercapture", endPan);
  document.addEventListener("keydown", escapeGraph);
  ui.close.addEventListener("click", () => ui.dialog.close());
  ui.remove.addEventListener("click", deleteNode);
  ui.logClose.addEventListener("click", () => ui.logDialog.close());
  ui.logRefresh.addEventListener("click", loadNodeLog);
  ui.logDialog.addEventListener("close", () => { logView = null; });
  ui.dialog.addEventListener("cancel", (event) => { if (formBusy) event.preventDefault(); });
  ui.dialog.addEventListener("close", () => {
    settingsRevision += 1;
    moduleController?.unmount();
    moduleController = null;
    paintControls();
  });
  ui.form.addEventListener("submit", submitConfig);
  loadGraph();
  pollLoop();
}
