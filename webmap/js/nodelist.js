// Node list in the inspector ("Knoten"): the same nodes as the layer "Meshtastic-Knoten"
// (same source, same age filter), including those without a position. A click on a node with a
// position shows it on the map; ✉ opens a direct conversation in the messaging pane.
import { $, esc, fmt } from "./util.js";

const SORTS = {
  last: ["zuletzt gehört", (a, b) => (b.last || 0) - (a.last || 0)],
  hops: ["Hops", (a, b) => (a.hops ?? 99) - (b.hops ?? 99) || (b.last || 0) - (a.last || 0)],
  snr: ["SNR", (a, b) => (b.snr ?? -99) - (a.snr ?? -99)],
  name: ["Name", (a, b) => (a.long || a.short || a.id).localeCompare(b.long || b.short || b.id, "de")],
};
const L = { api: null, data: null, filter: "", sort: "last", built: false };

// api: { store, focusNode(id), message(id) }
export function initNodeList(api) {
  L.api = api;
  L.sort = api.store.get("nodes.sort", "last");
}

// data: the nodes layer payload (features, nodes, note); null when the layer is off.
export function renderNodeList(data) {
  L.data = data;
  if (!L.built || !$("#t_nodes .nl-body")) build();
  fill();
}

function build() {
  $("#t_nodes").innerHTML = `<div class="sec">
      <div class="nl-hd"><input type="search" id="nlFilter" placeholder="Name oder ID filtern" aria-label="Knoten filtern">
        <label class="inline">Sortieren<select id="nlSort">${Object.entries(SORTS).map(([k, [l]]) => `<option value="${k}">${l}</option>`).join("")}</select></label></div>
      <p class="note nl-count" style="margin:6px 0 4px"></p>
      <div class="wrap nl-body"></div>
      <p class="note">Gleiche Quelle und Filter wie die Ebene „Meshtastic-Knoten“ (⚙ dort). Klick auf einen Knoten
        mit Position zeigt ihn auf der Karte, ✉ schreibt ihm direkt.</p></div>`;
  $("#nlFilter").value = L.filter;
  $("#nlSort").value = L.sort;
  $("#nlFilter").addEventListener("input", e => { L.filter = e.target.value; fill(); });
  $("#nlSort").addEventListener("change", e => { L.sort = e.target.value; L.api.store.set("nodes.sort", L.sort); fill(); });
  L.built = true;
}

function age(t) {
  if (!t) return "–";
  const s = Date.now() / 1000 - t;
  if (s < 90) return "gerade";
  if (s < 5400) return `${Math.round(s / 60)} min`;
  if (s < 172800) return `${Math.round(s / 3600)} h`;
  return new Date(t * 1000).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" });
}

function fill() {
  const body = $("#t_nodes .nl-body"); if (!body) return;
  const nodes = (L.data && L.data.nodes) || [];
  const q = L.filter.trim().toLowerCase();
  const shown = nodes.filter(n => !q || [n.id, n.short, n.long, n.hw].some(v => (v || "").toLowerCase().includes(q)))
    .sort(SORTS[L.sort][1]);
  const withPos = nodes.filter(n => n.lat != null).length;
  $("#t_nodes .nl-count").textContent = `${nodes.length} Knoten, ${withPos} mit Position` + (q ? ` · ${shown.length} passen zum Filter` : "");
  body.innerHTML = shown.length ? `<table class="nodes"><tr><th>Knoten</th><th>Hops</th><th>SNR</th><th>gehört</th><th></th></tr>
    ${shown.map(n => `<tr class="${n.own ? "own" : ""}">
      <td><button class="lnk nodebtn" data-focus="${esc(n.id)}" ${n.lat == null ? "disabled title=\"keine Position\"" : "title=\"auf der Karte zeigen\""}>
        <span class="sn">${esc(n.short || n.id.slice(-4))}</span> ${esc(n.long || n.id)}</button>
        <div class="sub">${esc(n.id)}${n.hw ? " · " + esc(n.hw) : ""}${n.battery != null ? ` · Akku ${n.battery} %` : ""}${n.own ? " · eigenes Gerät" : ""}</div></td>
      <td class="n">${n.hops ?? "–"}</td><td class="n">${n.snr != null ? fmt(n.snr, 1) : "–"}</td><td class="n">${age(n.last)}</td>
      <td>${n.own ? "" : `<button class="btn small" data-msg="${esc(n.id)}" aria-label="${esc(n.long || n.id)} direkt schreiben" title="Direktnachricht">✉</button>`}</td></tr>`).join("")}
    </table>` : `<p class="note">${nodes.length ? "Kein Knoten passt zum Filter." : esc((L.data && L.data.note) || "Keine Knoten.")}</p>`;
  body.querySelectorAll("[data-focus]").forEach(b => b.addEventListener("click", () => L.api.focusNode(b.dataset.focus)));
  body.querySelectorAll("[data-msg]").forEach(b => b.addEventListener("click", () => L.api.message(b.dataset.msg)));
}
