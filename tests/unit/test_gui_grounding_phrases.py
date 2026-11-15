"""GUI grounding regressions that made a real desktop click do nothing.

Two independent defects were observed live on an employee workstation:

* Tesseract groups words by baseline row, so a launcher menu item and an
  unrelated window's menu bar that happen to share a row were aggregated into
  one box spanning the empty space between them. Grounding a menu item then
  returned that box's center - a blank pixel - and the click was a no-op while
  the step still reported SUCCESS.
* The typed-value extractor kept the leading article ("type the text hello"),
  so the literal that reached the keyboard was "the text hello" and the expected
  deliverable never appeared on screen.
"""

from __future__ import annotations

from neural.gui_intelligence.perception.ocr_runtime import TesseractOCREngine
from neural.gui_intelligence.runtime.planner import _typed_value, derive_gui_steps

_HEADER = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
    "left\ttop\twidth\theight\tconf\ttext"
)


def _row(block, par, line, word, left, top, width, height, conf, text):
    return (
        f"5\t1\t{block}\t{par}\t{line}\t{word}\t"
        f"{left}\t{top}\t{width}\t{height}\t{conf}\t{text}"
    )


def test_ocr_row_with_wide_gap_is_split_into_separate_phrases():
    """A menu label and a distant window title on one row must not merge."""
    tsv = "\n".join(
        [
            _HEADER,
            # Launcher label at x=27..103
            _row(1, 1, 1, 1, 27, 63, 76, 12, 95, "Terminal"),
            _row(1, 1, 1, 2, 110, 63, 72, 12, 95, "Emulator"),
            # Unrelated window menu bar far to the right on the same Tesseract row
            _row(1, 1, 1, 3, 600, 63, 30, 12, 95, "File"),
            _row(1, 1, 1, 4, 640, 63, 30, 12, 95, "Edit"),
        ]
    )
    phrases = [r.text for r in TesseractOCREngine._parse_tsv(tsv)]
    assert "Terminal Emulator" in phrases
    assert "Terminal Emulator File Edit" not in phrases
    # The merged phrase must never be produced as a single box.
    merged = [
        r
        for r in TesseractOCREngine._parse_tsv(tsv)
        if r.text.startswith("Terminal") and r.bounding_box[2] > 300
    ]
    assert merged == []


def test_ocr_row_with_normal_spacing_keeps_phrase_together():
    tsv = "\n".join(
        [
            _HEADER,
            _row(1, 1, 1, 1, 27, 63, 76, 12, 95, "Terminal"),
            _row(1, 1, 1, 2, 110, 63, 72, 12, 95, "Emulator"),
        ]
    )
    phrases = [r.text for r in TesseractOCREngine._parse_tsv(tsv)]
    assert "Terminal Emulator" in phrases


def test_typed_value_strips_leading_article():
    assert _typed_value("type the text hello-ancos, then capture the screen.") == "hello-ancos"
    assert _typed_value("type the marker GUI_LIFECYCLE_42 into the document.") == "GUI_LIFECYCLE_42"
    assert (
        _typed_value("input the exact string GUI-OK into a new document.") == "GUI-OK"
    )


def test_planner_types_bare_payload_for_article_phrasing():
    steps = derive_gui_steps(
        "GUI Live Proof",
        "On the graphical desktop, open a terminal emulator window and type the text "
        "hello-ancos, then capture the on-screen result. Execute strictly through the "
        "real desktop GUI.",
    )
    typed = [s["text"] for s in steps if s["action_type"] == "KEY_TYPE"]
    assert typed == ["hello-ancos"]
