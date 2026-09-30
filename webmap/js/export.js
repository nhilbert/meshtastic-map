// Files the page builds for download (no server round trip): GPX tracks and CSV tables.
import { esc } from "./util.js";

export function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const iso = ts => new Date(ts * 1000).toISOString();

// waypoints: [{name, lat, lon}], track: [{time, lat, lon}]
export function gpx(name, waypoints, track) {
  const wpts = waypoints.map(w => `  <wpt lat="${w.lat}" lon="${w.lon}"><name>${esc(w.name)}</name></wpt>`).join("\n");
  const pts = track.map(p => `      <trkpt lat="${p.lat}" lon="${p.lon}"><time>${iso(p.time)}</time></trkpt>`).join("\n");
  return `<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="meshplay" xmlns="http://www.topografix.com/GPX/1/1">
  <metadata><name>${esc(name)}</name></metadata>
${wpts}
  <trk><name>${esc(name)}</name><trkseg>
${pts}
  </trkseg></trk>
</gpx>
`;
}

// rows: objects; columns: their keys in order; times (seconds) in `timeKeys` become ISO text
export function csv(columns, rows, timeKeys = ["time"]) {
  const cell = v => {
    if (v === null || v === undefined) return "";
    const s = typeof v === "object" ? JSON.stringify(v) : String(v);
    return /[",\n;]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = rows.map(r => columns.map(c => cell(timeKeys.includes(c) && r[c] ? iso(r[c]) : r[c])).join(","));
  return [columns.join(","), ...lines].join("\n") + "\n";
}
