// 2D view: Leaflet with OpenStreetMap. Draws layer payloads and the current link.
import { t } from "./i18n.js";
import { badgeHTML } from "./icons.js";
import { esc, featureHTML, fmt } from "./util.js";

export class Map2D {
  constructor(el, center, handlers) {
    this.h = handlers;          // { onClick(lat, lon), onMove(lat, lon), onFeatureAction(feature, "a"|"b") }
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
    this.map.on("click", e => this.h.onClick(e.latlng.lat, e.latlng.lng));
    this.map.on("mousemove", e => this.h.onMove?.(e.latlng.lat, e.latlng.lng));
    this.popupOpen = false;  // live layers skip their refresh while a popup is open
    this.map.on("popupopen", () => { this.popupOpen = true; });
    this.map.on("popupclose", () => { this.popupOpen = false; });
    new ResizeObserver(() => this.map.invalidateSize()).observe(el);
  }

  clear(id) {
    if (this.layers[id]) { this.map.removeLayer(this.layers[id]); delete this.layers[id]; }
  }

  // payload: GeoJSON FeatureCollection (optionally with .raster) or {type:"raster", image, bounds}
  show(id, payload) {
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
          if (p._title || p._fields) {
            lyr.bindPopup(featureHTML(p));
            lyr.on("popupopen", e => {
              e.popup.getElement().querySelectorAll("[data-set]").forEach(b =>
                b.addEventListener("click", () => { this.h.onFeatureAction(f, b.dataset.set); this.map.closePopup(); }));
            });
          }
        },
      }).addTo(group);
    }
    group.addTo(this.map);
    this.layers[id] = group;
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
}
