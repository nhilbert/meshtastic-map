// Wiring: layer panel (settings forms from the server's declarations), the link layer (A/B
// endpoints, computed in the browser session), the inspector on the right.
import { Map2D } from "./map2d.js";
import { Map3D, LAYERS_3D } from "./map3d.js";
import { assignTo, initCoord, renderMissionDetail, selectedMission } from "./coord.js";
import { bindInputs, initialValues, inputsHTML } from "./forms.js";
import { initMessages, openConversation } from "./messages.js";
import { initNodeList, renderNodeList } from "./nodelist.js";
import { LANGS, lang, loadCatalogue, setLang, t, translateStatic } from "./i18n.js";
import { renderLink, renderWalk } from "./panels.js";
import { initSites, renderSites } from "./sites.js";
import { initTasks, renderDetailIfShown, selectedJob, showDetail } from "./tasks.js";
import { $, css, esc, fmt, getJSON, grade, legendHTML, postJSON } from "./util.js";

const status = (msg, bad) => { const s = $("#status"); s.textContent = msg; s.style.color = bad ? "var(--bad)" : ""; };
const store = {
  get(k, d) { try { const v = localStorage.getItem("mapapp." + k); return v === null ? d : JSON.parse(v); } catch (_) { return d; } },
  set(k, v) { try { localStorage.setItem("mapapp." + k, JSON.stringify(v)); } catch (_) { } },
};

const INDOOR = () => [["", t("Antenne außen")], ["open", t("Fenster offen")], ["trad", t("Fenster zu, Altbau")], ["lowe", t("Fenster zu, Wärmeschutz")]];
const DEVICES = () => [["p1pro", t("Stabantenne")], ["t1000e", "T1000-E"]];
const S = {
  app: null, layers: {}, busy: false,
  // link layer: on/off and settings panel, endpoints, pick mode ("a", "b" or ""), last result
  link: { on: false, open: false, ...store.get("layer.link", {}) }, a: null, b: null, pick: "", res: null,
  mapPick: null,  // one-off map click for another tool: { label, cb }
  walk: null, walkId: null,  // layer data with a model summary (walk layer, colored by residual)
  insp: { open: store.get("insp.open", true), tab: null },
};
let map2d, map3d;
const E_tempMarker = (lat, lon) => map2d && map2d.setTemp(lat, lon);
// The 3D view may be unavailable (no WebGL); its errors must not break the 2D map and panels.
const in3d = f => { try { f(); } catch (e) { console.warn("3D-Ansicht:", e.message); } };

// ---------------------------------------------------------------- endpoints
function endpointFromFeature(f) {
  const e = f.properties._endpoint, [lon, lat] = f.geometry.coordinates;
  return { name: e.name || f.properties._title || t("Punkt"), lat, lon, height_m: e.height_m || [1.5, 3],
    clutter_m: e.clutter_m ?? 12, indoor: e.indoor ?? null, device: e.device || "p1pro", measured: e.measured || null };
}
function endpointAt(lat, lon, which) {
  const walker = which === "b";
  return { name: t("Punkt {lat}, {lon}", { lat: lat.toFixed(5), lon: lon.toFixed(5) }), lat, lon, height_m: walker ? [1.0, 1.6] : [1.5, 3.0],
    clutter_m: 12, indoor: null, device: walker ? "t1000e" : "p1pro", measured: null };
}
function setEndpoint(which, ep) {
  S[which] = ep;
  if (which === "a") setPick("b");
  renderEndpoints(); map2d.setEndpoints(S.a, S.b); compute();
}
function setPick(which) {
  S.pick = S.link.on ? which : "";
  document.body.classList.toggle("picking", !!S.pick || !!S.mapPick);
  document.querySelectorAll("[data-pick]").forEach(b => {
    b.classList.toggle("on", b.dataset.pick === S.pick); b.setAttribute("aria-pressed", String(b.dataset.pick === S.pick));
  });
  renderEndpoints();
}
const linkMsg = msg => { const el = $("#linkMsg"); if (el) el.textContent = msg || ""; };
function persistLink() { store.set("layer.link", { on: S.link.on, open: S.link.open }); }

// The link layer: a card in the layer list like the server layers, but computed on demand.
function renderLinkLayer() {
  const el = $("#lyr_link"), L = S.link;
  el.className = "lyr link";
  if (!S.app.scene) {  // the calculation runs over the laser-scan scene
    L.on = false;
    el.innerHTML = `<div class="hd"><input type="checkbox" disabled aria-label="${t("Strecke A → B")}">
      <span class="nm">${t("Strecke A → B")}</span><span class="grp">${t("Simulation")}</span></div>
      <div class="bd"><div class="note" style="margin:0">${t("Braucht eine Laserscan-Szene. Wie man sie herunterlädt und aufbereitet, steht in der README (Abschnitt „3D laser-scan data“).")}</div></div>`;
    return;
  }
  el.innerHTML = `<div class="hd"><input type="checkbox" data-on ${L.on ? "checked" : ""} aria-label="${t("Strecke A → B")}">
      <span class="nm">${t("Strecke A → B")}</span><span class="grp">${t("Simulation")}</span>
      <button class="btn small" data-open title="${t("Einstellungen")}" aria-expanded="${L.open}">⚙</button></div>
    <div class="bd" ${L.open ? "" : "hidden"}>
      <div class="note" style="margin:0">${t("Direktstrecke zwischen zwei Punkten, mit allen Modellfamilien über den Laserscan gerechnet. Ergebnis rechts unter „Details“.")}</div>
      <div class="pickrow"><span>${t("Klick in die Karte setzt")}</span>
        <span class="seg" role="group" aria-label="${t("Klick in die Karte setzt")}">
          <button class="btn" data-pick="a">A</button><button class="btn" data-pick="b">B</button><button class="btn" data-pick="">${t("aus")}</button>
        </span></div>
      <div id="endpoints"></div>
      <div class="row2">
        <label>${t("Preset")}<select id="preset">${S.app.presets.map(p => `<option>${p}</option>`).join("")}</select></label>
        <label>${t("Bäume")}<select id="leaf"><option value="belaubt">${t("belaubt")}</option><option value="unbelaubt">${t("unbelaubt")}</option></select></label>
      </div>
      <div class="row2"><button class="btn" id="btnSwap">A ⇄ B</button><button class="btn" id="btnClearB">${t("B entfernen")}</button></div>
      <div class="msg" id="linkMsg"></div>
      <p class="note" style="margin:0">${t("Punkte des Rundgangs, Standorte und Knoten haben im Popup „als A“ / „als B“. Esc beendet das Setzen.")}</p>
    </div>`;
  // own key (was "preset"), so a LongFast saved by the old page doesn't override the default
  $("#preset").value = store.get("link.preset", S.app.default_preset || "ShortSlow");
  $("#leaf").value = store.get("leaf", "belaubt");
  el.querySelector("[data-on]").addEventListener("change", e => {
    if (e.target.checked && !L.open) { L.open = true; renderLinkLayer(); }  // switching on shows its settings
    setLinkOn(e.target.checked);
  });
  el.querySelector("[data-open]").addEventListener("click", () => { L.open = !L.open; persistLink(); renderLinkLayer(); });
  el.querySelectorAll("[data-pick]").forEach(b => b.addEventListener("click", () => {
    if (!L.on) setLinkOn(true);
    setPick(b.dataset.pick);
  }));
  $("#preset").addEventListener("change", () => { store.set("link.preset", $("#preset").value); compute(); });
  $("#leaf").addEventListener("change", () => { store.set("leaf", $("#leaf").value); compute(); });
  $("#btnSwap").addEventListener("click", () => { [S.a, S.b] = [S.b, S.a]; renderEndpoints(); map2d.setEndpoints(S.a, S.b); compute(); });
  $("#btnClearB").addEventListener("click", () => {
    S.b = null; S.res = null; map2d.setEndpoints(S.a, null); map3d.setLink(null); setPick("b"); updateInspector(); linkState();
  });
  setPick(S.pick);
  linkState();
}
function setLinkOn(on) {
  S.link.on = on; persistLink();
  const cb = $("#lyr_link [data-on]"); if (cb) cb.checked = on;
  if (on) {
    map2d.setEndpoints(S.a, S.b);
    setPick(S.b ? "" : "b");
    compute();
  } else {
    setPick("");
    S.res = null; map2d.setEndpoints(null, null); map3d.setLink(null); updateInspector();
  }
  linkState();
}
// The hint under the link settings and the 3D "Strecke zeigen" button follow the link state.
function linkState() {
  $("#btn3dLink").hidden = !S.res;
  if (!S.link.on) linkMsg("");
  else if (!S.a || !S.b) linkMsg(t("Setze {p}: Klick in die Karte oder „als {p}“ im Popup.", { p: !S.a ? "A" : "B" }));
}
function renderEndpoints() {
  const box = $("#endpoints"); if (!box) return;
  const card = (which) => {
    const p = S[which], tag = which.toUpperCase();
    if (!p) return `<div class="ep ${S.pick === which ? "pick" : ""}"><div class="hd"><span class="tag">${tag}</span><span class="nm">${t("nicht gesetzt")}</span></div></div>`;
    const sel = (name, opts, val) => `<select data-ep="${which}" data-k="${name}">${opts.map(([v, l]) => `<option value="${v}" ${String(val ?? "") === v ? "selected" : ""}>${l}</option>`).join("")}</select>`;
    const m = p.measured;
    return `<div class="ep ${S.pick === which ? "pick" : ""}">
      <div class="hd"><span class="tag">${tag}</span><span class="nm" title="${esc(p.name)}">${esc(p.name)}</span></div>
      <div class="grid">
        <label>${t("Höhe min [m]")}<input type="number" step="0.1" data-ep="${which}" data-k="h0" value="${p.height_m[0]}"></label>
        <label>${t("Höhe max [m]")}<input type="number" step="0.1" data-ep="${which}" data-k="h1" value="${p.height_m[1]}"></label>
        <label>${t("Aufstellung")}${sel("indoor", INDOOR(), p.indoor)}</label>
        <label>${t("Gerät")}${sel("device", DEVICES(), p.device)}</label>
        <label>${t("Umgebung [m]")}<input type="number" step="1" data-ep="${which}" data-k="clutter_m" value="${p.clutter_m}"></label>
      </div>
      ${m ? `<div class="meas">${esc(t("Messung {time}: RSSI {rssi} dBm, SNR {snr} dB, {hops} Hops", { time: m.time || "", rssi: fmt(m.rssi, 0), snr: fmt(m.snr, 1), hops: m.hops }))}</div>` : ""}
    </div>`;
  };
  box.innerHTML = card("a") + card("b");
  box.querySelectorAll("[data-ep]").forEach(el => el.addEventListener("change", () => {
    const p = S[el.dataset.ep], k = el.dataset.k;
    if (k === "h0") p.height_m = [+el.value, Math.max(+el.value, p.height_m[1])];
    else if (k === "h1") p.height_m = [Math.min(p.height_m[0], +el.value), +el.value];
    else if (k === "clutter_m") p.clutter_m = +el.value;
    else if (k === "indoor") p.indoor = el.value || null;
    else p[k] = el.value;
    compute();
  }));
}

let pending = null;
async function compute() {
  if (!S.link.on) return;
  if (!S.a || !S.b) { S.res = null; map3d.setLink(null); updateInspector(); linkState(); return; }
  if (S.busy) { pending = true; return; }
  S.busy = true; status(t("rechne Strecke …"));
  const body = { a: S.a, b: S.b, preset: $("#preset").value, leaf: $("#leaf").value };
  try {
    const res = await postJSON("api/tools/link", body);
    if (!S.link.on) return;  // switched off while computing
    S.res = res; linkMsg("");
    renderLink(res, S.a, S.b);
    map3d.setLink(res);
    if (document.body.classList.contains("is3d")) map3d.frameLink();
    else map2d.fit(S.a, S.b);
    map2d.colorLink(css(grade(res.models.ENS.p_rx)[2]));
    openInspector("link");
    status(t("{d} m · ENS {level} dBm · P(Paket) {p} %", { d: fmt(res.d_m, 0), level: fmt(res.models.ENS.prx[1], 1), p: fmt(res.models.ENS.p_rx * 100, 0) }));
  } catch (e) {
    S.res = null; map3d.setLink(null); updateInspector();
    linkMsg(t("Keine Berechnung: {error}", { error: e.message })); status(e.message, true);
  } finally {
    S.busy = false; linkState();
    if (pending) { pending = null; compute(); }
  }
}

// ---------------------------------------------------------------- inspector
// Tabs appear only when they have content; the panel hides when none has.
const TABS = [
  ["link", () => t("Strecke"), () => !!S.res],
  ["models", () => t("Modelle"), () => !!S.res],
  ["walk", () => t("Rundgang"), () => !!S.walk],
  ["nodes", () => t("Knoten"), () => !!(S.layers.nodes && S.layers.nodes.enabled && S.layers.nodes.data)],
  ["job", () => t("Aufgabe"), () => !!selectedJob()],
  ["coord", () => t("Einsatz"), () => !!selectedMission()],
];
function updateInspector() {
  const avail = TABS.filter(([, , has]) => has());
  if (!avail.some(([id]) => id === S.insp.tab)) S.insp.tab = avail.length ? avail[0][0] : null;
  const show = S.insp.open && avail.length > 0, btn = $("#btnInsp");
  btn.disabled = !avail.length;
  btn.classList.toggle("on", show); btn.setAttribute("aria-pressed", String(show));
  btn.title = avail.length ? t("Detailbereich ein-/ausblenden") : t("Keine Details: Strecke berechnen oder Rundgang mit Modellen vergleichen");
  const wasShown = !$("#right").hidden;
  $("#right").hidden = !show; $("main").classList.toggle("noinsp", !show);
  $("#tabs").innerHTML = avail.map(([id, label]) =>
    `<button class="tab" role="tab" aria-selected="${id === S.insp.tab}" data-tab="${id}">${label()}</button>`).join("");
  $("#tabs").querySelectorAll("[data-tab]").forEach(t => t.addEventListener("click", () => { S.insp.tab = t.dataset.tab; updateInspector(); }));
  for (const [id] of TABS) $("#t_" + id).hidden = !(show && id === S.insp.tab);
  if (wasShown !== show && map3d && document.body.classList.contains("is3d")) map3d.resize();
  if (show && S.insp.tab === "job") renderDetailIfShown();
  if (show && S.insp.tab === "coord") renderMissionDetail();
}
// Redraw one server layer (after the coordination mode changed something on the map).
function refreshLayer(id) {
  const desc = S.app && S.app.layers.find(l => l.id === id);
  if (desc && S.layers[id] && S.layers[id].enabled) refresh(desc, true);
}
function openInspector(tab) { S.insp.open = true; S.insp.tab = tab; store.set("insp.open", true); updateInspector(); }
function toggleInspector(open) { S.insp.open = open; store.set("insp.open", open); updateInspector(); }

// ---------------------------------------------------------------- layers
function renderLayer(desc) {
  const st = S.layers[desc.id];
  const el = document.getElementById("lyr_" + desc.id);
  el.innerHTML = `<div class="hd"><input type="checkbox" data-on ${st.enabled ? "checked" : ""} aria-label="${esc(desc.name)}">
      <span class="nm">${esc(desc.name)}</span><span class="grp">${esc(desc.group)}</span>
      <button class="btn small" data-open title="${t("Einstellungen")}">⚙</button></div>
    <div class="bd" ${st.open ? "" : "hidden"}><div class="note" style="margin:0">${esc(desc.description)}</div>
      ${inputsHTML(desc.settings, st.values, desc.id)}
      <div class="msg"></div><div class="lg"></div><div class="summary"></div>
      ${desc.id === "sites" ? `<div class="sitemgr"></div>` : ""}</div>`;
  el.querySelector("[data-on]").addEventListener("change", e => { st.enabled = e.target.checked; persist(desc.id); refresh(desc, false, true); });
  el.querySelector("[data-open]").addEventListener("click", () => { st.open = !st.open; persist(desc.id); renderLayer(desc); showExtras(desc); });
  bindInputs(el, desc.settings, st.values, () => { persist(desc.id); renderLayer(desc); refresh(desc, false, true); });
  if (desc.id === "sites" && st.open) renderSites(el.querySelector(".sitemgr"));
}
// The inspector's "Rundgang" tab shows the model comparison of the layer that has one.
function setWalk(id, data, user = false) {
  if (data) { S.walk = data; S.walkId = id; renderWalk(data); if (user) { openInspector("walk"); return; } }
  else if (S.walkId === id) { S.walk = null; S.walkId = null; }
  updateInspector();
}
function persist(id) { const st = S.layers[id]; store.set("layer." + id, { enabled: st.enabled, open: st.open, values: st.values }); }
function showExtras(desc) {
  const st = S.layers[desc.id], el = document.getElementById("lyr_" + desc.id);
  if (!el || !st.data) return;
  el.querySelector(".lg").innerHTML = st.enabled ? legendHTML(st.data.legend) : "";
  el.querySelector(".msg").textContent = st.data.note || "";
  el.querySelector(".summary").innerHTML = st.enabled ? summaryHTML(st.data) : "";
  const cmp = el.querySelector("[data-cmp]");
  if (cmp) cmp.addEventListener("click", () => openInspector("walk"));
}
function summaryHTML(d) {
  const parts = [];
  if (d.stats) parts.push(`<div class="note" style="margin:0">${esc(d.stats.text || t("{n} Positionen, {m} direkt empfangen", { n: d.stats.positions, m: d.stats.direct }))}</div>`);
  if (d.stats && d.stats.basis) parts.push(`<div class="note" style="margin:0">${esc(d.stats.basis)}</div>`);
  if (d.summary && d.summary.models && Object.keys(d.summary.models).length)
    parts.push(`<button class="btn small" data-cmp style="align-self:flex-start">${t("Vergleich mit Modellen anzeigen")}</button>`);
  return parts.join("");
}
// user: the refresh follows a click in the layer panel (may open the inspector).
async function refresh(desc, auto = false, user = false) {
  const st = S.layers[desc.id];
  clearTimeout(st.timer);
  if (!st.enabled) {
    map2d.clear(desc.id); in3d(() => map3d.clear(desc.id)); showExtras(desc); setWalk(desc.id, null);
    if (desc.id === "nodes") nodesChanged();
    return;
  }
  const q = new URLSearchParams(Object.entries(st.values).map(([k, v]) => [k, String(v ?? "")]));
  if (!auto) status(t("lade {name} …", { name: desc.name }));
  try {
    st.data = await getJSON(`api/layers/${desc.id}?${q}`);
    map2d.show(desc.id, st.data); in3d(() => map3d.show(desc.id, st.data));
    if (!auto) status(t("bereit"));
  } catch (e) { st.data = { note: e.message }; status(e.message, true); }
  showExtras(desc);
  const scored = st.data && st.data.summary && st.data.summary.models && Object.keys(st.data.summary.models).length;
  setWalk(desc.id, scored ? st.data : null, user);
  if (desc.id === "nodes") nodesChanged();
  // Live layers ask to be refreshed; skip a round while a popup is open so it doesn't close.
  if (st.data && st.data.refresh_s) {
    const again = () => (map2d.popupOpen ? (st.timer = setTimeout(again, 3000)) : refresh(desc, true));
    st.timer = setTimeout(again, st.data.refresh_s * 1000);
  }
}

// ---------------------------------------------------------------- device
async function deviceStatus(action) {
  let d;
  try {
    d = action ? await postJSON(`api/device/${action}`, { port: $("#devPort").value.trim() })
      : await getJSON("api/device");
  } catch (e) { $("#devText").textContent = e.message; return; }
  const colors = { verbunden: "var(--ok)", verbinde: "var(--warn)", Fehler: "var(--bad)" };
  $("#devDot").style.background = colors[d.state] || "var(--line)";
  const since = d.last_packet ? ", " + t("letztes vor {s} s", { s: Math.round(Date.now() / 1000 - d.last_packet) }) : "";
  // Device states are German codes: t("getrennt") t("verbinde") t("verbunden") t("Fehler")
  $("#devText").textContent = d.state === "verbunden"
    ? t("{name} auf {port} · {n} Pakete", { name: d.me ? d.me.name : t("verbunden"), port: d.port === "sim" ? t("Simulation") : d.port, n: d.packets }) + since + (d.logging ? " · " + t("Log an") : "")
    : d.state === "Fehler" ? t("Fehler: {error}", { error: d.error }) : t(d.state);
  const connected = d.state === "verbunden" || d.state === "verbinde";
  $("#devBtn").textContent = connected ? t("Trennen") : t("Verbinden");
  $("#devBtn").dataset.action = connected ? "disconnect" : "connect";
  if (S.devState !== null && d.state !== S.devState && d.state !== "verbinde") {
    // The live node layer and today's packet log depend on the connection: reload the layer
    // list (new log dates, live source as default) and redraw.
    S.devState = d.state;
    try { S.app = await getJSON("api/app"); initLayers(); } catch (_) { }
  }
  S.devState = d.state;
}
function initLayers() {
  for (const st of Object.values(S.layers)) clearTimeout(st.timer);
  const box = $("#layers"); box.innerHTML = "";
  for (const desc of S.app.layers) {
    const saved = store.get("layer." + desc.id, null);
    const values = initialValues(desc.settings, saved && saved.values);
    S.layers[desc.id] = { enabled: saved ? saved.enabled : desc.enabled, open: saved ? saved.open : false, values, data: null };
    const div = document.createElement("div"); div.className = "lyr"; div.id = "lyr_" + desc.id; box.appendChild(div);
    renderLayer(desc);
  }
  for (const desc of S.app.layers) refresh(desc);
}

// ---------------------------------------------------------------- nodes and messages
// The inspector's node list follows the node layer (same data, same settings).
function nodesChanged() {
  const st = S.layers.nodes;
  if (st && st.enabled && st.data) renderNodeList(st.data);
  updateInspector();
}
// Show a node on the 2D map (switching from 3D) and open its popup.
function focusNode(id) {
  const st = S.layers.nodes;
  if (!st || !st.enabled) { toast(t("Die Ebene „Meshtastic-Knoten“ ist aus.")); return; }
  if (document.body.classList.contains("is3d")) setView("2d");
  if (!map2d.focusNode(id)) toast(t("{id} hat keine Position auf der Karte.", { id }));
}

// ---------------------------------------------------------------- map pick, toasts
// A one-off click on the map for another tool (placing a site); it wins over the link layer.
// With multi: clicks collect points until "Fertig" (cb gets the list) or Esc (nothing).
function pickOnMap(label, cb, { multi = false } = {}) {
  S.mapPick = { label, cb, multi, points: [] };
  $("#pickBanner").hidden = false;
  $("#pickText").textContent = t("Klick in die Karte: {label}", { label });
  $("#pickDone").hidden = !multi;
  document.body.classList.add("picking");
}
function clearMapPick() {
  S.mapPick = null; $("#pickBanner").hidden = true;
  E_tempMarker(null); if (map2d) map2d.setTempPath(null);
  document.body.classList.toggle("picking", !!S.pick);
}
function finishMapPick() {
  const pick = S.mapPick; if (!pick) return;
  clearMapPick();
  if (pick.multi) pick.cb(pick.points);
}
function mapClick(lat, lon) {
  if (S.mapPick) {
    const pick = S.mapPick;
    if (pick.multi) {
      pick.points.push([lat, lon]); map2d.setTempPath(pick.points);
      $("#pickText").textContent = t("Klick in die Karte: {label} ({n} Punkte)", { label: pick.label, n: pick.points.length });
      return true;
    }
    clearMapPick(); pick.cb(lat, lon); return true;
  }
  if (S.pick) { setEndpoint(S.pick, endpointAt(lat, lon, S.pick)); return true; }
  return false;
}

// Short notices at the bottom right; errors stay until closed. action: [label, fn]
function toast(msg, { bad = false, action = null } = {}) {
  const el = document.createElement("div");
  el.className = "toast" + (bad ? " bad" : "");
  el.setAttribute("role", bad ? "alert" : "status");
  el.innerHTML = `<span class="t">${esc(msg)}</span>${action ? `<button class="btn small" data-act>${esc(action[0])}</button>` : ""}
    <button class="btn small" data-close aria-label="${t("Schließen")}">✕</button>`;
  const close = () => el.remove();
  el.querySelector("[data-close]").addEventListener("click", close);
  if (action) el.querySelector("[data-act]").addEventListener("click", () => { close(); action[1](); });
  $("#toasts").appendChild(el);
  if (!bad) setTimeout(close, action ? 15000 : 6000);
}

// New files (grids, tracks, probe logs) and edited sites change the layers' options.
async function reloadApp() {
  try { S.app = await getJSON("api/app"); initLayers(); } catch (e) { toast(e.message, { bad: true }); }
}

function showCoverage(file) {
  const saved = store.get("layer.coverage", {}) || {};
  store.set("layer.coverage", { ...saved, enabled: true, open: true, values: { ...(saved.values || {}), file } });
  reloadApp();
}

// After a walk: upload the phone's GPX track and show the walk with the probes on the map.
function uploadGPX(job) {
  const inp = document.createElement("input");
  inp.type = "file"; inp.accept = ".gpx,application/gpx+xml";
  inp.addEventListener("change", async () => {
    const f = inp.files[0]; if (!f) return;
    status(t("lade {name} hoch …", { name: f.name }));
    try {
      const r = await fetch(`api/tracks?name=${encodeURIComponent(f.name)}`, { method: "POST", body: f });
      const res = await r.json();
      if (!r.ok) throw new Error(res.error || `HTTP ${r.status}`);
      const t0 = Date.parse(res.start) / 1000, t1 = Date.parse(res.end) / 1000;
      if (job.started && (t1 < job.started || t0 > (job.ended || Date.now() / 1000)))
        toast(t("{name}: die Spur liegt zeitlich nicht im Rundgang – falsche Datei?", { name: res.name }), { bad: true });
      const date = new Date(job.started * 1000);
      const iso = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
      const saved = store.get("layer.walk", {}) || {};
      store.set("layer.walk", { ...saved, enabled: true, open: true,
        values: { ...(saved.values || {}), date: iso, tracker: "probe:" + job.params.to, gpx: res.name } });
      toast(t("{name}: {n} Punkte hochgeladen", { name: res.name, n: res.points }));
      status(t("bereit"));
      reloadApp();
    } catch (e) { toast(t("GPX-Upload: {error}", { error: e.message }), { bad: true }); status(e.message, true); }
  });
  inp.click();
}

function taskActions(job) {
  if (job.kind === "coverage" && job.state === "fertig" && job.result && job.result.file)
    return [[t("Anzeigen"), () => showCoverage(job.result.file)]];
  if (job.kind === "probe" && job.state === "fertig" && job.result && job.result.sent)
    return [[t("GPX-Spur hochladen …"), () => uploadGPX(job)]];
  return [];
}
function taskTransition(job, prev) {
  if (job.state === "Fehler") toast(t("Fehler: {error}", { error: job.title }), { bad: true, action: [t("Protokoll"), () => showDetail(job.id)] });
  else if (job.state === "abgebrochen") toast(t("Abgebrochen: {title}", { title: job.title }));
  else if (job.state === "fertig") {
    reloadApp();
    const extra = taskActions(job)[0];
    const what = job.kind === "probe" ? " " + t("({a} von {s} beantwortet)", { a: job.result.answered ?? 0, s: job.result.sent ?? 0 }) : "";
    toast(t("Fertig: {title}", { title: job.title }) + what, { action: extra || [t("Protokoll"), () => showDetail(job.id)] });
  } else if (prev === "wartet" && job.state === "läuft") toast(t("Läuft: {title}", { title: job.title }));
}

// ---------------------------------------------------------------- start
async function defaultA() {
  try {
    const sites = await getJSON("api/layers/sites");
    const h = S.app.home; if (!sites.features.length || !h) return null;
    const d = f => Math.hypot(f.geometry.coordinates[1] - h.lat, (f.geometry.coordinates[0] - h.lon) * 0.63);
    const f = sites.features.slice().sort((x, y) => d(x) - d(y))[0];
    // same home placement as the walk layer's comparison ("none" = antenna outside)
    const walk = S.layers.walk && S.layers.walk.values.home_indoor;
    return { ...endpointFromFeature(f), indoor: walk ? (walk === "none" ? null : walk) : "open" };
  } catch (_) { return null; }
}

function setView(mode) {
  const is3d = mode === "3d";
  $("#map2d").hidden = is3d; $("#viewwrap").hidden = !is3d;
  document.body.classList.toggle("is3d", is3d);
  $("#view2d").classList.toggle("on", !is3d); $("#view2d").setAttribute("aria-pressed", String(!is3d));
  $("#view3d").classList.toggle("on", is3d); $("#view3d").setAttribute("aria-pressed", String(is3d));
  store.set("view", mode);
  if (is3d) { map3d.resize(); if (map3d.link) map3d.frameLink(); }
  else map2d.map.invalidateSize();
}

function bindUI() {
  // language switch: the page reloads in the chosen language
  $("#langSeg").innerHTML = LANGS.map(l => `<button class="btn ${l === lang ? "on" : ""}" data-lang="${l}" aria-pressed="${l === lang}">${l.toUpperCase()}</button>`).join("");
  $("#langSeg").querySelectorAll("[data-lang]").forEach(b => b.addEventListener("click", () => { if (b.dataset.lang !== lang) setLang(b.dataset.lang); }));
  $("#devBtn").addEventListener("click", () => deviceStatus($("#devBtn").dataset.action || "connect"));
  $("#view2d").addEventListener("click", () => setView("2d"));
  $("#view3d").addEventListener("click", () => setView("3d"));
  $("#btnInsp").addEventListener("click", () => toggleInspector($("#right").hidden));
  $("#btnCloseInsp").addEventListener("click", () => toggleInspector(false));
  $("#btn3dOverview").addEventListener("click", () => map3d.overview());
  $("#btn3dLink").addEventListener("click", () => map3d.frameLink());
  document.addEventListener("keydown", e => {
    if (e.key !== "Escape") return;
    if (S.mapPick) clearMapPick(); else if (S.pick) setPick("");
  });
  $("#pickCancel").addEventListener("click", clearMapPick);
  $("#pickDone").addEventListener("click", finishMapPick);
  // rail sections remember whether they are open
  document.querySelectorAll("details[data-sec]").forEach(d => {
    const open = store.get("sec." + d.dataset.sec, null);
    if (open !== null) d.open = open;
    d.addEventListener("toggle", () => store.set("sec." + d.dataset.sec, d.open));
  });
  $("#layers3d").innerHTML = LAYERS_3D.map(l => `<label class="tog"><input type="checkbox" data-l3="${l.id}" ${l.on ? "checked" : ""}><span class="sw" style="background:var(${l.sw})"></span>${l.label()}</label>`).join("");
  document.querySelectorAll("[data-l3]").forEach(el => el.addEventListener("change", () => map3d.setLayer(el.dataset.l3, el.checked)));
  $("#vex").addEventListener("input", e => { map3d.setVex(+e.target.value); $("#vexVal").textContent = fmt(+e.target.value, 1) + "×"; });
  $("#fres").addEventListener("input", e => { $("#fresVal").textContent = fmt(+e.target.value, 1) + " F"; map3d.setFresnel(+e.target.value); });
  $("#themeBtn").addEventListener("click", () => {
    const r = document.documentElement;
    const now = r.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    r.setAttribute("data-theme", now === "dark" ? "light" : "dark"); map3d.applyTheme();
  });
}

(async function main() {
  await loadCatalogue();
  translateStatic();
  try {
    S.app = await getJSON("api/app");
  } catch (e) { status(t("Server nicht erreichbar: {error}", { error: e.message }), true); return; }
  const center = S.app.home || { lat: 50.7374, lon: 7.0982 };
  map2d = new Map2D($("#map2d"), center, {
    onClick: (lat, lon) => mapClick(lat, lon),
    onFeatureAction: (f, which) => {
      if (which === "msg") { openConversation("dm:" + f.properties._node_id); return; }
      if (which === "coord") { assignTo(f.properties._node_id); return; }
      if (!S.link.on) setLinkOn(true);
      setEndpoint(which, endpointFromFeature(f));
    },
  });
  map3d = new Map3D($("#c"), {
    onPick: (lat, lon) => mapClick(lat, lon),
    onFeature: f => { if (S.pick && f.properties._endpoint) setEndpoint(S.pick, endpointFromFeature(f)); },
  });
  bindUI(); updateInspector();
  setView(store.get("view", "2d"));
  if (S.app.scene) {
    map3d.load().then(() => {
      $("#loading").hidden = true;
      for (const [id, st] of Object.entries(S.layers)) if (st.enabled && st.data) map3d.show(id, st.data);
      if (map3d.link) { map3d.setLink(map3d.link); map3d.frameLink(); }
    }).catch(e => { $("#loadingMsg").textContent = t("3D-Szene nicht verfügbar: {error}", { error: e.message }); });
  } else {
    $("#loadingMsg").innerHTML = [t("Keine Laserscan-Szene vorhanden."),
      t("Die 3D-Ansicht und die Streckenberechnung brauchen sie; wie man die Laserscan-Daten herunterlädt und aufbereitet, steht in der README (Abschnitt „3D laser-scan data“)."),
      t("Die 2D-Karte und alle anderen Ebenen funktionieren ohne.")].map(esc).join("<br>");
    $("#loading .bar").hidden = true;
  }
  initSites({ pickOnMap, tempMarker: E_tempMarker, changed: reloadApp, toast });
  initNodeList({ store, focusNode, message: id => openConversation("dm:" + id), assign: assignTo });
  initCoord({
    store, toast, openInspector, updateInspector, pickOnMap, tempMarker: E_tempMarker, focusNode, refreshLayer,
    nodes: () => (S.layers.nodes && S.layers.nodes.data && S.layers.nodes.data.nodes) || [],
    sites: () => ((S.layers.sites && S.layers.sites.data && S.layers.sites.data.features) || [])
      .map(f => ({ name: f.properties._title, lat: f.geometry.coordinates[1], lon: f.geometry.coordinates[0] })),
  });
  initMessages({ store, toast, connect: () => deviceStatus("connect"), focusNode });
  initTasks({ store, toast, openInspector, updateInspector, onTransition: taskTransition, actions: taskActions });
  initLayers();
  S.devState = null;
  deviceStatus(); setInterval(() => deviceStatus(), 5000);
  S.a = await defaultA();
  renderLinkLayer();
  if (S.link.on) setLinkOn(true);
})();
