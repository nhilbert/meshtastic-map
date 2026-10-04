// The device's configuration in the view Gerät (server: device_config.py): the check against
// what the app needs and the saved profile, the settings, the channels, the profile and backups.
// Writing goes in three steps: edits are collected on the page (marked, counted in the bar
// under the sections), "Prüfen" shows what would change and what follows from it, and only
// "Auf Gerät schreiben" writes. The node then reboots; the configuration read after it says
// whether it took the settings.
import { bindInputs, inputsHTML } from "./forms.js";
import { iconButton } from "./icons.js";
import { locale, t } from "./i18n.js";
import { $, esc, fmt, getJSON, postJSON } from "./util.js";

let E = {};  // { toast, refreshStatus }
let D = null;  // the configuration as last read, null without a connected device
let edits = {};  // name -> value: changed on the page, not on the device yet
// The channels as they are to be, once one was touched: entries in the new order, each
// { id, from (the device's index, null for a new one), name, key (keep | generate | custom |
// default | none), key_text, precision, deleted } or { id, import: url }. null: as on the device.
let chans = null;
const chanOpen = new Set();  // ids of the entries whose form is open
let importOpen = false;
let shared = null;  // { index (null: all), url, add }: the channel URL on show
let serial = 0;  // for the ids of new entries
let review = null;  // the server's preview of the edits while it is shown
let busy = false;  // a preview or a write is on its way
let awaiting = false;  // written: the next configuration read carries the result

const MAX_CHANNELS = 8;
const PRECISIONS = [0, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 32];  // position bits offered
const row = (label, value) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`;
const yesNo = v => v ? t("ja") : t("nein");
const when = s => new Date(s * 1000).toLocaleString(locale, { dateStyle: "medium", timeStyle: "short" });
const distance = m => m >= 1000 ? `${fmt(m / 1000, 1)} km` : `${fmt(m, 0)} m`;
const interval = s => s >= 3600 && s % 3600 === 0 ? `${s / 3600} h` : s >= 60 ? `${fmt(s / 60, 0)} min` : `${s} s`;
const MARK = { ok: "✓", warn: "⚠", info: "ⓘ" };
const notesHTML = notes => `<ul class="checks">${notes.map(c =>
  `<li class="${c.level}"><span aria-hidden="true">${MARK[c.level]}</span>${esc(c.text)}</li>`).join("")}</ul>`;

const TELEMETRY = { air_quality: () => t("Luftqualität"), health: () => t("Gesundheit") };  // read-only kinds

export function initDevConfig(env) {
  E = env;
  $("#cfgBackup").addEventListener("click", () => save("backup", { private_key: $("#cfgPrivate").checked }));
  $("#cfgProfile").addEventListener("click", () => save("profile", {}));
}

// Called when the connection changes: the configuration is read once per connection.
// restarting: the node reboots after a write and comes back by itself.
export async function loadDevConfig(connected, restarting = false) {
  if (!connected) { show(null, "", restarting); return; }
  try {
    const d = await getJSON("api/device/config");
    show(d);
    if (awaiting && d.result) { awaiting = false; E.toast(d.result.text, { bad: !d.result.ok }); }
  } catch (e) { show(null, e.message); }
}

async function save(what, body) {
  try {
    const d = await postJSON(`api/device/config/${what}`, body);
    show(d);
    if (what === "backup") E.toast(t("Backup geschrieben nach {dir}", { dir: d.files.dir }));
    else E.toast(t("Profil gespeichert: der Abgleich prüft jetzt dagegen."));
  } catch (e) { E.toast(e.message, { bad: true }); }
}

// ---------------------------------------------------------------- edits, preview, write
function same(s, a, b) {
  if (s.type === "bool") return !!a === !!b;
  if (s.type === "number") return Number(a) === Number(b);
  return String(a ?? "").trim() === String(b ?? "").trim();
}
// A section's values: the device's, overlaid with the edits.
const valuesOf = f => Object.fromEntries(f.settings.map(s => [s.name, s.name in edits ? edits[s.name] : s.default]));
function formHTML(id) {
  const f = D.form.find(x => x.id === id);
  return `<div class="cfgform" data-form="${id}">${D.locked ? `<p class="note" style="margin:0">${esc(D.locked)}</p>` : ""}
    ${inputsHTML(f.settings, valuesOf(f), "cfg")}</div>`;
}
function bindForm(id) {
  const f = D.form.find(x => x.id === id), root = document.querySelector(`[data-form="${id}"]`);
  const values = valuesOf(f);
  if (D.locked) root.querySelectorAll("[data-s]").forEach(inp => { inp.disabled = true; });
  bindInputs(root, f.settings, values, s => {
    if (same(s, values[s.name], s.default)) delete edits[s.name]; else edits[s.name] = values[s.name];
    review = null;  // it showed other edits
    markEdits(); pending();
  });
}
function markEdits() {
  document.querySelectorAll(".cfgform [data-s]").forEach(inp =>
    inp.closest("label").classList.toggle("changed", inp.dataset.s in edits));
}
// What a write would send: the changed settings, and the channel list if a channel changed.
const changes = () => channelChanges() ? { ...edits, channels: wantedChannels() } : edits;
// The bar under the sections: the count of edits, then the preview with the write button.
function pending(error = "") {
  const box = $("#cfgPending"), n = Object.keys(edits).length + channelChanges();
  box.hidden = !D || !n;
  if (box.hidden) return;
  const off = busy ? "disabled" : "";
  if (!review) {
    const title = n === 1 ? t("Eine Änderung, noch nicht auf dem Gerät") : t("{n} Änderungen, noch nicht auf dem Gerät", { n });
    const count = box.querySelector("[data-count]");
    if (count) {  // the same bar: its buttons stay, a click on one may be on its way
      count.textContent = title;
      box.querySelector(".msg").textContent = error;
      box.querySelectorAll("button").forEach(b => { b.disabled = busy; });
      return;
    }
    box.innerHTML = `<div class="jobform">
      <div class="hd" data-count>${esc(title)}</div>
      <div class="msg" role="alert">${esc(error)}</div>
      <div class="row2"><button class="btn small" data-discard ${off}>${esc(t("Verwerfen"))}</button>
        <button class="btn small on" data-review ${off}>${esc(t("Prüfen …"))}</button></div></div>`;
    box.querySelector("[data-discard]").addEventListener("click", () => { edits = {}; forgetChannels(); show(D); });
    box.querySelector("[data-review]").addEventListener("click", () => send("preview"));
    return;
  }
  box.innerHTML = `<div class="jobform">
    <div class="hd">${esc(t("Auf das Gerät schreiben?"))}</div>
    <dl class="changes">${review.changes.map(c =>
      `<div><dt>${esc(c.label)}</dt><dd><s>${esc(c.old)}</s> → <b>${esc(c.new)}</b></dd></div>`).join("")}</dl>
    ${notesHTML(review.notes)}
    <div class="msg" role="alert">${esc(error)}</div>
    <div class="row2"><button class="btn small" data-back ${off}>${esc(t("Zurück"))}</button>
      <button class="btn small on" data-write ${off}>${esc(busy ? t("Schreibe …") : t("Auf Gerät schreiben"))}</button></div></div>`;
  box.querySelector("[data-back]").addEventListener("click", () => { review = null; pending(); });
  box.querySelector("[data-write]").addEventListener("click", () => send("write"));
  box.scrollIntoView({ block: "nearest" });
}
async function send(step) {
  busy = true; pending();
  let r = null, error = "";
  try { r = await postJSON(`api/device/config/${step}`, { changes: changes() }); } catch (e) { error = e.message; }
  busy = false;
  if (!r) { pending(error); if (step === "write") E.refreshStatus(); return; }
  if (step === "preview") { review = r; pending(); return; }
  edits = {}; review = null; forgetChannels();
  if (r.view) {  // the simulated radio: no reboot, the result is there at once
    show(r.view);
    E.toast(r.view.result.text, { bad: !r.view.result.ok });
    return;
  }
  awaiting = true;
  show(null, "", true);
  E.refreshStatus();
}

// ---------------------------------------------------------------- channels
const precisionM = bits => 23860000 / 2 ** bits;
function position(bits) {
  if (bits === 0) return t("keine Position");
  if (bits === 32) return t("genaue Position");
  return t("Position auf {d} genau", { d: distance(precisionM(bits)) });
}
const deviceEntry = c => ({ id: "c" + c.index, from: c.index, name: c.name, key: "keep", key_text: "", precision: c.position_bits, deleted: false });
function forgetChannels() { chans = null; chanOpen.clear(); importOpen = false; shared = null; }
// The entries to edit: made from the device's channels at the first change.
const entries = () => chans || (chans = D.channels.map(deviceEntry));
function changed(e) {
  if (e.from === null || e.import) return true;
  const c = D.channels[e.from];
  return e.deleted || e.name.trim() !== c.name || e.key !== "keep" || +e.precision !== c.position_bits;
}
const channelChanges = () => chans ? chans.filter(changed).length : 0;
const wantedChannels = () => chans.filter(e => !e.deleted).map(e => e.import ? { import: e.import }
  : { from: e.from, name: e.name, key: e.key, precision: +e.precision, ...(e.key === "custom" ? { key_text: e.key_text } : {}) });
// The key of an entry in words, and whether it is one of its own.
function keyOf(e, c) {
  if (e.key === "keep") return [c.key_label, c.private];
  if (e.key === "generate") return [t("neuer Schlüssel (AES-256)"), true];
  if (e.key === "custom") return [t("eigener Schlüssel"), true];
  return [e.key === "default" ? t("Standardschlüssel, öffentlich bekannt") : t("unverschlüsselt"), false];
}
function channelForm(e, index) {
  const keys = [["generate", t("neuen Schlüssel erzeugen (AES-256)")], ["custom", t("eigenen Schlüssel eingeben")],
    ["default", t("Standardschlüssel (öffentlich bekannt)")], ["none", t("ohne Verschlüsselung")]];
  if (e.from !== null) keys.unshift(["keep", t("unverändert: {key}", { key: D.channels[e.from].key_label })]);
  const bits = [...new Set([...PRECISIONS, +e.precision])].sort((a, b) => a - b);
  return [
    { name: "name", label: t("Name"), type: "text", help: index === 0 ? t("Leer: der Name des Presets") : t("höchstens 11 Zeichen") },
    { name: "key", label: t("Schlüssel"), type: "select", options: keys },
    ...(e.key === "custom" ? [{ name: "key_text", label: t("Schlüssel (Base64, 16 oder 32 Bytes)"), type: "text" }] : []),
    { name: "precision", label: t("Position auf diesem Kanal"), type: "select", options: bits.map(b => [String(b), position(b)]) },
  ];
}
// textOnly: just the name and facts of the row, to refresh them under an open form.
function channelRow(e, index, textOnly = false) {
  const off = D.locked ? "disabled" : "";
  if (e.import) {
    return `<div class="chan changed"><span class="ix">+</span><div class="cbody"><div class="site">
      <div class="txt"><span class="nm">${esc(t("Kanäle aus einer URL"))}</span><span class="sub">${esc(t("„Prüfen“ zeigt, welche dazukommen"))}</span></div>
      ${iconButton("trash", t("Entfernen"), `data-remove="${e.id}"`, { danger: true })}</div></div></div>`;
  }
  const c = e.from !== null ? D.channels[e.from] : null, primary = index === 0;
  const [key, own] = keyOf(e, c);
  const name = e.name.trim() || (primary ? D.radio.preset : t("ohne Namen"));
  const tag = e.deleted ? t("wird gelöscht") : !c ? t("neu") : changed(e) ? t("geändert") : "";
  const facts = [primary ? t("primär") : t("sekundär"), position(+e.precision), c && (c.uplink || c.downlink) ? t("MQTT an") : ""];
  const dirty = channelChanges() > 0;
  const buttons = e.deleted ? `<button class="btn small" data-restore="${e.id}">${esc(t("Zurücknehmen"))}</button>`
    : `${c && !primary ? iconButton("copy", dirty ? t("Teilen: erst die Änderungen schreiben oder verwerfen") : t("Kanal {n} teilen", { n: e.from }), `data-share="${e.from}" ${dirty ? "disabled" : ""}`) : ""}
      ${iconButton("edit", t("{name} bearbeiten", { name }), `data-edit="${e.id}" aria-expanded="${chanOpen.has(e.id)}" ${off}`)}
      ${primary ? "" : iconButton("trash", c ? t("{name} löschen", { name }) : t("Entfernen"), `data-remove="${e.id}" ${off}`, { danger: true })}`;
  const text = `<span class="nm">${esc(name)}${tag ? ` <span class="tag">${esc(tag)}</span>` : ""}</span>
    <span class="sub${own ? "" : " open"}">${esc(key)}</span>
    <span class="sub">${esc(facts.filter(Boolean).join(" · "))}</span>`;
  if (textOnly) return text;
  return `<div class="chan${changed(e) ? " changed" : ""}${e.deleted ? " gone" : ""}" data-row="${e.id}">
    <span class="ix">${e.deleted ? "–" : index}</span>
    <div class="cbody"><div class="site">
      <div class="txt">${text}</div>
      ${buttons}</div>
      ${chanOpen.has(e.id) && !e.deleted ? `<div class="siteform" data-chanform="${e.id}">${inputsHTML(channelForm(e, index), e, e.id)}</div>` : ""}
      ${shared && c && shared.index === e.from ? shareHTML() : ""}</div></div>`;
}
function shareHTML() {
  const note = shared.add
    ? t("Fügt beim Empfänger diesen Kanal hinzu. Die URL enthält den Schlüssel: nur an eigene Geräte geben.")
    : t("Ersetzt beim Empfänger alle Kanäle und die Funk-Einstellungen. Die URL enthält die Schlüssel: nur an eigene Geräte geben.");
  return `<div class="siteform"><label>${esc(t("Kanal-URL"))}<input type="text" readonly data-shareurl value="${esc(shared.url)}"></label>
    <p class="note" style="margin:0">${esc(note)}</p>
    <div class="row2"><button class="btn small" data-shareclose>${esc(t("Schließen"))}</button>
      <button class="btn small on" data-sharecopy>${esc(t("Kopieren"))}</button></div></div>`;
}
// The section Kanäle; redrawn alone after a channel edit, so the other forms keep their state.
function drawChannels() {
  const box = $("#cfgChannels"), list = chans || D.channels.map(deviceEntry);
  const active = list.filter(e => !e.deleted && !e.import).length, dirty = channelChanges() > 0;
  const off = D.locked ? "disabled" : "";
  let index = 0;
  $("#cfgChanCount").textContent = active;
  box.innerHTML = `${list.map(e => channelRow(e, e.deleted || e.import ? null : index++)).join("")}
    <div class="devrow">
      <button class="btn small" data-add ${off || (active >= MAX_CHANNELS ? "disabled" : "")}>＋ ${esc(t("Kanal"))}</button>
      <button class="btn small" data-import ${off || (active >= MAX_CHANNELS ? "disabled" : "")}>${esc(t("Aus URL …"))}</button>
      <button class="btn small" data-share="all" ${dirty ? "disabled" : ""} title="${esc(dirty ? t("Teilen: erst die Änderungen schreiben oder verwerfen") : t("Alle Kanäle als URL für ein anderes Gerät"))}">${esc(t("Alle teilen"))}</button>
    </div>
    ${importOpen ? `<div class="siteform"><label>${esc(t("Kanal-URL eines anderen Geräts"))}<input type="text" data-importurl placeholder="https://meshtastic.org/e/#…" autocomplete="off"></label>
      <p class="note" style="margin:0">${esc(t("Ihre Kanäle kommen als weitere Kanäle dazu; vorhandene bleiben, wie sie sind."))}</p>
      <div class="row2"><button class="btn small" data-importcancel>${esc(t("Abbrechen"))}</button>
        <button class="btn small on" data-importadd>${esc(t("Hinzufügen"))}</button></div></div>` : ""}
    ${shared && shared.index === null ? shareHTML() : ""}
    <div class="msg" role="alert" data-chanmsg></div>`;
  const on = (sel, fn) => box.querySelectorAll(sel).forEach(el => el.addEventListener("click", () => fn(el)));
  const entry = id => entries().find(e => e.id === id);
  const edited = () => { review = null; shared = null; drawChannels(); pending(); };
  on("[data-edit]", el => {
    entries();
    if (!chanOpen.delete(el.dataset.edit)) chanOpen.add(el.dataset.edit);
    drawChannels();
  });
  on("[data-remove]", el => {
    const e = entry(el.dataset.remove);
    if (e.from !== null && !e.import) e.deleted = true; else chans = chans.filter(x => x !== e);
    chanOpen.delete(e.id);
    edited();
  });
  on("[data-restore]", el => { entry(el.dataset.restore).deleted = false; edited(); });
  on("[data-add]", () => {
    const e = { id: "n" + ++serial, from: null, name: "", key: "generate", key_text: "", precision: 0, deleted: false };
    entries().push(e);
    chanOpen.add(e.id);
    edited();
    box.querySelector(`[data-chanform="${e.id}"] [data-s="name"]`).focus();
  });
  on("[data-import]", () => { importOpen = !importOpen; drawChannels(); box.querySelector("[data-importurl]")?.focus(); });
  on("[data-importcancel]", () => { importOpen = false; drawChannels(); });
  on("[data-importadd]", () => {
    const url = box.querySelector("[data-importurl]").value.trim();
    if (!url.includes("#")) { box.querySelector("[data-chanmsg]").textContent = t("Keine gültige Kanal-URL"); return; }
    entries().push({ id: "n" + ++serial, import: url });
    importOpen = false;
    edited();
  });
  on("[data-share]", async el => {
    const index = el.dataset.share === "all" ? null : +el.dataset.share;
    try { shared = { index, ...await postJSON("api/device/config/share", { index }) }; drawChannels(); }
    catch (e) { box.querySelector("[data-chanmsg]").textContent = e.message; }
  });
  on("[data-shareclose]", () => { shared = null; drawChannels(); });
  on("[data-sharecopy]", async () => {
    try { await navigator.clipboard.writeText(shared.url); E.toast(t("Kanal-URL kopiert.")); }
    catch (_) { box.querySelector("[data-shareurl]").select(); }
  });
  box.querySelectorAll("[data-chanform]").forEach(root => {
    const e = entry(root.dataset.chanform), i = chans.filter(x => !x.deleted && !x.import).indexOf(e);
    bindInputs(root, channelForm(e, i), e, s => {
      if (s.name === "key") { edited(); return; }  // another key: the form changes
      // name or position: refresh the row in place, so that a click that ended the typing
      // still lands on what it was aimed at
      review = null;
      const rowEl = root.closest("[data-row]"), dirty = channelChanges() > 0;
      rowEl.classList.toggle("changed", changed(e));
      rowEl.querySelector(".txt").innerHTML = channelRow(e, i, true);
      box.querySelectorAll("[data-share]").forEach(b => { b.disabled = dirty; });
      pending();
    });
  });
}

// ---------------------------------------------------------------- the sections
function show(d, error = "", restarting = false) {
  D = d;
  $("#cfgSections").hidden = !d;
  if (!d) {
    $("#cfgDot").style.background = restarting ? "var(--warn)" : "var(--line)";
    const text = error || (restarting
      ? t("Das Gerät startet neu und übernimmt die Einstellungen. Die Verbindung kommt gleich von selbst wieder.")
      : t("Braucht das verbundene Gerät."));
    $("#cfgChecks").innerHTML = `<p class="note${error ? " bad" : ""}" style="margin:0" role="status">${esc(text)}</p>`;
    pending();
    return;
  }
  review = null; shared = null;
  for (const s of d.form.flatMap(f => f.settings)) if (s.name in edits && same(s, edits[s.name], s.default)) delete edits[s.name];
  // channel edits refer to the device's channels by index: another set of channels ends them
  if (chans && chans.some(e => e.from != null && e.from >= d.channels.length)) forgetChannels();
  const warn = d.checks.some(c => c.level === "warn");
  $("#cfgDot").style.background = warn ? "var(--warn)" : "var(--ok)";
  $("#cfgChecks").innerHTML = notesHTML(d.checks);

  const r = d.radio;
  $("#cfgRadio").innerHTML = `${formHTML("radio")}<dl class="kvl">
    ${row(t("Preset"), r.preset)}${row(t("Region"), r.region)}
    ${row(t("Frequenz-Slot"), r.slot ? String(r.slot) : t("aus dem Namen des Primärkanals"))}
    ${row(t("Senden"), r.tx_enabled ? t("an") : t("aus"))}</dl>`;

  drawChannels();

  $("#cfgTelemetry").innerHTML = `${formHTML("telemetry")}
    <dl class="kvl">${d.telemetry.filter(x => TELEMETRY[x.kind]).map(x => row(TELEMETRY[x.kind](),
    !x.on ? t("aus") : x.interval_s ? t("an, alle {t}", { t: interval(x.interval_s) }) : t("an, Standardabstand"))).join("")}</dl>
    <p class="note">${esc(t("Was das Gerät von sich aus misst und ins Netz sendet. Umwelt- und Stromwerte brauchen einen Sensor."))}</p>`;

  const s = d.security;
  $("#cfgSecurity").innerHTML = `<dl class="kvl">
    ${row(t("Admin-Schlüssel"), String(s.admin_keys))}${row(t("Verwalteter Modus"), yesNo(s.managed))}</dl>
    <div class="keylbl">${esc(t("Öffentlicher Schlüssel"))}</div>
    <div class="keyrow"><code>${esc(s.public_key || "—")}</code>
      ${s.public_key ? `<button class="btn small" data-copy>${esc(t("Kopieren"))}</button>` : ""}</div>
    <p class="note">${esc(t("Der private Schlüssel wird nicht angezeigt."))}</p>`;
  $("#cfgSecurity [data-copy]")?.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(s.public_key); E.toast(t("Öffentlicher Schlüssel kopiert.")); }
    catch (_) { E.toast(s.public_key); }
  });

  $("#cfgIdentity").innerHTML = `${formHTML("identity")}<dl class="kvl">
    ${row(t("Node-ID"), d.node || "—")}${row(t("Weiterleiten"), d.identity.rebroadcast)}</dl>`;

  $("#cfgAll").innerHTML = d.all.map(sec => `<details class="cfgall"><summary>${esc(sec.name)}</summary>
    <table>${sec.fields.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(String(v))}</td></tr>`).join("")}</table></details>`).join("");

  const f = d.files;
  $("#cfgFiles").innerHTML = `<dl class="kvl">
    ${row(t("Profil"), f.profile ? when(f.profile) : t("keins"))}
    ${row(t("Backups"), f.backups ? t("{n}, zuletzt {date}", { n: f.backups, date: when(f.last_backup) }) : t("keins"))}</dl>
    <p class="note" style="overflow-wrap:anywhere">${esc(f.dir)}</p>`;

  for (const id of ["radio", "telemetry", "identity"]) bindForm(id);
  markEdits(); pending();
}
