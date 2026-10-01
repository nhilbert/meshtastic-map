// Actions on the objects of the map: the buttons in a popup and the menu on a right click (a long
// press on touch screens) both come from here. The server marks each feature with _ref = {type,
// id, …}; the parts of the page register what they can do per type, so the map offers what their
// editors offer. Type "map" is a free spot on the map (ref {lat, lon}), "*" any feature.
import { t } from "./i18n.js";
import { esc } from "./util.js";

// type -> [provider(ref, feature) -> [{ label, run(), radio, danger, disabled: "reason" }]]
const REG = new Map();

export function registerActions(type, provider) {
  if (!REG.has(type)) REG.set(type, []);
  REG.get(type).push(provider);
}

export function actionsFor(ref, feature = null) {
  const out = [];
  for (const type of [ref && ref.type, "*"])
    for (const provider of REG.get(type) || []) out.push(...(provider(ref || {}, feature) || []));
  return out.filter(Boolean);
}

function buttonHTML(a, i, cls) {
  const title = a.disabled || (a.radio ? t("Sendet über Funk") : "");
  return `<button type="button" class="${cls}${a.danger ? " danger" : ""}" data-action="${i}" ${a.disabled ? "disabled" : ""}
    ${title ? `title="${esc(title)}"` : ""} role="menuitem">${a.radio ? "📡 " : ""}${esc(a.label)}</button>`;
}

// done() runs before the action (closing the popup or menu that showed it).
export function bindActions(root, actions, done) {
  root.querySelectorAll("[data-action]").forEach(b => b.addEventListener("click", ev => {
    ev.stopPropagation();
    if (done) done();
    actions[+b.dataset.action].run();
  }));
}

export const actionsHTML = actions => actions.map((a, i) => buttonHTML(a, i, "btn small")).join("");

// ---------------------------------------------------------------- right-click menu
let menu = null;
export function closeMenu() {
  if (menu) { menu.remove(); menu = null; }
}
export function openMenu(x, y, title, actions) {
  closeMenu();
  if (!actions.length) return;
  menu = document.createElement("div");
  menu.className = "ctxmenu";
  menu.setAttribute("role", "menu");
  menu.innerHTML = (title ? `<div class="hd">${esc(title)}</div>` : "") + actions.map((a, i) => buttonHTML(a, i, "item")).join("");
  document.body.appendChild(menu);
  const r = menu.getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(x, innerWidth - r.width - 8))}px`;
  menu.style.top = `${Math.max(8, Math.min(y, innerHeight - r.height - 8))}px`;
  bindActions(menu, actions, closeMenu);
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
