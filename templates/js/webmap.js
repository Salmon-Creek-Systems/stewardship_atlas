// Initialize the map
const map = new maplibregl.Map(MAP_CONFIG);

// Add scale control
map.addControl(new maplibregl.ScaleControl({
    maxWidth: 80,
    unit: 'metric'
}));

// Add legend control
map.addControl(new MaplibreLegendControl.MaplibreLegendControl(LEGEND_TARGETS, {reverseOrder: false}), 'bottom-left');

// Function to get URL parameters
function getUrlParameter(name) {
    const urlParams = new URLSearchParams(window.location.search);
    return urlParams.get(name);
}

// Function to generate grid lines with 0.1 degree spacing
function generateGridLines() {
    const features = [];
    
    // Generate latitude lines (horizontal)
    for (let lat = -90; lat <= 90; lat += 0.1) {
        features.push({
            type: 'Feature',
            geometry: {
                type: 'LineString',
                coordinates: [
                    [-180, lat],
                    [180, lat]
                ]
            },
            properties: {
                type: 'lat',
                value: lat
            }
        });
    }
    
    // Generate longitude lines (vertical)
    for (let lng = -180; lng <= 180; lng += 0.1) {
        features.push({
            type: 'Feature',
            geometry: {
                type: 'LineString',
                coordinates: [
                    [lng, -90],
                    [lng, 90]
                ]
            },
            properties: {
                type: 'lng',
                value: lng
            }
        });
    }
    
    return {
        type: 'FeatureCollection',
        features: features
    };
}

// Function to generate grid labels - simplified test version
function generateGridLabels() {
    const features = [];
    
    // Test with just a few labels first - every 10 degrees
    for (let lat = -90; lat <= 90; lat += 10) {
        features.push({
            type: 'Feature',
            geometry: {
                type: 'Point',
                coordinates: [-122, lat]  // California longitude
            },
            properties: {
                type: 'lat',
                value: lat,
                label: `${lat}°`
            }
        });
    }
    
    for (let lng = -180; lng <= 180; lng += 10) {
        features.push({
            type: 'Feature',
            geometry: {
                type: 'Point',
                coordinates: [lng, 40]  // California latitude
            },
            properties: {
                type: 'lng',
                value: lng,
                label: `${lng}°`
            }
        });
    }
    
    console.log('Test labels generated:', features.length);
    console.log('Sample label:', features[0]);
    return {
        type: 'FeatureCollection',
        features: features
    };
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
    const updateProgress = initializeProgressTracking(map, 4); // basemap + 3 basemaps

    map.addSource('satellite', SATELLITE_SOURCE);
    map.addSource('usgs', USGS_SOURCE);
    map.addSource('terrain', TERRAIN_SOURCE);
    
    // Add satellite layer before the first existing layer
    map.addLayer({
        'id': 'satellite-layer',
        'type': 'raster',
        'source': 'satellite',
        'paint': {
            'raster-opacity': 0.7
        }
    }, firstLayerId);
    
    // Add USGS layer before the first existing layer
    map.addLayer({
        'id': 'usgs-layer',
        'type': 'raster',
        'source': 'usgs',
        'paint': {
            'raster-opacity': 0.7
        }
    }, firstLayerId);
    
    // Add terrain layer before the first existing layer
    map.addLayer({
        'id': 'terrain-layer',
        'type': 'raster',
        'source': 'terrain',
        'paint': {
            'raster-opacity': 0.7
        }
    }, firstLayerId);
    
    // Add grid lines (0.1 degree spacing)
    map.addSource('grid-lines', {
        'type': 'geojson',
        'data': generateGridLines()
    });
    
    map.addLayer({
        'id': 'grid-lines',
        'type': 'line',
        'source': 'grid-lines',
        'paint': {
            'line-color': '#666666',
            'line-width': 0.5,
            'line-opacity': 0.3
        }
    }, firstLayerId);
    
    // Add grid labels
    const gridLabelsData = generateGridLabels();
    console.log('Grid labels generated:', gridLabelsData.features.length, 'labels');
    
    map.addSource('grid-labels', {
        'type': 'geojson',
        'data': gridLabelsData
    });
    
    map.addLayer({
        'id': 'grid-labels',
        'type': 'symbol',
        'source': 'grid-labels',
        'layout': {
            'text-field': '{label}',
            'text-font': ['Arial Unicode MS Regular'],
            'text-size': 12,
            'text-anchor': 'center',
            'text-allow-overlap': true,
            'text-ignore-placement': true,
            'visibility': 'visible'
        },
        'paint': {
            'text-color': '#000000',
            'text-halo-color': '#ffffff',
            'text-halo-width': 2,
            'text-halo-blur': 1,
            'text-opacity': 1
        }
    });
    
    // Initialize basemap switching
    initializeBasemapSwitching(map);

    // Set initial visibility - hide all basemaps except the default one
    map.setLayoutProperty('satellite-layer', 'visibility', 'none');
    map.setLayoutProperty('usgs-layer', 'visibility', 'none');
    map.setLayoutProperty('terrain-layer', 'visibility', 'none');
    // The default basemap should already be visible from the style
    // layers we need to load dynamically follow:
    // {dynamic_layers}

    // Add Alt+Click handler to copy coordinates to clipboard
    map.on('click', (e) => {
        console.log('Map click event fired', {
            ctrlKey: e.originalEvent.ctrlKey,
            metaKey: e.originalEvent.metaKey,
            shiftKey: e.originalEvent.shiftKey,
            altKey: e.originalEvent.altKey,
            type: e.originalEvent.type,
            target: e.originalEvent.target.tagName
        });
        
        // Check if meta key is pressed (Alt+Click)
        if (e.originalEvent.metaKey) {
            console.log('Alt key pressed, processing click');
            handleLocationShare(e.lngLat);
        } else {
            console.log('Alt key not pressed, ignoring click');
        }
    });

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
    
    // Add a global click listener to see if alt-clicks are being captured elsewhere
    document.addEventListener('click', (e) => {
        if (e.metaKey) {
            console.log('Global click listener detected alt-click on:', e.target.tagName, e.target.className);
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
        } else if (format === 'google') { // Google Maps link
            textToCopy = `https://www.google.com/maps/@${lngLat.lat},${lngLat.lng},15z`;
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
    function goToLocation() {
        const input = document.getElementById('location-input').value.trim();
        if (!input) {
            showErrorPopup('Please enter a location to go to.');
            return;
        }
        
        const coords = parseDegreesFormat(input);
        if (!coords) {
            showErrorPopup(`Cannot parse location: "${input}"<br><br>Supported formats:<br>• Degrees: 40°14′18″ N 123°57′39″ W<br>• JSON: {"latitude": 37.7749, "longitude": -122.4194}<br>• Google Maps: https://maps.google.com/...<br>• Plain: 37.7749, -122.4194`);
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
