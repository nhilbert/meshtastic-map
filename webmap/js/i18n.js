// Translations of the page (German source, catalogues webmap/i18n/en.json and fr.json, shared
// with the server). t(germanText, {n}) returns the text in the chosen language; a missing
// entry falls back to German. Static HTML is marked with data-i18n (text), data-i18n-title,
// data-i18n-placeholder and data-i18n-aria-label. The language is chosen in the header and
// remembered per browser; switching reloads the page. tests/test_i18n.py checks the catalogues.
export const LANGS = ["de", "en", "fr"];
const LOCALES = { de: "de-DE", en: "en-GB", fr: "fr-FR" };
let dict = {};

function stored() { try { return localStorage.getItem("mapapp.lang"); } catch (_) { return null; } }
function initial() {
  const s = stored();
  if (LANGS.includes(s)) return s;
  const nav = (navigator.language || "de").slice(0, 2).toLowerCase();
  return LANGS.includes(nav) ? nav : "en";
}
export const lang = initial();
export const locale = LOCALES[lang];

export async function loadCatalogue() {
  document.documentElement.lang = lang;
  if (lang === "de") return;
  try {
    const r = await fetch(`i18n/${lang}.json`, { cache: "no-cache" });
    if (r.ok) dict = await r.json();
  } catch (_) { /* German stays */ }
}

export function setLang(l) {
  try { localStorage.setItem("mapapp.lang", l); } catch (_) { }
  location.reload();
}

// e.g. t('{n} Knoten', {n: 3}) -> "3 nodes" in English
export function t(text, params) {
  const s = dict[text] ?? text;
  return params ? s.replace(/\{(\w+)\}/g, (m, k) => (k in params ? String(params[k]) : m)) : s;
}

// Translate the marked static elements of the page.
export function translateStatic(root = document) {
  root.querySelectorAll("[data-i18n]").forEach(el => { el.textContent = t(el.dataset.i18nKey || (el.dataset.i18nKey = el.textContent.trim())); });
  for (const attr of ["title", "placeholder", "aria-label"]) {
    root.querySelectorAll(`[data-i18n-${attr}]`).forEach(el => el.setAttribute(attr, t(el.getAttribute(`data-i18n-${attr}`))));
  }
  document.title = t(document.title);
}
