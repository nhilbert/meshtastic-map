// Background tasks: start forms built from the server's task kinds, the task list with progress,
// the log view in the inspector, and notifications when a task ends. The server runs the tasks;
// the page only polls /api/jobs (every 2 s while one is active, else every 8 s).
import { bindInputs, initialValues, inputsHTML } from "./forms.js";
import { locale, t } from "./i18n.js";
import { $, esc, fmt, getJSON, postJSON } from "./util.js";

const ACTIVE = ["läuft", "wartet"];
const STATE_CLASS = { "läuft": "run", wartet: "wait", fertig: "ok", Fehler: "bad", abgebrochen: "off" };
// States are stored as German codes and translated for display:
// t("läuft") t("wartet") t("fertig") t("Fehler") t("abgebrochen")
const T = { jobs: [], kinds: [], form: null, sel: null, timer: null, api: null, prev: null, detail: null };

// api: { store, toast(msg, opts), openInspector(tab), updateInspector(), onTransition(job, prev),
//        actions(job) -> [[label, fn], ...] extra buttons per task }
export function initTasks(api) {
  T.api = api;
  $("#btnJobs").addEventListener("click", () => {
    const sec = $("details[data-sec=jobs]"); sec.open = true; sec.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  loadKinds().then(renderStart).catch(e => { $("#jobStart").innerHTML = `<p class="msg">${esc(e.message)}</p>`; });
  poll();
}
export const selectedJob = () => (T.sel && T.jobs.find(j => j.id === T.sel)) || null;
export function pollSoon() { clearTimeout(T.timer); T.timer = setTimeout(poll, 300); }

async function loadKinds() { T.kinds = (await getJSON("api/jobs/kinds")).kinds; return T.kinds; }

function renderStart() {
  $("#jobStart").innerHTML = T.kinds.map(k =>
    `<button class="btn" data-kind="${k.id}" title="${esc(k.description)}">＋ ${esc(k.name)}</button>`).join("");
  $("#jobStart").querySelectorAll("[data-kind]").forEach(b => b.addEventListener("click", () => openForm(b.dataset.kind)));
}

// ---------------------------------------------------------------- start form
// Other parts of the page start a task kind through its form (the coordination mode's road
// graph download).
export function openTaskForm(kindId) {
  const sec = $("details[data-sec=jobs]"); sec.open = true;
  openForm(kindId);
}
async function openForm(kindId) {
  try { await loadKinds(); } catch (e) { T.api.toast(e.message, { bad: true }); return; }  // fresh nodes, sites
  const kind = T.kinds.find(k => k.id === kindId);
  T.form = { kind, values: initialValues(kind.settings, T.api.store.get("job." + kindId, null)) };
  renderForm();
  $("#jobForm").scrollIntoView({ behavior: "smooth", block: "nearest" });
}
function renderForm() {
  const box = $("#jobForm");
  if (!T.form) { box.innerHTML = ""; box.hidden = true; return; }
  const { kind, values } = T.form;
  box.hidden = false;
  box.innerHTML = `<div class="hd">${esc(kind.name)}</div>
    <p class="note" style="margin:0">${esc(kind.description)}</p>
    ${inputsHTML(kind.settings, values, "job")}
    <div class="msg" id="jobMsg" role="alert"></div>
    <div class="row2"><button class="btn" data-cancel>${t("Abbrechen")}</button><button class="btn on" data-start>${t("Starten")}</button></div>`;
  bindInputs(box, kind.settings, values, (_, rerender) => { if (rerender) renderForm(); });
  box.querySelector("[data-cancel]").addEventListener("click", () => { T.form = null; renderForm(); });
  box.querySelector("[data-start]").addEventListener("click", start);
}
async function start() {
  const { kind, values } = T.form, btn = $("#jobForm [data-start]");
  btn.disabled = true; $("#jobMsg").textContent = "";
  try {
    const job = await postJSON("api/jobs", { kind: kind.id, params: values });
    T.api.store.set("job." + kind.id, values);
    T.form = null; renderForm();
    T.sel = job.id;
    T.api.toast(t("Gestartet: {title}", { title: job.title }) + (job.state === "wartet" ? " " + t("(wartet auf die laufende Aufgabe)") : ""));
    pollSoon();
  } catch (e) {
    $("#jobMsg").textContent = e.message; btn.disabled = false;
  }
}

// ---------------------------------------------------------------- polling
async function poll() {
  clearTimeout(T.timer);
  try {
    const { jobs } = await getJSON("api/jobs");
    if (T.prev) for (const j of jobs) if (T.prev[j.id] && T.prev[j.id] !== j.state) T.api.onTransition(j, T.prev[j.id]);
    T.prev = Object.fromEntries(jobs.map(j => [j.id, j.state]));
    T.jobs = jobs;
    if (T.sel && !jobs.some(j => j.id === T.sel)) T.sel = null;
    renderList(); renderBadge();
    if (T.sel && !$("#t_job").hidden) await refreshDetail();
  } catch (e) {
    $("#jobList").innerHTML = `<p class="msg">${esc(t("Aufgaben nicht abrufbar: {error}", { error: e.message }))}</p>`;
  }
  T.timer = setTimeout(poll, T.jobs.some(j => ACTIVE.includes(j.state)) ? 2000 : 8000);
}

// ---------------------------------------------------------------- list
const clock = ts => ts ? new Date(ts * 1000).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" }) : "";
const day = ts => new Date(ts * 1000).toLocaleDateString(locale, { day: "2-digit", month: "2-digit" });
function duration(j) {
  if (!j.started) return "";
  const s = Math.max(0, Math.round((j.ended || Date.now() / 1000) - j.started));
  return s < 90 ? `${s} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${fmt(s / 3600, 1)} h`;
}
function progressHTML(j) {
  if (j.state !== "läuft") return "";
  const known = j.progress !== null && j.progress !== undefined;
  return `<div class="bar ${known ? "" : "ind"}" role="progressbar" ${known ? `aria-valuenow="${Math.round(j.progress * 100)}"` : ""}
    aria-valuemin="0" aria-valuemax="100"><b style="${known ? `width:${(j.progress * 100).toFixed(1)}%` : ""}"></b></div>`;
}
function buttons(j) {
  const kind = T.kinds.find(k => k.id === j.kind);
  const b = [];
  if (ACTIVE.includes(j.state)) b.push(["stop", j.state === "wartet" ? t("Abbrechen") : (kind ? kind.stop_label : t("Abbrechen"))]);
  b.push(["log", t("Protokoll")]);
  if (!ACTIVE.includes(j.state)) b.push(["remove", t("Entfernen")]);
  return b;
}
function actionsHTML(j) {
  const extra = T.api.actions(j).map(([label], i) => `<button class="btn small" data-x="${i}">${esc(label)}</button>`);
  return buttons(j).map(([a, label]) => `<button class="btn small" data-a="${a}">${label}</button>`).concat(extra).join("");
}
function bindActions(el, j) {
  el.querySelectorAll("[data-a]").forEach(b => b.addEventListener("click", () => act(j, b.dataset.a)));
  const extra = T.api.actions(j);
  el.querySelectorAll("[data-x]").forEach(b => b.addEventListener("click", () => extra[+b.dataset.x][1]()));
}
function renderList() {
  const box = $("#jobList");
  if (!T.jobs.length) { box.innerHTML = `<p class="note" style="margin:0">${t("Noch keine Aufgaben.")}</p>`; return; }
  const today = new Date().toDateString();
  box.innerHTML = T.jobs.map(j => {
    const when = new Date(j.created * 1000).toDateString() === today ? clock(j.created) : `${day(j.created)} ${clock(j.created)}`;
    return `<div class="job ${T.sel === j.id ? "sel" : ""}" data-id="${j.id}">
      <div class="hd"><span class="chip ${STATE_CLASS[j.state] || ""}">${esc(t(j.state))}</span><span class="nm" title="${esc(j.title)}">${esc(j.title)}</span></div>
      ${progressHTML(j)}
      <div class="det">${esc(j.state === "Fehler" ? j.error : j.detail || j.last || "")}</div>
      <div class="meta">${when}${j.started ? " · " + duration(j) : ""}</div>
      <div class="acts">${actionsHTML(j)}</div></div>`;
  }).join("");
  box.querySelectorAll(".job").forEach(el => bindActions(el, T.jobs.find(j => j.id === el.dataset.id)));
}
function renderBadge() {
  const run = T.jobs.filter(j => j.state === "läuft").length, wait = T.jobs.filter(j => j.state === "wartet").length;
  const b = $("#btnJobs");
  b.textContent = run ? t("Aufgaben · {n} läuft", { n: run }) : wait ? t("Aufgaben · {n} wartet", { n: wait }) : t("Aufgaben");
  b.classList.toggle("busy", run > 0);
}

async function act(j, a) {
  try {
    if (a === "log") { T.sel = j.id; renderList(); T.api.openInspector("job"); await refreshDetail(); return; }
    if (a === "stop") {
      const kind = T.kinds.find(k => k.id === j.kind);
      if (j.state === "läuft" && !(kind && kind.stop_is_success) &&
          !confirm(t("„{title}“ abbrechen? Die bisherige Rechenzeit geht verloren.", { title: j.title }))) return;
      await postJSON(`api/jobs/${j.id}/cancel`, {});
    }
    if (a === "remove") {
      await postJSON(`api/jobs/${j.id}/remove`, {});
      if (T.sel === j.id) { T.sel = null; T.api.updateInspector(); }
    }
    pollSoon();
  } catch (e) { T.api.toast(e.message, { bad: true }); }
}

// ---------------------------------------------------------------- detail (inspector tab)
async function refreshDetail() {
  const j = selectedJob(); if (!j) return;
  let d;
  try { d = await getJSON(`api/jobs/${j.id}`); } catch (e) { $("#t_job").innerHTML = `<div class="sec"><p class="msg">${esc(e.message)}</p></div>`; return; }
  const pre = $("#t_job pre"), stick = !pre || pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 20;
  const kind = T.kinds.find(k => k.id === d.kind);
  const decl = Object.fromEntries(((kind && kind.settings) || []).map(s => [s.name, s]));
  const shown = (k, v) => {  // option label instead of the raw value (e.g. "Antenne außen" for none)
    if (typeof v === "boolean") return v ? t("ja") : t("nein");
    const opt = decl[k] && decl[k].type === "select" && (decl[k].options || []).find(([o]) => String(o) === String(v));
    return opt ? opt[1] : v;
  };
  const params = Object.entries(d.params).map(([k, v]) =>
    `<tr><td>${esc(decl[k] ? decl[k].label : k)}</td><td>${esc(shown(k, v))}</td></tr>`).join("");
  $("#t_job").innerHTML = `<div class="sec">
      <div class="verdict"><span class="chip ${STATE_CLASS[d.state] || ""}">${esc(t(d.state))}</span><strong>${esc(d.title)}</strong></div>
      ${progressHTML(d)}
      <p class="note" style="margin:4px 0 8px">${esc(d.detail || "")}${d.started ? " · " + esc(t("gestartet {time}, {duration}", { time: clock(d.started), duration: duration(d) })) : ""}</p>
      ${d.error ? `<p class="msg" role="alert">${esc(d.error)}</p>` : ""}
      <div class="acts">${actionsHTML(d)}</div>
    </div>
    <div class="sec"><h2>${t("Parameter")}</h2><div class="wrap"><table>${params}</table></div></div>
    <div class="sec"><h2>${t("Protokoll")}</h2><pre class="log">${esc((d.log || []).join("\n")) || "—"}</pre></div>`;
  bindActions($("#t_job"), d);
  const p2 = $("#t_job pre");
  if (stick) p2.scrollTop = p2.scrollHeight;
  else if (pre) p2.scrollTop = pre.scrollTop;
}
export function showDetail(id) { T.sel = id; renderList(); T.api.openInspector("job"); refreshDetail(); }
export function renderDetailIfShown() { if (T.sel && !$("#t_job").hidden) refreshDetail(); }
