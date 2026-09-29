// Shared helpers.
import { lang, locale, t } from "./i18n.js";

export const $ = s => document.querySelector(s);
export const fmt = (v, d = 1) => (v === null || v === undefined || !isFinite(v)) ? "–"
  : Number(v).toLocaleString(locale, { minimumFractionDigits: d, maximumFractionDigits: d });
export const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
export const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// The server answers in the page's language (texts it sends, error messages).
export async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store", headers: { "X-Lang": lang } });
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || `${url}: HTTP ${r.status}`);
  return j;
}
export async function postJSON(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json", "X-Lang": lang }, body: JSON.stringify(body) });
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || `${url}: HTTP ${r.status}`);
  return j;
}

// EPSG:25832 (ETRS89 / UTM 32N) <-> WGS84, via proj4 (global from the CDN script).
proj4.defs("EPSG:25832", "+proj=utm +zone=32 +ellps=GRS80 +towgs84=0,0,0,0,0,0,0 +units=m +no_defs");
export const toUTM = (lon, lat) => proj4("EPSG:4326", "EPSG:25832", [lon, lat]);
export const toLonLat = (x, y) => proj4("EPSG:25832", "EPSG:4326", [x, y]);

// Grade of a link by the per-packet delivery probability.
export function grade(p) {
  if (p >= 0.9) return ["g-ok", t("trägt"), "--ok"];
  if (p >= 0.5) return ["g-warn", t("grenzwertig"), "--warn"];
  return ["g-bad", t("trägt nicht"), "--bad"];
}

// Popup / info HTML for a GeoJSON feature following the layer conventions.
export function featureHTML(props, withActions = true) {
  const rows = Object.entries(props._fields || {})
    .map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("");
  const buttons = [];
  if (withActions && props._endpoint) buttons.push(`<button class="btn small" data-set="a">${t("als A")}</button><button class="btn small" data-set="b">${t("als B")}</button>`);
  if (withActions && props._node_id && !(props._icon && props._icon.own))
    buttons.push(`<button class="btn small" data-set="msg">${t("Nachricht")}</button><button class="btn small on" data-set="coord">${t("Ziel zuweisen")}</button>`);
  const acts = buttons.length ? `<div class="acts">${buttons.join("")}</div>` : "";
  return `<div class="pop"><h3>${esc(props._title || "")}</h3><table>${rows}</table>${acts}</div>`;
}

export function legendHTML(lg) {
  if (!lg) return "";
  if (lg.ramp) {
    return `<div class="legend"><div class="t">${esc(lg.title)}</div>
      <div class="ramp" style="background:linear-gradient(90deg,${lg.ramp.join(",")})"></div>
      <div class="ends"><span>${lg.min}${lg.unit ? " " + lg.unit : ""}</span><span>${lg.max}${lg.unit ? " " + lg.unit : ""}</span></div></div>`;
  }
  return `<div class="legend"><div class="t">${esc(lg.title)}</div>${(lg.items || [])
    .map(([c, l]) => `<div class="it"><i style="background:${c}"></i>${esc(l)}</div>`).join("")}</div>`;
}
