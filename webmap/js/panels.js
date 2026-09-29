// Inspector: result of the link layer (profile, models, measured values, methodology) and the
// walk layer's comparison with the models. main.js decides which tabs are shown.
import { t } from "./i18n.js";
import { $, esc, fmt, grade } from "./util.js";

const NAMES = () => ({
  M1_P1812: t("P.1812 volles Profil"), M1b_P1812_clut: t("P.1812 + Clutter Gl.64"),
  M6_P1812_P833: t("Gebäude beugen, Bäume dämpfen"), M2_P1812_bare: t("P.1812 Gelände + P.2108"),
  M3_Bullington: t("Delta-Bullington"), M4_P1411: t("P.1411 Kurzstrecke"), M5_LogDist: t("Log-Distanz"),
  ENS: t("Ensemble (Median)"),
});
const INDOOR = () => ({ null: t("Antenne außen"), open: t("Fenster offen"), trad: t("Fenster zu (Altbau)"), lowe: t("Fenster zu (Wärmeschutz)") });
// Kinds of diffraction edges from the server (meshplay.sim.view3d): t("Gebäude") t("Bewuchs") t("Gelände")

export function renderLink(res, a, b) {
  const e = res.models.ENS, g = grade(e.p_rx), marg = e.prx[1] - e.sens;
  const names = NAMES(), indoor = INDOOR();
  const device = d => (d === "t1000e" ? "T1000-E" : t("Stabantenne"));
  const meas = b.measured || a.measured;
  const measHTML = meas && meas.signal !== null && meas.signal !== undefined ? `
    <div class="sec"><h2>${esc(t("Messung am Punkt ({time})", { time: meas.time || "" }))}</h2>
      <div class="kpis">
        <div class="kpi"><div class="k">${t("Signal gemessen")}</div><div class="v">${fmt(meas.signal, 1)}<span class="u"> dBm</span></div></div>
        <div class="kpi"><div class="k">${t("Modell (ENS)")}</div><div class="v">${fmt(e.prx[1], 1)}<span class="u"> dBm</span></div></div>
        <div class="kpi"><div class="k">${t("Messung − Modell")}</div><div class="v" style="color:var(${Math.abs(meas.signal - e.prx[1]) < 6 ? "--ok" : "--warn"})">${meas.signal - e.prx[1] > 0 ? "+" : ""}${fmt(meas.signal - e.prx[1], 1)}<span class="u"> dB</span></div></div>
      </div>
      <p class="note">${t("RSSI {rssi} dBm, SNR {snr} dB, {hops} Hops.", { rssi: fmt(meas.rssi, 0), snr: fmt(meas.snr, 2), hops: meas.hops })}
        ${t("Signal = RSSI ohne Rauschanteil (RSSI − 10·log10(1 + 10^(−SNR/10))).")}
        ${meas.signal >= e.prx[0] && meas.signal <= e.prx[2] ? t("Liegt im 80-%-Band des Ensembles.") : t("Liegt außerhalb des 80-%-Bands des Ensembles.")}</p>
    </div>` : "";
  const spread = Object.values(res.models).map(m => m.prx[1]);
  $("#t_link").innerHTML = `
  <div class="sec">
    <div class="verdict"><span class="dot ${g[0]}"></span><strong>${g[1]}</strong>
      <span style="color:var(--ink3)">— ${esc(t("Ensemble, ein Paket, Preset {preset}", { preset: res.preset }))}</span></div>
    <div class="kpis">
      <div class="kpi"><div class="k">${t("Distanz")}</div><div class="v">${fmt(res.d_m, 0)}<span class="u"> m</span></div></div>
      <div class="kpi"><div class="k">${t("Empfangspegel")}</div><div class="v">${fmt(e.prx[1], 0)}<span class="u"> dBm</span></div></div>
      <div class="kpi"><div class="k">${t("P(Paket)")}</div><div class="v">${fmt(e.p_rx * 100, 0)}<span class="u"> %</span></div></div>
      <div class="kpi"><div class="k">${t("Reserve")}</div><div class="v" style="color:var(${marg > 3 ? "--ok" : marg > 0 ? "--warn" : "--bad"})">${marg > 0 ? "+" : ""}${fmt(marg, 1)}<span class="u"> dB</span></div></div>
      <div class="kpi"><div class="k">${t("Pegel 10–90 %")}</div><div class="v" style="font-size:13px">${fmt(e.prx[0], 0)} … ${fmt(e.prx[2], 0)}</div></div>
      <div class="kpi"><div class="k">${t("Laserdaten")}</div><div class="v">${fmt(res.measured * 100, 0)}<span class="u"> %</span></div></div>
    </div>
    <p class="note">${t("Streuung über die Modellfamilien: {min} bis {max} dBm.", { min: fmt(Math.min(...spread), 0), max: fmt(Math.max(...spread), 0) })}
      ${res.measured < 0.95 ? t("Ein Teil der Strecke liegt über interpolierten Flächen ohne Laserpunkte.") : ""}</p>
  </div>
  ${measHTML}
  <div class="sec"><h2>${t("Höhenprofil")}</h2>${profileSVG(res)}
    <p class="note">${t("m über NHN, waagerecht m ab A. Sand = Gelände, grau = Gebäude, grün = Bewuchs (Korridor ±3 m). Blaue Hülle = 1. Fresnelzone, gestrichelt = Sichtlinie, gelb = stärkste Beugungskanten.")}</p></div>
  <div class="sec"><h2>${t("Endpunkte")}</h2><div class="wrap"><table>
    <tr><th></th><th>A</th><th>B</th></tr>
    <tr><td>${t("Name")}</td><td>${esc(a.name)}</td><td>${esc(b.name)}</td></tr>
    <tr><td>${t("Antenne ü. Grund")}</td><td class="n">${fmt(a.height_m[0], 1)}–${fmt(a.height_m[1], 1)} m</td><td class="n">${fmt(b.height_m[0], 1)}–${fmt(b.height_m[1], 1)} m</td></tr>
    <tr><td>${t("Antenne NHN")}</td><td class="n">${fmt(res.geometry.z_a_nhn, 1)} m</td><td class="n">${fmt(res.geometry.z_b_nhn, 1)} m</td></tr>
    <tr><td>${t("Aufstellung")}</td><td>${indoor[a.indoor ?? null]}</td><td>${indoor[b.indoor ?? null]}</td></tr>
    <tr><td>${t("Gerät")}</td><td>${device(a.device)}</td><td>${device(b.device)}</td></tr>
  </table></div>
  <h2 style="margin-top:12px">${t("Stärkste Beugungskanten")}</h2><div class="wrap"><table>
    <tr><th>${t("Abstand")}</th><th>${t("Art")}</th><th>${t("ü. Grund")}</th><th>ν</th><th>J(ν)</th></tr>
    ${res.geometry.edges.map(x => `<tr><td class="n">${fmt(x.d_m, 0)} m</td><td>${esc(t(x.art))}</td><td class="n">${fmt(x.h_agl, 1)} m</td><td class="n">${fmt(x.nu, 2)}</td><td class="n">${fmt(x.J_dB, 1)} dB</td></tr>`).join("")}
  </table></div>
  <p class="note">${t("Kleinste Freiheit {clear} × r₁. L_b nach P.1812 (Punktrechnung) {lb} dB, ohne Bewuchs {lb_bare} dB. Bewuchstiefe am Strahl bei den Endpunkten {veg} m.", {
    clear: fmt(res.geometry.min_clear_r1, 2), lb: fmt(res.geometry.Lb_ref, 1),
    lb_bare: fmt(res.geometry.Lb_ohne_bewuchs, 1), veg: fmt(res.geometry.d_veg_m, 0) })}</p></div>`;

  const rows = Object.entries(res.models);
  $("#t_models").innerHTML = `<div class="sec"><h2>${t("Modellfamilien")}</h2>${modelSVG(res, meas)}
    <div class="wrap" style="margin-top:8px"><table>
      <tr><th>${t("Modell")}</th><th>L_b</th><th>P_rx</th><th>10–90 %</th><th>${t("P(Paket)")}</th>${meas && meas.signal != null ? `<th>${t("Mess − Mod.")}</th>` : ""}</tr>
      ${rows.map(([k, m]) => `<tr class="${k === "ENS" ? "ens" : ""}"><td>${names[k] || k}</td>
        <td class="n">${fmt(m.Lb[1], 1)}</td><td class="n">${fmt(m.prx[1], 1)}</td>
        <td class="n">${fmt(m.prx[0], 0)}…${fmt(m.prx[2], 0)}</td><td class="n">${fmt(m.p_rx, 2)}</td>
        ${meas && meas.signal != null ? `<td class="n">${fmt(meas.signal - m.prx[1], 1)}</td>` : ""}</tr>`).join("")}
    </table></div>
    <p class="note">${t("Alle Familien auf denselben Zufallsziehungen ({draws} je Modell) für Sendeleistung, Antennengewinne, Rauschzahl, Antennenhöhen und Fensterdämpfung; P_rx je Paket inkl. Schwund σ = {sigma} dB. Unterschiede zwischen den Zeilen sind reine Modellunterschiede. M4 gilt nur bis 660 m.", { draws: res.draws, sigma: fmt(res.fading_sigma_db, 0) })}</p></div>
    ${methodHTML()}`;
}

// Walk layer scored against the models (layer data with a summary).
export function renderWalk(d) {
  const s = d.summary, st = d.stats || {}, names = NAMES();
  $("#t_walk").innerHTML = `<div class="sec"><h2>${t("Rundgang gegen Modelle")}</h2>
    ${st.text ? `<p class="note" style="margin:0 0 2px">${esc(st.text)}</p>` : ""}
    ${st.basis ? `<p class="note" style="margin:0 0 8px">${esc(st.basis)}</p>` : ""}
    <div class="wrap"><table><tr><th>${t("Modell")}</th><th>Bias</th><th>MAE</th><th title="${t("Anteil der Messungen im 80-%-Band des Modells")}">80 %</th><th>${t("Gew.")}</th><th>Brier</th></tr>
      ${Object.entries(s.models).map(([m, r]) => `<tr class="${m === "ENS" ? "ens" : ""}"><td>${names[m] || m}</td>
        <td class="n">${fmt(r.bias_db, 1)}</td><td class="n">${fmt(r.mae_db, 1)}</td>
        <td class="n">${r.coverage80 == null ? "–" : fmt(r.coverage80 * 100, 0) + " %"}</td>
        <td class="n">${r.weight == null ? "–" : fmt(r.weight, 2)}</td><td class="n">${fmt(r.brier, 3)}</td></tr>`).join("")}
    </table></div>
    <p class="note">${t("{packets} Messpunkte und {slots} Zeitfenster bewertet, {skipped} außerhalb der Szene.", { packets: s.packets, slots: s.slots, skipped: s.skipped })}
      ${t("Bias und MAE in dB, Bias = Messung − Modell (negativ: Modell zu optimistisch). „80 %“ = Anteil der Messungen im 80-%-Band des Modells. Brier bewertet die vorhergesagte Empfangswahrscheinlichkeit je Zeitfenster (0 = perfekt). Einstellungen im ⚙ der Ebene „Rundgang“.")}</p>
  </div>`;
}

function profileSVG(res) {
  const L = res.profile, G = res.geometry;
  const w = 344, h = 200, ml = 34, mr = 8, mt = 10, mb = 22, n = L.d.length;
  const zmin = Math.min(...L.ground) - 3, zmax = Math.max(...L.surface, G.z_a_nhn, G.z_b_nhn) + 8;
  const X = d => ml + (w - ml - mr) * d / L.L, Y = z => mt + (h - mt - mb) * (1 - (z - zmin) / (zmax - zmin));
  const path = arr => {
    let p = ""; const st = Math.max(1, Math.floor(n / 500));
    for (let i = 0; i < n; i += st) p += (p ? "L" : "M") + X(L.d[i]).toFixed(1) + " " + Y(arr[i]).toFixed(1);
    return p + `L${X(L.L).toFixed(1)} ${Y(arr[n - 1]).toFixed(1)}L${X(L.L).toFixed(1)} ${Y(zmin)}L${X(0).toFixed(1)} ${Y(zmin)}Z`;
  };
  let fu = "", fl = "";
  for (let i = 0; i < n; i += Math.max(1, Math.floor(n / 240))) {
    const z = G.los_nhn[i], r = G.r1_m[i] || 0;
    fu += (fu ? "L" : "M") + X(L.d[i]).toFixed(1) + " " + Y(z + r).toFixed(1);
    fl = "L" + X(L.d[i]).toFixed(1) + " " + Y(z - r).toFixed(1) + fl;
  }
  const stepX = L.L > 2000 ? 500 : L.L > 1000 ? 250 : L.L > 400 ? 100 : 50, ticks = [];
  for (let d = 0; d <= L.L; d += stepX) ticks.push(`<text x="${X(d).toFixed(1)}" y="${h - 8}" text-anchor="middle">${d}</text>`);
  const tz = [], stepZ = zmax - zmin > 60 ? 20 : 10;
  for (let z = Math.ceil(zmin / stepZ) * stepZ; z <= zmax; z += stepZ)
    tz.push(`<line x1="${ml}" x2="${w - mr}" y1="${Y(z).toFixed(1)}" y2="${Y(z).toFixed(1)}" stroke="var(--line-soft)" stroke-width=".5"/><text x="${ml - 4}" y="${(Y(z) + 3).toFixed(1)}" text-anchor="end">${z}</text>`);
  const edges = G.edges.slice(0, 3).map(e => `<line x1="${X(e.d_m).toFixed(1)}" x2="${X(e.d_m).toFixed(1)}" y1="${Y(e.z_nhn).toFixed(1)}" y2="${mt}" stroke="var(--warn)" stroke-width=".8" stroke-dasharray="2 2"/>`).join("");
  return `<svg viewBox="0 0 ${w} ${h}" style="width:100%;height:auto" role="img" aria-label="${t("Höhenprofil")}">
    ${tz.join("")}
    <path d="${fu}${fl}Z" fill="var(--accent)" fill-opacity=".14" stroke="var(--accent)" stroke-width=".6" stroke-opacity=".5"/>
    <path d="${path(L.surface_veg)}" fill="var(--veg)" fill-opacity=".55"/>
    <path d="${path(L.surface_bld)}" fill="var(--bldg)" fill-opacity=".75"/>
    <path d="${path(L.ground)}" fill="var(--terr)" fill-opacity=".95"/>
    ${edges}
    <line x1="${X(0)}" y1="${Y(G.z_a_nhn).toFixed(1)}" x2="${X(L.L)}" y2="${Y(G.z_b_nhn).toFixed(1)}" stroke="var(--ink)" stroke-width="1" stroke-dasharray="4 3"/>
    <circle cx="${X(0)}" cy="${Y(G.z_a_nhn).toFixed(1)}" r="3" fill="var(--accent)"/><circle cx="${X(L.L)}" cy="${Y(G.z_b_nhn).toFixed(1)}" r="3" fill="var(--accent)"/>
    <text x="${X(0) + 4}" y="${(Y(G.z_a_nhn) - 5).toFixed(1)}">A</text><text x="${X(L.L) - 10}" y="${(Y(G.z_b_nhn) - 5).toFixed(1)}">B</text>
    ${ticks.join("")}</svg>`;
}

// Received level per model family (10/50/90 %), with the measured value as a vertical line.
function modelSVG(res, meas) {
  const names = NAMES();
  const keys = Object.keys(res.models).filter(k => k !== "ENS").concat(["ENS"]);
  const vals = keys.flatMap(k => res.models[k].prx).concat(meas && meas.signal != null ? [meas.signal] : []);
  const lo = Math.floor(Math.min(...vals) / 10) * 10 - 2, hi = Math.ceil(Math.max(...vals) / 10) * 10 + 2;
  const w = 344, rowH = 20, h = keys.length * rowH + 30, ml = 122, mr = 14;
  const X = v => ml + (w - ml - mr) * (v - lo) / (hi - lo);
  let g = "";
  keys.forEach((k, i) => {
    const m = res.models[k], y = 14 + i * rowH, ens = k === "ENS";
    g += `<line x1="${X(m.prx[0]).toFixed(1)}" x2="${X(m.prx[2]).toFixed(1)}" y1="${y}" y2="${y}" stroke="${ens ? "var(--accent)" : "var(--ink3)"}" stroke-width="${ens ? 3 : 2}" stroke-linecap="round" opacity="${ens ? 1 : .55}"/>
      <circle cx="${X(m.prx[1]).toFixed(1)}" cy="${y}" r="${ens ? 4 : 3}" fill="${ens ? "var(--accent)" : "var(--ink2)"}"/>
      <text x="${ml - 6}" y="${y + 3}" text-anchor="end" fill="${ens ? "var(--ink)" : "var(--ink3)"}">${names[k] || k}</text>`;
  });
  let ax = "";
  for (let v = Math.ceil(lo / 10) * 10; v <= hi; v += 10)
    ax += `<line x1="${X(v).toFixed(1)}" x2="${X(v).toFixed(1)}" y1="6" y2="${h - 24}" stroke="var(--line-soft)" stroke-width=".5"/><text x="${X(v).toFixed(1)}" y="${h - 12}" text-anchor="middle">${v}</text>`;
  const sens = res.models.ENS.sens;
  ax += `<line x1="${X(sens).toFixed(1)}" x2="${X(sens).toFixed(1)}" y1="6" y2="${h - 24}" stroke="var(--bad)" stroke-dasharray="3 3"/>`;
  if (meas && meas.signal != null)
    ax += `<line x1="${X(meas.signal).toFixed(1)}" x2="${X(meas.signal).toFixed(1)}" y1="4" y2="${h - 24}" stroke="var(--ink)" stroke-width="1.6"/><text x="${X(meas.signal).toFixed(1)}" y="${h - 1}" text-anchor="middle" fill="var(--ink)">${t("gemessen")}</text>`;
  return `<svg viewBox="0 0 ${w} ${h}" style="width:100%;height:auto" role="img" aria-label="${t("Empfangspegel je Modellfamilie")}">${ax}${g}</svg>
    <p class="note">${meas && meas.signal != null
      ? t("Empfangspegel je Paket [dBm], 10 % / Median / 90 %. Rot gestrichelt = Empfindlichkeit, schwarz = Messung.")
      : t("Empfangspegel je Paket [dBm], 10 % / Median / 90 %. Rot gestrichelt = Empfindlichkeit.")}</p>`;
}

function methodHTML() {
  return `<details class="sec"><summary><h2>${t("Methodik und Grenzen")}</h2></summary>
    <h2>${t("Wie gerechnet wird")}</h2>
    <p class="note" style="color:var(--ink2)">${t("Direktstrecke A → B über das Laserscan-Profil (2-m-Schritt, Korridor ±3 m). Sieben Modellfamilien auf identischen Monte-Carlo-Ziehungen: ITU-R P.1812-6 (Portierung der offiziellen Referenzimplementierung, 63 Validierungsfälle auf 5·10⁻⁸ dB), dieselbe mit Endgeräte-Clutter Gl. (64), Gebäude als Kanten + Bewuchs nach P.833-10, nur Gelände + P.2108-1, Delta-Bullington, P.1411-12 (bis 660 m), Log-Distanz. Dazu P.2109-2 für Fenster/Gebäudeeindringung, Rauschen kTB + Rauschzahl + Störpegel, LoRa-Schwelle je Preset (SX1262).")}</p>
    <h2 style="margin-top:12px">${t("Grundlagen")}</h2><ul class="src">
      <li><strong>${t("Geometrie:")}</strong> ${t("NRW 3D-Messdaten Laserscanning (3dm_l_las), Geobasis NRW, dl-de/zero-2-0, 1-m-Raster. Gelände aus Klasse 2 + 26, Oberfläche aus 1 + 20.")}</li>
      <li><strong>${t("Gebäude oder Baum:")}</strong> ${t("je 1-m-Zelle über den Anteil der Mehrfachechos und der Klasse 20; über 32 m immer Gebäude [BELEG?].")}</li>
      <li><strong>${t("Messwerte:")}</strong> ${t("Signal = RSSI ohne Rauschanteil, aus RSSI und SNR des Pakets. Nur direkt (0 Hops) empfangene Pakete sind vergleichbar.")}</li>
    </ul>
    <h2 style="margin-top:12px">${t("Grenzen")}</h2><ul class="src">
      <li>${t("P.1812 ist ein Punkt-zu-Fläche-Modell; Knoten knapp über Straßenniveau direkt vor hohen Fassaden liegen außerhalb seines Kalibrierbereichs (M1b ist die pessimistische Klammer).")}</li>
      <li>${t("Reflexionen an Fassaden sind nicht modelliert; sie wirken in Richtung der optimistischen Familien.")}</li>
      <li>${t("Der Laserscan sieht Kronenoberkanten, nicht den Stammraum; Aufnahmedatum der Kacheln unbekannt [BELEG?].")}</li>
      <li>${t("Priors ohne belastbare Quelle: Antennengewinne in realer Montage, Rauschzahl, Störpegel, Schwundbreite [BELEG?].")}</li>
    </ul></details>`;
}
