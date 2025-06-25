// Initialize the map
const map = new maplibregl.Map(MAP_CONFIG);

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

    // Initialize progress tracking
    const progressBar = document.getElementById('loading-progress');
    const progressFill = progressBar.querySelector('.progress-fill');
    const progressText = progressBar.querySelector('.progress-text');
    let loadedLayers = 0;
    const totalLayers = 4; // hillshade + 3 basemaps

    // Update progress
    const updateProgress = () => {
        loadedLayers++;
        const percent = (loadedLayers / totalLayers) * 100;
        progressFill.style.width = `${percent}%`;
        progressText.textContent = `Loading layers... ${Math.round(percent)}%`;
        
        if (loadedLayers === totalLayers) {
            setTimeout(() => {
                progressBar.style.display = 'none';
            }, 500);
        }
    };

    // Track layer loading
    map.on('sourcedata', (e) => {
        if (e.isSourceLoaded) {
            updateProgress();
        }
    });

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

    // Handle basemap switching - moved inside load event
    document.getElementById('basemap-select').addEventListener('change', (e) => {
        const selectedBasemap = e.target.value;
        const layerMap = {
            'hillshade': 'hillshade-layer',
            'satellite': 'satellite-layer',
            'usgs': 'usgs-layer',
            'terrain': 'terrain-layer'
        };
        
        // Hide all layers first, with error handling
        ['hillshade-layer', 'satellite-layer', 'usgs-layer', 'terrain-layer'].forEach(layer => {
            try {
                if (map.getLayer(layer)) {
                    map.setLayoutProperty(layer, 'visibility', 'none');
                }
            } catch (error) {
                console.warn(`Layer ${layer} not found`);
            }
        });
        
        // Show selected layer, with error handling
        try {
            const layerId = layerMap[selectedBasemap];
            if (map.getLayer(layerId)) {
                map.setLayoutProperty(layerId, 'visibility', 'visible');
            }
        } catch (error) {
            console.warn(`Could not show selected basemap layer: ${error.message}`);
        }
    });
});

// Add reset button functionality
document.getElementById('reset-button').addEventListener('click', function() {
    if (confirm('Are you sure you want to reset? This will remove all features drawn in this session.')) {
        td.clear();
    }
});

// Add save button functionality
document.getElementById('save-button').addEventListener('click', function() {
    const features = td.getSnapshot();
    
    // Apply control values to features
    features.forEach(feature => {
        if (!feature.properties) {
            feature.properties = {};
        }
        
        EDIT_CONFIG.controls.forEach(control => {
            const value = document.getElementById(control.name).value;
            if (control.type === 'radio') {
		alert("Radio control detected! " + value);
		// feature.properties[control.name] = value;
		const values = JSON.parse(value)
		alert("Parsed: " + values);
                for (const key in values) {
                    feature.properties[key] = values[key];
                }
            } else {
		alert("NON-Radio control detected! " + value); 
                feature.properties[control.name] = value;
            }
        });
    });

    const geojson = {
        "type": "FeatureCollection",
        "layer": EDIT_CONFIG.layerName,
        "features": features
    };
    
    // Send features to server
    for(let i = 0; i < features.length; i++) {
        var xmlhttp = new XMLHttpRequest();
        xmlhttp.open("POST", 'https://internal.fireatlas.org:9998/store/' + EDIT_CONFIG.swalename);
        xmlhttp.setRequestHeader("Content-Type", "application/json");
        var geojson_data = JSON.stringify({"data":geojson});
        alert(geojson_data);
        xmlhttp.send(geojson_data);
    }

    xmlhttp.onreadystatechange = function() {
        if (xmlhttp.readyState == 4 && xmlhttp.status == 200) {
            alert('upload successful!');
        } else if (xmlhttp.readyState == 4 && xmlhttp.status !== 200) {
            alert('looks like something went wrong');
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
                
                // Send to server using the same API as store button
                var xmlhttp = new XMLHttpRequest();
                xmlhttp.open("POST", 'http://fireatlas.org:9998/store/' + EDIT_CONFIG.swalename);
                xmlhttp.setRequestHeader("Content-Type", "application/json");
                var geojson_data = JSON.stringify({"data": geojson});
                xmlhttp.send(geojson_data);
                
                xmlhttp.onreadystatechange = function() {
                    if (xmlhttp.readyState == 4 && xmlhttp.status == 200) {
                        alert('Upload successful!');
                    } else if (xmlhttp.readyState == 4 && xmlhttp.status !== 200) {
                        alert('Error uploading file: ' + (xmlhttp.responseText || 'Unknown error'));
                    }
                }
            } catch (error) {
                alert('Error reading file: ' + error.message);
            }
        };
        reader.readAsText(file);
    };
    
    fileInput.click();
}); 
