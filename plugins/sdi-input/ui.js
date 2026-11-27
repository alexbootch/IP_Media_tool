import { appendField, deckLinkCardControls, selectField, settingsController } from "/assets/node-fields.js";

/** Mount the operator's frequently used SDI capture controls on the graph card. */
export function mountCard(container, { config, saveSettings }) {
  const fields = document.createElement("div");
  fields.className = "node-sdi-input__card";
  container.append(fields);
  const channels = appendField(fields, "PCM канали",
    selectField([["2", "2"], ["8", "8"], ["16", "16"]], String(config.settings.audio_channels)), { readOnly: true });
  return deckLinkCardControls(container, fields, { config, direction: "input", saveSettings,
    controls: [{ input: channels, key: "audio_channels", parse: Number }] });
}

/** Build one SDI capture form and return its settings collector. */
export function mount(container, { readOnly }) {
  const fields = document.createElement("div");
  fields.className = "node-sdi-input__fields";
  container.append(fields);
  const port = appendField(fields, "Порт DeckLink", document.createElement("select"), { readOnly });
  port.required = true;
  const channels = appendField(fields, "Вбудовані аудіоканали",
    selectField([["", "Оберіть кількість каналів"], ["2", "2"], ["8", "8"], ["16", "16"]], ""),
    { readOnly, help: "Вкажи кількість каналів генератора чи джерела SDI." });
  channels.required = true;
  const format = appendField(fields, "Формат PCM",
    selectField([["S32LE", "S32LE"], ["S16LE", "S16LE"]], "S32LE"), { readOnly });
  const measure = document.createElement("input");
  measure.type = "checkbox";
  appendField(fields, "Вимірювати A/V за спалахом та імпульсом", measure, { readOnly });

  /** Read the current form without writing the config or changing worker state. */
  function readSettings() {
    return { persistent_id: port.value, audio_channels: Number(channels.value), audio_format: format.value, measure_av: measure.checked };
  }

  /** Load the saved capture parameters into an untouched form. */
  function applySettings(settings) {
    channels.value = String(settings.audio_channels ?? "");
    format.value = settings.audio_format ?? "S32LE";
    measure.checked = settings.measure_av ?? false;
  }
  return settingsController(container, fields, { port, direction: "input", readSettings, applySettings });
}
