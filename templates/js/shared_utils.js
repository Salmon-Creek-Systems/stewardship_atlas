// Shared utilities for webmap and webedit interfaces

// Function to show error popup
function showErrorPopup(message) {
    const popup = document.createElement('div');
    popup.className = 'error-popup';
    popup.innerHTML = `
        <h3>Error</h3>
        <p>${message}</p>
        <button onclick="this.parentElement.remove()" style="margin-top: 10px; padding: 5px 15px; border: none; border-radius: 4px; cursor: pointer;">OK</button>
    `;
    document.body.appendChild(popup);
}

// Function to show success notification
function showSuccessNotification(message, duration = 2000) {
    const notification = document.createElement('div');
    notification.className = 'success-notification';
    notification.textContent = message;
    document.body.appendChild(notification);
    
    // Remove notification after specified duration
    setTimeout(() => {
        notification.remove();
    }, duration);
}

// Function to parse degrees format coordinates
function parseDegreesFormat(input) {
    console.log('Parsing input:', input);
    
    // Try to parse degrees format: "40°14′18″ N  123°57′39″ W"
    const degreesPattern = /(\d+)°(\d+)′(\d+)″\s*([NS])\s*(\d+)°(\d+)′(\d+)″\s*([EW])/;
    const match = input.trim().match(degreesPattern);
    
    if (match) {
        const [, latDeg, latMin, latSec, latDir, lngDeg, lngMin, lngSec, lngDir] = match;
        
        // Convert to decimal degrees
        let lat = parseInt(latDeg) + parseInt(latMin) / 60 + parseInt(latSec) / 3600;
        let lng = parseInt(lngDeg) + parseInt(lngMin) / 60 + parseInt(lngSec) / 3600;
        
        // Apply direction
        if (latDir === 'S') lat = -lat;
        if (lngDir === 'W') lng = -lng;
        
        console.log('Parsed degrees format:', { lat, lng });
        return { lat, lng };
    }
    
    // Try to parse JSON format
    try {
        const jsonCoords = JSON.parse(input.trim());
        if (jsonCoords.latitude && jsonCoords.longitude) {
            console.log('Parsed JSON format:', jsonCoords);
            return { lat: jsonCoords.latitude, lng: jsonCoords.longitude };
        }
    } catch (e) {
        console.log('Not JSON format');
    }
    
    // Try to parse Google Maps link
    const googlePattern = /@(-?\d+\.\d+),(-?\d+\.\d+)/;
    const googleMatch = input.trim().match(googlePattern);
    if (googleMatch) {
        const [, lat, lng] = googleMatch;
        console.log('Parsed Google Maps format:', { lat: parseFloat(lat), lng: parseFloat(lng) });
        return { lat: parseFloat(lat), lng: parseFloat(lng) };
    }
    
    // Try to parse plain comma-separated coordinates
    const commaPattern = /(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)/;
    const commaMatch = input.trim().match(commaPattern);
    if (commaMatch) {
        const [, lat, lng] = commaMatch;
        console.log('Parsed comma format:', { lat: parseFloat(lat), lng: parseFloat(lng) });
        return { lat: parseFloat(lat), lng: parseFloat(lng) };
    }
    
    return null;
}

// Function to validate coordinates
function validateCoordinates(lat, lng) {
    return !isNaN(lat) && !isNaN(lng) && lat >= -90 && lat <= 90 && lng >= -180 && lng <= 180;
}

// Function to initialize help popup functionality
function initializeHelpPopup(helpContent) {
    // Create help popup HTML if it doesn't exist
    if (!document.getElementById('help-popup')) {
        const helpPopup = document.createElement('div');
        helpPopup.id = 'help-popup';
        helpPopup.className = 'help-popup';
        helpPopup.style.display = 'none';
        helpPopup.innerHTML = `
            <div class="help-content">
                <div class="help-header">
                    <h2>Help</h2>
                    <button id="close-help" class="close-button">&times;</button>
                </div>
                <div class="help-body">
                    ${helpContent}
                </div>
            </div>
        `;
        document.body.appendChild(helpPopup);
    }

    // Add event listeners
    const helpLink = document.getElementById('help-link');
    const closeHelp = document.getElementById('close-help');
    const helpPopup = document.getElementById('help-popup');

    if (helpLink) {
        helpLink.addEventListener('click', (e) => {
            e.preventDefault();
            helpPopup.style.display = 'flex';
        });
    }

    if (closeHelp) {
        closeHelp.addEventListener('click', () => {
            helpPopup.style.display = 'none';
        });
    }

    // Close popup when clicking outside
    helpPopup.addEventListener('click', (e) => {
        if (e.target.id === 'help-popup') {
            helpPopup.style.display = 'none';
        }
    });

    // Close popup with Escape key
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            helpPopup.style.display = 'none';
        }
    });
}

// Function to initialize enhanced progress tracking
function initializeProgressTracking(map, totalLayers) {
    const progressBar = document.getElementById('loading-progress');
    const progressFill = progressBar.querySelector('.progress-fill');
    const progressText = progressBar.querySelector('.progress-text');
    let loadedLayers = 0;

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

    return updateProgress;
}

// Function to initialize basemap switching
function initializeBasemapSwitching(map) {
    const basemapSelect = document.getElementById('basemap-select');
    if (!basemapSelect) return;

    basemapSelect.addEventListener('change', (event) => {
        const selectedBasemap = event.target.value;
        
        // Hide all basemap layers first
        const layers = map.getStyle().layers;
        for (const layer of layers) {
            if (layer.id === 'basemap-layer' || 
                layer.id === 'hillshade-layer' ||
                layer.id === 'satellite-layer' || 
                layer.id === 'usgs-layer' || 
                layer.id === 'terrain-layer') {
                map.setLayoutProperty(layer.id, 'visibility', 'none');
            }
        }
        
        // Show the selected basemap
        switch (selectedBasemap) {
            case 'basemap':
            case 'hillshade':
                map.setLayoutProperty('basemap-layer', 'visibility', 'visible');
                break;
            case 'satellite':
                map.setLayoutProperty('satellite-layer', 'visibility', 'visible');
                break;
            case 'usgs':
                map.setLayoutProperty('usgs-layer', 'visibility', 'visible');
                break;
            case 'terrain':
                map.setLayoutProperty('terrain-layer', 'visibility', 'visible');
                break;
        }
    });
}
