"""
game_kit outlet: a printable local version of the Wildfire Resilience board game.

Writes outlets/{name}/:
  index.html   readiness check, links to the original kit, board key, sample evacuation order
  cards.html   local chance cards, 9 per Letter page
  board.pdf    36 x 24 in board rendered with QGIS
  board/*.geojson  the generated overlays (grid, step dots, exits, places...)

The game logic and pages live in game_kit.py (pure Python, tested locally);
this module reads the layers, writes files and renders the board.

Asset config (on top of the shared `game_kit` def):
  roles:        {role: [layer, ...]} for roads, landmarks, safe_places, helispots,
                hazards, water. An unassigned role is reported by the readiness check.
  in_layers:    atlas layers drawn under the overlays (styled as in the runbook)
  label_layers: which of those keep their labels (default roads, creeks)
  title, alert_system, step_m, max_exits, exit_roads, board_size_in, feature_scale
"""

import json
import logging
from pathlib import Path

import game_kit
import versioning

logger = logging.getLogger(__name__)

MM_PER_IN = 25.4


def _read_layer(config, layer_name):
    path = versioning.atlas_path(config, "layers") / layer_name / f"{layer_name}.geojson"
    if not path.exists():
        logger.warning(f"game_kit: layer {layer_name} has no data at {path}")
        return []
    with open(path) as f:
        return json.load(f).get("features", [])


def outlet_game_kit(config, outlet_name):
    asset = config['assets'][outlet_name]
    # Shared-def keys sit under 'config' in the merged asset; per-atlas keys at the top.
    outlet_config = {**asset.get('config', {}), **asset}
    bbox = config['dataswale']['bbox']
    out_dir = versioning.atlas_path(config, "outlets") / outlet_name
    board_dir = out_dir / "board"
    board_dir.mkdir(parents=True, exist_ok=True)

    role_layers = {role: {name: _read_layer(config, name) for name in layers}
                   for role, layers in outlet_config.get('roles', {}).items()}
    kit = game_kit.build_kit(config['name'], bbox, role_layers, outlet_config)

    overlay_paths = {}
    for stem, fc in game_kit.overlay_layers(kit).items():
        path = board_dir / f"{stem}.geojson"
        with open(path, 'w') as f:
            json.dump(fc, f)
        overlay_paths[stem] = path

    with open(out_dir / "cards.html", 'w') as f:
        f.write(game_kit.cards_html(kit))

    board_ok = False
    try:
        board_ok = render_board(config, outlet_config, kit, overlay_paths, out_dir / "board.pdf")
    except Exception:
        logger.exception("game_kit: board render failed")

    with open(out_dir / "index.html", 'w') as f:
        f.write(game_kit.index_html(kit, board_ok))

    summary = {c['key']: c['status'] for c in kit['readiness']}
    logger.info(f"game_kit {config['name']}: board={'ok' if board_ok else 'FAILED'} "
                f"cards={len(kit['cards'])} readiness={summary}")
    return {'status': 'success' if board_ok else 'partial', 'readiness': summary,
            'cards': len(kit['cards']), 'output': str(out_dir)}


# ── board rendering (QGIS) ──────────────────────────────────────────────────

def _style_overlay(layer, stem):
    """Marker and label styling for one generated overlay layer."""
    from qgis.core import QgsFillSymbol, QgsMarkerSymbol, QgsSingleSymbolRenderer

    if stem == "game_grid":
        symbol = QgsFillSymbol.createSimple({
            'style': 'no', 'outline_color': '60,60,60,200',
            'outline_width': '1.0', 'outline_style': 'dash'})
    else:
        marker = {
            "game_grid_labels": {'name': 'circle', 'size': '0', 'outline_style': 'no'},
            "game_steps": {'name': 'circle', 'size': '3.2', 'color': '255,255,255',
                           'outline_color': '0,0,0', 'outline_width': '0.45'},
            "game_exits": {'name': 'triangle', 'size': '11', 'color': '200,30,30',
                           'outline_color': '255,255,255', 'outline_width': '0.8'},
            "game_places": {'name': 'circle', 'size': '11', 'color': '255,255,255',
                            'outline_color': '0,0,0', 'outline_width': '0.9'},
            "game_safe": {'name': 'square', 'size': '11', 'color': '30,140,60',
                          'outline_color': '255,255,255', 'outline_width': '0.8'},
            "game_helispots": {'name': 'circle', 'size': '12', 'color': '250,210,40',
                               'outline_color': '0,0,0', 'outline_width': '0.9'},
        }[stem]
        symbol = QgsMarkerSymbol.createSimple(marker)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))

    labels = {
        # stem: (field, size pt, color, around the point rather than on it)
        "game_grid_labels": ("label", 60, (90, 90, 90), False),
        "game_exits": ("label", 18, (200, 30, 30), True),
        "game_places": ("label", 16, (0, 0, 0), False),
        "game_safe": ("label", 16, (30, 140, 60), True),
        "game_helispots": ("label", 18, (0, 0, 0), False),
    }
    if stem in labels:
        _label(layer, *labels[stem])


def _label(layer, field, size_pt, rgb, around):
    from qgis.core import (QgsPalLayerSettings, QgsTextFormat, QgsTextBufferSettings,
                           QgsVectorLayerSimpleLabeling, QgsUnitTypes)
    from qgis.PyQt.QtGui import QColor, QFont

    settings = QgsPalLayerSettings()
    settings.enabled = True
    settings.fieldName = field
    settings.displayAll = True
    settings.placement = QgsPalLayerSettings.AroundPoint if around else QgsPalLayerSettings.OverPoint
    if around:
        settings.dist = 3
        settings.distUnits = QgsUnitTypes.RenderMillimeters

    text_format = QgsTextFormat()
    font = QFont("Arial")
    font.setBold(True)
    text_format.setFont(font)
    text_format.setSize(size_pt)
    text_format.setSizeUnit(QgsUnitTypes.RenderPoints)
    text_format.setColor(QColor(*rgb))
    buffer = QgsTextBufferSettings()
    buffer.setEnabled(True)
    buffer.setSize(1.0)
    buffer.setSizeUnit(QgsUnitTypes.RenderMillimeters)
    buffer.setColor(QColor(255, 255, 255))
    text_format.setBuffer(buffer)
    settings.setFormat(text_format)

    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)


# Bottom to top.
OVERLAY_ORDER = ["game_grid", "game_steps", "game_safe", "game_helispots",
                 "game_places", "game_exits", "game_grid_labels"]


def _panel_text(kit):
    lines = ["EXITS"]
    lines += [f"  {e['letter']}  {e['name']}" for e in kit['exits']] or ["  none found"]
    lines += ["", "SAFE PLACES"]
    lines += [f"  ■  {p['name']}" for p in kit['safe_places']] or ["  none mapped"]
    if kit['helispots']:
        lines += ["", "HELISPOTS"] + [f"  H  {h['name']}" for h in kit['helispots']]
    lines += ["", "NUMBERED PLACES"]
    places = [f"  {p['number']:>2}  {p['name']}" for p in kit['places']]
    lines += (places[:40] + [f"  … and {len(places) - 40} more"]) if len(places) > 40 else places
    lines += [] if places else ["  none mapped"]
    lines += ["", f"One dot = one step = {kit['step_m']} m of road.",
              "Grid cells 1–12 match the Fire Location spinner."]
    gaps = [c for c in kit['readiness'] if c['status'] != 'ok']
    if gaps:
        lines += ["", "THIS ATLAS IS MISSING"] + [f"  ✗  {c['label']}" for c in gaps]
    lines += ["", "Rules, spinners, vehicles and generic cards:",
              "prototypingresilience.com/diy-game-kit",
              "Game design: Tom Maiorana, UC Davis.",
              "Board and local cards: fireatlas.org"]
    return "\n".join(lines)


def render_board(config, outlet_config, kit, overlay_paths, pdf_path):
    """Render the board PDF: atlas layers, then the overlays, then a side panel."""
    import outlets_qgis
    from qgis.core import (QgsProject, QgsVectorLayer, QgsPrintLayout, QgsLayoutItemMap,
                           QgsLayoutItemLabel, QgsLayoutPoint, QgsLayoutSize, QgsUnitTypes,
                           QgsLayoutExporter, QgsRectangle, QgsCoordinateReferenceSystem,
                           QgsCoordinateTransform)
    from qgis.PyQt.QtGui import QFont

    outlets_qgis.qgis_init()
    project = QgsProject.instance()
    project.clear()
    try:
        layers_config = {x['name']: x for x in config['dataswale']['layers']}
        feature_scale = outlet_config.get('feature_scale', 1.0)
        label_layers = outlet_config.get('label_layers', ['roads', 'creeks'])
        base = []
        for name in outlet_config.get('in_layers', []):
            if name not in layers_config:
                logger.warning(f"game_kit: in_layer {name} not in config")
                continue
            layer = outlets_qgis.load_full_layer(layers_config[name], config)
            if layer is None:
                continue
            outlets_qgis.apply_basic_styling(layer, layers_config[name], config, feature_scale)
            if hasattr(layer, 'setLabelsEnabled') and name not in label_layers:
                layer.setLabelsEnabled(False)       # e.g. address numbers on buildings
            project.addMapLayer(layer)
            base.append(layer)

        overlays = []
        for stem in OVERLAY_ORDER:
            layer = QgsVectorLayer(str(overlay_paths[stem]), stem, "ogr")
            if not layer.isValid() or layer.featureCount() == 0:
                continue
            _style_overlay(layer, stem)
            project.addMapLayer(layer)
            overlays.append(layer)

        width_in, height_in = outlet_config.get('board_size_in', [36, 24])
        page_w, page_h = width_in * MM_PER_IN, height_in * MM_PER_IN
        margin, panel_w = 10, 170

        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        layout.pageCollection().page(0).setPageSize(
            QgsLayoutSize(page_w, page_h, QgsUnitTypes.LayoutMillimeters))

        render_crs = QgsCoordinateReferenceSystem("EPSG:3857")
        b = kit['bbox']
        extent = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), render_crs,
                                        project).transformBoundingBox(
            QgsRectangle(b['west'], b['south'], b['east'], b['north']))

        map_item = QgsLayoutItemMap(layout)
        map_item.attemptMove(QgsLayoutPoint(margin, margin, QgsUnitTypes.LayoutMillimeters))
        map_item.attemptResize(QgsLayoutSize(page_w - 3 * margin - panel_w, page_h - 2 * margin,
                                             QgsUnitTypes.LayoutMillimeters))
        map_item.setCrs(render_crs)
        map_item.setLayers(list(reversed(base + overlays)))     # first in the list draws on top
        map_item.setKeepLayerSet(True)
        map_item.setFrameEnabled(True)
        layout.addLayoutItem(map_item)
        # Pad so exit markers and labels on the atlas edge aren't cut off by the frame.
        extent.grow(0.04 * max(extent.width(), extent.height()))
        map_item.zoomToExtent(extent)

        panel_x = page_w - margin - panel_w
        title = QgsLayoutItemLabel(layout)
        title.setText(f"{kit['title']}\nWildfire Resilience — local board\n↑ N")
        title.setFont(QFont("Arial", 30, QFont.Bold))
        title.attemptMove(QgsLayoutPoint(panel_x, margin, QgsUnitTypes.LayoutMillimeters))
        title.attemptResize(QgsLayoutSize(panel_w, 50, QgsUnitTypes.LayoutMillimeters))
        layout.addLayoutItem(title)

        panel = QgsLayoutItemLabel(layout)
        panel.setText(_panel_text(kit))
        panel.setFont(QFont("Arial", 15))
        panel.attemptMove(QgsLayoutPoint(panel_x, margin + 60, QgsUnitTypes.LayoutMillimeters))
        panel.attemptResize(QgsLayoutSize(panel_w, page_h - 2 * margin - 60,
                                          QgsUnitTypes.LayoutMillimeters))
        layout.addLayoutItem(panel)

        settings = QgsLayoutExporter.PdfExportSettings()
        settings.dpi = 200
        settings.rasterizeWholeImage = False
        settings.forceVectorOutput = True
        result = QgsLayoutExporter(layout).exportToPdf(str(pdf_path), settings)
        if result != QgsLayoutExporter.Success:
            logger.error(f"game_kit: board export failed with code {result}")
            return False
        logger.info(f"game_kit: wrote {pdf_path}")
        return True
    finally:
        project.clear()
        outlets_qgis.qgis_cleanup()


asset_methods = {
    'outlet_game_kit': outlet_game_kit,
}
