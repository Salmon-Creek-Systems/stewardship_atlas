"""
The legend control is pointed at a layer's *label* layer (outlets.py does this
so the legend can show icons). Without installLayerGroupVisibility, unchecking
a line layer such as roads hides only its labels and leaves the lines drawn;
webedit shipped that way until 2026-11-19. Every map script that adds a legend
must install the group sync, and before the legend control is added.
"""
import re
import unittest
from pathlib import Path

JS_DIR = Path(__file__).parent.parent.parent / 'templates' / 'js'


class TestLegendGroupVisibility(unittest.TestCase):
    def test_helper_is_defined_in_shared_utils(self):
        src = (JS_DIR / 'shared_utils.js').read_text()
        self.assertIn('function installLayerGroupVisibility(map)', src)

    def test_every_legend_script_installs_group_visibility_first(self):
        scripts = [p for p in sorted(JS_DIR.glob('*.js'))
                   if 'MaplibreLegendControl(' in p.read_text()]
        self.assertTrue(scripts, 'no script adds a legend; test is out of date')
        for path in scripts:
            src = path.read_text()
            install = re.search(r'installLayerGroupVisibility\(map\)', src)
            legend = src.index('MaplibreLegendControl(')
            with self.subTest(script=path.name):
                self.assertIsNotNone(install, f'{path.name} adds a legend without group visibility')
                self.assertLess(install.start(), legend,
                                f'{path.name} installs group visibility after the legend')


if __name__ == '__main__':
    unittest.main()
