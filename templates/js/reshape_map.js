// Reshape page: edit the geometry of one existing feature of the edited layer.
//
// Click a feature -> it loads into TerraDraw's select mode with draggable
// vertices (or, for a point, a draggable point) -> Save sends a `reshape`
// delta keyed by the feature's atlas_id. Properties are never touched; use the
// Edit page for those. One feature at a time.

const map = new maplibregl.Map(MAP_CONFIG);
map.addControl(new MaplibreLegendControl.MaplibreLegendControl(LEGEND_TARGETS, {reverseOrder: false}), 'bottom-left');

const LAYER = EDIT_CONFIG.layerName;
// TerraDraw rejects coordinates finer than its precision (9 places) and drops
// the feature silently, so loaded geometry is rounded to match.
const COORD_PRECISION = 9;
const TD_MODE_FOR_GEOMETRY = {Point: 'point', LineString: 'linestring', Polygon: 'polygon'};

const vertexEditing = {
    feature: {
        draggable: true,
        coordinates: {midpoints: true, draggable: true, deletable: true}
    }
};

const td = new terraDraw.TerraDraw({
    adapter: new terraDraw.TerraDrawMapLibreGLAdapter({map: map, lib: maplibregl}),
    modes: [
        // Registered so loaded features validate against their own mode.
        new terraDraw.TerraDrawPointMode(),
        new terraDraw.TerraDrawLineStringMode(),
        new terraDraw.TerraDrawPolygonMode(),
        new terraDraw.TerraDrawSelectMode({
            flags: {
                point: {feature: {draggable: true}},
                linestring: vertexEditing,
                polygon: vertexEditing,
            },
            // No Delete key: removing the feature is the Edit page's job, and a
            // stray keypress would leave nothing to save.
            keyEvents: {deselect: 'Escape', delete: null},
        }),
    ],
});
td.start();
td.setMode('select');

// The state of the one feature being reshaped, or null.
let loaded = null;   // {atlasId, tdId, label}
let layerFeaturesById = {};

const statusEl = document.getElementById('reshape-status');
const saveBtn = document.getElementById('reshape-save-button');
const cancelBtn = document.getElementById('reshape-cancel-button');

function setStatus(text) { statusEl.textContent = text; }

function setLoaded(state) {
    loaded = state;
    saveBtn.disabled = !state;
    cancelBtn.disabled = !state;
    setStatus(state ? `Reshaping: ${state.label}` : 'Click a feature to reshape it.');
}

function sourceDataUrl() {
    return MAP_CONFIG.style.sources[LAYER].data;
}

function withCacheBust(url) {
    return url + (url.includes('?') ? '&' : '?') + 't=' + Date.now();
}

// The map's rendered features are tiled and simplified, so the exact geometry
// comes from the layer file itself, indexed by atlas_id.
async function loadLayerFeatures() {
    const response = await fetch(withCacheBust(sourceDataUrl()));
    const fc = await response.json();
    layerFeaturesById = {};
    (fc.features || []).forEach(f => {
        const id = (f.properties || {}).atlas_id;
        if (id) layerFeaturesById[id] = f;
    });
}

function roundCoords(coords) {
    if (typeof coords[0] === 'number') {
        return coords.map(c => Number(c.toFixed(COORD_PRECISION)));
    }
    return coords.map(roundCoords);
}

function editLayerIds() {
    return map.getStyle().layers.filter(l => l.source === LAYER).map(l => l.id);
}

function featureLabel(feature) {
    const p = feature.properties || {};
    return p.name || p.title || p.atlas_id;
}

function loadFeature(feature) {
    const geomType = feature.geometry && feature.geometry.type;
    const mode = TD_MODE_FOR_GEOMETRY[geomType];
    if (!mode) {
        showErrorPopup(`Can't reshape a ${geomType} — only Point, LineString and Polygon features.`);
        return;
    }
    const tdId = td.getFeatureId();
    td.addFeatures([{
        id: tdId,
        type: 'Feature',
        geometry: {type: geomType, coordinates: roundCoords(feature.geometry.coordinates)},
        properties: {mode: mode},
    }]);
    if (!td.hasFeature(tdId)) {
        showErrorPopup('This feature could not be loaded for editing (TerraDraw rejected its geometry — a self-intersecting polygon, perhaps).');
        return;
    }
    td.selectFeature(tdId);
    setLoaded({atlasId: feature.properties.atlas_id, tdId: tdId, label: featureLabel(feature)});
}

map.on('click', (e) => {
    if (loaded) return;  // one at a time; Save or Cancel first
    const pad = 4;
    const hits = map.queryRenderedFeatures(
        [[e.point.x - pad, e.point.y - pad], [e.point.x + pad, e.point.y + pad]],
        {layers: editLayerIds()});
    const hit = hits.find(f => (f.properties || {}).atlas_id);
    if (!hit) return;
    const feature = layerFeaturesById[hit.properties.atlas_id];
    if (!feature) {
        showErrorPopup('That feature is not in the layer file yet — reload the page and try again.');
        return;
    }
    loadFeature(feature);
});

function reset() {
    td.clear();
    td.setMode('select');
    setLoaded(null);
}

cancelBtn.addEventListener('click', reset);

saveBtn.addEventListener('click', async () => {
    if (!loaded) return;
    const edited = td.getSnapshot().find(f => f.id === loaded.tdId);
    if (!edited) {
        showErrorPopup('The shape being edited is gone — Cancel and start again.');
        return;
    }
    const delta = {
        type: 'FeatureCollection',
        layer: LAYER,
        action: 'reshape',
        features: [{type: 'Feature', geometry: edited.geometry,
                    properties: {atlas_id: loaded.atlasId}}],
    };
    saveBtn.disabled = true;
    try {
        const response = await fetch(EDIT_CONFIG.appUrl + '/delta_upload/' + EDIT_CONFIG.swalename, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({data: delta}),
        });
        if (!response.ok) throw new Error(`server returned ${response.status}`);
        // The delta is applied on upload; show the layer as it now is.
        await loadLayerFeatures();
        map.getSource(LAYER).setData(withCacheBust(sourceDataUrl()));
        showSuccessNotification(`Saved new shape for ${loaded.label}`);
        reset();
    } catch (err) {
        saveBtn.disabled = false;
        showErrorPopup('Save failed: ' + err.message);
    }
});

map.on('load', () => {
    initializeProgressTracking(map, 5);  // same bar as the other edit pages
    addEditBasemaps(map, map.getStyle().layers[0].id);

    loadLayerFeatures().catch(err => showErrorPopup('Could not load layer features: ' + err.message));

    initializeHelpPopup(`
        <h3>Reshape Help</h3>
        <ul>
            <li><strong>Pick:</strong> Click a feature of this layer to load it for reshaping</li>
            <li><strong>Vertices:</strong> Drag a vertex to move it; drag a midpoint handle to add a vertex; right-click a vertex to delete it</li>
            <li><strong>Move:</strong> Drag inside the feature (or drag a point) to move it whole</li>
            <li><strong>Save:</strong> Replaces the feature's geometry; its properties are unchanged</li>
            <li><strong>Cancel:</strong> Discards the edit</li>
        </ul>
        <p>Layers with a polygon shape (e.g. square regions) are re-shaped after saving.</p>
    `);

    const goBtn = document.getElementById('go-location-btn');
    const locationInput = document.getElementById('location-input');
    async function goToLocation() {
        const coords = await parseDegreesFormat(locationInput.value.trim());
        if (!coords || !validateCoordinates(coords.lat, coords.lng)) {
            showErrorPopup('Cannot parse that location.');
            return;
        }
        map.setCenter([coords.lng, coords.lat]);
        map.setZoom(14);
    }
    goBtn.addEventListener('click', goToLocation);
    locationInput.addEventListener('keypress', (e) => { if (e.key === 'Enter') goToLocation(); });
});
