// Badge markers for features with an _icon property: symbol + short text, coloured.
import { esc } from "./util.js";

const PATHS = {
  map: '<path d="m1.5 3 4-1.5 5 2 4-1.5v11l-4 1.5-5-2-4 1.5zM5.5 1.5v11M10.5 3.5v11"/>',
  cube: '<path d="m8 1.5 6 3.3v6.4L8 14.5l-6-3.3V4.8zM2 4.8 8 8l6-3.2M8 8v6.5M5 3.2l6 3.2"/>',
  message: '<path d="M2 2.5h12v9H6l-4 3zM5 5.5h6M5 8h4"/>',
  tasks: '<rect x="3" y="3" width="10" height="11" rx="1.5"/><path d="M6 3V1.5h4V3M5.5 8l1.5 1.5 3.5-4"/>',
  layers: '<path d="m8 1.5 6.5 3.8L8 9 1.5 5.3zM2 8.3 8 12l6-3.7M2 11 8 14.5l6-3.5"/>',
  route: '<circle cx="3" cy="3" r="1.5"/><circle cx="13" cy="13" r="1.5"/><path d="M4.5 3h6a2.5 2.5 0 0 1 0 5h-5a2.5 2.5 0 0 0 0 5h6"/>',
  panel: '<rect x="1.5" y="2" width="13" height="12" rx="1.5"/><path d="M10 2v12"/>',
  settings: '<path d="M2 4h12M2 12h12"/><rect x="5" y="2" width="3" height="4" rx="1"/><rect x="9" y="10" width="3" height="4" rx="1"/>',
  theme: '<path d="M13.5 9.5A6 6 0 0 1 6.5 2a6 6 0 1 0 7 7.5z"/>',
  close: '<path d="m4 4 8 8M12 4l-8 8"/>',
  refresh: '<path d="M13.5 8A5.5 5.5 0 1 1 11.9 4.1M13.5 1.5v3.5H10"/>',
  menu: '<path d="M2 4h12M2 8h12M2 12h12"/>',
  bluetooth: '<path d="m4.5 5 7 6L8 14.5v-13L11.5 5l-7 6"/>',
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
  // crosshair with a centre dot: ask a node for its position
  locate: '<circle cx="8" cy="8" r="4.5"/><circle cx="8" cy="8" r="1"/><path d="M8 1v2.5M8 12.5V15M1 8h2.5M12.5 8H15"/>',
  // actions (actions.js and the editors): one symbol per meaning everywhere
  edit: '<path d="M2.5 13.5h3l7.5-7.5-3-3-7.5 7.5z"/><path d="m9 4.5 2.5 2.5"/>',
  move: '<path d="M8 1.5v13M1.5 8h13M8 1.5 6.2 3.3M8 1.5l1.8 1.8M8 14.5l-1.8-1.8M8 14.5l1.8-1.8M1.5 8l1.8-1.8M1.5 8l1.8 1.8M14.5 8l-1.8-1.8M14.5 8l-1.8 1.8"/>',
  trash: '<path d="M2.5 4.5h11M6.5 7v5M9.5 7v5M3.8 4.5l.7 9.5h7l.7-9.5M6 4.5V2.5h4v2"/>',
  more: '<circle cx="3" cy="8" r=".6"/><circle cx="8" cy="8" r=".6"/><circle cx="13" cy="8" r=".6"/>',
  list: '<path d="M6 4h8M6 8h8M6 12h8M2.5 4h.01M2.5 8h.01M2.5 12h.01"/>',
  coverage: '<circle cx="8" cy="9" r="1.2"/><path d="M5.3 6.6a3.5 3.5 0 0 0 0 4.8M10.7 6.6a3.5 3.5 0 0 1 0 4.8M3.2 4.5a6.5 6.5 0 0 0 0 9M12.8 4.5a6.5 6.5 0 0 1 0 9"/>',
  // sends on the mesh: the mark of every action that transmits
  antenna: '<path d="M8 8v6.5M5.6 5.6a3.4 3.4 0 0 1 4.8 0M3.8 3.8a6 6 0 0 1 8.4 0"/><circle cx="8" cy="7.6" r=".8"/>',
  check: '<path d="m3 8.5 3 3 7-7"/>',
  star: '<path d="m8 1.8 1.9 3.9 4.3.6-3.1 3 .7 4.3L8 11.6l-3.8 2 .7-4.3-3.1-3 4.3-.6z"/>',
  copy: '<rect x="5" y="5" width="9" height="9" rx="1.5"/><path d="M11 5V3.5A1.5 1.5 0 0 0 9.5 2h-6A1.5 1.5 0 0 0 2 3.5v6A1.5 1.5 0 0 0 3.5 11H5"/>',
  area: '<path d="M3 4.5 9 2l5 5-2.5 6.5L3.5 12z"/>',
  place: '<circle cx="8" cy="8" r="5.5" stroke-dasharray="2 2"/><circle cx="8" cy="8" r="1.3"/>',
  // the link tool's endpoints, like the A/B marks on the map
  endA: '<rect x="2" y="2" width="12" height="12" rx="2.5"/><path d="M5.6 11.5 8 4.5l2.4 7M6.4 9.2h3.2"/>',
  endB: '<rect x="2" y="2" width="12" height="12" rx="2.5"/><path d="M6 4.5v7h2.6a1.8 1.8 0 0 0 0-3.6H6h2.2a1.7 1.7 0 0 0 0-3.4z"/>',
};
// the app's settings (the bottom of the activity bar); "settings" (sliders) is a layer's own
PATHS.gear = '<circle cx="8" cy="8" r="2"/><path d="M6.9 1.5h2.2l.4 1.8 1.5.9 1.8-.6 1.1 1.9-1.4 1.3v1.8l1.4 1.3-1.1 1.9-1.8-.6-1.5.9-.4 1.8H6.9l-.4-1.8-1.5-.9-1.8.6-1.1-1.9 1.4-1.3V7.2L2.1 5.9l1.1-1.9 1.8.6 1.5-.9z"/>';
// mission values: arrival time, distance, waiting
PATHS.clock = '<circle cx="8" cy="8" r="6"/><path d="M8 4.5V8l2.5 1.5"/>';
PATHS.ruler = '<path d="M2 11 11 2l3 3-9 9z"/><path d="m5 8 1.5 1.5M7 6l1 1M9 4l1.5 1.5"/>';
PATHS.hourglass = '<path d="M4.5 2h7M4.5 14h7M5 2c0 3 6 3.5 6 6s-6 3-6 6M11 2c0 3-6 3.5-6 6s6 3 6 6"/>';
// a dashed track from its start to a pin: the walks
PATHS.walk = '<circle cx="2.8" cy="13" r="1.3"/><path d="M5 13c3.5 0 1.5-3.6 4.5-3.6 1.6 0 2.4-.6 3-1.4" stroke-dasharray="1.8 2.2"/><path d="M12.5 8s-2.5-2.6-2.5-4.4a2.5 2.5 0 0 1 5 0C15 5.4 12.5 8 12.5 8z"/>';
// a walk's layer shows it, or doesn't
PATHS.eye = '<path d="M1.5 8s2.4-4.5 6.5-4.5S14.5 8 14.5 8s-2.4 4.5-6.5 4.5S1.5 8 1.5 8z"/><circle cx="8" cy="8" r="2"/>';
PATHS.eyeOff = '<path d="M6.3 3.7A6.6 6.6 0 0 1 8 3.5c4.1 0 6.5 4.5 6.5 4.5a11 11 0 0 1-1.7 2.2M10.9 11.7A6 6 0 0 1 8 12.5C3.9 12.5 1.5 8 1.5 8a10.6 10.6 0 0 1 2.7-3.1M6.6 6.6a2 2 0 0 0 2.8 2.8M2.5 2.5l11 11"/>';
PATHS.plus = '<path d="M8 3v10M3 8h10"/>';
PATHS.pin = PATHS.tracker;
PATHS.scene = PATHS.cube;

export function symbolSVG(name) {
  const p = PATHS[name] || PATHS.client;
  return `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${p}</svg>`;
}

// An icon-only button of the editors: the label is its tooltip and accessible name.
export function iconButton(icon, label, attrs = "", { danger = false } = {}) {
  return `<button type="button" class="btn small icon${danger ? " danger" : ""}" ${attrs} aria-label="${esc(label)}" title="${esc(label)}">${symbolSVG(icon)}</button>`;
}

// Refreshing unread/task counts must preserve the header's icon and accessible text.
export function buttonLabel(button, icon, label) {
  button.innerHTML = symbolSVG(icon) + `<span>${esc(label)}</span>`;
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
