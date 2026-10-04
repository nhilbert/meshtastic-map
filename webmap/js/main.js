// Wiring: layer panel (settings forms from the server's declarations), the link layer (A/B
// endpoints, computed in the browser session), the inspector on the right.
import { Map2D } from "./map2d.js";
import { G, actionsFor, bindToolbar, openMenu, registerActions, toolbarHTML } from "./actions.js";
import { Map3D, LAYERS_3D } from "./map3d.js";
import { assignTo, hasMissionDetail, initCoord, renderMissionDetail } from "./coord.js";
import { initDevConfig, loadDevConfig } from "./devconfig.js";
import { bindInputs, initialValues, inputsHTML, optionsFor, setFormMap } from "./forms.js";
import { initMessages, openConversation } from "./messages.js";
import { initNodeList, renderNodeList } from "./nodelist.js";
import { LANGS, lang, loadCatalogue, locale, setLang, t, translateStatic } from "./i18n.js";
import { renderLink, renderWalk } from "./panels.js";
import { initScenes, openNewScene, renderScenes } from "./scenes.js";
import { initSites, renderSites } from "./sites.js";
import { initTasks, isActive, openTaskForm, pollSoon, renderDetailIfShown, selectedJob, showDetail, watchJobs } from "./tasks.js";
import { drawWalks, initWalks, renderWalks, uploadTrack } from "./walks.js";
import { $, css, esc, featureHTML, fmt, getJSON, grade, legendHTML, postJSON } from "./util.js";
import { initWorkspace, setBadge, setRail, showSection } from "./workspace.js";
import { buttonLabel, symbolSVG } from "./icons.js";

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
  mapAdd: null,   // every map click goes to this (waypoints of a mission being written)
  walk: null, walkId: null,  // layer data with a model summary (walk layer, colored by residual)
  insp: { open: store.get("insp.open", !matchMedia("(max-width: 700px)").matches), tab: null },
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
      <div class="bd"><div class="note" style="margin:0">${t("Braucht eine Laserscan-Szene: Ebene „Laserscan-Szene“ → Einstellungen → „＋ Neue Szene“.")}</div></div>`;
    return;
  }
  el.innerHTML = `<div class="hd"><input type="checkbox" data-on ${L.on ? "checked" : ""} aria-label="${t("Strecke A → B")}">
      <span class="nm">${t("Strecke A → B")}</span><span class="grp">${t("Simulation")}</span>
      <button class="btn small quiet" data-open aria-label="${t("Einstellungen")}" title="${t("Einstellungen")}" aria-expanded="${L.open}">${symbolSVG("settings")}</button></div>
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
      <p class="note" style="margin:0">${t("Standorte und Knoten haben „als A“ / „als B“ im Popup, Messpunkte eines Rundgangs rechts unter „Auswahl“. Esc beendet das Setzen.")}</p>
    </div>`;
  // own key (was "preset"), so a LongFast saved by the old page doesn't override the default
  $("#preset").value = store.get("link.preset", S.app.default_preset || "ShortSlow");
  $("#leaf").value = store.get("leaf", "belaubt");
  el.querySelector("[data-on]").addEventListener("change", e => {
    if (e.target.checked && !L.open) { L.open = true; renderLinkLayer(); }  // switching on shows its settings
    setLinkOn(e.target.checked);
  });
  el.querySelector("[data-open]").addEventListener("click", () => {
    L.open = !L.open; persistLink(); renderLinkLayer(); el.querySelector("[data-open]").focus();
  });
  el.querySelectorAll("[data-pick]").forEach(b => b.addEventListener("click", () => {
    if (!L.on) setLinkOn(true);
    setPick(b.dataset.pick);
    if (b.dataset.pick && matchMedia("(max-width: 700px)").matches) setRail(false);
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
  else if (!S.a || !S.b) linkMsg(t("Setze {p}: Klick in die Karte oder „als {p}“ bei einem Objekt der Karte.", { p: !S.a ? "A" : "B" }));
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
  ["sel", () => t("Auswahl"), () => !!S.sel],
  ["job", () => t("Aufgabe"), () => !!selectedJob()],
  ["coord", () => t("Einsatz"), hasMissionDetail],
];
function updateInspector() {
  const focusedTab = $("#tabs").contains(document.activeElement) ? document.activeElement.dataset.tab : null;
  const avail = TABS.filter(([, , has]) => has());
  if (!avail.some(([id]) => id === S.insp.tab)) S.insp.tab = avail.length ? avail[0][0] : null;
  const show = S.insp.open && avail.length > 0, btn = $("#btnInsp");
  btn.disabled = !avail.length;
  btn.classList.toggle("on", show); btn.setAttribute("aria-pressed", String(show));
  btn.title = avail.length ? t("Detailbereich ein-/ausblenden") : t("Keine Details: Strecke berechnen oder Rundgang mit Modellen vergleichen");
  const wasShown = !$("#right").hidden;
  $("#right").hidden = !show; $("main").classList.toggle("noinsp", !show);
  $("#tabs").innerHTML = avail.map(([id, label]) =>
    `<button class="tab" id="tab_${id}" role="tab" aria-controls="t_${id}" tabindex="${id === S.insp.tab ? 0 : -1}" aria-selected="${id === S.insp.tab}" data-tab="${id}">${label()}</button>`).join("");
  $("#tabs").querySelectorAll("[data-tab]").forEach(t => t.addEventListener("click", () => { S.insp.tab = t.dataset.tab; updateInspector(); }));
  for (const [id] of TABS) {
    const panel = $("#t_" + id);
    panel.hidden = !(show && id === S.insp.tab);
    panel.setAttribute("role", "tabpanel"); panel.tabIndex = 0;
    if (avail.some(([key]) => key === id)) panel.setAttribute("aria-labelledby", "tab_" + id);
    else panel.removeAttribute("aria-labelledby");
  }
  if (focusedTab && show) $("#tab_" + S.insp.tab)?.focus();
  if (wasShown !== show && map3d && document.body.classList.contains("is3d")) map3d.resize();
  if (show && S.insp.tab === "job") renderDetailIfShown();
  if (show && S.insp.tab === "coord") renderMissionDetail();
}
// The link tool's actions on the map: any feature with _endpoint, or a free spot, as A or B.
function registerLinkActions() {
  const as = (which, ep) => () => { if (!S.link.on) setLinkOn(true); setEndpoint(which, ep()); };
  const ab = (a, b) => [
    { label: t("Als Startpunkt A"), short: "A", icon: "endA", group: G.link, quick: false, run: as("a", a) },
    { label: t("Als Endpunkt B"), short: "B", icon: "endB", group: G.link, quick: false, run: as("b", b) },
  ];
  registerActions("*", (ref, f) => f && f.properties._endpoint
    ? ab(() => endpointFromFeature(f), () => endpointFromFeature(f)) : []);
  registerActions("map", ({ lat, lon }) => [
    ...ab(() => endpointAt(lat, lon, "a"), () => endpointAt(lat, lon, "b")),
    { label: t("Koordinaten kopieren"), icon: "copy", group: G.copy, run: async () => {
      const text = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
      try { await navigator.clipboard.writeText(text); toast(t("Kopiert: {text}", { text })); }
      catch (_) { toast(text); }
    } },
  ]);
}

// Redraw one server layer (after the coordination mode changed something on the map).
function refreshLayer(id) {
  const desc = S.app && S.app.layers.find(l => l.id === id);
  if (desc && S.layers[id] && S.layers[id].enabled) refresh(desc, true);
}
function openInspector(tab) {
  setRail(false, false);
  S.insp.open = true; S.insp.tab = tab; store.set("insp.open", true); updateInspector();
  $("#tab_" + S.insp.tab)?.focus();
}
function toggleInspector(open) {
  S.insp.open = open; store.set("insp.open", open); updateInspector();
  if (open) { setRail(false, false); $("#tab_" + S.insp.tab)?.focus(); }
  else $("#btnInsp").focus();
}

// ---------------------------------------------------------------- selection
// The last object clicked on the map, in the details: its fields and the same toolbar as its
// popup; it stays when the popup closes. A measurement point has no popup: this is where its
// details show, and a ring marks it on the map.
function renderSelection() {
  const f = S.sel, box = $("#t_sel");
  if (!f) { box.innerHTML = ""; return; }
  const p = f.properties, acts = actionsFor(p._ref, f);
  box.innerHTML = `<div class="selwrap">${featureHTML(p)}</div>
    <div class="sec"><button class="btn small" data-unsel>${symbolSVG("close")}${esc(t("Auswahl aufheben"))}</button></div>`;
  const tb = box.querySelector(".acts");
  tb.innerHTML = toolbarHTML(acts);
  bindToolbar(tb, acts, p._title || "", null);
  box.querySelector("[data-unsel]").addEventListener("click", () => {
    S.sel = null; map2d.mark(null); renderSelection(); updateInspector();
  });
}

// ---------------------------------------------------------------- layers
function renderLayer(desc) {
  const st = S.layers[desc.id];
  const el = document.getElementById("lyr_" + desc.id);
  const walks = WALK_LAYERS.includes(desc.id);
  el.innerHTML = `<div class="hd"><input type="checkbox" data-on ${st.enabled ? "checked" : ""} aria-label="${esc(desc.name)}">
      <span class="nm">${esc(desc.name)}</span>
      <button class="btn small quiet" data-open aria-label="${esc(t("Einstellungen für {name}", { name: desc.name }))}" aria-expanded="${st.open}" title="${t("Einstellungen")}">${symbolSVG("settings")}</button></div>
    <div class="bd" ${st.open ? "" : "hidden"}><div class="note" style="margin:0">${esc(desc.description)}</div>
      ${walks ? `<div class="eyes" data-eyes></div>` : inputsHTML(desc.settings, st.values, desc.id)}
      <div class="msg"></div><div class="lg"></div><div class="summary"></div>
      ${MANAGED[desc.id] ? `<button class="lnk manage" data-manage>${esc(MANAGED[desc.id][1]())} ›</button>` : ""}</div>`;
  el.querySelector("[data-on]").addEventListener("change", e => { st.enabled = e.target.checked; persist(desc.id); refresh(desc, false, true); });
  el.querySelector("[data-open]").addEventListener("click", () => {
    st.open = !st.open; persist(desc.id); renderLayer(desc); showExtras(desc); el.querySelector("[data-open]").focus();
  });
  if (walks) drawWalks();
  else bindInputs(el, desc.settings, st.values, () => { persist(desc.id); renderLayer(desc); refresh(desc, false, true); });
  el.querySelector("[data-manage]")?.addEventListener("click", () => showSection(MANAGED[desc.id][0]));
}
// Give a layer other values and switch it on or off (enabled null: as it is), then redraw it:
// the eyes and the options of the walk layers, which are not in the layer's own card.
function setLayer(id, values = {}, enabled = null) {
  const desc = S.app.layers.find(l => l.id === id), st = S.layers[id];
  if (!desc || !st) return;
  Object.assign(st.values, values);
  for (const s of desc.settings) {  // a select whose options depend on a changed value
    if (!s.depends_on) continue;
    const opts = optionsFor(s, st.values);
    if (!opts.some(([o]) => String(o) === String(st.values[s.name]))) st.values[s.name] = opts.length ? opts[0][0] : "";
  }
  if (enabled !== null) st.enabled = enabled;
  persist(id); renderLayer(desc); refresh(desc, false, true);
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
  if (!auto && WALK_LAYERS.includes(desc.id)) drawWalks();  // its eyes and options follow
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
// Airtime ("Funklast"): what the device measures and what the app itself sent in the last
// hour (server: airtime.py). Kinds of own packets: coord, text, position_request, traceroute.
const AIR_KINDS = {
  coord: () => t("Funksprüche der Koordination"), text: () => t("Nachrichten"),
  position_request: () => t("Positionsanfragen"), traceroute: () => t("Traceroutes"),
};
function renderAirtime(a) {
  const pct = (v, d = 1) => (v === null || v === undefined) ? "—" : `${fmt(v, d)} %`;
  $("#telemetryChUtil").textContent = a ? pct(a.device.channel_util_pct) : "—";
  $("#telemetryAirTx").textContent = a ? pct(a.device.air_util_tx_pct) : "—";
  $("#airDot").style.background = !a ? "var(--line)" : a.warnings.length ? "var(--bad)" : "var(--ok)";
  if (!a) { $("#airBox").innerHTML = `<p class="note">${t("Braucht das verbundene Gerät.")}</p>`; return; }
  const dv = a.device, app = a.app;
  const row = (label, value) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`;
  const kinds = Object.entries(app.kinds).map(([k, v]) =>
    row((AIR_KINDS[k] || (() => k))(), t("{n} Pakete · {s} s", { n: v.packets, s: fmt(v.airtime_s, 1) }))).join("");
  $("#airBox").innerHTML = `
    ${a.warnings.map(w => `<p class="msg" role="alert" style="margin:4px 0">⚠ ${esc(w)}</p>`).join("")}
    <dl class="telemetry">
      ${row(t("Kanal belegt (letzte Minute)"), pct(dv.channel_util_pct))}
      ${row(t("Eigene Sendezeit (letzte Stunde)"), pct(dv.air_util_tx_pct, 2))}
      ${dv.tx_relay !== null && dv.tx_relay !== undefined ? row(t("Weitergeleitet / gesendet seit Start"), `${dv.tx_relay} / ${dv.packets_tx}`) : ""}
      ${dv.online_nodes ? row(t("Knoten online"), String(dv.online_nodes)) : ""}
      ${dv.noise_floor_dbm ? row(t("Grundrauschen"), `${dv.noise_floor_dbm} dBm`) : ""}
    </dl>
    <div class="hd2">${t("Von der App gesendet (letzte Stunde)")}</div>
    <dl class="telemetry">${kinds || row(t("nichts"), "")}
      ${row(t("Zusammen"), t("{n} Pakete · {s} s · {p} der Zeit", { n: app.packets, s: fmt(app.airtime_s, 1), p: pct(app.share_pct, 2) }))}</dl>
    ${a.notes.map(n => `<p class="note" style="margin:4px 0">${esc(n)}</p>`).join("")}
    <p class="note" style="margin:4px 0">${esc(t("Sendezeit geschätzt für {preset}; Bestätigungen und Antworten der Empfänger kommen dazu. Gerätewerte {age}.", {
      preset: a.preset, age: dv.age_s === null ? t("noch keine") : t("vor {n} s", { n: dv.age_s }) }))}</p>`;
}
// The system's serial ports for the port choice; "" = automatic, which the server resolves.
// Bluetooth devices ("ble:<address>") come from a search; the chosen one is remembered with its
// name, so that it stays in the list without a new search.
const isBle = port => (port || "").startsWith("ble:");
async function loadPorts() {
  try { renderPorts(await getJSON("api/device/ports")); } catch (_) { }
}
function renderPorts(d) {
  const sel = $("#devPort");
  const saved = store.get("device.port", "");
  const ports = isBle(saved) && !d.ports.some(p => p.device === saved)
    ? [...d.ports, { device: saved, kind: "ble", description: store.get("device.name", "") || saved.slice(4) }]
    : d.ports;
  const label = p => p.kind === "ble" ? t("Bluetooth · {name}", { name: p.description })
    : p.kind === "bluetooth" ? t("{port} · serieller Bluetooth-Port, kein Meshtastic-Gerät", { port: p.device })
    : `${p.device} · ${p.vendor || p.description || ""}`;
  const auto = isBle(d.auto) ? t("Automatisch (Bluetooth)")
    : d.auto ? t("Automatisch ({port})", { port: d.auto }) : t("Automatisch (kein Gerät gefunden)");
  sel.innerHTML = `<option value="">${esc(auto)}</option>`
    + ports.map(p => `<option value="${esc(p.device)}" data-name="${esc(p.kind === "ble" ? p.description : "")}">${esc(label(p))}</option>`).join("");
  sel.value = ports.some(p => p.device === saved) ? saved : "";
  S.simulated = d.simulated;
  sel.disabled = d.simulated || S.devState === "verbunden" || S.devState === "verbinde";
}
function choosePort(port) {
  const sel = $("#devPort");
  sel.value = port;
  store.set("device.port", sel.value);
  store.set("device.name", sel.selectedOptions[0]?.dataset.name || "");
}
// What the Bluetooth search is doing and what it found, under the buttons that started it; it
// stays until the next step (connecting, another choice). ready: that step is Verbinden.
function devHint(msg, { bad = false, ready = false } = {}) {
  const el = $("#devHint");
  el.hidden = !msg;
  el.textContent = msg;
  el.classList.toggle("bad", bad);
  $("#devBtn").classList.toggle("on", ready);
}
async function scanBluetooth() {
  const btn = $("#devBle");
  S.bleScan = btn.disabled = true;
  const end = Date.now() + 10000;
  const tick = () => {
    const n = Math.ceil((end - Date.now()) / 1000);
    if (n > 0) devHint(t("Suche Bluetooth-Geräte … noch {n} s", { n }));
    else devHint(t("Suche Bluetooth-Geräte …"));
  };
  tick();
  const timer = setInterval(tick, 1000);
  let d = null, error = "";
  try { d = await postJSON("api/device/scan", {}); } catch (e) { error = e.message; }
  clearInterval(timer);
  S.bleScan = false;
  btn.disabled = S.simulated || S.devState === "verbunden" || S.devState === "verbinde";
  if (!d) { devHint(error, { bad: true }); return; }
  renderPorts(d);
  const found = d.ports.filter(p => p.kind === "ble");
  if (found.length === 1) {
    choosePort(found[0].device);
    devHint(t("„{name}“ gefunden und ausgewählt. Jetzt verbinden.", { name: found[0].description }), { ready: true });
    $("#devBtn").focus();
  } else if (found.length) {
    devHint(t("{n} Bluetooth-Geräte gefunden: oben das eigene auswählen, dann verbinden.", { n: found.length }));
    $("#devPort").focus();
  } else devHint(t("Kein Meshtastic-Gerät über Bluetooth gefunden. Ist es eingeschaltet und in Reichweite? Solange die Handy-App mit ihm verbunden ist, ist es nicht sichtbar."), { bad: true });
}
async function deviceStatus(action) {
  let d;
  if (action) devHint("");
  try {
    d = action ? await postJSON(`api/device/${action}`, { port: $("#devPort").value })
      : await getJSON("api/device");
  } catch (e) {
    $("#devText").textContent = e.message;
    setBadge("device", { dot: "bad" }, t("Gerät: {state}", { state: action ? t("Fehler") : t("Server nicht erreichbar") }));
    $("#telemetryDevice").textContent = t("Fehler");
    $("#telemetryPackets").textContent = $("#telemetryLast").textContent = "—";
    return;
  }
  const colors = { verbunden: "var(--ok)", verbinde: "var(--warn)", Fehler: "var(--bad)" };
  const dots = { verbunden: "ok", verbinde: "warn", Fehler: "bad" };
  const stateText = d.state === "verbunden" && d.port === "sim" ? t("Simulation") : t(d.state);
  $("#devDot").style.background = colors[d.state] || "var(--line)";
  setBadge("device", dots[d.state] ? { dot: dots[d.state] } : null,
    t("Gerät: {state}", { state: d.state === "Fehler" ? d.error : stateText }));
  $("#telemetryDevice").textContent = stateText;
  $("#telemetryPackets").textContent = fmt(d.packets, 0);
  renderAirtime(d.airtime);
  $("#telemetryLast").textContent = d.last_packet ? t("vor {n} s", { n: Math.max(0, Math.round(Date.now() / 1000 - d.last_packet)) }) : "—";
  const since = d.last_packet ? ", " + t("letztes vor {s} s", { s: Math.round(Date.now() / 1000 - d.last_packet) }) : "";
  // Device states are German codes: t("getrennt") t("verbinde") t("verbunden") t("Fehler")
  $("#devText").textContent = d.state === "verbunden"
    ? t("{name} auf {port} · {n} Pakete", { name: d.me ? d.me.name : t("verbunden"), port: d.port === "sim" ? t("Simulation") : isBle(d.port) ? t("Bluetooth") : d.port, n: d.packets }) + since + (d.logging ? " · " + t("Log an") : "")
    : d.restarting ? t("Gerät startet nach dem Schreiben neu …")
    : d.state === "Fehler" ? t("Fehler: {error}", { error: d.error })
    : d.state === "verbinde" && isBle(d.trying) ? t("verbinde über Bluetooth (kann eine halbe Minute dauern) …")
    : t(d.state);
  const connected = d.state === "verbunden" || d.state === "verbinde";
  $("#devBtn").textContent = connected || d.retrying ? t("Trennen") : t("Verbinden");
  $("#devBtn").dataset.action = connected || d.retrying ? "disconnect" : "connect";
  $("#devPort").disabled = connected || S.simulated;
  $("#devBle").disabled = connected || d.retrying || S.simulated || S.bleScan;
  // Not paired: only the owner can do that, so the server stopped trying; say how.
  $("#devPair").hidden = !(d.state === "Fehler" && d.error_kind === "unpaired");
  // A connection that dropped is retried by the server; say so over the map until it is back.
  const lost = d.lost_at && d.retrying;
  $("#devBanner").hidden = !lost;
  $("#devBanner").classList.toggle("lost", !d.restarting);  // a reboot after a write is no alarm
  if (lost) {
    const p = { time: new Date(d.lost_at * 1000).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" }), s: d.retry_s };
    $("#devBannerText").textContent = d.restarting
      ? t("Das Gerät startet neu und übernimmt die Einstellungen. Die Verbindung kommt gleich von selbst wieder.")
      : isBle(d.port)
      ? t("Bluetooth-Verbindung zum Gerät seit {time} weg: neuer Versuch alle {s} s. Reichweite prüfen.", p)
      : t("Verbindung zum Gerät seit {time} weg: neuer Versuch alle {s} s. Kabel prüfen.", p);
  }
  if (d.retrying && !d.lost_at && d.state === "Fehler") $("#devText").textContent += " · " + t("neuer Versuch alle {s} s", { s: d.retry_s });
  if (!connected && d.state !== S.devState) loadPorts();  // plugged in or out meanwhile?
  if (d.state !== S.devState || !!d.restarting !== S.devRestarting) loadDevConfig(d.state === "verbunden", d.restarting);
  S.devRestarting = !!d.restarting;
  if (S.devState !== null && d.state !== S.devState && d.state !== "verbinde") {
    // The live node layer and today's packet log depend on the connection: reload the layer
    // list (new log dates, live source as default) and redraw.
    S.devState = d.state;
    try { S.app = await getJSON("api/app"); initLayers(); } catch (_) { }
  }
  S.devState = d.state;
}
// The walk layers have no settings in their card: an eye per walk there, their options under
// Rundgänge with the walks (walks.js).
const WALK_LAYERS = ["walk", "heard"];
// Ebenen only show things; managing them is in the view named here (link under the settings).
const MANAGED = {
  sites: ["sites", () => t("Standorte verwalten")],
  scene: ["scenes", () => t("Szenen verwalten")],
  coord: ["coord", () => t("Einsätze in der Koordination")],
  coverage: ["coverage", () => t("Abdeckung simulieren")],
  walk: ["walks", () => t("Optionen und Rundgänge verwalten")],
  heard: ["imports", () => t("Optionen und Rundgänge verwalten")],
};
function initLayers() {
  for (const st of Object.values(S.layers)) clearTimeout(st.timer);
  const box = $("#layers"); box.innerHTML = "";
  const groups = {};
  for (const desc of S.app.layers) {
    if (!groups[desc.group]) {  // one folding section per group, in the server's order
      const sec = document.createElement("details");
      sec.className = "sec"; sec.dataset.grp = desc.group;
      sec.open = store.get("grp." + desc.group, true);
      sec.innerHTML = `<summary><h2>${esc(desc.group)}</h2><span class="count"></span></summary><div class="stack"></div>`;
      sec.addEventListener("toggle", () => store.set("grp." + desc.group, sec.open));
      box.appendChild(sec);
      groups[desc.group] = sec;
    }
    const saved = store.get("layer." + desc.id, null);
    const values = initialValues(desc.settings, saved && saved.values);
    S.layers[desc.id] = { enabled: saved ? saved.enabled : desc.enabled, open: saved ? saved.open : false, values, data: null };
    const div = document.createElement("div"); div.className = "lyr"; div.id = "lyr_" + desc.id;
    groups[desc.group].querySelector(".stack").appendChild(div);
    renderLayer(desc);
  }
  for (const sec of Object.values(groups)) sec.querySelector(".count").textContent = sec.querySelectorAll(".lyr").length;
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
// With count: finished after that many clicks; two make a rectangle (a bounding box).
function pickOnMap(label, cb, { multi = false, count = 0 } = {}) {
  const fromRail = document.body.classList.contains("rail-open") && matchMedia("(max-width: 700px)").matches;
  const finish = (...args) => { if (fromRail) setRail(true); cb(...args); };
  if (count) multi = true;
  S.mapPick = { label, cb: finish, multi, count, points: [], fromRail };
  setRail(false, false);
  $("#pickBanner").hidden = false;
  $("#pickText").textContent = t("Klick in die Karte: {label}", { label });
  $("#pickDone").hidden = !multi || !!count;
  document.body.classList.add("picking");
  $("#pickCancel").focus();
}
function clearMapPick(restore = true) {
  const fromRail = S.mapPick?.fromRail;
  S.mapPick = null; $("#pickBanner").hidden = true;
  E_tempMarker(null); if (map2d) map2d.setTempPath(null);
  document.body.classList.toggle("picking", !!S.pick);
  if (restore && fromRail) setRail(true);
}
function finishMapPick() {
  const pick = S.mapPick; if (!pick) return;
  clearMapPick(false);
  if (pick.multi) pick.cb(pick.points);
}
function mapClick(lat, lon, feature = null) {
  if (S.mapPick) {
    const pick = S.mapPick;
    if (pick.multi) {
      pick.points.push([lat, lon]); map2d.setTempPath(pick.points);
      if (pick.count && pick.points.length >= pick.count) { finishMapPick(); return true; }
      $("#pickText").textContent = t("Klick in die Karte: {label} ({n} Punkte)", { label: pick.label, n: pick.points.length });
      return true;
    }
    clearMapPick(false); pick.cb(lat, lon, feature); return true;
  }
  if (S.pick) { setEndpoint(S.pick, endpointAt(lat, lon, S.pick)); return true; }
  if (S.mapAdd) { S.mapAdd(lat, lon, feature); return true; }
  return false;
}
// A click on a map object while a pick (or a mission's waypoints) waits: a point object gives its own position, an area
// the clicked spot; the tool gets the object too (a waypoint takes a target's name).
function featurePick(f, ll) {
  const type = f.properties._ref && f.properties._ref.type;
  // a mission being written takes targets, sites and places; nodes keep their popup
  if (!S.mapPick && !(S.mapAdd && !S.pick && ["target", "site", "place"].includes(type))) return false;
  const point = f.geometry && f.geometry.type === "Point";
  return mapClick(point ? f.geometry.coordinates[1] : ll.lat, point ? f.geometry.coordinates[0] : ll.lng, f);
}

// The rectangle from the first corner to the mouse while the second one is picked.
function mapMove(lat, lon) {
  const pick = S.mapPick;
  if (!pick || pick.count !== 2 || pick.points.length !== 1) return;
  const [a, b] = pick.points[0];
  map2d.setTempPath([[a, b], [a, lon], [lat, lon], [lat, b]]);
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

// New or deleted files (grids, tracks, logs, walks) and edited sites change the layers' options.
async function reloadApp() {
  try {
    S.app = await getJSON("api/app"); initLayers(); renderSites($("#sitesBox")); renderWalks();
  } catch (e) { toast(e.message, { bad: true }); }
}

// Switch a layer on with the given values (a finished task or import shows its result).
function showLayer(id, values) {
  const saved = store.get("layer." + id, {}) || {};
  store.set("layer." + id, { ...saved, enabled: true, open: true, values: { ...(saved.values || {}), ...values } });
  reloadApp();
}

function taskActions(job) {
  if (job.kind === "coverage" && job.state === "fertig" && job.result && job.result.file)
    return [[t("Anzeigen"), () => showLayer("coverage", { file: job.result.file })]];
  if (job.kind === "probe" && job.state === "fertig" && job.result && job.result.sent)
    return [[t("GPX-Spur hochladen …"), () => uploadTrack(job)]];
  // the 3D view is built for one scene: a new scene in use needs a fresh page
  if (job.kind === "scene" && job.state === "fertig" && job.result && job.result.active)
    return [[t("Neu laden"), () => location.reload()]];
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
  initWorkspace(store);
  for (const [id, icon] of Object.entries({ view2d: "map", view3d: "cube" })) buttonLabel($("#" + id), icon, $("#" + id).textContent);
  $("#btnInsp").innerHTML = symbolSVG("panel");
  $("#btnCloseInsp").innerHTML = symbolSVG("close");
  $("#tabs").addEventListener("keydown", e => {
    const tabs = [...$("#tabs").querySelectorAll("[data-tab]")];
    const index = tabs.indexOf(document.activeElement);
    if (index < 0 || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    const next = e.key === "Home" ? 0 : e.key === "End" ? tabs.length - 1
      : (index + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    S.insp.tab = tabs[next].dataset.tab; updateInspector();
  });
  // settings: language (the page reloads in it) and light or dark
  $("#langSeg").innerHTML = LANGS.map(l => `<button class="btn ${l === lang ? "on" : ""}" data-lang="${l}" aria-pressed="${l === lang}">${l.toUpperCase()}</button>`).join("");
  $("#langSeg").querySelectorAll("[data-lang]").forEach(b => b.addEventListener("click", () => { if (b.dataset.lang !== lang) setLang(b.dataset.lang); }));
  const theme = document.documentElement.getAttribute("data-theme") || "dark";
  $("#themeSeg").innerHTML = [["dark", t("Dunkel")], ["light", t("Hell")]].map(([v, label]) =>
    `<button class="btn ${v === theme ? "on" : ""}" data-theme="${v}" aria-pressed="${v === theme}">${label}</button>`).join("");
  $("#themeSeg").querySelectorAll("[data-theme]").forEach(b => b.addEventListener("click", () => {
    document.documentElement.setAttribute("data-theme", b.dataset.theme); store.set("theme", b.dataset.theme);
    $("#themeSeg").querySelectorAll("[data-theme]").forEach(x => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", String(x === b)); });
    in3d(() => map3d.applyTheme());
  }));
  // the status bar leads to the views its values come from
  for (const [id, section] of [["telemetryDevice", "device"], ["telemetryPackets", "device"], ["telemetryLast", "device"],
    ["telemetryChUtil", "airtime"], ["telemetryAirTx", "airtime"]]) {
    const item = $("#" + id).parentElement;
    item.tabIndex = 0; item.setAttribute("role", "button");
    item.addEventListener("click", () => showSection(section));
    item.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); showSection(section); } });
  }
  $("#devBtn").addEventListener("click", () => deviceStatus($("#devBtn").dataset.action || "connect"));
  $("#devScan").innerHTML = symbolSVG("refresh");
  $("#devScan").addEventListener("click", loadPorts);
  buttonLabel($("#devBle"), "bluetooth", t("Suchen"));
  $("#devBle").addEventListener("click", scanBluetooth);
  $("#devPairLink").hidden = !/Windows/.test(navigator.userAgent);  // the link opens Windows' settings
  $("#devBannerStop").addEventListener("click", () => deviceStatus("disconnect"));
  $("#devPort").addEventListener("change", () => { choosePort($("#devPort").value); devHint(""); });
  $("#view2d").addEventListener("click", () => setView("2d"));
  $("#view3d").addEventListener("click", () => setView("3d"));
  $("#btnInsp").addEventListener("click", () => toggleInspector($("#right").hidden));
  $("#btnCloseInsp").addEventListener("click", () => toggleInspector(false));
  $("#btn3dOverview").addEventListener("click", () => map3d.overview());
  $("#btn3dLink").addEventListener("click", () => map3d.frameLink());
  document.addEventListener("keydown", e => {
    if (e.key !== "Escape") return;
    if (S.mapPick) clearMapPick(); else if (S.pick) setPick("");
    else if (document.body.classList.contains("rail-open")) setRail(false);
    else if (!$("#right").hidden) toggleInspector(false);
  });
  $("#pickCancel").addEventListener("click", () => clearMapPick());
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
  $("#coverageBox").innerHTML = `<p class="note" style="margin:0">${esc(t("Rechnet die Empfangswahrscheinlichkeit um einen Standort; das Ergebnis zeigt die Ebene „Simulierte Abdeckung“."))}</p>
    <button class="btn" data-cov>${symbolSVG("coverage")}${esc(t("Abdeckung simulieren …"))}</button>`;
  $("#coverageBox [data-cov]").addEventListener("click", () => openTaskForm("coverage"));
}

(async function main() {
  const theme = store.get("theme", "dark");
  document.documentElement.setAttribute("data-theme", theme === "light" ? "light" : "dark");
  await loadCatalogue();
  translateStatic();
  try {
    S.app = await getJSON("api/app");
  } catch (e) {
    status(t("Server nicht erreichbar: {error}", { error: e.message }), true);
    $("#telemetryDevice").textContent = t("Server nicht erreichbar");
    return;
  }
  map2d = new Map2D($("#map2d"), S.app.home || null, {
    onClick: (lat, lon) => mapClick(lat, lon),
    featurePick: (f, ll) => featurePick(f, ll),
    onMove: (lat, lon) => mapMove(lat, lon),
    onSelect: (f, open) => { S.sel = f; renderSelection(); if (open) openInspector("sel"); else updateInspector(); },
  });
  map3d = new Map3D($("#c"), {
    onPick: (lat, lon) => mapClick(lat, lon),
    onFeature: f => { if (S.pick && f.properties._endpoint) setEndpoint(S.pick, endpointFromFeature(f)); },
    // the same menu as on the 2D map: the object's actions, or those of the spot
    onContext: (f, lat, lon, x, y) => f
      ? openMenu(x, y, f.properties._title || "", actionsFor(f.properties._ref, f))
      : openMenu(x, y, `${lat.toFixed(5)}, ${lon.toFixed(5)}`, actionsFor({ type: "map", lat, lon })),
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
      t("Die 3D-Ansicht und die Streckenberechnung brauchen sie. Erstellen: Aufgaben → „Laserscan-Szene erstellen“ oder Rechtsklick in die Karte → „Szene hier erstellen“."),
      t("Die 2D-Karte und alle anderen Ebenen funktionieren ohne.")].map(esc).join("<br>");
    $("#loading .bar").hidden = true;
  }
  initSites({ pickOnMap, tempMarker: E_tempMarker, changed: reloadApp, toast, openPanel: () => showSection("sites") });
  renderSites($("#sitesBox"));
  initWalks({ toast, status, showLayer, setLayer, changed: reloadApp,
    layer: id => {
      const desc = S.app.layers.find(l => l.id === id), st = S.layers[id];
      return desc && st ? { name: desc.name, settings: desc.settings, values: st.values, enabled: st.enabled } : null;
    }, startProbe: () => openTaskForm("probe", null, $("#walkForm")),
    fit: box => {
      if (document.body.classList.contains("is3d")) setView("2d");
      map2d.fitBox(box);
    } });
  renderWalks();
  // areas of task forms are picked on the 2D map: the rectangle and preview are drawn there
  setFormMap({
    pick: (label, count, cb) => {
      if (document.body.classList.contains("is3d")) setView("2d");
      pickOnMap(label, count === 1 ? (lat, lon) => cb([[lat, lon]]) : cb, { count: count > 1 ? count : 0 });
    },
    preview: ring => map2d.setPreview(ring),
  });
  initScenes({ pickOnMap, toast, tempArea: pts => map2d.setTempPath(pts), openPanel: () => showSection("scenes"),
    taskStarted: job => { toast(t("Gestartet: {title}", { title: job.title })); pollSoon(); } });
  renderScenes($("#scenesBox"));
  initNodeList({ store, toast, focusNode, message: id => openConversation("dm:" + id), assign: assignTo,
    connected: () => S.devState === "verbunden", refreshNodes: () => refreshLayer("nodes"),
    openList: () => openInspector("nodes"),
    panTo: id => map2d.panToNode(id), feature: id => map2d.nodeFeature(id),
    showRoute: r => {
      if (r && document.body.classList.contains("is3d")) setView("2d");
      if (map2d) map2d.setRoute(r);
    } });
  initCoord({
    store, toast, openInspector, updateInspector, pickOnMap, tempMarker: E_tempMarker, focusNode, refreshLayer,
    draft: (path, sel) => map2d && map2d.setDraft(path, sel),
    mapAdd: cb => { S.mapAdd = cb; document.body.classList.toggle("mapadd", !!cb); },
    nodes: () => (S.layers.nodes && S.layers.nodes.data && S.layers.nodes.data.nodes) || [],
    sites: () => ((S.layers.sites && S.layers.sites.data && S.layers.sites.data.features) || [])
      .map(f => ({ name: f.properties._title, lat: f.geometry.coordinates[1], lon: f.geometry.coordinates[0] })),
  });
  initMessages({ store, toast, connect: () => deviceStatus("connect"), focusNode,
    heard: (id, delay_ms) => { if (!document.body.classList.contains("is3d")) map2d.ripple(id, delay_ms); },
    onOpen: () => { if (matchMedia("(max-width: 700px)").matches && !$("#right").hidden) toggleInspector(false); },
  });
  initDevConfig({ toast, refreshStatus: () => deviceStatus() });
  initTasks({ store, toast, openInspector, updateInspector, onTransition: taskTransition, actions: taskActions,
    guided: { scene: openNewScene } });
  watchJobs($("#walkJobs"), job => job.kind === "probe" && isActive(job));
  registerLinkActions();
  initLayers();
  S.devState = null;
  deviceStatus(); setInterval(() => deviceStatus(), 5000);
  S.a = await defaultA();
  renderLinkLayer();
  if (S.link.on) setLinkOn(true);
})();
