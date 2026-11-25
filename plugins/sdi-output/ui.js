import { appendField, selectField, settingsController } from "/assets/node-fields.js";

/** Build one SDI playout form and return its settings collector. */
export function mount(container, { readOnly }) {
  const fields = document.createElement("div");
  fields.className = "node-sdi-output__fields";
  container.append(fields);
  const port = appendField(fields, "Порт DeckLink", document.createElement("select"), { readOnly });
  port.required = true;
  const mode = document.createElement("input");
  mode.placeholder = "1080p25";
  mode.required = true;
  mode.maxLength = 32;
  appendField(fields, "Режим SDI", mode, { readOnly, help: "Назва режиму з gst-inspect-1.0 decklinkvideosink." });
  const format = appendField(fields, "Формат відео",
    selectField([["UYVY", "UYVY · 8 біт"], ["v210", "v210 · 10 біт"]], "UYVY"), { readOnly });
  const delay = document.createElement("input");
  delay.type = "number";
  delay.min = "60";
  delay.max = "500";
  delay.required = true;
  appendField(fields, "Буфер відтворення, мс", delay, { readOnly });

  /** Read the current form without writing the config or changing worker state. */
  function readSettings() {
    return { persistent_id: port.value, mode: mode.value.trim(), video_format: format.value, playout_delay_ms: delay.valueAsNumber };
  }

  /** Load the saved playout parameters into an untouched form. */
  function applySettings(settings) {
    mode.value = settings.mode ?? "";
    format.value = settings.video_format ?? "UYVY";
    delay.value = settings.playout_delay_ms ?? 100;
  }
  return settingsController(container, fields, { port, direction: "output", readSettings, applySettings });
}
