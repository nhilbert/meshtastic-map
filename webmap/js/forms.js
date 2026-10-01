// Forms from the server's Setting declarations (layer settings, task parameters).
import { t } from "./i18n.js";
import { esc, fmt } from "./util.js";

// "point" and "bbox" settings are picked on the map; main.js provides the map:
// { pick(label, count, cb(points)), preview(ring | null) }
let mapApi = null;
export function setFormMap(api) { mapApi = api; }
// A closed form leaves no area on the map.
export function clearFormPreview() { mapApi?.preview(null); }

const nums = text => (String(text ?? "").match(/-?\d+(?:\.\d+)?/g) || []).map(Number);
const KM_LAT = 111.32;
const kmLon = lat => KM_LAT * Math.cos(lat * Math.PI / 180);

// [ring, "size text"] of a setting's value for the map preview, or null when it doesn't parse.
function area(s, values) {
  const v = nums(values[s.name]);
  if (s.type === "bbox" && v.length === 4) {
    const [south, west, north, east] = v;
    if (!(south < north && west < east)) return null;
    const w = (east - west) * kmLon((south + north) / 2), h = (north - south) * KM_LAT;
    const size = { w: fmt(w, 1), h: fmt(h, 1) };
    return [[[south, west], [south, east], [north, east], [north, west]],
      s.max && (north - south > s.max || east - west > s.max)
        ? t("{w} × {h} km: zu groß, höchstens {deg}° je Seite. Weiter hineinzoomen und neu wählen.", { ...size, deg: s.max })
        : t("{w} × {h} km", size)];
  }
  if (s.type === "point" && v.length === 2) {
    const [lat, lon] = v, km = +values[s.square_km_from] || 0;
    if (!km) return [[[lat, lon]], ""];
    const dy = km / 2 / KM_LAT, dx = km / 2 / kmLon(lat);
    return [[[lat - dy, lon - dx], [lat - dy, lon + dx], [lat + dy, lon + dx], [lat + dy, lon - dx]],
      t("{w} × {h} km", { w: fmt(km, 0), h: fmt(km, 0) })];
  }
  return null;
}

// Options of a select; a select that depends on another setting takes them from options_map.
export function optionsFor(s, values) {
  if (s.depends_on) return (s.options_map || {})[values[s.depends_on]] || [];
  return s.options || [];
}

// idPrefix keeps datalist ids unique when several forms are on the page.
export function inputsHTML(settings, values, idPrefix = "f") {
  return settings.map(s => {
    const v = values[s.name], tip = s.help ? ` title="${esc(s.help)}"` : "";
    if (s.type === "bool")
      return `<label class="tog"${tip}><input type="checkbox" data-s="${s.name}" ${v ? "checked" : ""}>${esc(s.label)}</label>`;
    if (s.type === "select") {
      const opts = optionsFor(s, values);
      return `<label${tip}>${esc(s.label)}<select data-s="${s.name}">${opts.map(([ov, ol]) =>
        `<option value="${esc(ov)}" ${String(v) === String(ov) ? "selected" : ""}>${esc(ol)}</option>`).join("") || "<option value=''>—</option>"}</select></label>`;
    }
    if (s.type === "point" || s.type === "bbox") {
      // typed or picked; the datalist offers the own sites for a point
      const id = `dl_${idPrefix}_${s.name}`, list = s.options && s.options.length;
      const ph = s.type === "bbox" ? "50.70, 7.05, 50.76, 7.15" : "50.73740, 7.09820";
      return `<label${tip}>${esc(s.label)}<span class="pickfield"><input type="text" data-s="${s.name}" value="${esc(v ?? "")}" placeholder="${ph}" autocomplete="off" ${list ? `list="${id}"` : ""}>
        <button type="button" class="btn small" data-pick="${s.name}">${t("Auf der Karte wählen")}</button></span>
        ${list ? `<datalist id="${id}">${s.options.map(([ov, ol]) => `<option value="${esc(ov)}">${esc(ol)}</option>`).join("")}</datalist>` : ""}
        <span class="note" data-area="${s.name}"></span></label>`;
    }
    if (s.type === "text" && s.options && s.options.length) {
      // free text with suggestions (e.g. node IDs)
      const id = `dl_${idPrefix}_${s.name}`;
      return `<label${tip}>${esc(s.label)}<input type="text" list="${id}" data-s="${s.name}" value="${esc(v ?? "")}" autocomplete="off">
        <datalist id="${id}">${s.options.map(([ov, ol]) => `<option value="${esc(ov)}">${esc(ol)}</option>`).join("")}</datalist></label>`;
    }
    const attrs = ["min", "max", "step"].filter(k => s[k] !== undefined).map(k => `${k}="${s[k]}"`).join(" ");
    return `<label${tip}>${esc(s.label)}<input type="${s.type === "number" ? "number" : "text"}" data-s="${s.name}" value="${esc(v ?? "")}" ${attrs}></label>`;
  }).join("");
}

// Keeps `values` in sync with the inputs; a changed select resets the selects that depend on it
// to their first option. onChange(setting, needsRender) — needsRender when dependents changed.
export function bindInputs(root, settings, values, onChange) {
  root.querySelectorAll("[data-s]").forEach(inp => inp.addEventListener("change", () => {
    const s = settings.find(x => x.name === inp.dataset.s);
    values[s.name] = s.type === "bool" ? inp.checked : s.type === "number" ? +inp.value : inp.value;
    const deps = settings.filter(x => x.depends_on === s.name);
    for (const d of deps) {
      const opts = optionsFor(d, values); values[d.name] = opts.length ? opts[0][0] : "";
    }
    onChange(s, deps.length > 0);
    showArea(root, settings, values);
  }));
  root.querySelectorAll("[data-pick]").forEach(btn => btn.addEventListener("click", () => {
    const s = settings.find(x => x.name === btn.dataset.pick);
    if (!mapApi) return;
    const bbox = s.type === "bbox";
    mapApi.pick(bbox ? t("zwei gegenüberliegende Ecken von „{label}“", { label: s.label }) : s.label,
      bbox ? 2 : 1, points => {
        const inp = root.querySelector(`[data-s="${s.name}"]`);
        if (!inp || !inp.isConnected) return;  // the form was closed meanwhile
        if (bbox) {
          const [[a, b], [c, d]] = points;
          inp.value = [Math.min(a, c), Math.min(b, d), Math.max(a, c), Math.max(b, d)].map(x => x.toFixed(4)).join(", ");
        } else {
          inp.value = `${points[0][0].toFixed(5)}, ${points[0][1].toFixed(5)}`;
        }
        inp.dispatchEvent(new Event("change"));
      });
  }));
  showArea(root, settings, values);
}

// The area of the form's point/bbox settings: size under the field, outline on the map.
function showArea(root, settings, values) {
  let ring = null;
  for (const s of settings) {
    if (s.type !== "point" && s.type !== "bbox") continue;
    const a = area(s, values), el = root.querySelector(`[data-area="${s.name}"]`);
    if (el) el.textContent = a ? a[1] : (values[s.name] ? t("Format nicht erkannt") : "");
    if (a && !ring) ring = a[0];
  }
  if (ring || settings.some(s => s.type === "point" || s.type === "bbox")) mapApi?.preview(ring);
}

// Defaults of the declarations, overlaid with saved values that are still valid.
export function initialValues(settings, saved) {
  const values = Object.fromEntries(settings.map(s => [s.name, s.default]));
  if (saved) for (const s of settings) {
    const v = saved[s.name];
    if (v === undefined) continue;
    if (s.type === "select" && !optionsFor(s, { ...values, ...saved }).some(([o]) => String(o) === String(v))) continue;
    values[s.name] = v;
  }
  return values;
}
