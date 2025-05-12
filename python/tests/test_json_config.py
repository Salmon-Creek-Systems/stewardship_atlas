import unittest
import json
import os
import sys
from pathlib import Path

# Add the parent directory to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from python.json_config import load

class TestJsonConfig(unittest.TestCase):
    def setUp(self):
        # Get the path to the fixtures directory
        self.fixtures_dir = Path(__file__).parent / "fixtures"
        
        # Set up paths to fixture files
        self.primary_path = self.fixtures_dir / "primary.json"
        self.secrets_path = self.fixtures_dir / "secrets.json"
        self.env_path = self.fixtures_dir / "env.json"
    
    def test_basic_interpolation(self):
        result = load(str(self.primary_path))
        
        self.assertEqual(result["app_name"], "MyApp")
        self.assertEqual(result["api_key"], "abc123")
        self.assertEqual(result["environment"], "production")
        self.assertEqual(result["nested"]["value"], "nested_value")
        self.assertEqual(result["array"], ["array_value", "regular_string"])
    
    def test_no_interpolation(self):
        # Create a config without any interpolation
        simple_config = {
            "key": "value",
            "nested": {
                "key": "value"
            }
        }
        simple_config_path = self.fixtures_dir / "simple.json"
        with open(simple_config_path, 'w') as f:
            json.dump(simple_config, f)
        
        try:
            result = load(str(simple_config_path))
            self.assertEqual(result, simple_config)
        finally:
            if simple_config_path.exists():
                simple_config_path.unlink()
    
    def test_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            load("nonexistent.json")
    
    def test_missing_secondary_file(self):
        # Create a config with a non-existent secondary file
        bad_config = {
            "config_sources": {
                "secrets": "nonexistent.json"
            },
            "key": "$secrets::value"
        }
        bad_config_path = self.fixtures_dir / "bad.json"
        with open(bad_config_path, 'w') as f:
            json.dump(bad_config, f)
        
        try:
            with self.assertRaises(FileNotFoundError):
                load(str(bad_config_path))
        finally:
            if bad_config_path.exists():
                bad_config_path.unlink()
    
    def test_missing_key(self):
        # Create a config with a reference to a non-existent key
        bad_config = {
            "config_sources": {
                "secrets": "secrets.json"
            },
            "bad_key": "$secrets::nonexistent"
        }
        bad_config_path = self.fixtures_dir / "bad.json"
        with open(bad_config_path, 'w') as f:
            json.dump(bad_config, f)
        
        try:
            with self.assertRaises(KeyError):
                load(str(bad_config_path))
        finally:
            if bad_config_path.exists():
                bad_config_path.unlink()
    
    def test_missing_config_sources_with_interpolation(self):
        # Create a config with interpolation but no config_sources
        bad_config = {
            "key": "$secrets::value"
        }
        bad_config_path = self.fixtures_dir / "bad.json"
        with open(bad_config_path, 'w') as f:
            json.dump(bad_config, f)
        
        try:
            with self.assertRaises(KeyError) as cm:
                load(str(bad_config_path))
            # self.assertEqual(str(cm.exception), "Variable interpolation requires a 'config_sources' section")
        finally:
            if bad_config_path.exists():
                bad_config_path.unlink()
    
    def test_invalid_config_sources(self):
        # Create a config with invalid config_sources
        bad_config = {
            "config_sources": "not a dict",
            "key": "value"
        }
        bad_config_path = self.fixtures_dir / "bad.json"
        with open(bad_config_path, 'w') as f:
            json.dump(bad_config, f)
        
        try:
            with self.assertRaises(ValueError) as cm:
                load(str(bad_config_path))
            self.assertEqual(str(cm.exception), "'config_sources' must be a dictionary")
        finally:
            if bad_config_path.exists():
                bad_config_path.unlink()

if __name__ == '__main__':
    unittest.main() 