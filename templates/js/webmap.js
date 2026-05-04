// Initialize the map
const map = new maplibregl.Map(MAP_CONFIG);

// Add scale control
map.addControl(new maplibregl.ScaleControl({
    maxWidth: 80,
    unit: 'metric'
}));

// Legend is added inside map.on('load') so dynamically-added layers (COG) exist when it initializes

// Zoom indicator — custom MapLibre control at bottom-left (appears below legend/scale)
const zoomControl = {
    onAdd(map) {
        this._map = map;
        this._container = document.createElement('div');
        this._container.className = 'maplibregl-ctrl';
        this._el = document.createElement('span');
        this._el.id = 'zoom-indicator';
        this._container.appendChild(this._el);
        map.on('zoom', () => this._update());
        return this._container;
    },
    onRemove() { this._container.parentNode.removeChild(this._container); },
    _update() { this._el.textContent = 'z\u2009' + this._map.getZoom().toFixed(1); }
};
map.addControl(zoomControl, 'bottom-left');


// Function to get URL parameters
function getUrlParameter(name) {
    const urlParams = new URLSearchParams(window.location.search);
    return urlParams.get(name);
}

// Function to add a marker at specified coordinates
function addLocationMarker(lat, lng) {
    // Create a marker element
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
    
    // Add the marker to the map
    new maplibregl.Marker(markerEl)
        .setLngLat([lng, lat])
        .addTo(map);
}

// Check for lat/lng in URL parameters
const urlLat = getUrlParameter('lat');
const urlLng = getUrlParameter('lng');
const urlZoom = getUrlParameter('zoom');

console.log('URL parameters found:', { lat: urlLat, lng: urlLng, zoom: urlZoom });

// Add group visibility control for related layers
map.on('styledata', () => {
    // Get all layers with group metadata
    const style = map.getStyle();
    const groupedLayers = {};
    
    // Build group mapping
    style.layers.forEach(layer => {
        if (layer.metadata && layer.metadata.group) {
            if (!groupedLayers[layer.metadata.group]) {
                groupedLayers[layer.metadata.group] = [];
            }
            groupedLayers[layer.metadata.group].push(layer.id);
        }
    });
    
    // Store group mapping globally for visibility control
    window.LAYER_GROUPS = groupedLayers;
});

// Override layer visibility to sync group members
const originalSetLayoutProperty = map.setLayoutProperty;
map.setLayoutProperty = function(layerId, property, value) {
    if (property === 'visibility' && window.LAYER_GROUPS) {
        // Find which group this layer belongs to
        for (const [groupName, layerIds] of Object.entries(window.LAYER_GROUPS)) {
            if (layerIds.includes(layerId)) {
                // Set visibility for all layers in the group
                layerIds.forEach(id => {
                    if (map.getLayer(id)) {
                        originalSetLayoutProperty.call(this, id, property, value);
                    }
                });
                return; // Exit after handling the group
            }
        }
    }
    
    // Default behavior for non-grouped layers
    return originalSetLayoutProperty.call(this, layerId, property, value);
};

// Add satellite source and layer
map.on('load', async () => {
    zoomControl._update();

    // Get the first layer ID from the style to ensure basemaps are at the bottom
    const style = map.getStyle();
    const firstLayerId = style.layers[0].id;

    // Handle URL parameters after map is loaded
    if (urlLat && urlLng) {
        const lat = parseFloat(urlLat);
        const lng = parseFloat(urlLng);
        const zoom = urlZoom ? parseFloat(urlZoom) : 14; // Default zoom if not specified
        
        console.log('Processing URL parameters:', { lat, lng, zoom });
        
        // Validate coordinates
        if (!isNaN(lat) && !isNaN(lng) && lat >= -90 && lat <= 90 && lng >= -180 && lng <= 180) {
            // Validate zoom level (0-22)
            const validZoom = !isNaN(zoom) && zoom >= 0 && zoom <= 22 ? zoom : 14;
            
            console.log('Valid coordinates, centering map...');
            
            // Center map on the specified location
            map.setCenter([lng, lat]);
            map.setZoom(validZoom); // Use the specified or validated zoom level
            
            // Add a marker at the location
            addLocationMarker(lat, lng);
            
            console.log(`Map centered at: ${lat}, ${lng} with zoom: ${validZoom}`);
        } else {
            console.warn('Invalid coordinates in URL parameters');
        }
    }

    // Initialize enhanced progress tracking
    const updateProgress = initializeProgressTracking(map, 5); // basemap + 4 basemaps

    map.addSource('satellite', SATELLITE_SOURCE);
    map.addSource('usgs', USGS_SOURCE);
    map.addSource('terrain', TERRAIN_SOURCE);
    map.addSource('shaded-relief', SHADED_RELIEF_SOURCE);
    
    // Add satellite layer before the first existing layer
    map.addLayer({
        'id': 'satellite-layer',
        'type': 'raster',
        'source': 'satellite',
        'paint': {
            'raster-opacity': 1.0
        }
    }, firstLayerId);

    // Add USGS layer before the first existing layer
    map.addLayer({
        'id': 'usgs-layer',
        'type': 'raster',
        'source': 'usgs',
        'paint': {
            'raster-opacity': 1.0
        }
    }, firstLayerId);

    // Add terrain layer before the first existing layer
    map.addLayer({
        'id': 'terrain-layer',
        'type': 'raster',
        'source': 'terrain',
        'paint': {
            'raster-opacity': 1.0
        }
    }, firstLayerId);

    // Add shaded relief layer before the first existing layer
    map.addLayer({
        'id': 'shaded-relief-layer',
        'type': 'raster',
        'source': 'shaded-relief',
        'paint': {
            'raster-opacity': 1.0
        }
    }, firstLayerId);
    
    // Initialize basemap switching
    initializeBasemapSwitching(map);

    // Hide all basemaps then show the one matching the dropdown's selected value
    const allBasemapLayerIds = ['basemap-layer', 'hillshade-layer', 'satellite-layer', 'usgs-layer', 'terrain-layer', 'shaded-relief-layer'];
    allBasemapLayerIds.forEach(id => {
        try { map.setLayoutProperty(id, 'visibility', 'none'); } catch(e) {}
    });
    const basemapValueToLayer = {
        'basemap': 'basemap-layer', 'hillshade': 'hillshade-layer',
        'satellite': 'satellite-layer', 'usgs': 'usgs-layer',
        'terrain': 'terrain-layer', 'shaded-relief': 'shaded-relief-layer'
    };
    const initialBasemap = document.getElementById('basemap-select').value;
    if (basemapValueToLayer[initialBasemap]) {
        map.setLayoutProperty(basemapValueToLayer[initialBasemap], 'visibility', 'visible');
    }
    // Add COG sources and layers dynamically (protocol requires post-load addition)
    if (typeof COG_SOURCES !== 'undefined') {
        for (const [sourceId, sourceDef] of Object.entries(COG_SOURCES)) {
            map.addSource(sourceId, sourceDef);
        }
        for (const layerDef of COG_LAYERS) {
            map.addLayer(layerDef);
        }
    }

    // Add legend after all layers exist (including dynamically-added COG layers)
    map.addControl(new MaplibreLegendControl.MaplibreLegendControl(LEGEND_TARGETS, {reverseOrder: false}), 'bottom-left');

    // layers we need to load dynamically follow:
    // {dynamic_layers}

    // Add click handler for features and coordinates
    map.on('click', (e) => {
        if (e.originalEvent.metaKey || e.originalEvent.altKey || e.originalEvent.ctrlKey) {
            handleLocationShare(e.lngLat);
            return;
        }

        const features = map.queryRenderedFeatures(e.point);
        for (const feature of features) {
            if (feature.layer.metadata && feature.layer.metadata.conversations_enabled) {
                showConversationPopup(e.lngLat, feature);
                return;
            }
            if (feature.layer.metadata && feature.layer.metadata.show_attributes) {
                showAttributesPopup(e.lngLat, feature);
                return;
            }
            const url = feature.properties.URL;
            if (url && url.trim() !== '') {
                window.open(url, '_blank');
                return;
            }
        }
    });

    function parseConversations(val) {
        if (!val) return [];
        if (Array.isArray(val)) return val;
        try { return JSON.parse(val); } catch(e) { return []; }
    }

    function renderConversationList(convos) {
        if (!convos.length) return '<p style="color:#888;font-size:0.85em;margin:4px 0">No comments yet.</p>';
        return convos.map(c => {
            const ts = c.ts ? new Date(c.ts).toLocaleString() : '';
            return `<div style="border-bottom:1px solid #eee;padding:6px 0;font-size:0.85em">
                <strong>${c.author || 'Anonymous'}</strong> <span style="color:#888">${ts}</span>
                <div>${c.text}</div>
            </div>`;
        }).join('');
    }

    function showConversationPopup(lngLat, feature) {
        const layerSource = feature.layer.source;
        const imageUrl = feature.properties.URL || '';
        const convos = parseConversations(feature.properties.conversations);

        const imageHtml = imageUrl
            ? `<a href="${imageUrl}" target="_blank"><img src="${imageUrl}" style="max-width:100%;max-height:120px;object-fit:cover;display:block;margin-bottom:8px"></a>`
            : '';

        const popupContent = `
            <div style="width:260px;font-family:sans-serif">
                ${imageHtml}
                <div id="conv-list" style="max-height:200px;overflow-y:auto;margin-bottom:8px">
                    ${renderConversationList(convos)}
                </div>
                <div style="border-top:1px solid #ddd;padding-top:8px">
                    <textarea id="conv-text" placeholder="Add a comment..." style="width:100%;box-sizing:border-box;height:60px;resize:vertical;font-size:0.85em"></textarea>
                    <input id="conv-author" placeholder="Your name" style="width:100%;box-sizing:border-box;margin-top:4px;font-size:0.85em">
                    <button id="conv-submit" style="margin-top:6px;padding:4px 12px;cursor:pointer">Submit</button>
                    <span id="conv-status" style="font-size:0.8em;margin-left:8px;color:#666"></span>
                </div>
            </div>`;

        const popup = new maplibregl.Popup({ maxWidth: '300px' })
            .setLngLat(lngLat)
            .setHTML(popupContent)
            .addTo(map);

        popup.getElement().querySelector('#conv-submit').addEventListener('click', async () => {
            const text = popup.getElement().querySelector('#conv-text').value.trim();
            const author = popup.getElement().querySelector('#conv-author').value.trim();
            const status = popup.getElement().querySelector('#conv-status');
            if (!text) { status.textContent = 'Please enter a comment.'; return; }
            if (!feature.properties.atlas_id) { status.textContent = 'This feature has no ID yet — try refreshing the map.'; return; }

            status.textContent = 'Saving...';
            try {
                const resp = await fetch(`${API_URL}/comment/${SWALENAME}/${layerSource}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        text,
                        author: author || 'Anonymous',
                        atlas_id: feature.properties.atlas_id,
                        existing_conversations: convos
                    })
                });
                if (!resp.ok) throw new Error(await resp.text());
                const data = await resp.json();
                popup.getElement().querySelector('#conv-list').innerHTML =
                    renderConversationList(data.conversations);
                popup.getElement().querySelector('#conv-text').value = '';
                popup.getElement().querySelector('#conv-author').value = '';
                status.textContent = '';
            } catch(err) {
                status.textContent = 'Error saving comment.';
                console.error(err);
            }
        });
    }

    function showAttributesPopup(lngLat, feature) {
        const meta = feature.layer.metadata || {};
        const cols = meta.editable_columns || [];
        const props = feature.properties;

        const rawUrl = props.image_url || props.URL || '';
        const url = rawUrl && rawUrl.trim() ? rawUrl.trim() : null;
        let urlHtml = '';
        if (url) {
            const isImage = /\.(jpg|jpeg|png|gif|webp)(\?.*)?$/i.test(url);
            if (isImage) {
                urlHtml = `<a href="${url}" target="_blank"><img src="${url}" style="max-width:100%;max-height:120px;object-fit:cover;display:block;margin-bottom:8px;border-radius:3px"></a>`;
            } else {
                urlHtml = `<a href="${url}" target="_blank" style="display:block;margin-bottom:8px;font-size:0.8em;word-break:break-all">${url}</a>`;
            }
        }

        const nameHtml = props.name
            ? `<div style="font-weight:bold;margin-bottom:6px">${props.name}</div>`
            : '';

        const attrsHtml = cols.map(col => {
            const val = props[col];
            const display = (val !== null && val !== undefined && val !== '') ? val : '<span style="color:#bbb">—</span>';
            return `<div style="display:flex;gap:8px;padding:3px 0;border-bottom:1px solid #f0f0f0;font-size:0.85em">
                <span style="color:#888;min-width:70px;flex-shrink:0">${col}</span>
                <span>${display}</span>
            </div>`;
        }).join('');

        const popupContent = `
            <div style="width:220px;font-family:sans-serif;padding:2px">
                ${urlHtml}
                ${nameHtml}
                ${attrsHtml || '<span style="color:#aaa;font-size:0.85em">No attributes</span>'}
            </div>`;

        new maplibregl.Popup({ maxWidth: '260px' })
            .setLngLat(lngLat)
            .setHTML(popupContent)
            .addTo(map);
    }

    // Mobile long-press for location sharing
    let longPressTimer = null;
    let touchStartPos = null;
    let touchStartTime = null;
    let touchEndTime = null;
    let maxMovement = 0;
    const LONG_PRESS_DELAY = 800; // 800ms delay
    const MOVEMENT_THRESHOLD = 10; // 10px movement threshold

    // Add touch event listeners to the map container
    const mapContainer = map.getContainer();
    
    mapContainer.addEventListener('touchstart', (e) => {
        // Only handle single touch and don't start if already in progress
        if (e.touches.length !== 1 || longPressTimer !== null) return;
        
        const touch = e.touches[0];
        touchStartPos = {
            x: touch.clientX,
            y: touch.clientY
        };
        touchStartTime = Date.now();
        maxMovement = 0;
        
        console.log('Touch start at:', touchStartPos);
        
        // Start long-press timer
        longPressTimer = setTimeout(() => {
            console.log('Long press detected');
            
            if (!touchStartPos) {
                return;
            }
            
            // Get coordinates from the touch position
            try {
                // Convert screen coordinates directly to map coordinates
                const lngLat = map.unproject([touchStartPos.x, touchStartPos.y]);
                handleLocationShare(lngLat);
            } catch (error) {
                console.error('Error in coordinate conversion:', error);
            }
            
            // Clear the timer since we successfully completed the long press
            longPressTimer = null;
        }, LONG_PRESS_DELAY);
    });
    
    mapContainer.addEventListener('touchmove', (e) => {
        // Cancel long-press if finger moves too much
        if (longPressTimer && touchStartPos) {
            const touch = e.touches[0];
            const currentPos = {
                x: touch.clientX,
                y: touch.clientY
            };
            
            const movement = Math.sqrt(
                Math.pow(currentPos.x - touchStartPos.x, 2) + 
                Math.pow(currentPos.y - touchStartPos.y, 2)
            );
            
            maxMovement = Math.max(maxMovement, movement);
            
            if (movement > MOVEMENT_THRESHOLD) {
                console.log('Touch movement detected, canceling long-press');
                clearTimeout(longPressTimer);
                longPressTimer = null;
            }
        }
    });
    
    mapContainer.addEventListener('touchend', (e) => {
        // Only process if we have a valid touch sequence
        if (!touchStartTime) return;
        
        // Cancel long-press on touch end
        if (longPressTimer) {
            console.log('Touch end, canceling long-press');
            clearTimeout(longPressTimer);
            longPressTimer = null;
        }
        touchStartPos = null;
        touchStartTime = null;
        touchEndTime = null;
        maxMovement = 0;
    });
    
    mapContainer.addEventListener('touchcancel', (e) => {
        // Only process if we have a valid touch sequence
        if (!touchStartTime) return;
        
        // Cancel long-press on touch cancel
        if (longPressTimer) {
            console.log('Touch cancel, canceling long-press');
            clearTimeout(longPressTimer);
            longPressTimer = null;
        }
        touchStartPos = null;
        touchStartTime = null;
        touchEndTime = null;
        maxMovement = 0;
    });
    
    // Add a global click listener to see if modifier-clicks are being captured elsewhere
    document.addEventListener('click', (e) => {
        if (e.metaKey || e.altKey || e.ctrlKey) {
            console.log('Global click listener detected modifier-click on:', e.target.tagName, e.target.className);
        }
    });

    // Function to handle location sharing
    function handleLocationShare(lngLat) {
        console.log('Location sharing triggered at:', lngLat);
        
        const coords = {
            latitude: lngLat.lat,
            longitude: lngLat.lng
        };
        
        const format = document.getElementById('coords-format-select').value;
        let textToCopy;

        if (format === 'json') {
            textToCopy = JSON.stringify(coords, null, 2);
        } else if (format === 'google') { // Google Maps link with pin
            textToCopy = `https://www.google.com/maps?q=${lngLat.lat},${lngLat.lng}`;
        } else if (format === 'internal') { // Internal map link
            textToCopy = `https://${window.location.hostname}${window.location.pathname}?lat=${lngLat.lat}&lng=${lngLat.lng}&zoom=${map.getZoom()}`;
        }
        
        console.log('Text to copy:', textToCopy);
        
        // Try to copy to clipboard (works for desktop Alt+click)
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(textToCopy).then(() => {
                console.log('Successfully copied to clipboard');
                showSuccessNotification('Location copied to clipboard!');
            }).catch(err => {
                console.error('Failed to copy coordinates:', err);
                // Fallback: show the text in an alert
                alert(`Select, Copy, and Share:\n\n${textToCopy}`);
            });
        } else {
            // Fallback for browsers that don't support clipboard API
            console.log('Clipboard API not supported, showing alert');
            alert(`Select, Copy, and Share:\n\n${textToCopy}`);
        }
    }
    
    // Function to go to location
    async function goToLocation() {
        const input = document.getElementById('location-input').value.trim();
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
        
        // Redirect to internal map
        const url = `https://${window.location.hostname}${window.location.pathname}?lat=${coords.lat}&lng=${coords.lng}`;
        console.log('Redirecting to:', url);
        window.location.href = url;
    }
    
    // Add event listeners for the Go button and Enter key
    const goBtn = document.getElementById('go-location-btn');
    const locationInput = document.getElementById('location-input');
    
    if (goBtn) {
        goBtn.addEventListener('click', goToLocation);
    }
    
    if (locationInput) {
        locationInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                goToLocation();
            }
        });
    }

    // Initialize help popup
    initializeHelpPopup(document.getElementById('help-popup').querySelector('.help-body').innerHTML);
});
