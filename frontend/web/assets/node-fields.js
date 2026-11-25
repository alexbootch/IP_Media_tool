/** Add a labelled field to a module-owned form container. */
export function appendField(container, label, input, { readOnly = false, help = "" } = {}) {
  const wrapper = document.createElement("label");
  const text = document.createElement("span");
  text.textContent = label;
  input.disabled = readOnly;
  wrapper.append(text, input);
  if (help) {
    const hint = document.createElement("small");
    hint.className = "field-help";
    hint.textContent = help;
    wrapper.append(hint);
  }
  container.append(wrapper);
  return input;
}

/** Build a select with stable values and labels supplied by a node module. */
export function selectField(options, value) {
  const select = document.createElement("select");
  setOptions(select, options, value);
  return select;
}

/** Replace options while preserving the module's current value. */
function setOptions(select, options, value) {
  select.replaceChildren(...options.map(([key, label]) => new Option(label, key)));
  select.value = value;
}

/** Refresh the discovered ports for one direction, retaining a saved unavailable ID. */
export function updateDeckLinkPorts(select, direction, capabilities, currentId = select.value) {
  const label = capabilities === undefined ? "Завантаження портів…" : capabilities ? "Оберіть порт DeckLink" : "Перелік портів недоступний";
  const options = [["", label]];
  for (const port of capabilities?.ports ?? []) {
    if (!port[`has_${direction}`] || port.identity_status !== "persistent") continue;
    options.push([port.persistent_id, `${port.model_name} · ID ${port.persistent_id}`]);
  }
  if (currentId && !options.some(([id]) => id === currentId)) options.push([currentId, `Збережений ID ${currentId}`]);
  setOptions(select, options, currentId);
}

/** Keep live caps separate from editable values and release the form on close. */
export function settingsController(container, fields, { port, direction, readSettings, applySettings }) {
  const details = document.createElement("p");
  details.className = "field-help";
  details.hidden = true;
  fields.append(details);
  let dirty = false;
  let capabilities;
  let currentStatus;

  /** Preserve edited values if the control plane publishes another snapshot. */
  function markDirty() {
    dirty = true;
  }
  fields.addEventListener("change", markDirty);
  fields.addEventListener("input", markDirty);
  return {
    readSettings,
    /** Apply a config snapshot only while the form is untouched. */
    updateConfig(config) {
      if (dirty) return;
      const settings = config?.settings ?? {};
      updateDeckLinkPorts(port, direction, capabilities, settings.persistent_id ?? "");
      applySettings(settings);
    },
    /** Render media caps separately from the editable configuration. */
    updateStatus(worker) {
      const status = worker?.status;
      if (currentStatus && status?.instance_id === currentStatus.instance_id &&
          status.status_revision < currentStatus.status_revision) return;
      currentStatus = status;
      const caps = status?.metrics?.video_caps;
      details.textContent = caps ?? "";
      details.hidden = !caps;
    },
    /** Update the port choices without resetting this node's draft. */
    updateCapabilities(snapshot) {
      capabilities = snapshot;
      updateDeckLinkPorts(port, direction, capabilities);
    },
    /** Remove the instance's own listeners and controls. */
    unmount() {
      fields.removeEventListener("change", markDirty);
      fields.removeEventListener("input", markDirty);
      container.replaceChildren();
    },
  };
}
