"""Lightweight PDF concatenation for the runbook outlet.

Kept in its own module (importing only pypdf) so the logic is unit-testable
without QGIS, unlike outlets_qgis_atlas which imports the QGIS bindings at load.
"""


def concat_individual_pdfs(entries, output_path):
    """Concatenate per-region PDFs into a single file with per-page bookmarks.

    Uses pypdf, which copies page objects without rasterizing — no geodata is
    loaded, so memory stays roughly at one page. This is the cheap alternative
    to the QGIS Atlas multi-page export.

    Args:
        entries: list of (pdf_path, title) in the desired page order. Each source
            PDF is one region page; title becomes its outline (bookmark) label.
        output_path: destination path for the combined PDF.

    Returns:
        The number of source PDFs merged.
    """
    from pypdf import PdfWriter

    writer = PdfWriter()
    try:
        for pdf_path, title in entries:
            # outline_item labels the appended page range in the PDF bookmarks pane.
            writer.append(str(pdf_path), outline_item=title)
        with open(output_path, 'wb') as f:
            writer.write(f)
    finally:
        writer.close()
    return len(entries)
