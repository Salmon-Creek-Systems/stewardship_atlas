import os
import sys
import tempfile
import shutil
from pathlib import Path

import pytest

# Add the python directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# pypdf is the only dependency; skip cleanly where it (or the module) is absent.
pytest.importorskip("pypdf")
from pdf_concat import concat_individual_pdfs  # noqa: E402


def _make_pdf(path, num_pages):
    """Write a tiny valid PDF with the given number of blank pages."""
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(num_pages):
        w.add_blank_page(width=200, height=200)
    with open(path, 'wb') as f:
        w.write(f)
    w.close()


class TestConcatIndividualPdfs:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())

    def teardown_method(self):
        shutil.rmtree(self.tmp)

    def test_page_count_is_sum_of_sources(self):
        from pypdf import PdfReader
        # Three source PDFs with 1, 2, and 1 pages -> 4 total.
        specs = [("a", 1), ("b", 2), ("c", 1)]
        entries = []
        for name, pages in specs:
            p = self.tmp / f"{name}.pdf"
            _make_pdf(p, pages)
            entries.append((p, f"Region {name}"))

        out = self.tmp / "combined.pdf"
        merged = concat_individual_pdfs(entries, out)

        assert merged == 3
        assert out.exists()
        reader = PdfReader(str(out))
        assert len(reader.pages) == 4

    def test_bookmarks_one_per_source(self):
        from pypdf import PdfReader
        entries = []
        for name in ("alpha", "beta", "gamma"):
            p = self.tmp / f"{name}.pdf"
            _make_pdf(p, 1)
            entries.append((p, f"Region {name}"))

        out = self.tmp / "combined.pdf"
        concat_individual_pdfs(entries, out)

        reader = PdfReader(str(out))
        titles = [str(o.title) for o in reader.outline]
        assert titles == ["Region alpha", "Region beta", "Region gamma"]

    def test_order_is_preserved(self):
        # Page order should follow entries order, not filename sort.
        from pypdf import PdfReader
        entries = []
        for name in ("z", "a", "m"):
            p = self.tmp / f"{name}.pdf"
            _make_pdf(p, 1)
            entries.append((p, name))

        out = self.tmp / "combined.pdf"
        concat_individual_pdfs(entries, out)

        reader = PdfReader(str(out))
        titles = [str(o.title) for o in reader.outline]
        assert titles == ["z", "a", "m"]

    def test_accepts_str_paths(self):
        from pypdf import PdfReader
        p = self.tmp / "one.pdf"
        _make_pdf(p, 1)
        out = self.tmp / "combined.pdf"
        # str path rather than Path — should work via str() coercion.
        concat_individual_pdfs([(str(p), "only")], out)
        assert len(PdfReader(str(out)).pages) == 1
