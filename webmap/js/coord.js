// Coordination mode: the rail section (mode switch, settings, mission form with the path
// builder, mission cards, targets editor) and the inspector tab "Einsatz". The server decides
// and sends (coord/missions.py); the page polls /api/coord and shows what happened.
import { bindInputs, initialValues, inputsHTML } from "./forms.js";
import { locale, t } from "./i18n.js";
import { openTaskForm } from "./tasks.js";
import { $, esc, fmt, getJSON, postJSON } from "./util.js";
import { setBadge, showSection } from "./workspace.js";
import { iconButton, symbolSVG } from "./icons.js";
import { csv, download, gpx } from "./export.js";
import { G, openMenu, registerActions } from "./actions.js";

// Mission states are German codes: t("zugewiesen") t("unterwegs") t("wartet") t("erreicht")
// t("abgebrochen") t("beendet"); message kinds: t("assign") t("status") t("route") t("target")
// t("path") t("help") t("halt") t("resume") t("aborted") t("ended") t("offcourse") t("late")
// t("early") t("confirm") t("reached") t("next") t("changed") t("legend") t("nogo") t("notice") t("place");
// speed sources: t("gemessen") t("Standard"); OSM suggestion reasons: t("militärisch") t("kein Zugang")
const ACTIVE = ["zugewiesen", "unterwegs", "wartet"];
const STATE_CLASS = { zugewiesen: "wait", unterwegs: "run", wartet: "wait", erreicht: "ok", abgebrochen: "bad", beendet: "off" };
const C = {
  api: null, data: null, timer: null, prev: {}, prevTargets: null, err: "",
  form: null,          // { node, path: [rows], profile, lang, msg }
  settings: null,      // values being edited, null = closed
  editArea: null,      // { isNew, id, name, kind, text, buffer_m, polygon }
  editPlace: null,     // { isNew, id, name, lat, lon, radius_m, text }
  editTarget: null,    // { isNew, orig, name, lat, lon, note }
  sel: null,           // node of the mission shown in the inspector
  arch: null,          // or the id of an archived mission shown there
  archive: null,       // archive panel open: the list of missions (null = closed)
};

// api: { store, toast(msg, opts), openInspector(tab), updateInspector(), pickOnMap(label, cb), draft(path, sel),
//   mapAdd(cb or null: map clicks and clicks on targets, sites, places go to cb(lat, lon, feature)),
//        tempMarker(lat, lon), focusNode(id), refreshLayer(id), nodes() -> rows of the node layer,
//        sites() -> [{name, lat, lon}] }
export function initCoord(api) {
  C.api = api;
  registerMapActions();
  poll();
}

// ---------------------------------------------------------------- actions on the map
// The editors' actions for the objects on the map; editing opens the editor in the rail.
function registerMapActions() {
  const edit = run => ({ label: t("Bearbeiten …"), short: t("Bearbeiten"), icon: "edit", group: G.main, run });
  const del = run => ({ label: t("Löschen …"), icon: "trash", danger: true, run });
  const showMission = node => ({ label: t("Einsatz anzeigen"), short: t("Einsatz"), icon: "target", group: G.work, run: () => showDetail(node) });
  registerActions("node", ref => {
    if (ref.own || !C.data) return [];
    const m = C.data.missions.find(x => x.node === ref.id && ACTIVE.includes(x.state));
    return [m ? showMission(ref.id)
      : { label: t("Ziel zuweisen …"), short: t("Ziel …"), icon: "target", group: G.work, run: () => assignTo(ref.id) }];
  });
  for (const type of ["target", "site", "place"]) registerActions(type, (ref, f) => C.form && C.form.step === 2
    ? [{ label: t("Als Wegpunkt hinzufügen"), short: t("Wegpunkt"), icon: "plus", group: G.main, run: () => addFromFeature(f) }] : []);
  registerActions("target", ref => [
    edit(() => inEditor("targets", () => targetAction("edit", ref.id))),
    { label: t("Verschieben"), icon: "move", group: G.work, run: () => targetAction("move", ref.id) },
    del(() => targetAction("del", ref.id)),
  ]);
  registerActions("area", ref => [
    edit(() => inEditor("areas", () => areaAction("edit", ref.id))),
    { label: t("Neu zeichnen"), icon: "area", group: G.work, run: () => areaAction("redraw", ref.id) },
    del(() => areaAction("del", ref.id)),
  ]);
  registerActions("place", ref => [
    edit(() => inEditor("areas", () => areaAction("editp", ref.id))),
    { label: t("Verschieben"), icon: "move", group: G.work, run: () => areaAction("movep", ref.id) },
    del(() => areaAction("delp", ref.id)),
  ]);
  registerActions("suggestion", ref => [
    { label: t("Als Sperrgebiet übernehmen"), short: t("Übernehmen"), icon: "check", group: G.main, run: () => areaAction("acceptg", ref.id) },
    { label: t("Verwerfen"), icon: "close", group: G.work, run: () => areaAction("dismissg", ref.id) },
  ]);
  const mission = ref => [showMission(ref.node)];
  registerActions("waypoint", mission);
  registerActions("mission", mission);
  registerActions("map", ({ lat, lon }) => [
    { label: t("Ziel hier anlegen …"), icon: "target", group: G.main, run: () => inEditor("targets", () => {
      C.editTarget = { isNew: true, name: "", note: "", radius_m: null, lat, lon }; C.api.tempMarker(lat, lon); render();
      $("#targetsBox [data-tf=name]")?.focus();
    }) },
    { label: t("Ort hier anlegen …"), icon: "place", group: G.main, run: () => inEditor("areas", () => {
      C.editPlace = { isNew: true, name: "", text: "", radius_m: 100, lat, lon }; C.api.tempMarker(lat, lon); render();
      $("#areasBox [data-pf=name]")?.focus();
    }) },
  ]);
}
// Open the targets or the areas editor (view "Orte"), then act there.
function inEditor(which, fn) {
  if (!C.data) { C.api.toast(t("Die Koordination lädt noch.")); return; }
  showSection(which);
  renderPlaces();
  fn();
  $(`#${which}Box .siteform`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}
export const selectedMission = () => (C.sel && C.data && C.data.missions.find(m => m.node === C.sel)) || null;
export const hasMissionDetail = () => !!(C.arch || selectedMission());
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
      if (prev && prev.created !== m.created && m.origin === "radio") radioMission(m);
      else if (!prev && C.data && m.origin === "radio") radioMission(m);
      else if (prev && prev.state !== m.state) transition(m, prev.state);
    }
    C.prev = Object.fromEntries(d.missions.map(m => [m.node, { state: m.state, created: m.created }]));
    if (C.prevTargets) {  // markers the field set by radio (+D)
      for (const [name, tg] of Object.entries(d.targets)) {
        if (tg.by && !C.prevTargets.has(name)) C.api.toast(t("{node} hat per Funk die Markierung {name} gesetzt", { node: nodeName(tg.by), name }));
      }
    }
    C.prevTargets = new Set(Object.keys(d.targets));
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

function radioMission(m) {
  const stop = m.path[m.path.length - 1].name;
  C.api.toast(t("{name} hat sich per Funk {stop} als Ziel geholt", { name: nodeName(m.node), stop }), { action: [t("Details"), () => showDetail(m.node)] });
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
function statusText(s) {  // delivery states are German codes, see messages.js
  const m = /^nicht zugestellt \((.+)\)$/.exec(s || "");
  if (m) return t("nicht zugestellt ({reason})", { reason: m[1] });
  const f = /^nicht gesendet: (.+)$/.exec(s || "");
  if (f) return t("nicht gesendet: {error}", { error: f[1] });
  return t(s || "gesendet");  // t("gesendet") t("zugestellt") t("im Netz")
}

// ---------------------------------------------------------------- status
// One status per mission, with its reason, by severity: bad (no signal, aborted), warn (late,
// off the route, halted, no position yet), ok (assigned, on its way, waiting), done (arrived),
// off (ended). The same levels colour the card, the detail and the activity bar.
function missionStatus(m) {
  const k = m.metrics || {};
  if (m.state === "abgebrochen") return { level: "bad", text: t("Abgebrochen") };
  if (m.state === "beendet") return { level: "off", text: t("Beendet") };
  if (m.state === "erreicht") return { level: "done", text: t("Erreicht") };
  if (k.stale) return { level: "bad", text: t("Kein Signal"), pill: ago(k.position_age_s) };
  if (k.dist_m == null) return { level: "warn", text: t("Keine Position"), pill: k.requested_s != null ? t("angefragt") : "" };
  if (m.held) return { level: "warn", text: t("Halt") };
  if (k.mode === "route" && k.off_route_m > 30) return { level: "warn", text: t("Abseits der Route"), pill: dist(k.off_route_m) };
  if (k.margin_min < 0) return { level: "warn", text: t("Verspätet"), pill: `+${-k.margin_min} min` };
  if (m.state === "wartet") {
    const w = m.path[m.index];
    return { level: "ok", text: t("Wartet"), pill: w && w.hold_until ? clock(w.hold_until) : "" };
  }
  return { level: "ok", text: m.state === "zugewiesen" ? t("Zugewiesen") : t("Unterwegs") };
}
const RANK = { bad: 0, warn: 1, ok: 2, done: 3, off: 4 };
const STATUS_ICON = { bad: "antenna", warn: "route", ok: "route", done: "check", off: "check" };
function statusHTML(s, big = false) {
  return `<div class="mstatus ${s.level}${big ? " big" : ""}">${symbolSVG(STATUS_ICON[s.level])}<b>${esc(s.text)}</b>${s.pill ? `<span class="pill">${esc(s.pill)}</span>` : ""}</div>`;
}
function nodeParts(id) {
  const n = (C.api.nodes() || []).find(x => x.id === id);
  return { short: n ? n.short || id.slice(-4) : id.slice(-4), long: n ? n.long || id : id };
}
// Arrival at the current stop as a clock time, and the deviation from its deadline (+ late).
function arrival(m) {
  const k = m.metrics || {};
  if (k.eta_s == null) return { time: "–", dev: null };
  return { time: clock(Date.now() / 1000 + k.eta_s), dev: k.margin_min === undefined ? null : -k.margin_min };
}
const devHTML = dev => dev === null || dev === undefined ? ""
  : `<span class="dev ${dev > 0 ? "late" : "early"}">${dev > 0 ? "+" : "−"}${Math.abs(dev)}</span>`;
const val = (icon, html, title, cls = "") => `<span class="mv ${cls}" title="${esc(title)}">${symbolSVG(icon)}${html}</span>`;

// ---------------------------------------------------------------- rail section
const editing = () => !!(C.form || C.settings || C.editTarget || C.editArea || C.editPlace);
// On the activity bar: a dot while the mode is on (green, yellow while it waits for the device,
// red when a mission has a problem).
function renderBadge() {
  const d = C.data;
  if (!d) { setBadge("coord", null); return; }
  const active = d.missions.filter(m => ACTIVE.includes(m.state));
  const worst = active.map(missionStatus).sort((a, b) => RANK[a.level] - RANK[b.level])[0];
  const dot = !d.enabled ? null : d.device_state !== "verbunden" ? "warn" : worst && worst.level === "bad" ? "bad" : worst && worst.level === "warn" ? "warn" : "ok";
  setBadge("coord", dot ? { dot } : null, d.enabled ? t("Koordination · {n}", { n: active.length }) : t("Koordination"));
}
// Targets, areas and the road graph are places and map data (view "Orte"), drawn from the
// coordination's data.
function renderPlaces() {
  const d = C.data, tb = $("#targetsBox"), ab = $("#areasBox"), rb = $("#roadsBox");
  if (!tb || !ab) return;
  if (!d) { tb.innerHTML = ab.innerHTML = `<p class="note" style="margin:0">${esc(C.err || t("lädt …"))}</p>`; return; }
  const err = C.err ? `<div class="msg" role="alert">${esc(C.err)}</div>` : "";
  tb.innerHTML = targetsHTML(d) + err;
  bindTargets(tb);
  ab.innerHTML = areasHTML(d) + err;
  ab.querySelectorAll("[data-ar]").forEach(b => b.addEventListener("click", () => areaAction(b.dataset.ar, b.dataset.id)));
  if (rb) {
    rb.innerHTML = `<p class="note" style="margin:0">${esc(osmLine(d.osm))}</p>
      <button class="btn small" data-osm title="${esc(t("Straßen und Wege der Umgebung von OpenStreetMap laden (Overpass-API); danach führt der Server über Straßen statt Luftlinie."))}">${symbolSVG("map")}${t("Straßennetz laden …")}</button>`;
    rb.querySelector("[data-osm]").addEventListener("click", () => openTaskForm("osm"));
  }
}
// Which of the view's sections are open (they are drawn anew on every update).
const secOpen = key => C.api.store.get("coord.sec." + key, key === "missions");
function section(key, title, count, body) {
  return `<details class="sec" data-sec="${key}" ${secOpen(key) ? "open" : ""}><summary><h2>${esc(title)}</h2>${count === null ? "" : `<span class="count">${count}</span>`}</summary>${body}</details>`;
}
function render() {
  const box = $("#coordBox"); if (!box) return;
  const d = C.data;
  renderBadge();
  renderPlaces();
  C.api.draft(C.form && C.form.step >= 2 ? C.form.path : null, C.form ? C.form.sel : null);
  C.api.mapAdd(C.form && C.form.step === 2 ? addFromMap : null);
  if (!d) { box.innerHTML = `<div class="sec"><p class="msg">${esc(C.err || t("lädt …"))}</p></div>`; return; }
  const connected = d.device_state === "verbunden";
  const modeTip = d.enabled
    ? t("An: der eigene Knoten funkt selbstständig an die Knoten mit Einsatz (Zuweisung, Kurs, Ankunft, Antworten auf ? ?R ?Z ?P HALT GO X).")
    : connected ? t("Aus: es wird nichts gesendet. Einschalten erlaubt dem Server, Knoten mit Einsatz selbstständig anzufunken.")
      : t("Braucht das verbundene Gerät (oben unter „Gerät (USB)“ verbinden).");
  const waiting = d.enabled && !connected;
  const missions = d.missions.slice().sort((a, b) => RANK[missionStatus(a).level] - RANK[missionStatus(b).level]);
  const list = C.form ? formHTML(d)
    : `<div class="mlist">${missions.map(cardHTML).join("") || `<p class="note" style="margin:0">${t("Noch keine Einsätze.")}</p>`}</div>
       <button class="btn on" data-act="new">${symbolSVG("plus")}${t("Neuer Einsatz")}</button>`;
  box.innerHTML = `
    <div class="coordbar" title="${esc(modeTip)}">
      <label class="switch"><input type="checkbox" id="coordOn" role="switch" ${d.enabled ? "checked" : ""} ${connected || d.enabled ? "" : "disabled"}><span class="sl"></span>${t("Modus")}</label>
      <span class="cstate ${waiting ? "warn" : d.enabled ? "on" : ""}">${esc(waiting ? t("wartet auf das Gerät") : d.enabled ? t("an") : t("aus"))}</span>
    </div>
    <div class="msg" role="alert" style="padding:0 14px">${esc(C.err)}</div>
    ${section("missions", t("Einsätze"), d.missions.length, `<div class="stack">${list}</div>`)}
    ${section("archive", t("Archiv"), null, `<div class="stack">${C.archive ? archiveHTML() : `<p class="note" style="margin:0">${t("lädt …")}</p>`}</div>`)}
    ${section("coordset", t("Einstellungen"), null, settingsHTML(d))}`;
  $("#coordOn").addEventListener("change", async e => {
    try { await postJSON("api/coord/mode", { on: e.target.checked }); C.api.toast(e.target.checked ? t("Koordinationsmodus an") : t("Koordinationsmodus aus")); }
    catch (err) { C.err = err.message; }
    pollSoon();
  });
  box.querySelectorAll("details[data-sec]").forEach(sec => sec.addEventListener("toggle", () => {
    C.api.store.set("coord.sec." + sec.dataset.sec, sec.open);
    if (sec.dataset.sec === "archive") { if (sec.open && !C.archive) loadArchive(); if (!sec.open) C.archive = null; }
  }));
  if (secOpen("archive") && !C.archive) loadArchive();
  box.querySelectorAll("[data-act]").forEach(b => b.addEventListener("click", () => action(b.dataset.act, b.dataset.node)));
  box.querySelectorAll("[data-sel]").forEach(b => b.addEventListener("click", () => showDetail(b.dataset.sel)));
  bindSettings(box, d);
  if (C.form) bindForm(box, d);
  box.querySelectorAll("[data-arch]").forEach(b => b.addEventListener("click", () => archiveAction(b.dataset.arch, b.dataset.id)));
}

// ---------------------------------------------------------------- archive
// Every mission, current or archived (replaced by a new one or removed from the list), with
// its whole event log and trail from the server's daily logs; GPX and CSV built here.
const stamp = ts => new Date(ts * 1000).toLocaleString(locale, { dateStyle: "short", timeStyle: "short" });
function archiveHTML() {
  const rows = C.archive.map(a => `<div class="site"><div class="txt">
      <span class="nm">${esc(stamp(a.created))} · ${esc(nodeName(a.node))}</span>
      <span class="sub">${esc(a.path.join(" › "))} · ${esc(t(a.state))} · ${dist(a.travelled_m)}${a.current ? " · " + esc(t("in der Liste")) : ""}</span></div>
    <button class="btn small" data-arch="detail" data-id="${esc(a.id)}">${t("Details")}</button>
    <button class="btn small" data-arch="gpx" data-id="${esc(a.id)}" title="${t("Spur und Wegpunkte als GPX")}">GPX</button>
    <button class="btn small" data-arch="csv" data-id="${esc(a.id)}" title="${t("Ereignisse als CSV")}">CSV</button></div>`).join("");
  return `<div class="sitemgr">
    <p class="note" style="margin:0">${t("Alle Einsätze mit vollständigem Ereignisprotokoll und Spur; ersetzte und entfernte bleiben hier.")}</p>
    <div class="sitelist">${rows || `<p class="note" style="margin:0">${t("Noch keine Einsätze.")}</p>`}</div></div>`;
}
async function loadArchive() {
  try { C.archive = (await getJSON("api/coord/archive")).missions; } catch (e) { C.err = e.message; C.archive = []; }
  render();
}
async function archiveAction(act, id) {
  if (act === "detail") { showArchived(id); return; }
  try { exportMission(await getJSON(`api/coord/archive/${id}`), act); } catch (e) { C.err = e.message; render(); }
}
function exportMission(d, kind) {
  const when = new Date(d.created * 1000).toISOString().slice(0, 16).replace(/[-:T]/g, "");
  const base = `einsatz-${d.node.replace("!", "")}-${when}`;
  if (kind === "gpx") {
    const name = `${nodeName(d.node)} ${d.path.map(w => w.name).join(" > ")}`;
    download(`${base}.gpx`, gpx(name, d.path, d.trail), "application/gpx+xml");
  } else {
    const rows = d.events.map(e => {
      const { time, kind: k, lat, lon, snr, what, text, node: _node, ...rest } = e;
      return { time, kind: k, lat, lon, snr, what, text, details: Object.keys(rest).length ? rest : null };
    });
    download(`${base}.csv`, csv(["time", "kind", "lat", "lon", "snr", "what", "text", "details"], rows), "text/csv");
  }
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

// A mission in the list: who, the status, then the next stop, distance, arrival and signal.
// A click shows its details on the right.
function cardHTML(m) {
  const s = missionStatus(m), k = m.metrics || {}, n = nodeParts(m.node);
  const stop = m.path[m.index] || m.path[m.path.length - 1], a = arrival(m);
  const live = ACTIVE.includes(m.state);
  return `<button class="mcard ${s.level} ${C.sel === m.node ? "sel" : ""}" data-sel="${esc(m.node)}" title="${esc(m.node)}">
    <span class="mc-hd"><span class="sn">${esc(n.short)}</span><b>${esc(n.long)}</b><span class="mst ${s.level}"><i></i>${esc(s.text)}</span></span>
    ${live ? `<span class="mc-ln">${val("target", esc(stop.name), t("Nächster Halt"))}${val("ruler", dist(k.dist_m), t("Distanz"))}
      ${val("clock", a.time + devHTML(a.dev), t("Ankunft"))}<span class="sp"></span>${val("antenna", ago(k.position_age_s), t("Letzte Position"), k.stale ? "bad" : "")}</span>` : ""}
  </button>`;
}

async function action(act, node) {
  C.err = "";
  try {
    if (act === "new") { openForm(null); return; }
    if (act === "focus") { C.api.focusNode(node); return; }
    if (act === "detail") { showDetail(node); return; }
    if (act === "again" || act === "edit") {
      const m = C.data.missions.find(x => x.node === node);
      openForm(node, m ? m.path : null, act === "edit"); return;
    }
    if (act === "end") {
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
// Always shown in their section; a change keeps them out of the updates until saved or reset.
function settingsHTML(d) {
  const vals = C.settings || { ...d.settings, channel: String(d.settings.channel) };
  C.settingsView = vals;
  return `<div class="jobform" data-settings>${inputsHTML(d.declarations, vals, "coord")}
    ${C.settings ? `<div class="row2"><button class="btn small" data-s-cancel>${t("Verwerfen")}</button><button class="btn small on" data-s-save>${t("Speichern")}</button></div>` : ""}</div>`;
}
function bindSettings(box, d) {
  const form = box.querySelector(".jobform[data-settings]");
  bindInputs(form, d.declarations, C.settingsView, () => { if (!C.settings) { C.settings = C.settingsView; render(); } });
  form.querySelector("[data-s-cancel]")?.addEventListener("click", () => { C.settings = null; render(); });
  form.querySelector("[data-s-save]")?.addEventListener("click", async () => {
    try { await postJSON("api/coord/settings", C.settings); C.settings = null; C.api.toast(t("Einstellungen gespeichert")); }
    catch (e) { C.err = e.message; }
    pollSoon();
  });
}

// ---------------------------------------------------------------- mission form: three steps
// 1 the node, 2 the waypoints (from the map, targets, sites or a template; times and radius on
// the selected one), 3 travel profile and language, then assign. Editing a running mission's
// path starts at step 2 and saves there.
function openForm(node, path = null, edit = false) {
  const d = C.data || { settings: {} };
  C.form = {
    step: node ? 2 : 1, node: node || "", profile: d.settings.profile || "foot", lang: d.settings.lang || "de", msg: "", edit, sel: null,
    path: path ? path.map(w => ({ ...w, arrive_by: w.arrive_by ? clock(w.arrive_by) : "", hold_until: w.hold_until ? clock(w.hold_until) : "" })) : [],
  };
  showSection("missions");
  render();
  $("#coordBox [data-f=node]")?.focus();
}
function nodeOptions(current) {
  const rows = (C.api.nodes() || []).filter(n => !n.own)
    .sort((a, b) => (b.favorite - a.favorite) || ((b.last || 0) - (a.last || 0)));
  return `<option value="">${t("Knoten wählen …")}</option>` + rows.map(n => `<option value="${esc(n.id)}">${n.favorite ? "★ " : ""}${esc(n.short || n.id.slice(-4))} · ${esc(n.long || n.id)}</option>`).join("")
    + (current && !rows.some(n => n.id === current) ? `<option value="${esc(current)}">${esc(current)}</option>` : "");
}
function stepsHTML(f) {
  const steps = f.edit ? [[2, t("Wegpunkte")]] : [[1, t("Knoten")], [2, t("Wegpunkte")], [3, t("Senden")]];
  return `<div class="wsteps">${steps.map(([n, label]) => `<span class="${n === f.step ? "on" : n < f.step ? "done" : ""}"><i>${n < f.step ? symbolSVG("check") : n}</i>${esc(label)}</span>`).join("")}</div>`;
}
function wpHTML(w, i, f, d) {
  const sel = f.sel === i, stop = w.kind !== "via";
  const times = (w.arrive_by ? val("clock", esc(w.arrive_by), t("Ankunft bis")) : "") + (w.hold_until ? val("hourglass", esc(w.hold_until), t("Warten bis")) : "");
  return `<div class="wprow ${sel ? "sel" : ""}" data-i="${i}">
    <span class="wpn ${stop ? "" : "via"}">${stop ? f.path.slice(0, i + 1).filter(x => x.kind !== "via").length : ""}</span>
    <input type="text" data-f="name" data-i="${i}" value="${esc(w.name)}" maxlength="24" aria-label="${t("Name")}">
    <span class="wpt">${times}</span>
    ${iconButton("edit", t("Art, Radius und Zeiten"), `data-wp="sel" data-i="${i}"`)}
    ${iconButton("move", t("Verschieben: Klick in die Karte"), `data-wp="move" data-i="${i}"`)}
    <button class="btn small icon" data-wp="up" data-i="${i}" aria-label="${t("nach oben")}" title="${t("nach oben")}" ${i === 0 ? "disabled" : ""}>↑</button>
    ${iconButton("close", t("Entfernen"), `data-wp="del" data-i="${i}"`)}</div>
    ${sel ? `<div class="wpedit">
      <label>${t("Art")}<select data-f="kind" data-i="${i}"><option value="stop" ${stop ? "selected" : ""}>${t("Halt")}</option><option value="via" ${stop ? "" : "selected"}>${t("Durchgang")}</option></select></label>
      <label>${t("Radius [m]")}<input type="number" data-f="radius_m" data-i="${i}" value="${w.radius_m || ""}" min="5" max="500" step="5" placeholder="${d.settings.arrive_radius_m}"></label>
      <label title="${t("Ankunft bis (12:55 oder +15)")}">${t("Ankunft bis")}<input type="text" data-f="arrive_by" data-i="${i}" value="${esc(w.arrive_by || "")}" placeholder="12:55 / +15" ${stop ? "" : "disabled"}></label>
      <label title="${t("Warten bis (13:05 oder +30)")}">${t("Warten bis")}<input type="text" data-f="hold_until" data-i="${i}" value="${esc(w.hold_until || "")}" placeholder="13:05 / +30" ${stop ? "" : "disabled"}></label></div>` : ""}`;
}
function formHTML(d) {
  const f = C.form;
  let body = "", next = "";
  if (f.step === 1) {
    body = `<label>${t("Knoten")}<select data-f="node">${nodeOptions(f.node)}</select></label>`;
    next = `<button class="btn on" data-form="next" ${f.node ? "" : "disabled"}>${t("Weiter")}</button>`;
  } else if (f.step === 2) {
    const targets = Object.keys(d.targets || {}).map(n => `<option value="t:${esc(n)}">${esc(n)}</option>`).join("");
    const sites = (C.api.sites() || []).map(s => `<option value="s:${esc(s.name)}">${esc(s.name)}</option>`).join("");
    const places = (d.places || []).map(p => `<option value="o:${esc(p.id)}">${esc(p.name)}</option>`).join("");
    const templates = Object.keys(d.paths || {}).map(n => `<option value="p:${esc(n)}">${esc(n)}</option>`).join("");
    // the map takes waypoints by click while this step is open (see render)
    body = `<div class="wplist">${f.path.map((w, i) => wpHTML(w, i, f, d)).join("") || `<p class="note wphint">${symbolSVG("pin")}${t("Klick in die Karte setzt einen Wegpunkt.")}</p>`}</div>
      <select data-f="add" aria-label="${t("Wegpunkt hinzufügen …")}"><option value="">${t("Hinzufügen …")}</option>
        ${targets ? `<optgroup label="${t("Ziele")}">${targets}</optgroup>` : ""}${sites ? `<optgroup label="${t("Eigene Standorte")}">${sites}</optgroup>` : ""}
        ${places ? `<optgroup label="${t("Orte")}">${places}</optgroup>` : ""}
        ${templates ? `<optgroup label="${t("Vorlagen")}">${templates}</optgroup>` : ""}</select>
      ${f.path.length ? `<button class="lnk" data-form="template">${t("Als Vorlage speichern …")}</button>` : ""}`;
    next = f.edit ? `<button class="btn on" data-form="start" ${f.path.length ? "" : "disabled"}>${t("Pfad speichern")}</button>`
      : `<button class="btn on" data-form="next" ${f.path.length ? "" : "disabled"}>${t("Weiter")}</button>`;
  } else {
    const stops = f.path.filter(w => w.kind !== "via");
    body = `<div class="msum">${val("router", esc(nodeParts(f.node).long), t("Knoten"))}${val("target", esc(stops.map(w => w.name).join(" › ")), t("Halte"))}</div>
      <div class="row2">
      <label>${t("Fortbewegung")}<select data-f="profile"><option value="foot">${t("zu Fuß")}</option><option value="bike">${t("Fahrrad")}</option><option value="car">${t("Auto")}</option></select></label>
      <label>${t("Sprache der Funksprüche")}<select data-f="lang"><option value="de">Deutsch</option><option value="en">English</option></select></label></div>
      ${C.data.enabled ? "" : `<p class="note" style="margin:0">${t("Einsätze können auch bei ausgeschaltetem Modus angelegt werden; gesendet wird erst, wenn er an ist.")}</p>`}`;
    next = `<button class="btn on" data-form="start">${symbolSVG("antenna")}${t("Zuweisen")}</button>`;
  }
  const back = f.step > 1 && !(f.edit && f.step === 2) ? `<button class="btn" data-form="back">${t("Zurück")}</button>` : `<button class="btn" data-form="cancel">${t("Abbrechen")}</button>`;
  return `<div class="jobform" data-mission><div class="hd">${f.edit ? t("Pfad bearbeiten") : t("Neuer Einsatz")}</div>${stepsHTML(f)}
    ${body}<div class="msg" role="alert">${esc(f.msg)}</div><div class="row2">${back}${next}</div></div>`;
}
const wp = (name, lat, lon, radius_m = null) => ({ name: name.slice(0, 24), lat, lon, kind: "stop", radius_m, arrive_by: "", hold_until: "" });
// A waypoint from a map object or a list entry: a target keeps its name and arrival radius, a
// place and a site their name (a place's radius is where its notice is sent, not an arrival);
// anything else is a new numbered point at the clicked spot.
function wpFrom(feature, lat, lon) {
  const d = C.data || {}, p = (feature && feature.properties) || {}, ref = p._ref || {};
  const tg = ref.type === "target" && (d.targets || {})[ref.id];
  if (tg) return wp(ref.id, tg.lat, tg.lon, tg.radius_m);
  const pl = ref.type === "place" && (d.places || []).find(x => x.id === ref.id);
  if (pl) return wp(pl.name, pl.lat, pl.lon);
  if (ref.type === "site" && lat != null) return wp(p._title || ref.id, lat, lon);
  return wp(`P${C.form.path.length + 1}`, lat, lon);
}
function addFromFeature(feature) {
  const g = feature && feature.geometry, point = g && g.type === "Point";
  addFromMap(point ? g.coordinates[1] : null, point ? g.coordinates[0] : null, feature);
  showSection("missions");
}
function addFromMap(lat, lon, feature) {
  const form = $("#coordBox .jobform[data-mission]");
  if (form) readForm(form);
  C.form.path.push(wpFrom(feature, lat, lon));
  render();
}
function readForm(box) {
  const f = C.form;
  const node = box.querySelector("[data-f=node]"); if (node) f.node = node.value;
  const profile = box.querySelector("[data-f=profile]"); if (profile) f.profile = profile.value;
  const lang = box.querySelector("[data-f=lang]"); if (lang) f.lang = lang.value;
  box.querySelectorAll("[data-i][data-f]").forEach(inp => {
    const w = f.path[+inp.dataset.i];
    if (w) w[inp.dataset.f] = inp.type === "number" ? (inp.value === "" ? null : +inp.value) : inp.value.trim();
  });
}
function bindForm(box, d) {
  const f = C.form, form = box.querySelector(".jobform[data-mission]");
  const redraw = () => { readForm(form); render(); };
  if (form.querySelector("[data-f=node]")) form.querySelector("[data-f=node]").value = f.node;
  form.querySelector("[data-f=node]")?.addEventListener("change", redraw);
  if (form.querySelector("[data-f=profile]")) form.querySelector("[data-f=profile]").value = f.profile;
  if (form.querySelector("[data-f=lang]")) form.querySelector("[data-f=lang]").value = f.lang;
  form.querySelectorAll("[data-f=kind]").forEach(s => s.addEventListener("change", redraw));
  form.querySelector("[data-form=next]")?.addEventListener("click", () => { readForm(form); f.step++; f.sel = null; render(); });
  form.querySelector("[data-form=back]")?.addEventListener("click", () => { readForm(form); f.step--; render(); });
  form.querySelector("[data-form=template]")?.addEventListener("click", async () => {
    readForm(form);
    const name = prompt(t("Name der Vorlage (1–24 Zeichen, keine Leerzeichen)"), "");
    if (!name) return;
    try { await postJSON("api/coord/paths/save", { name: name.trim(), path: f.path }); C.api.toast(t("Vorlage {name} gespeichert", { name: name.trim() })); }
    catch (e) { f.msg = e.message; render(); }
    pollSoon();
  });
  form.querySelector("[data-f=add]")?.addEventListener("change", e => {
    readForm(form);
    const v = e.target.value; e.target.value = "";
    if (v.startsWith("p:")) f.path = (d.paths[v.slice(2)] || []).map(w => ({ ...w, arrive_by: "", hold_until: "" }));
    else if (v.startsWith("t:")) f.path.push(wpFrom({ properties: { _ref: { type: "target", id: v.slice(2) } } }));
    else if (v.startsWith("o:")) f.path.push(wpFrom({ properties: { _ref: { type: "place", id: v.slice(2) } } }));
    else if (v.startsWith("s:")) {
      const s = C.api.sites().find(x => x.name === v.slice(2));
      if (s) f.path.push(wp(s.name, s.lat, s.lon));
    }
    render();
  });
  form.querySelectorAll("[data-wp]").forEach(b => b.addEventListener("click", () => {
    readForm(form);
    const i = +b.dataset.i;
    if (b.dataset.wp === "del") { f.path.splice(i, 1); f.sel = null; }
    else if (b.dataset.wp === "up") { [f.path[i - 1], f.path[i]] = [f.path[i], f.path[i - 1]]; f.sel = null; }
    else if (b.dataset.wp === "sel") f.sel = f.sel === i ? null : i;
    else if (b.dataset.wp === "move") {
      C.api.pickOnMap(t("Neue Position für {name}", { name: f.path[i].name }), (lat, lon) => { f.path[i].lat = lat; f.path[i].lon = lon; render(); });
      return;
    }
    render();
  }));
  form.querySelector("[data-form=cancel]")?.addEventListener("click", () => { C.form = null; C.api.tempMarker(null); render(); });
  form.querySelector("[data-form=start]")?.addEventListener("click", async () => {
    readForm(form);
    try {
      if (f.edit) {
        await postJSON(`api/coord/missions/${f.node}/path`, { path: f.path });
        C.form = null;
        C.api.toast(t("Pfad für {name} geändert", { name: nodeName(f.node) }));
      } else {
        const m = await postJSON("api/coord/missions", { node: f.node, path: f.path, profile: f.profile, lang: f.lang });
        C.form = null;
        C.api.toast(C.data.enabled ? t("Einsatz für {name} zugewiesen: „{text}“", { name: nodeName(m.node), text: m.messages.length ? m.messages[m.messages.length - 1].text : "" })
          : t("Einsatz für {name} angelegt; der Modus ist aus, es wurde nichts gesendet.", { name: nodeName(m.node) }));
        showDetail(m.node);
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
    : `<div class="site"><div class="txt"><span class="nm">${esc(name)}</span><span class="sub">${esc(tg.note || "")}${tg.radius_m ? ` · ${tg.radius_m} m` : ""}${tg.by ? ` · ${esc(t("per Funk von {node}, {time}", { node: nodeName(tg.by), time: clock(tg.created) }))}` : ""}</span></div>
      ${iconButton("edit", t("{name} bearbeiten", { name }), `data-tg="edit" data-name="${esc(name)}"`)}
      ${iconButton("move", t("{name} verschieben", { name }), `data-tg="move" data-name="${esc(name)}"`)}
      ${iconButton("trash", t("{name} löschen", { name }), `data-tg="del" data-name="${esc(name)}"`, { danger: true })}</div>`).join("");
  const templates = Object.entries(d.paths || {}).map(([name, p]) => `<div class="site"><div class="txt"><span class="nm">${esc(name)}</span>
      <span class="sub">${esc(p.map(w => w.name).join(" › "))}</span></div>
      ${iconButton("trash", t("{name} löschen", { name }), `data-tg="deltpl" data-name="${esc(name)}"`, { danger: true })}</div>`).join("");
  return `<div class="sitemgr">
    <p class="note" style="margin:0">${t("Benannte Orte, die sich als Wegpunkte wiederverwenden lassen. Eigene Standorte gehen auch direkt.")}</p>
    <p class="note" style="margin:0">${esc(t("Ziele sind zugleich Markierungen für das Feld: „+D NAME Text“ setzt eines an der Position des Absenders, „?D NAME“ macht es zu dessen Einsatz, „?D“ das nächste (Einstellung „Markierungen per Funk“)."))}</p>
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
        const inp = $("#targetsBox [data-tf=name]"); if (inp) inp.focus();
      });
      return;
    }
    if (act === "edit") { const tg = d.targets[name]; C.editTarget = { isNew: false, orig: name, name, note: tg.note || "", radius_m: tg.radius_m, lat: tg.lat, lon: tg.lon, by: tg.by, created: tg.created }; render(); return; }
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
      const ed = C.editTarget, box = $("#targetsBox");
      box.querySelectorAll("[data-tf]").forEach(inp => { ed[inp.dataset.tf] = inp.type === "number" ? (inp.value === "" ? null : +inp.value) : inp.value.trim(); });
      if (ed.isNew) await postJSON("api/coord/targets/add", { name: ed.name, lat: ed.lat, lon: ed.lon, note: ed.note, radius_m: ed.radius_m });
      else {
        if (ed.name !== ed.orig) {  // rename = add the new, delete the old
          await postJSON("api/coord/targets/add", { name: ed.name, lat: ed.lat, lon: ed.lon, note: ed.note, radius_m: ed.radius_m, by: ed.by, created: ed.created });
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
      ${iconButton("edit", t("{name} bearbeiten", { name: a.name }), `data-ar="edit" data-id="${esc(a.id)}"`)}
      ${iconButton("area", t("Neu zeichnen: Klicks in die Karte, dann Fertig"), `data-ar="redraw" data-id="${esc(a.id)}"`)}
      ${iconButton("trash", t("{name} löschen", { name: a.name }), `data-ar="del" data-id="${esc(a.id)}"`, { danger: true })}</div>`).join("");
  const places = (d.places || []).map(p => ep && !ep.isNew && ep.id === p.id ? placeFormHTML(ep)
    : `<div class="site"><div class="txt"><span class="nm">📍 ${esc(p.name)}</span>
      <span class="sub">${p.radius_m} m${p.text ? " · " + esc(p.text) : ""}</span></div>
      ${iconButton("edit", t("{name} bearbeiten", { name: p.name }), `data-ar="editp" data-id="${esc(p.id)}"`)}
      ${iconButton("move", t("{name} verschieben", { name: p.name }), `data-ar="movep" data-id="${esc(p.id)}"`)}
      ${iconButton("trash", t("{name} löschen", { name: p.name }), `data-ar="delp" data-id="${esc(p.id)}"`, { danger: true })}</div>`).join("");
  return `<div class="sitemgr">
    <p class="note" style="margin:0">${t("Sperrgebiete meidet die Wegführung; der Knoten wird gewarnt, wenn er hinein läuft oder eines voraus liegt. Hinweisgebiete und Orte schicken ihren Text, wenn der Knoten hineinkommt.")}</p>
    <div class="sitelist">${areas || `<p class="note" style="margin:0">${t("Noch keine Gebiete.")}</p>`}</div>
    ${ea && ea.isNew ? areaFormHTML(ea) : ""}
    <div class="sitelist">${places || `<p class="note" style="margin:0">${t("Noch keine Orte.")}</p>`}</div>
    ${ep && ep.isNew ? placeFormHTML(ep) : ""}
    ${ea || ep ? "" : `<div class="acts"><button class="btn small" data-ar="add">＋ ${t("Gebiet zeichnen")}</button><button class="btn small" data-ar="addp">＋ ${t("Neuer Ort")}</button></div>`}
    ${suggestionsHTML(d.suggestions || [])}</div>`;
}
// Restricted areas found in OpenStreetMap (task "Sperrgebiete aus OSM suchen"): dashed on the
// map; each one is taken over or dismissed on its own.
function suggestionsHTML(list) {
  const rows = list.map(g => `<div class="site"><div class="txt"><span class="nm">${esc(g.name || t(g.reason))}</span>
      <span class="sub">${esc(t(g.reason))} · ${fmt(g.area_m2 / 1e4, 1)} ha</span></div>
      ${iconButton("pin", t("Auf der Karte zeigen"), `data-ar="showg" data-id="${esc(g.id)}"`)}
      ${iconButton("check", t("Als Sperrgebiet übernehmen"), `data-ar="acceptg" data-id="${esc(g.id)}"`)}
      ${iconButton("close", t("Verwerfen"), `data-ar="dismissg" data-id="${esc(g.id)}"`)}</div>`).join("");
  return `<div class="hd2">${t("Vorschläge aus OpenStreetMap")}</div>
    <p class="note" style="margin:0">${t("Militärische Flächen und Flächen ohne Zugang; gestrichelt auf der Karte. Erst übernommene meidet die Wegführung.")}</p>
    <div class="sitelist">${rows || `<p class="note" style="margin:0">${t("Keine offenen Vorschläge.")}</p>`}</div>
    <div class="acts"><button class="btn small" data-ar="searchg">${t("Sperrgebiete aus OSM suchen …")}</button></div>`;
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
  const d = C.data, box = $("#areasBox");
  C.err = "";
  const read = (sel, obj) => box.querySelectorAll(sel).forEach(inp => {
    const k = inp.dataset.af || inp.dataset.pf;
    obj[k] = inp.type === "number" ? (inp.value === "" ? null : +inp.value) : inp.value.trim();
  });
  try {
    if (act === "searchg") { openTaskForm("osm_areas"); return; }
    if (act === "showg") {
      const g = d.suggestions.find(x => x.id === id);
      if (g) C.api.tempMarker(g.lat, g.lon);
      return;
    }
    if (act === "acceptg" || act === "dismissg") {
      await postJSON(`api/coord/suggestions/${act === "acceptg" ? "accept" : "dismiss"}`, { id });
      C.api.tempMarker(null); C.api.refreshLayer("coord"); pollSoon(); return;
    }
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
// ---------------------------------------------------------------- details (right panel)
export function showDetail(node) { C.sel = node; C.arch = null; render(); C.api.openInspector("coord"); renderMissionDetail(); }
function showArchived(id) { C.arch = id; C.sel = null; render(); C.api.openInspector("coord"); renderMissionDetail(); }

// The path as a timeline: reached stops with their time and a check, the current one with the
// expected arrival and its deviation, later ones with their deadline; waiting with an hourglass.
function routeHTML(d) {
  const k = d.metrics || {}, ev = d.events || [], live = ACTIVE.includes(d.state);
  const passed = name => [...ev].reverse().find(e => ["reached", "via", "skipped"].includes(e.kind) && e.name === name);
  let n = 0;
  return `<ol class="tl">${d.path.map((w, i) => {
    const stop = w.kind !== "via";
    if (stop) n++;
    const state = i < d.index || (i === d.index && d.state === "erreicht") ? "done" : i === d.index && live ? "now" : "next";
    let time = "";
    if (state === "done") {
      const e = passed(w.name);
      time = e ? `<span class="tt" title="${esc(e.kind === "skipped" ? t("übersprungen") : t("erreicht"))}">${clock(e.time)}${symbolSVG(e.kind === "skipped" ? "close" : "check")}</span>` : "";
    } else if (state === "now" && k.eta_s != null) {
      const a = arrival(d);
      time = `<span class="tt" title="${esc(w.arrive_by ? t("geplant bis {time}", { time: clock(w.arrive_by) }) : t("Ankunft"))}">${devHTML(a.dev)}${symbolSVG("clock")}${a.time}</span>`;
    } else if (w.arrive_by) {
      time = `<span class="tt plan" title="${esc(t("Ankunft bis"))}">${symbolSVG("clock")}${clock(w.arrive_by)}</span>`;
    }
    const hold = stop && w.hold_until ? `<span class="th" title="${esc(t("Warten bis"))}">${symbolSVG("hourglass")}${clock(w.hold_until)}</span>` : "";
    return `<li class="${state} ${stop ? "" : "via"}"><i>${state === "done" ? symbolSVG("check") : stop ? n : ""}</i>
      <span class="tn"><b>${esc(w.name)}</b>${time}</span>${hold}</li>`;
  }).join("")}</ol>`;
}
// Radio texts as a conversation: ours with their delivery (✓ sent, ✓✓ delivered, ! failed),
// the node's commands from the event log.
function radioHTML(d) {
  const out = d.messages.map(x => ({ time: x.time, out: true, text: x.text, status: x.status }));
  const inc = (d.events || []).filter(e => e.kind === "command" && e.text).map(e => ({ time: e.time, out: false, text: e.text }));
  const items = [...out, ...inc].sort((a, b) => a.time - b.time);
  if (!items.length) return `<p class="note">${t("Noch keine.")}</p>`;
  const tick = s => s === "zugestellt" ? `<span class="tick ok" title="${esc(statusText(s))}">✓✓</span>`
    : /^nicht/.test(s || "") ? `<span class="tick bad" title="${esc(statusText(s))}">!</span>` : `<span class="tick" title="${esc(statusText(s))}">✓</span>`;
  return `<div class="thread">${items.map(x => `<div class="bub ${x.out ? "out" : "in"}"><span class="bt">${esc(x.text)}</span>
    <span class="bm">${clock(x.time)}${x.out ? tick(x.status) : ""}</span></div>`).join("")}</div>`;
}
function detailsHTML(d) {
  const k = d.metrics || {};
  const rows = [
    [t("Tempo"), k.speed_kmh != null ? `${fmt(k.speed_kmh, 1)} km/h${k.speed_source ? " (" + t(k.speed_source) + ")" : ""}` : "–"],
    [t("Zurückgelegt"), dist(k.travelled_m)], [t("Unterwegs seit"), `${fmt((k.elapsed_s || 0) / 60, 0)} min`],
    ["SNR", k.snr == null ? "–" : `${fmt(k.snr, 1)} dB`], [t("Hops"), k.hops ?? "–"],
    [t("Positionsgenauigkeit"), k.precision_bits ? t("{n} Bit", { n: k.precision_bits }) : "–"],
    [t("Fortbewegung"), t({ foot: "zu Fuß", bike: "Fahrrad", car: "Auto" }[d.profile] || d.profile)],
    [t("Sprache der Funksprüche"), d.lang === "en" ? "English" : "Deutsch"],
  ];
  if (k.mode === "route") rows.push([t("Auf der Straße"), dist(k.route_left_m)]);
  const events = (d.events || []).slice().reverse().map(e => {
    const extra = Object.entries(e).filter(([key]) => !["time", "node", "kind"].includes(key)).map(([key, v]) => `${key}=${typeof v === "number" ? fmt(v, 2) : esc(String(v))}`).join(" ");
    return `${clock(e.time)}  ${e.kind}  ${extra}`;
  }).join("\n");
  return `<dl class="kvl">${rows.map(([key, v]) => `<div><dt>${esc(key)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>
    ${d.legs ? `<div class="legs" title="${esc(t("Abschnitte ab der aktuellen Position: Himmelsrichtung oder Abbiegen (L/R/U), Meter, Straßenname; Z ist der Halt."))}">${symbolSVG("route")}<span>R: ${esc(d.legs)}</span></div>` : ""}
    <details class="log"><summary>${t("Protokoll")} <span class="count">${(d.events || []).length}</span></summary><pre class="log">${events || "—"}</pre></details>`;
}
export async function renderMissionDetail() {
  let url;
  if (C.arch) url = `api/coord/archive/${C.arch}`;
  else { const m = selectedMission(); if (!m) return; url = `api/coord/missions/${m.node}`; }
  let d;
  try { d = await getJSON(url); } catch (e) { $("#t_coord").innerHTML = `<div class="sec"><p class="msg">${esc(e.message)}</p></div>`; return; }
  const live = !C.arch && ACTIVE.includes(d.state), s = missionStatus(d), n = nodeParts(d.node), k = d.metrics || {};
  const stop = d.path[d.index] || d.path[d.path.length - 1], a = arrival(d);
  // the main action follows the situation: the route when late or off it, else the status
  const routeFirst = s.text === t("Verspätet") || s.text === t("Abseits der Route");
  const main = live ? (routeFirst ? ["route", "route", t("Route senden")] : ["status", "antenna", t("Status senden")]) : null;
  const second = live ? (routeFirst ? ["status", "antenna", t("Status senden")] : ["route", "route", t("Route senden")]) : null;
  const tab = C.dtab || "route";
  const radioCount = d.messages.length + (d.events || []).filter(e => e.kind === "command" && e.text).length;
  $("#t_coord").innerHTML = `<div class="sec mhead">
      <div class="mh1"><span class="sn">${esc(n.short)}</span><b>${esc(n.long)}</b>
        <span class="mv" title="${esc(t("zugewiesen um"))}">${symbolSVG("clock")}${clock(d.assigned_at || d.created)}</span>
        <button class="btn small quiet icon" data-more aria-label="${t("Weitere Aktionen")}" title="${t("Weitere Aktionen")}">${symbolSVG("more")}</button></div>
      ${statusHTML(s, true)}
      ${C.arch ? `<p class="note" style="margin:0">${esc(t("Aus dem Archiv, Stand {time}.", { time: stamp(d.archived_at || Date.now() / 1000) }))}</p>` : ""}
      ${main ? `<div class="macts"><button class="btn on" data-act="${main[0]}">${symbolSVG(main[1])}${esc(main[2])}</button>
        <button class="btn" data-act="${second[0]}">${symbolSVG(second[1])}${esc(second[2])}</button></div>` : ""}
      ${live ? `<div class="mkv">${val("target", esc(stop.name), t("Nächster Halt"))}${val("ruler", `${dist(k.dist_m)} <small>${esc(k.compass || "")}</small>`, t("Distanz"))}
        ${val("clock", a.time + devHTML(a.dev), t("Ankunft"))}${val("antenna", `${k.snr == null ? "–" : fmt(k.snr, 1) + " dB"} <small>${esc(ago(k.position_age_s))}</small>`, t("Signal und letzte Position"), k.stale ? "bad" : "")}</div>` : ""}
    </div>
    <div class="dtabs" role="tablist">${[["route", t("Route")], ["radio", t("Funk") + ` <span class="count" title="${esc(t("{n} Funknachrichten", { n: radioCount }))}">${radioCount}</span>`], ["more", t("Details")]].map(([id, label]) =>
      `<button class="dtab ${tab === id ? "on" : ""}" role="tab" aria-selected="${tab === id}" data-dtab="${id}">${label}</button>`).join("")}</div>
    <div class="sec">${tab === "route" ? routeHTML(d) : tab === "radio" ? radioHTML(d) : detailsHTML(d)}</div>`;
  const box = $("#t_coord");
  box.querySelectorAll("[data-act]").forEach(b => b.addEventListener("click", () => action(b.dataset.act, d.node)));
  box.querySelectorAll("[data-dtab]").forEach(b => b.addEventListener("click", () => { C.dtab = b.dataset.dtab; renderMissionDetail(); }));
  box.querySelector("[data-more]").addEventListener("click", ev => {
    const r = ev.currentTarget.getBoundingClientRect();
    const items = [];
    if (live) {
      if (d.index < d.path.length - 1) items.push({ label: t("Nächster Halt"), icon: "target", group: G.work, run: () => action("next", d.node) });
      items.push({ label: t("Pfad bearbeiten …"), icon: "edit", group: G.work, run: () => action("edit", d.node) });
    } else if (!C.arch) {
      items.push({ label: t("Neuer Einsatz …"), icon: "plus", group: G.main, run: () => action("again", d.node) });
      items.push({ label: t("Ins Archiv"), icon: "close", group: G.work, run: () => action("remove", d.node) });
    }
    items.push({ label: t("Als GPX"), icon: "route", group: G.copy, run: () => archiveAction("gpx", d.id) });
    items.push({ label: t("Als CSV"), icon: "list", group: G.copy, run: () => archiveAction("csv", d.id) });
    if (live) items.push({ label: t("Beenden …"), icon: "close", danger: true, group: G.del, run: () => action("end", d.node) });
    openMenu(r.left, r.bottom + 4, `${n.short} · ${n.long}`, items.map((x, i) => ({ ...x, i })));
  });
}
