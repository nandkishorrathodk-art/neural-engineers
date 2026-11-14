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
import re
import shutil
import subprocess
import tempfile
import time
from typing import Optional

import numpy as np

from neural.gui_intelligence.models import ScreenFrame
from neural.gui_intelligence.perception.ocr_cache import (
    OCR_NORMALIZATION_VERSION,
    OCRRegion,
    RegionOCRCache,
)
from neural.gui_intelligence.perception.pixel_ocr import OCRResult
from neural.gui_intelligence.runtime.errors import DesktopRuntimeUnavailable
from neural.logger import get_logger

logger = get_logger("ancos.gui_intelligence.runtime.ocr")

# Desktop UI text (menu labels, document markers) is often only 9-11px tall; at
# native resolution tesseract misreads it ("GUI-OK" -> "GUI-K|"). Upscaling the
# frame before OCR restores legibility.
#
# No single scale is reliable. Measured on 46 real markers rendered by Mousepad
# on a live X desktop, a 3x upscale reads only 39/46: markers whose last digit is
# zero are systematically misread ("NEURO12A-GUI-010" -> "...-016", "...-014",
# "...-919"), so verification fails closed on correct work. 2x reads those but
# drops other glyphs. Reading the frame at several scales and unioning the
# results reads all 46 with no false positive on an empty document.
_OCR_UPSCALES = (2, 3)


class TesseractOCREngine:
    """Real pixel OCR using the tesseract binary (no DOM / AT-SPI access)."""

    def __init__(
        self,
        tesseract_bin: str = "tesseract",
        psm: int = 6,
        upscales: tuple[int, ...] = _OCR_UPSCALES,
        lang: str = "eng",
        region_cache: Optional[RegionOCRCache] = None,
        max_cache_entries: int = 64,
    ) -> None:
        self.tesseract_bin = tesseract_bin
        # psm 6 = "single uniform block of text": reliably reads the menu bars,
        # window titles, and labels of a desktop screenshot. Sparse mode (psm 11)
        # fragments those labels into unreadable noise on a real X desktop.
        self.psm = psm
        self.lang = lang
        self.upscales = tuple(sorted({max(1, int(u)) for u in upscales})) or (1,)
        self._cache_key: str | None = None
        self._cache_regions: list[OCRResult] = []
        # Region-level cache: keyed on region pixels + geometry + OCR config, so an
        # unchanged region is reused even when the whole-frame hash moved (cursor
        # blink, clock tick). Bounded, so a long run cannot grow memory forever.
        self._region_cache = region_cache or RegionOCRCache(max_entries=max_cache_entries)
        # Config signature: results must never survive a change to any of these.
        self._config_signature = "|".join(
            [
                f"bin={os.path.basename(self.tesseract_bin)}",
                f"psm={self.psm}",
                f"lang={self.lang}",
                f"upscales={','.join(str(u) for u in self.upscales)}",
                f"norm={OCR_NORMALIZATION_VERSION}",
            ]
        )
        # Per-call telemetry for the last public call, plus cumulative counters
        # for the whole engine lifetime (merged upward into worker timings).
        self.last_metrics: dict[str, float] = {}
        self.metrics: dict[str, float] = {
            "ocr_full_frame_calls": 0.0,
            "ocr_region_calls": 0.0,
            "ocr_cache_hits": 0.0,
            "ocr_cache_misses": 0.0,
            "ocr_total_ms": 0.0,
            "ocr_full_frame_ms": 0.0,
            "ocr_region_ms": 0.0,
            "ocr_regions_processed": 0.0,
        }

    def metrics_snapshot(self) -> dict[str, float]:
        return dict(self.metrics)

    @property
    def available(self) -> bool:
        return shutil.which(self.tesseract_bin) is not None

    @property
    def cache_stats(self) -> dict[str, int]:
        return self._region_cache.stats.to_dict()

    @property
    def cache_hit_ratio(self) -> float:
        return self._region_cache.stats.hit_ratio

    def _require(self) -> str:
        path = shutil.which(self.tesseract_bin)
        if not path:
            raise DesktopRuntimeUnavailable(
                "tesseract OCR is not installed; cannot read text from real desktop pixels."
            )
        return path

    def _run_tsv(self, pixels: np.ndarray, upscale: int) -> str:
        binary = self._require()
        from PIL import Image

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            tmp_path = handle.name
        try:
            image = Image.fromarray(np.asarray(pixels, dtype="uint8"))
            if upscale > 1:
                # Grayscale + nearest-neighbour upscale: nearest preserves the crisp
                # pixel edges tesseract's classifier expects, whereas smoothing
                # resampling blurs 10px glyphs into unreadable shapes.
                image = image.convert("L").resize(
                    (image.width * upscale, image.height * upscale),
                    Image.NEAREST,
                )
            image.save(tmp_path)
            proc = subprocess.run(
                [binary, tmp_path, "stdout", "-l", self.lang, "--psm", str(self.psm), "tsv"],
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
    def _parse_tsv(tsv: str, scale: int = 1) -> list[OCRResult]:
        results: list[OCRResult] = []
        lines = tsv.splitlines()
        if not lines:
            return results
        header = lines[0].split("\t")
        try:
            idx = {
                name: header.index(name)
                for name in ("left", "top", "width", "height", "conf", "text")
            }
        except ValueError:
            return results
        block_key = ("block_num", "par_num", "line_num")
        if not all(name in header for name in block_key):
            return results
        b_i = header.index("block_num")
        p_i = header.index("par_num")
        l_i = header.index("line_num")
        from collections import defaultdict

        grouped: dict[tuple[str, str, str], list[tuple[int, int, int, int, float, str]]] = (
            defaultdict(list)
        )
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
            # Tesseract ran on the upscaled image; map boxes back to frame pixels
            # so callers click the original coordinates, not the enlarged ones.
            left = int(cols[idx["left"]]) // scale
            top = int(cols[idx["top"]]) // scale
            right = (int(cols[idx["left"]]) + int(cols[idx["width"]])) // scale
            bottom = (int(cols[idx["top"]]) + int(cols[idx["height"]])) // scale
            box = (left, top, right, bottom)
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
            results.append(
                OCRResult(text=text, bounding_box=(xmin, ymin, xmax, ymax), confidence=conf)
            )
        return results

    def extract_text_regions(self, frame: ScreenFrame) -> list[OCRResult]:
        """Read text by running tesseract at every configured scale and unioning.

        Tesseract's digit recognition is scale-sensitive: a marker legible at one
        upscale is misread at another, and which scale wins varies with the glyphs.
        Unioning several scales makes a single correct reading sufficient, so a
        correct on-screen marker is not lost to one scale's misread. Results are
        cached per frame so a verification pass reads each frame once.
        """
        if frame.frame_hash and frame.frame_hash == self._cache_key:
            self.last_metrics = {
                "ocr_total_ms": 0.0,
                "ocr_full_frame_ms": 0.0,
                "ocr_regions_processed": 0.0,
                "ocr_cache_hit": 1.0,
            }
            self.metrics["ocr_cache_hits"] += 1
            return self._cache_regions
        start = time.perf_counter()
        regions = self._ocr_pixels(frame.pixels)
        self._cache_key = frame.frame_hash
        self._cache_regions = regions
        elapsed = (time.perf_counter() - start) * 1000.0
        self.last_metrics = {
            "ocr_total_ms": elapsed,
            "ocr_full_frame_ms": elapsed,
            "ocr_regions_processed": float(len(regions)),
            "ocr_cache_hit": 0.0,
        }
        self.metrics["ocr_full_frame_calls"] += 1
        self.metrics["ocr_cache_misses"] += 1
        self.metrics["ocr_total_ms"] += elapsed
        self.metrics["ocr_full_frame_ms"] += elapsed
        self.metrics["ocr_regions_processed"] += float(len(regions))
        logger.debug(
            "Tesseract OCR scan completed",
            regions=len(regions),
            upscales=list(self.upscales),
            elapsed_ms=elapsed,
        )
        return regions

    def _ocr_pixels(self, pixels: np.ndarray) -> list[OCRResult]:
        """Run multi-scale OCR on a pixel array, mapping boxes to array coordinates."""
        regions: list[OCRResult] = []
        seen: set[tuple[str, tuple[int, int, int, int]]] = set()
        for upscale in self.upscales:
            for region in self._parse_tsv(self._run_tsv(pixels, upscale), scale=upscale):
                key = (region.text, region.bounding_box)
                if key in seen:
                    continue
                seen.add(key)
                regions.append(region)
        return regions

    # -- region OCR ------------------------------------------------------------

    def extract_region_text(
        self,
        frame: ScreenFrame,
        region: OCRRegion,
        cache: bool = True,
    ) -> list[OCRResult]:
        """OCR a single screen region and return boxes in *original* coordinates.

        The region is cropped from the frame and OCR'd at every configured upscale;
        a crop preserves glyph pixel size, so the same upscale ladder gives the same
        legibility as a full-frame read. Every box is offset back into original
        desktop space. Results are cached on the region's own pixels so an unrelated
        change elsewhere on screen cannot evict a still-valid region.
        """
        screen = (int(frame.width), int(frame.height))
        self._region_cache.note_screen(screen)
        clamped = region.clamped(*screen)
        if clamped is None:
            self.last_metrics = {
                "ocr_total_ms": 0.0,
                "ocr_region_ms": 0.0,
                "ocr_region_area": 0.0,
                "ocr_screen_area": float(screen[0] * screen[1]),
                "ocr_regions_processed": 0.0,
                "ocr_cache_hit": 0.0,
            }
            return []

        crop = frame.pixels[
            clamped.y : clamped.y + clamped.height,
            clamped.x : clamped.x + clamped.width,
        ]
        if crop.size == 0:
            self.last_metrics = {
                "ocr_total_ms": 0.0,
                "ocr_region_ms": 0.0,
                "ocr_region_area": float(clamped.area),
                "ocr_screen_area": float(screen[0] * screen[1]),
                "ocr_regions_processed": 0.0,
                "ocr_cache_hit": 0.0,
            }
            return []

        start = time.perf_counter()
        cache_key = self._region_cache.make_key(
            clamped, crop, screen, self._config_signature
        )
        if cache:
            cached = self._region_cache.get(cache_key)
            if cached is not None:
                elapsed = (time.perf_counter() - start) * 1000.0
                self.last_metrics = {
                    "ocr_total_ms": elapsed,
                    "ocr_region_ms": elapsed,
                    "ocr_region_area": float(clamped.area),
                    "ocr_screen_area": float(screen[0] * screen[1]),
                    "ocr_regions_processed": float(len(cached)),
                    "ocr_cache_hit": 1.0,
                }
                self.metrics["ocr_region_calls"] += 1
                self.metrics["ocr_cache_hits"] += 1
                self.metrics["ocr_total_ms"] += elapsed
                self.metrics["ocr_region_ms"] += elapsed
                return cached

        # A region crop preserves the glyphs' pixel size, so the same configured
        # upscale ladder gives the same legibility as a full-frame read. Scaling a
        # small crop further would only enlarge already-legible glyphs and cost time.
        local = self._ocr_pixels(crop)
        results = [
            OCRResult(
                text=r.text,
                bounding_box=(
                    r.bounding_box[0] + clamped.x,
                    r.bounding_box[1] + clamped.y,
                    r.bounding_box[2] + clamped.x,
                    r.bounding_box[3] + clamped.y,
                ),
                confidence=r.confidence,
            )
            for r in local
        ]
        if cache and results:
            self._region_cache.put(cache_key, results)
        elapsed = (time.perf_counter() - start) * 1000.0
        self.last_metrics = {
            "ocr_total_ms": elapsed,
            "ocr_region_ms": elapsed,
            "ocr_region_area": float(clamped.area),
            "ocr_screen_area": float(screen[0] * screen[1]),
            "ocr_regions_processed": float(len(results)),
            "ocr_cache_hit": 0.0,
        }
        self.metrics["ocr_region_calls"] += 1
        self.metrics["ocr_cache_misses"] += 1
        self.metrics["ocr_total_ms"] += elapsed
        self.metrics["ocr_region_ms"] += elapsed
        self.metrics["ocr_regions_processed"] += float(len(results))
        return results

    def find_text_in_region(
        self,
        frame: ScreenFrame,
        query: str,
        region: OCRRegion,
        fuzzy: bool = True,
        min_confidence: float = 0.70,
        cache: bool = True,
    ) -> list[OCRResult]:
        """Match a query against the OCR of a single region (boxes in screen space)."""
        if not query or not query.strip():
            return []
        return self._match(
            query,
            self.extract_region_text(frame, region, cache=cache),
            fuzzy=fuzzy,
            min_confidence=min_confidence,
        )

    def has_containment_match(self, query: str, matches: list[OCRResult]) -> bool:
        """True when the query's normalized text appears inside a matched reading.

        Tesseract's per-word confidence is noisier on a cropped region than on the
        full frame (block context differs), so a correct region read can score a
        little lower than the same word read frame-wide. A separator-insensitive
        containment is strong textual evidence in its own right and must not be
        discarded merely because the confidence dipped.
        """
        query_key = self._normalize(query)
        if len(query_key) < 4:
            return False
        return any(query_key in self._normalize(m.text) for m in matches)

    @staticmethod
    def _normalize(text: str) -> str:
        """Fold case and drop separators so OCR noise ("GUI-K|", "GUI - OK") still
        matches the intended token ("GUI-OK"). A live desktop renders markers with
        hyphens, spaces, or trailing cursor glyphs that tesseract reads inconsistently."""
        return re.sub(r"[^a-z0-9]", "", text.lower())

    @staticmethod
    def _digits(text: str) -> str:
        return re.sub(r"\D", "", text)

    def _match(
        self,
        query: str,
        regions: list[OCRResult],
        fuzzy: bool,
        min_confidence: float,
    ) -> list[OCRResult]:
        """Apply the existing matching rules to a set of OCR regions.

        Shared by the full-frame and region paths so both obey the same
        separator-insensitive containment, digit guard and fuzzy ratio.
        """
        import difflib

        query_norm = query.strip().lower()
        query_key = self._normalize(query)
        matched: list[OCRResult] = []
        for region in regions:
            text_norm = region.text.strip().lower()
            text_key = self._normalize(region.text)
            # Separator-insensitive containment: a marker rendered as "GUI-OK" must
            # match OCR output like "GUI-K|" that dropped or mangled the separator.
            # Guarded by a minimum key length so short queries cannot match loosely.
            if len(query_key) >= 4 and query_key in text_key:
                matched.append(region)
                continue
            if region.confidence < min_confidence:
                continue
            if query_norm == text_norm or query_norm in text_norm:
                matched.append(region)
            elif fuzzy:
                # Marker queries ("NEURO12A-GUI-010") share a long prefix, so the
                # fuzzy ratio stays high against a *different* marker ("...-039"),
                # which would let a wrong document satisfy the postcondition. When
                # both sides carry digits, the query's digits must appear in the
                # reading; a digit mismatch is a different token, not OCR noise.
                query_digits = self._digits(query_norm)
                text_digits = self._digits(text_norm)
                if query_digits and text_digits and query_digits not in text_digits:
                    continue
                ratio = max(
                    difflib.SequenceMatcher(None, query_norm, text_norm).ratio(),
                    difflib.SequenceMatcher(None, query_key, text_key).ratio(),
                )
                if ratio >= 0.8:
                    matched.append(
                        OCRResult(
                            text=region.text,
                            bounding_box=region.bounding_box,
                            confidence=region.confidence * ratio,
                        )
                    )
        return matched

    def find_text(
        self,
        frame: ScreenFrame,
        query: str,
        fuzzy: bool = True,
        min_confidence: float = 0.70,
    ) -> list[OCRResult]:
        if not query or not query.strip():
            return []
        return self._match(
            query,
            self.extract_text_regions(frame),
            fuzzy=fuzzy,
            min_confidence=min_confidence,
        )

    def register_mock_text(self, *_args, **_kwargs) -> None:  # interface parity with PixelOCREngine
        raise DesktopRuntimeUnavailable(
            "Mock text registration is not supported by the real desktop OCR engine."
        )
