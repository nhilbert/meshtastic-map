// Badge markers for features with an _icon property: symbol + short text, coloured.
import { esc } from "./util.js";

const PATHS = {
  // antenna mast with waves
  router: '<path d="M8 6v8M5.5 14h5M8 6l-2.5 8M8 6l2.5 8"/><path d="M5 4.2a4 4 0 0 1 6 0M3.2 2.4a6.5 6.5 0 0 1 9.6 0"/>',
  // location pin
  tracker: '<path d="M8 14.5s-4.5-4.6-4.5-8a4.5 4.5 0 0 1 9 0c0 3.4-4.5 8-4.5 8z"/><circle cx="8" cy="6.5" r="1.6"/>',
  // handheld radio
  client: '<rect x="4.5" y="5" width="7" height="9.5" rx="1.2"/><path d="M6.5 5V1.5M6.5 8h3M6.5 10.5h3"/>',
  // thermometer
  sensor: '<path d="M8 2.5a1.5 1.5 0 0 1 1.5 1.5v6a2.6 2.6 0 1 1-3 0V4A1.5 1.5 0 0 1 8 2.5z"/><path d="M8 7v4.5"/>',
  // house
  home: '<path d="M2.5 7.5 8 3l5.5 4.5M4 6.5V13.5h8V6.5M6.8 13.5v-3.5h2.4v3.5"/>',
  // flag: a stop of a coordination path
  target: '<path d="M4 14.5V2.5M4 3h8l-2.5 3L12 9H4"/>',
  // small ring: a via point
  via: '<circle cx="8" cy="8" r="3.5"/><path d="M8 1.5v3M8 11.5v3M1.5 8h3M11.5 8h3"/>',
};

export function symbolSVG(name) {
  const p = PATHS[name] || PATHS.client;
  return `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${p}</svg>`;
}

// Dark or light text depending on the badge colour.
export function inkFor(hex) {
  const c = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map(i => parseInt(c.slice(i, i + 2), 16) / 255);
  const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return lum > 0.55 ? "#10171d" : "#ffffff";
}

export function badgeHTML(icon) {
  const color = icon.color || "#3388ff";
  const cls = ["nd", icon.own ? "own" : "", icon.faded ? "faded" : "", icon.text ? "" : "notext"].join(" ");
  return `<div class="${cls}" style="--c:${color};--fg:${inkFor(color)}">${symbolSVG(icon.symbol)}${icon.text ? `<span>${esc(icon.text)}</span>` : ""}</div>`;
}

// Canvas with the badge text for a three.js sprite (3D view). Returns {canvas, aspect}.
export function badgeCanvas(icon) {
  const color = icon.color || "#3388ff", text = icon.text || "•";
  const h = 64, font = "600 38px 'IBM Plex Mono', ui-monospace, monospace";
  const probe = document.createElement("canvas").getContext("2d");
  probe.font = font;
  const w = Math.ceil(probe.measureText(text).width) + 36;
  const cv = document.createElement("canvas");
  cv.width = w; cv.height = h + 12;
  const g = cv.getContext("2d");
  g.fillStyle = color; g.strokeStyle = icon.own ? "#2cc4d6" : "#ffffff"; g.lineWidth = icon.own ? 6 : 4;
  const r = 20;
  g.beginPath();
  g.moveTo(r, 2); g.lineTo(w - r, 2); g.quadraticCurveTo(w - 2, 2, w - 2, r);
  g.lineTo(w - 2, h - r); g.quadraticCurveTo(w - 2, h - 2, w - r, h - 2);
  g.lineTo(w / 2 + 9, h - 2); g.lineTo(w / 2, h + 10); g.lineTo(w / 2 - 9, h - 2);
  g.lineTo(r, h - 2); g.quadraticCurveTo(2, h - 2, 2, h - r); g.lineTo(2, r); g.quadraticCurveTo(2, 2, r, 2);
  g.closePath(); g.fill(); g.stroke();
  g.fillStyle = inkFor(color); g.font = font; g.textAlign = "center"; g.textBaseline = "middle";
  g.fillText(text, w / 2, h / 2 + 1);
  return { canvas: cv, aspect: w / (h + 12) };
}
