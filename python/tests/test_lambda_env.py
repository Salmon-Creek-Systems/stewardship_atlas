"""lambda_env.prepare: a writable HOME, DuckDB's extension, and secrets as files/env."""
import importlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import lambda_env  # noqa: E402


class TestPrepare(unittest.TestCase):
    def setUp(self):
        importlib.reload(lambda_env)            # _prepared is per container
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / 'home'
        self.home.mkdir()
        self.image = self.tmp / 'image'
        (self.image / '.duckdb' / 'extensions').mkdir(parents=True)
        (self.image / '.duckdb' / 'extensions' / 'spatial.duckdb_extension').write_text('x')
        self.asked = []

    def secret(self, name):
        self.asked.append(name)
        return {'gs': '{"type": "service_account"}', 'ak': 'sk-test\n'}[name]

    def prepare(self, env):
        lambda_env.prepare(env, secret=self.secret, image_duckdb_home=self.image)

    def test_everything_lands_under_home(self):
        env = {'HOME': str(self.home), 'ATLAS_GSPREAD_SECRET': 'gs', 'ATLAS_ANTHROPIC_SECRET': 'ak'}
        self.prepare(env)
        self.assertTrue((self.home / '.duckdb/extensions/spatial.duckdb_extension').exists())
        sa = self.home / '.config/gspread/service_account.json'
        self.assertEqual(sa.read_text(), '{"type": "service_account"}')
        self.assertEqual(env['ANTHROPIC_API_KEY'], 'sk-test')
        self.assertEqual(env['MPLCONFIGDIR'], str(self.home / '.matplotlib'))

    def test_a_second_call_does_nothing(self):
        env = {'HOME': str(self.home), 'ATLAS_GSPREAD_SECRET': 'gs'}
        self.prepare(env)
        self.prepare(env)
        self.assertEqual(self.asked, ['gs'])

    def test_no_secret_names_means_no_secret_calls(self):
        self.prepare({'HOME': str(self.home)})
        self.assertEqual(self.asked, [])

    def test_an_existing_key_is_kept(self):
        env = {'HOME': str(self.home), 'ATLAS_ANTHROPIC_SECRET': 'ak', 'ANTHROPIC_API_KEY': 'mine'}
        self.prepare(env)
        self.assertEqual((env['ANTHROPIC_API_KEY'], self.asked), ('mine', []))


if __name__ == '__main__':
    unittest.main()
