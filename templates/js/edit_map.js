// Initialize the map
const map = new maplibregl.Map(MAP_CONFIG);

// Add legend control
map.addControl(new MaplibreLegendControl.MaplibreLegendControl(LEGEND_TARGETS, {reverseOrder: false}), 'bottom-left');

// Map the mode string to the correct TerraDraw mode
const modeMap = {
    'point': 'TerraDrawPointMode',
    'linestring': 'TerraDrawLineStringMode',
    'polygon': 'TerraDrawPolygonMode'
};

// Populate the move target dropdown and hide the move section if no targets exist
(function initMoveTargets() {
    const select = document.getElementById('move-target-select');
    const group = document.getElementById('move-button-group');
    if (!MOVE_TARGETS || MOVE_TARGETS.length === 0) {
        if (group) group.style.display = 'none';
        return;
    }
    MOVE_TARGETS.forEach(function(name) {
        const opt = document.createElement('option');
        opt.value = name;
        opt.text = name;
        select.appendChild(opt);
    });
})();

// Initialize Terra Draw with all possible modes
const td = new terraDraw.TerraDraw({
    adapter: new terraDraw.TerraDrawMapLibreGLAdapter({
        map: map,
        lib: maplibregl,
    }),
    modes: [
        new terraDraw.TerraDrawPointMode(),
        new terraDraw.TerraDrawLineStringMode(),
        new terraDraw.TerraDrawPolygonMode()
    ],
});

td.start();

// Set the mode, defaulting to point mode if not found
td.setMode(EDIT_CONFIG.mode || 'TerraDrawPointMode');

// ---- Selection preview ------------------------------------------------------
// Annotate, Delete and Move all act on the features of the edited layer that
// intersect the drawn shapes. The server computes that set (/select_features,
// same predicate as the actions themselves); we highlight it on the map and,
// on annotate pages, list each feature's properties under the form.

const IS_ANNOTATE = EDIT_CONFIG.action === 'annotate';
const EDITABLE_COLS = EDIT_CONFIG.controls.map(c => c.name);
const EMPTY_FC = {type: 'FeatureCollection', features: []};
let selectionFeatures = [];
let activeSelectionIdx = 0;
let selectionRequestId = 0;

// Ask the server which features the current drawing selects. Resolves to the
// feature list, or null when a newer request has superseded this one.
async function refreshSelection() {
    const drawn = td.getSnapshot();
    const requestId = ++selectionRequestId;
    if (drawn.length === 0) {
        setSelection([]);
        return [];
    }
    try {
        const response = await fetch(EDIT_CONFIG.appUrl + '/select_features/' + EDIT_CONFIG.swalename, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                layer: EDIT_CONFIG.layerName,
                selection: {type: 'FeatureCollection', features: drawn}
            })
        });
        const data = await response.json();
        if (requestId !== selectionRequestId) return null;
        if (!response.ok) throw new Error(data.detail || response.statusText);
        setSelection(data.features || []);
        return selectionFeatures;
    } catch (err) {
        if (requestId !== selectionRequestId) return null;
        console.error('Selection preview failed:', err);
        setSelection([]);
        setSelectionHeader('Could not load the selection: ' + err.message);
        return null;
    }
}

function clearSelection() {
    selectionRequestId++;  // drop any in-flight response
    setSelection([]);
}

function setSelection(features) {
    selectionFeatures = features.map((f, i) => ({
        ...f,
        properties: {...(f.properties || {}), _sel_idx: i}
    }));
    activeSelectionIdx = 0;
    updateSelectionHighlight();
    renderSelectionPanel();
}

function updateSelectionHighlight() {
    const source = map.getSource('selection-highlight');
    if (!source) return;  // map not loaded yet; the load handler adds it with current data
    source.setData({type: 'FeatureCollection', features: selectionFeatures});
    SELECTION_ACTIVE_LAYERS.forEach(id => {
        if (map.getLayer(id)) map.setFilter(id, activeSelectionFilter(id));
    });
}

const SELECTION_COLOR = '#ff00c8';
const SELECTION_ACTIVE_COLOR = '#ffd400';
const SELECTION_ACTIVE_LAYERS = ['selection-active-line', 'selection-active-circle'];

function activeSelectionFilter(layerId) {
    const geomFilter = layerId === 'selection-active-circle'
        ? ['==', '$type', 'Point']
        : ['!=', '$type', 'Point'];
    return ['all', geomFilter, ['==', '_sel_idx', IS_ANNOTATE ? activeSelectionIdx : -1]];
}

// Highlight layers sit above the data layers but below TerraDraw's own, so the
// shape being drawn stays on top.
function addSelectionHighlightLayers() {
    map.addSource('selection-highlight', {
        type: 'geojson',
        data: {type: 'FeatureCollection', features: selectionFeatures}
    });
    const tdLayer = map.getStyle().layers.find(l => l.id.startsWith('td-'));
    const before = tdLayer ? tdLayer.id : undefined;
    map.addLayer({
        id: 'selection-fill', type: 'fill', source: 'selection-highlight',
        filter: ['==', '$type', 'Polygon'],
        paint: {'fill-color': SELECTION_COLOR, 'fill-opacity': 0.2}
    }, before);
    map.addLayer({
        id: 'selection-line', type: 'line', source: 'selection-highlight',
        filter: ['!=', '$type', 'Point'],
        paint: {'line-color': SELECTION_COLOR, 'line-width': 4, 'line-opacity': 0.9}
    }, before);
    map.addLayer({
        id: 'selection-circle', type: 'circle', source: 'selection-highlight',
        filter: ['==', '$type', 'Point'],
        paint: {'circle-radius': 9, 'circle-color': 'rgba(0,0,0,0)',
                'circle-stroke-color': SELECTION_COLOR, 'circle-stroke-width': 3}
    }, before);
    map.addLayer({
        id: 'selection-active-line', type: 'line', source: 'selection-highlight',
        filter: activeSelectionFilter('selection-active-line'),
        paint: {'line-color': SELECTION_ACTIVE_COLOR, 'line-width': 6}
    }, before);
    map.addLayer({
        id: 'selection-active-circle', type: 'circle', source: 'selection-highlight',
        filter: activeSelectionFilter('selection-active-circle'),
        paint: {'circle-radius': 12, 'circle-color': 'rgba(0,0,0,0)',
                'circle-stroke-color': SELECTION_ACTIVE_COLOR, 'circle-stroke-width': 4}
    }, before);
}

function selectionTabLabel(feature, i) {
    const p = feature.properties || {};
    const label = p.name || p.atlas_id;
    return (label !== undefined && label !== null && label !== '') ? String(label) : '#' + (i + 1);
}

function setSelectionHeader(text) {
    const header = document.getElementById('selection-header');
    if (header) header.textContent = text;
}

function renderSelectionPanel() {
    const tabs = document.getElementById('selection-tabs');
    const body = document.getElementById('selection-body');
    if (!tabs || !body) return;  // no panel on create pages

    const n = selectionFeatures.length;
    if (n === 0) {
        setSelectionHeader('No features selected. Draw a polygon over features to edit.');
        tabs.innerHTML = '';
        body.innerHTML = '';
        return;
    }
    setSelectionHeader(n === 1 ? '1 feature selected' : `${n} features selected`);
    tabs.innerHTML = n === 1 ? '' : selectionFeatures.map((f, i) =>
        `<button type="button" class="selection-tab${i === activeSelectionIdx ? ' active' : ''}" data-idx="${i}">${escapeHtml(selectionTabLabel(f, i))}</button>`
    ).join('');
    body.innerHTML = renderPropertiesTable(selectionFeatures[activeSelectionIdx].properties, EDITABLE_COLS);
}

const selectionTabsEl = document.getElementById('selection-tabs');
if (selectionTabsEl) {
    selectionTabsEl.addEventListener('click', (e) => {
        const tab = e.target.closest('.selection-tab');
        if (!tab) return;
        activeSelectionIdx = Number(tab.dataset.idx);
        updateSelectionHighlight();
        renderSelectionPanel();
    });
}

if (IS_ANNOTATE) {
    td.on('finish', () => { refreshSelection(); });
}

// Add satellite source and layer
map.on('load', () => {
    // Get the first layer ID from the style to ensure basemaps are at the bottom
    const style = map.getStyle();
    const firstLayerId = style.layers[0].id;

    // Initialize enhanced progress tracking
    const updateProgress = initializeProgressTracking(map, 5); // hillshade + 4 basemaps

    addEditBasemaps(map, firstLayerId);
    addSelectionHighlightLayers();

    // Initialize help popup
    const helpContent = `
        <h3>Edit Layer Help</h3>
        <ul>
            <li><strong>Drawing:</strong> Click to start drawing, double-click to finish</li>
            <li><strong>Reset:</strong> Click "Reset Drawing" to clear all features</li>
            <li><strong>Upload:</strong> Use "Upload GeoJSON" to import existing features</li>
            <li><strong>Save:</strong> Click "Save Features" when done to submit your work</li>
            <li><strong>Selection:</strong> Features your polygon selects are highlighted; on annotate pages their properties are listed below the form</li>
            <li><strong>Location:</strong> Use the location input to navigate to specific coordinates</li>
            <li><strong>Basemap:</strong> Switch between different map backgrounds</li>
        </ul>
        <h4>Supported Location Formats:</h4>
        <ul>
            <li>Degrees: 40°14′18″ N 123°57′39″ W</li>
            <li>JSON: {"latitude": 37.7749, "longitude": -122.4194}</li>
            <li>Google Maps: https://maps.google.com/... (including shortened goo.gl links)</li>
            <li>Plain: 37.7749, -122.4194</li>
        </ul>
    `;
    initializeHelpPopup(helpContent);

    // Initialize location input functionality
    const goBtn = document.getElementById('go-location-btn');
    const locationInput = document.getElementById('location-input');
    
    if (goBtn && locationInput) {
        // Function to go to location
        async function goToLocation() {
            const input = locationInput.value.trim();
            if (!input) {
                showErrorPopup('Please enter a location to go to.');
                return;
            }
            
            const coords = await parseDegreesFormat(input);
            if (!coords) {
                showErrorPopup(`Cannot parse location: "${input}"<br><br>Supported formats:<br>• Degrees: 40°14′18″ N 123°57′39″ W<br>• JSON: {"latitude": 37.7749, "longitude": -122.4194}<br>• Google Maps: https://maps.google.com/... (including shortened goo.gl links)<br>• Plain: 37.7749, -122.4194`);
                return;
            }
            
            // Validate coordinates
            if (!validateCoordinates(coords.lat, coords.lng)) {
                showErrorPopup(`Invalid coordinates: ${coords.lat}, ${coords.lng}<br><br>Latitude must be between -90 and 90<br>Longitude must be between -180 and 180`);
                return;
            }
            
            // Center map on the specified location
            map.setCenter([coords.lng, coords.lat]);
            map.setZoom(14); // Default zoom level
            
            // Add a marker at the location
            const markerEl = document.createElement('div');
            markerEl.className = 'location-marker';
            markerEl.style.cssText = `
                width: 20px;
                height: 20px;
                background-color: #ff0000;
                border: 2px solid #ffffff;
                border-radius: 50%;
                cursor: pointer;
                box-shadow: 0 2px 8px rgba(0,0,0,0.3);
            `;
            
            // Remove any existing markers
            const existingMarkers = document.querySelectorAll('.location-marker');
            existingMarkers.forEach(marker => marker.remove());
            
            // Add the new marker to the map
            const marker = new maplibregl.Marker(markerEl)
                .setLngLat([coords.lng, coords.lat])
                .addTo(map);
            
            // Make marker clickable to create geometry point
            console.log('Adding click listener to location marker, mode:', EDIT_CONFIG.mode);
            
            // Try both approaches - direct element click and MapLibre click
            markerEl.addEventListener('click', (e) => {
                console.log('Direct element click!', e);
                e.stopPropagation();
                addGeometryAtLocation();
            });
            
            markerEl.addEventListener('mousedown', (e) => {
                console.log('Mouse down on marker!', e);
                e.stopPropagation();
            });
            
            // Also try using MapLibre's click event
            marker.getElement().addEventListener('click', (e) => {
                console.log('MapLibre marker click!', e);
                e.stopPropagation();
                addGeometryAtLocation();
            });
            
            function addGeometryAtLocation() {
                console.log('addGeometryAtLocation called');
                // Only create geometry if we're in point mode
                if (EDIT_CONFIG.mode === 'point') {
                    try {
                        const feature = {
                            type: 'Feature',
                            geometry: {
                                type: 'Point',
                                coordinates: [coords.lng, coords.lat]
                            },
                            properties: {
                                mode: 'point'
                            },
                            id: td._store.idStrategy.getId()
                        };
                        
                        td.addFeatures([feature]);
                        
                        // Check what TerraDraw actually has after adding
                        setTimeout(() => {
                            const currentFeatures = td.getSnapshot();
                            console.log('TerraDraw features after addFeatures:', currentFeatures);
                            console.log('Number of features:', currentFeatures.length);
                        }, 100);
                        
                        // Show success message and log details
                        showSuccessNotification('Location Added To Geometry');
                        console.log('Location pin clicked - feature added directly');
                    } catch (error) {
                        console.error('Error adding feature:', error);
                        showErrorPopup('Error adding geometry: ' + error.message);
                    }
                } else {
                    console.log('Not in point mode, current mode:', EDIT_CONFIG.mode);
                }
            }
                
            showSuccessNotification('Location found and map centered!');
        }
        
        goBtn.addEventListener('click', goToLocation);
        
        locationInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                goToLocation();
            }
        });
    }
});

// Add reset button functionality
document.getElementById('reset-button').addEventListener('click', function() {
    if (confirm('Are you sure you want to reset? This will remove all features drawn in this session.')) {
        td.clear();
        clearSelection();
        showSuccessNotification('Drawing reset successfully!');
    }
});

// Add save button functionality
document.getElementById('save-button').addEventListener('click', function() {
    const features = td.getSnapshot();
    
    if (features.length === 0) {
        showErrorPopup('No features to save. Please draw some features first.');
        return;
    }
    
    // Apply control values to features
    features.forEach(feature => {
        if (!feature.properties) {
            feature.properties = {};
        }
        
        EDIT_CONFIG.controls.forEach(control => {
            const value = document.getElementById(control.name).value;
            if (control.type === 'radio') {
                const values = JSON.parse(value);
                for (const key in values) {
                    feature.properties[key] = values[key];
                }
            } else {
                feature.properties[control.name] = value;
            }
        });
    });

    const geojson = {
        "type": "FeatureCollection",
        "layer": EDIT_CONFIG.layerName,
        "action": EDIT_CONFIG.action,
        "features": features
    };
    
    // Send features to server
    for(let i = 0; i < features.length; i++) {
        var xmlhttp = new XMLHttpRequest();
        xmlhttp.open("POST", EDIT_CONFIG.appUrl + '/delta_upload/' + EDIT_CONFIG.swalename);
        xmlhttp.setRequestHeader("Content-Type", "application/json");
        var geojson_data = JSON.stringify({"data":geojson});
        xmlhttp.send(geojson_data);
    }

    xmlhttp.onreadystatechange = function() {
        if (xmlhttp.readyState == 4 && xmlhttp.status == 200) {
            showSuccessNotification('Upload successful!');
            // The delta is applied on upload, so re-query to show the new values.
            if (IS_ANNOTATE) refreshSelection();
        } else if (xmlhttp.readyState == 4 && xmlhttp.status !== 200) {
            showErrorPopup('Upload failed. Please try again.');
        }
    }
});

// Delete button — show confirmation panel
document.getElementById('delete-button').addEventListener('click', function() {
    const features = td.getSnapshot();
    if (features.length === 0) {
        showErrorPopup('No area drawn. Please draw a polygon to select features for deletion.');
        return;
    }
    document.getElementById('delete-confirm').style.display = 'block';
    showSelectionCount('delete-count-text', 'Delete', 'in selected area?');
});

document.getElementById('delete-cancel-button').addEventListener('click', function() {
    document.getElementById('delete-confirm').style.display = 'none';
    if (!IS_ANNOTATE) clearSelection();
});

document.getElementById('delete-confirm-button').addEventListener('click', function() {
    const features = td.getSnapshot();
    if (features.length === 0) {
        showErrorPopup('No area drawn.');
        return;
    }
    const geojson = {
        "type": "FeatureCollection",
        "layer": EDIT_CONFIG.layerName,
        "action": "delete",
        "features": features
    };
    var xmlhttp = new XMLHttpRequest();
    xmlhttp.open("POST", EDIT_CONFIG.appUrl + '/delta_upload/' + EDIT_CONFIG.swalename);
    xmlhttp.setRequestHeader("Content-Type", "application/json");
    xmlhttp.onreadystatechange = function() {
        if (xmlhttp.readyState === 4) {
            if (xmlhttp.status === 200) {
                document.getElementById('delete-confirm').style.display = 'none';
                td.clear();
                clearSelection();
                showSuccessNotification('Features deleted successfully.');
            } else {
                showErrorPopup('Delete failed. Please try again.');
            }
        }
    };
    xmlhttp.send(JSON.stringify({"data": geojson}));
});

// Add photo upload button functionality (point layers only)
const uploadPhotoBtn = document.getElementById('upload-photo-button');
if (uploadPhotoBtn) {
    uploadPhotoBtn.addEventListener('click', function() {
        const fileInput = document.createElement('input');
        fileInput.type = 'file';
        fileInput.accept = 'image/*';

        fileInput.onchange = function(e) {
            const file = e.target.files[0];
            if (!file) return;

            const reader = new FileReader();
            reader.onload = function(ev) {
                // Strip the data URL prefix to get raw base64
                const base64 = ev.target.result.split(',')[1];

                // Get fallback coords from any drawn point in TerraDraw
                let fallback_lat = null, fallback_lon = null;
                const features = td.getSnapshot();
                if (features.length > 0 && features[0].geometry.type === 'Point') {
                    fallback_lon = features[0].geometry.coordinates[0];
                    fallback_lat = features[0].geometry.coordinates[1];
                }

                const payload = {
                    atlas_name: EDIT_CONFIG.swalename,
                    layer_name: EDIT_CONFIG.layerName,
                    image_data: base64,
                    filename: file.name,
                    fallback_lat: fallback_lat,
                    fallback_lon: fallback_lon
                };

                fetch(EDIT_CONFIG.appUrl + '/ingest/web_photo', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                }).then(function(r) {
                    return r.json().then(function(data) { return {ok: r.ok, data: data}; });
                }).then(function(result) {
                    if (result.ok && result.data.status === 'ok') {
                        showSuccessNotification('Photo added at ' + result.data.lat.toFixed(5) + ', ' + result.data.lon.toFixed(5));
                    } else {
                        showErrorPopup('Photo upload failed: ' + (result.data.detail || 'unknown error'));
                    }
                }).catch(function(err) {
                    showErrorPopup('Photo upload error: ' + err.message);
                });
            };
            reader.readAsDataURL(file);
        };

        fileInput.click();
    });
}

// Move to Layer — show confirm panel
document.getElementById('move-button').addEventListener('click', function() {
    const features = td.getSnapshot();
    if (features.length === 0) {
        showErrorPopup('No area drawn. Please draw a polygon to select features to move.');
        return;
    }
    document.getElementById('move-confirm').style.display = 'block';
    showSelectionCount('move-count-text', 'Move', 'to:');
});

document.getElementById('move-cancel-button').addEventListener('click', function() {
    document.getElementById('move-confirm').style.display = 'none';
    if (!IS_ANNOTATE) clearSelection();
});

// Fill a Delete/Move confirm prompt with how many features the action will hit.
async function showSelectionCount(elementId, verb, suffix) {
    const el = document.getElementById(elementId);
    if (el) el.textContent = `${verb} features ${suffix} (checking selection…)`;
    const features = await refreshSelection();
    if (!el || features === null) return;
    const n = features.length;
    el.textContent = `${verb} ${n} feature${n === 1 ? '' : 's'} ${suffix}`;
}

document.getElementById('move-confirm-button').addEventListener('click', function() {
    const features = td.getSnapshot();
    if (features.length === 0) {
        showErrorPopup('No area drawn.');
        return;
    }
    const targetLayer = document.getElementById('move-target-select').value;
    if (!targetLayer) {
        showErrorPopup('Please select a target layer.');
        return;
    }

    const selection = features.length === 1
        ? features[0]
        : {"type": "FeatureCollection", "features": features};

    const payload = {
        source_layer: EDIT_CONFIG.layerName,
        target_layer: targetLayer,
        selection: selection
    };

    fetch(EDIT_CONFIG.appUrl + '/move_features/' + EDIT_CONFIG.swalename, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
    }).then(function(r) {
        return r.json().then(function(data) { return {ok: r.ok, data: data}; });
    }).then(function(result) {
        if (result.ok) {
            document.getElementById('move-confirm').style.display = 'none';
            td.clear();
            clearSelection();
            showSuccessNotification('Moved ' + result.data.moved + ' feature(s) to ' + targetLayer);
        } else {
            showErrorPopup('Move failed: ' + (result.data.detail || 'unknown error'));
        }
    }).catch(function(err) {
        showErrorPopup('Move error: ' + err.message);
    });
});

// Add upload button functionality
document.getElementById('upload-button').addEventListener('click', function() {
    // Create a file input element
    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.accept = '.geojson,application/json';
    
    fileInput.onchange = function(e) {
        const file = e.target.files[0];
        if (!file) return;
        
        const reader = new FileReader();
        reader.onload = function(e) {
            try {
                const geojson = JSON.parse(e.target.result);
                
                // Add layer name to the GeoJSON
                geojson.layer = EDIT_CONFIG.layerName;
                geojson.action = EDIT_CONFIG.action;
                // Send to server using the same API as store button
                var xmlhttp = new XMLHttpRequest();
                xmlhttp.open("POST", EDIT_CONFIG.appUrl + '/delta_upload/' + EDIT_CONFIG.swalename);
                xmlhttp.setRequestHeader("Content-Type", "application/json");
                var geojson_data = JSON.stringify({"data": geojson});
                xmlhttp.send(geojson_data);
                
                xmlhttp.onreadystatechange = function() {
                    if (xmlhttp.readyState == 4 && xmlhttp.status == 200) {
                        showSuccessNotification('Upload successful!');
                    } else if (xmlhttp.readyState == 4 && xmlhttp.status !== 200) {
                        showErrorPopup('Error uploading file: ' + (xmlhttp.responseText || 'Unknown error'));
                    }
                }
            } catch (error) {
                showErrorPopup('Error reading file: ' + error.message);
            }
        };
        reader.readAsText(file);
    };
    
    fileInput.click();
}); 
