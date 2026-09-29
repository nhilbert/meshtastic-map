// Inspector: result of the link layer (profile, models, measured values, methodology) and the
// walk layer's comparison with the models. main.js decides which tabs are shown.
import { $, esc, fmt, grade } from "./util.js";

const NAMES = {
  M1_P1812: "P.1812 volles Profil", M1b_P1812_clut: "P.1812 + Clutter Gl.64",
  M6_P1812_P833: "Gebäude beugen, Bäume dämpfen", M2_P1812_bare: "P.1812 Gelände + P.2108",
  M3_Bullington: "Delta-Bullington", M4_P1411: "P.1411 Kurzstrecke", M5_LogDist: "Log-Distanz",
  ENS: "Ensemble (Median)",
};
const INDOOR = { null: "Antenne außen", open: "Fenster offen", trad: "Fenster zu (Altbau)", lowe: "Fenster zu (Wärmeschutz)" };

export function renderLink(res, a, b) {
  const e = res.models.ENS, g = grade(e.p_rx), marg = e.prx[1] - e.sens;
  const meas = b.measured || a.measured;
  const measHTML = meas && meas.signal !== null && meas.signal !== undefined ? `
    <div class="sec"><h2>Messung am Punkt (${esc(meas.time || "")})</h2>
      <div class="kpis">
        <div class="kpi"><div class="k">Signal gemessen</div><div class="v">${fmt(meas.signal, 1)}<span class="u"> dBm</span></div></div>
        <div class="kpi"><div class="k">Modell (ENS)</div><div class="v">${fmt(e.prx[1], 1)}<span class="u"> dBm</span></div></div>
        <div class="kpi"><div class="k">Messung − Modell</div><div class="v" style="color:var(${Math.abs(meas.signal - e.prx[1]) < 6 ? "--ok" : "--warn"})">${meas.signal - e.prx[1] > 0 ? "+" : ""}${fmt(meas.signal - e.prx[1], 1)}<span class="u"> dB</span></div></div>
      </div>
      <p class="note">RSSI ${fmt(meas.rssi, 0)} dBm, SNR ${fmt(meas.snr, 2)} dB, ${meas.hops} Hops. Signal = RSSI ohne Rauschanteil
        (RSSI − 10·log10(1 + 10^(−SNR/10))). ${meas.signal >= e.prx[0] && meas.signal <= e.prx[2] ? "Liegt im 80-%-Band des Ensembles." : "Liegt außerhalb des 80-%-Bands des Ensembles."}</p>
    </div>` : "";
  $("#t_link").innerHTML = `
  <div class="sec">
    <div class="verdict"><span class="dot ${g[0]}"></span><strong>${g[1]}</strong>
      <span style="color:var(--ink3)">— Ensemble, ein Paket, Preset ${esc(res.preset)}</span></div>
    <div class="kpis">
      <div class="kpi"><div class="k">Distanz</div><div class="v">${fmt(res.d_m, 0)}<span class="u"> m</span></div></div>
      <div class="kpi"><div class="k">Empfangspegel</div><div class="v">${fmt(e.prx[1], 0)}<span class="u"> dBm</span></div></div>
      <div class="kpi"><div class="k">P(Paket)</div><div class="v">${fmt(e.p_rx * 100, 0)}<span class="u"> %</span></div></div>
      <div class="kpi"><div class="k">Reserve</div><div class="v" style="color:var(${marg > 3 ? "--ok" : marg > 0 ? "--warn" : "--bad"})">${marg > 0 ? "+" : ""}${fmt(marg, 1)}<span class="u"> dB</span></div></div>
      <div class="kpi"><div class="k">Pegel 10–90 %</div><div class="v" style="font-size:13px">${fmt(e.prx[0], 0)} … ${fmt(e.prx[2], 0)}</div></div>
      <div class="kpi"><div class="k">Laserdaten</div><div class="v">${fmt(res.measured * 100, 0)}<span class="u"> %</span></div></div>
    </div>
    <p class="note">Streuung über die Modellfamilien: ${fmt(Math.min(...Object.values(res.models).map(m => m.prx[1])), 0)} bis
      ${fmt(Math.max(...Object.values(res.models).map(m => m.prx[1])), 0)} dBm. ${res.measured < 0.95 ? "Ein Teil der Strecke liegt über interpolierten Flächen ohne Laserpunkte." : ""}</p>
  </div>
  ${measHTML}
  <div class="sec"><h2>Höhenprofil</h2>${profileSVG(res)}
    <p class="note">m über NHN, waagerecht m ab A. Sand = Gelände, grau = Gebäude, grün = Bewuchs (Korridor ±3 m).
      Blaue Hülle = 1. Fresnelzone, gestrichelt = Sichtlinie, gelb = stärkste Beugungskanten.</p></div>
  <div class="sec"><h2>Endpunkte</h2><div class="wrap"><table>
    <tr><th></th><th>A</th><th>B</th></tr>
    <tr><td>Name</td><td>${esc(a.name)}</td><td>${esc(b.name)}</td></tr>
    <tr><td>Antenne ü. Grund</td><td class="n">${fmt(a.height_m[0], 1)}–${fmt(a.height_m[1], 1)} m</td><td class="n">${fmt(b.height_m[0], 1)}–${fmt(b.height_m[1], 1)} m</td></tr>
    <tr><td>Antenne NHN</td><td class="n">${fmt(res.geometry.z_a_nhn, 1)} m</td><td class="n">${fmt(res.geometry.z_b_nhn, 1)} m</td></tr>
    <tr><td>Aufstellung</td><td>${INDOOR[a.indoor ?? null]}</td><td>${INDOOR[b.indoor ?? null]}</td></tr>
    <tr><td>Gerät</td><td>${a.device === "t1000e" ? "T1000-E" : "Stabantenne"}</td><td>${b.device === "t1000e" ? "T1000-E" : "Stabantenne"}</td></tr>
  </table></div>
  <h2 style="margin-top:12px">Stärkste Beugungskanten</h2><div class="wrap"><table>
    <tr><th>Abstand</th><th>Art</th><th>ü. Grund</th><th>ν</th><th>J(ν)</th></tr>
    ${res.geometry.edges.map(x => `<tr><td class="n">${fmt(x.d_m, 0)} m</td><td>${esc(x.art)}</td><td class="n">${fmt(x.h_agl, 1)} m</td><td class="n">${fmt(x.nu, 2)}</td><td class="n">${fmt(x.J_dB, 1)} dB</td></tr>`).join("")}
  </table></div>
  <p class="note">Kleinste Freiheit ${fmt(res.geometry.min_clear_r1, 2)} × r₁. L_b nach P.1812 (Punktrechnung) ${fmt(res.geometry.Lb_ref, 1)} dB,
    ohne Bewuchs ${fmt(res.geometry.Lb_ohne_bewuchs, 1)} dB. Bewuchstiefe am Strahl bei den Endpunkten ${fmt(res.geometry.d_veg_m, 0)} m.</p></div>`;

  const rows = Object.entries(res.models);
  $("#t_models").innerHTML = `<div class="sec"><h2>Modellfamilien</h2>${modelSVG(res, meas)}
    <div class="wrap" style="margin-top:8px"><table>
      <tr><th>Modell</th><th>L_b</th><th>P_rx</th><th>10–90 %</th><th>P(Paket)</th>${meas && meas.signal != null ? "<th>Mess − Mod.</th>" : ""}</tr>
      ${rows.map(([k, m]) => `<tr class="${k === "ENS" ? "ens" : ""}"><td>${NAMES[k] || k}</td>
        <td class="n">${fmt(m.Lb[1], 1)}</td><td class="n">${fmt(m.prx[1], 1)}</td>
        <td class="n">${fmt(m.prx[0], 0)}…${fmt(m.prx[2], 0)}</td><td class="n">${fmt(m.p_rx, 2)}</td>
        ${meas && meas.signal != null ? `<td class="n">${fmt(meas.signal - m.prx[1], 1)}</td>` : ""}</tr>`).join("")}
    </table></div>
    <p class="note">Alle Familien auf denselben Zufallsziehungen (${res.draws} je Modell) für Sendeleistung, Antennengewinne,
      Rauschzahl, Antennenhöhen und Fensterdämpfung; P_rx je Paket inkl. Schwund σ = ${fmt(res.fading_sigma_db, 0)} dB.
      Unterschiede zwischen den Zeilen sind reine Modellunterschiede. M4 gilt nur bis 660 m.</p></div>
    ${methodHTML()}`;
}

// Walk layer scored against the models (layer data with a summary).
export function renderWalk(d) {
  const s = d.summary, st = d.stats || {};
  $("#t_walk").innerHTML = `<div class="sec"><h2>Rundgang gegen Modelle</h2>
    ${st.text ? `<p class="note" style="margin:0 0 2px">${esc(st.text)}</p>` : ""}
    ${st.basis ? `<p class="note" style="margin:0 0 8px">${esc(st.basis)}</p>` : ""}
    <div class="wrap"><table><tr><th>Modell</th><th>Bias</th><th>MAE</th><th title="Anteil der Messungen im 80-%-Band des Modells">80 %</th><th>Gew.</th><th>Brier</th></tr>
      ${Object.entries(s.models).map(([m, r]) => `<tr class="${m === "ENS" ? "ens" : ""}"><td>${NAMES[m] || m}</td>
        <td class="n">${fmt(r.bias_db, 1)}</td><td class="n">${fmt(r.mae_db, 1)}</td>
        <td class="n">${r.coverage80 == null ? "–" : fmt(r.coverage80 * 100, 0) + " %"}</td>
        <td class="n">${r.weight == null ? "–" : fmt(r.weight, 2)}</td><td class="n">${fmt(r.brier, 3)}</td></tr>`).join("")}
    </table></div>
    <p class="note">${s.packets} Messpunkte und ${s.slots} Zeitfenster bewertet, ${s.skipped} außerhalb der Szene.
      Bias und MAE in dB, Bias = Messung − Modell (negativ: Modell zu optimistisch). „80 %“ = Anteil der Messungen im 80-%-Band des Modells. Brier bewertet die
      vorhergesagte Empfangswahrscheinlichkeit je Zeitfenster (0 = perfekt). Einstellungen im ⚙ der Ebene „Rundgang“.</p>
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
  return `<svg viewBox="0 0 ${w} ${h}" style="width:100%;height:auto" role="img" aria-label="Höhenprofil">
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
      <text x="${ml - 6}" y="${y + 3}" text-anchor="end" fill="${ens ? "var(--ink)" : "var(--ink3)"}">${NAMES[k] || k}</text>`;
  });
  let ax = "";
  for (let v = Math.ceil(lo / 10) * 10; v <= hi; v += 10)
    ax += `<line x1="${X(v).toFixed(1)}" x2="${X(v).toFixed(1)}" y1="6" y2="${h - 24}" stroke="var(--line-soft)" stroke-width=".5"/><text x="${X(v).toFixed(1)}" y="${h - 12}" text-anchor="middle">${v}</text>`;
  const sens = res.models.ENS.sens;
  ax += `<line x1="${X(sens).toFixed(1)}" x2="${X(sens).toFixed(1)}" y1="6" y2="${h - 24}" stroke="var(--bad)" stroke-dasharray="3 3"/>`;
  if (meas && meas.signal != null)
    ax += `<line x1="${X(meas.signal).toFixed(1)}" x2="${X(meas.signal).toFixed(1)}" y1="4" y2="${h - 24}" stroke="var(--ink)" stroke-width="1.6"/><text x="${X(meas.signal).toFixed(1)}" y="${h - 1}" text-anchor="middle" fill="var(--ink)">gemessen</text>`;
  return `<svg viewBox="0 0 ${w} ${h}" style="width:100%;height:auto" role="img" aria-label="Empfangspegel je Modellfamilie">${ax}${g}</svg>
    <p class="note">Empfangspegel je Paket [dBm], 10 % / Median / 90 %. Rot gestrichelt = Empfindlichkeit${meas && meas.signal != null ? ", schwarz = Messung" : ""}.</p>`;
}

function methodHTML() {
  return `<details class="sec"><summary><h2>Methodik und Grenzen</h2></summary>
    <h2>Wie gerechnet wird</h2>
    <p class="note" style="color:var(--ink2)">Direktstrecke A → B über das Laserscan-Profil (2-m-Schritt, Korridor ±3 m).
      Sieben Modellfamilien auf identischen Monte-Carlo-Ziehungen: ITU-R P.1812-6 (Portierung der offiziellen
      Referenzimplementierung, 63 Validierungsfälle auf 5·10⁻⁸ dB), dieselbe mit Endgeräte-Clutter Gl. (64),
      Gebäude als Kanten + Bewuchs nach P.833-10, nur Gelände + P.2108-1, Delta-Bullington, P.1411-12 (bis 660 m),
      Log-Distanz. Dazu P.2109-2 für Fenster/Gebäudeeindringung, Rauschen kTB + Rauschzahl + Störpegel,
      LoRa-Schwelle je Preset (SX1262).</p>
    <h2 style="margin-top:12px">Grundlagen</h2><ul class="src">
      <li><strong>Geometrie:</strong> NRW 3D-Messdaten Laserscanning (3dm_l_las), Geobasis NRW, dl-de/zero-2-0, 1-m-Raster.
        Gelände aus Klasse 2 + 26, Oberfläche aus 1 + 20.</li>
      <li><strong>Gebäude oder Baum:</strong> je 1-m-Zelle über den Anteil der Mehrfachechos und der Klasse 20;
        über 32 m immer Gebäude [BELEG?].</li>
      <li><strong>Messwerte:</strong> Signal = RSSI ohne Rauschanteil, aus RSSI und SNR des Pakets. Nur direkt (0 Hops)
        empfangene Pakete sind vergleichbar.</li>
    </ul>
    <h2 style="margin-top:12px">Grenzen</h2><ul class="src">
      <li>P.1812 ist ein Punkt-zu-Fläche-Modell; Knoten knapp über Straßenniveau direkt vor hohen Fassaden liegen außerhalb
        seines Kalibrierbereichs (M1b ist die pessimistische Klammer).</li>
      <li>Reflexionen an Fassaden sind nicht modelliert; sie wirken in Richtung der optimistischen Familien.</li>
      <li>Der Laserscan sieht Kronenoberkanten, nicht den Stammraum; Aufnahmedatum der Kacheln unbekannt [BELEG?].</li>
      <li>Priors ohne belastbare Quelle: Antennengewinne in realer Montage, Rauschzahl, Störpegel, Schwundbreite [BELEG?].</li>
    </ul></details>`;
}
