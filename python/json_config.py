import json
from typing import Dict, Any
from pathlib import Path

def load(config_path: str) -> Dict[str, Any]:
    """
    Load and parse a JSON configuration file with variable interpolation from other files.
    If variable interpolation is used, the primary config file should contain a "config_sources" 
    key that maps labels to file paths.
    
    Args:
        config_path: Path to the primary configuration JSON file
        
    Returns:
        The parsed and interpolated configuration dictionary
        
    Raises:
        FileNotFoundError: If the primary config file or any secondary file doesn't exist
        KeyError: If a referenced key doesn't exist in a secondary file or if config_sources is missing when needed
        json.JSONDecodeError: If any of the JSON files are invalid
    """
    # Validate primary config file exists
    if not Path(config_path).exists():
        raise FileNotFoundError(f"Primary config file not found: {config_path}")
        
    # Load primary config

    with open(config_path, 'r') as f:
        config_str = f.read()
        print(f"Loaded:\n{config_str}")
        config = json.loads(config_str)
    
    # Get config_sources from primary config if it exists
    config_sources = config.get("config_sources", {})
    print(f"config sources at init: {config_sources}")
    if not isinstance(config_sources, dict):
        raise ValueError("'config_sources' must be a dictionary")
    # config.pop("config_sources", None)  # Remove it from the config
    
    # Cache for secondary files
    secondary_cache: Dict[str, Dict[str, Any]] = {}
    
    def load_secondary(label: str) -> Dict[str, Any]:
        """Load and cache a secondary JSON file."""
        if label not in secondary_cache:
            file_path = config_sources.get(label)
            if not file_path:
                raise KeyError(f"Label '{label}' not found in config_sources")
            
            # Resolve relative paths relative to the primary config file
            full_path = Path(config_path).parent / file_path
            if not full_path.exists():
                raise FileNotFoundError(f"Secondary config file not found: {full_path}")
            
            with open(full_path, 'r') as f:
                secondary_cache[label] = json.load(f)
        return secondary_cache[label]
    
    def interpolate_value(value: Any) -> Any:
        """Recursively process a value, performing interpolation at leaf nodes."""
        if isinstance(value, dict):
            return {k: interpolate_value(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [interpolate_value(item) for item in value]
        elif isinstance(value, str) and value.startswith("$"):
            # Split on first occurrence of ::
            parts = value.split("::", 1)
            if len(parts) != 2:
                return value
                
            label, key = parts
            label = label[1:]  # Remove the $ prefix
            
            print(f"config sources in interpolate_value: {config_sources}")
            # Check if we have config_sources when we need it
            if not config_sources:
                raise KeyError("Variable interpolation requires a 'config_sources' section")
            
            secondary = load_secondary(label)
            if key not in secondary:
                raise KeyError(f"Key '{key}' not found in secondary file for label '{label}'")
            return secondary[key]
        return value
    
    return interpolate_value(config)

        
            

