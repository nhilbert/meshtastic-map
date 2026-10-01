// Node list in the inspector ("Knoten"): the same nodes as the layer "Meshtastic-Knoten"
// (same source, same age filter), including those without a position. A click on a node with a
// position shows it on the map; ✉ opens a direct conversation in the messaging pane. Traceroute
// and position request go out over the mesh (server: node_requests.py); the latest result per
// node shows under its row, polled while a request runs; a traceroute can be drawn on the map.
import { locale, t } from "./i18n.js";
import { $, esc, fmt, getJSON, postJSON } from "./util.js";
import { symbolSVG } from "./icons.js";
import { G, actionsFor, bindToolbar, registerActions, toolbarHTML } from "./actions.js";

const SORTS = {
  last: [() => t("zuletzt gehört"), (a, b) => (b.last || 0) - (a.last || 0)],
  hops: [() => t("Hops"), (a, b) => (a.hops ?? 99) - (b.hops ?? 99) || (b.last || 0) - (a.last || 0)],
  snr: [() => "SNR", (a, b) => (b.snr ?? -99) - (a.snr ?? -99)],
  name: [() => t("Name"), (a, b) => (a.long || a.short || a.id).localeCompare(b.long || b.short || b.id, locale)],
};
const L = { api: null, data: null, filter: "", sort: "last", built: false, req: {}, hidden: new Set(), timer: null,
  shown: null };  // key of the traceroute drawn on the map
const POLL_MS = 1500;

// api: { store, toast, focusNode(id), message(id), assign(id), connected(), refreshNodes(),
//        showRoute(traceroute | null), openList(), panTo(id), feature(id) }
export function initNodeList(api) {
  L.api = api;
  L.sort = api.store.get("nodes.sort", "last");
  // a node's actions (map popup and menu, the opened row here); a request's result shows under
  // the node's row in the list
  registerActions("node", (ref, _f, where) => {
    const other = where === "list"
      ? { label: t("Auf der Karte zeigen"), icon: "pin", group: G.view, quick: false, run: () => L.api.focusNode(ref.id),
        disabled: ref.hasPos === false ? t("keine Position") : null }
      : { label: t("In der Knotenliste zeigen"), icon: "list", group: G.view, quick: false, run: () => showInList(ref.id) };
    if (ref.own) return [other];
    const off = L.api.connected() ? null : t("Gerät nicht verbunden");
    const ask = kind => () => { request(ref.id, kind); if (where === "map") showInList(ref.id); };
    return [
      { label: t("Direktnachricht"), short: t("Nachricht"), icon: "message", group: G.main, run: () => L.api.message(ref.id) },
      { label: t("Traceroute"), icon: "route", group: G.radio, radio: true, disabled: off, run: ask("traceroute") },
      { label: t("Position anfragen"), short: t("Position"), icon: "locate", group: G.radio, radio: true, disabled: off, run: ask("position") },
      other,
    ];
  });
  poll();
}

// The node's row in the inspector's list, scrolled into view and briefly highlighted.
function showInList(id) {
  L.api.openList();
  if (L.filter) { L.filter = ""; const f = $("#nlFilter"); if (f) f.value = ""; fill(); }
  L.open = id;
  L.flash = { id, until: Date.now() + 2000 };  // survives the redraws of the next polls
  fill();
  document.querySelector(`#t_nodes [data-focus="${CSS.escape(id)}"]`)?.closest("tr")
    ?.scrollIntoView({ block: "center", behavior: "smooth" });
}

// Results of the requests; while one runs, again shortly. A position that came back is on the
// map only after the node layer reloads, so reload it right away.
async function poll() {
  clearTimeout(L.timer);
  const prev = L.req;
  try { L.req = (await getJSON("api/nodes/requests")).requests; } catch (_) { return; }
  const arrived = Object.entries(L.req).some(([id, r]) =>
    r.position && r.position.state === "ok" && prev[id] && prev[id].position && prev[id].position.state === "läuft");
  if (arrived) L.api.refreshNodes();
  fill();
  if (Object.values(L.req).some(r => Object.values(r).some(x => x.state === "läuft"))) L.timer = setTimeout(poll, POLL_MS);
}

async function request(id, kind) {
  try { await postJSON(`api/nodes/${encodeURIComponent(id)}/${kind}`, {}); }
  catch (e) { L.api.toast(e.message, { bad: true }); }
  poll();
}

const clock = ts => new Date(ts * 1000).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });

// "HOME → R1 (6,5 dB) → SIM (3,0 dB)": the first node has no SNR (it sends).
function chain(hops) {
  return hops.map((h, i) => (h.id ? h.short || h.id : t("unbekannt"))
    + (i ? ` (${h.snr === null ? "?" : fmt(h.snr, 1)} dB)` : "")).join(" → ");
}

// A route can be drawn when at least two of its nodes had a position.
function drawable(r) {
  return new Set([...r.towards, ...(r.back || [])].filter(h => h.lat != null).map(h => h.id)).size >= 2;
}

function traceLines(r, id, key) {
  if (r.state === "läuft") return [`<span class="run">${esc(t("Traceroute läuft …"))}</span>`];
  if (r.state === "keine Antwort") return [`<span class="bad">${esc(t("Traceroute um {time}: keine Antwort", { time: clock(r.at) }))}</span>`];
  if (r.state === "Fehler") return [`<span class="bad">${esc(t("Traceroute um {time} fehlgeschlagen: {error}", { time: clock(r.at), error: r.error }))}</span>`];
  const on = L.shown === key;
  const btn = drawable(r)
    ? `<button class="lnk" data-route="${esc(key)}">${on ? t("von der Karte nehmen") : t("auf der Karte zeigen")}</button>`
    : `<span class="sub">${t("nicht auf der Karte: zu wenige Knoten mit Position")}</span>`;
  return [`${esc(t("Traceroute um {time}", { time: clock(r.at) }))} ${btn}`,
    esc(t("Hin: {route}", { route: chain(r.towards) })),
    esc(r.back ? t("Zurück: {route}", { route: chain(r.back) }) : t("Rückweg nicht aufgezeichnet (ältere Firmware)"))];
}

function positionLines(r, id) {
  if (r.state === "läuft") return [`<span class="run">${esc(t("Position angefragt …"))}</span>`];
  if (r.state === "keine Antwort") return [`<span class="bad">${esc(t("Positionsanfrage um {time}: keine Antwort", { time: clock(r.at) }))}</span>`];
  return [`${esc(t("Position erhalten um {time}", { time: clock(r.done) }))} <button class="lnk" data-focus="${esc(id)}">${t("auf der Karte zeigen")}</button>`];
}

// The result rows under a node; ✕ hides one until the next request.
function resultRows(n) {
  const r = L.req[n.id] || {};
  return [["traceroute", traceLines], ["position", positionLines]].map(([kind, lines]) => {
    const x = r[kind], key = `${n.id}|${kind}|${x && x.at}`;
    if (!x || L.hidden.has(key)) return "";
    return `<tr class="nl-req"><td colspan="4"><div class="nl-res"><div>${lines(x, n.id, key).map(l => `<div>${l}</div>`).join("")}</div>
      ${x.state === "läuft" ? "" : `<button class="btn small quiet" data-hide="${esc(key)}" aria-label="${t("Ergebnis ausblenden")}" title="${t("Ergebnis ausblenden")}">${symbolSVG("close")}</button>`}</div></td></tr>`;
  }).join("");
}

// data: the nodes layer payload (features, nodes, note); null when the layer is off.
export function renderNodeList(data) {
  L.data = data;
  if (!L.built || !$("#t_nodes .nl-body")) build();
  fill();
}

function build() {
  $("#t_nodes").innerHTML = `<div class="sec">
      <div class="nl-hd"><input type="search" id="nlFilter" placeholder="${t("Name oder ID filtern")}" aria-label="${t("Knoten filtern")}">
        <label class="inline">${t("Sortieren")}<select id="nlSort">${Object.entries(SORTS).map(([k, [l]]) => `<option value="${k}">${l()}</option>`).join("")}</select></label></div>
      <p class="note nl-count" style="margin:6px 0 4px"></p>
      <div class="wrap nl-body"></div>
      <p class="note">${t("Gleiche Quelle und Filter wie die Ebene „Meshtastic-Knoten“ (⚙ dort). Klick auf einen Knoten zeigt seine Aktionen (dieselben wie im Popup auf der Karte) und rückt ihn auf der Karte ins Bild.")}
        ${t("Traceroute und Positionsanfrage gehen über das Funknetz, auf dem Kanal, auf dem der Knoten gehört wurde; das Ergebnis steht unter dem Knoten.")}</p></div>`;
  $("#nlFilter").value = L.filter;
  $("#nlSort").value = L.sort;
  $("#nlFilter").addEventListener("input", e => { L.filter = e.target.value; fill(); });
  $("#nlSort").addEventListener("change", e => { L.sort = e.target.value; L.api.store.set("nodes.sort", L.sort); fill(); });
  L.built = true;
}

function age(ts) {
  if (!ts) return "–";
  const s = Date.now() / 1000 - ts;
  if (s < 90) return t("gerade");
  if (s < 5400) return `${Math.round(s / 60)} min`;
  if (s < 172800) return `${Math.round(s / 3600)} h`;
  return new Date(ts * 1000).toLocaleDateString(locale, { day: "2-digit", month: "2-digit" });
}

function fill() {
  const body = $("#t_nodes .nl-body"); if (!body) return;
  const nodes = (L.data && L.data.nodes) || [];
  const q = L.filter.trim().toLowerCase();
  const shown = nodes.filter(n => !q || [n.id, n.short, n.long, n.hw].some(v => (v || "").toLowerCase().includes(q)))
    .sort(SORTS[L.sort][1]);
  const withPos = nodes.filter(n => n.lat != null).length;
  // the drawn route goes with its result: a new traceroute or ✕ takes it off the map
  if (L.shown && !shownTrace()) { L.shown = null; L.api.showRoute(null); }
  $("#t_nodes .nl-count").textContent = t("{n} Knoten, {m} mit Position", { n: nodes.length, m: withPos })
    + (q ? " · " + t("{n} passen zum Filter", { n: shown.length }) : "");
  body.innerHTML = shown.length ? `<table class="nodes"><tr><th>${t("Knoten")}</th><th>${t("Hops")}</th><th>SNR</th><th>${t("gehört")}</th></tr>
    ${shown.map(n => `<tr class="${n.own ? "own" : ""}${L.open === n.id ? " open" : ""}${L.flash && L.flash.id === n.id && Date.now() < L.flash.until ? " flash" : ""}">
      <td><button class="lnk nodebtn" data-focus="${esc(n.id)}" aria-expanded="${L.open === n.id}" title="${t("Aktionen zeigen")}">
        <span class="sn">${esc(n.short || n.id.slice(-4))}</span> ${esc(n.long || n.id)}</button>
        <div class="sub">${esc(n.id)}${n.hw ? " · " + esc(n.hw) : ""}${n.battery != null ? " · " + t("Akku {n} %", { n: n.battery }) : ""}${n.own ? " · " + t("eigenes Gerät") : ""}</div></td>
      <td class="n">${n.hops ?? "–"}</td><td class="n">${n.snr != null ? fmt(n.snr, 1) : "–"}</td><td class="n">${age(n.last)}</td></tr>
      ${L.open === n.id ? `<tr class="nl-tools"><td colspan="4" data-tools="${esc(n.id)}">${toolbarHTML(rowActions(n))}</td></tr>` : ""}
      ${resultRows(n)}`).join("")}
    </table>` : `<p class="note">${nodes.length ? t("Kein Knoten passt zum Filter.") : esc((L.data && L.data.note) || t("Keine Knoten."))}</p>`;
  // a click on the name opens the row's toolbar (one at a time) and brings the node into view
  body.querySelectorAll("[data-focus]").forEach(b => b.addEventListener("click", () => {
    const id = b.dataset.focus;
    L.open = L.open === id ? null : id;
    if (L.open) L.api.panTo(id);
    fill();
    body.querySelector(`[data-focus="${CSS.escape(id)}"]`)?.focus();
  }));
  body.querySelectorAll("[data-tools]").forEach(cell => {
    const n = nodes.find(x => x.id === cell.dataset.tools);
    if (n) bindToolbar(cell, rowActions(n), n.long || n.id);
  });
  body.querySelectorAll("[data-hide]").forEach(b => b.addEventListener("click", () => { L.hidden.add(b.dataset.hide); fill(); }));
  body.querySelectorAll("[data-route]").forEach(b => b.addEventListener("click", () => {
    L.shown = L.shown === b.dataset.route ? null : b.dataset.route;
    L.api.showRoute(shownTrace());
    fill();
  }));
}

// The same actions as the node's popup on the map, in the list's variant.
function rowActions(n) {
  return actionsFor({ type: "node", id: n.id, own: !!n.own, hasPos: n.lat != null }, L.api.feature(n.id), "list");
}

// The traceroute of L.shown while its result is still there and not hidden, else null.
function shownTrace() {
  if (!L.shown || L.hidden.has(L.shown)) return null;
  const [id, , at] = L.shown.split("|");
  const r = L.req[id] && L.req[id].traceroute;
  return r && r.state === "ok" && String(r.at) === at ? r : null;
}
