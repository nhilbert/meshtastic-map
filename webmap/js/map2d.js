// 2D view: Leaflet with OpenStreetMap. Draws layer payloads and the current link.
import { badgeHTML } from "./icons.js";
import { featureHTML } from "./util.js";

export class Map2D {
  constructor(el, center, handlers) {
    this.h = handlers;          // { onClick(lat, lon), onFeatureAction(feature, "a"|"b") }
    this.map = L.map(el, { zoomControl: true }).setView([center.lat, center.lon], 15);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: "© OpenStreetMap-Mitwirkende",
    }).addTo(this.map);
    this.layers = {};
    this.nodeMarkers = {};  // node ID -> marker, for the node list
    this.linkGroup = L.layerGroup().addTo(this.map);
    this.map.on("click", e => this.h.onClick(e.latlng.lat, e.latlng.lng));
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
  colorLink(color) { if (this.line) this.line.setStyle({ color, dashArray: null, weight: 4 }); }
  fit(a, b) { this.map.fitBounds([[a.lat, a.lon], [b.lat, b.lon]], { padding: [40, 40], maxZoom: 17 }); }
}
