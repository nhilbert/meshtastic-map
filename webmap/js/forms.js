// Forms from the server's Setting declarations (layer settings, task parameters).
import { esc } from "./util.js";

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
  }));
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
