// The device's configuration in the view Gerät (server: device_config.py): the check against
// what the app needs and the saved profile, the settings, the profile and backups.
// Writing goes in three steps: edits are collected on the page (marked, counted in the bar
// under the sections), "Prüfen" shows what would change and what follows from it, and only
// "Auf Gerät schreiben" writes. The node then reboots; the configuration read after it says
// whether it took the settings.
import { bindInputs, inputsHTML } from "./forms.js";
import { locale, t } from "./i18n.js";
import { $, esc, fmt, getJSON, postJSON } from "./util.js";

let E = {};  // { toast, refreshStatus }
let D = null;  // the configuration as last read, null without a connected device
let edits = {};  // name -> value: changed on the page, not on the device yet
let review = null;  // the server's preview of the edits while it is shown
let busy = false;  // a preview or a write is on its way
let awaiting = false;  // written: the next configuration read carries the result

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
// The bar under the sections: the count of edits, then the preview with the write button.
function pending(error = "") {
  const box = $("#cfgPending"), n = Object.keys(edits).length;
  box.hidden = !D || !n;
  if (box.hidden) return;
  const off = busy ? "disabled" : "";
  if (!review) {
    box.innerHTML = `<div class="jobform">
      <div class="hd">${esc(n === 1 ? t("Eine Änderung, noch nicht auf dem Gerät") : t("{n} Änderungen, noch nicht auf dem Gerät", { n }))}</div>
      <div class="msg" role="alert">${esc(error)}</div>
      <div class="row2"><button class="btn small" data-discard ${off}>${esc(t("Verwerfen"))}</button>
        <button class="btn small on" data-review ${off}>${esc(t("Prüfen …"))}</button></div></div>`;
    box.querySelector("[data-discard]").addEventListener("click", () => { edits = {}; show(D); });
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
  try { r = await postJSON(`api/device/config/${step}`, { changes: edits }); } catch (e) { error = e.message; }
  busy = false;
  if (!r) { pending(error); if (step === "write") E.refreshStatus(); return; }
  if (step === "preview") { review = r; pending(); return; }
  edits = {}; review = null;
  if (r.view) {  // the simulated radio: no reboot, the result is there at once
    show(r.view);
    E.toast(r.view.result.text, { bad: !r.view.result.ok });
    return;
  }
  awaiting = true;
  show(null, "", true);
  E.refreshStatus();
}

// ---------------------------------------------------------------- the sections
function position(c) {
  if (c.position_bits === 0) return t("keine Position");
  if (c.position_bits === 32) return t("genaue Position");
  return t("Position auf {d} genau", { d: distance(c.position_m) });
}

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
  review = null;
  for (const s of d.form.flatMap(f => f.settings)) if (s.name in edits && same(s, edits[s.name], s.default)) delete edits[s.name];
  const warn = d.checks.some(c => c.level === "warn");
  $("#cfgDot").style.background = warn ? "var(--warn)" : "var(--ok)";
  $("#cfgChecks").innerHTML = notesHTML(d.checks);

  const r = d.radio;
  $("#cfgRadio").innerHTML = `${formHTML("radio")}<dl class="kvl">
    ${row(t("Preset"), r.preset)}${row(t("Region"), r.region)}
    ${row(t("Frequenz-Slot"), r.slot ? String(r.slot) : t("aus dem Namen des Primärkanals"))}
    ${row(t("Senden"), r.tx_enabled ? t("an") : t("aus"))}</dl>`;

  $("#cfgChanCount").textContent = d.channels.length;
  $("#cfgChannels").innerHTML = d.channels.map(c => `<div class="chan">
    <span class="ix">${c.index}</span>
    <div><strong>${esc(c.name || (c.primary ? d.radio.preset : t("ohne Namen")))}</strong>
      <span class="sub ${c.private ? "" : "open"}">${esc(c.key_label)}</span>
      <span class="sub">${esc([c.primary ? t("primär") : t("sekundär"), position(c),
        c.uplink || c.downlink ? t("MQTT an") : ""].filter(Boolean).join(" · "))}</span></div></div>`).join("");

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
