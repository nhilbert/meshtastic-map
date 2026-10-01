// Actions on the objects of the app, with one logic everywhere: the toolbar in a map popup and in
// an opened node-list row, the "Mehr" menu and the right-click menu (long press on touch) all come
// from here. The server marks each feature with _ref = {type, id, …}; the parts of the page
// register what they can do per type. Type "map" is a free spot (ref {lat, lon}), "*" any feature.
//
// Rules: actions are ordered by group (G); a separator lies between groups. The toolbar shows up
// to four actions with icon and short label, never one that deletes; "Mehr" opens the full menu.
// An action that transmits carries the antenna mark; a label ending in "…" asks or opens a form;
// a disabled action stays visible and says why.
import { t } from "./i18n.js";
import { symbolSVG } from "./icons.js";
import { esc } from "./util.js";

export const G = { main: 1, radio: 2, work: 3, view: 4, link: 5, copy: 6, del: 9 };
const TOOLBAR_MAX = 4;

// type -> [provider(ref, feature, where) -> [action]]
// action: { label, short?, icon, group, run(), radio?, danger?, quick? (false: menu only),
//           disabled?: "reason" }; where: "map" (popup, right click) or "list" (node list)
const REG = new Map();

export function registerActions(type, provider) {
  if (!REG.has(type)) REG.set(type, []);
  REG.get(type).push(provider);
}

export function actionsFor(ref, feature = null, where = "map") {
  const out = [];
  for (const type of [ref && ref.type, "*"])
    for (const provider of REG.get(type) || []) out.push(...(provider(ref || {}, feature, where) || []));
  return out.filter(Boolean)
    .map((a, i) => ({ ...a, group: a.danger ? G.del : a.group ?? G.work, i }))
    .sort((a, b) => a.group - b.group || a.i - b.i);
}

// ---------------------------------------------------------------- toolbar
// The quick actions; with none flagged (own node), the first ones that don't delete.
function toolbarActions(actions) {
  const ok = actions.filter(a => !a.danger);
  const quick = ok.filter(a => a.quick !== false);
  return (quick.length ? quick : ok).slice(0, TOOLBAR_MAX);
}

const mark = a => a.radio ? `<span class="radiomark" title="${esc(t("Sendet über Funk"))}">${symbolSVG("antenna")}</span>` : "";

export function toolbarHTML(actions) {
  if (!actions.length) return "";
  const shown = toolbarActions(actions);
  const more = actions.length > shown.length;
  return `<div class="tbar" role="toolbar">${shown.map(a => {
    const title = a.disabled || (a.radio ? `${a.label} – ${t("Sendet über Funk")}` : a.label);
    return `<button type="button" class="tool" data-action="${a.i}" ${a.disabled ? "disabled" : ""} title="${esc(title)}" aria-label="${esc(a.label)}">
      ${symbolSVG(a.icon)}<span>${esc(a.short || a.label)}</span>${mark(a)}</button>`;
  }).join("")}${more ? `<button type="button" class="tool" data-more aria-haspopup="menu" title="${esc(t("Weitere Aktionen"))}">
      ${symbolSVG("more")}<span>${t("Mehr")}</span></button>` : ""}</div>`;
}

// done() runs before an action (closing the popup that showed the toolbar).
export function bindToolbar(root, actions, title, done) {
  const byIndex = Object.fromEntries(actions.map(a => [a.i, a]));
  root.querySelectorAll("[data-action]").forEach(b => b.addEventListener("click", ev => {
    ev.stopPropagation();
    if (done) done();
    byIndex[+b.dataset.action].run();
  }));
  root.querySelector("[data-more]")?.addEventListener("click", ev => {
    ev.stopPropagation();
    const r = ev.currentTarget.getBoundingClientRect();
    openMenu(r.left, r.bottom + 4, title, actions, done);
  });
}

// ---------------------------------------------------------------- menu
let menu = null;
export function closeMenu() {
  if (menu) { menu.remove(); menu = null; }
}
export function openMenu(x, y, title, actions, done = null) {
  closeMenu();
  if (!actions.length) return;
  menu = document.createElement("div");
  menu.className = "ctxmenu";
  menu.setAttribute("role", "menu");
  let prev = null;
  menu.innerHTML = (title ? `<div class="hd">${esc(title)}</div>` : "") + actions.map(a => {
    const sep = prev !== null && a.group !== prev ? `<div class="sep" role="separator"></div>` : "";
    prev = a.group;
    return `${sep}<button type="button" class="item${a.danger ? " danger" : ""}" role="menuitem" data-action="${a.i}" ${a.disabled ? `disabled title="${esc(a.disabled)}"` : ""}>
      ${symbolSVG(a.icon)}<span>${esc(a.label)}</span>${a.radio ? `<span class="tag">${symbolSVG("antenna")}${t("Funk")}</span>` : ""}</button>`;
  }).join("");
  document.body.appendChild(menu);
  const r = menu.getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(x, innerWidth - r.width - 8))}px`;
  menu.style.top = `${Math.max(8, Math.min(y, innerHeight - r.height - 8))}px`;
  const byIndex = Object.fromEntries(actions.map(a => [a.i, a]));
  menu.querySelectorAll("[data-action]").forEach(b => b.addEventListener("click", ev => {
    ev.stopPropagation();
    closeMenu();
    if (done) done();
    byIndex[+b.dataset.action].run();
  }));
  menu.querySelector("button:not([disabled])")?.focus();
}
document.addEventListener("pointerdown", e => { if (menu && !menu.contains(e.target)) closeMenu(); }, true);
document.addEventListener("keydown", e => {
  if (!menu) return;
  if (e.key === "Escape") { closeMenu(); return; }
  if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
  const items = [...menu.querySelectorAll("button:not([disabled])")];
  const i = items.indexOf(document.activeElement);
  items[(i + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
  e.preventDefault();
});
