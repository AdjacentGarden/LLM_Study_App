from adaptive_learning.ingestion.inspector import PDFInspector
from adaptive_learning.ingestion.models import PageKind


def test_page_classification_distinguishes_scans_and_digital_pages() -> None:
    classify = PDFInspector._classify

    assert classify(0, 1.0) == PageKind.SCANNED
    assert classify(800, 0.2) == PageKind.DIGITAL
    assert classify(90, 0.8) == PageKind.MIXED
