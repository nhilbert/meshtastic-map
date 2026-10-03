// Coverage walks of both kinds, handled the same way. Active: the home node measures the
// tracker (traceroutes started here, or the tracker's positions in the packet log); a walk is a
// day with such a log, shown by the layer "Rundgang (Messung)". Passive: what the carried device
// heard (GPX track + the Meshtastic app's CSV export), shown by the layer "Mesh-Empfang (passiv)".
// View Rundgänge, a section per kind: a note, the buttons that bring a walk in, the list of
// walks (each with Löschen and its choices) and the options of the kind's layer.
// View Ebenen, in the card of each walk layer: an eye per walk; the layer shows one walk at a
// time, so the open eye is the walk on the map.
// The server lists, pairs, checks and deletes the files (walks_store.py, heard_store.py).
import { locale, t } from "./i18n.js";
import { G, registerActions } from "./actions.js";
import { bindInputs, inputsHTML } from "./forms.js";
import { iconButton, symbolSVG } from "./icons.js";
import { $, esc, getJSON, postJSON } from "./util.js";

const SHOWN = 6;  // walks listed per kind before "ältere anzeigen"
// own: the layer's settings that say which walk it shows; the eyes and the rows set them, the
// options form has the rest. pick (active): tracker and track chosen per day, { date: { tracker, gpx } }
const W = {
  api: null,
  active: { list: "#walkList", opts: "#walkOpts", eyes: "#lyr_walk [data-eyes]", url: "api/walks", layer: "walk",
    own: ["date", "tracker", "gpx"], id: w => w.date, walks: [], msg: "", all: false, pick: {} },
  passive: { list: "#heardList", opts: "#heardOpts", eyes: "#lyr_heard [data-eyes]", url: "api/heard", layer: "heard",
    own: ["walk"], id: w => w.walk, walks: [], msg: "", all: false },
};
const KINDS = ["active", "passive"];

// api: { toast(msg, opts), status(msg, bad), fit([[south, west], [north, east]]), startProbe(),
//        layer(id) -> { name, settings, values, enabled } or null while the layers load,
//        setLayer(id, values, enabled) (enabled null: as it is) redraws the layer,
//        showLayer(id, values) the same after new files (reloads the layers' options),
//        changed() (walks and layer options are stale) }
export function initWalks(api) {
  W.api = api;
  $("#walkHead").innerHTML = `<p class="note" style="margin:0">${t("Der Heimknoten misst den Tracker: mit Traceroutes, die du hier startest, oder aus den Positionspaketen des Trackers im Paketlog. Die GPX-Spur des Handys gibt den Traceroutes ihren Ort.")}</p>
    <div class="acts"><button class="btn small" data-start>＋ ${t("Traceroute-Rundgang …")}</button>
      <button class="btn small" data-upload>${t("GPX-Spur hochladen …")}</button></div>`;
  $("#walkHead [data-start]").addEventListener("click", () => api.startProbe());
  $("#walkHead [data-upload]").addEventListener("click", () => uploadTrack());
  $("#heardHead").innerHTML = `<p class="note" style="margin:0">${t("GPX-Spur des Handys und CSV-Export der Meshtastic-App eines Rundgangs zusammen auswählen. Gesendet wird nichts.")}</p>
    <div class="acts"><button class="btn small" data-upload>＋ ${t("Rundgang hochladen …")}</button></div>`;
  $("#heardHead [data-upload]").addEventListener("click", pickFiles);
  // Candidate nodes of a relay byte are other people's nodes: no radio actions, only copying.
  registerActions("heard-candidate", ref => [
    { label: t("Koordinaten kopieren"), icon: "copy", group: G.copy, run: async () => {
      const text = `${ref.lat.toFixed(5)}, ${ref.lon.toFixed(5)}`;
      try { await navigator.clipboard.writeText(text); api.toast(t("Kopiert: {text}", { text })); }
      catch (_) { api.toast(text); }
    } },
  ]);
}

export async function renderWalks() {
  await Promise.all(KINDS.map(load));
  drawWalks();
}
// Everything again without asking the server: a walk layer was switched, set or redrawn.
export function drawWalks() {
  for (const kind of KINDS) { drawList(kind); drawEyes(kind); drawOptions(kind); }
}

async function load(kind) {
  const K = W[kind];
  try { K.walks = (await getJSON(K.url)).walks; K.msg = ""; } catch (e) { K.msg = e.message; }
}

const dayLabel = date => new Date(date + "T12:00").toLocaleDateString(locale);
function title(kind, w) {
  if (kind === "active") return dayLabel(w.date);
  if (!w.start) return w.walk;
  const a = new Date(w.start), b = new Date(w.end);
  const hm = d => d.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
  return `${a.toLocaleDateString(locale)} ${hm(a)}–${hm(b)}`;
}
const find = (kind, el) => W[kind].walks.find(w => W[kind].id(w) === el.closest("[data-walk]").dataset.walk);
const moreHTML = n => n > 0 ? `<button class="lnk manage" data-more>${esc(t("{n} ältere anzeigen", { n }))}</button>` : "";
const empty = () => `<p class="note" style="margin:0">${t("Noch keine Rundgänge.")}</p>`;

// ---------------------------------------------------------------- visibility (view Ebenen)
// Whether the kind's layer is on and set to this walk.
function visible(kind, w) {
  const L = W.api.layer(W[kind].layer);
  return !!L && L.enabled && (kind === "active" ? L.values.date === w.date : L.values.walk === w.walk);
}

function drawEyes(kind) {
  const K = W[kind], box = document.querySelector(K.eyes);
  if (!box) return;
  const shown = K.walks.filter((w, i) => K.all || i < SHOWN || visible(kind, w));
  box.innerHTML = shown.map(w => {
    const name = title(kind, w), on = visible(kind, w);
    const tip = on ? t("{name} ausblenden", { name }) : t("{name} anzeigen", { name });
    return `<button type="button" class="eye" data-walk="${esc(K.id(w))}" aria-pressed="${on}" title="${esc(tip)}">
      ${on ? symbolSVG("eye") : symbolSVG("eyeOff")}<span>${esc(name)}</span></button>`;
  }).join("") + moreHTML(K.walks.length - shown.length) || empty();
  box.querySelectorAll(".eye").forEach(b => b.addEventListener("click", () => {
    const w = find(kind, b);
    if (visible(kind, w)) W.api.setLayer(K.layer, {}, false); else show(kind, w);
    document.querySelector(`${K.eyes} [data-walk="${CSS.escape(K.id(w))}"]`)?.focus();
  }));
  box.querySelector("[data-more]")?.addEventListener("click", () => { K.all = true; drawWalks(); });
}

// Switch the kind's layer on with this walk. p: an active walk's tracker and track; reload:
// after new files, when the layer's options are stale.
function show(kind, w, { p = null, fit = true, reload = false } = {}) {
  if (!w) return;
  if (kind === "active") p = p || picked(w);
  const values = kind === "active" ? { date: w.date, tracker: p.tracker, gpx: p.gpx } : { walk: w.walk };
  if (reload) W.api.showLayer(W[kind].layer, values); else W.api.setLayer(W[kind].layer, values, true);
  const track = kind === "active" && w.tracks.find(x => x.name === values.gpx);
  const bbox = kind === "active" ? track && track.bbox : w.bbox;
  if (fit && bbox) W.api.fit(bbox);
}

// ---------------------------------------------------------------- list (view Rundgänge)
function drawList(kind) {
  const K = W[kind], box = $(K.list);
  const shown = K.all ? K.walks : K.walks.slice(0, SHOWN);
  box.innerHTML = `<div class="msg" role="alert">${esc(K.msg)}</div>
    <div class="sitelist">${shown.map(w => rowHTML(kind, w)).join("") || empty()}</div>
    ${moreHTML(K.walks.length - shown.length)}`;
  box.querySelectorAll("[data-del]").forEach(b => b.addEventListener("click", () => remove(kind, find(kind, b))));
  box.querySelectorAll("[data-choice]").forEach(s => s.addEventListener("change", () => choose(kind, find(kind, s), s.dataset.choice, s.value)));
  box.querySelector("[data-more]")?.addEventListener("click", () => { K.all = true; drawWalks(); });
}

// A walk's row: when it was, what it holds, Löschen, then its choices.
function rowHTML(kind, w) {
  const name = title(kind, w);
  const sub = kind === "active" ? t("{p} Traceroutes · {n} Positionen", { p: w.probes, n: w.positions })
    : t("{n} Pakete anderer Knoten · {name}", { n: w.packets, name: w.walk });
  return `<div class="site" style="flex-wrap:wrap" data-walk="${esc(W[kind].id(w))}">
    <div class="txt"><span class="nm">${esc(name)}</span><span class="sub" title="${esc(sub)}">${esc(sub)}</span></div>
    ${iconButton("trash", t("{name} löschen", { name }), "data-del", { danger: true })}
    ${kind === "active" ? activeChoices(w) : passiveChoices(w)}</div>`;
}
const option = (v, label, sel) => `<option value="${esc(v)}" ${v === sel ? "selected" : ""}>${esc(label)}</option>`;
const choice = (label, name, options) => `<label style="flex-basis:100%">${label}<select data-choice="${name}">${options}</select></label>`;

function activeChoices(w) {
  const p = picked(w);
  return choice(t("Tracker"), "tracker", w.trackers.map(([v, label]) => option(v, label, p.tracker)).join(""))
    + (w.tracks.length
      ? choice(t("GPX-Spur"), "gpx", option("", "—", p.gpx) + w.tracks.map(x => option(x.name, x.name, p.gpx)).join(""))
      : `<span class="sub" style="flex-basis:100%">${t("Keine GPX-Spur von diesem Tag.")}</span>`);
}
function passiveChoices(w) {
  const label = w.receiverGuessed ? t("Empfänger (eigenes Gerät, Vorschlag)") : t("Empfänger (eigenes Gerät)");
  return choice(label, "receiver", (w.receiver ? "" : option("", "—", ""))
    + w.senders.map(([id, name, n]) => option(id, `${name} ${id} (${n})`.trim(), w.receiver)).join(""));
}

// An active day's tracker and track: what the layer shows of it, else what was chosen in its
// row, else the first of each.
function picked(w) {
  const p = visible("active", w) ? W.api.layer("walk").values : W.active.pick[w.date] || {};
  return {
    tracker: w.trackers.some(([v]) => v === p.tracker) ? p.tracker : w.trackers[0][0],
    gpx: p.gpx === "" || w.tracks.some(x => x.name === p.gpx) ? p.gpx : (w.tracks[0] || { name: "" }).name,
  };
}

async function choose(kind, w, name, value) {
  if (kind === "active") {
    const p = W.active.pick[w.date] = { ...picked(w), [name]: value };
    if (visible(kind, w)) show(kind, w, { p, fit: false });  // the walk on the map follows at once
    return;
  }
  if (!value) return;
  try {
    await postJSON(`api/heard/${encodeURIComponent(w.walk)}/receiver`, { receiver: value });
    W.api.toast(t("Empfänger geändert: {node}", { node: value }));
    W.api.changed();
  } catch (e) { W.passive.msg = e.message; drawList("passive"); }
}

// ---------------------------------------------------------------- options (view Rundgänge)
// The settings of the kind's layer, except which walk it shows.
function drawOptions(kind) {
  const K = W[kind], box = $(K.opts), L = W.api.layer(K.layer);
  if (!L) { box.innerHTML = ""; return; }
  const settings = L.settings.filter(s => !K.own.includes(s.name));
  box.innerHTML = `<div class="hd2">${t("Darstellung und Auswertung")}</div>
    <p class="note" style="margin:0">${esc(t("Gilt für den Rundgang, den die Ebene „{layer}“ zeigt (Auge unter Ebenen).", { layer: L.name }))}</p>
    ${inputsHTML(settings, L.values, K.layer)}`;
  bindInputs(box, settings, L.values, () => W.api.setLayer(K.layer, {}, null));
}

// ---------------------------------------------------------------- delete
// Deleting asks first and names what goes: a track another walk uses stays.
function question(kind, w) {
  const tracks = kind === "active" ? w.tracks : [{ name: w.gpxName, shared: w.gpxShared }];
  const names = shared => tracks.filter(x => x.shared === shared).map(x => x.name).join(", ");
  const parts = [kind === "active"
    ? t("Rundgang vom {date} löschen? Gelöscht werden das Traceroute-Log und das Paketlog dieses Tages ({p} Traceroutes, {n} Positionspakete; das Paketlog enthält alle Pakete, die das Gerät an dem Tag empfangen hat).", { date: dayLabel(w.date), p: w.probes, n: w.positions })
    : t("Rundgang {name} löschen? Gelöscht werden die importierten Pakete ({n} Pakete anderer Knoten).", { name: title(kind, w), n: w.packets })];
  if (names(false)) parts.push(t("Auch gelöscht wird die GPX-Spur: {tracks}.", { tracks: names(false) }));
  if (names(true)) parts.push(t("Die GPX-Spur bleibt, weil ein anderer Rundgang sie nutzt: {tracks}.", { tracks: names(true) }));
  parts.push(t("Das lässt sich nicht rückgängig machen."));
  return parts.join("\n\n");
}

async function remove(kind, w) {
  if (!confirm(question(kind, w))) return;
  const K = W[kind];
  try {
    await postJSON(`${K.url}/${encodeURIComponent(K.id(w))}/delete`, {});
    W.api.toast(t("Rundgang gelöscht: {name}", { name: title(kind, w) }));
    W.api.changed();
  } catch (e) { K.msg = e.message; drawList(kind); }
}

// ---------------------------------------------------------------- uploads
const isoDay = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

// Upload the phone's GPX track and show the active walk of its day with it. After a traceroute
// walk (job: its task) that is the walk to the task's tracker on the day the task started.
export function uploadTrack(job = null) {
  const inp = document.createElement("input");
  inp.type = "file"; inp.accept = ".gpx,application/gpx+xml";
  inp.addEventListener("change", async () => {
    const f = inp.files[0]; if (!f) return;
    W.api.status(t("lade {name} hoch …", { name: f.name }));
    try {
      const r = await fetch(`api/tracks?name=${encodeURIComponent(f.name)}`, { method: "POST", body: f });
      const res = await r.json();
      if (!r.ok) throw new Error(res.error || `HTTP ${r.status}`);
      const t0 = Date.parse(res.start) / 1000, t1 = Date.parse(res.end) / 1000;
      if (job && job.started && (t1 < job.started || t0 > (job.ended || Date.now() / 1000)))
        W.api.toast(t("{name}: die Spur liegt zeitlich nicht im Rundgang – falsche Datei?", { name: res.name }), { bad: true });
      W.api.toast(t("{name}: {n} Punkte hochgeladen", { name: res.name, n: res.points }));
      W.api.status(t("bereit"));
      const date = isoDay(job ? new Date(job.started * 1000) : new Date(res.start));
      await load("active");
      const w = W.active.walks.find(x => x.date === date);
      if (!w) {
        drawWalks();
        W.api.toast(t("{name}: vom {date} gibt es kein Log, die Spur zeigt noch keinen Rundgang.", { name: res.name, date: dayLabel(date) }), { bad: true });
        return;
      }
      const p = { tracker: job ? "probe:" + job.params.to : picked(w).tracker, gpx: res.name };
      show("active", w, { p, reload: true });
    } catch (e) { W.api.toast(t("GPX-Upload: {error}", { error: e.message }), { bad: true }); W.api.status(e.message, true); }
  });
  inp.click();
}

// A passive walk: the GPX track and the app's CSV export, chosen together.
function pickFiles() {
  const inp = document.createElement("input");
  inp.type = "file"; inp.multiple = true; inp.accept = ".gpx,.csv";
  inp.addEventListener("change", () => importWalk([...inp.files]));
  inp.click();
}

async function importWalk(files) {
  if (!files.length) return;
  const K = W.passive;
  K.msg = t("lade {n} Dateien hoch …", { n: files.length }); drawList("passive");
  try {
    const payload = await Promise.all(files.map(async f => ({ name: f.name, text: await f.text() })));
    const res = await postJSON("api/heard", { files: payload });
    const who = res.receiver ? `${res.receiverName} ${res.receiver}`.trim() : "—";
    W.api.toast(res.receiverGuessed
      ? t("{walk}: {n} Pakete im Rundgang. Empfänger {node} (Vorschlag, in der Liste änderbar).", { walk: res.walk, n: res.inWalk, node: who })
      : t("{walk}: {n} Pakete im Rundgang. Empfänger {node}.", { walk: res.walk, n: res.inWalk, node: who }));
    if (res.offsetHint) W.api.toast(res.offsetHint, { bad: true });
    await load("passive");
    drawWalks();
    show("passive", K.walks.find(w => w.walk === res.walk), { reload: true });
  } catch (e) { K.msg = e.message; drawList("passive"); }
}
