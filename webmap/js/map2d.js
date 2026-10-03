// 2D view: Leaflet with OpenStreetMap. Draws layer payloads and the current link.
import { t } from "./i18n.js";
import { badgeHTML } from "./icons.js";
import { actionsFor, bindToolbar, openMenu, toolbarHTML } from "./actions.js";
import { esc, featureHTML, fmt } from "./util.js";

// A measurement marker: hexagon with a ring in the theme's contrast colour (CSS --hex-ring), so
// it reads on the dark and the light map; an optional count inside.
function hexHTML(s, size) {
  const r = size / 2, pts = [0, 1, 2, 3, 4, 5].map(i => {
    const a = Math.PI / 3 * i - Math.PI / 2;
    return `${(r + r * Math.cos(a)).toFixed(1)},${(r + r * Math.sin(a)).toFixed(1)}`;
  }).join(" ");
  // in style, not as attribute: var() is only defined for CSS properties
  const ring = s.ring ? esc(s.ring) : "var(--hex-ring)";
  const text = s.text ? `<text x="${r}" y="${r}" dy=".35em" text-anchor="middle">${esc(s.text)}</text>` : "";
  return `<svg width="${size}" height="${size}" viewBox="-2 -2 ${size + 4} ${size + 4}"><polygon points="${pts}"
    style="fill:${esc(s.fillColor || "#9e9e9e")};stroke:${ring}" stroke-width="2" stroke-linejoin="round"/>${text}</svg>`;
}

export class Map2D {
  constructor(el, center, handlers) {
    // { onClick(lat, lon), onMove(lat, lon), onSelect(feature, open: show it in the details),
    //   featurePick(feature, latlng) }
    this.h = handlers;
    this.map = L.map(el, { zoomControl: true });
    // without a home position: Germany, to find the own area
    if (center) this.map.setView([center.lat, center.lon], 15); else this.map.setView([51.2, 10.4], 6);
    this.addBasemap();
    // The server caches the OpenStreetMap tiles under data/tiles/ (works offline for areas
    // seen before); the attribution is OSM's and stays.
    L.tileLayer("tiles/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: t("© OpenStreetMap-Mitwirkende"),
    }).addTo(this.map);
    this.layers = {};
    this.nodeMarkers = {};  // node ID -> marker, for the node list
    this.linkGroup = L.layerGroup().addTo(this.map);
    // the click that closes a popup only closes it (it must not also set a point)
    this.map.on("click", e => { if (Date.now() - (this.popupClosedAt || 0) > 300) this.h.onClick(e.latlng.lat, e.latlng.lng); });
    this.map.on("mousemove", e => this.h.onMove?.(e.latlng.lat, e.latlng.lng));
    // right click (long press on touch) on a free spot: what can be done here
    this.map.on("contextmenu", e => {
      if (Date.now() - (this.featureMenuAt || 0) < 300) return;  // a feature's menu opened
      const { lat, lng } = e.latlng;
      openMenu(e.originalEvent.clientX, e.originalEvent.clientY, `${lat.toFixed(5)}, ${lng.toFixed(5)}`,
        actionsFor({ type: "map", lat, lon: lng }));
    });
    this.popupOpen = false;  // live layers skip their refresh while a popup is open
    this.map.on("popupopen", () => { this.popupOpen = true; });
    this.map.on("popupclose", () => { this.popupOpen = false; this.popupClosedAt = Date.now(); });
    new ResizeObserver(() => this.map.invalidateSize()).observe(el);
  }

  clear(id) {
    if (this.layers[id]) { this.map.removeLayer(this.layers[id]); delete this.layers[id]; }
    if (this.marked && this.marked.id === id) this.mark(null);
  }

  // The ring around the measurement point shown in the details (id: its layer); null removes it.
  mark(id, f = null) {
    if (this.marked) { this.map.removeLayer(this.marked.ring); this.marked = null; }
    if (!f) return;
    const [lon, lat] = f.geometry.coordinates, size = ((f.properties._style || {}).size || 18) + 16;
    const ring = L.marker([lat, lon], { interactive: false, keyboard: false, zIndexOffset: 1000,
      icon: L.divIcon({ className: "selring", iconSize: [size, size] }) }).addTo(this.map);
    this.marked = { id, f, ring };
  }

  // payload: GeoJSON FeatureCollection (optionally with .raster) or {type:"raster", image, bounds}
  show(id, payload) {
    const marked = this.marked && this.marked.id === id ? this.marked.f : null;
    this.clear(id);
    const group = L.layerGroup();
    const raster = payload.type === "raster" ? payload : payload.raster;
    if (raster && raster.image) L.imageOverlay(raster.image, raster.bounds, { opacity: 1 }).addTo(group);
    if (payload.type === "FeatureCollection") {
      L.geoJSON(payload, {
        pointToLayer: (f, ll) => {
          const icon = f.properties._icon;
          if (icon) {
            const m = L.marker(ll, { icon: L.divIcon({ className: "ndwrap", html: badgeHTML(icon), iconSize: [0, 0] }),
              riseOnHover: true, opacity: 1 });
            if (icon.hint) m.bindTooltip(icon.hint, { direction: "top", offset: [0, -30] });
            return m;
          }
          const s = f.properties._style || {};
          if (s.shape === "hex") {
            const size = s.size || 18;
            return L.marker(ll, { icon: L.divIcon({ className: "hexmark", html: hexHTML(s, size), iconSize: [size, size],
              iconAnchor: [size / 2, size / 2] }), riseOnHover: true, bubblingMouseEvents: false });
          }
          return L.circleMarker(ll, { radius: s.radius || 6, color: s.color || "#333", weight: s.weight ?? 1,
            fillColor: s.fillColor || s.color || "#3388ff", fillOpacity: s.fillOpacity ?? 0.9, bubblingMouseEvents: false });
        },
        // a click on a feature opens its popup and must not also set a link endpoint
        bubblingMouseEvents: false,
        style: f => {
          const s = f.properties._style || {};
          return { color: s.color || "#3388ff", weight: s.weight ?? 3, opacity: s.opacity ?? 0.9,
            dashArray: s.dash || null, fillOpacity: s.fillOpacity ?? 0.15 };
        },
        onEachFeature: (f, lyr) => {
          const p = f.properties;
          if (p._node_id) this.nodeMarkers[p._node_id] = lyr;
          if (p._label) lyr.bindTooltip(p._label, { permanent: true, direction: "right", className: "lbl", offset: [6, 0] });
          // while another tool waits for a map click, a click on an object goes to that tool
          // (registered before the popup's own click handler, which then shows nothing)
          lyr.on("click", e => { if (this.h.featurePick?.(f, e.latlng)) this.pickedAt = Date.now(); });
          // a measurement point only informs: its details go to the panel on the right
          if (p._panel) lyr.on("click", () => {
            if (Date.now() - (this.pickedAt || 0) < 300) return;
            this.map.closePopup();
            this.mark(id, f);
            this.h.onSelect?.(f, true);
          });
          // built on every opening: the actions depend on the moment (device connected, mission)
          else if (p._title || p._fields) lyr.bindPopup(() => {
            if (Date.now() - (this.pickedAt || 0) < 300) { setTimeout(() => this.map.closePopup()); return document.createElement("div"); }
            const el = document.createElement("div");
            el.innerHTML = featureHTML(p);
            const acts = actionsFor(p._ref, f), box = el.querySelector(".acts");
            box.innerHTML = toolbarHTML(acts);
            bindToolbar(box, acts, p._title || "", () => this.map.closePopup());
            this.mark(null);
            this.h.onSelect?.(f);
            return el.firstElementChild;
          }, { minWidth: 280, maxWidth: 360 });
          lyr.on("contextmenu", e => {
            L.DomEvent.stop(e);
            this.featureMenuAt = Date.now();
            openMenu(e.originalEvent.clientX, e.originalEvent.clientY, p._title || "", actionsFor(p._ref, f));
          });
        },
      }).addTo(group);
    }
    group.addTo(this.map);
    this.layers[id] = group;
    // a redrawn layer (live refresh, other settings) keeps the ring while the point is still there
    const same = marked && (payload.features || []).find(f => f.properties._panel
      && f.properties._title === marked.properties._title && String(f.geometry.coordinates) === String(marked.geometry.coordinates));
    if (same) this.mark(id, same);
  }

  // Link line coloured by the ensemble delivery probability, plus A/B markers.
  setEndpoints(a, b) {
    this.linkGroup.clearLayers();
    for (const [p, tag] of [[a, "A"], [b, "B"]]) {
      if (!p) continue;
      L.marker([p.lat, p.lon], {
        icon: L.divIcon({ className: "abmark", html: tag, iconSize: [26, 26] }), zIndexOffset: 1000,
      }).addTo(this.linkGroup);
    }
    if (a && b) this.line = L.polyline([[a.lat, a.lon], [b.lat, b.lon]], { color: "#00707f", weight: 3, dashArray: "4 6" }).addTo(this.linkGroup);
  }
  // Show a node from the node list: centre it and open its popup. False if it isn't drawn.
  focusNode(id) {
    const m = this.nodeMarkers[id];
    if (!m || !this.map.hasLayer(m)) return false;
    this.map.setView(m.getLatLng(), Math.max(this.map.getZoom(), 16));
    m.openPopup();
    return true;
  }
  // A node selected in the list: into view, without its popup (the list shows its actions).
  panToNode(id) {
    const m = this.nodeMarkers[id];
    if (m && this.map.hasLayer(m)) this.map.panTo(m.getLatLng());
  }
  // The node's feature, for actions that need more than its ID (link endpoint).
  nodeFeature(id) { return this.nodeMarkers[id]?.feature || null; }
  // Marker for a position that is being placed (new site), null removes it.
  setTemp(lat, lon) {
    if (this.temp) { this.map.removeLayer(this.temp); this.temp = null; }
    if (lat === null || lat === undefined) return;
    this.temp = L.circleMarker([lat, lon], { radius: 9, color: "#00707f", weight: 3, fillColor: "#2cc4d6",
      fillOpacity: 0.6, dashArray: "3 3", bubblingMouseEvents: false }).addTo(this.map);
  }
  // Offline overview under the tiles (Natural Earth, public domain; scripts/make_basemap.py):
  // borders, states, rivers and cities show where tiles are missing (offline, never viewed).
  async addBasemap() {
    let data;
    try { data = await (await fetch("vendor/basemap/germany.json")).json(); } catch (_) { return; }
    this.map.createPane("basemap").style.zIndex = 150;  // below the tiles (200)
    L.geoJSON(data, {
      pane: "basemap", interactive: false,
      style: f => ({ className: `bm bm-${f.properties.k}${f.properties.de ? " de" : ""}` }),
      pointToLayer: (f, ll) => L.marker(ll, { pane: "basemap", interactive: false, icon: L.divIcon({
        className: `bm-city${f.properties.pop >= 400000 ? " big" : ""}`, iconSize: [0, 0],
        html: `<i></i><span>${esc(f.properties.name)}</span>` }) }),
    }).addTo(this.map);
    const zoom = () => this.map.getContainer().classList.toggle("bm-far", this.map.getZoom() < 8);
    this.map.on("zoomend", zoom); zoom();
  }
  // The area of an open form (point or bounding box), null removes it.
  setPreview(ring) {
    if (this.preview) { this.map.removeLayer(this.preview); this.preview = null; }
    if (!ring || !ring.length) return;
    this.preview = ring.length === 1
      ? L.circleMarker(ring[0], { radius: 7, color: "#c2410c", weight: 3, fillOpacity: 0.3, interactive: false })
      : L.polygon(ring, { color: "#c2410c", weight: 2, fillOpacity: 0.08, interactive: false });
    this.preview.addTo(this.map);
  }
  // The points of a polygon being drawn (multi pick), null removes them.
  setTempPath(points) {
    if (this.tempPath) { this.map.removeLayer(this.tempPath); this.tempPath = null; }
    if (!points || !points.length) return;
    this.tempPath = L.layerGroup().addTo(this.map);
    L.polygon(points, { color: "#00707f", weight: 2, dashArray: "4 4", fillOpacity: 0.15 }).addTo(this.tempPath);
    for (const [lat, lon] of points)
      L.circleMarker([lat, lon], { radius: 5, color: "#00707f", fillColor: "#2cc4d6", fillOpacity: 1, bubblingMouseEvents: false }).addTo(this.tempPath);
  }
  // The waypoints of a mission being written (numbered stops, small via points, a dashed line
  // between them), null removes them. Not clickable, so map clicks reach the form's pick.
  setDraft(path, sel = null) {
    if (this.draft) { this.map.removeLayer(this.draft); this.draft = null; }
    if (!path || !path.length) return;
    this.draft = L.layerGroup().addTo(this.map);
    L.polyline(path.map(w => [w.lat, w.lon]), { color: "#2cc4d6", weight: 2.5, dashArray: "6 6", interactive: false }).addTo(this.draft);
    let n = 0;
    path.forEach((w, i) => {
      const stop = w.kind !== "via";
      if (stop) n++;
      L.marker([w.lat, w.lon], { interactive: false, keyboard: false, zIndexOffset: 1000, icon: L.divIcon({
        className: `wpdraft${stop ? "" : " via"}${i === sel ? " sel" : ""}`, iconSize: stop ? [22, 22] : [12, 12],
        html: stop ? `<span>${n}</span>` : "" }) })
        .bindTooltip(esc(w.name), { permanent: stop, direction: "right", className: "lbl", offset: [10, 0] }).addTo(this.draft);
    });
  }
  // A traceroute from the node list, null removes it. The way there is the wide line, the way
  // back the dashed one on top; each leg is coloured by the SNR it was heard with. A leg across
  // nodes without a position (or relays that don't name themselves) is grey and dotted.
  setRoute(r) {
    if (this.route) { this.map.removeLayer(this.route); this.route = null; }
    if (!r) return;
    this.route = L.layerGroup().addTo(this.map);
    const points = [];
    const draw = (hops, weight, dash, label, side) => {
      let prev = null, gap = false;
      for (const h of hops) {
        if (h.lat == null) { gap = true; continue; }
        points.push([h.lat, h.lon]);
        if (prev) {
          const snr = h.snr === null ? "?" : fmt(h.snr, 1);
          // a dark casing keeps the way back visible on a way there of the same colour
          if (dash) L.polyline([[prev.lat, prev.lon], [h.lat, h.lon]], { color: "#0b1016", weight: weight + 2.5,
            opacity: 0.9, interactive: false }).addTo(this.route);
          L.polyline([[prev.lat, prev.lon], [h.lat, h.lon]], {
            color: gap ? "#8a8f98" : h.color, weight, opacity: 0.95,
            dashArray: gap ? "2 7" : dash, bubblingMouseEvents: false,
          }).bindTooltip(gap ? t("über Knoten ohne Position, {snr} dB", { snr }) : label(snr),
            { permanent: true, direction: side, className: "lbl" }).addTo(this.route);
        }
        prev = h; gap = false;
      }
    };
    draw(r.towards, 6, null, snr => t("hin {snr} dB", { snr }), "top");
    if (r.back) draw(r.back, 2.5, "6 5", snr => t("zurück {snr} dB", { snr }), "bottom");
    const seen = new Set();
    for (const h of [...r.towards, ...(r.back || [])]) {
      if (h.lat == null || seen.has(h.id)) continue;
      seen.add(h.id);
      L.circleMarker([h.lat, h.lon], { radius: 5, color: "#00707f", weight: 2, fillColor: "#2cc4d6", fillOpacity: 1,
        bubblingMouseEvents: false }).bindTooltip(h.long || h.short || h.id || "?").addTo(this.route);
    }
    if (points.length) this.map.fitBounds(points, { padding: [50, 50], maxZoom: 16 });
  }
  colorLink(color) { if (this.line) this.line.setStyle({ color, dashArray: null, weight: 4 }); }
  fit(a, b) { this.map.fitBounds([[a.lat, a.lon], [b.lat, b.lon]], { padding: [40, 40], maxZoom: 17 }); }
  fitBox(box) { this.map.fitBounds(box, { padding: [40, 40], maxZoom: 16 }); }
}
