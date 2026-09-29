"""Data layers. To add one: write a Layer subclass in a new module and list it here."""

from meshplay.mapapp.layers.coord import CoordLayer
from meshplay.mapapp.layers.coverage import CoverageLayer
from meshplay.mapapp.layers.nodes import NodesLayer
from meshplay.mapapp.layers.scene import SceneLayer
from meshplay.mapapp.layers.sites import SitesLayer
from meshplay.mapapp.layers.walk import WalkLayer

ALL = [SitesLayer(), NodesLayer(), CoordLayer(), WalkLayer(), CoverageLayer(), SceneLayer()]
BY_ID = {layer.id: layer for layer in ALL}
