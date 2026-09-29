"""Map app: 2D (OpenStreetMap) and 3D (laser-scan scene) views with pluggable data layers.

Server side of webmap/. Each data layer is a module in meshplay.mapapp.layers that declares its
settings and returns GeoJSON or a raster; tools (e.g. the link calculator) compute on demand.
Start with: python scripts/mapapp.py
"""
