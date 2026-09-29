// Editor for the own sites, inside the "Eigene Standorte" layer settings: add (click on the map),
// move, edit, rename, delete. The server writes data/sim/sites.json (with a backup) and refuses
// to delete a site that variants, scenarios or the corridor still use.
import { esc, fmt, getJSON, postJSON } from "./util.js";

const E = { sites: [], edit: null, msg: "", api: null, box: null };

// api: { pickOnMap(label, cb), tempMarker(lat, lon) (null clears), changed(), toast(msg, opts) }
export function initSites(api) { E.api = api; }

export async function renderSites(box) {
  E.box = box;
  try { E.sites = (await getJSON("api/sites")).sites; } catch (e) { E.msg = e.message; }
  draw();
}

function draw() {
  const box = E.box; if (!box || !box.isConnected) return;
  const ed = E.edit;
  box.innerHTML = `<div class="hd2">Standorte bearbeiten</div>
    <div class="sitelist">${E.sites.map(s => ed && !ed.isNew && ed.orig === s.name ? formHTML(ed) : rowHTML(s)).join("")
      || `<p class="note" style="margin:0">Noch keine Standorte.</p>`}</div>
    ${ed && ed.isNew ? formHTML(ed) : ""}
    <div class="msg" role="alert">${esc(E.msg)}</div>
    ${ed ? "" : `<button class="btn small" data-add>＋ Neuer Standort</button>`}`;
  box.querySelectorAll("[data-edit]").forEach(b => b.addEventListener("click", () => startEdit(b.dataset.edit)));
  box.querySelectorAll("[data-move]").forEach(b => b.addEventListener("click", () => move(b.dataset.move)));
  box.querySelectorAll("[data-del]").forEach(b => b.addEventListener("click", () => remove(b.dataset.del)));
  const add = box.querySelector("[data-add]"); if (add) add.addEventListener("click", startAdd);
  const save = box.querySelector("[data-save]"); if (save) save.addEventListener("click", saveEdit);
  const cancel = box.querySelector("[data-cancel]"); if (cancel) cancel.addEventListener("click", cancelEdit);
}

function rowHTML(s) {
  const sub = s.same_as ? `Variante von ${esc(s.same_as)}, ${fmt(s.height_m[0], 1)}–${fmt(s.height_m[1], 1)} m`
    : `${fmt(s.height_m[0], 1)}–${fmt(s.height_m[1], 1)} m ü. Grund`;
  const n = esc(s.name);
  return `<div class="site"><div class="txt"><span class="nm">${n}</span><span class="sub" title="${esc(s.description)}">${sub}</span></div>
    <button class="btn small" data-edit="${n}" aria-label="${n} bearbeiten" title="Bearbeiten">✎</button>
    ${s.same_as ? "" : `<button class="btn small" data-move="${n}" aria-label="${n} verschieben" title="Verschieben: Klick in die Karte">⌖</button>`}
    <button class="btn small" data-del="${n}" aria-label="${n} löschen" title="${s.refs.length ? "Wird verwendet: " + esc(s.refs.join(", ")) : "Löschen"}">✕</button></div>`;
}

function formHTML(ed) {
  const num = (k, label, step) => `<label>${label}<input type="number" step="${step}" data-f="${k}" value="${ed[k] ?? ""}"></label>`;
  return `<div class="siteform">
    <label>Name<input type="text" data-f="name" value="${esc(ed.name)}" maxlength="24" placeholder="z. B. DACH_NORD"></label>
    <label>Beschreibung<input type="text" data-f="description" value="${esc(ed.description)}" maxlength="200"></label>
    <div class="row2">${num("h0", "Höhe min [m]", 0.1)}${num("h1", "Höhe max [m]", 0.1)}</div>
    ${ed.variant ? "" : `<div class="row2">${num("clutter", "Umgebung [m]", 0.5)}<div class="note pos">${fmt(ed.lat, 5)}, ${fmt(ed.lon, 5)}</div></div>`}
    ${ed.hint ? `<p class="note" style="margin:0">${esc(ed.hint)}</p>` : ""}
    <div class="row2"><button class="btn small" data-cancel>Abbrechen</button><button class="btn small on" data-save>Speichern</button></div></div>`;
}

function readForm() {
  const ed = E.edit;
  E.box.querySelectorAll("[data-f]").forEach(inp => {
    ed[inp.dataset.f] = inp.type === "number" ? (inp.value === "" ? null : +inp.value) : inp.value.trim();
  });
}

function startAdd() {
  E.msg = "";
  E.api.pickOnMap("Position des neuen Standorts", async (lat, lon) => {
    let sug = { clutter_m: 12, in_scene: false };
    try { sug = await getJSON(`api/sites/suggest?lat=${lat}&lon=${lon}`); } catch (_) { }
    E.edit = { isNew: true, name: "", description: "", h0: 2, h1: 4, clutter: sug.clutter_m, lat, lon,
      hint: sug.in_scene ? `Umgebung aus dem Laserscan: höchste Oberfläche im Umkreis von 10 m (${fmt(sug.clutter_m, 1)} m).`
        : "Außerhalb des Laserscans: Umgebung bitte schätzen (Dach- oder Baumhöhe rundum)." };
    E.api.tempMarker(lat, lon);
    draw();
    const name = E.box.querySelector("[data-f=name]"); if (name) name.focus();
  });
}
function startEdit(name) {
  const s = E.sites.find(x => x.name === name); if (!s) return;
  E.msg = "";
  E.edit = { isNew: false, orig: s.name, name: s.name, description: s.description, h0: s.height_m[0], h1: s.height_m[1],
    clutter: s.clutter_m, lat: s.lat, lon: s.lon, variant: !!s.same_as };
  draw();
}
function cancelEdit() { E.edit = null; E.msg = ""; E.api.tempMarker(null); draw(); }

async function saveEdit() {
  readForm();
  const ed = E.edit;
  try {
    if (ed.isNew) {
      await postJSON("api/sites/add", { name: ed.name, lat: ed.lat, lon: ed.lon, height_m: [ed.h0, ed.h1],
        clutter_m: ed.clutter, description: ed.description });
      E.api.toast(`Standort ${ed.name} angelegt`);
    } else {
      let name = ed.orig;
      if (ed.name !== ed.orig) { await postJSON("api/sites/rename", { name, new_name: ed.name }); name = ed.name; }
      const fields = { description: ed.description, height_m: [ed.h0, ed.h1] };
      if (!ed.variant) fields.clutter_m = ed.clutter;
      await postJSON("api/sites/update", { name, fields });
      E.api.toast(`Standort ${name} gespeichert`);
    }
    E.edit = null; E.msg = ""; E.api.tempMarker(null);
    E.api.changed();
  } catch (e) {
    E.msg = e.message; draw();
    const box = E.box.querySelector(".siteform"); if (box) readBack(box, ed);
  }
}
// after a failed save the form was redrawn: keep what the user typed
function readBack(box, ed) {
  box.querySelectorAll("[data-f]").forEach(inp => { const v = ed[inp.dataset.f]; if (v !== undefined && v !== null) inp.value = v; });
}

function move(name) {
  E.msg = "";
  E.api.pickOnMap(`Neue Position für ${name}`, async (lat, lon) => {
    try {
      await postJSON("api/sites/update", { name, fields: { lat, lon } });
      E.api.toast(`${name} verschoben`);
      E.api.changed();
    } catch (e) { E.msg = e.message; draw(); }
  });
}

async function remove(name) {
  const s = E.sites.find(x => x.name === name);
  if (s && s.refs.length) { E.msg = `${name} wird noch verwendet: ${s.refs.join(", ")}. Erst dort ändern.`; draw(); return; }
  if (!confirm(`Standort ${name} löschen? (Eine Sicherung bleibt als sites.json.bak.)`)) return;
  try {
    await postJSON("api/sites/delete", { name });
    E.api.toast(`Standort ${name} gelöscht`);
    E.api.changed();
  } catch (e) { E.msg = e.message; draw(); }
}
