const CODECS = { h264: "H.264", hevc: "H.265 / HEVC", av1: "AV1" };
const STATES = {
  pending: "Очікування перевірки", querying: "Перевірка", complete: "Можливості отримано",
  partial: "Часткові дані", failed: "Помилка перевірки", unavailable: "NVENC недоступний",
  not_implemented: "Цей backend ще не перевіряється",
};
function text(tag, content, className = "") {
  const element = document.createElement(tag);
  element.textContent = content;
  element.className = className;
  return element;
}
function parameter(list, label, value) {
  list.append(text("dt", label), text("dd", value ?? "Невідомо"));
}
function feature(name, value) {
  const caption = value === true ? "Оголошено плагіном" : value === false ? "Не оголошено плагіном" : "Невідомо";
  const element = text("span", `${value === true ? "✓" : value === false ? "−" : "?"} ${name}`, `gpu-feature ${value === true ? "available" : value === false ? "unavailable" : "unknown"}`);
  element.title = `${name}: ${caption}`;
  element.setAttribute("aria-label", `${name}: ${caption}`);
  return element;
}
function codecCard(codec) {
  const card = text("div", "", "gpu-codec-card");
  const heading = text("div", "", "gpu-codec-heading");
  heading.append(text("strong", CODECS[codec.codec] ?? codec.codec), text("code", codec.element));
  const features = text("div", "", "gpu-features");
  features.append(feature("10-bit", codec.bit_depth_10), feature("4:2:2", codec.chroma_422), feature("4:4:4", codec.chroma_444), feature("Interlace", codec.field_encoding), feature("Temporal AQ", codec.temporal_aq));
  card.append(heading, features);
  const limits = text("dl", "", "gpu-parameters");
  parameter(limits, "Межі caps", codec.max_width && codec.max_height ? `${codec.max_width} × ${codec.max_height}` : null);
  parameter(limits, "Профілі", codec.profiles?.join(", ") || null);
  parameter(limits, "Формати входу", codec.input_formats?.join(", ") || null);
  parameter(limits, "B-frames, до", codec.max_b_frames);
  parameter(limits, "Lookahead, кадрів", codec.lookahead_frames);
  parameter(limits, "Rate control", codec.rate_control_modes?.join(", ") || null);
  card.append(limits);
  const raw = document.createElement("details");
  raw.className = "gpu-raw";
  raw.append(text("summary", "GStreamer caps"), text("pre", [...(codec.sink_caps ?? []), ...(codec.src_caps ?? [])].join("\n\n")));
  card.append(raw);
  return card;
}
function adapterCard(adapter) {
  const card = text("article", "", "gpu-adapter-card");
  const heading = text("div", "", "gpu-adapter-heading");
  heading.append(text("h3", adapter.display_name), text("span", STATES[adapter.query_status] ?? "Невідомий стан", `gpu-state ${adapter.query_status}`));
  const info = text("dl", "", "gpu-parameters");
  parameter(info, "Драйвер", adapter.driver_version || adapter.kernel_driver);
  parameter(info, "PCI", adapter.pci_bus_id);
  parameter(info, "VRAM", adapter.memory_mib != null ? `${(adapter.memory_mib / 1024).toLocaleString("uk-UA", { maximumFractionDigits: 2 })} GiB` : null);
  parameter(info, "CUDA ID", adapter.cuda_device_id);
  card.append(heading, info);
  if (adapter.message) card.append(text("p", adapter.message, "gpu-message"));
  for (const codec of adapter.codecs ?? []) card.append(codecCard(codec));
  if (adapter.backend === "nvenc" && adapter.cuda_device_id != null) {
    const present = new Set((adapter.codecs ?? []).map((codec) => codec.codec));
    const missing = Object.keys(CODECS).filter((key) => !present.has(key));
    if (missing.length) card.append(text("p", `Не надано GStreamer: ${missing.map((key) => CODECS[key]).join(", ")}.`, "muted small-text"));
  }
  return card;
}

export function initializeGpuPanel({ request, role }) {
  const panel = document.querySelector("#gpu-panel");
  const name = document.querySelector("#gpu-name");
  const summary = document.querySelector("#gpu-summary-codecs");
  const indicator = document.querySelector("#gpu-status");
  const status = document.querySelector("#gpu-query-state");
  const error = document.querySelector("#gpu-error");
  const list = document.querySelector("#gpu-adapters");
  const refresh = document.querySelector("#gpu-refresh");
  let timer;
  let disposed = false;
  let refreshing = false;
  let queryRunning = false;
  refresh.hidden = !["admin", "operator"].includes(role);

  function render(snapshot) {
    queryRunning = snapshot.query_running;
    const adapters = snapshot.adapters ?? [];
    const primary = adapters.find((adapter) => adapter.backend === "nvenc") ?? adapters[0];
    const supported = [...new Set(adapters.flatMap((adapter) => (adapter.codecs ?? []).map((codec) => codec.codec)))];
    name.textContent = primary ? `${primary.display_name}${adapters.length > 1 ? ` +${adapters.length - 1}` : ""}` : snapshot.initialized ? "NVIDIA GPU не виявлено" : "GPU · перевірка";
    summary.textContent = snapshot.query_running ? "Опитування можливостей…" : snapshot.stale ? "Попередні дані · потрібна перевірка" : supported.length ? supported.map((key) => CODECS[key] ?? key).join(" · ") : "Апаратні енкодери не підтверджені";
    const state = snapshot.query_running ? "querying" : snapshot.last_error || snapshot.stale ? "failed" : adapters.some((adapter) => adapter.query_status === "complete") ? "complete" : "partial";
    indicator.className = `gpu-status ${state}`;
    const date = snapshot.queried_at ? new Date(snapshot.queried_at).toLocaleString("uk-UA") : "ще не завершено";
    status.textContent = `${snapshot.query_running ? "Триває перевірка. " : ""}${snapshot.from_cache ? "Збережені можливості" : "Останнє опитування"}: ${date}${snapshot.gstreamer_version ? ` · ${snapshot.gstreamer_version}` : ""}${snapshot.stale ? " · дані неактуальні" : ""}`;
    error.textContent = snapshot.last_error ?? "";
    error.hidden = !snapshot.last_error;
    list.replaceChildren(...adapters.map(adapterCard));
    if (!adapters.length && snapshot.initialized && !snapshot.query_running && !snapshot.last_error) list.append(text("p", "Відеокарти NVIDIA не знайдені в інвентарі сервера.", "muted"));
    refresh.disabled = snapshot.query_running || refreshing;
  }
  function schedule(snapshot) {
    clearTimeout(timer);
    if (!disposed && snapshot.query_running) timer = setTimeout(load, 1500);
  }
  async function load() {
    try {
      const snapshot = await request("/api/hardware/encoder-capabilities");
      if (disposed) return;
      render(snapshot);
      schedule(snapshot);
    } catch (failure) {
      if (disposed) return;
      name.textContent = "GPU · дані недоступні";
      error.textContent = failure.message;
      error.hidden = false;
      indicator.className = "gpu-status failed";
      queryRunning = false;
      refresh.disabled = false;
    }
  }
  refresh.addEventListener("click", async () => {
    if (refreshing) return;
    refreshing = true;
    refresh.disabled = true;
    clearTimeout(timer);
    try {
      const snapshot = await request("/api/hardware/encoder-capabilities/refresh", { method: "POST", body: {} });
      refreshing = false;
      render(snapshot);
      schedule(snapshot);
    } catch (failure) {
      error.textContent = failure.message;
      error.hidden = false;
      queryRunning = false;
    } finally {
      refreshing = false;
      refresh.disabled = queryRunning;
    }
  });
  document.addEventListener("pointerdown", (event) => { if (panel.open && !panel.contains(event.target)) panel.open = false; });
  panel.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { panel.open = false; panel.querySelector("summary").focus(); }
  });
  window.addEventListener("pagehide", () => { disposed = true; clearTimeout(timer); });
  window.addEventListener("pageshow", (event) => { if (event.persisted) { disposed = false; load(); } });
  load();
}
