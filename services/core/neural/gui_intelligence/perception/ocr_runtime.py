"""Tesseract-backed pixel OCR for real desktops.

The ANCOS strict GUI contract forbids DOM / accessibility-tree inspection. Text on
a real desktop must therefore be recovered from raster pixels. This engine shells
out to the ``tesseract`` binary (TSV output gives per-word bounding boxes) and
returns :class:`~neural.gui_intelligence.perception.pixel_ocr.OCRResult` records,
which the existing visual grounder consumes unchanged.

When ``tesseract`` is not installed the engine reports ``available == False`` and
raises :class:`DesktopRuntimeUnavailable`, so a real-desktop run fails loudly
rather than pretending to have read the screen.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time

from neural.gui_intelligence.models import ScreenFrame
from neural.gui_intelligence.perception.pixel_ocr import OCRResult
from neural.gui_intelligence.runtime.errors import DesktopRuntimeUnavailable
from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.runtime.ocr")


class TesseractOCREngine:
    """Real pixel OCR using the tesseract binary (no DOM / AT-SPI access)."""

    def __init__(self, tesseract_bin: str = "tesseract", psm: int = 6) -> None:
        self.tesseract_bin = tesseract_bin
        # psm 6 = "single uniform block of text": reliably reads the menu bars,
        # window titles, and labels of a desktop screenshot. Sparse mode (psm 11)
        # fragments those labels into unreadable noise on a real X desktop.
        self.psm = psm

    @property
    def available(self) -> bool:
        return shutil.which(self.tesseract_bin) is not None

    def _require(self) -> str:
        path = shutil.which(self.tesseract_bin)
        if not path:
            raise DesktopRuntimeUnavailable(
                "tesseract OCR is not installed; cannot read text from real desktop pixels."
            )
        return path

    def _run_tsv(self, frame: ScreenFrame) -> str:
        binary = self._require()
        import numpy as np
        from PIL import Image

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            tmp_path = handle.name
        try:
            image = Image.fromarray(np.asarray(frame.pixels, dtype="uint8"))
            image.save(tmp_path)
            proc = subprocess.run(
                [binary, tmp_path, "stdout", "--psm", str(self.psm), "tsv"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode != 0:
                raise DesktopRuntimeUnavailable(
                    f"tesseract failed (exit {proc.returncode}): {proc.stderr.strip()}"
                )
            return proc.stdout
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    @staticmethod
    def _parse_tsv(tsv: str) -> list[OCRResult]:
        results: list[OCRResult] = []
        lines = tsv.splitlines()
        if not lines:
            return results
        header = lines[0].split("\t")
        try:
            idx = {name: header.index(name) for name in ("left", "top", "width", "height", "conf", "text")}
        except ValueError:
            return results
        block_key = ("block_num", "par_num", "line_num")
        if not all(name in header for name in block_key):
            return results
        b_i = header.index("block_num")
        p_i = header.index("par_num")
        l_i = header.index("line_num")
        from collections import defaultdict

        grouped: dict[tuple[str, str, str], list[tuple[int, int, int, int, float, str]]] = defaultdict(list)
        for line in lines[1:]:
            cols = line.split("\t")
            if len(cols) <= idx["text"]:
                continue
            text = cols[idx["text"]].strip()
            if not text:
                continue
            try:
                conf = float(cols[idx["conf"]])
            except ValueError:
                continue
            if conf < 0:
                continue
            box = (
                int(cols[idx["left"]]),
                int(cols[idx["top"]]),
                int(cols[idx["left"]]) + int(cols[idx["width"]]),
                int(cols[idx["top"]]) + int(cols[idx["height"]]),
            )
            word_conf = conf / 100.0
            # Word-level box: the precise click target for a single on-screen label.
            results.append(OCRResult(text=text, bounding_box=box, confidence=word_conf))
            grouped[(cols[b_i], cols[p_i], cols[l_i])].append((*box, word_conf, text))
        # Line-level aggregate: lets a multi-word label match as one phrase.
        for words in grouped.values():
            words.sort(key=lambda w: w[0])
            text = " ".join(w[5] for w in words)
            xmin = min(w[0] for w in words)
            ymin = min(w[1] for w in words)
            xmax = max(w[2] for w in words)
            ymax = max(w[3] for w in words)
            conf = sum(w[4] for w in words) / len(words)
            results.append(OCRResult(text=text, bounding_box=(xmin, ymin, xmax, ymax), confidence=conf))
        return results

    def extract_text_regions(self, frame: ScreenFrame) -> list[OCRResult]:
        start = time.perf_counter()
        tsv = self._run_tsv(frame)
        regions = self._parse_tsv(tsv)
        logger.debug(
            "Tesseract OCR scan completed",
            regions=len(regions),
            elapsed_ms=(time.perf_counter() - start) * 1000.0,
        )
        return regions

    def find_text(
        self,
        frame: ScreenFrame,
        query: str,
        fuzzy: bool = True,
        min_confidence: float = 0.70,
    ) -> list[OCRResult]:
        if not query or not query.strip():
            return []
        import difflib

        query_norm = query.strip().lower()
        matched: list[OCRResult] = []
        for region in self.extract_text_regions(frame):
            text_norm = region.text.strip().lower()
            if region.confidence < min_confidence and query_norm not in text_norm:
                continue
            if query_norm == text_norm or query_norm in text_norm:
                matched.append(region)
            elif fuzzy:
                ratio = difflib.SequenceMatcher(None, query_norm, text_norm).ratio()
                if ratio >= 0.8:
                    matched.append(
                        OCRResult(text=region.text, bounding_box=region.bounding_box, confidence=region.confidence * ratio)
                    )
        return matched

    def register_mock_text(self, *_args, **_kwargs) -> None:  # interface parity with PixelOCREngine
        raise DesktopRuntimeUnavailable(
            "Mock text registration is not supported by the real desktop OCR engine."
        )
