# Vendored libraries and fonts

Served by the map app so the page works without internet. Unmodified copies; update by
replacing the files and the version numbers here.

| What | Version | Source | Licence |
|---|---|---|---|
| `leaflet/` | Leaflet 1.9.4 | cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/ | BSD-2-Clause |
| `three.min.js` | three.js r128 | cdnjs.cloudflare.com/ajax/libs/three.js/r128/ | MIT |
| `proj4.js` | proj4js 2.11.0 | cdnjs.cloudflare.com/ajax/libs/proj4js/2.11.0/ | MIT |
| `basemap/germany.json` | Natural Earth 5.1.2 (1:10m), cut to Germany by `scripts/make_basemap.py` | github.com/nvkelso/natural-earth-vector | public domain |
| `fonts/` | IBM Plex Sans 400/500/600, IBM Plex Mono 400/500 (latin, latin-ext) | fonts.gstatic.com via fonts.googleapis.com | SIL Open Font License 1.1 |

Map tiles are not vendored: the server fetches them from tile.openstreetmap.org when the
page shows an area and keeps them under `data/tiles/` (see `src/meshplay/mapapp/tiles.py`).
