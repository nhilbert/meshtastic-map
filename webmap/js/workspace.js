// The left zone: an activity bar with one icon per view and the panel that shows the chosen
// view (like VS Code). A click on the active icon folds the panel away; its edge can be dragged
// to set the width. On phones the bar sits at the bottom and the panel is a drawer.
// Badges on the icons: a number (count, unread), a dot (state: ok, warn, bad) or a ring
// (a running task's progress). Other modules reach a part of a view through showSection().
import { t } from "./i18n.js";
import { symbolSVG } from "./icons.js";
import { $ } from "./util.js";

const compact = matchMedia("(max-width: 700px)");
const ICONS = { layers: "layers", places: "pin", sim: "coverage", walks: "walk", coord: "target", tasks: "tasks",
  device: "client", settings: "gear", msg: "message" };
const TITLES = { layers: () => t("Ebenen"), places: () => t("Orte"), sim: () => t("Simulation"), walks: () => t("Rundgänge"),
  coord: () => t("Koordination"), tasks: () => t("Aufgaben"), device: () => t("Gerät"), settings: () => t("Einstellungen"),
  msg: () => t("Nachrichten") };
// the sections of the views (details[data-sec]) that other modules open
const SECTION_VIEW = { layers: "layers", "3d": "layers", sites: "places", targets: "places", areas: "places",
  roads: "places", scenes: "sim", link: "sim", coverage: "sim", walks: "walks", imports: "walks", coord: "coord",
  missions: "coord", archive: "coord", coordset: "coord", jobs: "tasks", device: "device", airtime: "device" };
const WIDTH = { min: 240, max: 560, normal: 300 };
const W = { view: "layers", closed: false, store: null, returnFocus: null };

export function initWorkspace(store) {
  W.store = store;
  W.view = store.get("rail.view", "layers");
  W.closed = store.get("rail.closed", false);
  setWidth(store.get("rail.width", WIDTH.normal));
  document.querySelectorAll("#actbar .ab").forEach(b => {
    const id = b.dataset.view || b.dataset.panel;
    b.innerHTML = symbolSVG(ICONS[id]) + `<span class="badge" hidden></span>`;
    b.title = TITLES[id]();
    if (b.dataset.view) b.addEventListener("click", () => toggleView(b.dataset.view));
  });
  $("#btnCloseRail").innerHTML = symbolSVG("close");
  $("#btnCloseRail").addEventListener("click", () => setRail(false));
  $("#railBackdrop").addEventListener("click", () => setRail(false));
  bindResize();
  compact.addEventListener("change", () => { setRail(false, false); render(); });
  setRail(false, false);
  render();
}

// Show a view; on phones as the drawer.
export function showView(id) {
  W.view = id; W.closed = false;
  W.store.set("rail.view", id); W.store.set("rail.closed", false);
  render();
  if (compact.matches) setRail(true);
}

function toggleView(id) {
  if (compact.matches) {
    if (document.body.classList.contains("rail-open") && W.view === id) setRail(false);
    else showView(id);
    return;
  }
  if (W.view === id && !W.closed) { W.closed = true; W.store.set("rail.closed", true); render(); return; }
  showView(id);
}

function render() {
  document.body.classList.toggle("panel-closed", W.closed && !compact.matches);
  document.querySelectorAll("#rail .view").forEach(v => { v.hidden = v.dataset.view !== W.view; });
  document.querySelectorAll("#actbar [data-view]").forEach(b => {
    const on = b.dataset.view === W.view && (compact.matches ? document.body.classList.contains("rail-open") : !W.closed);
    b.classList.toggle("on", on);
    b.setAttribute("aria-pressed", String(on));
  });
  $("#railTitle").textContent = TITLES[W.view]();
}

// Open a part of a view (a section by its data-sec), e.g. the task list or the device.
export function showSection(name) {
  const view = SECTION_VIEW[name];
  if (view) showView(view);
  const section = document.querySelector(`[data-sec="${name}"]`);
  if (!section) return;
  if (section.tagName === "DETAILS") section.open = true;
  (section.querySelector("summary") || section).scrollIntoView({ block: "nearest" });
}

// The drawer on phones; on wider screens the panel is part of the layout and this only
// restores focus.
export function setRail(open, restoreFocus = true) {
  const wasOpen = document.body.classList.contains("rail-open");
  if (open && !wasOpen) W.returnFocus = document.activeElement;
  document.body.classList.toggle("rail-open", open);
  $("#railBackdrop").hidden = !open || !compact.matches;
  $("#rail").inert = compact.matches && !open;
  $("#view").inert = compact.matches && open;
  $("#right").inert = compact.matches && open;
  if (compact.matches && open) $("#btnCloseRail").focus();
  else if (wasOpen && restoreFocus && W.returnFocus?.isConnected) W.returnFocus.focus();
  if (W.store) render();
}

// badge: null, { num }, { dot: "ok" | "warn" | "bad" } or { run: 0..1 }; title says it in words.
export function setBadge(id, badge, title) {
  const b = document.querySelector(`#actbar [data-view="${id}"], #actbar [data-panel="${id}"]`);
  if (!b) return;
  const el = b.querySelector(".badge");
  el.hidden = !badge;
  el.className = "badge" + (badge ? " " + (badge.num !== undefined ? "num" : badge.dot ? "dot " + badge.dot : "run") : "");
  el.textContent = badge && badge.num !== undefined ? (badge.num > 99 ? "99+" : String(badge.num)) : "";
  el.style.setProperty("--p", badge && badge.run !== undefined ? String(Math.round(badge.run * 360)) + "deg" : "0deg");
  b.title = title || TITLES[id]();
}

// ---------------------------------------------------------------- width
function setWidth(px) {
  const w = Math.round(Math.min(WIDTH.max, Math.max(WIDTH.min, px)));
  document.documentElement.style.setProperty("--rail-w", w + "px");
  $("#railResize").setAttribute("aria-valuenow", String(w));
  return w;
}
function bindResize() {
  const h = $("#railResize");
  h.setAttribute("aria-valuemin", String(WIDTH.min));
  h.setAttribute("aria-valuemax", String(WIDTH.max));
  h.addEventListener("pointerdown", e => {
    const x0 = e.clientX, w0 = $("#rail").getBoundingClientRect().width;
    h.setPointerCapture(e.pointerId);
    document.body.classList.add("resizing");
    const move = ev => setWidth(w0 + ev.clientX - x0);
    const up = ev => {
      h.removeEventListener("pointermove", move); h.removeEventListener("pointerup", up);
      document.body.classList.remove("resizing");
      W.store.set("rail.width", setWidth(w0 + ev.clientX - x0));
    };
    h.addEventListener("pointermove", move); h.addEventListener("pointerup", up);
  });
  h.addEventListener("dblclick", () => W.store.set("rail.width", setWidth(WIDTH.normal)));
  h.addEventListener("keydown", e => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    const w = $("#rail").getBoundingClientRect().width + (e.key === "ArrowRight" ? 20 : -20);
    W.store.set("rail.width", setWidth(w));
    e.preventDefault();
  });
}
