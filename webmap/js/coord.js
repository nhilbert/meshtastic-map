// Coordination mode: the rail section (mode switch, settings, mission form with the path
// builder, mission cards, targets editor) and the inspector tab "Einsatz". The server decides
// and sends (coord/missions.py); the page polls /api/coord and shows what happened.
import { bindInputs, initialValues, inputsHTML } from "./forms.js";
import { locale, t } from "./i18n.js";
import { openTaskForm } from "./tasks.js";
import { $, esc, fmt, getJSON, postJSON } from "./util.js";
import { showSection } from "./workspace.js";
import { buttonLabel, symbolSVG } from "./icons.js";

// Mission states are German codes: t("zugewiesen") t("unterwegs") t("wartet") t("erreicht")
// t("abgebrochen") t("beendet"); message kinds: t("assign") t("status") t("route") t("target")
// t("path") t("help") t("halt") t("resume") t("aborted") t("ended") t("offcourse") t("late")
// t("early") t("confirm") t("reached") t("next") t("changed") t("legend") t("nogo") t("notice") t("place");
// speed sources: t("gemessen") t("Standard")
const ACTIVE = ["zugewiesen", "unterwegs", "wartet"];
const STATE_CLASS = { zugewiesen: "wait", unterwegs: "run", wartet: "wait", erreicht: "ok", abgebrochen: "bad", beendet: "off" };
const C = {
  api: null, data: null, timer: null, prev: {}, err: "",
  form: null,          // { node, path: [rows], profile, lang, msg }
  settings: null,      // values being edited, null = closed
  targets: false,      // targets editor open
  areas: false,        // areas and places editor open
  editArea: null,      // { isNew, id, name, kind, text, buffer_m, polygon }
  editPlace: null,     // { isNew, id, name, lat, lon, radius_m, text }
  editTarget: null,    // { isNew, orig, name, lat, lon, note }
  sel: null,           // node of the mission shown in the inspector
};

// api: { store, toast(msg, opts), openInspector(tab), updateInspector(), pickOnMap(label, cb),
//        tempMarker(lat, lon), focusNode(id), refreshLayer(id), nodes() -> rows of the node layer,
//        sites() -> [{name, lat, lon}] }
export function initCoord(api) {
  C.api = api;
  C.targets = api.store.get("coord.targets", false);
  C.areas = api.store.get("coord.areas", false);
  $("#btnCoord").addEventListener("click", () => {
    showSection("coord");
  });
  poll();
}
export const selectedMission = () => (C.sel && C.data && C.data.missions.find(m => m.node === C.sel)) || null;
export function pollSoon() { clearTimeout(C.timer); C.timer = setTimeout(poll, 200); }

// "Ziel zuweisen" from a node popup or the node list: the mission form with the node preset.
export function assignTo(nodeId) {
  openForm(nodeId);
  showSection("coord");
  $("#coordBox").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ---------------------------------------------------------------- polling
async function poll() {
  clearTimeout(C.timer);
  try {
    const d = await getJSON("api/coord");
    for (const m of d.missions) {
      const prev = C.prev[m.node];
      if (prev && prev !== m.state) transition(m, prev);
    }
    C.prev = Object.fromEntries(d.missions.map(m => [m.node, m.state]));
    C.data = d; C.err = "";
    if (C.sel && !d.missions.some(m => m.node === C.sel)) { C.sel = null; C.api.updateInspector(); }
  } catch (e) { C.err = e.message; }
  // While an editor is open the section is not rebuilt (that would wipe what is being typed);
  // only the badge follows the state.
  if (editing()) renderBadge(); else render();
  if (C.sel && !$("#t_coord").hidden) renderMissionDetail();
  const active = C.data && C.data.enabled && C.data.missions.some(m => ACTIVE.includes(m.state));
  C.timer = setTimeout(poll, active ? 3000 : 10000);
}
function transition(m, prev) {
  const who = nodeName(m.node), stop = m.path[m.index] ? m.path[m.index].name : "";
  if (m.state === "erreicht") C.api.toast(t("{name} hat {stop} erreicht", { name: who, stop }), { action: [t("Details"), () => showDetail(m.node)] });
  else if (m.state === "abgebrochen") C.api.toast(t("{name} hat den Einsatz abgebrochen", { name: who }), { bad: true, action: [t("Details"), () => showDetail(m.node)] });
  else if (m.state === "wartet") C.api.toast(t("{name} wartet an {stop}", { name: who, stop }));
  else if (prev === "zugewiesen" && m.state === "unterwegs") C.api.toast(t("{name} ist unterwegs nach {stop}", { name: who, stop }));
  C.api.refreshLayer("coord");
}

// ---------------------------------------------------------------- helpers
function nodeName(id) {
  const n = (C.api.nodes() || []).find(x => x.id === id);
  return n ? `${n.short || id.slice(-4)} · ${n.long || id}` : id;
}
const clock = ts => ts ? new Date(ts * 1000).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" }) : "–";
const ago = s => (s === null || s === undefined) ? "–" : s < 90 ? t("vor {n} s", { n: Math.round(s) }) : s < 5400 ? t("vor {n} min", { n: Math.round(s / 60) }) : t("vor {n} h", { n: fmt(s / 3600, 1) });
const dist = m => (m === null || m === undefined) ? "–" : m < 995 ? `${Math.round(m)} m` : `${fmt(m / 1000, 1)} km`;
const eta = s => (s === null || s === undefined) ? "–" : `~${Math.max(1, Math.round(s / 60))} min`;
const stateChip = s => `<span class="chip ${STATE_CLASS[s] || ""}">${esc(t(s))}</span>`;
function metricsHTML(m) {
  const k = m.metrics || {};
  if (k.dist_m == null) return `<p class="note" style="margin:0">${t("noch keine Position vom Knoten")}</p>`;
  const readings = [[t("Distanz"), `${dist(k.dist_m)} ${k.compass || ""}`],
    [t("Ankunft"), eta(k.eta_s)], [t("Tempo"), `${fmt(k.speed_kmh, 1)} km/h`]];
  const parts = [t("Position {ago}", { ago: ago(k.position_age_s) })];
  if (k.mode === "route") parts.unshift(t("{d} auf der Straße", { d: dist(k.route_left_m) }) + (k.off_route_m > 30 ? ` (${t("{d} daneben", { d: dist(k.off_route_m) })})` : ""));
  if (k.margin_min !== undefined) parts.push(k.margin_min >= 0 ? t("{n} min vor Plan", { n: k.margin_min }) : t("{n} min hinter Plan", { n: -k.margin_min }));
  if (k.stale) parts.push("⚠ " + t("Position veraltet"));
  return `<dl class="telemetry">${readings.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join("")}</dl>
    <div class="meta${k.stale ? " st bad" : ""}">${esc(parts.join(" · "))}</div>`;
}
function statusClass(s) { return s === "zugestellt" || s === "im Netz" ? "ok" : /^nicht/.test(s || "") ? "bad" : ""; }
function statusText(s) {  // delivery states are German codes, see messages.js
  const m = /^nicht zugestellt \((.+)\)$/.exec(s || "");
  if (m) return t("nicht zugestellt ({reason})", { reason: m[1] });
  const f = /^nicht gesendet: (.+)$/.exec(s || "");
  if (f) return t("nicht gesendet: {error}", { error: f[1] });
  return t(s || "gesendet");  // t("gesendet") t("zugestellt") t("im Netz")
}

// ---------------------------------------------------------------- rail section
const editing = () => !!(C.form || C.settings || C.editTarget || C.editArea || C.editPlace);
function renderBadge() {
  const d = C.data, badge = $("#btnCoord");
  if (!d) { buttonLabel(badge, "route", t("Koordination")); badge.classList.remove("busy"); return; }
  const active = d.missions.filter(m => ACTIVE.includes(m.state)).length;
  buttonLabel(badge, "route", d.enabled && active ? t("Koordination · {n}", { n: active }) : t("Koordination"));
  badge.classList.toggle("busy", d.enabled);
  $("#coordDot").style.background = d.enabled ? "var(--ok)" : "var(--line)";
}
function render() {
  const box = $("#coordBox"); if (!box) return;
  const d = C.data;
  renderBadge();
  if (!d) { box.innerHTML = `<p class="msg">${esc(C.err || t("lädt …"))}</p>`; return; }
  const connected = d.device_state === "verbunden";
  box.innerHTML = `
    <label class="tog"><input type="checkbox" id="coordOn" ${d.enabled ? "checked" : ""} ${connected || d.enabled ? "" : "disabled"}>
      <strong>${t("Koordinationsmodus")}</strong></label>
    <p class="note" style="margin:0 0 6px">${d.enabled
      ? esc(t("An: der eigene Knoten funkt selbstständig an die Knoten mit Einsatz (Zuweisung, Kurs, Ankunft, Antworten auf ? ?R ?Z ?P HALT GO X)."))
      : connected ? esc(t("Aus: es wird nichts gesendet. Einschalten erlaubt dem Server, Knoten mit Einsatz selbstständig anzufunken."))
        : esc(t("Braucht das verbundene Gerät (oben unter „Gerät (USB)“ verbinden)."))}</p>
    <div class="jobstart">
      <button class="btn on" data-act="new" ${d.enabled ? "" : `title="${t("Einsätze können auch bei ausgeschaltetem Modus angelegt werden; gesendet wird erst, wenn er an ist.")}"`}>${symbolSVG("target")} ${t("Einsatz")}</button>
      <button class="btn small quiet" data-act="settings" aria-expanded="${!!C.settings}">${symbolSVG("settings")} ${t("Einstellungen")}</button>
      <button class="btn small" data-act="targets" aria-expanded="${C.targets}">${t("Ziele")} (${Object.keys(d.targets).length})</button>
      <button class="btn small" data-act="areas" aria-expanded="${C.areas}">${t("Gebiete")} (${(d.areas || []).length + (d.places || []).length})</button>
      <button class="btn small" data-act="osm" title="${esc(t("Straßen und Wege der Umgebung von OpenStreetMap laden (Overpass-API); danach führt der Server über Straßen statt Luftlinie."))}">${t("Straßennetz laden …")}</button>
    </div>
    <p class="note" style="margin:0 0 6px">${esc(osmLine(d.osm))}</p>
    ${C.settings ? settingsHTML(d) : ""}
    ${C.form ? formHTML(d) : ""}
    <div class="msg" role="alert">${esc(C.err)}</div>
    <div class="joblist">${d.missions.map(cardHTML).join("") || `<p class="note" style="margin:0">${t("Noch keine Einsätze.")}</p>`}</div>
    ${C.targets ? targetsHTML(d) : ""}
    ${C.areas ? areasHTML(d) : ""}`;
  $("#coordOn").addEventListener("change", async e => {
    try { await postJSON("api/coord/mode", { on: e.target.checked }); C.api.toast(e.target.checked ? t("Koordinationsmodus an") : t("Koordinationsmodus aus")); }
    catch (err) { C.err = err.message; }
    pollSoon();
  });
  box.querySelectorAll("[data-act]").forEach(b => b.addEventListener("click", () => action(b.dataset.act, b.dataset.node)));
  if (C.settings) bindSettings(box, d);
  if (C.form) bindForm(box, d);
  if (C.targets) bindTargets(box);
  if (C.areas) box.querySelectorAll("[data-ar]").forEach(b => b.addEventListener("click", () => areaAction(b.dataset.ar, b.dataset.id)));
}

// One line about the road graph: loaded (with its size and date) or missing.
function osmLine(o) {
  if (!o) return "";
  if (o.error) return t("Straßennetz {name}: {error}", { name: o.name, error: o.error });
  if (!o.bbox) return t("Kein Straßennetz geladen: Wegführung als Luftlinie (Richtung und Entfernung).");
  const when = o.downloaded ? new Date(o.downloaded).toLocaleDateString(locale) : "";
  const size = o.edges ? t("{n} Kanten", { n: o.edges }) : t("{n} Wege", { n: o.n_ways || 0 });
  return t("Straßennetz {name}: {size}, Stand {date}. Wegführung über Straßen.", { name: o.name, size, date: when });
}

function cardHTML(m) {
  const stop = m.path[m.index] || m.path[m.path.length - 1];
  const last = m.messages.length ? m.messages[m.messages.length - 1] : null;
  const active = ACTIVE.includes(m.state);
  const stops = m.path.filter(w => w.kind === "stop");
  const where = m.path.length > 1 ? t("Halt {n} von {total}: {name}", { n: stops.indexOf(stop) + 1 + (stop.kind === "via" ? 1 : 0), total: stops.length, name: stop.name }) : stop.name;
  return `<div class="job ${C.sel === m.node ? "sel" : ""}">
    <div class="hd">${stateChip(m.state)}<span class="nm" title="${esc(m.node)}"><button class="lnk" data-act="focus" data-node="${esc(m.node)}">${esc(nodeName(m.node))}</button></span></div>
    <div class="mission-target">${esc(where)}</div>
    ${metricsHTML(m)}
    ${m.legs && ACTIVE.includes(m.state) ? `<div class="det"><span class="port">R:</span> ${esc(m.legs)}</div>` : ""}
    ${last ? `<div class="meta">${clock(last.time)} „${esc(last.text)}“ · <span class="st ${statusClass(last.status)}">${esc(statusText(last.status))}</span></div>` : ""}
    <div class="acts">
      ${active ? `<button class="btn small" data-act="status" data-node="${esc(m.node)}">${t("Status senden")}</button>
        <button class="btn small" data-act="route" data-node="${esc(m.node)}">${t("Route senden")}</button>
        ${m.index < m.path.length - 1 ? `<button class="btn small" data-act="next" data-node="${esc(m.node)}">${t("Nächster Halt")}</button>` : ""}
        <button class="btn small" data-act="edit" data-node="${esc(m.node)}">${t("Pfad bearbeiten")}</button>
        <button class="btn small" data-act="end" data-node="${esc(m.node)}">${t("Beenden")}</button>`
      : `<button class="btn small" data-act="again" data-node="${esc(m.node)}">${t("Neuer Einsatz")}</button>
         <button class="btn small" data-act="remove" data-node="${esc(m.node)}">${t("Entfernen")}</button>`}
      <button class="btn small" data-act="detail" data-node="${esc(m.node)}">${t("Details")}</button>
    </div></div>`;
}

async function action(act, node) {
  C.err = "";
  try {
    if (act === "new") { openForm(null); return; }
    if (act === "settings") { C.settings = C.settings ? null : { ...C.data.settings, channel: String(C.data.settings.channel) }; render(); return; }
    if (act === "targets") { C.targets = !C.targets; C.api.store.set("coord.targets", C.targets); render(); return; }
    if (act === "areas") { C.areas = !C.areas; C.api.store.set("coord.areas", C.areas); render(); return; }
    if (act === "osm") { openTaskForm("osm"); return; }
    if (act === "focus") { C.api.focusNode(node); return; }
    if (act === "detail") { showDetail(node); return; }
    if (act === "again" || act === "edit") {
      const m = C.data.missions.find(x => x.node === node);
      openForm(node, m ? m.path : null, act === "edit"); return;
    }
    if (act === "end") {
      const m = C.data.missions.find(x => x.node === node);
      const notify = C.data.enabled && C.data.settings.end_message
        ? confirm(t("Einsatz von {name} beenden und den Knoten benachrichtigen? (Abbrechen = nicht beenden)", { name: nodeName(node) }))
        : confirm(t("Einsatz von {name} beenden?", { name: nodeName(node) }));
      if (!notify) return;
      await postJSON(`api/coord/missions/${node}/end`, { notify: true });
    } else if (act === "remove") {
      await postJSON(`api/coord/missions/${node}/remove`, {});
      if (C.sel === node) { C.sel = null; C.api.updateInspector(); }
    } else {
      await postJSON(`api/coord/missions/${node}/${act}`, {});
      if (act === "status" || act === "route") C.api.toast(t("Gesendet an {name}", { name: nodeName(node) }));
    }
    C.api.refreshLayer("coord");
  } catch (e) { C.err = e.message; render(); }
  pollSoon();
}

// ---------------------------------------------------------------- settings
function settingsHTML(d) {
  return `<div class="jobform" data-settings><div class="hd">${t("Einstellungen")}</div>
    ${inputsHTML(d.declarations, C.settings, "coord")}
    <div class="row2"><button class="btn small" data-s-cancel>${t("Abbrechen")}</button><button class="btn small on" data-s-save>${t("Speichern")}</button></div></div>`;
}
function bindSettings(box, d) {
  bindInputs(box.querySelector(".jobform[data-settings]"), d.declarations, C.settings, () => { });
  box.querySelector("[data-s-cancel]").addEventListener("click", () => { C.settings = null; render(); });
  box.querySelector("[data-s-save]").addEventListener("click", async () => {
    try { await postJSON("api/coord/settings", C.settings); C.settings = null; C.api.toast(t("Einstellungen gespeichert")); }
    catch (e) { C.err = e.message; }
    pollSoon();
  });
}

// ---------------------------------------------------------------- mission form
// edit: change the path of the running mission of `node` instead of assigning a new one.
function openForm(node, path = null, edit = false) {
  const d = C.data || { settings: {} };
  C.form = {
    node: node || "", profile: d.settings.profile || "foot", lang: d.settings.lang || "de", msg: "", edit,
    path: path ? path.map(w => ({ ...w, arrive_by: w.arrive_by ? clock(w.arrive_by) : "", hold_until: w.hold_until ? clock(w.hold_until) : "" })) : [],
  };
  render();
  const first = $("#coordBox [data-f=node]"); if (first && !node) first.focus();
}
function nodeOptions(current) {
  const rows = (C.api.nodes() || []).filter(n => !n.own)
    .sort((a, b) => (b.favorite - a.favorite) || ((b.last || 0) - (a.last || 0)));
  return rows.map(n => `<option value="${esc(n.id)}">${n.favorite ? "★ " : ""}${esc(n.short || n.id.slice(-4))} · ${esc(n.long || n.id)}</option>`).join("")
    + (current && !rows.some(n => n.id === current) ? `<option value="${esc(current)}">${esc(current)}</option>` : "");
}
function formHTML(d) {
  const f = C.form;
  const rows = f.path.map((w, i) => `<div class="wp" data-i="${i}">
      <span class="tag">${i + 1}</span>
      <input type="text" data-f="name" data-i="${i}" value="${esc(w.name)}" maxlength="24" placeholder="${t("Name")}" aria-label="${t("Name")}" style="width:7em">
      <select data-f="kind" data-i="${i}" aria-label="${t("Art")}"><option value="stop" ${w.kind !== "via" ? "selected" : ""}>${t("Halt")}</option><option value="via" ${w.kind === "via" ? "selected" : ""}>${t("Durchgang")}</option></select>
      <input type="number" data-f="radius_m" data-i="${i}" value="${w.radius_m || ""}" min="5" max="500" step="5" placeholder="${d.settings.arrive_radius_m}" title="${t("Radius [m]")}" aria-label="${t("Radius [m]")}" style="width:4.5em">
      <input type="text" data-f="arrive_by" data-i="${i}" value="${esc(w.arrive_by || "")}" placeholder="${t("bis")}" title="${t("Ankunft bis (12:55 oder +15)")}" aria-label="${t("Ankunft bis")}" style="width:4.5em" ${w.kind === "via" ? "disabled" : ""}>
      <input type="text" data-f="hold_until" data-i="${i}" value="${esc(w.hold_until || "")}" placeholder="${t("warten")}" title="${t("Warten bis (13:05 oder +30)")}" aria-label="${t("Warten bis")}" style="width:4.5em" ${w.kind === "via" ? "disabled" : ""}>
      <button class="btn small" data-wp="up" data-i="${i}" title="${t("nach oben")}" ${i === 0 ? "disabled" : ""}>↑</button>
      <button class="btn small" data-wp="move" data-i="${i}" title="${t("Verschieben: Klick in die Karte")}">⌖</button>
      <button class="btn small" data-wp="del" data-i="${i}" title="${t("Entfernen")}">✕</button></div>`).join("");
  const targets = Object.keys(d.targets || {}).map(n => `<option value="t:${esc(n)}">${esc(n)}</option>`).join("");
  const sites = (C.api.sites() || []).map(s => `<option value="s:${esc(s.name)}">${esc(s.name)} (${t("Standort")})</option>`).join("");
  const templates = Object.keys(d.paths || {}).map(n => `<option value="p:${esc(n)}">${esc(t("Vorlage laden: {name}", { name: n }))}</option>`).join("");
  return `<div class="jobform" data-mission><div class="hd">${f.edit ? t("Pfad bearbeiten") : t("Einsatz")}</div>
    <label>${t("Knoten")}<select data-f="node" ${f.edit ? "disabled" : ""}>${nodeOptions(f.node)}</select></label>
    <div class="hd2" style="font-size:11px;color:var(--ink2)">${t("Pfad: Wegpunkte in Reihenfolge; der letzte ist das Ziel. Zeiten als 12:55 oder +15 (Minuten).")}</div>
    <div class="wplist">${rows || `<p class="note" style="margin:0">${t("Noch kein Wegpunkt.")}</p>`}</div>
    <div class="row2">
      <select data-f="add"><option value="">${t("Wegpunkt hinzufügen …")}</option><option value="map">${t("Klick in die Karte")}</option>${targets}${sites}${templates}</select>
      <button class="btn small" data-form="template" ${f.path.length ? "" : "disabled"}>${t("Als Vorlage speichern …")}</button></div>
    ${f.edit ? "" : `<div class="row2">
      <label>${t("Fortbewegung")}<select data-f="profile"><option value="foot">${t("zu Fuß")}</option><option value="bike">${t("Fahrrad")}</option><option value="car">${t("Auto")}</option></select></label>
      <label>${t("Sprache der Funksprüche")}<select data-f="lang"><option value="de">Deutsch</option><option value="en">English</option></select></label></div>`}
    <div class="msg" role="alert">${esc(f.msg)}</div>
    <div class="row2"><button class="btn" data-form="cancel">${t("Abbrechen")}</button><button class="btn on" data-form="start">${f.edit ? t("Pfad speichern") : t("Zuweisen")}</button></div></div>`;
}
function readForm(box) {
  const f = C.form;
  f.node = box.querySelector("[data-f=node]").value;
  if (!f.edit) {
    f.profile = box.querySelector("[data-f=profile]").value;
    f.lang = box.querySelector("[data-f=lang]").value;
  }
  box.querySelectorAll(".wp").forEach(row => {
    const w = f.path[+row.dataset.i];
    row.querySelectorAll("[data-f]").forEach(inp => { w[inp.dataset.f] = inp.type === "number" ? (inp.value === "" ? null : +inp.value) : inp.value.trim(); });
  });
}
function bindForm(box, d) {
  const f = C.form, form = box.querySelector(".jobform[data-mission]");
  form.querySelector("[data-f=node]").value = f.node;
  if (!f.edit) {
    form.querySelector("[data-f=profile]").value = f.profile;
    form.querySelector("[data-f=lang]").value = f.lang;
  }
  form.querySelectorAll(".wp [data-f=kind]").forEach(s => s.addEventListener("change", () => { readForm(form); render(); }));
  form.querySelector("[data-form=template]").addEventListener("click", async () => {
    readForm(form);
    const name = prompt(t("Name der Vorlage (1–24 Zeichen, keine Leerzeichen)"), "");
    if (!name) return;
    try { await postJSON("api/coord/paths/save", { name: name.trim(), path: f.path }); C.api.toast(t("Vorlage {name} gespeichert", { name: name.trim() })); }
    catch (e) { f.msg = e.message; render(); }
    pollSoon();
  });
  form.querySelector("[data-f=add]").addEventListener("change", e => {
    readForm(form);
    const v = e.target.value; e.target.value = "";
    if (v.startsWith("p:")) {
      f.path = (d.paths[v.slice(2)] || []).map(w => ({ ...w, arrive_by: "", hold_until: "" })); render();
    } else if (v === "map") {
      C.api.pickOnMap(t("Position des Wegpunkts"), (lat, lon) => {
        f.path.push({ name: `P${f.path.length + 1}`, lat, lon, kind: "stop", radius_m: null, arrive_by: "", hold_until: "" });
        render();
      });
    } else if (v.startsWith("t:")) {
      const tg = d.targets[v.slice(2)];
      f.path.push({ name: v.slice(2), lat: tg.lat, lon: tg.lon, kind: "stop", radius_m: tg.radius_m, arrive_by: "", hold_until: "" }); render();
    } else if (v.startsWith("s:")) {
      const s = C.api.sites().find(x => x.name === v.slice(2));
      if (s) { f.path.push({ name: s.name, lat: s.lat, lon: s.lon, kind: "stop", radius_m: null, arrive_by: "", hold_until: "" }); render(); }
    }
  });
  form.querySelectorAll("[data-wp]").forEach(b => b.addEventListener("click", () => {
    readForm(form);
    const i = +b.dataset.i;
    if (b.dataset.wp === "del") f.path.splice(i, 1);
    else if (b.dataset.wp === "up") [f.path[i - 1], f.path[i]] = [f.path[i], f.path[i - 1]];
    else if (b.dataset.wp === "move") {
      C.api.pickOnMap(t("Neue Position für {name}", { name: f.path[i].name }), (lat, lon) => { f.path[i].lat = lat; f.path[i].lon = lon; render(); });
      return;
    }
    render();
  }));
  form.querySelector("[data-form=cancel]").addEventListener("click", () => { C.form = null; C.api.tempMarker(null); render(); });
  form.querySelector("[data-form=start]").addEventListener("click", async () => {
    readForm(form);
    try {
      if (f.edit) {
        await postJSON(`api/coord/missions/${f.node}/path`, { path: f.path });
        C.form = null;
        C.api.toast(t("Pfad für {name} geändert", { name: nodeName(f.node) }));
      } else {
        const m = await postJSON("api/coord/missions", { node: f.node, path: f.path, profile: f.profile, lang: f.lang });
        C.form = null; C.sel = m.node;
        C.api.toast(C.data.enabled ? t("Einsatz für {name} zugewiesen: „{text}“", { name: nodeName(m.node), text: m.messages.length ? m.messages[m.messages.length - 1].text : "" })
          : t("Einsatz für {name} angelegt; der Modus ist aus, es wurde nichts gesendet.", { name: nodeName(m.node) }));
      }
      C.api.refreshLayer("coord");
    } catch (e) { f.msg = e.message; render(); }
    pollSoon();
  });
}

// ---------------------------------------------------------------- targets editor
function targetsHTML(d) {
  const ed = C.editTarget;
  const rows = Object.entries(d.targets).map(([name, tg]) => ed && !ed.isNew && ed.orig === name ? targetFormHTML(ed)
    : `<div class="site"><div class="txt"><span class="nm">${esc(name)}</span><span class="sub">${esc(tg.note || "")}${tg.radius_m ? ` · ${tg.radius_m} m` : ""}</span></div>
      <button class="btn small" data-tg="edit" data-name="${esc(name)}" title="${t("Bearbeiten")}">✎</button>
      <button class="btn small" data-tg="move" data-name="${esc(name)}" title="${t("Verschieben: Klick in die Karte")}">⌖</button>
      <button class="btn small" data-tg="del" data-name="${esc(name)}" title="${t("Löschen")}">✕</button></div>`).join("");
  const templates = Object.entries(d.paths || {}).map(([name, p]) => `<div class="site"><div class="txt"><span class="nm">${esc(name)}</span>
      <span class="sub">${esc(p.map(w => w.name).join(" › "))}</span></div>
      <button class="btn small" data-tg="deltpl" data-name="${esc(name)}" title="${t("Löschen")}">✕</button></div>`).join("");
  return `<div class="sitemgr"><div class="hd2">${t("Ziele")}</div>
    <p class="note" style="margin:0">${t("Benannte Orte, die sich als Wegpunkte wiederverwenden lassen. Eigene Standorte gehen auch direkt.")}</p>
    <div class="sitelist">${rows || `<p class="note" style="margin:0">${t("Noch keine Ziele.")}</p>`}</div>
    ${ed && ed.isNew ? targetFormHTML(ed) : ""}
    ${ed ? "" : `<button class="btn small" data-tg="add" style="align-self:flex-start">＋ ${t("Neues Ziel")}</button>`}
    <div class="hd2">${t("Vorlagen")}</div>
    <p class="note" style="margin:0">${t("Gespeicherte Pfade ohne Zeiten; im Einsatzformular unter „Wegpunkt hinzufügen“ zu laden.")}</p>
    <div class="sitelist">${templates || `<p class="note" style="margin:0">${t("Noch keine Vorlagen.")}</p>`}</div></div>`;
}
function targetFormHTML(ed) {
  return `<div class="siteform">
    <label>${t("Name")}<input type="text" data-tf="name" value="${esc(ed.name)}" maxlength="24" placeholder="${t("z. B. ALPHA (kurz, steht in jedem Funkspruch)")}"></label>
    <label>${t("Notiz")}<input type="text" data-tf="note" value="${esc(ed.note)}" maxlength="100"></label>
    <div class="row2"><label>${t("Radius [m] (leer = Standard)")}<input type="number" data-tf="radius_m" value="${ed.radius_m ?? ""}" min="5" max="500" step="5"></label>
      <div class="note pos">${fmt(ed.lat, 5)}, ${fmt(ed.lon, 5)}</div></div>
    <div class="row2"><button class="btn small" data-tg="cancel">${t("Abbrechen")}</button><button class="btn small on" data-tg="save">${t("Speichern")}</button></div></div>`;
}
function bindTargets(box) {
  box.querySelectorAll("[data-tg]").forEach(b => b.addEventListener("click", () => targetAction(b.dataset.tg, b.dataset.name)));
}
async function targetAction(act, name) {
  const d = C.data;
  C.err = "";
  try {
    if (act === "add") {
      C.api.pickOnMap(t("Position des neuen Ziels"), (lat, lon) => {
        C.editTarget = { isNew: true, name: "", note: "", radius_m: null, lat, lon }; C.api.tempMarker(lat, lon); render();
        const inp = $("#coordBox [data-tf=name]"); if (inp) inp.focus();
      });
      return;
    }
    if (act === "edit") { const tg = d.targets[name]; C.editTarget = { isNew: false, orig: name, name, note: tg.note || "", radius_m: tg.radius_m, lat: tg.lat, lon: tg.lon }; render(); return; }
    if (act === "cancel") { C.editTarget = null; C.api.tempMarker(null); render(); return; }
    if (act === "move") {
      C.api.pickOnMap(t("Neue Position für {name}", { name }), async (lat, lon) => {
        try { await postJSON("api/coord/targets/update", { name, lat, lon }); C.api.refreshLayer("coord"); } catch (e) { C.err = e.message; }
        pollSoon();
      });
      return;
    }
    if (act === "del") {
      if (!confirm(t("Ziel {name} löschen?", { name }))) return;
      await postJSON("api/coord/targets/delete", { name });
    }
    if (act === "deltpl") {
      if (!confirm(t("Vorlage {name} löschen?", { name }))) return;
      await postJSON("api/coord/paths/delete", { name });
    }
    if (act === "save") {
      const ed = C.editTarget, box = $("#coordBox");
      box.querySelectorAll("[data-tf]").forEach(inp => { ed[inp.dataset.tf] = inp.type === "number" ? (inp.value === "" ? null : +inp.value) : inp.value.trim(); });
      if (ed.isNew) await postJSON("api/coord/targets/add", { name: ed.name, lat: ed.lat, lon: ed.lon, note: ed.note, radius_m: ed.radius_m });
      else {
        if (ed.name !== ed.orig) {  // rename = add the new, delete the old
          await postJSON("api/coord/targets/add", { name: ed.name, lat: ed.lat, lon: ed.lon, note: ed.note, radius_m: ed.radius_m });
          await postJSON("api/coord/targets/delete", { name: ed.orig });
        } else await postJSON("api/coord/targets/update", { name: ed.name, note: ed.note, radius_m: ed.radius_m });
      }
      C.editTarget = null; C.api.tempMarker(null);
    }
    C.api.refreshLayer("coord");
  } catch (e) { C.err = e.message; render(); return; }
  pollSoon();
}

// ---------------------------------------------------------------- areas and places editor
function areasHTML(d) {
  const ea = C.editArea, ep = C.editPlace;
  const areas = (d.areas || []).map(a => ea && !ea.isNew && ea.id === a.id ? areaFormHTML(ea)
    : `<div class="site"><div class="txt"><span class="nm">${a.kind === "nogo" ? "⛔ " : "ℹ "}${esc(a.name)}</span>
      <span class="sub">${esc(a.kind === "nogo" ? t("Sperrgebiet") : t("Hinweisgebiet"))} · ${t("{n} Eckpunkte", { n: a.polygon.length })}${a.text ? " · " + esc(a.text) : ""}</span></div>
      <button class="btn small" data-ar="edit" data-id="${esc(a.id)}" title="${t("Bearbeiten")}">✎</button>
      <button class="btn small" data-ar="redraw" data-id="${esc(a.id)}" title="${t("Neu zeichnen: Klicks in die Karte, dann Fertig")}">⌖</button>
      <button class="btn small" data-ar="del" data-id="${esc(a.id)}" title="${t("Löschen")}">✕</button></div>`).join("");
  const places = (d.places || []).map(p => ep && !ep.isNew && ep.id === p.id ? placeFormHTML(ep)
    : `<div class="site"><div class="txt"><span class="nm">📍 ${esc(p.name)}</span>
      <span class="sub">${p.radius_m} m${p.text ? " · " + esc(p.text) : ""}</span></div>
      <button class="btn small" data-ar="editp" data-id="${esc(p.id)}" title="${t("Bearbeiten")}">✎</button>
      <button class="btn small" data-ar="movep" data-id="${esc(p.id)}" title="${t("Verschieben: Klick in die Karte")}">⌖</button>
      <button class="btn small" data-ar="delp" data-id="${esc(p.id)}" title="${t("Löschen")}">✕</button></div>`).join("");
  return `<div class="sitemgr"><div class="hd2">${t("Gebiete und Orte")}</div>
    <p class="note" style="margin:0">${t("Sperrgebiete meidet die Wegführung; der Knoten wird gewarnt, wenn er hinein läuft oder eines voraus liegt. Hinweisgebiete und Orte schicken ihren Text, wenn der Knoten hineinkommt.")}</p>
    <div class="sitelist">${areas || `<p class="note" style="margin:0">${t("Noch keine Gebiete.")}</p>`}</div>
    ${ea && ea.isNew ? areaFormHTML(ea) : ""}
    <div class="sitelist">${places || `<p class="note" style="margin:0">${t("Noch keine Orte.")}</p>`}</div>
    ${ep && ep.isNew ? placeFormHTML(ep) : ""}
    ${ea || ep ? "" : `<div class="acts"><button class="btn small" data-ar="add">＋ ${t("Gebiet zeichnen")}</button><button class="btn small" data-ar="addp">＋ ${t("Neuer Ort")}</button></div>`}</div>`;
}
function areaFormHTML(ea) {
  return `<div class="siteform">
    <label>${t("Name")}<input type="text" data-af="name" value="${esc(ea.name)}" maxlength="24" placeholder="${t("z. B. KASERNE (steht im Funkspruch)")}"></label>
    <label>${t("Art")}<select data-af="kind"><option value="nogo" ${ea.kind === "nogo" ? "selected" : ""}>${t("Sperrgebiet")}</option><option value="notice" ${ea.kind === "notice" ? "selected" : ""}>${t("Hinweisgebiet")}</option></select></label>
    <label>${t("Text (bei Hinweisgebieten der Funkspruch)")}<input type="text" data-af="text" value="${esc(ea.text)}" maxlength="120"></label>
    <div class="row2"><label>${t("Puffer [m]")}<input type="number" data-af="buffer_m" value="${ea.buffer_m ?? 0}" min="0" max="500" step="5"></label>
      <div class="note pos">${t("{n} Eckpunkte", { n: ea.polygon.length })}</div></div>
    <div class="row2"><button class="btn small" data-ar="cancel">${t("Abbrechen")}</button><button class="btn small on" data-ar="save">${t("Speichern")}</button></div></div>`;
}
function placeFormHTML(ep) {
  return `<div class="siteform">
    <label>${t("Name")}<input type="text" data-pf="name" value="${esc(ep.name)}" maxlength="24"></label>
    <label>${t("Text (der Funkspruch in der Nähe)")}<input type="text" data-pf="text" value="${esc(ep.text)}" maxlength="120"></label>
    <div class="row2"><label>${t("Radius [m]")}<input type="number" data-pf="radius_m" value="${ep.radius_m ?? 100}" min="10" max="2000" step="10"></label>
      <div class="note pos">${fmt(ep.lat, 5)}, ${fmt(ep.lon, 5)}</div></div>
    <div class="row2"><button class="btn small" data-ar="cancelp">${t("Abbrechen")}</button><button class="btn small on" data-ar="savep">${t("Speichern")}</button></div></div>`;
}
async function areaAction(act, id) {
  const d = C.data, box = $("#coordBox");
  C.err = "";
  const read = (sel, obj) => box.querySelectorAll(sel).forEach(inp => {
    const k = inp.dataset.af || inp.dataset.pf;
    obj[k] = inp.type === "number" ? (inp.value === "" ? null : +inp.value) : inp.value.trim();
  });
  try {
    if (act === "add") {
      C.api.pickOnMap(t("Eckpunkte des Gebiets"), points => {
        if (points.length < 3) { C.err = t("Ein Gebiet braucht mindestens drei Eckpunkte"); render(); return; }
        C.editArea = { isNew: true, name: "", kind: "nogo", text: "", buffer_m: 0, polygon: points }; render();
        const inp = box.querySelector("[data-af=name]"); if (inp) inp.focus();
      }, { multi: true });
      return;
    }
    if (act === "edit") { const a = d.areas.find(x => x.id === id); C.editArea = { ...a, isNew: false }; render(); return; }
    if (act === "cancel") { C.editArea = null; render(); return; }
    if (act === "redraw") {
      C.api.pickOnMap(t("Neue Eckpunkte des Gebiets"), async points => {
        if (points.length < 3) { C.err = t("Ein Gebiet braucht mindestens drei Eckpunkte"); render(); return; }
        try { await postJSON("api/coord/areas/update", { id, polygon: points }); C.api.refreshLayer("coord"); } catch (e) { C.err = e.message; }
        pollSoon();
      }, { multi: true });
      return;
    }
    if (act === "save") {
      const ea = C.editArea; read("[data-af]", ea);
      await postJSON(ea.isNew ? "api/coord/areas/add" : "api/coord/areas/update",
        { id: ea.id, name: ea.name, kind: ea.kind, text: ea.text, buffer_m: ea.buffer_m, polygon: ea.polygon });
      C.editArea = null;
    }
    if (act === "del") {
      if (!confirm(t("Gebiet {name} löschen?", { name: (d.areas.find(x => x.id === id) || {}).name || id }))) return;
      await postJSON("api/coord/areas/delete", { id });
    }
    if (act === "addp") {
      C.api.pickOnMap(t("Position des Orts"), (lat, lon) => {
        C.editPlace = { isNew: true, name: "", text: "", radius_m: 100, lat, lon }; C.api.tempMarker(lat, lon); render();
        const inp = box.querySelector("[data-pf=name]"); if (inp) inp.focus();
      });
      return;
    }
    if (act === "editp") { const p = d.places.find(x => x.id === id); C.editPlace = { ...p, isNew: false }; render(); return; }
    if (act === "cancelp") { C.editPlace = null; C.api.tempMarker(null); render(); return; }
    if (act === "movep") {
      C.api.pickOnMap(t("Neue Position des Orts"), async (lat, lon) => {
        try { await postJSON("api/coord/places/update", { id, lat, lon }); C.api.refreshLayer("coord"); } catch (e) { C.err = e.message; }
        pollSoon();
      });
      return;
    }
    if (act === "savep") {
      const ep = C.editPlace; read("[data-pf]", ep);
      await postJSON(ep.isNew ? "api/coord/places/add" : "api/coord/places/update",
        { id: ep.id, name: ep.name, text: ep.text, radius_m: ep.radius_m, lat: ep.lat, lon: ep.lon });
      C.editPlace = null; C.api.tempMarker(null);
    }
    if (act === "delp") {
      if (!confirm(t("Ort {name} löschen?", { name: (d.places.find(x => x.id === id) || {}).name || id }))) return;
      await postJSON("api/coord/places/delete", { id });
    }
    C.api.refreshLayer("coord");
  } catch (e) { C.err = e.message; render(); return; }
  pollSoon();
}

// ---------------------------------------------------------------- inspector tab
export function showDetail(node) { C.sel = node; render(); C.api.openInspector("coord"); renderMissionDetail(); }
export async function renderMissionDetail() {
  const m = selectedMission(); if (!m) return;
  let d;
  try { d = await getJSON(`api/coord/missions/${m.node}`); } catch (e) { $("#t_coord").innerHTML = `<div class="sec"><p class="msg">${esc(e.message)}</p></div>`; return; }
  const k = d.metrics || {};
  const kpi = (label, value, unit = "") => `<div class="kpi"><div class="k">${label}</div><div class="v">${value}<span class="u"> ${unit}</span></div></div>`;
  const rows = d.path.map((w, i) => `<tr class="${i === d.index && ACTIVE.includes(d.state) ? "ens" : ""}"><td>${i + 1}</td><td>${esc(w.name)}</td><td>${w.kind === "via" ? t("Durchgang") : t("Halt")}</td>
    <td class="n">${w.radius_m} m</td><td class="n">${w.arrive_by ? clock(w.arrive_by) : "–"}</td><td class="n">${w.hold_until ? clock(w.hold_until) : "–"}</td>
    <td>${i < d.index ? "✓" : i === d.index ? (d.state === "erreicht" ? "✓" : "→") : ""}</td></tr>`).join("");
  const msgs = d.messages.slice().reverse().map(x => `<div class="pkt"><span class="tm">${clock(x.time)}</span> <span class="port">${esc(t(x.kind))}</span> „${esc(x.text)}“ · <span class="st ${statusClass(x.status)}">${esc(statusText(x.status))}</span></div>`).join("");
  const events = (d.events || []).slice().reverse().map(e => {
    const extra = Object.entries(e).filter(([key]) => !["time", "node", "kind"].includes(key)).map(([key, v]) => `${key}=${typeof v === "number" ? fmt(v, 2) : esc(String(v))}`).join(" ");
    return `${clock(e.time)}  ${e.kind}  ${extra}`;
  }).join("\n");
  const log = d.events || [];
  $("#t_coord").innerHTML = `<div class="sec">
      <div class="verdict">${stateChip(d.state)}<strong>${esc(nodeName(d.node))}</strong>${d.held ? `<span class="chip wait">HALT</span>` : ""}</div>
      <div class="kpis">
        ${kpi(t("Distanz"), dist(k.dist_m), k.compass || "")}
        ${kpi(t("Ankunft"), eta(k.eta_s), k.margin_min !== undefined ? (k.margin_min >= 0 ? `+${k.margin_min}` : k.margin_min) + " min" : "")}
        ${kpi(t("Tempo"), fmt(k.speed_kmh, 1), `km/h ${k.speed_source ? "(" + t(k.speed_source) + ")" : ""}`)}
        ${kpi(t("Zurückgelegt"), dist(k.travelled_m), "")}
        ${kpi(t("Unterwegs seit"), fmt((k.elapsed_s || 0) / 60, 0), "min")}
        ${kpi(t("Position"), ago(k.position_age_s), k.precision_bits ? t("{n} Bit", { n: k.precision_bits }) : "")}
        ${k.mode === "route" ? kpi(t("Auf der Straße"), dist(k.route_left_m), k.off_route_m > 30 ? t("{d} daneben", { d: dist(k.off_route_m) }) : "") : ""}
        ${kpi("SNR", k.snr === null || k.snr === undefined ? "–" : fmt(k.snr, 1), "dB")}
        ${kpi(t("Hops"), k.hops ?? "–", "")}
        ${kpi(t("Funksprüche"), d.messages.length, t("{n} zugestellt", { n: d.messages.filter(x => x.status === "zugestellt").length }))}
      </div>
      ${k.stale ? `<p class="msg" role="alert">${t("Die letzte Position ist älter als eingestellt; der Knoten ist vielleicht außer Reichweite.")}</p>` : ""}
      <div class="acts" style="margin-top:8px">${ACTIVE.includes(d.state)
        ? `<button class="btn small" data-act="status">${t("Status senden")}</button><button class="btn small" data-act="route">${t("Route senden")}</button><button class="btn small" data-act="end">${t("Beenden")}</button>` : ""}</div>
    </div>
    <div class="sec"><h2>${t("Pfad")}</h2><div class="wrap"><table>
      <tr><th>#</th><th>${t("Name")}</th><th>${t("Art")}</th><th>${t("Radius")}</th><th>${t("bis")}</th><th>${t("warten")}</th><th></th></tr>${rows}</table></div>
      <p class="note">${t("Fortbewegung: {profile} · Funksprüche auf {lang}", { profile: t({ foot: "zu Fuß", bike: "Fahrrad", car: "Auto" }[d.profile] || d.profile), lang: d.lang === "en" ? "English" : "Deutsch" })}</p></div>
    ${d.legs ? `<div class="sec"><h2>${t("Wegbeschreibung")}</h2><p class="pkt" style="font-size:12px">R: ${esc(d.legs)}</p>
      <p class="note">${t("Abschnitte ab der aktuellen Position: Himmelsrichtung oder Abbiegen (L/R/U), Meter, Straßenname; Z ist der Halt.")}</p></div>` : ""}
    <div class="sec"><h2>${t("Funksprüche")}</h2>${msgs || `<p class="note">${t("Noch keine.")}</p>`}</div>
    <div class="sec"><h2>${t("Ereignisse")}</h2><pre class="log">${events || "—"}</pre>
      <p class="note">${t("{n} Ereignisse; alle stehen in data/coord/events-<Datum>.jsonl.", { n: log.length })}</p></div>`;
  $("#t_coord").querySelectorAll("[data-act]").forEach(b => b.addEventListener("click", () => action(b.dataset.act, d.node)));
}
