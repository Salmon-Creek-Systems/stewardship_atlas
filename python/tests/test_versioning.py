import os
import sys
import unittest
from pathlib import Path
import shutil
import tempfile

# Add the python directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from versioning import atlas_path, atlas_file

class TestVersioning(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for testing
        self.test_dir = tempfile.mkdtemp()
        self.test_config = {
            "name": "test_atlas",
            "data_root": self.test_dir
        }

    def tearDown(self):
        # Clean up the temporary directory
        shutil.rmtree(self.test_dir)

    def test_atlas_path(self):
        """Test that atlas_path correctly constructs paths"""
        # Test with default version
        path = atlas_path(self.test_config)
        expected_path = Path(self.test_dir) / "test_atlas" / "staging"
        self.assertEqual(path, expected_path)

        # Test with custom version
        path = atlas_path(self.test_config, version="prod")
        expected_path = Path(self.test_dir) / "test_atlas" / "prod"
        self.assertEqual(path, expected_path)

        # Test with local path
        path = atlas_path(self.test_config, local_path="data/input")
        expected_path = Path(self.test_dir) / "test_atlas" / "staging" / "data" / "input"
        self.assertEqual(path, expected_path)

        # Test with both custom version and local path
        path = atlas_path(self.test_config, local_path="data/output", version="prod")
        expected_path = Path(self.test_dir) / "test_atlas" / "prod" / "data" / "output"
        self.assertEqual(path, expected_path)

    def test_atlas_file(self):
        """Test that atlas_file correctly creates directories and files"""
        # Test creating a new file
        test_path = Path(self.test_dir) / "test_atlas" / "staging" / "test.txt"
        with atlas_file(test_path, 'w') as f:
            f.write("test content")
        
        # Verify file exists and has correct content
        self.assertTrue(test_path.exists())
        with open(test_path, 'r') as f:
            self.assertEqual(f.read(), "test content")

        # Test reading an existing file
        with atlas_file(test_path, 'r') as f:
            content = f.read()
            self.assertEqual(content, "test content")

        # Test creating a file in a deep directory structure
        deep_path = Path(self.test_dir) / "test_atlas" / "staging" / "deep" / "nested" / "file.txt"
        with atlas_file(deep_path, 'w') as f:
            f.write("deep content")
        
        # Verify deep directory structure was created
        self.assertTrue(deep_path.exists())
        with open(deep_path, 'r') as f:
            self.assertEqual(f.read(), "deep content")

if __name__ == '__main__':
    unittest.main() 