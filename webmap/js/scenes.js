// Scene manager inside the "Laserscan-Szene" layer settings: switch the scene in use, delete
// scenes and the downloaded tiles, create a new scene (click its centre on the map, choose the
// size). The form lists the tiles the area needs; the owner downloads the missing ones by hand
// into data/sim/laz/ (the app never downloads, see mapapp/scenes.py), then the server builds the
// scene as a background task. Switching reloads the page: the 3D view is built for one scene.
import { t } from "./i18n.js";
import { esc, fmt, getJSON, postJSON } from "./util.js";

const E = { data: null, plan: null, form: null, msg: "", api: null, box: null };
const SIZES = [1, 2, 3, 4, 5];

// api: { pickOnMap(label, cb), tempArea(points) (null clears), taskStarted(job), toast(msg, opts) }
export function initScenes(api) { E.api = api; }

export async function renderScenes(box) {
  E.box = box;
  try { E.data = await getJSON("api/scenes"); E.msg = ""; } catch (e) { E.msg = e.message; }
  draw();
}

const gb = mb => mb >= 1000 ? `${fmt(mb / 1000, 1)} GB` : `${fmt(mb, 0)} MB`;

function draw() {
  const box = E.box; if (!box || !box.isConnected) return;
  const d = E.data || { scenes: [], tiles_mb: 0 };
  box.innerHTML = `<div class="hd2">${t("Szenen")}</div>
    <div class="sitelist">${d.scenes.map(rowHTML).join("") || `<p class="note" style="margin:0">${t("Noch keine Szene.")}</p>`}</div>
    ${E.form ? formHTML() : ""}
    <div class="msg" role="alert">${esc(E.msg)}</div>
    ${E.form ? "" : `<button class="btn small" data-new>＋ ${t("Neue Szene")}</button>`}
    ${d.tiles_mb > 0 && !E.form ? `<div class="row2"><span class="note" style="margin:0">${esc(t("Heruntergeladene Kacheln: {size}", { size: gb(d.tiles_mb) }))}</span>
      <button class="btn small" data-tiles title="${esc(t("Werden nur zum Bauen gebraucht; die fertigen Szenen bleiben."))}">${t("Kacheln löschen")}</button></div>` : ""}`;
  box.querySelectorAll("[data-use]").forEach(b => b.addEventListener("click", () => use(b.dataset.use)));
  box.querySelectorAll("[data-del]").forEach(b => b.addEventListener("click", () => remove(b.dataset.del)));
  box.querySelector("[data-new]")?.addEventListener("click", startNew);
  box.querySelector("[data-tiles]")?.addEventListener("click", deleteTiles);
  if (E.form) bindForm();
}

function rowHTML(s) {
  const n = esc(s.name);
  const sub = [`${fmt(s.size_km[0], 1)} × ${fmt(s.size_km[1], 1)} km`, gb(s.mb), (s.created || "").slice(0, 10)].filter(Boolean).join(" · ");
  return `<div class="site"><div class="txt"><span class="nm">${n}${s.active ? ` <span class="chip ok">${t("verwendet")}</span>` : ""}</span><span class="sub">${esc(sub)}</span></div>
    ${s.active ? "" : `<button class="btn small" data-use="${n}" title="${t("Diese Szene verwenden (lädt die Seite neu)")}">${t("Verwenden")}</button>`}
    <button class="btn small" data-del="${n}" aria-label="${esc(t("{name} löschen", { name: s.name }))}" title="${t("Löschen")}">✕</button></div>`;
}

function formHTML() {
  const f = E.form, p = E.plan;
  const exists = E.data && E.data.scenes.some(s => s.name === f.name);
  const planText = !p ? "" : p.missing.length
    ? t("{n} Kacheln, {have} schon da. Es fehlen {missing} (etwa {size}, frei: {free}):", { n: p.tiles, have: p.present, missing: p.missing.length, size: gb(p.download_mb), free: gb(p.free_mb) })
    : t("{n} Kacheln, alle schon da.", { n: p.tiles });
  const missingHTML = !p || !p.missing.length ? "" : `
    <pre class="log tiles">${esc(p.missing.join("\n"))}</pre>
    <p class="note" style="margin:0">${esc(t("Selbst herunterladen (Browser oder Download-Programm) und unverändert in diesen Ordner legen:"))} <code>${esc(p.folder)}</code>
      · <a href="${esc(p.source)}" target="_blank" rel="noopener">${t("Download-Ordner von Geobasis NRW")}</a>
      ${p.present ? esc(t("Was fehlt, wird interpoliert.")) : ""}</p>
    <div class="row2"><button class="btn small" data-copy>${t("Liste kopieren")}</button><button class="btn small" data-recheck>${t("Erneut prüfen")}</button></div>`;
  return `<div class="siteform">
    <label>${t("Name")}<input type="text" data-f="name" value="${esc(f.name)}" maxlength="32" placeholder="${t("z. B. innenstadt")}"></label>
    <div class="row2"><label>${t("Kantenlänge")}<select data-f="size">${SIZES.map(k => `<option value="${k}" ${k === f.size ? "selected" : ""}>${k} km</option>`).join("")}</select></label>
      <div class="note pos">${fmt(f.lat, 5)}, ${fmt(f.lon, 5)}</div></div>
    <label class="tog"><input type="checkbox" data-f="activate" ${f.activate ? "checked" : ""}>${t("Danach verwenden")}</label>
    <p class="note" style="margin:0" data-replace>${exists ? esc(t("Die Szene „{name}“ wird ersetzt.", { name: f.name })) : ""}</p>
    <p class="note" style="margin:0">${esc(planText)} ${t("Nur Nordrhein-Westfalen.")}</p>
    ${missingHTML}
    <div class="row2"><button class="btn small" data-cancel>${t("Abbrechen")}</button><button class="btn small on" data-create ${p && !p.present ? "disabled" : ""}>${t("Erstellen")}</button></div></div>`;
}

function bindForm() {
  const box = E.box;
  // the hint is updated in place: a redraw on blur would swallow a click on "Erstellen"
  box.querySelector("[data-f=name]").addEventListener("input", e => {
    E.form.name = e.target.value.trim();
    const exists = E.data && E.data.scenes.some(s => s.name === E.form.name);
    box.querySelector("[data-replace]").textContent = exists ? t("Die Szene „{name}“ wird ersetzt.", { name: E.form.name }) : "";
  });
  box.querySelector("[data-f=size]").addEventListener("change", e => { E.form.size = +e.target.value; loadPlan(); });
  box.querySelector("[data-f=activate]").addEventListener("change", e => { E.form.activate = e.target.checked; });
  box.querySelector("[data-cancel]").addEventListener("click", cancel);
  box.querySelector("[data-recheck]")?.addEventListener("click", loadPlan);
  box.querySelector("[data-copy]")?.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(E.plan.missing.join("\n")); E.api.toast(t("Liste kopiert")); }
    catch (_) { E.api.toast(t("Kopieren nicht möglich: die Liste bitte markieren und kopieren."), { bad: true }); }
  });
  box.querySelector("[data-create]").addEventListener("click", create);
}

function startNew() {
  E.msg = "";
  E.api.pickOnMap(t("Mitte der neuen Szene"), (lat, lon) => {
    const first = !E.data || !E.data.scenes.length;
    E.form = { lat, lon, size: 3, name: first ? "home" : "", activate: true };
    E.plan = null;
    draw(); loadPlan();
    E.box.querySelector("[data-f=name]")?.focus();
  });
}

async function loadPlan() {
  const f = E.form; if (!f) return;
  try {
    E.plan = await getJSON(`api/scenes/plan?lat=${f.lat}&lon=${f.lon}&size=${f.size}`);
    E.api.tempArea(E.plan.ring);
  } catch (e) { E.msg = e.message; }
  draw();
}

function cancel() { E.form = null; E.plan = null; E.msg = ""; E.api.tempArea(null); draw(); }

async function create() {
  const f = E.form;
  const exists = E.data && E.data.scenes.some(s => s.name === f.name);
  try {
    const job = await postJSON("api/jobs", { kind: "scene", params: {
      name: f.name, center: `${f.lat.toFixed(5)}, ${f.lon.toFixed(5)}`, size: f.size, activate: f.activate, overwrite: exists } });
    E.form = null; E.plan = null; E.msg = ""; E.api.tempArea(null);
    E.api.taskStarted(job);
    draw();
  } catch (e) { E.msg = e.message; draw(); }
}

async function use(name) {
  try {
    await postJSON("api/scenes/activate", { name });
    E.api.toast(t("Szene {name} wird verwendet", { name }));
    location.reload();
  } catch (e) { E.msg = e.message; draw(); }
}

async function remove(name) {
  const s = E.data.scenes.find(x => x.name === name);
  const q = s && s.active ? t("Szene {name} löschen? Sie wird gerade verwendet; danach gilt die nächste (falls es eine gibt).", { name })
    : t("Szene {name} löschen?", { name });
  if (!confirm(q)) return;
  try {
    E.data = await postJSON("api/scenes/delete", { name });
    E.api.toast(t("Szene {name} gelöscht", { name }));
    if (s && s.active) location.reload(); else draw();
  } catch (e) { E.msg = e.message; draw(); }
}

async function deleteTiles() {
  if (!confirm(t("Alle heruntergeladenen Laserscan-Kacheln löschen ({size})? Die Szenen bleiben; für einen Neubau müssen die Kacheln erneut heruntergeladen werden.", { size: gb(E.data.tiles_mb) }))) return;
  try { E.data = await postJSON("api/scenes/delete_tiles", {}); E.msg = ""; draw(); } catch (e) { E.msg = e.message; draw(); }
}
