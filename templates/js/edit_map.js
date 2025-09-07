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

// Add satellite source and layer
map.on('load', () => {
    // Get the first layer ID from the style to ensure basemaps are at the bottom
    const style = map.getStyle();
    const firstLayerId = style.layers[0].id;

    // Initialize enhanced progress tracking
    const updateProgress = initializeProgressTracking(map, 4); // hillshade + 3 basemaps

    // Add sources
    map.addSource('satellite', SATELLITE_SOURCE);
    map.addSource('usgs', USGS_SOURCE);
    map.addSource('terrain', TERRAIN_SOURCE);

    // Add layers before the first existing layer to ensure they're at the bottom
    map.addLayer({
        'id': 'satellite-layer',
        'type': 'raster',
        'source': 'satellite',
        'layout': {
            'visibility': 'none'
        }
    }, firstLayerId);

    map.addLayer({
        'id': 'usgs-layer',
        'type': 'raster',
        'source': 'usgs',
        'layout': {
            'visibility': 'none'
        }
    }, firstLayerId);

    map.addLayer({
        'id': 'terrain-layer',
        'type': 'raster',
        'source': 'terrain',
        'layout': {
            'visibility': 'none'
        }
    }, firstLayerId);

    // Initialize basemap switching
    initializeBasemapSwitching(map);

    // Initialize help popup
    const helpContent = `
        <h3>Edit Layer Help</h3>
        <ul>
            <li><strong>Drawing:</strong> Click to start drawing, double-click to finish</li>
            <li><strong>Reset:</strong> Click "Reset Drawing" to clear all features</li>
            <li><strong>Upload:</strong> Use "Upload GeoJSON" to import existing features</li>
            <li><strong>Save:</strong> Click "Save Features" when done to submit your work</li>
            <li><strong>Location:</strong> Use the location input to navigate to specific coordinates</li>
            <li><strong>Basemap:</strong> Switch between different map backgrounds</li>
        </ul>
        <h4>Supported Location Formats:</h4>
        <ul>
            <li>Degrees: 40°14′18″ N 123°57′39″ W</li>
            <li>JSON: {"latitude": 37.7749, "longitude": -122.4194}</li>
            <li>Google Maps: https://maps.google.com/...</li>
            <li>Plain: 37.7749, -122.4194</li>
        </ul>
    `;
    initializeHelpPopup(helpContent);

    // Initialize location input functionality
    const goBtn = document.getElementById('go-location-btn');
    const locationInput = document.getElementById('location-input');
    
    if (goBtn && locationInput) {
        // Function to go to location
        function goToLocation() {
            const input = locationInput.value.trim();
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
                    // Try using TerraDraw's addFeatures method with proper format
                    try {
                        const feature = {
                            type: 'Feature',
                            geometry: {
                                type: 'Point',
                                coordinates: [coords.lng, coords.lat]
                            },
                            properties: {}
                        };
                        
                        console.log('Attempting to simulate click at location:', coords);
                        
                        // Try simulating a click at the exact location
                        const point = map.project([coords.lng, coords.lat]);
                        const clickEvent = new MouseEvent('click', {
                            clientX: point.x,
                            clientY: point.y,
                            bubbles: true,
                            cancelable: true
                        });
                        
                        // Dispatch the click event on the map container
                        map.getContainer().dispatchEvent(clickEvent);
                        
                        // Show success message and log details
                        showSuccessNotification('Location Added To Geometry');
                        console.log('Location pin clicked - added geometry point:', feature);
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
        xmlhttp.open("POST", 'https://internal.fireatlas.org:9998/delta_upload/' + EDIT_CONFIG.swalename);
        xmlhttp.setRequestHeader("Content-Type", "application/json");
        var geojson_data = JSON.stringify({"data":geojson});
        xmlhttp.send(geojson_data);
    }

    xmlhttp.onreadystatechange = function() {
        if (xmlhttp.readyState == 4 && xmlhttp.status == 200) {
            showSuccessNotification('Upload successful!');
        } else if (xmlhttp.readyState == 4 && xmlhttp.status !== 200) {
            showErrorPopup('Upload failed. Please try again.');
        }
    }
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
                xmlhttp.open("POST", 'https://internal.fireatlas.org:9998/delta_upload/' + EDIT_CONFIG.swalename);
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
