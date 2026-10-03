// Passive walks (view Aufgaben, section "Rundgänge importieren"): upload a GPX track together
// with the Meshtastic app's CSV export, list the walks, set the receiving device, show a walk in
// the layer "Mesh-Empfang (passiv)". The server pairs and checks the files (heard_store.py).
import { locale, t } from "./i18n.js";
import { G, registerActions } from "./actions.js";
import { esc, getJSON, postJSON } from "./util.js";

const I = { walks: [], msg: "", api: null, box: null };

// api: { toast(msg, opts), showLayer(id, values), fit([[south, west], [north, east]]),
//        changed() (layer options are stale) }
export function initImports(api) {
  I.api = api;
  // Candidate nodes of a relay byte are other people's nodes: no radio actions, only copying.
  registerActions("heard-candidate", ref => [
    { label: t("Koordinaten kopieren"), icon: "copy", group: G.copy, run: async () => {
      const text = `${ref.lat.toFixed(5)}, ${ref.lon.toFixed(5)}`;
      try { await navigator.clipboard.writeText(text); api.toast(t("Kopiert: {text}", { text })); }
      catch (_) { api.toast(text); }
    } },
  ]);
}

export async function renderImports(box) {
  I.box = box;
  try { I.walks = (await getJSON("api/heard")).walks; I.msg = ""; } catch (e) { I.msg = e.message; }
  draw();
}

function draw() {
  const box = I.box; if (!box || !box.isConnected) return;
  box.innerHTML = `<p class="note" style="margin:0">${t("GPX-Spur des Handys und CSV-Export der Meshtastic-App eines Rundgangs zusammen auswählen. Gesendet wird nichts.")}</p>
    <button class="btn small" data-upload style="align-self:flex-start">＋ ${t("Rundgang hochladen …")}</button>
    <div class="msg" role="alert">${esc(I.msg)}</div>
    <div class="sitelist">${I.walks.map(rowHTML).join("") || `<p class="note" style="margin:0">${t("Noch keine Rundgänge.")}</p>`}</div>`;
  box.querySelector("[data-upload]").addEventListener("click", pickFiles);
  box.querySelectorAll("[data-show]").forEach(b => b.addEventListener("click", () =>
    show(I.walks.find(w => w.walk === b.dataset.show))));
  box.querySelectorAll("[data-recv]").forEach(s => s.addEventListener("change", () => setReceiver(s.dataset.recv, s.value)));
}

function show(w) {
  if (!w) return;
  I.api.showLayer("heard", { walk: w.walk });
  if (w.bbox) I.api.fit(w.bbox);
}

function when(w) {
  if (!w.start) return w.walk;
  const a = new Date(w.start), b = new Date(w.end);
  const hm = d => d.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
  return `${a.toLocaleDateString(locale)} ${hm(a)}–${hm(b)}`;
}

function rowHTML(w) {
  const opts = w.senders.map(([id, name, n]) =>
    `<option value="${esc(id)}" ${id === w.receiver ? "selected" : ""}>${esc(`${name} ${id} (${n})`.trim())}</option>`).join("");
  const none = w.receiver ? "" : `<option value="" selected>—</option>`;
  return `<div class="site" style="flex-wrap:wrap">
    <div class="txt"><span class="nm">${esc(when(w))}</span>
      <span class="sub" title="${esc(w.walk)}">${esc(t("{n} Pakete anderer Knoten · {name}", { n: w.packets, name: w.walk }))}</span></div>
    <button class="btn small" data-show="${esc(w.walk)}">${t("Anzeigen")}</button>
    <label style="flex-basis:100%">${w.receiverGuessed ? t("Empfänger (eigenes Gerät, Vorschlag)") : t("Empfänger (eigenes Gerät)")}
      <select data-recv="${esc(w.walk)}">${none}${opts}</select></label></div>`;
}

function pickFiles() {
  const inp = document.createElement("input");
  inp.type = "file"; inp.multiple = true; inp.accept = ".gpx,.csv";
  inp.addEventListener("change", () => upload([...inp.files]));
  inp.click();
}

async function upload(files) {
  if (!files.length) return;
  I.msg = t("lade {n} Dateien hoch …", { n: files.length }); draw();
  try {
    const payload = await Promise.all(files.map(async f => ({ name: f.name, text: await f.text() })));
    const res = await postJSON("api/heard", { files: payload });
    I.msg = "";
    const who = res.receiver ? `${res.receiverName} ${res.receiver}`.trim() : "—";
    I.api.toast(res.receiverGuessed
      ? t("{walk}: {n} Pakete im Rundgang. Empfänger {node} (Vorschlag, in der Liste änderbar).", { walk: res.walk, n: res.inWalk, node: who })
      : t("{walk}: {n} Pakete im Rundgang. Empfänger {node}.", { walk: res.walk, n: res.inWalk, node: who }));
    if (res.offsetHint) I.api.toast(res.offsetHint, { bad: true });
    await renderImports(I.box);
    show(I.walks.find(w => w.walk === res.walk));
  } catch (e) { I.msg = e.message; draw(); }
}

async function setReceiver(walk, receiver) {
  if (!receiver) return;
  try {
    await postJSON(`api/heard/${encodeURIComponent(walk)}/receiver`, { receiver });
    I.api.toast(t("Empfänger geändert: {node}", { node: receiver }));
    await renderImports(I.box);
    I.api.changed();
  } catch (e) { I.msg = e.message; draw(); }
}
